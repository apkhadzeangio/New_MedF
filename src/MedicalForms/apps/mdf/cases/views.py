"""Vascular conference case list, CRUD, decision, and full-screen presentation views."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Prefetch, Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ..board import clean_text, doctors, person_name
from ..duty import strip_astral
from ..models import BoardPatient, Case, CasePhoto
from .doppler_views import doppler_view
from .photos import _date, _photos_by_kind
from .staging_views import _limbs_of, case_limb_metrics, case_plan_info, staging_views

TEXT_FIELDS = ('diagnosis', 'comorbidities', 'past_history', 'history', 'findings', 'proposal')


def models_nulls_last(field):
    return F(field).asc(nulls_last=True)


class CaseListView(LoginRequiredMixin, generic.TemplateView):
    template_name = 'mdf/cases/list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        status = self.request.GET.get('s', Case.Status.OPEN)
        if status not in Case.Status.values:
            status = Case.Status.OPEN
        q = ' '.join(self.request.GET.get('q', '').split())[:100]
        qs = Case.objects.filter(status=status).select_related('doctor', 'doctor__profile').annotate(n_photos=Count('photos'))
        if q:
            qs = qs.filter(
                Q(patient_name__icontains=q) | Q(diagnosis__icontains=q)
                | Q(proposal__icontains=q) | Q(decision__icontains=q)
            )
        if status == Case.Status.OPEN:
            # the nearest conference first, cases without a date after them
            qs = qs.order_by(models_nulls_last('conference_on'), 'created_at')
        else:
            qs = qs.order_by('-decided_at', '-updated_at')
        cases = list(qs[:300])
        covers = {}
        for photo in CasePhoto.objects.filter(case__in=cases).order_by('case_id', 'taken_on', 'id'):
            covers.setdefault(photo.case_id, photo)
        for case in cases:
            case.cover = covers.get(case.pk)
        counts = dict(Case.objects.values_list('status').annotate(n=Count('id')))
        ctx.update({
            'cases': cases, 'q': q, 'status': status,
            'tabs': [(key, label, counts.get(key, 0)) for key, label in Case.Status.choices],
            'today': timezone.localdate(),
        })
        return ctx


class CaseFormView(LoginRequiredMixin, generic.View):
    """New case, or edit the card of an existing one."""

    def get(self, request, pk=None):
        case = get_object_or_404(Case, pk=pk) if pk else None
        values = self._values_of(case) if case else self._prefill(request)
        return self._render(request, case, values)

    def post(self, request, pk=None):
        case = get_object_or_404(Case, pk=pk) if pk else None
        values = self._read(request.POST)
        if not values['patient_name']:
            messages.error(request, 'ჩაწერეთ პაციენტის სახელი და გვარი.')
            return self._render(request, case, values)
        is_new = case is None
        if is_new:
            case = Case(created_by=request.user)
        for field in ('patient_name', 'age', 'sex', 'side', 'conference_on', *TEXT_FIELDS):
            setattr(case, field, values[field])
        case.doctor_id = values['doctor']
        case.updated_by = request.user
        case.save()
        if is_new:
            messages.success(request, 'ქეისი შეიქმნა. ახლა დაამატეთ ფოტოები.')
        else:
            messages.success(request, 'შენახულია.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]))

    @staticmethod
    def _read(data):
        age = data.get('age', '').strip()
        doctor = data.get('doctor', '')
        valid_doctors = {pk for pk, _ in doctors()}
        return {
            'patient_name': ' '.join(strip_astral(data.get('patient_name', '')).split())[:150],
            'age': int(age) if age.isdigit() and 0 < int(age) < 130 else None,
            'sex': data.get('sex') if data.get('sex') in Case.Sex.values else '',
            'side': data.get('side') if data.get('side') in Case.Side.values else '',
            'conference_on': _date(data.get('conference_on')),
            'doctor': int(doctor) if doctor.isdigit() and int(doctor) in valid_doctors else None,
            **{f: clean_text(data.get(f, ''), 5000) for f in TEXT_FIELDS},
        }

    @staticmethod
    def _values_of(case):
        return {f: getattr(case, f) for f in ('patient_name', 'age', 'sex', 'side', 'conference_on', *TEXT_FIELDS)} \
            | {'doctor': case.doctor_id}

    @staticmethod
    def _prefill(request):
        values = {'doctor': request.user.pk}
        board_id = request.GET.get('board', '')
        if board_id.isdigit():
            patient = BoardPatient.objects.filter(pk=board_id).first()
            if patient:
                values.update(
                    patient_name=patient.name, diagnosis=patient.diagnosis,
                    doctor=patient.doctor_id or request.user.pk,
                )
        return values

    @staticmethod
    def _render(request, case, values):
        plan_info = case_plan_info(case) if case else {'items': [], 'show_side': False, 'insert_text': ''}
        return render(request, 'mdf/cases/form.html', {
            'case': case, 'values': values, 'doctors': doctors(),
            'sexes': Case.Sex.choices[1:], 'sides': Case.Side.choices[1:],
            'plan_items': plan_info['items'], 'plan_show_side': plan_info['show_side'],
            'plan_insert_text': plan_info['insert_text'],
        })


class CaseView(LoginRequiredMixin, generic.TemplateView):
    template_name = 'mdf/cases/detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        case = get_object_or_404(
            Case.objects.select_related('doctor', 'decided_by', 'created_by', 'updated_by'),
            pk=kwargs['pk'],
        )
        limb_metrics = case_limb_metrics(case)
        plan_info = case_plan_info(case, limb_metrics=limb_metrics)
        ctx.update({
            'case': case, 'groups': _photos_by_kind(case), 'kinds': CasePhoto.Kind.choices,
            'dopplers': [doppler_view(d) for d in case.dopplers.all()],
            'stagings': staging_views(case), 'limbs': _limbs_of(case),
            'limb_metrics': limb_metrics,
            'manual_doppler_rows': [row for row in limb_metrics if row['has_manual_doppler']],
            'plan_items': plan_info['items'], 'plan_show_side': plan_info['show_side'],
            'linked_preop': case.preops.filter(archived=False).order_by('-pk').first(),
            'kind': self.request.GET.get('kind') or CasePhoto.Kind.WOUND,
            'today': timezone.localdate(),
        })
        return ctx


class DecisionView(LoginRequiredMixin, generic.View):
    """The conference decision (from the case page or during the presentation)."""

    def post(self, request, pk):
        case = get_object_or_404(Case, pk=pk)
        action = request.POST.get('action', 'decide')
        if action == 'reopen':
            case.status = Case.Status.OPEN
        elif action == 'archive':
            case.status = Case.Status.ARCHIVED
        else:
            case.decision = clean_text(request.POST.get('decision', ''), 5000)
            if case.decision:
                case.status = Case.Status.DECIDED
                case.decided_at, case.decided_by = timezone.now(), request.user
            elif case.status == Case.Status.DECIDED:
                case.status = Case.Status.OPEN
        case.updated_by = request.user
        case.save()
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True, 'status': case.status, 'status_label': case.get_status_display(),
                'by': person_name(case.decided_by) if case.decision else '',
            })
        labels = {'reopen': 'ქეისი ისევ მოსახსენებელია.', 'archive': 'ქეისი არქივშია.'}
        messages.success(request, labels.get(action, 'გადაწყვეტილება შენახულია.' if case.decision else 'შენახულია.'))
        return HttpResponseRedirect(reverse('case', args=[case.pk]))


class CaseDeleteView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        case = get_object_or_404(Case, pk=pk)
        name = case.patient_name
        case.delete()                       # photos and their files go too
        messages.success(request, f'ქეისი „{name}“ წაიშალა.')
        return HttpResponseRedirect(reverse('cases'))


class PresentView(LoginRequiredMixin, generic.TemplateView):
    """Full-screen presentation: one case after another (?ids=3,5,8 or the open ones)."""
    template_name = 'mdf/cases/present.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ids = [int(x) for x in self.request.GET.get('ids', '').split(',') if x.strip().isdigit()][:60]
        qs = Case.objects.select_related('doctor', 'decided_by').prefetch_related(
            Prefetch('photos', queryset=CasePhoto.objects.order_by('taken_on', 'id')), 'dopplers',
        )
        if ids:
            by_id = {c.pk: c for c in qs.filter(pk__in=ids)}
            cases = [by_id[i] for i in ids if i in by_id]
        else:
            day = _date(self.request.GET.get('day'))
            qs = qs.filter(status=Case.Status.OPEN)
            if day:
                qs = qs.filter(conference_on=day)
            cases = list(qs.order_by(models_nulls_last('conference_on'), 'created_at')[:60])
        if not cases:
            raise Http404('მოსახსენებელი ქეისი არ არის.')
        order = {kind: i for i, kind in enumerate(CasePhoto.Kind.values)}
        for case in cases:
            case.slides = sorted(case.photos.all(), key=lambda p: (order.get(p.kind, 9), p.taken_on, p.pk))
            case.doppler_views = [doppler_view(d) for d in case.dopplers.all()]
            case.staging_views = staging_views(case)
            case.limb_metrics = case_limb_metrics(case)
            plan_info = case_plan_info(case, limb_metrics=case.limb_metrics)
            case.plan_items = plan_info['items']
            case.plan_show_side = plan_info['show_side']
        ctx.update({
            'cases': cases, 'anon': self.request.GET.get('anon') == '1',
            'back': self.request.GET.get('back') or reverse('cases'),
        })
        if not ctx['back'].startswith('/') or ctx['back'].startswith('//'):
            ctx['back'] = reverse('cases')
        return ctx

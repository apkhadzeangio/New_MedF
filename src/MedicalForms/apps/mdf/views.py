import gzip
import io
import json
import logging
from urllib.parse import quote
from typing import Any

from django.contrib import messages
from django.contrib.auth import logout, authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import serializers
from django.core.management import call_command
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.forms import model_to_dict
from django.http import (
    Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import generic
from django_docxtpl import DocxTemplateView

from . import analysis
from .backends import ModelFilterBackend
from .enums import Symbols, YesNo, RadialPulse
from .forms import LoginForm, NivFormFrom, PhotoForm
from .mixins import LoginRedirectMixin, GetObjectMixin
from .models import (
    Medicine, Note, NivForm, PrescriptionTemplate, StaffProfile, ROW_COUNT,
)
from .utils import validate_take_medicine, clean_template_rows, resize_photo

logger = logging.getLogger(__name__)


def csrf_failure(request, reason=''):
    """Shown instead of Django's English "CSRF verification failed" page."""
    from django.shortcuts import render
    logger.warning('CSRF failure on %s: %s', request.path, reason)
    return render(request, 'mdf/csrf-failure.html', status=403)


# --------------------------------------------------------------------------
# Login / logout / profile photo
# --------------------------------------------------------------------------

class LoginView(LoginRedirectMixin, generic.FormView):
    """Login page: pick your photo/name, type the password."""
    template_name = 'mdf/login.html'
    form_class = LoginForm
    success_url = reverse_lazy('niv-cover')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['next'] = self.request.GET.get('next', self.request.POST.get('next', ''))
        ctx['profiles'] = (StaffProfile.objects.filter(show_on_login=True, user__is_active=True)
                           .select_related('user'))
        return ctx

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(
            form=form, message='აირჩიეთ სახელი და შეიყვანეთ პაროლი.'
        ))

    def form_valid(self, form):
        user = authenticate(
            self.request,
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password'],
        )
        if not user:
            return self.render_to_response(self.get_context_data(
                form=form, message='პაროლი არასწორია.'
            ))
        login(self.request, user)  # session lasts SESSION_COOKIE_AGE (stays logged in)

        next_page = form.cleaned_data['next']
        if next_page and next_page != reverse('logout') and url_has_allowed_host_and_scheme(
            next_page, allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return HttpResponseRedirect(next_page)
        return super().form_valid(form)


class LogoutView(LoginRequiredMixin, generic.View):

    def get(self, request, *args, **kwargs):
        logout(request)
        return HttpResponseRedirect('/')

    post = get


class PhotoView(LoginRequiredMixin, generic.FormView):
    """Each person can set their own login-page photo."""
    template_name = 'mdf/photo.html'
    form_class = PhotoForm
    success_url = reverse_lazy('niv-cover')

    def get_profile(self):
        user = self.request.user
        profile, _ = StaffProfile.objects.get_or_create(
            user=user, defaults={'display_name': user.get_full_name() or user.username}
        )
        return profile

    def get_initial(self):
        return {'full_name': self.get_profile().full_name}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['profile'] = self.get_profile()
        return ctx

    def form_valid(self, form):
        profile = self.get_profile()
        profile.full_name = form.cleaned_data.get('full_name', '').strip()
        profile.save(update_fields=['full_name'])
        photo = form.cleaned_data.get('photo')
        if photo:
            if profile.photo:
                profile.photo.delete(save=False)
            profile.photo.save(f'{profile.user.username}.jpg', resize_photo(photo))
        messages.success(self.request, 'პროფილი შენახულია.')
        return super().form_valid(form)


# --------------------------------------------------------------------------
# Prescription (front side of NIV-300-9/a)
# --------------------------------------------------------------------------

def templates_for_page():
    return [
        {'id': t.pk, 'name': t.name, 'rows': t.rows}
        for t in PrescriptionTemplate.objects.order_by('-use_count', 'name')
    ]


def board_prefill(request):
    """A new, unsaved prescription with the patient's data from the board (?board=<id>)."""
    from .models import BoardPatient
    try:
        p = BoardPatient.objects.get(pk=int(request.GET.get('board', '')))
    except (TypeError, ValueError, BoardPatient.DoesNotExist):
        return None
    return NivForm(title=p.name, patient_name=p.name, chamber_number=p.bed, diagnosis=p.diagnosis)


class NivFormCoverView(LoginRequiredMixin, GetObjectMixin, generic.TemplateView):
    template_name = 'mdf/niv.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['niv'] = self.get_object()
        if not ctx['niv']:
            ctx['niv'] = self.get_object(kwarg='as')
            ctx['can_update'] = False
        else:
            ctx['can_update'] = True
        if not ctx['niv']:
            ctx['niv'] = board_prefill(self.request)
        ctx['rx_templates'] = templates_for_page()
        ctx['apply_template'] = self.request.GET.get('tpl', '')
        return ctx


# How many suggestions the autocomplete returns at most.
AUTOCOMPLETE_LIMIT = 30


class BaseAPIView(LoginRequiredMixin, generic.View):
    queryset = None
    fields = ('name',)

    def get_queryset(self):
        q = str(self.request.GET.get('q', '')).strip()
        if not q:
            return self.queryset.none()
        qs = ModelFilterBackend.filter_queryset(self.request, self.queryset)
        # Names that *start* with the typed text are shown first.
        return qs.annotate(
            _rank=Case(
                When(name__istartswith=q, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by('_rank', 'name')[:AUTOCOMPLETE_LIMIT]

    def get(self, request, *args, **kwargs):
        qs = self.get_queryset()
        if len(self.fields) == 1:
            data = list(qs.values_list(self.fields[0], flat=True))
        else:
            data = list(qs.values_list(*self.fields))
        return JsonResponse(data, safe=False)


class MedicineAPIView(BaseAPIView):
    queryset = Medicine.objects.filter(is_active=True)
    fields = ('name', 'note')


class NoteAPIView(BaseAPIView):
    queryset = Note.objects.filter(is_active=True)


def _row_columns():
    columns = ['id', 'title']
    for i in range(1, ROW_COUNT + 1):
        columns += [f'm{i}', f'n{i}']
    return columns


class NivTemplateListView(LoginRequiredMixin, generic.ListView):
    """List of all prescriptions (everyone sees everyone's)."""
    model = NivForm
    template_name = 'mdf/niv-templates.html'
    paginate_by = 50

    def get_search_term(self):
        return self.request.POST.get(
            'q', self.request.GET.get('q', '')
        ).strip()

    def get_queryset(self):
        qs = NivForm.objects.only(*_row_columns(), 'diagnosis', 'doe', 'patient_name', 'card_number').order_by('-id')
        q = self.get_search_term()
        if q:
            qs = qs.filter(
                Q(title__icontains=q) | Q(doe__icontains=q) |
                Q(patient_name__icontains=q) | Q(department__icontains=q) |
                Q(card_number__icontains=q) | Q(chamber_number__icontains=q) |
                Q(diagnosis__icontains=q) | Q(person_id__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.get_search_term()
        return ctx

    def paginate_queryset(self, queryset, page_size):
        # An out-of-range ?page= shows the last page instead of a 404 error.
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get('page'))
        return paginator, page, page.object_list, page.has_other_pages()

    def post(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data())

    def get_template_names(self):
        if self.request.method == 'POST':
            return ['mdf/niv-form-search.html']
        return ['mdf/niv-templates.html']


def prescription_title(data, instance=None):
    """Internal name of a prescription = the patient's name (or diagnosis)."""
    for field in ('patient_name', 'diagnosis'):
        value = ' '.join((data.get(field) or '').split())
        if value:
            return value[:255]
    if instance and instance.title:
        return instance.title
    return f'დანიშნულება {timezone.localtime():%d.%m.%Y %H:%M}'


class NivTemplateCreateView(LoginRequiredMixin, GetObjectMixin, generic.View):
    """Save a prescription (new, update, or "Save As" copy)."""
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        data = request.POST.copy()
        validated_data, _ = validate_take_medicine(data)
        data.update(validated_data)

        action = request.POST.get('action')
        instance = self.get_object()
        # no separate name: the prescription is known by the patient (and diagnosis)
        data['title'] = prescription_title(data, instance)

        if instance and action == 'save_as':
            # "Save As": create a copy with the edits, leave the original as it was.
            for name, value in model_to_dict(instance).items():
                if name == 'id' or name in data or value in (None, False):
                    continue
                data[name] = json.dumps(value) if isinstance(value, (dict, list)) else value
            form = NivFormFrom(data=data)
        elif instance:
            form = NivFormFrom(instance=instance, data=data)
        else:
            form = NivFormFrom(data=data)

        if not form.is_valid():
            logger.warning('NivForm save failed: %s', form.errors.as_json())
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else field
                messages.error(request, f'{label}: {" ".join(errors)}')
            messages.error(request, 'დანიშნულება ვერ შეინახა. გაასწორეთ შეცდომა და სცადეთ თავიდან.')
            back = reverse('niv-cover')
            if instance:
                back = f'{back}?t={instance.pk}'
            return HttpResponseRedirect(back)

        instance = form.save()
        messages.success(request, f'დანიშნულება „{instance.title}“ შენახულია.')

        cover = reverse('niv-cover')
        if action == 'save_and_new':
            return HttpResponseRedirect(cover)
        if action == 'save_as':
            return HttpResponseRedirect(reverse('niv-templates'))
        return HttpResponseRedirect(f'{cover}?t={instance.pk}')


class NivTemplateDeleteView(LoginRequiredMixin, generic.View):
    http_method_names = ['post', 'delete']
    success_url = reverse_lazy('niv-templates')

    def post(self, request, *args, **kwargs):
        obj = get_object_or_404(NivForm, **kwargs)
        title = obj.title
        obj.delete()
        logger.info('NivForm %s (%s) deleted by %s', kwargs.get('pk'), title, request.user)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'deleted': True})
        messages.success(request, f'დანიშნულება „{title}“ წაიშალა.')
        return HttpResponseRedirect(self.success_url)

    delete = post


class NivDocumentView(LoginRequiredMixin, DocxTemplateView):
    # NOTE: the printed form (MDFNew.docx) is the officially approved layout.
    # Do not change the template or the context keys below.
    template_name = 'MDFNew.docx'
    filename = "MDF"
    output_format = 'pdf'

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        response['Content-Type'] = 'application/pdf'
        response['Content-Disposition'] = f'inline; filename="{self.filename}.pdf"'
        return response

    @staticmethod
    def _find(value):
        try:
            return NivForm.objects.filter(id=int(value))
        except (TypeError, ValueError):
            return NivForm.objects.none()

    def get_context_data(self, **kwargs) -> dict:
        niv = self._find(self.request.GET.get('t', self.request.POST.get('t')))
        if not niv.exists():
            niv = self._find(self.request.GET.get('as', self.request.POST.get('as')))
        if not niv.exists():
            raise Http404('დანიშნულება ვერ მოიძებნა.')
        data = serializers.serialize("json", niv)
        data = json.loads(data)[0]['fields']
        for i in range(1, ROW_COUNT + 1):
            row = data[f'r{i}']
            if not isinstance(row, dict):
                row = data[f'r{i}'] = {}
            for d in ('d1', 'd2', 'd3'):
                if d not in row:
                    row[d] = {}

        data['allergy'] = self.get_yes_no(data['allergy'])
        data['diabetes'] = self.get_yes_no(data['diabetes'])
        data['neuro'] = self.get_yes_no(data['neuro'])
        data['radial_pulse'] = self.get_radial_pulse(data['radial_pulse'])
        return data

    @staticmethod
    def get_yes_no(value) -> str:
        if value:
            return f'{Symbols.CHECKED.value} {YesNo.YES} {Symbols.UNCHECKED.value} {YesNo.NO}'
        return f'{Symbols.UNCHECKED.value} {YesNo.YES} {Symbols.UNCHECKED.value} {YesNo.NO}'

    @staticmethod
    def get_radial_pulse(value) -> str:
        txt = ''
        for pulse in RadialPulse:
            if pulse == value:
                txt += f' {Symbols.CHECKED.value} {pulse}'
            else:
                txt += f' {Symbols.UNCHECKED.value} {pulse}'
        return txt.strip()


# --------------------------------------------------------------------------
# Templates (reusable medicine sets)
# --------------------------------------------------------------------------

def _save_template(request, name, rows, overwrite=False):
    """Create or (with overwrite) update a template. Returns (template, error)."""
    name = (name or '').strip()[:150]
    rows = clean_template_rows(rows)
    if not name:
        return None, 'შაბლონის სახელი აუცილებელია.'
    if not any(row['m'] for row in rows):
        return None, 'შაბლონში არ არის არცერთი მედიკამენტი.'
    existing = PrescriptionTemplate.objects.filter(name__iexact=name).first()
    if existing and not overwrite:
        return existing, 'exists'
    if existing:
        existing.rows = rows
        existing.save(update_fields=['rows', 'updated_at'])
        return existing, None
    tpl = PrescriptionTemplate.objects.create(
        name=name, rows=rows,
        created_by=request.user if request.user.is_authenticated else None,
    )
    return tpl, None


class TemplateListView(LoginRequiredMixin, generic.ListView):
    model = PrescriptionTemplate
    template_name = 'mdf/rx-templates.html'

    def get_queryset(self):
        qs = PrescriptionTemplate.objects.select_related('created_by').order_by('-use_count', 'name')
        q = self.request.GET.get('q', '').strip().lower()
        if q:
            qs = [t for t in qs if q in t.name.lower()
                  or any(q in m.lower() for m in t.medicines)]
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '').strip()
        return ctx


class TemplateSaveView(LoginRequiredMixin, generic.View):
    """POST JSON {name, rows, overwrite} -> save template (from the prescription page)."""
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body or b'{}')
        except ValueError:
            return JsonResponse({'error': 'bad json'}, status=400)
        tpl, error = _save_template(request, payload.get('name'), payload.get('rows'),
                                    overwrite=bool(payload.get('overwrite')))
        if error == 'exists':
            return JsonResponse({'exists': True, 'error': 'ასეთი შაბლონი უკვე არსებობს.'}, status=409)
        if error:
            return JsonResponse({'error': error}, status=400)
        return JsonResponse({'id': tpl.pk, 'name': tpl.name})


class TemplateFromPrescriptionView(LoginRequiredMixin, generic.View):
    """Copy the medicines/hours of an existing prescription into a template."""
    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        niv = get_object_or_404(NivForm, pk=pk)
        name = request.POST.get('name') or niv.title
        tpl, error = _save_template(request, name, niv.rows_data(),
                                    overwrite=request.POST.get('overwrite') == '1')
        if error == 'exists':
            messages.warning(request, f'შაბლონი „{tpl.name}“ უკვე არსებობს — აირჩიეთ სხვა სახელი.')
        elif error:
            messages.error(request, error)
        else:
            messages.success(request, f'შაბლონი „{tpl.name}“ შეიქმნა.')
        back = request.POST.get('next') or reverse('rx-templates')
        if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            back = reverse('rx-templates')
        return HttpResponseRedirect(back)


class TemplateRenameView(LoginRequiredMixin, generic.View):
    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        tpl = get_object_or_404(PrescriptionTemplate, pk=pk)
        name = (request.POST.get('name') or '').strip()[:150]
        if not name:
            messages.error(request, 'სახელი ცარიელია.')
        elif PrescriptionTemplate.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f'შაბლონი „{name}“ უკვე არსებობს.')
        else:
            tpl.name = name
            tpl.save(update_fields=['name', 'updated_at'])
            messages.success(request, 'სახელი შეიცვალა.')
        return HttpResponseRedirect(reverse('rx-templates'))


class TemplateDeleteView(LoginRequiredMixin, generic.View):
    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        tpl = get_object_or_404(PrescriptionTemplate, pk=pk)
        tpl.delete()
        messages.success(request, f'შაბლონი „{tpl.name}“ წაიშალა.')
        return HttpResponseRedirect(reverse('rx-templates'))


class TemplateUsedView(LoginRequiredMixin, generic.View):
    """Called by the page when a template is applied (for "most used" sorting)."""
    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        PrescriptionTemplate.objects.filter(pk=pk).update(use_count=F('use_count') + 1)
        return JsonResponse({'ok': True})


class NosologyAnalysisView(LoginRequiredMixin, generic.TemplateView):
    """Which medicines are prescribed for which nosology (by the written diagnosis)."""
    template_name = 'mdf/rx-analysis.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        get = self.request.GET
        mode = get.get('by', 'terms')
        if mode not in analysis.MODES:
            mode = 'terms'
        search = get.get('s', '').strip()
        nosology = get.get('q', '').strip()
        match_in = get.get('in', 'diagnosis')
        if match_in not in ('diagnosis', 'title', 'both'):
            match_in = 'diagnosis'
        ctx.update({
            'mode': mode, 'modes': analysis.MODES, 'search': search,
            'q': nosology, 'match_in': match_in,
            'total_prescriptions': NivForm.objects.count(),
        })
        # list on the left, the chosen nosology on the right (the first one by default)
        groups, groups_total = analysis.overview(mode, search)
        max_count = groups[0]['count'] if groups else 1
        for group in groups:
            group['width'] = round(100 * group['count'] / max_count)
        label = get.get('l', '').strip()
        if not nosology and groups:
            nosology, label, match_in = groups[0]['key'], groups[0]['label'], 'diagnosis'
        ctx.update({'groups': groups, 'groups_total': groups_total, 'q': nosology, 'match_in': match_in})
        if nosology:
            total, medicines = analysis.medicines_for(nosology, match_in)
            label = label or nosology
            ctx.update({'matched': total, 'medicines': medicines, 'label': label,
                        'suggested_name': label[:150]})
        return ctx


class TemplateFromAnalysisView(LoginRequiredMixin, generic.View):
    """Create a template from the medicines ticked on the analysis page."""
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        nosology = request.POST.get('q', '').strip()
        match_in = request.POST.get('in', 'diagnosis')
        chosen = set(request.POST.getlist('med'))
        _, medicines = analysis.medicines_for(nosology, match_in)
        selected = [m for m in medicines if m['key'] in chosen]
        back = (f"{reverse('rx-analysis')}?q={quote(nosology)}&in={quote(match_in)}"
                f"&l={quote(request.POST.get('l', ''))}")
        if not selected:
            messages.error(request, 'მონიშნეთ მინიმუმ ერთი მედიკამენტი.')
            return HttpResponseRedirect(back)
        tpl, error = _save_template(request, request.POST.get('name'),
                                    analysis.template_rows(selected),
                                    overwrite=request.POST.get('overwrite') == '1')
        if error == 'exists':
            messages.warning(request, f'შაბლონი „{tpl.name}“ უკვე არსებობს — აირჩიეთ სხვა სახელი '
                                      f'ან მონიშნეთ „ჩანაცვლება“.')
            return HttpResponseRedirect(back)
        if error:
            messages.error(request, error)
            return HttpResponseRedirect(back)
        messages.success(request, f'შაბლონი „{tpl.name}“ შეიქმნა ({len(selected)} მედიკამენტი).')
        return HttpResponseRedirect(reverse('rx-templates'))


# --------------------------------------------------------------------------
# Backup download
# --------------------------------------------------------------------------

BACKUP_MODELS = ['mdf.medicine', 'mdf.note', 'mdf.nivform', 'mdf.prescriptiontemplate',
                 'mdf.staffprofile', 'mdf.dutynote', 'mdf.boardpatient', 'mdf.boardentry', 'mdf.consentform', 'mdf.rosterperson', 'mdf.rosterday', 'mdf.rosterlog', 'auth.user']
# duty reports are not backed up: they must disappear after a month


def write_backup(stream):
    """Write a gzip-compressed JSON dump of all clinic data to ``stream``."""
    with gzip.GzipFile(fileobj=stream, mode='wb') as gz:
        text = io.TextIOWrapper(gz, encoding='utf-8')
        call_command('dumpdata', *BACKUP_MODELS, indent=1, stdout=text)
        text.flush()
        text.detach()


class BackupDownloadView(LoginRequiredMixin, generic.View):

    def get(self, request, *args, **kwargs):
        buf = io.BytesIO()
        write_backup(buf)
        from .models import SiteState
        SiteState.touch('backup_downloaded')
        stamp = timezone.localtime().strftime('%Y-%m-%d_%H%M')
        response = HttpResponse(buf.getvalue(), content_type='application/gzip')
        response['Content-Disposition'] = f'attachment; filename="medf-backup-{stamp}.json.gz"'
        return response

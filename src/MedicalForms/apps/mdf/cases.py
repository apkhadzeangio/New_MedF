"""Cases for the vascular conference: patients after angiography, which operation.

Phase 1: the case card, photos taken with the phone from the site, the
presentation mode and the conference decision. (Phase 2 adds GLASS/WIfI,
phase 3 the Doppler PDF.)

Photos live in settings.PRIVATE_MEDIA_ROOT, which no web server publishes;
PhotoView hands them out to logged-in users only.
"""
import datetime
import io
import json
import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .board import clean_text, doctors, person_name
from .duty import strip_astral
from . import doppler
from .models import BoardPatient, Case, CasePhoto, DopplerStudy

MAX_UPLOAD = 30 * 1024 * 1024        # one photo as it comes from the phone
FULL_SIZE = 2000                     # longest side kept (sharp on a projector)
THUMB_SIZE = 480

TEXT_FIELDS = ('diagnosis', 'comorbidities', 'past_history', 'history', 'findings', 'proposal')


class PhotoError(ValueError):
    pass


def process_photo(uploaded):
    """Uploaded image -> (full JPEG, thumbnail JPEG, width, height).

    Turned upright, shrunk, re-encoded: no location or other hidden data stays in the file.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    if (getattr(uploaded, 'size', 0) or 0) > MAX_UPLOAD:
        raise PhotoError('ფაილი ძალიან დიდია (მაქს. 30 მბ).')
    try:
        image = Image.open(uploaded)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise PhotoError('ეს ფაილი სურათად ვერ წავიკითხე (JPG, PNG ან WEBP გამოიყენეთ).')
    image = ImageOps.exif_transpose(image)
    if image.mode in ('RGBA', 'LA', 'P'):
        image = image.convert('RGBA')
        back = Image.new('RGB', image.size, 'white')
        back.paste(image, mask=image.split()[-1])
        image = back
    else:
        image = image.convert('RGB')

    def jpeg(img, size, quality):
        img = img.copy()
        img.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=quality, optimize=True, progressive=True)
        return out.getvalue(), img.size

    full, (width, height) = jpeg(image, FULL_SIZE, 86)
    thumb, _ = jpeg(image, THUMB_SIZE, 80)
    return full, thumb, width, height


def add_photo(case, uploaded, kind, user, taken_on=None):
    full, thumb, width, height = process_photo(uploaded)
    name = uuid.uuid4().hex
    photo = CasePhoto(case=case, kind=kind, width=width, height=height, created_by=user,
                      taken_on=taken_on or timezone.localdate())
    photo.image.save(f'{name}.jpg', ContentFile(full), save=False)
    photo.thumb.save(f'{name}-t.jpg', ContentFile(thumb), save=False)
    photo.save()
    Case.objects.filter(pk=case.pk).update(updated_at=timezone.now(), updated_by=user)
    return photo


def _date(value):
    try:
        return datetime.date.fromisoformat((value or '').strip())
    except ValueError:
        return None


def _photos_by_kind(case):
    groups = []
    photos = list(case.photos.all())
    for kind, label in CasePhoto.Kind.choices:
        items = [p for p in photos if p.kind == kind]
        if items:
            groups.append({'kind': kind, 'label': label, 'photos': items})
    return groups


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
            qs = qs.filter(Q(patient_name__icontains=q) | Q(diagnosis__icontains=q) |
                           Q(proposal__icontains=q) | Q(decision__icontains=q))
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


def models_nulls_last(field):
    from django.db.models import F
    return F(field).asc(nulls_last=True)


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
                values.update(patient_name=patient.name, diagnosis=patient.diagnosis,
                              doctor=patient.doctor_id or request.user.pk)
        return values

    @staticmethod
    def _render(request, case, values):
        return render(request, 'mdf/cases/form.html', {
            'case': case, 'values': values, 'doctors': doctors(),
            'sexes': Case.Sex.choices[1:], 'sides': Case.Side.choices[1:],
        })


class CaseView(LoginRequiredMixin, generic.TemplateView):
    template_name = 'mdf/cases/detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        case = get_object_or_404(Case.objects.select_related('doctor', 'decided_by', 'created_by', 'updated_by'),
                                 pk=kwargs['pk'])
        ctx.update({
            'case': case, 'groups': _photos_by_kind(case), 'kinds': CasePhoto.Kind.choices,
            'dopplers': [doppler_view(d) for d in case.dopplers.all()],
            'stagings': staging_views(case), 'limbs': _limbs_of(case),
            'kind': self.request.GET.get('kind') or CasePhoto.Kind.WOUND,
            'today': timezone.localdate(),
        })
        return ctx


class PhotoUploadView(LoginRequiredMixin, generic.View):
    """One or more photos. The site's script sends them one by one (already shrunk on the phone)."""

    def post(self, request, pk):
        case = get_object_or_404(Case, pk=pk)
        kind = request.POST.get('kind')
        if kind not in CasePhoto.Kind.values:
            kind = CasePhoto.Kind.WOUND
        taken_on = _date(request.POST.get('taken_on'))
        if taken_on and taken_on > timezone.localdate():
            taken_on = None
        files = request.FILES.getlist('photo')
        ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        added, errors = 0, []
        for f in files[:20]:
            try:
                add_photo(case, f, kind, request.user, taken_on)
                added += 1
            except PhotoError as e:
                errors.append(f'{f.name}: {e}')
        if ajax:
            return JsonResponse({'added': added, 'errors': errors}, status=200 if added or not errors else 400)
        for e in errors:
            messages.error(request, e)
        if added:
            messages.success(request, f'დაემატა {added} ფოტო.')
        elif not errors:
            messages.error(request, 'ფოტო არ არის არჩეული.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + f'?kind={kind}#photos')


class PhotoView(LoginRequiredMixin, generic.View):
    """The photo file itself, for logged-in users only."""

    def get(self, request, pk, size='full'):
        photo = get_object_or_404(CasePhoto, pk=pk)
        field = photo.thumb if size == 'thumb' else photo.image
        try:
            fh = field.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404
        response = FileResponse(fh, content_type='image/jpeg')
        response['Cache-Control'] = 'private, max-age=604800'
        return response


class PhotoEditView(LoginRequiredMixin, generic.View):
    """Change the caption / type / date of a photo, or delete it."""

    def post(self, request, pk):
        photo = get_object_or_404(CasePhoto, pk=pk)
        case = photo.case
        if request.POST.get('action') == 'delete':
            photo.delete()
            messages.success(request, 'ფოტო წაიშალა.')
        else:
            photo.caption = ' '.join(strip_astral(request.POST.get('caption', '')).split())[:255]
            if request.POST.get('kind') in CasePhoto.Kind.values:
                photo.kind = request.POST['kind']
            taken_on = _date(request.POST.get('taken_on'))
            if taken_on and taken_on <= timezone.localdate():
                photo.taken_on = taken_on
            photo.save(update_fields=['caption', 'kind', 'taken_on'])
        Case.objects.filter(pk=case.pk).update(updated_at=timezone.now(), updated_by=request.user)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True})
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#photos')


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
            return JsonResponse({'ok': True, 'status': case.status, 'status_label': case.get_status_display(),
                                 'by': person_name(case.decided_by) if case.decision else ''})
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
            Prefetch('photos', queryset=CasePhoto.objects.order_by('taken_on', 'id')), 'dopplers')
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
        ctx.update({'cases': cases, 'anon': self.request.GET.get('anon') == '1',
                    'back': self.request.GET.get('back') or reverse('cases')})
        if not ctx['back'].startswith('/') or ctx['back'].startswith('//'):
            ctx['back'] = reverse('cases')
        return ctx


# --------------------------------------------------------------------------
# Doppler (Smart-XT report PDF -> curves and numbers)
# --------------------------------------------------------------------------

PRESSURE_KEYS = (('arm', 'მხარი'), ('pt', 'PT'), ('dp', 'DP'), ('toe', 'დიდი თითი'))
INDEX_KEYS = (('abi_pt', 'ABI (PT)'), ('abi_dp', 'ABI (DP)'), ('tbi', 'TBI'))
ROW_PRESSURE = {'ankle_pt': 'pt', 'metatarsal_dp': 'dp', 'toe': 'toe'}


def doppler_view(study):
    """Everything the panel shows, ready for the template."""
    data = study.data or {}
    sites = {(s['side'], s['key']): s for s in data.get('sites', [])}
    rows = []
    for key, label, label_en in doppler.SITES:
        row = {'key': key, 'label': label, 'label_en': label_en, 'sides': []}
        for side in ('R', 'L'):
            site = dict(sites.get((side, key)) or {'key': key, 'side': side, 'unit': 'mV/V' if key == 'toe' else 'cm/s'})
            site['chart'] = doppler.chart(site)
            site['pressure'] = (data.get('pressures', {}).get(side) or {}).get(ROW_PRESSURE.get(key))
            row['sides'].append(site)
        rows.append(row)
    sides = []
    for side, name in (('R', 'მარჯვენა'), ('L', 'მარცხენა')):
        idx = data.get('indices', {}).get(side) or {}
        prs = data.get('pressures', {}).get(side) or {}
        indices = []
        for key, label in INDEX_KEYS:
            value = idx.get(key)
            css, text = doppler.tbi_class(value) if key == 'tbi' else doppler.abi_class(value)
            indices.append({'key': key, 'label': label, 'value': value, 'css': css, 'text': text})
        sides.append({'side': side, 'name': name, 'indices': indices,
                      'pressures': [{'key': k, 'label': lb, 'value': prs.get(k)} for k, lb in PRESSURE_KEYS]})
    edit_rows = []
    for prefix, keys, unit in (('p', PRESSURE_KEYS, ', mmHg'), ('i', INDEX_KEYS, '')):
        source = data.get('pressures' if prefix == 'p' else 'indices', {})
        for key, label in keys:
            edit_rows.append({'label': label + unit, 'cells': [
                {'name': f'{prefix}_{side}_{key}', 'value': (source.get(side) or {}).get(key)} for side in ('R', 'L')]})
    return {'study': study, 'rows': rows, 'sides': sides, 'edit_rows': edit_rows,
            'warnings': data.get('warnings', [])}


class DopplerUploadView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        case = get_object_or_404(Case, pk=pk)
        uploaded = request.FILES.get('report')
        back = HttpResponseRedirect(reverse('case', args=[case.pk]) + '#doppler')
        if not uploaded:
            messages.error(request, 'აირჩიეთ დოპლერის PDF.')
            return back
        try:
            image, raw = doppler.load(uploaded)
            data = doppler.parse(image)
        except doppler.DopplerError as e:
            messages.error(request, f'დოპლერი ვერ წავიკითხე: {e}')
            return back
        performed_on = _date(request.POST.get('performed_on'))
        if not performed_on or performed_on > timezone.localdate():
            performed_on = timezone.localdate()
        study = DopplerStudy(case=case, performed_on=performed_on, data=data, created_by=request.user)
        ext = 'pdf' if raw[:5] == b'%PDF-' else 'png'
        study.file.save(f'{uuid.uuid4().hex}.{ext}', ContentFile(raw), save=False)
        study.save()
        Case.objects.filter(pk=case.pk).update(updated_at=timezone.now(), updated_by=request.user)
        if data['warnings']:
            messages.warning(request, 'დოპლერი წაკითხულია — რამდენიმე მნიშვნელობა გადაამოწმეთ (ყვითლად მონიშნულია).')
        else:
            messages.success(request, 'დოპლერი წაკითხულია.')
        return back


class DopplerEditView(LoginRequiredMixin, generic.View):
    """Correct numbers by hand, change the date, or delete the study."""

    def post(self, request, pk):
        study = get_object_or_404(DopplerStudy, pk=pk)
        case = study.case
        if request.POST.get('action') == 'delete':
            study.delete()
            messages.success(request, 'დოპლერი წაიშალა.')
            return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#doppler')
        data = study.data or {}

        def num(name, cast=float, low=-1000, high=1000):
            raw = (request.POST.get(name) or '').strip().replace(',', '.')
            try:
                value = cast(float(raw))
            except ValueError:
                return None
            return value if low <= value <= high else None

        for side in ('R', 'L'):
            for key, _ in PRESSURE_KEYS:
                data.setdefault('pressures', {}).setdefault(side, {})[key] = num(f'p_{side}_{key}', int, 0, 400)
            for key, _ in INDEX_KEYS:
                data.setdefault('indices', {}).setdefault(side, {})[key] = num(f'i_{side}_{key}', float, 0, 3)
        for site in data.get('sites', []):
            site['value'] = num(f's_{site["side"]}_{site["key"]}', float, -500, 500)
            site['hr'] = num(f'h_{site["side"]}_{site["key"]}', int, 0, 300)
        data['warnings'] = []                   # checked by a doctor
        study.data = data
        performed_on = _date(request.POST.get('performed_on'))
        if performed_on and performed_on <= timezone.localdate():
            study.performed_on = performed_on
        study.save()
        messages.success(request, 'დოპლერის მონაცემები შენახულია.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#doppler')


class DopplerFileView(LoginRequiredMixin, generic.View):
    """The original report, for logged-in users only."""

    def get(self, request, pk):
        study = get_object_or_404(DopplerStudy, pk=pk)
        try:
            fh = study.file.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404
        is_pdf = study.file.name.endswith('.pdf')
        response = FileResponse(fh, content_type='application/pdf' if is_pdf else 'image/png')
        response['Content-Disposition'] = 'inline; filename="doppler.%s"' % ('pdf' if is_pdf else 'png')
        response['Cache-Control'] = 'private, max-age=3600'
        return response


# --------------------------------------------------------------------------
# Staging: artery map, GLASS, WIfI, PLAN (tables in staging.py)
# --------------------------------------------------------------------------

LIMBS = (('R', 'მარჯვენა'), ('L', 'მარცხენა'))


def _limbs_of(case):
    """Which legs a case is about (both when not said)."""
    if case.side in ('R', 'L'):
        return [l for l in LIMBS if l[0] == case.side]
    return list(LIMBS)


def _latest_doppler(case):
    study = case.dopplers.order_by('-performed_on', '-id').first()
    return study.data if study else None


def staging_views(case):
    """[{side, name, limb, result, map}] for the limbs that have been staged."""
    from . import staging
    out = []
    doppler_data = _latest_doppler(case) if case.staging else None
    for side, name in LIMBS:
        limb = (case.staging or {}).get(side)
        if not limb:
            continue
        result = staging.compute(limb, staging.doppler_measurements(doppler_data, side))
        out.append({'side': side, 'name': name, 'limb': limb, 'r': result, 'outline': staging.LEG_OUTLINE,
                    'map': staging.segment_view(limb.get('segments') or {})})
    return out


class StagingView(LoginRequiredMixin, generic.View):
    template_name = 'mdf/cases/staging.html'

    def _context(self, case, side, limb):
        from . import staging
        measured = staging.doppler_measurements(_latest_doppler(case), side)
        return {
            'case': case, 'side': side, 'limbs': _limbs_of(case) if len(_limbs_of(case)) > 1 else LIMBS,
            'side_name': dict(LIMBS)[side], 'limb': limb, 'measured': measured,
            'r': staging.compute(limb, measured), 'map': staging.segment_view(limb.get('segments') or {}),
            'S': staging, 'outline': staging.LEG_OUTLINE,
            'segments_json': limb.get('segments') or {},
        }

    def get(self, request, pk, side):
        case = get_object_or_404(Case, pk=pk)
        if side not in dict(LIMBS):
            raise Http404
        from . import staging
        limb = (case.staging or {}).get(side) or staging.clean({})
        return render(request, self.template_name, self._context(case, side, limb))

    def post(self, request, pk, side):
        from . import staging
        case = get_object_or_404(Case, pk=pk)
        if side not in dict(LIMBS):
            raise Http404
        data = request.POST.dict()
        try:
            data['segments'] = json.loads(request.POST.get('segments') or '{}')
        except ValueError:
            data['segments'] = {}
        limb = staging.clean(data)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.POST.get('preview'):
            ctx = self._context(case, side, limb) | {'live': True}
            return JsonResponse({'html': render(request, 'mdf/cases/_staging_result.html', ctx).content.decode()})
        case.staging = {**(case.staging or {}), side: limb}
        case.updated_by = request.user
        case.save(update_fields=['staging', 'updated_by', 'updated_at'])
        messages.success(request, f'{dict(LIMBS)[side]} კიდურის სტადირება შენახულია.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#staging')

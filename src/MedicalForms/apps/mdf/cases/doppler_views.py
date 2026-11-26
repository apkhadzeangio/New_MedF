"""Doppler (Hadeco Smart-XT PDF & manual hemodynamics) upload, edit, and serving views."""
import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .. import doppler
from ..models import Case, DopplerStudy
from .photos import _date
from .staging_views import LIMBS, _latest_doppler, _limbs_of

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
        sides.append({
            'side': side, 'name': name, 'indices': indices,
            'pressures': [{'key': k, 'label': lb, 'value': prs.get(k)} for k, lb in PRESSURE_KEYS],
        })
    edit_rows = []
    for prefix, keys, unit in (('p', PRESSURE_KEYS, ', mmHg'), ('i', INDEX_KEYS, '')):
        source = data.get('pressures' if prefix == 'p' else 'indices', {})
        for key, label in keys:
            edit_rows.append({
                'label': label + unit,
                'cells': [{'name': f'{prefix}_{side}_{key}', 'value': (source.get(side) or {}).get(key)} for side in ('R', 'L')],
            })
    return {'study': study, 'rows': rows, 'sides': sides, 'edit_rows': edit_rows, 'warnings': data.get('warnings', [])}


class DopplerUploadView(LoginRequiredMixin, generic.View):
    """Dedicated Doppler window: enter ABI/TBI/AP/TP by hand or upload a Hadeco Smart-XT PDF."""
    template_name = 'mdf/cases/doppler.html'

    def _context(self, case):
        from .. import staging

        today = timezone.localdate()
        pdf_data = _latest_doppler(case)
        active_sides = {s for s, _ in _limbs_of(case)}
        manual_limbs = []
        for side, name in LIMBS:
            limb = staging.get_limb(case.staging, side, today=today)
            pdf_m = staging.doppler_measurements(pdf_data, side)
            manual_limbs.append({
                'side': side,
                'name': name,
                'primary': side in active_sides,
                'limb': limb,
                'pdf': pdf_m,
            })
        default_side = _limbs_of(case)[0][0]
        return {
            'case': case,
            'default_side': default_side,
            'manual_limbs': manual_limbs,
            'dopplers': [doppler_view(d) for d in case.dopplers.all()],
            'today': today,
        }

    def get(self, request, pk):
        case = get_object_or_404(Case, pk=pk)
        return render(request, self.template_name, self._context(case))

    def post(self, request, pk):
        from .. import staging

        case = get_object_or_404(Case, pk=pk)
        back = HttpResponseRedirect(reverse('case', args=[case.pk]) + '#doppler')
        if request.POST.get('action') == 'manual':
            today = timezone.localdate()
            staging_map = dict(case.staging) if isinstance(case.staging, dict) else {}
            for side, _ in LIMBS:
                had_side = isinstance(staging_map.get(side), dict)
                existing = staging.get_limb(staging_map, side, today=today)
                candidate = dict(existing)
                for field in ('abi', 'tbi', 'ap', 'tp'):
                    candidate[field] = request.POST.get(f'{field}_{side}', '')
                cleaned = staging.clean(candidate, today=today)
                if had_side or staging.has_manual_doppler(cleaned):
                    staging_map = staging.save_limb(staging_map, side, cleaned, today=today)
            case.staging = staging_map
            case.updated_by = request.user
            case.save(update_fields=['staging', 'updated_by', 'updated_at'])
            messages.success(request, 'დოპლერის პარამეტრები შენახულია.')
            return back

        uploaded = request.FILES.get('report')
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
        ext = 'pdf' if is_pdf else 'png'
        response['Content-Disposition'] = f'inline; filename="doppler.{ext}"'
        response['Cache-Control'] = 'private, max-age=3600'
        return response

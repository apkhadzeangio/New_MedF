"""Case staging helpers (GLASS/WIfI/PLAN/limb metrics) and dedicated Angiography/WIfI views."""
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .. import doppler
from ..models import Case

LIMBS = (('R', 'მარჯვენა'), ('L', 'მარცხენა'))


def _limbs_of(case):
    """Which legs a case is about (both when not said)."""
    if case.side in ('R', 'L'):
        return [limb for limb in LIMBS if limb[0] == case.side]
    return list(LIMBS)


def _latest_doppler(case):
    studies = sorted(case.dopplers.all(), key=lambda s: (s.performed_on, s.pk), reverse=True)
    return studies[0].data if studies else None


def case_limb_metrics(case):
    """Mandatory per-limb summary (GLASS, WIfI, PLAN, ABI, TBI, AP, Toe pressure) for presentation & detail."""
    from .. import staging

    today = timezone.localdate()
    doppler_data = _latest_doppler(case)
    staging_map = case.staging if isinstance(case.staging, dict) else {}

    target_sides = {s for s, _ in _limbs_of(case)}
    for s, _ in LIMBS:
        if staging_map.get(s):
            target_sides.add(s)
    if case.side == '':
        # When side wasn't specified on the card, prefer staged/measured limbs if only one side has data
        with_data = {
            s for s, _ in LIMBS
            if staging_map.get(s) or staging.doppler_measurements(doppler_data, s)
        }
        if len(with_data) == 1:
            target_sides = with_data

    rows = []
    for side, name in LIMBS:
        if side not in target_sides:
            continue
        raw_limb = staging_map.get(side)
        limb = staging.get_limb(staging_map, side, today=today) if raw_limb else staging.clean({}, today=today)
        measured = staging.doppler_measurements(doppler_data, side)
        r = staging.compute(limb, measured)
        m = r['measured']
        tp = m.get('tp')
        if tp is None:
            tp_css = ''
        elif tp < 40:
            tp_css = 'bad'
        elif tp < 60:
            tp_css = 'border'
        else:
            tp_css = 'ok'
        rows.append({
            'side': side,
            'name': name,
            'limb': limb,
            'r': r,
            'abi': m.get('abi'),
            'abi_css': doppler.abi_class(m.get('abi'))[0],
            'tbi': m.get('tbi'),
            'tbi_css': doppler.tbi_class(m.get('tbi'))[0],
            'ap': m.get('ap'),
            'tp': tp,
            'tp_css': tp_css,
            'has_any_doppler': any(m.get(k) is not None for k in ('abi', 'tbi', 'ap', 'tp')),
            'has_manual_doppler': staging.has_manual_doppler(raw_limb),
        })
    return rows


def case_plan_info(case, limb_metrics=None):
    """PLAN algorithm conclusion(s) for display inside 'შეთავაზება' and 1-click insertion into proposal."""
    rows = limb_metrics if limb_metrics is not None else case_limb_metrics(case)
    items = [
        {'side': row['side'], 'name': row['name'], 'plan': row['r']['plan'], 'label': row['r']['plan_label']}
        for row in rows if row['r'].get('plan_label')
    ]
    show_side = len(items) > 1 or (len(items) == 1 and case.side in ('B', '') and len(rows) > 1)
    if not items:
        insert_text = ''
    elif len(items) == 1 and not show_side:
        insert_text = f'PLAN: {items[0]["label"]}'
    else:
        insert_text = '\n'.join(f'PLAN ({it["name"]}): {it["label"]}' for it in items)
    return {'items': items, 'show_side': show_side, 'insert_text': insert_text}


def staging_views(case):
    """[{side, name, limb, r, am, fm, has_angio, has_wifi}] for the limbs that have been staged."""
    from .. import staging

    out = []
    doppler_data = _latest_doppler(case) if case.staging else None
    today = timezone.localdate()
    for side, name in LIMBS:
        raw = (case.staging or {}).get(side)
        if not raw or not staging.has_staging(raw):
            continue
        limb = staging.get_limb(case.staging, side, today=today)
        result = staging.compute(limb, staging.doppler_measurements(doppler_data, side))
        has_angio = staging.has_angiography(limb)
        out.append({
            'side': side, 'name': name, 'limb': limb, 'r': result,
            'has_angio': has_angio, 'has_wifi': staging.has_wifi(limb),
            'interventions': staging.interventions_view(limb.get('interventions')),
            'am': staging.map_context(limb.get('segments') or {}, side, limb.get('interventions')) if has_angio else None,
            'fm': staging.foot_context(
                limb.get('segments') or {}, side, limb.get('interventions'),
                limb.get('wound_angiosome') or '',
            ) if has_angio else None,
        })
    return out


class StagingView(LoginRequiredMixin, generic.View):
    """Dedicated Angiography window: artery map, prior interventions, GLASS, and copy-paste protocol."""
    template_name = 'mdf/cases/staging.html'

    def _context(self, case, side, limb):
        from .. import staging

        today = timezone.localdate()
        measured = staging.doppler_measurements(_latest_doppler(case), side)
        other = 'L' if side == 'R' else 'R'
        other_raw = (case.staging or {}).get(other)
        has_other_angio = staging.has_angiography(other_raw)
        protocol_text = staging.angiography_protocol(limb, side, today=today)
        protocol_bilateral = (
            staging.angiography_protocol_bilateral(staging.save_limb(case.staging, side, limb, today=today), today=today)
            if has_other_angio else ''
        )
        return {
            'case': case, 'side': side, 'limbs': _limbs_of(case) if len(_limbs_of(case)) > 1 else LIMBS,
            'side_name': dict(LIMBS)[side], 'limb': limb, 'measured': measured,
            'r': staging.compute(limb, measured),
            'am': staging.map_context(limb.get('segments') or {}, side, limb.get('interventions')),
            'fm': staging.foot_context(
                limb.get('segments') or {}, side, limb.get('interventions'),
                limb.get('wound_angiosome') or '',
            ),
            'zoom_zones': staging.zoom_zones(),
            'S': staging,
            'segments_json': limb.get('segments') or {},
            'interventions_json': limb.get('interventions') or [],
            'iv_catalog_json': staging.intervention_catalog(),
            'protocol_text': protocol_text,
            'protocol_bilateral_text': protocol_bilateral,
            'mode': 'angio',
        }

    def get(self, request, pk, side):
        case = get_object_or_404(Case, pk=pk)
        if side not in dict(LIMBS):
            raise Http404
        from .. import staging

        limb = staging.get_limb(case.staging, side, today=timezone.localdate())
        return render(request, self.template_name, self._context(case, side, limb))

    def post(self, request, pk, side):
        from .. import staging

        case = get_object_or_404(Case, pk=pk)
        if side not in dict(LIMBS):
            raise Http404
        today = timezone.localdate()
        existing = staging.get_limb(case.staging, side, today=today)
        data = dict(existing)

        if request.POST.get('_form') == 'angio':
            for key in ('tap', 'ai', 'cfa', 'fp', 'ip', 'pedal'):
                data[key] = request.POST.get(key, '')
            data['fp_calc'] = 'fp_calc' in request.POST
            data['ip_calc'] = 'ip_calc' in request.POST
        else:
            for key, val in request.POST.items():
                if key not in ('csrfmiddlewaretoken', 'preview', '_form'):
                    data[key] = val
            if 'fp' in request.POST or 'fp_calc' in request.POST:
                data['fp_calc'] = 'fp_calc' in request.POST
            if 'ip' in request.POST or 'ip_calc' in request.POST:
                data['ip_calc'] = 'ip_calc' in request.POST
            if 'w' in request.POST or 'unsalvageable' in request.POST:
                data['unsalvageable'] = 'unsalvageable' in request.POST

        if 'segments' in request.POST:
            try:
                data['segments'] = json.loads(request.POST.get('segments') or '{}')
            except ValueError:
                data['segments'] = {}
        if 'interventions' in request.POST:
            try:
                data['interventions'] = json.loads(request.POST.get('interventions') or '[]')
            except ValueError:
                data['interventions'] = []

        limb = staging.clean(data, today=today)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.POST.get('preview'):
            ctx = self._context(case, side, limb) | {'live': True, 'interactive': True}
            return JsonResponse({
                'html': render(request, 'mdf/cases/_staging_result.html', ctx).content.decode(),
                'map_html': render(request, 'mdf/cases/_artery_map.html', ctx).content.decode(),
                'foot_html': render(request, 'mdf/cases/_foot_map.html', ctx).content.decode(),
                'protocol_text': ctx['protocol_text'],
                'protocol_bilateral_text': ctx['protocol_bilateral_text'],
            })
        case.staging = staging.save_limb(case.staging, side, limb, today=today)
        case.updated_by = request.user
        case.save(update_fields=['staging', 'updated_by', 'updated_at'])
        messages.success(request, f'{dict(LIMBS)[side]} კიდურის ანგიოგრაფია შენახულია.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#staging')


class WifiView(LoginRequiredMixin, generic.View):
    """Dedicated WIfI & PLAN clinical assessment window (separate from Angiography and Doppler)."""
    template_name = 'mdf/cases/wifi.html'

    def _context(self, case, side, limb):
        from .. import staging

        measured = staging.doppler_measurements(_latest_doppler(case), side)
        r = staging.compute(limb, measured)
        return {
            'case': case, 'side': side, 'limbs': _limbs_of(case) if len(_limbs_of(case)) > 1 else LIMBS,
            'side_name': dict(LIMBS)[side], 'limb': limb, 'measured': measured,
            'doppler_eff': r['measured'],
            'r': r,
            'fm': staging.foot_context(
                limb.get('segments') or {}, side, limb.get('interventions'),
                limb.get('wound_angiosome') or '',
            ),
            'S': staging,
            'mode': 'wifi',
        }

    def get(self, request, pk, side):
        case = get_object_or_404(Case, pk=pk)
        if side not in dict(LIMBS):
            raise Http404
        from .. import staging

        limb = staging.get_limb(case.staging, side, today=timezone.localdate())
        return render(request, self.template_name, self._context(case, side, limb))

    def post(self, request, pk, side):
        from .. import staging

        case = get_object_or_404(Case, pk=pk)
        if side not in dict(LIMBS):
            raise Http404
        today = timezone.localdate()
        existing = staging.get_limb(case.staging, side, today=today)
        data = dict(existing)
        for key in ('w', 'i', 'fi', 'wound_angiosome', 'risk', 'vein', 'note'):
            data[key] = request.POST.get(key, '')
        data['unsalvageable'] = 'unsalvageable' in request.POST
        limb = staging.clean(data, today=today)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.POST.get('preview'):
            ctx = self._context(case, side, limb) | {'live': True, 'interactive': False}
            return JsonResponse({
                'html': render(request, 'mdf/cases/_staging_result.html', ctx).content.decode(),
                'foot_html': render(request, 'mdf/cases/_foot_map.html', ctx).content.decode(),
            })
        case.staging = staging.save_limb(case.staging, side, limb, today=today)
        case.updated_by = request.user
        case.save(update_fields=['staging', 'updated_by', 'updated_at'])
        messages.success(request, f'{dict(LIMBS)[side]} კიდურის WIfI და კლინიკური შეფასება შენახულია.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#staging')

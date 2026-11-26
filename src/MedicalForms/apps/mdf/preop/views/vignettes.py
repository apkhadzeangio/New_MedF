"""Clinician 40 Clinical Vignettes verification view ('ვინიეტები') with doctor sign-off."""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ...board import clean_text
from ...models import PreopRuleSignoff
from ..registry import REGISTRY_VERSION, registry_summary
from ..vignettes import (
    CATEGORY_LABELS,
    VIGNETTES,
    VIGNETTES_BY_ID,
    evaluate_all_vignettes,
)


def _vignette_signoffs():
    qs = PreopRuleSignoff.objects.filter(
        item_kind=PreopRuleSignoff.ItemKind.VIGNETTE,
    ).select_related('signed_by', 'signed_by__profile')
    return {row.item_id: row for row in qs}


def _category_pills():
    counts = {}
    for v in VIGNETTES:
        counts[v.category] = counts.get(v.category, 0) + 1
    pills = [('all', 'ყველა მიმართულება', len(VIGNETTES))]
    for key, lbl in CATEGORY_LABELS.items():
        if counts.get(key):
            pills.append((key, lbl, counts[key]))
    return pills


def _filter_vignette_rows(results, cat_filter, status_filter, query, signoffs):
    q_low = (query or '').strip().lower()
    rows = []
    for res in results:
        v = res['vignette']
        so = signoffs.get(v.id)
        is_signed = bool(so and so.verdict == PreopRuleSignoff.Verdict.APPROVED)
        is_flagged = bool(so and so.verdict == PreopRuleSignoff.Verdict.FLAGGED)
        if cat_filter != 'all' and v.category != cat_filter:
            continue
        if status_filter == 'failed' and res['passed']:
            continue
        if status_filter == 'unsigned' and is_signed:
            continue
        if status_filter == 'signed' and not is_signed:
            continue
        if status_filter == 'flagged' and not is_flagged:
            continue
        if q_low:
            blob = f'{v.id} {v.title_ka} {v.summary_ka} {v.expected_summary_ka} {v.source_ref}'.lower()
            if q_low not in blob:
                continue
        rows.append({
            'res': res,
            'v': v,
            'ev': res['ev'],
            'cat_label': CATEGORY_LABELS.get(v.category, v.category),
            'signoff': so,
            'is_signed': is_signed,
            'is_flagged': is_flagged,
        })
    return rows


class PreopVignettesView(LoginRequiredMixin, generic.View):
    """Clinician review page running all 40 synthetic clinical vignettes live."""

    template_name = 'mdf/preop/vignettes.html'

    def get(self, request):
        cat_filter = request.GET.get('cat') or 'all'
        if cat_filter != 'all' and cat_filter not in CATEGORY_LABELS:
            cat_filter = 'all'
        status_filter = request.GET.get('status') or 'all'
        if status_filter not in ('all', 'unsigned', 'signed', 'flagged', 'failed'):
            status_filter = 'all'
        query = (request.GET.get('q') or '').strip()

        run = evaluate_all_vignettes()
        signoffs = _vignette_signoffs()
        rows = _filter_vignette_rows(run['results'], cat_filter, status_filter, query, signoffs)
        signed_count = sum(1 for v in VIGNETTES if v.id in signoffs and signoffs[v.id].verdict == PreopRuleSignoff.Verdict.APPROVED)
        flagged_count = sum(1 for so in signoffs.values() if so.verdict == PreopRuleSignoff.Verdict.FLAGGED)

        status_pills = [
            ('all', 'ყველა ვინიეტა', run['total']),
            ('unsigned', 'ხელმოწერის მოლოდინში', run['total'] - signed_count),
            ('signed', 'ექიმის დადასტურებული', signed_count),
            ('flagged', 'გადახედვაზე', flagged_count),
            ('failed', 'შეუსაბამო', run['failed_count']),
        ]
        return render(request, self.template_name, {
            'summary': registry_summary(),
            'run': run,
            'rows': rows,
            'cat_filter': cat_filter,
            'status_filter': status_filter,
            'query': query,
            'category_pills': _category_pills(),
            'status_pills': status_pills,
            'signed_count': signed_count,
            'flagged_count': flagged_count,
        })

    def post(self, request):
        vid = (request.POST.get('vignette_id') or '').strip()
        if vid not in VIGNETTES_BY_ID:
            messages.error(request, 'მითითებული ვინიეტა ვერ მოიძებნა.')
            return HttpResponseRedirect(reverse('preop-vignettes'))

        verdict = request.POST.get('verdict') or PreopRuleSignoff.Verdict.APPROVED
        if verdict not in PreopRuleSignoff.Verdict.values:
            verdict = PreopRuleSignoff.Verdict.APPROVED
        note = clean_text(request.POST.get('note'), 300).replace('\n', ' ')

        PreopRuleSignoff.objects.update_or_create(
            item_kind=PreopRuleSignoff.ItemKind.VIGNETTE,
            item_id=vid,
            defaults={
                'registry_version': REGISTRY_VERSION,
                'verdict': verdict,
                'note': note,
                'signed_by': request.user,
                'signed_at': timezone.now(),
            },
        )
        v = VIGNETTES_BY_ID[vid]
        if verdict == PreopRuleSignoff.Verdict.FLAGGED:
            messages.info(request, f'ვინიეტა „{v.id}“ მოინიშნა გადახედვისთვის.')
        else:
            messages.success(request, f'ვინიეტა დადასტურებულია ექიმის მიერ: {v.title_ka}.')

        params = {}
        for key in ('cat', 'status', 'q'):
            val = (request.POST.get(f'ret_{key}') or '').strip()
            if val and val != 'all':
                params[key] = val
        url = reverse('preop-vignettes')
        if params:
            url = f'{url}?{urlencode(params)}'
        return HttpResponseRedirect(f'{url}#vig-{vid}')

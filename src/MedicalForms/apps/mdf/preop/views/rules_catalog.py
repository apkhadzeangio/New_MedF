"""Clinician Clinical Rule Registry view ('კლინიკური წესები') with doctor sign-off."""
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
from ..registry import (
    DOMAIN_LABELS,
    KIND_LABELS,
    REGISTRY_VERSION,
    RULES,
    RULES_BY_ID,
    STATUS_LABELS,
    STATUS_LOCAL,
    STATUS_VERIFIED,
    list_rules,
    registry_summary,
)


def _signoff_map_for_rules():
    qs = PreopRuleSignoff.objects.filter(
        item_kind=PreopRuleSignoff.ItemKind.RULE,
    ).select_related('signed_by', 'signed_by__profile')
    return {row.item_id: row for row in qs}


def _build_domain_pills(signoffs):
    counts = {}
    for entry in RULES:
        counts[entry.domain] = counts.get(entry.domain, 0) + 1
    pills = [('all', 'ყველა დარგი', len(RULES))]
    for key, label in DOMAIN_LABELS.items():
        if counts.get(key):
            pills.append((key, label, counts[key]))
    return pills


def _filter_rule_rows(domain, status_filter, query, signoffs):
    raw_status = None if status_filter in ('all', 'unsigned-local') else status_filter
    entries = list_rules(domain=domain, status=raw_status, query=query)
    rows = []
    for entry in entries:
        so = signoffs.get(entry.id)
        if status_filter == 'unsigned-local':
            if entry.status != STATUS_LOCAL:
                continue
            if so and so.verdict == PreopRuleSignoff.Verdict.APPROVED:
                continue
        rows.append({
            'entry': entry,
            'meta': entry.to_meta(),
            'signoff': so,
            'is_local': entry.status == STATUS_LOCAL,
            'is_signed': bool(so and so.verdict == PreopRuleSignoff.Verdict.APPROVED),
            'is_flagged': bool(so and so.verdict == PreopRuleSignoff.Verdict.FLAGGED),
        })
    return rows


class PreopRulesView(LoginRequiredMixin, generic.View):
    """Clinician catalog of every preoperative rule, primary source, and local sign-off."""

    template_name = 'mdf/preop/rules.html'

    def get(self, request):
        domain = request.GET.get('domain') or 'all'
        if domain != 'all' and domain not in DOMAIN_LABELS:
            domain = 'all'
        status_filter = request.GET.get('status') or 'all'
        if status_filter not in ('all', STATUS_VERIFIED, STATUS_LOCAL, 'unsigned-local'):
            status_filter = 'all'
        query = (request.GET.get('q') or '').strip()

        signoffs = _signoff_map_for_rules()
        rows = _filter_rule_rows(domain, status_filter, query, signoffs)
        summary = registry_summary()
        local_ids = {r.id for r in RULES if r.status == STATUS_LOCAL}
        signed_local = sum(
            1 for rid in local_ids
            if rid in signoffs and signoffs[rid].verdict == PreopRuleSignoff.Verdict.APPROVED
        )
        flagged_count = sum(
            1 for so in signoffs.values()
            if so.verdict == PreopRuleSignoff.Verdict.FLAGGED
        )
        status_pills = [
            ('all', 'ყველა წესი', summary['total']),
            (STATUS_VERIFIED, 'პირველწყაროთი დადასტურებული', summary['verified_count']),
            (STATUS_LOCAL, 'ადგილობრივი გადაწყვეტილება', summary['local_count']),
            ('unsigned-local', 'ხელმოწერის მოლოდინში', max(0, summary['local_count'] - signed_local)),
        ]
        return render(request, self.template_name, {
            'summary': summary,
            'rows': rows,
            'domain_filter': domain,
            'status_filter': status_filter,
            'query': query,
            'domain_pills': _build_domain_pills(signoffs),
            'status_pills': status_pills,
            'signed_local_count': signed_local,
            'flagged_count': flagged_count,
            'status_labels': STATUS_LABELS,
            'kind_labels': KIND_LABELS,
        })

    def post(self, request):
        if not (request.user.is_superuser or request.user.is_staff or hasattr(request.user, 'profile')):
            messages.error(request, 'კლინიკური წესების ხელმოწერის უფლება აქვს მხოლოდ ექიმს!')
            return HttpResponseRedirect(reverse('preop-rules'))
        rule_id = (request.POST.get('rule_id') or '').strip()
        if rule_id not in RULES_BY_ID:
            messages.error(request, 'მითითებული კლინიკური წესი რეესტრში ვერ მოიძებნა.')
            return HttpResponseRedirect(reverse('preop-rules'))

        verdict = request.POST.get('verdict') or PreopRuleSignoff.Verdict.APPROVED
        if verdict not in PreopRuleSignoff.Verdict.values:
            verdict = PreopRuleSignoff.Verdict.APPROVED
        note = clean_text(request.POST.get('note'), 300).replace('\n', ' ')

        PreopRuleSignoff.objects.update_or_create(
            item_kind=PreopRuleSignoff.ItemKind.RULE,
            item_id=rule_id,
            defaults={
                'registry_version': REGISTRY_VERSION,
                'verdict': verdict,
                'note': note,
                'signed_by': request.user,
                'signed_at': timezone.now(),
            },
        )
        entry = RULES_BY_ID[rule_id]
        if verdict == PreopRuleSignoff.Verdict.FLAGGED:
            messages.info(request, f'წესი „{entry.title_ka}“ მოინიშნა გადახედვისთვის.')
        else:
            messages.success(request, f'ექიმის ხელმოწერა დაფიქსირდა: {entry.title_ka}.')

        params = {}
        for key in ('domain', 'status', 'q'):
            val = (request.POST.get(f'ret_{key}') or '').strip()
            if val and val != 'all':
                params[key] = val
        url = reverse('preop-rules')
        if params:
            url = f'{url}?{urlencode(params)}'
        return HttpResponseRedirect(f'{url}#rule-{rule_id}')


class PreopChangelogView(LoginRequiredMixin, generic.View):
    """Clinician protocol version changelog ('ცვლილებების ჟურნალი')."""

    template_name = 'mdf/preop/changelog.html'

    def get(self, request):
        from ..registry import CHANGELOG
        return render(request, self.template_name, {
            'summary': registry_summary(),
            'changelog': CHANGELOG,
        })

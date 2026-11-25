"""Cardiology Consultation, Theatre Cancellation & 30-Day SVS VQI / NVR Quality Audit view."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views import generic

from ...board import person_name
from ...models import BoardPatient, PreopPatient
from ..constants import CHIP_CATALOGS, DEFAULT_CARDIOLOGISTS, TARGET_PREP_DAYS
from ..utils import _date, chip_summary


def _collect_audit_metrics(qs, today, active_inpatient_bp_ids, evaluate_fn):
    groups = {
        name: {'name': name, 'total': 0, 'cleared': 0, 'extra': 0, 'non_compliant': 0, 'days_sum': 0, 'over_7': 0}
        for name in DEFAULT_CARDIOLOGISTS
    }
    m = {
        'groups': groups, 'evaluated_rows': [], 'vqi_rows': [],
        'over_target_total': 0, 'non_compliant_total': 0, 'cardio_seen_total': 0,
        'operated_total': 0, 'cancelled_total': 0, 'arterial_operated_total': 0,
        'vqi_statin_ap_count': 0, 'cea_symp_operated_total': 0, 'cea_symp_within_14d_count': 0,
        'vqi_30d_tracked_total': 0, 'vqi_uneventful_count': 0, 'vqi_complication_total': 0,
        'cancel_counts': {k: 0 for k, _ in CHIP_CATALOGS['cancel_reason_chips']},
    }

    for p in qs:
        ev = evaluate_fn(p, today=today)
        p.ev = ev
        p.surgeon_name = person_name(p.surgeon)
        s, q = ev['s'], ev['q']
        if ev['over_target_days']:
            m['over_target_total'] += 1
        if p.cardio_decision:
            m['cardio_seen_total'] += 1
            cname = p.cardiologist or 'ლიკა ინჯია'
            g = groups.setdefault(cname, {
                'name': cname, 'total': 0, 'cleared': 0,
                'extra': 0, 'non_compliant': 0, 'days_sum': 0, 'over_7': 0,
            })
            g['total'] += 1
            if p.cardio_decision == 'cleared':
                g['cleared'] += 1
            else:
                g['extra'] += 1
            if ev['cardio_non_compliant']:
                g['non_compliant'] += 1
                m['non_compliant_total'] += 1
            if ev['over_target_days']:
                g['over_7'] += 1
            g['days_sum'] += ev['days_elapsed']
            m['evaluated_rows'].append(p)

        outcome = ev['case_outcome']
        p.cancel_reasons_str = chip_summary(s.get('cancel_reason_chips'), 'cancel_reason_chips')
        p.vqi_complications_str = chip_summary(s.get('vqi_complication_chips'), 'vqi_complication_chips')
        if outcome == 'operated':
            m['operated_total'] += 1
            bpid = s.get('board_patient_id')
            still_inpatient = bool(bpid and bpid in active_inpatient_bp_ids and not ev['vqi_bundle_done'])
            in_hosp_mortality = (ev['vqi_30d_status'] == 'mortality_30d')
            if not ev['is_venous'] and not still_inpatient and not in_hosp_mortality:
                m['arterial_operated_total'] += 1
                if ev['vqi_bundle_done']:
                    m['vqi_statin_ap_count'] += 1
            if ev.get('is_symp_cea'):
                ev_dt = _date(q.get('cea_event_date'))
                op_done_dt = _date(s.get('operated_date')) or _date(s.get('planned_surgery_date'))
                symp_wait = (
                    (op_done_dt - ev_dt).days if (ev_dt and op_done_dt)
                    else ev.get('days_since_ev')
                )
                if symp_wait is not None and 0 <= symp_wait <= 180:
                    m['cea_symp_operated_total'] += 1
                    if symp_wait <= 14:
                        m['cea_symp_within_14d_count'] += 1
            if ev['vqi_30d_status'] != 'pending':
                m['vqi_30d_tracked_total'] += 1
                if ev['vqi_30d_status'] == 'uneventful':
                    m['vqi_uneventful_count'] += 1
                else:
                    m['vqi_complication_total'] += 1
            m['vqi_rows'].append(p)
        elif outcome.startswith('cancelled_'):
            m['cancelled_total'] += 1
            for ck in (s.get('cancel_reason_chips') or []):
                if ck in m['cancel_counts']:
                    m['cancel_counts'][ck] += 1
            m['vqi_rows'].append(p)
    return m


class PreopAuditView(LoginRequiredMixin, generic.TemplateView):
    """Cardiology Consultation, Theatre Cancellation & 30-Day SVS VQI / NVR Quality Audit."""
    template_name = 'mdf/preop/audit.html'

    def get_context_data(self, **kwargs):
        from .. import evaluate

        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        pathway_filter = self.request.GET.get('pathway', 'all')
        if pathway_filter not in ('all', *PreopPatient.Pathway.values):
            pathway_filter = 'all'

        base_qs = PreopPatient.objects.select_related('surgeon', 'surgeon__profile').order_by('-created_at', '-id')
        if pathway_filter != 'all':
            base_qs = base_qs.filter(pathway=pathway_filter)
        qs = list(base_qs[:500])

        # Active still-hospitalized BoardPatient IDs (excluded from VQI discharge medication denominator until discharged!)
        active_inpatient_bp_ids = set(
            BoardPatient.objects.filter(discharged_at__isnull=True).values_list('pk', flat=True)
        )
        m = _collect_audit_metrics(qs, today, active_inpatient_bp_ids, evaluate)

        table_rows = []
        for g in m['groups'].values():
            g['extra_pct'] = round(g['extra'] * 100 / g['total']) if g['total'] else 0
            g['avg_days'] = round(g['days_sum'] / g['total'], 1) if g['total'] else None
            table_rows.append(g)

        theatre_decided_total = m['operated_total'] + m['cancelled_total']
        cancel_rate_pct = round(m['cancelled_total'] * 100 / theatre_decided_total, 1) if theatre_decided_total else 0
        vqi_statin_ap_pct = round(m['vqi_statin_ap_count'] * 100 / m['arterial_operated_total']) if m['arterial_operated_total'] else None
        cea_symp_within_14d_pct = round(m['cea_symp_within_14d_count'] * 100 / m['cea_symp_operated_total']) if m['cea_symp_operated_total'] else None
        vqi_uneventful_30d_pct = round(m['vqi_uneventful_count'] * 100 / m['vqi_30d_tracked_total']) if m['vqi_30d_tracked_total'] else None
        cancel_reason_rows = [
            {'key': ck, 'label': clbl, 'count': m['cancel_counts'][ck]}
            for ck, clbl in CHIP_CATALOGS['cancel_reason_chips']
            if m['cancel_counts'][ck] > 0
        ]

        ctx.update({
            'pathway_filter': pathway_filter,
            'pathway_pills': [
                ('all', 'ყველა'), ('aaa_infra', 'AAA'), ('cea', 'CEA'), ('abf', 'ABF'),
                ('pad_bypass', 'პერიფ. შუნტირება'), ('pad_pta', 'PTA / სტენტი'), ('venous', 'ვენური'),
            ],
            'total_patients': len(qs), 'total_aaa': len(qs),
            'cardio_seen_total': m['cardio_seen_total'],
            'non_compliant_total': m['non_compliant_total'],
            'over_7_total': m['over_target_total'],
            'table_rows': table_rows,
            'patient_rows': m['evaluated_rows'][:150],
            'target_days': TARGET_PREP_DAYS,
            'operated_total': m['operated_total'],
            'cancelled_total': m['cancelled_total'],
            'cancel_rate_pct': cancel_rate_pct,
            'cancel_reason_rows': cancel_reason_rows,
            'arterial_operated_total': m['arterial_operated_total'],
            'vqi_statin_ap_count': m['vqi_statin_ap_count'],
            'vqi_statin_ap_pct': vqi_statin_ap_pct,
            'cea_symp_operated_total': m['cea_symp_operated_total'],
            'cea_symp_within_14d_count': m['cea_symp_within_14d_count'],
            'cea_symp_within_14d_pct': cea_symp_within_14d_pct,
            'vqi_30d_tracked_total': m['vqi_30d_tracked_total'],
            'vqi_uneventful_count': m['vqi_uneventful_count'],
            'vqi_complication_total': m['vqi_complication_total'],
            'vqi_uneventful_30d_pct': vqi_uneventful_30d_pct,
            'vqi_rows': m['vqi_rows'][:150],
        })
        return ctx

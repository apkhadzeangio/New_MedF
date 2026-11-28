"""Regression tests for Stage-4 Round-21 (v1.2.20) clinical, NLP, parity, deadlock, JS, and plain-Ka fixes."""
import datetime
import pathlib

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from apps.mdf.models import PreopPatient, StaffProfile
from apps.mdf.preop import build_med_schedule, evaluate, generate_anamnesis
from apps.mdf.preop.anamnesis import build_self_fill_highlights
from apps.mdf.preop.meds_freetext import _detect_freetext_high_risk_meds
from apps.mdf.preop.nurse_plan import (
    build_patient_plain_explanation,
    build_phone_med_script,
    build_plain_georgian_missing_items,
    detect_postponement_impacts,
)
from apps.mdf.preop.nurse_ui import get_nurse_readiness_verdict
from apps.mdf.preop.plain_ka import scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text
from apps.mdf.preop.views.board import _build_handoff_tasks

TODAY = datetime.date(2026, 11, 27)
OP_DATE = (TODAY + datetime.timedelta(days=14)).isoformat()


def _mk(subtype='open', *, age=66, sex='M', side='', q=None, s=None, archived=False, approved=False):
    base_q = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if subtype == 'evla':
        base_q.setdefault('ven_ceap', 'C2')
        side = side or 'R'
    elif subtype.startswith(('cea', 'bypass', 'pta', 'phleb')):
        side = side or 'R'
    base_s = {
        'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 78, 'height_cm': 174, 'hb': 136, 'wbc': 6.8, 'plt': 230,
        'inr': 1.02, 'fibrinogen': 3.1, 'creat': 84, 'creat_unit': 'umol',
        'k': 4.2, 'na': 140, 'glucose': 5.3, 'albumin': 42, 'ldl': 1.2,
        'labs_date': TODAY.isoformat(), 'ecg': 'normal', 'cxr': 'normal',
        'blood_group': 'O(I) Rh+', 'blood_reserved': True, 'anesth_done': True,
        'planned_surgery_date': OP_DATE,
    }
    if subtype in ('open', 'evar', 'tbd'):
        base_s.update({'aaa_mm': 58, 'cta_date': TODAY.isoformat(), 'cta_disc': True, 'popliteal_screen_status': 'normal'})
    elif subtype in ('bypass_clti', 'bypass_claud'):
        base_s.update({'abi_r': 0.55, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate', 'decolonization_ready': True})
    elif subtype in ('evla', 'phlebectomy'):
        base_s.update({'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 7.0, 'compression_ready': True})
    if q:
        base_q.update(q)
    if s:
        base_s.update(s)
    pw_map = {
        'open': PreopPatient.Pathway.AAA_INFRA, 'evar': PreopPatient.Pathway.AAA_INFRA, 'tbd': PreopPatient.Pathway.AAA_INFRA,
        'cea_symp': PreopPatient.Pathway.CEA, 'cea_asymp': PreopPatient.Pathway.CEA,
        'abf_claud': PreopPatient.Pathway.ABF, 'abf_clti': PreopPatient.Pathway.ABF,
        'bypass_claud': PreopPatient.Pathway.PAD_BYPASS, 'bypass_clti': PreopPatient.Pathway.PAD_BYPASS,
        'pta_claud': PreopPatient.Pathway.PAD_PTA, 'pta_clti': PreopPatient.Pathway.PAD_PTA,
        'evla': PreopPatient.Pathway.VENOUS, 'phlebectomy': PreopPatient.Pathway.VENOUS,
    }
    p = PreopPatient(
        patient_name='Test R21', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        archived=archived, started_on=TODAY,
    )
    if approved:
        p.approved_at = timezone.now()
    return p


class PreopStressFixesR21Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r21_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R21 ქირურგი')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)

    def test_r21_s1_nlp_ing_verbs_prefix_switches_sov_ka_1st_person_and_lmwh(self):
        # Fuzz S1-1: unanchored (?<=g) must not split taking/using/continuing
        self.assertEqual(_detect_freetext_high_risk_meds('taking apixaban stopped warfarin')['matched_keys'], {'doac'})
        self.assertEqual(_detect_freetext_high_risk_meds('taking apixaban 5mg stopped clopidogrel')['matched_keys'], {'doac'})
        self.assertEqual(_detect_freetext_high_risk_meds('using warfarin stopped aspirin', {'med_aspirin': True})['matched_keys'], {'warfarin'})
        self.assertEqual(_detect_freetext_high_risk_meds('continuing rivaroxaban 20mg stopped aspirin', {'med_aspirin': True})['matched_keys'], {'doac'})
        ev_e2e = evaluate(_mk('evla', q={'med_aspirin': True, 'other_meds': 'taking apixaban stopped aspirin'}), today=TODAY)
        self.assertNotEqual(ev_e2e['clearance_seal']['code'], 'go')
        self.assertIn('doac', ev_e2e['ft_meds_keys'])

        # Fuzz S1-2: unpunctuated KA & EN prefix-stopped drug switches with doses/frequencies
        for txt, exp in (
            ('მოხსნილია ელიქვისი 5მგ x2 ვარფარინი 5მგ', {'warfarin'}),
            ('მოხსნილია ელიქვისი 5mg bid ვარფარინი 5mg', {'warfarin'}),
            ('მოხსნილია ვარფარინი 5mg daily ელიქვისი 5mg', {'doac'}),
            ('მოხსნილია ვარფარინი 5მგ ტაბ ელიქვისი 5მგ', {'doac'}),
            ('stopped warfarin 5mg daily apixaban 5mg bid', {'doac'}),
            ('stopped warfarin apixaban 5mg bid', {'doac'}),
            ('stopped low-dose aspirin', set()),
            ('stopped blood thinners', set()),
            ('მოხსნილია დაბალმოლეკულური ჰეპარინი', set()),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        # Fuzz S1-3: KA contrastive SOV followed by parenthetical or dash continuation
        for txt in (
            'ვარფარინი მოხსნილია, მაგრამ ელიქვისი არა (სვამს დილას)',
            'ვარფარინი მოხსნილია, მაგრამ ელიქვისი არა — აგრძელებს',
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], {'doac'}, txt)

        # Clin F-01: 1st-person KA cessation verbs & double negation + ASRA neuraxial hard-block
        for txt, exp_key in (
            ('ლოსარტანი შევწყვიტე მაგრამ ქსარელტო არა', 'doac'),
            ('ომეპრაზოლი მოვხსენი მაგრამ ვარფარინი არა', 'warfarin'),
            ('ბისოპროლოლი შევაჩერე, მაგრამ არა პრადაქსა', 'dabigatran'),
            ('ვარფარინი შევწყვიტე მაგრამ კლოპიდოგრელს არა', 'clopidogrel'),
            ('ვარფარინი არ შემიწყვეტია', 'warfarin'),
        ):
            p_nx = _mk('bypass_clti', q={'other_meds': txt}, s={'anesthesia_plan': 'neuraxial'})
            ev_nx = evaluate(p_nx, today=TODAY)
            self.assertIn(exp_key, ev_nx['ft_meds_keys'], txt)
            self.assertTrue(ev_nx['neuraxial_hard_blocked'], txt)
            self.assertEqual(ev_nx['clearance_seal']['code'], 'stop', txt)
        self.assertNotIn('warfarin', _detect_freetext_high_risk_meds('ვარფარინი შევწყვიტე მაგრამ კლოპიდოგრელს არა')['matched_keys'])

        # Fuzz S2-3: LMWH disambiguation on Georgian 'წონის' and hyphenated phrases
        for phrase in ('დაბალმოლეკულური წონის ჰეპარინი 40მგ', 'low-molecular-weight-heparin 40mg', 'დაბალმოლეკულური-ჰეპარინი 40მგ'):
            ft = _detect_freetext_high_risk_meds(phrase)
            self.assertTrue(ft['has_ft_lmwh'], phrase)
            self.assertFalse(ft['has_ft_ufh'], phrase)

    def test_r21_s1_dual_xa_oac_and_raaa_p2y12_emergency(self):
        # Combos A1-S1: med_doac=True + free-text rivaroxaban 2.5mg blocked from GO & HTTP approve
        p_dual = _mk(
            'bypass_clti',
            q={'med_doac': True, 'other_meds': 'rivaroxaban 2.5mg'},
            s={'med_hold_agreed': True, 'med_hold_agreed_date': TODAY.isoformat()},
        )
        p_dual.save()
        ev_dual = evaluate(p_dual, today=TODAY)
        self.assertTrue(any('2 ან მეტი პერორალური ანტიკოაგულანტი' in r for r in ev_dual['red_flags']))
        self.assertNotEqual(ev_dual['clearance_seal']['code'], 'go')
        self.c_doc.post(f'/preop/{p_dual.pk}/', {'section': 'signoff', 'action': 'ack_alerts'})
        self.c_doc.post(f'/preop/{p_dual.pk}/', {'section': 'signoff', 'action': 'approve'})
        p_dual.refresh_from_db()
        self.assertIsNone(p_dual.approved_at)

        # Mixed free-text Xa dual-OAC (apixaban 5mg + rivaroxaban 2.5mg)
        ev_ft_dual = evaluate(_mk('bypass_clti', q={'other_meds': 'apixaban 5mg, rivaroxaban 2.5mg'}), today=TODAY)
        self.assertTrue(any('2 ან მეტი პერორალური ანტიკოაგულანტი' in r for r in ev_ft_dual['red_flags']))

        # Combos A2-S1: suspected rAAA + P2Y12 emits emergency non-delay plan, never future hold dates
        for med_k in ('med_clopidogrel', 'med_ticagrelor', 'med_prasugrel'):
            p_raaa = _mk('open', q={'aaa_pain': True, med_k: True}, s={'bp_sys': 82, 'bp_dia': 50, 'hr': 115})
            ev_r = evaluate(p_raaa, today=TODAY)
            self.assertTrue(ev_r['is_rupture_suspect'])
            self.assertTrue(any('გადაუდებელი ოპერაცია' in r['date_plan'] for r in ev_r['med_schedule']), med_k)
            self.assertFalse(any('არ მიიღოთ' in r['date_plan'] for r in ev_r['med_schedule']), med_k)
            self.assertEqual(build_phone_med_script(p_raaa, ev_r, today=TODAY)['stop_rows'], [], med_k)
            self.assertEqual(build_patient_plain_explanation(p_raaa, ev_r, today=TODAY)['stop_meds'], [], med_k)

    def test_r21_s2_parenteral_conflicts_vte_hit_bypass_local_and_dos_bp(self):
        # Combos A3-S2: parenteral + potent P2Y12 ESC Class III, post-op VOYAGER/LMWH, EVLA ft-warfarin parity
        for p2y_k in ('med_ticagrelor', 'med_prasugrel'):
            p_par = _mk('bypass_clti', q={p2y_k: True, 'other_meds': 'enoxaparin 40mg'})
            ev_par = evaluate(p_par, today=TODAY)
            self.assertTrue(any('ESC Class III' in r for r in ev_par['red_flags']), p2y_k)
            self.assertTrue(any(r.get('contraindicated') for r in ev_par['med_schedule']), p2y_k)
        p_enox = _mk('bypass_clti', q={'other_meds': 'enoxaparin 40mg'})
        ev_enox = evaluate(p_enox, today=TODAY)
        self.assertNotIn('VOYAGER PAD', ev_enox['post_antithrombotic_label'])
        self.assertNotIn('VOYAGER PAD', ev_enox['surgical_brief']['postop_antithrombotic'])
        handoff = _build_handoff_tasks(ev_enox, ev_enox['q'], ev_enox['s'], p_enox.pathway, ev_enox['eff_allergy_chips'], ev_enox['eff_renal'])
        self.assertFalse(any('LMWH პროფილაქტიკური დოზით' in t for t in handoff))
        p_evla_enox = _mk('evla', q={'ven_thrombophilia': True, 'ven_cancer_immob': True, 'other_meds': 'enoxaparin 40mg'})
        ev_evla_enox = evaluate(p_evla_enox, today=TODAY)
        self.assertIn('დამატებითი პროფილაქტიკური LMWH/DOAC არ დაუმატოთ', ev_evla_enox['caprini_advice'])
        self.assertNotIn('ფარმაკოლოგიური პროფილაქტიკა (LMWH / DOAC', ev_evla_enox['caprini_advice'])
        ev_evla_ft_vka = evaluate(_mk('evla', q={'other_meds': 'warfarin 5mg, enoxaparin 40mg'}), today=TODAY)
        self.assertTrue(any('დუბლირებული პარენტერული ანტიკოაგულანტი' in r for r in ev_evla_ft_vka['red_flags']))
        self.assertTrue(any(r.get('contraindicated') and 'პარენტერული' in r['drug'] for r in ev_evla_ft_vka['med_schedule']))

        # Combos A6-S2 & A9-S3: HIT suppresses IV UFH timing note; bypass+local blocked; ft-OAC neuraxial details
        ev_hit = evaluate(_mk('bypass_clti', q={'allergy_chips': ['hit_heparin']}, s={'anesthesia_plan': 'neuraxial'}), today=TODAY)
        self.assertNotIn('ASRA 2025 UFH წესი', ev_hit['anesthetic_brief']['neuraxial_gate'])
        for sub in ('bypass_clti', 'bypass_claud'):
            ev_loc = evaluate(_mk(sub, s={'anesthesia_plan': 'local'}), today=TODAY)
            self.assertEqual(ev_loc['clearance_seal']['code'], 'stop')
            self.assertTrue(ev_loc['solo_neuraxial_anatomic_mismatch'])
            self.assertEqual(match_rule_for_text(ev_loc['red_flags'][0]).id, 'ASRA_NEURAXIAL_COAG_GATE')
        ev_ft_vka_nx = evaluate(_mk('bypass_clti', q={'other_meds': 'warfarin 5mg'}, s={'anesthesia_plan': 'neuraxial', 'inr': 1.35}), today=TODAY)
        self.assertIn('≤1.2', ev_ft_vka_nx['anesthetic_brief']['neuraxial_gate'])
        ev_ft_doac_nx = evaluate(_mk('bypass_clti', q={'other_meds': 'apixaban 5mg'}, s={'anesthesia_plan': 'neuraxial', 'creat': None}), today=TODAY)
        self.assertIn('კრეატინინი/CrCl უცნობია', ev_ft_doac_nx['anesthetic_brief']['neuraxial_gate'])

        # Fuzz S2-1: DoS BP amber parity (SBP 90-99, elevated >=160/100, narrow PP <20 mmHg)
        for sbp, dbp, needle, exp_rule in (
            (90, 76, 'დილის ვიწრო პულსური წნევა', 'VIT_BP_HYPOTENSION_SHOCK'),
            (95, 70, 'დილის სასაზღვრო ჰიპოტენზია', 'VIT_BP_HYPOTENSION_SHOCK'),
            (110, 95, 'დილის ვიწრო პულსური წნევა', 'VIT_BP_HYPOTENSION_SHOCK'),
            (160, 90, 'დილის მომატებული არტერიული წნევა', 'VIT_BP_SEVERE_HTN'),
            (175, 105, 'დილის მომატებული არტერიული წნევა', 'VIT_BP_SEVERE_HTN'),
        ):
            ev_bp = evaluate(_mk('evla', s={'dos_bp_sys': sbp, 'dos_bp_dia': dbp}), today=TODAY)
            hit = [a for a in ev_bp['amber_flags'] if needle in a]
            self.assertTrue(hit, f'{sbp}/{dbp}')
            self.assertEqual(match_rule_for_text(hit[0]).id, exp_rule)

    def test_r21_s2_pathway_switch_deadlocks_and_inactive_stop_parity(self):
        # Combos A4-S2: carried-over tsh_status, laryngoscopy_res, tp_l/tbi_l editable & clearable via HTTP
        p_tsh = _mk('evla', s={'tsh_status': 'hyperthyroid'})
        p_tsh.save()
        self.assertIn('name="tsh_status"', self.c_doc.get(f'/preop/{p_tsh.pk}/?tab=s2').content.decode('utf-8'))
        self.c_doc.post(f'/preop/{p_tsh.pk}/', {'section': 'stage2', 'tsh_status': 'pending'})
        p_tsh.refresh_from_db()
        self.assertEqual(p_tsh.studies['tsh_status'], 'pending')

        p_lar = _mk('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 5}, s={'carotid_r': 75, 'laryngoscopy_res': 'palsy'})
        p_lar.save()
        self.assertIn('name="laryngoscopy_res"', self.c_doc.get(f'/preop/{p_lar.pk}/?tab=s2').content.decode('utf-8'))
        self.c_doc.post(f'/preop/{p_lar.pk}/', {'section': 'stage2', 'laryngoscopy_res': 'pending'})
        p_lar.refresh_from_db()
        self.assertEqual(p_lar.studies['laryngoscopy_res'], 'pending')

        p_tp = _mk('open', s={'tp_l': 20, 'tbi_l': 0.25})
        p_tp.save()
        self.assertIn('name="tp_l"', self.c_doc.get(f'/preop/{p_tp.pk}/?tab=s2').content.decode('utf-8'))
        self.c_doc.post(f'/preop/{p_tp.pk}/', {'section': 'stage2', 'tp_l': '', 'tbi_l': ''})
        p_tp.refresh_from_db()
        self.assertIsNone(p_tp.studies['tp_l'])
        self.assertIsNone(p_tp.studies['tbi_l'])

        # Combos A5-S2: inactive/archived cross-surface parity (fasting, postponement, passport P2, phone script)
        p_arch = _mk(
            'open',
            q={'med_warfarin': True, 'glp1': True},
            s={'inr': 2.3, 'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=5)).isoformat()},
            archived=True, approved=True,
        )
        p_arch.save()
        ev_a = evaluate(p_arch, today=TODAY)
        self.assertIsNone(detect_postponement_impacts(p_arch, ev_a, today=TODAY))
        self.assertNotIn('24 სთ მხოლოდ სუფთა თხევადი დიეტა', ev_a['fasting_instruction'])
        self.c_doc.post('/preop/ui-mode/', {'mode': 'old'})
        pass_a = self.c_doc.get(f'/preop/{p_arch.pk}/passport/').content.decode('utf-8')
        self.assertNotIn('ექიმთან ჯერ არ არის დადასტურებული!', pass_a)
        self.assertNotIn('24 სთ მხოლოდ სუფთა თხევადი დიეტა', pass_a)
        self.assertIn('მოქმედებს მხოლოდ მკურნალი ექიმის დადასტურებით!', pass_a)
        self.c_doc.post('/preop/ui-mode/', {'mode': 'new'})
        new_a = self.c_doc.get(f'/preop/{p_arch.pk}/').content.decode('utf-8')
        self.assertNotIn('nw-postpone-banner', new_a)
        self.assertNotIn('24 სთ მხოლოდ სუფთა თხევადი დიეტა', new_a)

        p_def = _mk('evla', q={'med_warfarin': True}, s={'inr': 2.3, 'ven_duplex_status': 'acute_dvt', 'case_outcome_status': 'deferred'})
        p_def.save()
        ev_d = evaluate(p_def, today=TODAY)
        phone_d = build_phone_med_script(p_def, ev_d, today=TODAY)
        self.assertFalse(phone_d['stop_locked'])
        self.assertTrue(phone_d['inactive_locked'])
        self.assertTrue(phone_d['has_meds'])
        new_d = self.c_doc.get(f'/preop/{p_def.pk}/').content.decode('utf-8')
        self.assertNotIn('STOP — წამლები არ შეწყვიტოთ!', new_d)
        self.assertNotIn('წამლები ჯერ არ არის მონიშნული', new_d)

        # Combos A8-S3: STOP + acute_hold ACEi tone/wording parity across surfaces
        p_st = _mk('open', q={'med_chips': ['acei_arb'], 'med_warfarin': True}, s={'k': 5.7, 'inr': 2.3, 'dos_bp_sys': 200, 'dos_bp_dia': 115})
        p_st.save()
        self.c_doc.post('/preop/ui-mode/', {'mode': 'old'})
        self.assertNotIn('წითლად მონიშნული', self.c_doc.get(f'/preop/{p_st.pk}/passport/').content.decode('utf-8'))
        self.assertNotIn('წითლად მონიშნული', self.c_doc.get(f'/preop/{p_st.pk}/?tab=s2').content.decode('utf-8'))
        guide_st = self.c_doc.get(f'/preop/{p_st.pk}/patient-guide/').content.decode('utf-8')
        self.assertIn('nw-med-script-row tone-amber', guide_st)
        self.c_doc.post('/preop/ui-mode/', {'mode': 'new'})
        self.assertNotIn('წითლად მონიშნული', self.c_doc.get(f'/preop/{p_st.pk}/').content.decode('utf-8'))

    def test_r21_clin_plain_ka_anamnesis_registry_js_and_html(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.27')
        self.assertEqual(len(RULES_BY_ID), 83)

        # Clin F-02: mixed KA+EN unrecognized meds & allergens never silently erased
        for q_in in (
            {'other_meds': 'ვიღებ Digoxin 0.25 mg'},
            {'other_meds': 'ასპირინი + Digoxin 0.25 mg'},
            {'allergy_text': 'ალერგია Bactrim 400 mg'},
            {'allergy_text': 'ალერგია Codeine-ზე'},
        ):
            p_hl = _mk('open', q=q_in)
            hl = build_self_fill_highlights(p_hl.questionnaire, patient=p_hl)
            self.assertTrue(any('გადაამოწმეთ ჩანაწერი' in lbl for _, lbl in hl), f'{q_in} -> {hl}')

        # Clin F-03: CEA_SYMP_WINDOW_DAYS matches symptomatic-in-Q cea_asymp amber alert
        ev_cea = evaluate(_mk('cea_asymp', q={'cea_symp_event': True, 'cea_days_since_event': 10}, s={'carotid_r': 75}), today=TODAY)
        symp_amb = next(a for a in ev_cea['amber_flags'] if 'ნევროლოგიური სიმპტომი ბოლო' in a)
        self.assertEqual(match_rule_for_text(symp_amb).id, 'CEA_SYMP_WINDOW_DAYS')

        # Clin F-04, F-05, F-06: partial BP R/L, PAD R/L კიდური, FDA/EMA fragment, ASCII >=3x
        p_bp = _mk('open', s={'bp_sys': None, 'bp_dia': 115, 'bp_sys_l': 170, 'bp_dia_l': None})
        ev_bp = evaluate(p_bp, today=TODAY)
        vd = get_nurse_readiness_verdict(p_bp, ev_bp)['detail']
        self.assertIn('მარჯვენა ?/115', vd)
        self.assertNotIn('R?', vd)
        disc = scrub_plain_ka('ჰემოდინამიკური მაჩვენებლების შეუსაბამობა (R კიდური: ABI 0.32)', second_person=False, strip_quoted_ft=True)
        self.assertIn('მარჯვენა კიდური', disc)
        p_preg = _mk('bypass_clti', age=30, sex='F', q={'pregnant_possible': True, 'med_prasugrel': True, 'med_doac': True})
        phone_p = build_phone_med_script(p_preg, evaluate(p_preg, today=TODAY), today=TODAY)
        self.assertFalse(any('— /!]' in r['rule'] for r in phone_p['caution_rows']))
        p_liv = _mk('open', s={'liver_status': '3x'})
        miss_liv = build_plain_georgian_missing_items(p_liv, evaluate(p_liv, today=TODAY), today=TODAY)
        self.assertFalse(any('>=3x' in m['label'] for m in miss_liv))

        # Combos A7-S3: generate_anamnesis never emits 'no chronic disease' on chronic meds or objective labs
        for q_u, s_u in (
            ({'med_aspirin': True}, {}),
            ({'med_chips': ['statin']}, {}),
            ({'other_meds': 'carvedilol 12.5mg'}, {}),
            ({'other_meds': 'ეუთიროქსი 50მკგ'}, {}),
            ({}, {'lvef': 45}),
            ({}, {'ecg': 'lbbb'}),
            ({}, {'albumin': 26}),
            ({}, {'viral_positive': True}),
            ({}, {'liver_status': '2x'}),
            ({}, {'plt': 78}),
            ({}, {'inr': 1.9}),
            ({}, {'k': 5.9}),
            ({}, {'na': 126}),
            ({}, {'dos_bp_sys': 170, 'dos_bp_dia': 105}),
        ):
            p_an = _mk('evla', q=q_u, s=s_u)
            self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', generate_anamnesis(p_an, evaluate(p_an, today=TODAY)))

        # Fuzz S2-2, S3-1, S3-2: JS helpers & HTML step="any" attributes
        root = pathlib.Path(__file__).resolve().parents[3]
        for js_name in ('preop-patient.js', 'preop-nurse.js'):
            js_txt = (root / 'static/js' / js_name).read_text(encoding='utf-8')
            self.assertIn('function roundWbcK(', js_txt)
        nurse_js = (root / 'static/js/preop-nurse.js').read_text(encoding='utf-8')
        self.assertIn('sbp >= 30 && sbp <= 320 && dbp >= 20 && dbp <= 220', nurse_js)
        self.assertIn('s >= 40 && s <= 300 && d >= 20 && d <= 200', nurse_js)
        for tpl, name in (
            ('templates/mdf/preop/_tab_s1.html', 'bp_sys'),
            ('templates/mdf/preop/_tab_s1.html', 'bp_dia'),
            ('templates/mdf/preop/_tab_s1.html', 'hr'),
            ('templates/mdf/preop/_tab_s1.html', 'bp_sys_l'),
            ('templates/mdf/preop/_tab_s1.html', 'bp_dia_l'),
            ('templates/mdf/preop/_tab_dos.html', 'dos_bp_sys'),
            ('templates/mdf/preop/_tab_dos.html', 'dos_bp_dia'),
            ('templates/mdf/preop/_tab_q.html', 'cta_months'),
        ):
            self.assertIn(f'type="number" step="any" name="{name}"', (root / tpl).read_text(encoding='utf-8'))

        # Standalone build_med_schedule parity sanity check on dual-OAC & rAAA P2Y12
        p_sync = _mk('open', q={'aaa_pain': True, 'med_clopidogrel': True}, s={'bp_sys': 82, 'bp_dia': 50, 'hr': 115})
        ev_sync = evaluate(p_sync, today=TODAY)
        self.assertEqual(
            ev_sync['med_schedule'],
            build_med_schedule(p_sync, ev_sync['q'], ev_sync['s'], ev_sync['egfr'], crcl=ev_sync['crcl'], stop_blocked=(ev_sync['clearance_seal']['code'] == 'stop'), today=TODAY),
        )

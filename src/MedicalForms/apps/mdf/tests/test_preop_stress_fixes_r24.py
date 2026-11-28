"""Regression tests for Stage-4 Round-24 (v1.2.23) NLP, parenteral, rAAA, WIfI, EVLA INR & Plain-KA fixes."""
import dataclasses
import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.mdf.models import PreopPatient, StaffProfile
from apps.mdf.preop import build_med_schedule, evaluate
from apps.mdf.preop.constants import DEFAULT_Q, DEFAULT_STUDIES
from apps.mdf.preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _effective_allergy_state,
)
from apps.mdf.preop.nurse_plan import build_patient_plain_explanation, build_phone_med_script
from apps.mdf.preop.plain_ka import scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, RULES_BY_ID
from apps.mdf.preop.views.board import _build_handoff_tasks
from apps.mdf.staging.suggestions import compute as staging_compute
from apps.mdf.staging.tables import suggest_ischemia

TODAY = datetime.date(2026, 12, 1)
FUTURE_OP = '2026-12-13'
FUTURE_KA = '13.12.2026'
FUTURE_SH = '13.12'


def _mk(sub='evar', sex='M', age=65, side='R', q=None, s=None, surgeon=None, save=False):
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    qd = {**DEFAULT_Q, 'filled': True, 'allergy_iodine_asked': True, **(q or {})}
    sd = {
        **DEFAULT_STUDIES,
        'bp_sys': 130, 'bp_dia': 80, 'hr': 72, 'spo2': 98,
        'weight_kg': 75, 'height_cm': 170,
        'hb': 135, 'plt': 230, 'inr': 1.0, 'creat': 85, 'creat_unit': 'umol',
        'glucose': 5.4, 'k': 4.2, 'na': 140,
        'labs_date': (TODAY - datetime.timedelta(days=1)).isoformat(),
        'ecg': 'normal', 'cxr': 'normal', 'lvef': 60,
        'planned_surgery_date': FUTURE_OP,
        **(s or {}),
    }
    kw = dict(
        patient_name=f'Test-{sub}', age=age, sex=sex,
        side='' if pw in ('aaa_infra', 'abf') else side,
        op_subtype=sub, pathway=pw, surgeon=surgeon,
        questionnaire=qd, studies=sd, visit_date=TODAY, started_on=TODAY,
    )
    return PreopPatient.objects.create(**kw) if save else PreopPatient(**kw)


class PreopStressFixesR24Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(username='r24_doc', password='pw', is_staff=True)
        StaffProfile.objects.create(user=cls.doc, display_name='Dr R24', full_name='Dr R24')
        cls.nurse = User.objects.create_user(username='r24_nurse', password='pw', is_staff=False)

    def setUp(self):
        self.c_doc, self.c_nurse = Client(), Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse.force_login(self.nurse)

    def test_r24_nlp_garda_except_tails_conj_freq_and_riva_escalations(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.23')
        # Fuzz A1 / Clin S1-3: Georgian garda with comma punctuation (fronted & post-positioned)
        for txt, want in (
            ('ვარფარინის გარდა, ყველა მოხსნილია', 'warfarin'),
            ('ყველა წამალი მოხსნილია, ვარფარინის გარდა', 'warfarin'),
            ('არ იღებს არაფერს, ვარფარინის გარდა', 'warfarin'),
            ('წამლებს არ იღებს, ელიქვისის გარდა', 'doac'),
            ('გარდა ვარფარინისა, ყველა შეწყვეტილია', 'warfarin'),
        ):
            ft = _detect_freetext_high_risk_meds(txt)
            self.assertIn(want, ft['unstructured_keys'], txt)
        for txt, want in (
            ('იოდის გარდა, ალერგია არ აქვს', {'iodine'}),
            ('ჰეპარინის გარდა, ალერგია არ აქვს', {'hit_heparin'}),
            ('ალერგია არ აქვს, იოდის გარდა', {'iodine'}),
            ('ალერგია არ აქვს, ჰეპარინის გარდა', {'hit_heparin'}),
            ('ალერგია არ აქვს, ლატექსისა და იოდის გარდა', {'latex_chlorhex', 'iodine'}),
        ):
            self.assertTrue(want.issubset(_detect_freetext_allergies(txt)), txt)
        self.assertNotIn('warfarin', _detect_freetext_high_risk_meds('ყველა წამალს იღებს, ვარფარინის გარდა')['matched_keys'])

        # Fuzz A2 & A3 & A4: post-stop duration tails, 2-drug affirm conj + stop, fronted except, slash dose, temporal adverbs, multi-word heads
        for txt, want, drop in (
            ('warfarin 5mg stopped 5 days ago apixaban 5mg', 'doac', 'warfarin'),
            ('warfarin 5mg held for 3 days apixaban 5mg', 'doac', 'warfarin'),
            ('ვარფარინი 5მგ მოხსნილია 5 დღის წინ ელიქვისი 5მგ', 'doac', 'warfarin'),
            ('taking aspirin and apixaban warfarin stopped', 'doac', 'warfarin'),
            ('იღებს ასპირინს და ელიქვისს ვარფარინი მოხსნილია', 'doac', 'warfarin'),
            ('except warfarin all meds stopped', 'warfarin', None),
            ('except for warfarin all meds are stopped', 'warfarin', None),
            ('stopped warfarin 5mg/day apixaban 5mg', 'doac', 'warfarin'),
            ('stopped warfarin yesterday apixaban', 'doac', 'warfarin'),
            ('stopped warfarin last week apixaban', 'doac', 'warfarin'),
            ('მოხსნილია ვარფარინი ადრე ელიქვისი', 'doac', 'warfarin'),
            ('stopped low molecular weight heparin warfarin', 'warfarin', 'parenteral_oac'),
            ('მოხსნილია დაბალმოლეკულური ჰეპარინი ვარფარინი', 'warfarin', 'parenteral_oac'),
        ):
            ft = _detect_freetext_high_risk_meds(txt, q={'med_aspirin': True})
            self.assertIn(want, ft['unstructured_keys'], txt)
            if drop:
                self.assertNotIn(drop, ft['matched_keys'], txt)

        # Fuzz A5: freq/timing modifiers before stop verb never false-flag stopped OAC
        for txt in (
            'ვარფარინი 5მგ 1-ჯერ მოხსნილია',
            'ვარფარინი 5მგ დილას მოხსნილია',
            'taking aspirin 100mg warfarin 5mg once daily stopped',
            'taking aspirin 100mg eliquis 5mg twice daily stopped',
            'taking aspirin 100mg warfarin 5mg in the morning stopped',
        ):
            ft = _detect_freetext_high_risk_meds(txt, q={'med_aspirin': True})
            self.assertFalse(ft['matched_keys'] & {'warfarin', 'doac'}, txt)

        # Clin S1-4 & S2-1: real-world riva escalations flagged; space-separated co-meds NOT false-flagged
        for esc in (
            'ქსარელტო 2.5 მგ, კარდიოლოგმა გაზარდა 20 მგ-მდე',
            'ქსარელტო 2.5 მგ, დოზა შეუცვალა 20 მგ-ზე',
            'ქსარელტო 2.5 მგ, გადავიდა 20 მგ-ზე',
            'xarelto 2.5mg, now taking 20mg',
            'xarelto 2.5mg, increased to 20mg for afib',
        ):
            ft_e = _detect_freetext_high_risk_meds(esc, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertTrue(ft_e['has_ft_dual_xa'], esc)
        for comed in (
            'ქსარელტო 2.5 მგ ატორვასტატინი 20 მგ',
            'xarelto 2.5mg atorvastatin 20mg',
            'atorvastatin 20mg xarelto 2.5mg',
        ):
            ft_c = _detect_freetext_high_risk_meds(comed, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertFalse(ft_c['has_ft_dual_xa'], comed)
            self.assertTrue(ft_c['has_ft_riva_low'], comed)

    def test_r24_parenteral_hit_pregnancy_pta_caprini_board_and_postop_at(self):
        # Combos/Clin S1-1: HIT + CrCl<30/dialysis + parenteral AC never recommends UFH
        ev_hit = evaluate(_mk('pta_clti', sex='F', age=75, q={'allergy_chips': ['hit_heparin'], 'other_meds': 'არგატრობანი'}, s={'creat': 360}), today=TODAY)
        self.assertNotIn('უპირატესია UFH', ev_hit['post_antithrombotic_label'])
        self.assertIn('ჰეპარინი/UFH/LMWH', ev_hit['post_antithrombotic_label'])
        dapt_h = next(t['label'] for t in ev_hit['tasks'] if t['key'] == 'dapt')
        self.assertNotIn('უპირატესია UFH', dapt_h)

        # Combos S1-2: Explicit dapt/voyager + active parenteral AC + pregnancy never recommends oral OAC
        ev_pr = evaluate(_mk('pta_clti', sex='F', age=30, q={'pregnant_possible': True, 'other_meds': 'კლექსანი 80მგ'}, s={'post_antithrombotic_plan': 'dapt'}), today=TODAY)
        self.assertNotIn('შეცვალეთ: OAC +', ev_pr['post_antithrombotic_label'])
        self.assertIn('ორსულობა', ev_pr['post_antithrombotic_label'])

        # Combos S1-3 / Clin S1-2: PTA + clopidogrel_intol + parenteral AC never recommends ticagrelor
        ev_pt = evaluate(_mk('pta_clti', q={'allergy_chips': ['clopidogrel_intol'], 'other_meds': 'კლექსანი 80მგ'}), today=TODAY)
        self.assertFalse(any('ალტერნატიული P2Y12 (ტიკაგრელორი)' in rf for rf in ev_pt['red_flags']))

        # Combos S1-4: Pregnancy + active parenteral AC has no double-AC self-contradiction in Caprini, venous amber & VTE task
        ev_vp = evaluate(_mk('phlebectomy', sex='F', age=30, q={'pregnant_possible': True, 'ven_dvt_history': True, 'other_meds': 'კლექსანი 80მგ'}, s={'ven_duplex_status': 'deep_patent_reflux', 'liver_status': '3x'}), today=TODAY)
        self.assertNotIn('LMWH / UFH 7–10 დღე', ev_vp['caprini_advice'])
        self.assertIn('აქტიური პარენტერული', ev_vp['caprini_advice'])
        vte_t = next(t['label'] for t in ev_vp['tasks'] if t['key'] == 'vte')
        self.assertIn('აქტიური პარენტერული', vte_t)
        self.assertIn('ღვიძლის ≥3x', vte_t)

        # Combos S2-2: Board VTE handoff preserves liver_3x under pregnancy and CrCl<30 + liver_3x under pregnancy+HIT
        ev_b1 = evaluate(_mk('phlebectomy', sex='F', age=30, q={'pregnant_possible': True, 'ven_dvt_history': True}, s={'liver_status': '3x', 'ven_duplex_status': 'deep_patent_reflux'}), today=TODAY)
        bt1 = next(t[1] for t in _build_handoff_tasks(ev_b1, ev_b1['q'], ev_b1['s'], ev_b1['pathway'], _effective_allergy_state(ev_b1['q'])[0], ev_b1.get('eff_renal_sat')) if t[1].startswith(('VTE', 'HIT')))
        self.assertIn('ღვიძლის ≥3x', bt1)
        ev_b2 = evaluate(_mk('phlebectomy', sex='F', age=30, q={'pregnant_possible': True, 'ven_dvt_history': True, 'allergy_chips': ['hit_heparin']}, s={'creat': 450, 'liver_status': '3x', 'ven_duplex_status': 'deep_patent_reflux'}), today=TODAY)
        bt2 = next(t[1] for t in _build_handoff_tasks(ev_b2, ev_b2['q'], ev_b2['s'], ev_b2['pathway'], _effective_allergy_state(ev_b2['q'])[0], ev_b2.get('eff_renal_sat')) if t[1].startswith(('VTE', 'HIT')))
        self.assertIn('ღვიძლის ≥3x', bt2)
        self.assertIn('CrCl<30', bt2)

        # Combos S2-3: AAA/CEA + parenteral caveats, explicit sapt + parenteral caveats, explicit oac_sapt without OAC warning
        ev_cea = evaluate(_mk('cea_symp', sex='F', age=30, q={'pregnant_possible': True, 'other_meds': 'კლექსანი 80მგ'}, s={'creat': 450, 'liver_status': '3x'}), today=TODAY)
        sp_c = ev_cea['surgical_brief']['postop_antithrombotic']
        self.assertIn('ორსულობა', sp_c)
        self.assertIn('CrCl/eGFR <30', sp_c)
        self.assertIn('ღვიძლის ≥3x', sp_c)
        ev_sp = evaluate(_mk('pta_clti', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'არგატრობანი'}, s={'post_antithrombotic_plan': 'sapt', 'creat': 360}), today=TODAY)
        self.assertIn('HIT', ev_sp['post_antithrombotic_label'])
        ev_no = evaluate(_mk('pta_clti', q={}, s={'post_antithrombotic_plan': 'oac_sapt'}), today=TODAY)
        self.assertTrue(any('არჩეულია პოსტოპ. OAC' in a for a in ev_no['amber_flags']))

    def test_r24_raaa_surfaces_shock_reversal_registry_wifi_evla_inr_and_beta_prefix(self):
        # Combos S1-5 & Clin S1-6: rAAA compensated shock (SI>=1.0), 5 surfaces hide future date, emergency fasting, block +3d reschedule
        p_r = _mk('open', q={'aaa_pain': True, 'glp1': True, 'med_aspirin': True, 'med_chips': ['statin', 'beta_blocker', 'acei_arb', 'ccb'], 'ven_hormones': True}, s={'bp_sys': 115, 'bp_dia': 70, 'hr': 122, 'aaa_mm': 70}, surgeon=self.doc, save=True)
        ev_r = evaluate(p_r, today=TODAY)
        sc_r = build_phone_med_script(p_r, ev_r, TODAY)
        gd_r = build_patient_plain_explanation(p_r, ev_r, TODAY)
        self.assertEqual(sc_r['op_date_ka'], '')
        self.assertNotIn(FUTURE_KA, gd_r['arrival_items'][0])
        self.assertIn('არ გადაიდება', gd_r['fasting_plain'])
        for url in (reverse('preop-passport', args=[p_r.pk]), reverse('preop-patient-guide', args=[p_r.pk]), reverse('preop-patient', args=[p_r.pk])):
            self.assertNotIn(FUTURE_KA, self.c_doc.get(url).content.decode('utf-8'), url)
        self.c_doc.post(reverse('preop-ui-mode'), {'mode': 'new'})
        self.assertNotIn(FUTURE_KA, self.c_doc.get(reverse('preop-patient', args=[p_r.pk])).content.decode('utf-8'))
        self.c_doc.post(reverse('preop-ui-mode'), {'mode': 'old'})
        for kw in ('ბეტა-ბლოკ', 'ACEi', 'CCB'):
            row = next(r for r in ev_r['med_schedule'] if kw in r['drug'])
            self.assertEqual(row['tone'], 'red', kw)
            self.assertTrue(row['hold_or_skip'], kw)
        horm_r = next(r for r in ev_r['med_schedule'] if 'ჰორმონ' in r['drug'])
        self.assertNotIn('4 კვ-ით ადრე', horm_r['rule'])
        self.c_nurse.post(reverse('preop-reschedule', args=[p_r.pk]), {'action': 'shift_3d'})
        p_r.refresh_from_db()
        self.assertEqual(p_r.studies.get('planned_surgery_date'), FUTURE_OP)

        # Clin S1-5 & S2-3: rAAA surgical_brief dual-Xa vs 2.5mg + parenteral reversal
        ev_dx = evaluate(_mk('open', q={'aaa_pain': True, 'other_meds': 'ქსარელტო 2.5 მგ და ელიქვისი 5 მგ'}, s={'bp_sys': 85, 'hr': 115}), today=TODAY)
        self.assertIn('4F-PCC 50 სე/კგ IV', ev_dx['surgical_brief']['tactical_note'])
        ev_25 = evaluate(_mk('open', q={'aaa_pain': True, 'med_chips': ['rivaroxaban_2_5'], 'other_meds': 'ქსარელტო 2.5 მგ'}, s={'bp_sys': 85, 'hr': 115}), today=TODAY)
        self.assertIn('რივაროქსაბანი 2.5 მგ (Xa)', ev_25['surgical_brief']['tactical_note'])
        self.assertNotIn('50 სე/კგ', ev_25['surgical_brief']['tactical_note'])
        ev_pr = evaluate(_mk('open', q={'aaa_pain': True, 'other_meds': 'კლექსანი 0.8'}, s={'bp_sys': 85, 'hr': 115}), today=TODAY)
        self.assertIn('პროტამინ', ev_pr['surgical_brief']['tactical_note'])

        # Clin S2-2: Live registry mutations propagate into rAAA metabolic/fasting branch
        p_mut = _mk('open', q={'aaa_pain': True, 'sglt2': True, 'glp1': True, 'metformin': True}, s={'bp_sys': 85, 'hr': 115, 'creat': 150})
        orig = {k: RULES_BY_ID[k] for k in ('MED_SGLT2_HOLD_DAYS', 'MED_GLP1_FASTING_PROTOCOL', 'MED_METFORMIN_RENAL_CONTRAST')}
        try:
            RULES_BY_ID['MED_SGLT2_HOLD_DAYS'] = dataclasses.replace(orig['MED_SGLT2_HOLD_DAYS'], value={**orig['MED_SGLT2_HOLD_DAYS'].value, 'std_days': 5})
            RULES_BY_ID['MED_GLP1_FASTING_PROTOCOL'] = dataclasses.replace(orig['MED_GLP1_FASTING_PROTOCOL'], value={**orig['MED_GLP1_FASTING_PROTOCOL'].value, 'liquid_hours': 36})
            RULES_BY_ID['MED_METFORMIN_RENAL_CONTRAST'] = dataclasses.replace(orig['MED_METFORMIN_RENAL_CONTRAST'], value={**orig['MED_METFORMIN_RENAL_CONTRAST'].value, 'contra_renal': 50})
            ev_m = evaluate(p_mut, today=TODAY)
            self.assertIn('120 სთ', next(r['rule'] for r in ev_m['med_schedule'] if 'SGLT2' in r['drug']))
            self.assertIn('36 სთ', next(r['rule'] for r in ev_m['med_schedule'] if 'GLP-1' in r['drug']))
            self.assertIn('36 სთ', ev_m['fasting_instruction'])
            self.assertTrue(next(r.get('contraindicated') for r in ev_m['med_schedule'] if 'მეტფორმ' in r['drug']))
        finally:
            RULES_BY_ID.update(orig)

        # Combos S2-1: EVLA structured & FT warfarin invalid baseline INR + FT mech_valve subtherapeutic INR <2.0
        for q_w in ({'med_warfarin': True}, {'other_meds': 'ვარფარინი 5მგ'}):
            p_w = _mk('evla', q=q_w, s={'inr': '0.1', 'ven_duplex_status': 'deep_patent_reflux'})
            ev_w = evaluate(p_w, today=TODAY)
            self.assertTrue(any(r.get('contraindicated') for r in ev_w['med_schedule']), q_w)
            ms_w = build_med_schedule(p_w, ev_w['q'], ev_w['s'], ev_w['egfr'], crcl=ev_w['crcl'], today=TODAY)
            self.assertTrue(any(r.get('contraindicated') for r in ms_w), q_w)
        ev_mv = evaluate(_mk('evla', q={'other_meds': 'ვარფარინი 5მგ', 'cad_chips': ['mech_valve']}, s={'inr': 1.5}), today=TODAY)
        self.assertTrue(any(r.get('contraindicated') for r in ev_mv['med_schedule']))

        # Clin S1-7: WIfI ABI > 1.3 never falls back to calcified AP
        self.assertIsNone(suggest_ischemia(abi=1.55, ap=220, tp=None)[0])
        c_no_tp = staging_compute({'w': 2, 'fi': 1, 'abi': 1.55, 'ap': 220, 'tp': None, 'fp': 2, 'ip': 2, 'risk': 'high'})
        self.assertNotIn('wifi', c_no_tp)
        c_tp = staging_compute({'w': 2, 'fi': 1, 'abi': 1.55, 'ap': 220, 'tp': 25, 'fp': 2, 'ip': 2, 'risk': 'high'})
        self.assertEqual((c_tp['i'], c_tp['wifi'], c_tp['plan']), (3, 4, 'endo'))

        # Clin S3-1: Greek beta-prefix transliterated cleanly to plain Georgian (never '(ლაქტამი)')
        self.assertEqual(scrub_plain_ka('β-ლაქტამი (პენიცილინი/ცეფაზოლინი)', second_person=False, strip_quoted_ft=True), 'ბეტა-ლაქტამი (პენიცილინი/ცეფაზოლინი)')
        self.assertEqual(scrub_plain_ka('β-ბლოკერი', second_person=False, strip_quoted_ft=True), 'ბეტა-ბლოკერი')

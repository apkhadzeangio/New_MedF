"""Regression tests for Stage-4 Round-23 (v1.2.22) NLP, Xa-DOAC, rAAA, parenteral, EVLA/CEA epidural, anamnesis, Plain-KA & inactive fixes."""
import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from apps.mdf.models import PreopPatient, StaffProfile
from apps.mdf.preop import build_med_schedule, evaluate, generate_anamnesis
from apps.mdf.preop.anamnesis import build_self_fill_highlights
from apps.mdf.preop.meds_freetext import _detect_freetext_allergies, _detect_freetext_high_risk_meds
from apps.mdf.preop.nurse_plan import build_phone_med_script, build_plain_georgian_missing_items
from apps.mdf.preop.plain_ka import plain_drug_name, scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION
from apps.mdf.preop.views.board import _append_vte_handoff_tasks

TODAY = datetime.date(2026, 11, 27)
OP_DATE = (TODAY + datetime.timedelta(days=14)).isoformat()


def _mk(subtype='evla', *, age=62, sex='M', side='', q=None, s=None, archived=False):
    base_q = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if subtype == 'evla':
        base_q.setdefault('ven_ceap', 'C2')
        side = side or 'R'
    elif subtype.startswith(('cea', 'bypass', 'pta', 'phleb')):
        side = side or 'R'
    base_s = {
        'bp_sys': 124, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 76, 'height_cm': 174, 'hb': 142, 'wbc': 6.8, 'plt': 230,
        'inr': 1.02, 'fibrinogen': 3.1, 'creat': 82, 'creat_unit': 'umol',
        'k': 4.2, 'na': 140, 'glucose': 5.3, 'albumin': 42, 'ldl': 1.2,
        'labs_date': TODAY.isoformat(), 'ecg': 'normal', 'cxr': 'normal',
        'blood_group': 'O(I) Rh+', 'blood_reserved': True, 'anesth_done': True,
        'planned_surgery_date': OP_DATE,
    }
    if subtype in ('open', 'evar', 'tbd'):
        base_s.update({'aaa_mm': 58, 'cta_date': TODAY.isoformat(), 'cta_disc': True, 'popliteal_screen_status': 'normal'})
    elif subtype in ('bypass_clti', 'bypass_claud', 'pta_clti', 'pta_claud'):
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
    return PreopPatient(
        patient_name='Test R23', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        archived=archived, started_on=TODAY,
    )


class PreopStressFixesR23Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r23_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R23 ქირურგი')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)

    def test_r23_s1_nlp_digitfree_counts_except_garda_and_xa_stems(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.22')
        # Fuzz S1-1: digit-free prefix-stopped drug switches never dropped by _is_uniform_neg_chain
        for txt, exp in (
            ('stopped warfarin apixaban', {'doac'}),
            ('discontinued clopidogrel warfarin', {'warfarin'}),
            ('held xarelto plavix', {'clopidogrel'}),
            ('მოხსნილია ელიქვისი ვარფარინი', {'warfarin'}),
            ('შევწყვიტე ვარფარინი პლავიქსი', {'clopidogrel'}),
            ('მოვხსენი ვარფარინი ქსარელტო', {'doac'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        # Fuzz S1-2 & Clin S1-2: tablets, spaceless 1tab/1აბი/1ტაბ, 1-ჯერ, in the morning, decimals, 1/2
        for txt, exp in (
            ('stopped warfarin 1 tablet apixaban 5mg', {'doac'}),
            ('stopped warfarin 2 tablets apixaban 5mg', {'doac'}),
            ('stopped warfarin 1tab apixaban 5mg', {'doac'}),
            ('მოხსნილია ვარფარინი 1აბი ელიქვისი 5მგ', {'doac'}),
            ('მოხსნილია ვარფარინი 1ტაბ ელიქვისი 5მგ', {'doac'}),
            ('stopped warfarin 2.5 days ago apixaban 5mg', {'doac'}),
            ('stopped warfarin 1.5 weeks ago apixaban 5mg', {'doac'}),
            ('მოხსნილია ვარფარინი 2.5 დღის წინ ელიქვისი 5მგ', {'doac'}),
            ('მოვხსენი ვარფარინი 5მგ 1-ჯერ 5 დღის წინ ელიქვისი 5მგ', {'doac'}),
            ('stopped warfarin 5mg in the morning 5 days ago apixaban 5mg', {'doac'}),
            ('stopped warfarin 1/2 tab 5d ago apixaban 5mg', {'doac'}),
            ('stopped warfarin 5mg in the morning', set()),
            ('მოხსნილია ვარფარინი 5მგ 1-ჯერ', set()),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        # Fuzz S1-3: except & Georgian გარდა (pre, post, fronted)
        for txt, exp in (
            ('NKDA except iodine', {'iodine'}),
            ('ალერგია არ აქვს გარდა იოდისა', {'iodine'}),
            ('ალერგია არ აქვს იოდის გარდა', {'iodine'}),
            ('იოდის გარდა ალერგია არ აქვს', {'iodine'}),
            ('ალერგია არ აქვს გარდა ჰეპარინისა', {'hit_heparin'}),
            ('no allergies except heparin', {'hit_heparin'}),
            ('no drug allergies except penicillin', {'beta_lactam'}),
        ):
            self.assertEqual(_detect_freetext_allergies(txt), exp, txt)
        for txt, exp in (
            ('stopped all blood thinners except warfarin', {'warfarin'}),
            ('no meds except warfarin', {'warfarin'}),
            ('არ სვამს წამლებს გარდა ვარფარინისა', {'warfarin'}),
            ('მოხსნილია ყველა წამალი გარდა პლავიქსისა', {'clopidogrel'}),
            ('takes aspirin except warfarin', {'aspirin'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        # Fuzz S3-1: active-then-suffix-stopped clauses split before stopped drug
        for txt, exp in (
            ('იღებს პლავიქსს ვარფარინი მოხსნილია', {'clopidogrel'}),
            ('taking plavix warfarin stopped', {'clopidogrel'}),
            ('taking plavix 75mg warfarin 5mg stopped', {'clopidogrel'}),
            ('takes aspirin 100 mg stopped warfarin 5mg', {'aspirin'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        # Combos/Clin S1-1: all Georgian Xa-DOAC spelling variants block GO & approve
        for txt in ('ედოკსაბანი 60მგ + ელიქვისი 5მგ', 'სავაისა 60მგ + ქსარელტო 20მგ', 'რივაროკსაბანი 20მგ + ელიქვისი 5მგ', 'რივაროკსაბანი 2.5მგ'):
            p_xa = _mk('evla', q={'cad': True, 'cad_chips': ['afib'], 'med_doac': True, 'other_meds': txt}, s={'med_hold_agreed': True, 'med_hold_agreed_date': TODAY.isoformat()})
            p_xa.save()
            ev_xa = evaluate(p_xa, today=TODAY)
            self.assertNotEqual(ev_xa['clearance_seal']['code'], 'go', txt)
            self.assertTrue(any('2 ან მეტი პერორალური ანტიკოაგულანტი' in r for r in ev_xa['red_flags']), txt)
            self.c_doc.post(f'/preop/{p_xa.pk}/', {'section': 'signoff', 'action': 'ack_alerts'})
            self.c_doc.post(f'/preop/{p_xa.pk}/', {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
            p_xa.refresh_from_db()
            self.assertIsNone(p_xa.approved_at, txt)

        # Clin S2-2: routine VOYAGER rivaroxaban 2.5mg + unrelated 10/15/20mg co-med does NOT false-flag dual OAC
        for txt in ('ქსარელტო 2.5 მგ, კარდიოლოგმა დაუნიშნა ატორვასტატინი 20 მგ', 'xarelto 2.5mg, also amlodipine 10mg'):
            ft_v = _detect_freetext_high_risk_meds(txt, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertFalse(ft_v['has_ft_dual_xa'], txt)
            self.assertTrue(ft_v['has_ft_riva_low'], txt)

    def test_r23_s2_priority_parenteral_board_raaa_epidural_and_anamnesis(self):
        # Fuzz S2-1: priority_tier monotonicity above 200mm
        ev_85 = evaluate(_mk('evla', s={'aaa_mm': 85}), today=TODAY)
        ev_210 = evaluate(_mk('evla', s={'aaa_mm': 210}), today=TODAY)
        self.assertEqual(ev_85['priority_tier'], 'high')
        self.assertEqual(ev_210['priority_tier'], 'high')

        # Combos S2-1 & S2-2: PTA parenteral dapt task, explicit SAPT + parenteral, Board liver/pregnancy/HIT, _par_vte_advice renal+liver
        p_pta = _mk('pta_clti', sex='F', age=30, q={'other_meds': 'enoxaparin 40mg', 'pregnant_possible': True}, s={'creat': 340, 'creat_unit': 'umol', 'liver_status': '3x'})
        ev_pta = evaluate(p_pta, today=TODAY)
        dapt_task = next(t['label'] for t in ev_pta['tasks'] if t.get('key') == 'dapt')
        self.assertNotIn('DAPT:', dapt_task)
        self.assertIn('ორსულობა', ev_pta['post_antithrombotic_label'])
        self.assertIn('CrCl/eGFR <30', ev_pta['post_antithrombotic_label'])
        self.assertIn('ღვიძლის ≥3x', ev_pta['caprini_advice'])
        self.assertIn('CrCl<30', ev_pta['caprini_advice'])
        ev_rl = evaluate(_mk('evla', q={'other_meds': 'enoxaparin 40mg'}, s={'creat': 340, 'creat_unit': 'umol', 'liver_status': '3x'}), today=TODAY)
        self.assertIn('CrCl/eGFR <30', ev_rl['caprini_advice'])
        self.assertIn('ღვიძლის ≥3x', ev_rl['caprini_advice'])
        p_sapt = _mk('bypass_clti', q={'other_meds': 'enoxaparin 40mg'}, s={'post_antithrombotic_plan': 'sapt'})
        ev_sapt = evaluate(p_sapt, today=TODAY)
        self.assertTrue(any('პარენტერულ' in r for r in ev_sapt['red_flags']))
        cands = []
        _append_vte_handoff_tasks(cands, ev_pta, ev_pta['q'], ev_pta['s'], p_pta.pathway, set(), ev_pta['eff_renal'])
        vte_b = cands[0][1]
        self.assertIn('ორსულობა', vte_b)
        self.assertIn('ღვიძლის ≥3x', vte_b)
        p_hit_b = _mk('evla', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'argatroban IV'}, s={'creat': 340, 'creat_unit': 'umol', 'liver_status': '3x'})
        ev_hb = evaluate(p_hit_b, today=TODAY)
        cands_h = []
        _append_vte_handoff_tasks(cands_h, ev_hb, ev_hb['q'], ev_hb['s'], p_hit_b.pathway, {'hit_heparin'}, ev_hb['eff_renal'])
        self.assertIn('ორმაგი ანტიკოაგულაციის გარეშე', cands_h[0][1])

        # Combos S2-3 & Clin S2-4: rAAA metabolic/cilostazol/diuretic & combined FT OAC+P2Y12 never leak future elective hold dates
        p_raaa = _mk('open', q={'aaa_pain': True, 'sglt2': True, 'glp1': True, 'metformin': True, 'dm_insulin': True, 'med_chips': ['cilostazol', 'diuretic'], 'other_meds': 'warfarin 5mg, clopidogrel 75mg'}, s={'bp_sys': 84, 'bp_dia': 52, 'hr': 104})
        ev_raaa = evaluate(p_raaa, today=TODAY)
        self.assertIn('არ გადაიდება', ev_raaa['fasting_instruction'])
        for row in ev_raaa['med_schedule']:
            self.assertNotIn('თხევადი დიეტა (24', row['date_plan'], row)
            self.assertNotIn('72 სთ', row['date_plan'], row)
            self.assertNotIn('48 სთ', row['date_plan'], row)
            self.assertNotIn('დილას', row['date_plan'], row)
        ft_row = next(r for r in ev_raaa['med_schedule'] if r.get('unstructured'))
        self.assertIn('4F-PCC', ft_row['rule'])
        self.assertIn('თრომბოკონცენტრატ', ft_row['rule'])
        phone_r = build_phone_med_script(p_raaa, ev_raaa, TODAY)
        self.assertFalse(any('(დან)' in str(r) for r in phone_r.get('stop_rows', []) + phone_r.get('urgent_rows', [])))

        # Clin S2-3: EVLA+local+epidural and CEA+plexus+epidural isolate epidural mismatch cleanly
        for sub, plan in (('evla', 'local'), ('cea_asymp', 'plexus')):
            p_ep = _mk(sub, s={'anesthesia_plan': plan, 'anesth_lines_chips': ['epidural']})
            ev_ep = evaluate(p_ep, today=TODAY)
            self.assertNotIn('ადგილობრივი/წნულის', ev_ep['anesthetic_brief']['neuraxial_gate'])
            self.assertIn('ეპიდურული', ev_ep['anesthetic_brief']['neuraxial_gate'])
            self.assertNotIn('ASRA 2025 UFH', ev_ep['anesthetic_brief']['neuraxial_gate'])
            miss_ep = build_plain_georgian_missing_items(p_ep, ev_ep, TODAY)
            self.assertFalse(any('ადგილობრივი ან წნულის' in m['label'] for m in miss_ep))
            if sub == 'cea_asymp':
                self.assertIn('წნულის', ev_ep['effective_anesth_label'])

        # Combos S2-4: has_ft_acei_arb exported & anamnesis catches brand ACEi/ARB + objective findings
        for q_u, s_u, sx in (
            ({'other_meds': 'cozaar 50mg'}, {}, 'M'),
            ({'other_meds': 'diovan 80mg'}, {}, 'M'),
            ({}, {'bp_sys': 130, 'bp_sys_l': 105}, 'M'),
            ({}, {'hb': 124}, 'M'),
            ({}, {'hb': 192}, 'M'),
            ({}, {'hb': 170}, 'F'),
            ({}, {'k': 5.2}, 'M'),
            ({}, {'abf_mesenteric': 'sma_stenosis'}, 'M'),
            ({}, {'urine_nitrite': True}, 'M'),
            ({}, {'laryngoscopy_res': 'palsy'}, 'M'),
            ({'ven_dvt_history': True}, {}, 'M'),
        ):
            p_a = _mk('evla', age=45, sex=sx, q=q_u, s=s_u)
            ev_a = evaluate(p_a, today=TODAY)
            if 'other_meds' in q_u:
                self.assertTrue(ev_a['has_ft_acei_arb'], q_u)
            self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', generate_anamnesis(p_a, ev_a), (q_u, s_u, sx))

    def test_r23_s3_plain_ka_highlights_inactive_evla_ft_and_wifi_clear(self):
        # Fuzz S3-2 & Combos S2-3: non-finite strings stripped & (01.12-დან) preserved
        for bad in ('NaN', 'nan', '-inf', '+inf', 'Infinity'):
            self.assertEqual(scrub_plain_ka(bad), '')
            self.assertEqual(plain_drug_name(bad), '')
        self.assertIn('(01.12-დან)', scrub_plain_ka('შეჩერება (01.12-დან) მხოლოდ ექიმით', second_person=False, strip_quoted_ft=True))

        # Clin S2-1 & S3-1: free-text iodine allergy & space-separated KA unrecognized med/allergen
        hl_io = build_self_fill_highlights({'filled': True, 'allergy_text': 'იოდი და პენიცილინი'})
        self.assertTrue(any('იოდზე' in txt for _, txt in hl_io), hl_io)
        self.assertTrue(any('პენიცილინ' in txt for _, txt in hl_io), hl_io)
        hl_sp_m = build_self_fill_highlights({'filled': True, 'other_meds': 'ატორვასტატინი მილდრონატი'})
        self.assertTrue(any('დამატებითი წამალი' in txt for _, txt in hl_sp_m), hl_sp_m)
        hl_sp_a = build_self_fill_highlights({'filled': True, 'allergy_text': 'პენიცილინი მარწყვი'})
        self.assertTrue(any('დამატებითი ალერგენი' in txt for _, txt in hl_sp_a), hl_sp_a)

        # Combos S3-1: inactive/archived cards have empty missing list & hide stale surgery date in headers
        p_in = _mk('evar', archived=True)
        p_in.save()
        ev_in = evaluate(p_in, today=TODAY)
        self.assertEqual(build_plain_georgian_missing_items(p_in, ev_in, TODAY), [])
        h_old = self.c_doc.get(f'/preop/{p_in.pk}/').content.decode('utf-8')
        self.assertNotIn(f'ოპერაცია: {ev_in["planned_surgery_ka"]}', h_old)
        StaffProfile.objects.filter(user=self.doc).update(preop_ui_mode='new')
        h_new = self.c_doc.get(f'/preop/{p_in.pk}/').content.decode('utf-8')
        self.assertIn('ეს ბარათი დაარქივებული ან არააქტიურია', h_new)
        self.assertIn('არქივშია', h_new)
        StaffProfile.objects.filter(user=self.doc).update(preop_ui_mode='old')

        # Combos S3-2: EVLA free-text warfarin + supratherapeutic/invalid INR sets contraindicated=True
        for s_ov in ({'inr': 3.5}, {'dos_inr': 'abc'}):
            p_ev = _mk('evla', q={'other_meds': 'warfarin 5mg'}, s=s_ov)
            ev_ev = evaluate(p_ev, today=TODAY)
            ft_w = next(r for r in ev_ev['med_schedule'] if r.get('unstructured'))
            self.assertTrue(ft_w['contraindicated'], s_ov)
            ms_st = build_med_schedule(p_ev, ev_ev['q'], ev_ev['s'], ev_ev['egfr'], crcl=ev_ev['crcl'], today=TODAY)
            self.assertTrue(any(r.get('contraindicated') for r in ms_st), s_ov)

        # Combos S3-3: imported case_wifi_unsalvageable rendered, clearable via S1 POST, and cleared on pathway switch
        p_wf = _mk('bypass_clti', s={'case_wifi_unsalvageable': True})
        p_wf.save()
        h_s1 = self.c_doc.get(f'/preop/{p_wf.pk}/?tab=s1').content.decode('utf-8')
        self.assertIn('name="case_wifi_unsalvageable"', h_s1)
        self.c_doc.post(f'/preop/{p_wf.pk}/', {'section': 'stage1', 'case_wifi_unsalvageable_presented': '1'})
        p_wf.refresh_from_db()
        self.assertFalse(p_wf.studies.get('case_wifi_unsalvageable'))
        p_wf.studies['case_wifi_unsalvageable'] = True
        p_wf.save(update_fields=['studies'])
        self.c_doc.post(f'/preop/{p_wf.pk}/', {'section': 'header', 'patient_name': p_wf.patient_name, 'op_subtype': 'evla', 'side': 'R', 'age': '62', 'sex': 'M'})
        p_wf.refresh_from_db()
        self.assertFalse(p_wf.studies.get('case_wifi_unsalvageable'))

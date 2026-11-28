"""Regression tests for Stage-4 Round-22 (v1.2.21) clinical, NLP, parity, anamnesis, and plain-Ka fixes."""
import datetime
from dataclasses import replace

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from apps.mdf.models import PreopPatient, StaffProfile
from apps.mdf.preop import evaluate, generate_anamnesis
from apps.mdf.preop.anamnesis import build_self_fill_highlights
from apps.mdf.preop.meds_freetext import _detect_freetext_allergies, _detect_freetext_high_risk_meds
from apps.mdf.preop.nurse_plan import build_plain_georgian_missing_items
from apps.mdf.preop.plain_ka import plain_drug_name, scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text

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
        'weight_kg': 76, 'height_cm': 174, 'hb': 138, 'wbc': 6.8, 'plt': 230,
        'inr': 1.02, 'fibrinogen': 3.1, 'creat': 82, 'creat_unit': 'umol',
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
    return PreopPatient(
        patient_name='Test R22', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        archived=archived, started_on=TODAY,
    )


class PreopStressFixesR22Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r22_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R22 ქირურგი')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)

    def test_r22_s1_par_vte_advice_args_and_dual_xa_doac_block(self):
        # Combos/Clin S1-1: Venous + HIT + parenteral must NEVER recommend UFH or fabricate CrCl<30
        p_hit = _mk('evla', q={'allergy_chips': ['hit_heparin'], 'ven_dvt_history': True, 'ven_cancer_immob': True, 'other_meds': 'argatroban IV'})
        ev_hit = evaluate(p_hit, today=TODAY)
        vte_amb = next(a for a in ev_hit['amber_flags'] if 'თრომბოპროფილაქტიკა' in a)
        vte_task = next(t['label'] for t in ev_hit['tasks'] if t.get('key') == 'vte')
        for txt in (vte_amb, vte_task):
            self.assertIn('HIT', txt)
            self.assertNotIn('(UFH)', txt)
            self.assertNotIn('CrCl/eGFR <30', txt)
        p_ckd = _mk('evla', q={'ven_dvt_history': True, 'ven_cancer_immob': True, 'other_meds': 'enoxaparin 40mg'}, s={'creat': 320, 'creat_unit': 'umol'})
        ev_ckd = evaluate(p_ckd, today=TODAY)
        vte_ckd = next(a for a in ev_ckd['amber_flags'] if 'თრომბოპროფილაქტიკა' in a)
        self.assertIn('CrCl/eGFR <30', vte_ckd)
        self.assertNotIn('ღვიძლის ≥3x', vte_ckd)
        p_liv = _mk('evla', q={'ven_dvt_history': True, 'ven_cancer_immob': True, 'other_meds': 'heparin 5000U'}, s={'liver_status': '3x'})
        ev_liv = evaluate(p_liv, today=TODAY)
        self.assertIn('ღვიძლის ≥3x', next(a for a in ev_liv['amber_flags'] if 'თრომბოპროფილაქტიკა' in a))

        # Combos/Clin S1-2: Multiple distinct Xa-DOACs or therapeutic+2.5mg rivaroxaban never yield GO or approve
        for med_doac in (True, False):
            for txt in ('apixaban 5mg + rivaroxaban 20mg', 'apixaban 5mg + edoxaban 60mg', 'rivaroxaban 20mg + rivaroxaban 2.5mg'):
                p_dx = _mk('evla', q={'cad': True, 'cad_chips': ['afib'], 'med_doac': med_doac, 'other_meds': txt}, s={'med_hold_agreed': True, 'med_hold_agreed_date': TODAY.isoformat()})
                p_dx.save()
                ev_dx = evaluate(p_dx, today=TODAY)
                self.assertTrue(any('2 ან მეტი პერორალური ანტიკოაგულანტი' in r for r in ev_dx['red_flags']), f'{med_doac}:{txt}')
                self.assertNotEqual(ev_dx['clearance_seal']['code'], 'go', f'{med_doac}:{txt}')
                self.assertTrue(any(r.get('contraindicated') for r in ev_dx['med_schedule']), f'{med_doac}:{txt}')
                self.c_doc.post(f'/preop/{p_dx.pk}/', {'section': 'signoff', 'action': 'ack_alerts'})
                self.c_doc.post(f'/preop/{p_dx.pk}/', {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
                p_dx.refresh_from_db()
                self.assertIsNone(p_dx.approved_at, f'{med_doac}:{txt}')

    def test_r22_s1_s2_nlp_counts_durations_movkhseni_and_prefix_conjunctions(self):
        # Fuzz S1: prefix-stopped switches with numeric tablet counts or stop durations
        for txt, exp in (
            ('შეწყვეტილია ვარფარინი 2.5 მგ 1 აბი ქსარელტო 15მგ', {'doac'}),
            ('stopped warfarin 5mg 1 tab apixaban 5mg', {'doac'}),
            ('შეწყვეტილია კლოპიდოგრელი 75მგ 1 ტაბ ბრილინტა 90მგ', {'ticagrelor'}),
            ('stopped warfarin 5 days ago apixaban 5mg', {'doac'}),
            ('მოხსნილია ვარფარინი 5 დღის წინ ქსარელტო 20მგ', {'doac'}),
            ('stopped aspirin 100mg 5 days ago', set()),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        # Fuzz/Clin S2-1: 1st-person მოვხსენი & მოვხსენით properly negated
        for txt in ('მოვხსენი ქსარელტო 20მგ', 'ქსარელტო მოვხსენი', 'ელიქვისი 5მგ მოვხსენი', 'მოვხსენით ვარფარინი 5მგ'):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], set(), txt)
        self.assertEqual(_detect_freetext_high_risk_meds('მოვხსენი ელიქვისი 5მგ, ვარფარინი 5მგ')['matched_keys'], {'warfarin'})

        # Fuzz S2-2: all Georgian prefix stop verbs & allergy-prefix conjunction negation
        for txt in (
            'მოხსნილია ასპირინი 100mg and კლოპიდოგრელი 75mg',
            'მოხსნილია ასპირინი 100მგ და კლოპიდოგრელი 75მგ',
            'მოხსნილია ვარფარინი და ქსარელტო',
            'შეწყვეტილია პლავიქსი ან ბრილინტა',
            'შევწყვიტე ასპირინი 100მგ და კლოპიდოგრელი 75მგ',
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], set(), txt)
        self.assertEqual(_detect_freetext_allergies('ალერგია არ აქვს პენიცილინზე და ვანკომიცინზე'), set())
        self.assertEqual(_detect_freetext_allergies('ალერგია არ აქვს პენიცილინზე და ალერგია აქვს ვანკომიცინზე'), {'vancomycin'})

    def test_r22_s2_s3_raaa_ft_p2y12_bypass_local_anamnesis_highlights_and_plain_ka(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.27')
        # Combos S2-1: rAAA + free-text P2Y12 emits emergency do-not-delay directive
        for drug_txt in ('clopidogrel 75mg', 'ticagrelor 90mg', 'prasugrel 10mg'):
            ev_r = evaluate(_mk('open', q={'aaa_pain': True, 'med_aspirin': True, 'other_meds': drug_txt}, s={'bp_sys': 82, 'bp_dia': 50, 'hr': 110}), today=TODAY)
            self.assertTrue(any('გადაუდებელი ოპერაცია' in r['date_plan'] and r.get('contraindicated') for r in ev_r['med_schedule']), drug_txt)

        # Clin S2-2: bypass + local modality label, neuraxial_gate, and nurse missing-item text
        p_bl = _mk('bypass_clti', s={'anesthesia_plan': 'local'})
        ev_bl = evaluate(p_bl, today=TODAY)
        self.assertNotEqual(ev_bl['effective_anesth_label'], 'ადგილობრივი / ტუმესცენტური')
        self.assertIn('შეუსაბამოა', ev_bl['effective_anesth_label'])
        self.assertNotIn('სოლო-ნეირაქსიული', ev_bl['anesthetic_brief']['neuraxial_gate'])
        miss_bl = build_plain_georgian_missing_items(p_bl, ev_bl, today=TODAY)
        self.assertFalse(any('მარტო ნეირაქსიული' in m['label'] for m in miss_bl))

        # Clin S2-3: build_self_fill_highlights retains Georgian unrecognized & secondary recognized classes
        for q_in, needle in (
            ({'other_meds': 'პლავიქსი 75მგ, დიგოქსინი 0.25მგ'}, 'გადაამოწმეთ ჩანაწერი'),
            ({'other_meds': 'ბისოპროლოლი 5მგ, დიგოქსინი 0.25მგ'}, 'გადაამოწმეთ ჩანაწერი'),
            ({'other_meds': 'Plavix 75mg, Ibuprofen 400mg'}, 'ტკივილგამაყუჩებელი'),
            ({'other_meds': 'Atorvastatin 20mg, Amiodarone 200mg'}, 'სტატინი'),
        ):
            p_hl = _mk('open', q=q_in)
            hl = build_self_fill_highlights(p_hl.questionnaire, patient=p_hl)
            self.assertTrue(any(needle in lbl for _, lbl in hl), f'{q_in} -> {hl}')

        # Combos S2-2: anamnesis brand statins, chronic meds & objective abnormal labs/studies
        for q_u, s_u in (
            ({'other_meds': 'mertenil 20mg'}, {}),
            ({'other_meds': 'roswera 20mg'}, {}),
            ({'med_chips': ['ppi']}, {}),
            ({'other_meds': 'thyrozol 5mg'}, {}),
            ({'other_meds': 'symbicort 160/4.5'}, {}),
            ({'other_meds': 'levetiracetam 500mg'}, {}),
            ({}, {'gastro_result': 'acute_ulcer'}),
            ({}, {'abi_r': 0.55}),
            ({}, {'carotid_r': 75}),
            ({}, {'bp_sys': 155, 'bp_dia': 95}),
            ({}, {'hb': 115}),
            ({}, {'ldl': 4.9}),
            ({}, {'ferritin': 12}),
            ({}, {'weight_kg': 135, 'height_cm': 170}),
            ({}, {'spo2': 88}),
            ({}, {'ven_duplex_status': 'acute_dvt'}),
            ({}, {'plt': 125}),
            ({}, {'wbc': 15.0}),
        ):
            p_an = _mk('evla', age=45, q=q_u, s=s_u)
            self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', generate_anamnesis(p_an, evaluate(p_an, today=TODAY)))

        # Fuzz S3, Clin S3-1, S3-2, Combos S3-1, S3-2, S3-3
        for bad_f in (float('nan'), float('inf'), float('-inf')):
            self.assertEqual(scrub_plain_ka(bad_f), '')
            self.assertEqual(plain_drug_name(bad_f), '')
        self.assertNotIn('—:', scrub_plain_ka('ახალგაზრდა ასაკში AAA — ESVS 2024: გამოირიცხოს მარფანი.', second_person=False, strip_quoted_ft=True))
        self.assertNotIn('— /', scrub_plain_ka('(19 დღე — ESVS 2023 / NVR სამიზნე ≤14 დღე)!', second_person=False, strip_quoted_ft=True))
        self.assertNotIn('[!]', scrub_plain_ka('უკუნაჩვენებია [FDA / EMA!]', second_person=False, strip_quoted_ft=True))
        orig_cea = RULES_BY_ID['CEA_SYMP_WINDOW_DAYS']
        try:
            RULES_BY_ID['CEA_SYMP_WINDOW_DAYS'] = replace(orig_cea, value=(14, 10, 120), match_patterns=('ნევროლოგიური სიმპტომი ბოლო {val2} დღეში',))
            self.assertEqual(match_rule_for_text('ნევროლოგიური სიმპტომი ბოლო 120 დღეში').id, 'CEA_SYMP_WINDOW_DAYS')
        finally:
            RULES_BY_ID['CEA_SYMP_WINDOW_DAYS'] = orig_cea
        ev_ft_ace = evaluate(_mk('open', q={'other_meds': 'ramipril 5mg'}, s={'k': 5.8, 'dos_bp_sys': 200}), today=TODAY)
        self.assertEqual(next(r['tone'] for r in ev_ft_ace['med_schedule'] if 'ACEi' in r['drug']), 'amber')
        ev_ft_vka = evaluate(_mk('evla', q={'ven_dvt_history': True, 'ven_cancer_immob': True, 'other_meds': 'warfarin 5mg'}, s={'inr': 3.8}), today=TODAY)
        self.assertIn('უკუნაჩვენები', ev_ft_vka['caprini_advice'])
        p_arch = _mk('evar', q={'med_warfarin': True}, s={'med_hold_agreed': True}, archived=True)
        p_arch.save()
        h_s2 = self.c_doc.get(f'/preop/{p_arch.pk}/?tab=s2').content.decode('utf-8')
        self.assertNotIn('(ოპ:', h_s2)
        self.assertIn('name="med_hold_agreed" value="1" checked disabled', h_s2)

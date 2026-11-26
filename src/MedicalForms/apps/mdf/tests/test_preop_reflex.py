"""Tests for Pre-op standing reflex orders, zero-text chips, passport, and peripheral PAD."""

from django.urls import reverse

from ..models import StaffProfile
from .helpers import PrivateMediaTest


class PreopReflexZeroTextAndPeripheralTests(PrivateMediaTest):
    """Standing Reflex Protocols, Zero-Free-Text Chips, Med Schedule, PAD Bypass, PAD PTA, Passport & Consent."""

    def _setup_reflex_evar_patient(self):
        from datetime import timedelta

        from django.utils import timezone

        from ..models import PreopPatient

        StaffProfile.objects.update_or_create(user=self.user, defaults={'full_name': 'გიორგი აფხაძე'})
        today = timezone.localdate()
        p = PreopPatient.objects.create(
            patient_name='რეფლექს ტესტაძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        # 1. Nurse fills questionnaire purely via 1-tap chips (Zero Free-Text)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'aaa_pain': '0', 'cfs_level': '1_3', 'aaa_pedal_pulse': 'reduced',
            'htn': '1', 'cad': '1', 'cad_chips': ['mi', 'afib'],
            'dm': '1', 'thyroid_disease': '1', 'dental_infection': '1',
            'med_aspirin': '1', 'med_doac': '1', 'sglt2': '1', 'metformin': '1',
            'med_chips': ['statin', 'beta_blocker'],
            'allergy_iodine': '1', 'allergy_other': '1', 'allergy_chips': ['beta_lactam'],
            'mets_ok': '1',
        })
        # 2. Nurse enters Stage 1 labs: Hb 108 (<115), Glucose 8.4 (>=7.0, no HbA1c yet),
        #    Creatinine 168 (eGFR < 45 & < 60), stale labs (>14 days ago)
        stale_dt = (today - timedelta(days=18)).isoformat()
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '136', 'bp_dia': '82', 'hr': '68', 'spo2': '97',
            'hb': '108', 'wbc': '7.1', 'plt': '215', 'inr': '1.08', 'fibrinogen': '3.4',
            'creat': '168', 'k': '4.4', 'na': '139', 'glucose': '8.4', 'albumin': '39', 'ldl': '1.9',
            'labs_date': stale_dt, 'urine_wbc': '3', 'liver_status': 'normal',
            'carotid_r': '20', 'carotid_l': '25', 'lvef': '56', 'aaa_mm': '59',
            'cxr': 'normal', 'ecg': 'normal', 'aaa_iliac_access': 'ok_7mm', 'cta_disc': '1',
        })
        p.refresh_from_db()
        return p

    def test_standing_reflex_orders_zero_text_chips_med_schedule_passport_and_consent(self):
        from ..preop import (
            STATUS_ALERT,
            STATUS_PROGRESS,
            STATUS_READY,
            consent_url_for_preop,
            evaluate,
            generate_anamnesis,
        )

        p = self._setup_reflex_evar_patient()
        ev = evaluate(p)

        # Verify every autonomous Standing Reflex Order activated automatically
        reflex_keys = {r['key'] for r in ev['reflex_orders']}
        self.assertTrue({'aaa_abi', 'anemia', 'nephro', 'hydration', 'hba1c', 'tsh', 'dental', 'contrast_premed', 'stale_labs'}.issubset(reflex_keys))
        self.assertIn('ვანკომიცინი ან კლინდამიცინი', ev['abx_prophylaxis_note'])

        # Verify Automated Perioperative Medication Schedule (DOAC -72h because eGFR < 50, SGLT2 -72h, Aspirin/Statin continue)
        sched_by_drug = {m['drug']: m for m in ev['med_schedule']}
        self.assertIn('72 საათით', sched_by_drug['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertIn('72 საათით (3 დღით) ადრე', sched_by_drug['SGLT2 (ჯარდიანსი / ფორსიგა)']['rule'])
        self.assertEqual(sched_by_drug['ასპირინი (75–100 მგ)']['tone'], 'ok')
        self.assertEqual(sched_by_drug['სტატინი']['tone'], 'ok')

        tab_s2 = self.client.get(reverse('preop-patient', args=[p.pk]) + '?tab=s2')
        self.assertContains(tab_s2, 'DOAC (ქსარელტო / ელიქვისი)')
        self.assertContains(tab_s2, '72 საათით (3 დღით) ადრე')

        anam = generate_anamnesis(p, ev)
        self.assertIn('გადატანილი ინფარქტი', anam)
        self.assertIn('მოციმციმე არითმია', anam)
        self.assertIn('პენიცილინი / ცეფაზოლინი (β-ლაქტამი)', anam)

        # 3. Nurse enters HbA1c = 9.1% (>=8.5%) + contrast premedication -> escalates to Endocrinology reflex
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'hba1c': '9.1', 'contrast_premed_done': '1',
        })
        p.refresh_from_db()
        self.assertTrue(evaluate(p)['needs_endo_reflex'])
        self.assertEqual(evaluate(p)['status'], STATUS_PROGRESS)

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'stale_labs_refreshed': '1', 'ferritin': '42',
            'anemia_panel_done': '1', 'hba1c': '9.1', 'endo_consult_done': '1',
            'nephro_panel_done': '1', 'hydration_planned': '1', 'tsh_status': 'euthyroid',
            'dental_sanitation_done': '1', 'contrast_premed_done': '1',
            'abi_r': '0.94', 'abi_l': '0.91', 'gastro_result': 'remission',
            'evar_sized': '1', 'decolonization_ready': '1', 'med_hold_agreed': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
            'cardio_note_chips': ['cleared_rx', 'added_bb_statin'],
            'nurse_note_chips': ['fasting_instructed', 'family_contacted'], 'anesth_done': '1',
        })
        p.refresh_from_db()
        ev_done = evaluate(p)
        self.assertEqual(ev_done['status'], STATUS_READY)
        self.assertIn('დაშვებულია მიმდინარე თერაპიის ფონზე', p.cardio_note)
        self.assertIn('უზმოდ მოსვლა და წამლების სქემა განემარტა', p.studies['nurse_note'])

        # 4. 1-Page Printable Preop Passport (/preop/<pk>/passport/) & Direct Consent Prefill
        passport_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertEqual(passport_resp.status_code, 200)
        self.assertContains(passport_resp, 'წინასაოპერაციო სამედიცინო პასპორტი')
        self.assertContains(passport_resp, 'რეფლექს ტესტაძე')
        self.assertContains(passport_resp, 'ვანკომიცინი ან კლინდამიცინი')

        c_url = consent_url_for_preop(p)
        self.assertIn(f'preop={p.pk}', c_url)
        consent_resp = self.client.get(c_url)
        self.assertEqual(consent_resp.status_code, 200)
        self.assertContains(consent_resp, 'რეფლექს ტესტაძე')
        self.assertContains(consent_resp, 'გიორგი აფხაძე')

        # 5. HIT heparin allergy chip triggers immediate Red Flag
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'allergy_other': '1',
            'allergy_chips': ['hit_heparin'], 'mets_ok': '1',
        })
        p.refresh_from_db()
        ev_hit = evaluate(p)
        self.assertEqual(ev_hit['status'], STATUS_ALERT)
        self.assertTrue(any('HIT' in rf for rf in ev_hit['red_flags']))

    def test_bilateral_arm_bp_asymmetry_on_cea_and_venous_caprini_score(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()
        cea = PreopPatient.objects.create(
            patient_name='კაროტიდ წნევაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_asymp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1',
            'bp_sys': '154', 'bp_dia': '86',
            'bp_sys_l': '122', 'bp_dia_l': '74',
            'hb': '140', 'creat': '88', 'inr': '1.0', 'urine_wbc': '1',
            'carotid_r': '80', 'carotid_l': '30', 'cea_cta_status': 'concordant',
            'lvef': '60', 'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        cea.refresh_from_db()
        ev_cea = evaluate(cea)
        self.assertEqual(ev_cea['bp_arm_diff'], 32)
        self.assertTrue(any('ლავიწქვეშა არტერიის სტენოზი' in af for af in ev_cea['amber_flags']))

        ven = PreopPatient.objects.create(
            patient_name='ვენა კაპრინიძე', age=66, sex='F', side='L',
            pathway='venous', op_subtype='evla', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'ven_ceap': 'C4', 'ven_dvt_history': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage1',
            'weight_kg': '96', 'height_cm': '162',
            'hb': '130', 'plt': '240', 'inr': '1.0', 'glucose': '5.2',
            'ven_duplex_status': 'deep_patent_reflux', 'ven_epifascial': '1', 'ecg': 'normal',
        })
        ven.refresh_from_db()
        ev_ven = evaluate(ven)
        self.assertGreaterEqual(ev_ven['caprini_score'], 7)
        self.assertTrue(ev_ven['needs_vte_prophylaxis'])
        self.assertTrue(any('ეპიფასციური' in af for af in ev_ven['amber_flags']))

    def test_pad_bypass_vein_mapping_reflex_and_pad_pta_endovascular_pathway(self):
        from ..models import PreopPatient
        from ..preop import STATUS_PROGRESS, STATUS_READY, evaluate

        # 1. Case transfer auto-maps femoropopliteal bypass -> pad_bypass
        c_byp = self.make_case(
            patient_name='შუნტ მუხლქვეშაძე', age=67, sex='M', side='R',
            diagnosis='ქვემო კიდურის კრიტიკული იშემია, მოსვენებითი ტკივილი',
            proposal='ბარძაყ-მუხლქვეშა აუტოვენური შუნტირება',
        )
        self.client.get(reverse('preop-new'), {'case': c_byp.pk})
        byp = PreopPatient.objects.get(case=c_byp)
        self.assertEqual((byp.pathway, byp.op_subtype), ('pad_bypass', 'bypass_clti'))

        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_rest_pain': '1',
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        # Ipsilateral GSV is 2.3 mm (< 3.0 mm) -> automatically triggers contralateral/arm vein mapping!
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage1',
            'hb': '136', 'creat': '86', 'inr': '1.02', 'urine_wbc': '2',
            'abi_r': '0.34', 'abi_l': '0.88',
            'pad_runoff_status': 'tibial_single',
            'pad_gsv_status': 'inadequate', 'pad_gsv_mm': '2.3',
            'carotid_r': '25', 'carotid_l': '20', 'lvef': '58',
            'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        byp.refresh_from_db()
        ev_byp = evaluate(byp)
        self.assertTrue(ev_byp['s1_core_done'])
        self.assertTrue(ev_byp['needs_alt_vein'])
        self.assertEqual(ev_byp['status'], STATUS_PROGRESS)

        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage2',
            'alt_vein_mapped': 'contra_gsv_ok',
            'blood_reserved': '1',
            'decolonization_ready': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        byp.refresh_from_db()
        self.assertEqual(evaluate(byp)['status'], STATUS_READY)

        # 2. Case transfer auto-maps SFA angioplasty/stenting -> pad_pta
        c_pta = self.make_case(
            patient_name='სტენტ ბალონაძე', age=64, sex='M', side='L',
            diagnosis='ქვემო კიდურის არტერიების ათეროსკლეროზი, კოჭლობა',
            proposal='ზედაპირული ბარძაყის არტერიის ბალონური ანგიოპლასტიკა და სტენტირება (PTA)',
        )
        self.client.get(reverse('preop-new'), {'case': c_pta.pk})
        pta = PreopPatient.objects.get(case=c_pta)
        self.assertEqual((pta.pathway, pta.op_subtype), ('pad_pta', 'pta_claud'))

        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'abf_claud_m': '100', 'pta_cant_lie_flat': '1',
            'med_aspirin': '1', 'med_clopidogrel': '1', 'mets_ok': '1',
        })
        # Normal eGFR (creat 82) + pta_cant_lie_flat -> lean endovascular workup: no cardiology needed, sedation anesthesia triggered
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage1',
            'hb': '138', 'plt': '230', 'inr': '1.01', 'glucose': '5.6', 'creat': '82',
            'abi_r': '0.96', 'abi_l': '0.54',
            'pta_lesion_site': 'femoropopliteal',
            'pta_access_site': 'cfa_clean',
            'ecg': 'normal',
        })
        pta.refresh_from_db()
        ev_pta = evaluate(pta)
        self.assertTrue(ev_pta['s1_core_done'])
        self.assertFalse(ev_pta['needs_hydration'])
        self.assertTrue(ev_pta['needs_anesth'])
        self.assertFalse(ev_pta['needs_cardio'])

        # CKD3 (creat 128 -> eGFR 52 < 60) -> activates contrast hydration reflex + RCRI cardiology
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage1',
            'hb': '138', 'plt': '230', 'inr': '1.01', 'glucose': '5.6', 'creat': '128',
            'abi_r': '0.96', 'abi_l': '0.54',
            'pta_lesion_site': 'femoropopliteal',
            'pta_access_site': 'cfa_clean',
            'ecg': 'normal',
        })
        pta.refresh_from_db()
        self.assertTrue(evaluate(pta)['needs_hydration'])
        self.assertTrue(evaluate(pta)['needs_cardio'])

        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage2',
            'hydration_planned': '1',
            'dapt_ready': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        pta.refresh_from_db()
        self.assertEqual(evaluate(pta)['status'], STATUS_READY)

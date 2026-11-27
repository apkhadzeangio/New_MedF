"""Tests for Pre-op Stage P0 pharmacology, ASRA neuraxial, unit guards, and case auto-import."""

from django.test import Client
from django.urls import reverse

from ..models import StaffProfile
from .helpers import PrivateMediaTest


class PreopStageP0SafetyTests(PrivateMediaTest):
    """Stage P0 multi-agent international safety, pharmacology, UX, and 2D triage tests."""

    def test_p0_1a_cea_clopidogrel_uninterrupted_and_missing_antiplatelet_guard(self):
        from datetime import timedelta

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, STATUS_READY, evaluate

        today = timezone.localdate()

        # 1. Symptomatic CEA: Clopidogrel continues uninterrupted (ESVS 2023 Class I/IIa), needs_med_hold=False
        cea = PreopPatient.objects.create(
            patient_name='სიმპტომურ კაროტიდაძე', age=70, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cea_symp_event': '1',
            'cea_event_chips': ['minor_stroke'],
            'cea_event_date': (today - timedelta(days=5)).isoformat(),
            'med_clopidogrel': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        cea.refresh_from_db()
        ev_cea = evaluate(cea, today=today)
        sched_cea = {m['drug']: m for m in ev_cea['med_schedule']}
        self.assertEqual(sched_cea['კლოპიდოგრელი / P2Y12']['tone'], 'ok')
        self.assertIn('გრძელდება უწყვეტად', sched_cea['კლოპიდოგრელი / P2Y12']['rule'])
        self.assertFalse(ev_cea['needs_med_hold'])
        self.assertGreaterEqual(ev_cea['caprini_score'], 9)

        # Missing antiplatelet on CEA raises Red Flag until cea_antiplatelet_started is checked in Stage 2
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cea_symp_event': '1',
            'cea_event_date': (today - timedelta(days=4)).isoformat(),
            'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1', 'labs_date': today.isoformat(),
            'bp_sys': '130', 'bp_dia': '78', 'hr': '70', 'spo2': '98',
            'hb': '142', 'creat': '84', 'inr': '1.0', 'urine_wbc': '1',
            'carotid_r': '85', 'carotid_l': '30',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'lvef': '58', 'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        cea.refresh_from_db()
        ev_no_ap = evaluate(cea, today=today)
        self.assertEqual(ev_no_ap['status'], STATUS_ALERT)
        self.assertTrue(ev_no_ap['needs_cea_antiplatelet'])
        self.assertTrue(any('არ იღებს ანტიაგრეგანტს' in rf for rf in ev_no_ap['red_flags']))

        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage2', 'cea_antiplatelet_started': '1', 'neuro_consult_done': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared', 'anesth_done': '1',
        })
        cea.refresh_from_db()
        self.assertEqual(evaluate(cea, today=today)['status'], STATUS_READY)

    def test_p0_1b_asra_neuraxial_crcl_doac_dabigatran_glp1_hit_and_cied(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()
        # 2. ASRA 5th Ed Neuraxial vs General Hold + Unknown-Creatinine Guard + Dabigatran + Prasugrel + GLP-1 + CIED + HIT
        aaa = PreopPatient.objects.create(
            patient_name='ფარმაკო უსაფრთხოძე', age=74, sex='M',
            pathway='aaa_infra', op_subtype='evar', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'questionnaire',
            'med_doac': '1', 'med_dabigatran': '1', 'med_prasugrel': '1', 'med_clopidogrel': '1',
            'glp1': '1', 'pacemaker': '1', 'allergy_other': '1', 'allergy_chips': ['hit_heparin'],
            'mets_ok': '1',
        })
        aaa.refresh_from_db()
        ev_no_cr = evaluate(aaa, today=today)
        sched_no_cr = {m['drug']: m for m in ev_no_cr['med_schedule']}
        self.assertEqual(sched_no_cr['DOAC (ქსარელტო / ელიქვისი)']['tone'], 'red')
        self.assertIn('შეიყვანეთ კრეატინინი', sched_no_cr['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertNotIn('48 საათით', sched_no_cr['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertIn('ასპირინი 75–100 მგ', sched_no_cr['კლოპიდოგრელი / P2Y12']['rule'])
        self.assertIn('7–10 დღით', sched_no_cr['პრასუგრელი (ეფიენტი)']['rule'])
        self.assertIn('24 სთ სუფთა თხევადი დიეტა', sched_no_cr['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['rule'])
        self.assertTrue(ev_no_cr['needs_cied'])
        self.assertTrue(ev_no_cr['needs_anesth'])
        self.assertIn('ფონდაპარინუქსი', ev_no_cr['caprini_advice'])
        self.assertIn('აკრძალულია', ev_no_cr['caprini_advice'])

        # Enter weight=82, creat=85 (CrCl >= 50): General surgery -> DOAC 48h; Neuraxial -> DOAC 72h (ASRA 5th Ed)!
        s1_aaa = {
            'section': 'stage1', 'weight_kg': '82', 'height_cm': '176',
            'hb': '138', 'creat': '85', 'inr': '1.05', 'urine_wbc': '1',
            'carotid_r': '20', 'carotid_l': '20', 'lvef': '55', 'aaa_mm': '58',
            'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        }
        self.client.post(reverse('preop-patient', args=[aaa.pk]), s1_aaa)
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {'section': 'stage2', 'anesthesia_plan': 'general'})
        aaa.refresh_from_db()
        self.assertIn('48 საათით', {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        self.client.post(reverse('preop-patient', args=[aaa.pk]), {'section': 'stage2', 'anesthesia_plan': 'neuraxial'})
        aaa.refresh_from_db()
        self.assertIn('72 საათით', {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        # Lower CrCl to 30-49 (weight=62, creat=135 -> CrCl ~36): Dabigatran 96h general vs 120h neuraxial!
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {**s1_aaa, 'weight_kg': '62', 'height_cm': '170', 'creat': '135'})
        aaa.refresh_from_db()
        self.assertIn('120 საათით', {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}['დაბიგატრანი (პრადაქსა)']['rule'])
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {'section': 'stage2', 'anesthesia_plan': 'general'})
        aaa.refresh_from_db()
        self.assertIn('96 საათით', {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}['დაბიგატრანი (პრადაქსა)']['rule'])

    def test_p0_2_self_fill_data_wipe_protection_post_verification_lock_and_passport(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import make_self_fill_token, merged_q, merged_studies

        StaffProfile.objects.update_or_create(
            user=self.user, defaults={'display_name': 'გიორგი აფხაძე', 'full_name': 'გიორგი აფხაძე'}
        )
        today = timezone.localdate()

        p = PreopPatient.objects.create(
            patient_name='თვითშევსება დაცვაძე', age=69, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={'dm_insulin': True, 'loose_teeth': True, 'pacemaker': True},
            studies={'cta_disc': True, 'aaa_mm': 62, 'ecg': 'afib_controlled', 'cxr': 'normal'},
        )
        token = make_self_fill_token(p)
        url = reverse('preop-self-fill', args=[token])

        # Public unauthenticated client submits Self-Fill form
        anon = Client()
        resp_get = anon.get(url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, 'კითხვები საძილე არტერიის შესახებ')
        self.assertContains(resp_get, 'ქსარელტო')

        resp_post = anon.post(url, {
            'age': '69', 'sex': 'M', 'phone': '599112233',
            'cea_symp_event': '1',
            'cea_event_chips': ['amaurosis'],
            'cea_event_date': (today - timezone.timedelta(days=3)).isoformat(),
            'htn': '1',
            'med_aspirin': '1',
            'allergy_iodine': '1',
            'mets_ok': '1',
        })
        self.assertEqual(resp_post.status_code, 302)
        p.refresh_from_db()
        q = merged_q(p)
        s = merged_studies(p)
        # Nurse-only fields MUST remain untouched (zero data wipe!)
        self.assertTrue(s['cta_disc'])
        self.assertEqual(s['aaa_mm'], 62)
        self.assertTrue(q['dm_insulin'])
        self.assertTrue(q['loose_teeth'])
        self.assertTrue(q['pacemaker'])
        # Patient-submitted fields captured cleanly
        self.assertTrue(q['cea_symp_event'])
        self.assertTrue(q['allergy_iodine'])
        self.assertEqual(q['filled_by'], 'პაციენტი (ბმულით)')
        self.assertFalse(q['nurse_verified'])

        # Nurse verifies Tab 1 -> locks Self-Fill link against further public overwrites!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'htn': '1', 'dm_insulin': '1', 'pacemaker': '1',
            'med_aspirin': '1', 'allergy_iodine': '1', 'cta_disc': '1', 'mets_ok': '1',
        })
        p.refresh_from_db()
        self.assertTrue(merged_q(p)['nurse_verified'])

        locked_get = anon.get(url)
        self.assertContains(locked_get, 'კითხვარი უკვე დადასტურებულია სამედიცინო გუნდის მიერ')
        anon.post(url, {'htn': '0', 'med_aspirin': '0', 'phone': '000000'})
        p.refresh_from_db()
        self.assertEqual(p.phone, '599112233')
        self.assertTrue(merged_q(p)['med_aspirin'])

        # Passport renders Georgian ECG/CXR labels (never raw 'afib_controlled'/'normal') and nurse verifier name
        pass_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertContains(pass_resp, 'ნორმოსისტოლური მოციმციმე არითმია')
        self.assertContains(pass_resp, 'ნორმა (კეროვან-ინფილტრაციული ცვლილების გარეშე)')
        self.assertContains(pass_resp, 'გიორგი აფხაძე')

    def test_p0_3_2d_triage_state_machine_specialist_signoff_hold_and_alert_ack_stability(self):
        from datetime import timedelta

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, STATUS_READY, evaluate

        today = timezone.localdate()
        StaffProfile.objects.get_or_create(user=self.user, defaults={'display_name': 'ტესტ ქირურგი'})
        p = PreopPatient.objects.create(
            patient_name='გიგანტ ანევრიზმაძე', age=72, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today - timedelta(days=9),
            started_on=today - timedelta(days=9),
        )
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'aaa_pain': '0', 'htn': '1', 'cad': '1', 'pulm': '1',
            'med_aspirin': '1', 'med_chips': ['statin', 'beta_blocker'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1', 'labs_date': today.isoformat(),
            'bp_sys': '134', 'bp_dia': '78', 'hr': '78', 'irregular_hr': '1', 'spo2': '96',
            'hb': '136', 'creat': '88', 'inr': '1.04', 'urine_wbc': '2',
            'carotid_r': '25', 'carotid_l': '20', 'lvef': '36', 'aaa_mm': '72',
            'cxr': 'emphysema', 'ecg': 'afib_controlled', 'cta_disc': '1',
        })
        p.refresh_from_db()
        ev1 = evaluate(p, today=today)
        self.assertEqual(ev1['status'], STATUS_ALERT)
        self.assertEqual(ev1['priority_tier'], 'urgent')
        self.assertFalse(ev1['workup_ready'])

        # Alert acknowledgement signature stays stable across day 9 -> day 10!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'signoff', 'action': 'ack_alerts',
        })
        p.refresh_from_db()
        self.assertTrue(evaluate(p, today=today)['alerts_acked'])
        self.assertTrue(evaluate(p, today=today + timedelta(days=1))['alerts_acked'])

        # Putting patient on SomedayList.HOLD pauses over_target_days
        p.someday_list = PreopPatient.SomedayList.HOLD
        p.visit_date = None
        p.save()
        self.assertFalse(evaluate(p, today=today)['over_target_days'])
        p.someday_list = ''
        p.visit_date = today - timedelta(days=9)
        p.save()

        # Specialist sign-off (cardio_decision='cleared' + anesth_done='1') + completing remaining tasks ->
        # 72 mm urgent AAA reaches STATUS_READY with priority_tier == 'urgent'!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'spiro_fev1': '46',
            'blood_reserved': '1',
            'decolonization_ready': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        p.refresh_from_db()
        ev_ready = evaluate(p, today=today)
        self.assertTrue(ev_ready['workup_ready'])
        self.assertEqual(ev_ready['red_flags'], [])
        self.assertEqual(ev_ready['status'], STATUS_READY)
        self.assertEqual(ev_ready['priority_tier'], 'urgent')
        self.assertTrue(any('[სასწრაფო პრიორიტეტი]' in af for af in ev_ready['amber_flags']))

    def test_p0_4a_unit_guards_and_live_calendar_dates(self):
        from datetime import timedelta

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import calc_egfr, evaluate, merged_studies, normalize_glucose_mmol, normalize_hb_gl

        today = timezone.localdate()

        # 1. Unit guards (Track A3): Hb 29 stays 29 g/L, 13.4 -> 134 g/L; Glucose 34 stays 34.0 mmol/L HHS, 40 mg/dL -> 2.2 mmol/L hypo, 180 -> 10.0
        self.assertEqual(normalize_hb_gl(29), 29.0)
        self.assertEqual(normalize_hb_gl(13.4), 134.0)
        self.assertEqual((normalize_glucose_mmol(34), normalize_glucose_mmol(40)), (34.0, 2.22))
        self.assertEqual(normalize_glucose_mmol(180), 10.0)
        self.assertIsNone(calc_egfr(90, None, 'M'))

        # Ambiguous creatinine (22 without unit) blocks eGFR & Stage 1 until creat_unit is chosen; PLT/WBC auto-scale
        p_u = PreopPatient.objects.create(
            patient_name='ერთეულ საზღვრაძე', age=65, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_claud', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[p_u.pk]), {
            'section': 'questionnaire', 'side': 'R', 'med_aspirin': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p_u.pk]), {
            'section': 'stage1',
            'hb': '29', 'wbc': '7800', 'plt': '245000', 'inr': '1.0', 'glucose': '34', 'creat': '22',
            'abi_r': '0.62', 'abi_l': '0.95', 'pta_access_site': 'cfa_clean', 'ecg': 'normal',
        })
        p_u.refresh_from_db()
        s_u = merged_studies(p_u)
        self.assertEqual(s_u['wbc'], 7.8)
        self.assertEqual(s_u['plt'], 245)
        ev_u = evaluate(p_u, today=today)
        self.assertTrue(ev_u['creat_unit_ambiguous'])
        self.assertIsNone(ev_u['egfr'])
        self.assertFalse(ev_u['s1_core_done'])
        self.assertTrue(ev_u['hb_unit_ambiguous'])
        self.assertIsNone(ev_u['hb_gl'])
        self.assertTrue(any('ჰემოგლობინის ერთეული' in f for f in ev_u['red_flags'] + ev_u['amber_flags']))
        self.assertTrue(any('კრიტიკული გლიკემია (გლუკოზა 34 მმოლ/ლ)' in rf for rf in ev_u['red_flags']))
        p_u.studies['hb'] = 8.5
        ev_anem = evaluate(p_u, today=today)
        self.assertTrue(any('Hb 85 გ/ლ < 100' in rf for rf in ev_anem['red_flags']))

        # Clarifying creat_unit='mgdl' resolves ambiguity and computes severe renal failure eGFR (<15)
        self.client.post(reverse('preop-patient', args=[p_u.pk]), {
            'section': 'stage1',
            'hb': '132', 'wbc': '7.8', 'plt': '245', 'inr': '1.0', 'glucose': '5.4',
            'creat': '22', 'creat_unit': 'mgdl',
            'abi_r': '0.62', 'abi_l': '0.95', 'pta_access_site': 'cfa_clean', 'ecg': 'normal',
        })
        p_u.refresh_from_db()
        ev_mgdl = evaluate(p_u, today=today)
        self.assertFalse(ev_mgdl['creat_unit_ambiguous'])
        self.assertLess(ev_mgdl['egfr'], 10)

        # 2. Live calendar dates: cea_event_date ticks past 14-day ESVS window dynamically!
        cea_live = PreopPatient.objects.create(
            patient_name='კალენდარ ცოცხლაძე', age=67, sex='F', side='L',
            pathway='cea', op_subtype='cea_symp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea_live.pk]), {
            'section': 'questionnaire', 'side': 'L', 'cea_symp_event': '1',
            'cea_event_date': (today - timedelta(days=5)).isoformat(),
            'med_aspirin': '1', 'med_clopidogrel': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        cea_live.refresh_from_db()
        self.assertEqual(evaluate(cea_live, today=today)['days_since_ev'], 5)
        ev_future = evaluate(cea_live, today=today + timedelta(days=11))
        self.assertEqual(ev_future['days_since_ev'], 16)
        self.assertTrue(any('14-დღიანი ოპტიმალური ფანჯარა' in rf for rf in ev_future['red_flags']))

    def test_p0_4b_non_compressible_abi_tbi_and_case_auto_import(self):
        from django.core.files.base import ContentFile
        from django.utils import timezone

        from ..models import DopplerStudy, PreopPatient
        from ..preop import STATUS_PROGRESS, STATUS_READY, evaluate, merged_q, merged_studies

        today = timezone.localdate()

        # 3. Non-compressible ABI > 1.40 on VENOUS blocks compression_ready until Toe Pressure >= 60 or TBI >= 0.70
        ven = PreopPatient.objects.create(
            patient_name='მონკებერგ ვენაძე', age=68, sex='F', side='R',
            pathway='venous', op_subtype='evla', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'questionnaire', 'side': 'R', 'ven_ceap': 'C5', 'dm': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage1', 'labs_date': today.isoformat(),
            'bp_sys': '128', 'bp_dia': '78', 'hr': '72', 'spo2': '98',
            'hb': '130', 'plt': '230', 'inr': '1.0', 'glucose': '6.1',
            'ven_duplex_status': 'deep_patent_reflux', 'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage2', 'abi_r': '1.52', 'abi_l': '1.10',
            'compression_ready': '1', 'vte_prophylaxis_planned': '1',
        })
        ven.refresh_from_db()
        ev_ven_nc = evaluate(ven, today=today)
        self.assertTrue(ev_ven_nc['abi_non_compressible'])
        self.assertTrue(ev_ven_nc['needs_tbi_check'])
        self.assertEqual(ev_ven_nc['status'], STATUS_PROGRESS)

        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage2', 'abi_r': '1.52', 'abi_l': '1.10',
            'tp_r': '68', 'tp_l': '74', 'compression_ready': '1', 'vte_prophylaxis_planned': '1',
        })
        ven.refresh_from_db()
        self.assertEqual(evaluate(ven, today=today)['status'], STATUS_READY)

        # 4. Conference Case -> Preop 1-click Auto-Import of Doppler (ABI/TBI/TP) + WIfI + GLASS + GSV status
        c_imp = self.make_case(
            patient_name='იმპორტ კონფერენციაძე', age=69, sex='M', side='R',
            diagnosis='ქვემო კიდურის ათეროსკლეროზი', proposal='ბარძაყ-მუხლქვეშა შუნტირება',
        )
        c_imp.staging = {
            'R': {
                'w': 2, 'i': 2, 'fi': 2, 'vein': 'no', 'cfa': 'B',
                'segments': {
                    'sfa1': {'stenosis': 'occlusion', 'calc': False},
                    'pop1': {'stenosis': 'severe', 'calc': False},
                    'at1': {'stenosis': 'occlusion', 'calc': False},
                    'pt1': {'stenosis': 'occlusion', 'calc': False},
                    'per1': {'stenosis': 'mild', 'calc': False},
                },
            },
        }
        c_imp.save()
        ds = DopplerStudy(
            case=c_imp, performed_on=today,
            data={
                'pressures': {'R': {'brachial': 140, 'pt': 220, 'dp': 215, 'toe': 26}, 'L': {}},
                'indices': {'R': {'abi_pt': 1.55, 'abi_dp': 1.48, 'tbi': 0.28}, 'L': {'abi_pt': 0.96}},
                'sites': [],
            },
        )
        ds.file.save('dop.pdf', ContentFile(b'%PDF-1.4 test'), save=True)

        self.client.post(reverse('preop-new'), {'case': c_imp.pk})
        p_imp = PreopPatient.objects.get(case=c_imp)
        self.assertEqual(p_imp.op_subtype, 'bypass_clti')
        q_imp = merged_q(p_imp)
        s_imp = merged_studies(p_imp)
        self.assertTrue(q_imp['abf_ulcer'])
        self.assertTrue(q_imp['abf_wet_infection'])
        self.assertEqual(s_imp['abi_r'], 1.55)
        self.assertEqual(s_imp['tbi_r'], 0.28)
        self.assertEqual(s_imp['tp_r'], 26)
        self.assertEqual(s_imp['pad_gsv_status'], 'inadequate')
        self.assertEqual(s_imp['pad_runoff_status'], 'tibial_single')
        self.assertEqual(s_imp['pta_access_site'], 'cfa_calcified_scar')
        self.assertIn('WIfI Stage', s_imp['case_summary_badge'])

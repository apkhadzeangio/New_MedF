"""Tests for core Pre-op AAA, CEA, ABF, and Venous clinical pathways."""
import datetime
import io

from django.urls import reverse

from .helpers import PrivateMediaTest


class PreopTests(PrivateMediaTest):
    """Tweek-style preoperative calendar, Infrarenal AAA nurse POA, and Cardiology Audit."""

    def test_tweek_calendar_case_transfer_and_clock_persistence(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        # Menu shows "წინასაოპერაციო"
        cal = self.client.get(reverse('preop'))
        self.assertEqual(cal.status_code, 200)
        self.assertContains(cal, 'წინასაოპერაციო')
        self.assertContains(cal, 'ინფრარენალური AAA')

        # 1-click transfer from a Conference Case -> Someday "conf" list (no duplicate on second click)
        case = self.make_case(patient_name='ბერიძე გიორგი', age=69, sex='M', decision='EVAR')
        r1 = self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertRedirects(r1, reverse('preop-patient', args=[p.pk]))
        self.assertEqual((p.op_subtype, p.someday_list), ('evar', 'conf'))
        r2 = self.client.get(reverse('preop-new'), {'case': case.pk})
        self.assertEqual(PreopPatient.objects.filter(case=case).count(), 1)
        self.assertRedirects(r2, reverse('preop-patient', args=[p.pk]))

        # Inline quick-add onto a visit date 9 days ago -> overdue (>7 days) Red Flag
        nine_days_ago = (timezone.localdate() - datetime.timedelta(days=8)).isoformat()
        self.client.post(reverse('preop-new'), {
            'patient_name': 'მაისურაძე ოთარი',
            'slot': nine_days_ago,
            'op_subtype': 'open',
            'surgeon': str(self.user.pk),
        })
        late = PreopPatient.objects.get(patient_name='მაისურაძე ოთარი')
        self.assertEqual(evaluate(late)['days_elapsed'], 9)

        # Drag-and-drop move to tomorrow does NOT reset started_on or the 7-day rupture clock
        tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        mv = self.client.post(
            reverse('preop-move', args=[late.pk]),
            {'slot': tomorrow},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).json()
        late.refresh_from_db()
        self.assertEqual(str(late.visit_date), tomorrow)
        self.assertEqual(mv['days_elapsed'], 9)

    def _create_aaa_workup_patient(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, STATUS_NO_Q, evaluate

        today = timezone.localdate().isoformat()
        self.client.post(reverse('preop-new'), {
            'patient_name': 'კაპანაძე თამაზი',
            'age': '70',
            'sex': 'M',
            'slot': today,
            'op_subtype': 'open',
            'surgeon': str(self.user.pk),
        })
        p = PreopPatient.objects.get(patient_name='კაპანაძე თამაზი')
        self.assertEqual(evaluate(p)['status'], STATUS_NO_Q)

        # Step 1: Nurse fills questionnaires (asymptomatic AAA 60mm, >=4 METs, active ulcer symptoms, COPD/pulm, clopidogrel)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1',
            'htn': '1',
            'ulcer': '1',
            'ulcer_active_symp': '1',
            'pulm': '1',
            'smoker': '1',
            'med_aspirin': '1',
            'med_clopidogrel': '1',
            'other_meds': 'პერინდოპრილი 5 მგ',
            'aaa_mm': '60',
            'cta_months': '1',
            'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_gastro'])
        self.assertTrue(ev['needs_spirometry'])
        self.assertTrue(ev['needs_med_hold'])
        return p

    def test_aaa_thresholds_two_stage_flow_anamnesis_and_cardiology_audit(self):
        from django.utils import timezone

        from ..preop import STATUS_ALERT, STATUS_READY, evaluate
        from ..views import BACKUP_MODELS

        self.assertIn('mdf.preoppatient', BACKUP_MODELS)
        self.assertIn('mdf.preopfile', BACKUP_MODELS)
        p = self._create_aaa_workup_patient()

        # Step 2: Stage 1 labs with pyuria + bacteriuria (urine WBC 18 + nitrite+) -> triggers UTI prosthetic-graft Red Flag
        s1_base = {
            'section': 'stage1',
            'labs_date': timezone.localdate().isoformat(),
            'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '135', 'bp_dia': '82', 'hr': '70', 'spo2': '97',
            'hb': '13.6',           # g/dL auto-normalized to 136 g/L
            'wbc': '7.5', 'plt': '210', 'inr': '1.05',
            'creat': '88.4',        # 1.0 mg/dL -> normal eGFR >= 60
            'k': '4.2', 'na': '140', 'glucose': '5.5', 'albumin': '39',
            'urine_wbc': '18', 'urine_nitrite': '1',
            'carotid_r': '30', 'carotid_l': '25',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '56',
            'blood_group': 'A (II) Rh (+)',
        }
        self.client.post(reverse('preop-patient', args=[p.pk]), s1_base)
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['hb_gl'], 136.0)
        self.assertGreaterEqual(ev['egfr'], 60)
        self.assertTrue(ev['uti_suspected'])
        self.assertEqual(ev['status'], STATUS_ALERT)

        # Clean urinalysis, but cardiologist orders CAG in asymptomatic >=4 METs LVEF 56% patient -> ESC 2022 Class III audit flag!
        clean_s1 = {**s1_base, 'hb': '136', 'urine_wbc': '2'}
        clean_s1.pop('urine_nitrite')
        self.client.post(reverse('preop-patient', args=[p.pk]), clean_s1)
        s2_base = {
            'section': 'stage2',
            'planned_surgery_date': (timezone.localdate() + datetime.timedelta(days=10)).isoformat(),
            'spiro_fev1': '78', 'gastro_result': 'remission', 'ppi_started': '1',
            'blood_reserved': '1', 'med_hold_agreed': '1', 'anesth_done': '1',
        }
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            **s2_base, 'cardiologist': 'გარე კარდიოლოგი', 'cardio_decision': 'cag',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['cardio_non_compliant'])
        self.assertEqual(ev['status'], STATUS_ALERT)

        audit_page = self.client.get(reverse('preop-audit'))
        self.assertContains(audit_page, 'ESC 2022 Class III')
        self.assertContains(audit_page, 'ლიკა ინჯია')

        # Lika Injia clears the patient -> all tasks complete -> STATUS_READY!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            **s2_base, 'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertFalse(ev['cardio_non_compliant'])
        self.assertEqual(ev['status'], STATUS_READY)

        # Auto-anamnesis & Surgeon sign-off moves patient to Ready Someday list
        page = self.client.get(reverse('preop-patient', args=[p.pk]), {'tab': 'doc'})
        self.assertContains(page, '60 მმ')
        self.assertContains(page, 'კლოპიდოგრელი')
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'signoff', 'action': 'approve', 'move_ready': '1',
            'anamnesis': 'დადასტურებული მოკლე ანამნეზი.',
        })
        p.refresh_from_db()
        self.assertIsNotNone(p.approved_at)
        self.assertEqual(p.someday_list, 'ready')

        # Handoff prefills to Prescription and Blood Order
        rx = self.client.get(reverse('niv-cover'), {'preop': p.pk})
        self.assertContains(rx, 'კაპანაძე თამაზი')
        blood = self.client.get(reverse('blood-order'), {'preop': p.pk})
        self.assertContains(blood, 'A (II) Rh (+)')

    def test_private_photo_and_pdf_uploads_and_cleanup(self):
        import os

        from PIL import Image

        from ..models import PreopFile, PreopPatient

        p = PreopPatient.objects.create(patient_name='გელაშვილი ზურაბი', created_by=self.user)
        buf = io.BytesIO()
        Image.new('RGB', (400, 300), (200, 210, 220)).save(buf, 'PNG')
        buf.seek(0)
        buf.name = 'ecg.png'

        pdf_buf = io.BytesIO(b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n')
        pdf_buf.name = 'labs.pdf'

        self.client.post(reverse('preop-files', args=[p.pk]), {
            'category': 'labs',
            'caption': 'სისხლის საერთო',
            'file': [buf, pdf_buf],
        })
        self.assertEqual(PreopFile.objects.filter(patient=p).count(), 2)
        img_rec = PreopFile.objects.get(patient=p, is_pdf=False)
        pdf_rec = PreopFile.objects.get(patient=p, is_pdf=True)
        img_path = img_rec.file.path
        pdf_path = pdf_rec.file.path

        self.assertEqual(self.client.get(reverse('preop-file', args=[img_rec.pk]))['Content-Type'], 'image/jpeg')
        self.assertEqual(self.client.get(reverse('preop-file', args=[pdf_rec.pk]))['Content-Type'], 'application/pdf')

        # Unauthenticated users cannot read private preoperative files
        self.client.logout()
        self.assertEqual(self.client.get(reverse('preop-file', args=[pdf_rec.pk])).status_code, 302)

        # Deleting patient deletes underlying private files on disk
        self.client.force_login(self.user)
        self.client.post(reverse('preop-move', args=[p.pk]), {'action': 'delete'})
        self.assertFalse(PreopPatient.objects.filter(pk=p.pk).exists())
        self.assertFalse(os.path.exists(img_path))
        self.assertFalse(os.path.exists(pdf_path))


class PreopCeaAndAbfTests(PrivateMediaTest):
    """Carotid Endarterectomy (CEA — ESVS 2023) & Aortobifemoral Bypass (ABF — ESVS 2024 / GVG) tests."""

    def _setup_symptomatic_cea(self):
        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, evaluate

        case = self.make_case(
            patient_name='ნოზაძე მერაბი', age=68, sex='M', side='R',
            diagnosis='მარჯვენა შიგნითა საძილე არტერიის სიმპტომური სტენოზი (TIA)',
            decision='კაროტიდული ენდარტერექტომია',
        )
        r = self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertRedirects(r, reverse('preop-patient', args=[p.pk]))
        self.assertEqual((p.pathway, p.op_subtype, p.side), ('cea', 'cea_symp', 'R'))
        self.assertEqual(evaluate(p)['target_days'], 5)

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cea_symp_event': '1',
            'cea_days_since_event': '16', 'cea_event_text': 'მარცხენა მტევნის გარდამავალი სისუსტე',
            'cea_prior_neck': '1', 'cea_prior_neck_text': 'თირეოიდექტომია 2019',
            'mets_ok': '1', 'htn': '1', 'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_neuro'])
        self.assertTrue(ev['needs_laryngoscopy'])
        self.assertTrue(ev['needs_brain_ct'])
        self.assertFalse(ev['needs_spirometry'])
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('14-დღიანი', joined_red)
        self.assertIn('ანტიაგრეგანტს', joined_red)
        return p

    def test_cea_esvs_2023_symptomatic_flow_laryngoscopy_antiplatelet_and_14day_window(self):
        from django.utils import timezone

        from ..preop import STATUS_READY, evaluate, generate_anamnesis

        p = self._setup_symptomatic_cea()
        # Within 14-day window (5d ago) + Aspirin, but Stage 1 has BP 186/112 & contralateral 85% stenosis
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cea_symp_event': '1',
            'cea_days_since_event': '5', 'cea_event_text': 'მარცხენა მტევნის გარდამავალი სისუსტე',
            'cea_prior_neck': '1', 'cea_prior_neck_text': 'თირეოიდექტომია 2019',
            'mets_ok': '1', 'htn': '1', 'med_aspirin': '1', 'cta_disc': '1',
        })
        s1_cea = {
            'section': 'stage1',
            'labs_date': timezone.localdate().isoformat(),
            'bp_sys': '186', 'bp_dia': '112', 'hr': '68', 'spo2': '98',
            'hb': '142', 'wbc': '6.8', 'plt': '230', 'inr': '1.02',
            'creat': '84', 'k': '4.3', 'na': '140', 'glucose': '5.4', 'urine_wbc': '1',
            'carotid_r': '82', 'carotid_l': '85',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '58', 'cta_disc': '1',
        }
        self.client.post(reverse('preop-patient', args=[p.pk]), s1_cea)
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['s1_core_done'])
        self.assertEqual(ev['key_badge'], 'R 82%')
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('ჰიპერპერფუზიის', joined_red)
        self.assertIn('კონტრალატერალური', joined_red)

        # Ipsilateral 100% occlusion triggers non-operative occlusion Red Flag
        p.studies['carotid_r'] = 100
        p.save()
        self.assertIn('სრული ოკლუზია (100%)', ' | '.join(evaluate(p)['red_flags']))

        # Stabilized BP + contralateral 35%; vocal cord palsy triggers Red Flag, normal clears to READY
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            **s1_cea, 'bp_sys': '136', 'bp_dia': '80', 'carotid_l': '35',
        })
        s2_cea = {
            'section': 'stage2', 'neuro_consult_done': '1', 'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
        }
        self.client.post(reverse('preop-patient', args=[p.pk]), {**s2_cea, 'laryngoscopy_res': 'palsy'})
        p.refresh_from_db()
        self.assertIn('სახმო იოგის პარეზი', ' | '.join(evaluate(p)['red_flags']))

        self.client.post(reverse('preop-patient', args=[p.pk]), {**s2_cea, 'laryngoscopy_res': 'normal'})
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_READY)
        anam = generate_anamnesis(p, ev)
        self.assertIn('მარჯვენა კაროტიდული ენდარტერექტომია', anam)
        self.assertIn('R ICA 82%', anam)
        self.assertIn('სახმო იოგები მოძრავია', anam)

        # Prescription prefill marks neuro=True and formats carotid diagnosis
        rx = self.client.get(reverse('niv-cover'), {'preop': p.pk})
        self.assertContains(rx, 'მარჯვენა შიგნითა საძილე არტერიის სიმპტომური სტენოზი (82%)')
        self.assertTrue(rx.context['niv'].neuro)

    def _setup_abf_clti_patient(self):
        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, evaluate

        case = self.make_case(
            patient_name='ლომიძე ვახტანგი', age=64, sex='M', side='B',
            diagnosis='ლერიშის სინდრომი, ქვემო კიდურების კრიტიკული იშემია (CLTI)',
            decision='აორტო-ბიფემორალური შუნტირება',
        )
        self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertEqual((p.pathway, p.op_subtype), ('abf', 'abf_clti'))
        self.assertEqual(evaluate(p)['target_days'], 7)

        q_base = {
            'section': 'questionnaire', 'abf_claud_m': '25', 'abf_duration': '8 თვე',
            'abf_buttock_pain': '1', 'abf_rest_pain': '1', 'abf_ulcer': '1',
            'abf_ulcer_text': 'მარჯვენა I თითის წყლული', 'abf_wet_infection': '1',
            'abf_groin_issue': '1', 'abf_prior_vasc': '1',
            'abf_prior_vasc_text': 'მარჯვენა CIA სტენტი 2022',
            'mets_ok': '1', 'pulm': '1', 'smoker': '1', 'med_aspirin': '1', 'cta_disc': '1',
        }
        self.client.post(reverse('preop-patient', args=[p.pk]), q_base)
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_spirometry'])
        self.assertTrue(ev['needs_blood'])
        self.assertTrue(ev['needs_wound_care'])
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('სველი ნეკროზი', joined_red)
        self.assertIn('ინტერტრიგო', joined_red)
        return p, q_base

    def test_abf_esvs_2024_clti_wet_gangrene_groin_outflow_spirometry_and_pathway_filters(self):
        from django.utils import timezone

        from ..preop import STATUS_READY, evaluate, generate_anamnesis

        p, q_base = self._setup_abf_clti_patient()
        # Step 2: Dry ulcer + clean groin, but Stage 1 has juxtarenal occlusion, poor outflow & albumin 27
        q_dry = {**q_base, 'abf_ulcer_text': 'მარჯვენა I თითის მშრალი ნეკროზი'}
        q_dry.pop('abf_wet_infection')
        q_dry.pop('abf_groin_issue')
        self.client.post(reverse('preop-patient', args=[p.pk]), q_dry)
        s1_abf = {
            'section': 'stage1', 'labs_date': timezone.localdate().isoformat(),
            'abi_r': '0.32', 'abi_l': '0.41',
            'abf_clamp_zone': 'juxtarenal_calcified', 'abf_outflow': 'poor_outflow',
            'bp_sys': '140', 'bp_dia': '84', 'hr': '76', 'spo2': '96',
            'hb': '132', 'wbc': '7.9', 'plt': '245', 'inr': '1.04',
            'creat': '90', 'k': '4.4', 'na': '139', 'glucose': '5.8', 'albumin': '27',
            'urine_wbc': '3', 'carotid_r': '35', 'carotid_l': '20',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '55', 'cta_disc': '1',
            'blood_group': 'O (I) Rh (+)',
        }
        self.client.post(reverse('preop-patient', args=[p.pk]), s1_abf)
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['s1_core_done'])
        self.assertEqual(ev['key_badge'], 'ABI 0.32')
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('იუქსტარენალური', joined_red)
        self.assertIn('Outflow', joined_red)
        self.assertIn('ჰიპოალბუმინემია', joined_red)

        # Step 3: Infrarenal clamp + profunda stenosis (Amber profundaplasty note) + albumin 38 + Stage 2 done -> READY
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            **s1_abf, 'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'profunda_stenosis', 'albumin': '38',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'spiro_fev1': '74', 'wound_care_done': '1',
            'blood_reserved': '1', 'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_READY)
        self.assertIn('პროფუნდოპლასტიკა', ' | '.join(ev['amber_flags']))
        anam = generate_anamnesis(p, ev)
        self.assertIn('აორტო-ბიფემორალური შუნტირება', anam)
        self.assertIn('ABI R 0.32', anam)

        # Pathway filters on Tweek weekly view and Cardiology Audit view
        self.assertContains(self.client.get(reverse('preop'), {'pathway': 'abf'}), 'ლომიძე ვახტანგი')
        self.assertNotContains(self.client.get(reverse('preop'), {'pathway': 'cea'}), 'ლომიძე ვახტანგი')
        self.assertContains(self.client.get(reverse('preop-audit'), {'pathway': 'abf'}), 'ლომიძე ვახტანგი')

        blood = self.client.get(reverse('blood-order'), {'preop': p.pk})
        self.assertContains(blood, 'აორტო-ილიაკური ოკლუზიური დაავადება')
        self.assertContains(blood, 'O (I) Rh (+)')

    def test_cea_crescendo_thrombolysis_brain_hemorrhage_and_cardio_audit_stroke_window(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        six_days_ago = (timezone.localdate() - datetime.timedelta(days=5)).isoformat()
        self.client.post(reverse('preop-new'), {
            'patient_name': 'ჭანტურია ნინო',
            'age': '71',
            'sex': 'F',
            'side': 'L',
            'slot': six_days_ago,
            'op_subtype': 'cea_symp',
            'surgeon': str(self.user.pk),
        })
        p = PreopPatient.objects.get(patient_name='ჭანტურია ნინო')
        self.assertEqual((p.pathway, p.side), ('cea', 'L'))

        # Crescendo TIA + thrombolysis <6d + disabling stroke + >5 prep days -> all flagged
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'L',
            'cea_symp_event': '1',
            'cea_days_since_event': '4',
            'cea_crescendo': '1',
            'cea_thrombolysis': '1',
            'cea_disabling_stroke': '1',
            'mets_ok': '1',
            'med_aspirin': '1',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '130', 'bp_dia': '80', 'hr': '72', 'spo2': '97',
            'hb': '128', 'wbc': '7.1', 'plt': '215', 'inr': '1.0',
            'creat': '75', 'k': '4.1', 'na': '140', 'glucose': '5.2',
            'urine_wbc': '2',
            'carotid_r': '20', 'carotid_l': '78',
            'cea_cta_status': 'high_calcified',
            'brain_imaging': 'large_or_hemorrhage',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '60',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'cardiologist': 'გარე კარდიოლოგი',
            'cardio_decision': 'stress',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['over_target_days'])
        self.assertTrue(ev['cardio_non_compliant'])
        red_blob = ' | '.join(ev['red_flags'])
        self.assertIn('Crescendo TIA', red_blob)
        self.assertIn('თრომბოლიზისი', red_blob)
        self.assertIn('mRS >= 3', red_blob)
        self.assertIn('>1/3 MCA', red_blob)
        self.assertIn('ESC 2022 Class III', red_blob)

        page = self.client.get(reverse('preop-patient', args=[p.pk]), {'tab': 's2'})
        self.assertContains(page, '14-დღიანი თერაპიული ფანჯრის')

        # Elective Claudication ABF and Asymptomatic CEA use 14-day prep target
        p_asymp = PreopPatient.objects.create(patient_name='ასიმპტომური ტესტი', op_subtype='cea_asymp', side='L')
        self.assertEqual((p_asymp.pathway, evaluate(p_asymp)['target_days']), ('cea', 14))
        p_claud = PreopPatient.objects.create(patient_name='ლერიშის ტესტი', op_subtype='abf_claud')
        self.assertEqual((p_claud.pathway, evaluate(p_claud)['target_days']), ('abf', 14))


class PreopVenousTests(PrivateMediaTest):
    """EVLA & Phlebectomy (ESVS 2022 / ESC 2022) lean day-surgery pathway & Patient Self-Fill Link."""

    def test_evla_lean_day_surgery_and_signed_patient_self_fill_link(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_READY, evaluate, generate_anamnesis, make_self_fill_token

        self.client.post(reverse('preop-new'), {
            'patient_name': 'ჯაფარიძე სალომე',
            'slot': timezone.localdate().isoformat(),
            'op_subtype': 'evla',
            'side': 'L',
            'surgeon': str(self.user.pk),
        })
        p = PreopPatient.objects.get(patient_name='ჯაფარიძე სალომე')
        self.assertEqual((p.pathway, p.op_subtype, p.side, p.sex), ('venous', 'evla', 'L', 'F'))

        # Unauthenticated patient fills own questionnaire via signed token; bad token gets 404
        token = make_self_fill_token(p)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('preop-self-fill', args=['tampered-token'])).status_code, 404)

        sf_get = self.client.get(reverse('preop-self-fill', args=[token]))
        self.assertEqual(sf_get.status_code, 200)
        self.assertContains(sf_get, 'ჯაფარიძე სალომე')

        sf_post = self.client.post(reverse('preop-self-fill', args=[token]), {
            'age': '34',
            'weight_kg': '62',
            'height_cm': '168',
            'side': 'L',
            'ven_ceap': 'C2',
            'mets_ok': '1',
        }, follow=True)
        self.assertContains(sf_post, 'კითხვარი შენახულია')

        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['q']['filled'])
        self.assertEqual(ev['q']['filled_by'], 'პაციენტი (ბმულით)')
        # Healthy young EVLA does NOT demand routine ECG, Spirometry, Cardiology, or Anesthesiology!
        self.assertFalse(ev['needs_ecg_venous'])
        self.assertFalse(ev['needs_spirometry'])
        self.assertFalse(ev['needs_cardio'])
        self.assertFalse(ev['needs_anesth'])
        self.assertEqual(ev['total_count'], 3)  # Q + Stage 1 + Class II Compression Stocking

        # Nurse enters lean Stage 1 + Compression Stocking ready -> STATUS_READY!
        self.client.force_login(self.user)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'labs_date': timezone.localdate().isoformat(),
            'bp_sys': '118', 'bp_dia': '74', 'hr': '70', 'spo2': '99',
            'hb': '132', 'wbc': '6.4', 'plt': '245', 'inr': '1.01', 'glucose': '4.9',
            'ven_duplex_status': 'deep_patent_reflux',
            'ven_target_vein': 'gsv',
            'ven_gsv_mm': '7.5',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'compression_ready': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_bhcg_negative': '1',
        })
        p.refresh_from_db()
        # Unverified patient self-fill cannot reach STATUS_READY until nurse confirms questionnaire
        self.assertEqual(evaluate(p)['clearance_seal']['code'], 'pending')
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'side': 'L', 'ven_ceap': 'C2', 'mets_ok': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_READY)
        self.assertEqual(ev['done_count'], 3)
        anam = generate_anamnesis(p, ev)
        self.assertIn('ენდოვენური ლაზერული აბლაცია', anam)
        self.assertIn('დიდი საჩინო ვენა (GSV)', anam)
        self.assertIn('7.5 მმ', anam)

        # ESVS 2022 Class IIa: Warfarin with INR 2.4 on EVLA raises Amber coordination note, NOT coagulation Red Flag
        p.questionnaire['med_warfarin'] = True
        p.studies['inr'] = 2.4
        p.save()
        ev_w = evaluate(p)
        self.assertFalse(any('INR > 1.35' in m for m in ev_w['red_flags']))
        self.assertTrue(any('ESVS 2022' in m for m in ev_w['amber_flags']))

    def test_phlebectomy_contraindications_dvt_lidocaine_pregnancy_abi_and_vte_prophylaxis(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, STATUS_READY, evaluate

        # Case transfer auto-detects Phlebectomy
        case = self.make_case(
            patient_name='კიკნაძე ნანა', age=38, sex='F', side='R',
            diagnosis='მარჯვენა ქვემო კიდურის ვენების ვარიკოზული დაავადება',
            decision='კომბინირებული ფლებექტომია',
        )
        self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertEqual((p.pathway, p.op_subtype, p.side), ('venous', 'phlebectomy', 'R'))

        # Contraindications: lidocaine allergy + pregnancy + skin infection + DVT + DOAC on spinal phlebectomy
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'ven_ceap': 'C4',
            'ven_dvt_history': '1',
            'ven_lidocaine_allergy': '1',
            'ven_skin_infection': '1',
            'ven_pregnant': '1',
            'claudication': '1',
            'med_doac': '1',
            'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'labs_date': timezone.localdate().isoformat(),
            'bp_sys': '124', 'bp_dia': '78', 'hr': '74', 'spo2': '98',
            'weight_kg': '94', 'height_cm': '165',
            'hb': '128', 'plt': '220', 'inr': '1.05', 'glucose': '5.3', 'creat': '74',
            'ven_duplex_status': 'acute_dvt',
            'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'abi_r': '0.72', 'abi_l': '0.95',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_vte_prophylaxis'])
        self.assertTrue(ev['needs_abi_check'])
        self.assertTrue(ev['needs_anesth'])
        red_blob = ' | '.join(ev['red_flags'])
        amber_blob = ' | '.join(ev['amber_flags'])
        self.assertIn('DVT', red_blob)
        self.assertIn('ლიდოკაინზე', red_blob)
        self.assertIn('ორსულობა', red_blob)
        self.assertIn('წითელი ქარი', red_blob)
        self.assertIn('ABI 0.72 < 0.80', red_blob)
        self.assertIn('სპინალური ანესთეზიისა და ფლებექტომიის', amber_blob)

        # NIV prescription prefill carries lidocaine allergy -> allergy=True and CEAP C4 diagnosis
        rx = self.client.get(reverse('niv-cover'), {'preop': p.pk})
        self.assertTrue(rx.context['niv'].allergy)
        self.assertContains(rx, 'CEAP C4')

        # Resolve contraindications: deep veins patent, normal ABI 1.05, VTE prophylaxis + med hold + anesth + compression -> READY
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'ven_ceap': 'C4',
            'ven_dvt_history': '1',
            'claudication': '1',
            'med_doac': '1',
            'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'labs_date': timezone.localdate().isoformat(),
            'bp_sys': '124', 'bp_dia': '78', 'hr': '74', 'spo2': '98',
            'weight_kg': '94', 'height_cm': '165',
            'hb': '128', 'plt': '220', 'inr': '1.05', 'glucose': '5.3', 'creat': '74',
            'ven_duplex_status': 'deep_patent_reflux',
            'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'planned_surgery_date': (timezone.localdate() + datetime.timedelta(days=10)).isoformat(),
            'abi_r': '1.05', 'abi_l': '1.02',
            'vte_prophylaxis_planned': '1',
            'compression_ready': '1',
            'med_hold_agreed': '1',
            'anesth_done': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_bhcg_negative': '1',
        })
        p.refresh_from_db()
        self.assertEqual(evaluate(p)['status'], STATUS_READY)

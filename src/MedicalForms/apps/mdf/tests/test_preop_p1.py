"""Tests for Pre-op Stage P1 rational testing, fast-track CEA, and board/prescription bridges."""

from django.urls import reverse

from .helpers import PrivateMediaTest


class PreopStageP1RationalAndBridgeTests(PrivateMediaTest):
    """Stage P1 rational testing (NICE NG45 / ESC 2022 / ESVS 2023-2024 / IDSA 2019 / ESAIC 2024),
    Claudication Class III appropriateness + VOYAGER PAD, AAA/CEA anatomy + PBM, and 1-click Preop->Board bridge."""

    def test_p1_1a_fast_track_cea_and_open_aaa_bnp_ppi_asb_spiro(self):
        from datetime import timedelta

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_READY, evaluate

        today = timezone.localdate()

        # 1. Fast-Track Symptomatic CEA ("Time is Brain"): reaches READY without routine CXR, TTE, Urine, or Cardiology!
        cea = PreopPatient.objects.create(
            patient_name='სწრაფ კაროტიდაძე', age=67, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cea_symp_event': '1',
            'cea_event_chips': ['tia'], 'cea_event_date': (today - timedelta(days=3)).isoformat(),
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1', 'labs_date': today.isoformat(), 'bp_sys': '134', 'bp_dia': '78', 'hr': '68', 'spo2': '98',
            'hb': '142', 'creat': '82', 'inr': '1.01', 'carotid_r': '84', 'carotid_l': '25',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small', 'ecg': 'normal', 'cta_disc': '1',
        })
        cea.refresh_from_db()
        ev_cea = evaluate(cea, today=today)
        self.assertFalse(ev_cea['needs_cxr'])
        self.assertFalse(ev_cea['needs_echo_or_bnp'])
        self.assertFalse(ev_cea['needs_cardio'])
        self.assertTrue(ev_cea['s1_core_done'])

        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage2', 'neuro_consult_done': '1', 'anesth_done': '1',
        })
        cea.refresh_from_db()
        self.assertEqual(evaluate(cea, today=today)['status'], STATUS_READY)

        # 2. Open AAA with NT-proBNP < 125 pg/mL, remote ulcer (PPI, no EGD), asymptomatic smoker, ASB urine_wbc=18
        aaa = PreopPatient.objects.create(
            patient_name='რაციონალ აორტაძე', age=68, sex='M',
            pathway='aaa_infra', op_subtype='open', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'questionnaire', 'mets_ok': '1', 'ulcer': '1', 'smoker': '1',
            'med_aspirin': '1', 'med_chips': ['statin'], 'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
        })
        s1_aaa = {
            'section': 'stage1', 'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '130', 'bp_dia': '80', 'spo2': '97',
            'hb': '138', 'creat': '86', 'inr': '1.02',
            'nt_probnp': '84', 'hs_ctn': '9', 'urine_wbc': '18', 'ecg': 'normal',
        }
        self.client.post(reverse('preop-patient', args=[aaa.pk]), s1_aaa)
        aaa.refresh_from_db()
        ev_aaa = evaluate(aaa, today=today)
        self.assertFalse(ev_aaa['needs_carotid_duplex'])
        self.assertFalse(ev_aaa['needs_cxr'])
        self.assertTrue(ev_aaa['needs_echo_or_bnp'])
        self.assertTrue(ev_aaa['s1_core_done'])
        self.assertTrue(ev_aaa['needs_ppi'])
        self.assertFalse(ev_aaa['needs_gastro'])
        self.assertFalse(ev_aaa['needs_spirometry'])
        self.assertFalse(ev_aaa['uti_suspected'])
        self.assertIn('ომეპრაზოლი / პანტოპრაზოლი (PPI)', {m['drug'] for m in ev_aaa['med_schedule']})

        # Elevated NT-proBNP 125-299 -> amber flag; >=300 -> Red Flag
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {**s1_aaa, 'urine_wbc': '2', 'nt_probnp': '210'})
        aaa.refresh_from_db()
        self.assertTrue(any('NT-proBNP (210' in af for af in evaluate(aaa, today=today)['amber_flags']))
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {**s1_aaa, 'urine_wbc': '2', 'nt_probnp': '410'})
        aaa.refresh_from_db()
        self.assertTrue(any('NT-proBNP (410' in rf for rf in evaluate(aaa, today=today)['red_flags']))

    def test_p1_1b_claudication_mets_audit_and_lean_asa1_evla(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, evaluate

        today = timezone.localdate()

        # 3. Claudication METs Cardiology Audit Loophole Closed (mets_ok='0' + mets_limitation='limb' -> ESC 2022 Class III!)
        byp = PreopPatient.objects.create(
            patient_name='კოჭლობა აუდიტაძე', age=65, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire', 'side': 'R', 'abf_rest_pain': '1',
            'mets_ok': '0', 'mets_limitation': 'limb', 'med_aspirin': '1', 'med_chips': ['statin'],
        })
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage1', 'hb': '136', 'creat': '80', 'inr': '1.0',
            'abi_r': '0.35', 'abi_l': '0.90', 'pad_runoff_status': 'popliteal_patent',
            'pad_gsv_status': 'adequate', 'ecg': 'normal', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage2', 'cardiologist': 'გარე კარდიოლოგი', 'cardio_decision': 'cag',
        })
        byp.refresh_from_db()
        ev_byp = evaluate(byp, today=today)
        self.assertFalse(ev_byp['guideline_justifies_extra_cardio'])
        self.assertTrue(ev_byp['cardio_non_compliant'])
        self.assertEqual(ev_byp['status'], STATUS_ALERT)
        self.assertTrue(any('ESC 2022 Class III' in rf for rf in ev_byp['red_flags']))

        # If limitation is cardiac dyspnea ('cardiac'), stress/CAG IS guideline-justified
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire', 'side': 'R', 'abf_rest_pain': '1',
            'mets_ok': '0', 'mets_limitation': 'cardiac', 'med_aspirin': '1', 'med_chips': ['statin'],
        })
        byp.refresh_from_db()
        ev_byp_card = evaluate(byp, today=today)
        self.assertTrue(ev_byp_card['guideline_justifies_extra_cardio'])
        self.assertFalse(ev_byp_card['cardio_non_compliant'])

        # 4. Lean ASA I EVLA (age 34, C2): reaches s1_core_done without INR, glucose, creatinine, or ECG!
        evla = PreopPatient.objects.create(
            patient_name='მსუბუქ ლაზერაძე', age=34, sex='F', side='L',
            pathway='venous', op_subtype='evla', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[evla.pk]), {
            'section': 'questionnaire', 'side': 'L', 'ven_ceap': 'C2', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[evla.pk]), {
            'section': 'stage1', 'hb': '130', 'plt': '240', 'ven_duplex_status': 'deep_patent_reflux',
        })
        evla.refresh_from_db()
        self.assertTrue(evaluate(evla, today=today)['s1_core_done'])

    def test_p1_2_claudication_class3_appropriateness_oac_pta_guard_and_voyager_pad(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate, generate_anamnesis

        today = timezone.localdate()

        # 1. BTK / Infrapopliteal PTA for claudication -> ESVS 2024 / ACC-AHA 2024 Class III Red Flag + <3m BMT amber warning
        pta = PreopPatient.objects.create(
            patient_name='წვივ კოჭლობაძე', age=63, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_claud', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_claud_m': '150', 'abf_duration': '< 2 კვირა',
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage1',
            'hb': '140', 'plt': '230', 'inr': '1.0', 'glucose': '5.4', 'creat': '84',
            'abi_r': '0.62', 'abi_l': '0.98',
            'pta_lesion_site': 'infrapopliteal',
            'pta_access_site': 'cfa_clean',
            'ecg': 'normal',
        })
        pta.refresh_from_db()
        ev_btk = evaluate(pta, today=today)
        self.assertTrue(any('ALI' in rf for rf in ev_btk['red_flags']))
        self.assertFalse(ev_btk['has_hard_contraindication'])
        self.assertFalse(any('≥3 თვიანი' in af for af in ev_btk['amber_flags']))
        pta.questionnaire['abf_duration'] = '> 3 თვე'
        ev_btk_chr = evaluate(pta, today=today)
        self.assertTrue(any('ESVS 2024 / ACC-AHA 2024 Class III' in rf and 'BTK' in rf for rf in ev_btk_chr['red_flags']))
        self.assertTrue(any('≥3 თვიანი' in af for af in ev_btk_chr['amber_flags']))

        # 2. Prosthetic infrainguinal bypass for claudication -> ESVS 2024 Class III Red Flag
        byp_ic = PreopPatient.objects.create(
            patient_name='პროთეზ კოჭლობაძე', age=64, sex='M', side='L',
            pathway='pad_bypass', op_subtype='bypass_claud', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[byp_ic.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'abf_claud_m': '80', 'ic_bmt_3m': '1',
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp_ic.pk]), {
            'section': 'stage1',
            'hb': '138', 'creat': '82', 'inr': '1.0',
            'abi_r': '0.95', 'abi_l': '0.52',
            'pad_runoff_status': 'popliteal_patent',
            'pad_gsv_status': 'inadequate',
            'ecg': 'normal', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp_ic.pk]), {
            'section': 'stage2',
            'alt_vein_mapped': 'no_autovein_prosthetic',
            'post_antithrombotic_plan': 'voyager',
        })
        byp_ic.refresh_from_db()
        ev_pros = evaluate(byp_ic, today=today)
        self.assertTrue(any('სინთეზური ინფრაინგვინალური შუნტირება უკუნაჩვენებია' in rf for rf in ev_pros['red_flags']))
        self.assertIn('VOYAGER PAD', ev_pros['post_antithrombotic_label'])
        self.assertIn('VOYAGER PAD', generate_anamnesis(byp_ic, ev_pros))
        self.assertContains(self.client.get(reverse('preop-passport', args=[byp_ic.pk])), 'VOYAGER PAD')

        # 3. OAC + PTA Guard: prevents triple antithrombotic therapy (OAC + ASA + Clopidogrel)
        pta_oac = PreopPatient.objects.create(
            patient_name='სამმაგ სისხლდენაძე', age=72, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_clti', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[pta_oac.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_rest_pain': '1',
            'med_doac': '1', 'med_aspirin': '1', 'med_clopidogrel': '1',
            'mets_ok': '1',
        })
        pta_oac.refresh_from_db()
        ev_oac = evaluate(pta_oac, today=today)
        self.assertTrue(ev_oac['has_full_oac'])
        self.assertTrue(any('სამმაგი ანტითრომბოზული თერაპია' in rf for rf in ev_oac['red_flags']))
        dapt_tasks = [t['label'] for t in ev_oac['tasks'] if t['key'] == 'dapt']
        self.assertEqual(len(dapt_tasks), 1)
        self.assertIn('სამმაგი თერაპიის გარეშე', dapt_tasks[0])

    def test_p1_3_aaa_cea_anatomical_depth_pbm_iv_iron_and_contrast_budget(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()

        # 1. EVAR with Hostile Neck + Popliteal Aneurysm + CKD4 Contrast Budget (<=30 mL + CO2/IVUS)
        evar = PreopPatient.objects.create(
            patient_name='ანატომია ევარაძე', age=73, sex='M',
            pathway='aaa_infra', op_subtype='evar', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[evar.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'med_aspirin': '1', 'med_chips': ['statin'],
            'aaa_mm': '48', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[evar.pk]), {
            'section': 'stage1',
            'aaa_mm': '48',
            'aaa_morphology': 'saccular',
            'aaa_neck_status': 'hostile_neck',
            'popliteal_screen_status': 'aneurysm_found',
            'hb': '134', 'creat': '240', 'creat_unit': 'umol', 'inr': '1.0', 'urine_wbc': '1',
            'ecg': 'normal', 'cta_disc': '1',
        })
        evar.refresh_from_db()
        ev_evar = evaluate(evar, today=today)
        joined_red = ' | '.join(ev_evar['red_flags'])
        joined_amb = ' | '.join(ev_evar['amber_flags'])
        self.assertIn('Hostile Neck', joined_red)
        self.assertIn('მუხლქვეშა/ბარძაყის ანევრიზმა', joined_red)
        # Saccular 48 mm AAA suppresses the standard "<55 mm conservative" warning!
        self.assertNotIn('< 55 მმ', joined_amb)
        self.assertIn('საკულარული მორფოლოგია', joined_amb)
        self.assertIsNotNone(ev_evar['max_contrast_ml'])
        self.assertLess(ev_evar['max_contrast_ml'], 30)
        self.assertTrue(ev_evar['co2_angio_advised'])

        # 2. CEA High Surgical Risk (prior neck radiation) -> CAS/TCAR consideration advisory
        cea_rad = PreopPatient.objects.create(
            patient_name='დასხივება კაროტიდაძე', age=69, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea_rad.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'cea_prior_neck': '1', 'cea_neck_chips': ['neck_rad'],
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        cea_rad.refresh_from_db()
        ev_rad = evaluate(cea_rad, today=today)
        self.assertTrue(ev_rad['cea_high_surg_risk'])
        self.assertTrue(any('CAS / TCAR' in af for af in ev_rad['amber_flags']))

        # 3. Elective PBM Iron-Deficiency Anemia (Hb 108 + ferritin 18 + TSAT 14%) + Cell Saver + TXA
        aaa_pbm = PreopPatient.objects.create(
            patient_name='რკინა სეივერაძე', age=68, sex='M',
            pathway='aaa_infra', op_subtype='open', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[aaa_pbm.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'med_aspirin': '1', 'med_chips': ['statin'],
            'aaa_mm': '59', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[aaa_pbm.pk]), {
            'section': 'stage1',
            'aaa_mm': '59', 'cta_months': '1', 'cta_disc': '1',
            'hb': '108', 'ferritin': '18', 'tsat': '14',
            'creat': '84', 'inr': '1.0', 'urine_wbc': '1',
            'nt_probnp': '70', 'ecg': 'normal',
        })
        aaa_pbm.refresh_from_db()
        ev_pbm = evaluate(aaa_pbm, today=today)
        self.assertTrue(ev_pbm['pbm_iv_iron_indicated'])
        self.assertTrue(any(t['key'] == 'pbm_iron' and not t['done'] for t in ev_pbm['tasks']))

        self.client.post(reverse('preop-patient', args=[aaa_pbm.pk]), {
            'section': 'stage2',
            'anemia_panel_done': '1',
            'pbm_iv_iron_done': '1',
            'cell_saver_ready': '1',
            'blood_reserved': '1',
            'decolonization_ready': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        aaa_pbm.refresh_from_db()
        self.assertTrue(any(t['key'] == 'pbm_iron' and t['done'] for t in evaluate(aaa_pbm, today=today)['tasks']))
        self.assertContains(self.client.get(reverse('preop-passport', args=[aaa_pbm.pk])), 'Cell Saver')

    def test_p1_4_seamless_preop_to_board_one_click_handoff_bridge(self):
        from django.utils import timezone

        from ..models import BoardEntry, BoardPatient, PreopPatient
        from ..preop import merged_studies

        today = timezone.localdate()

        # 1. Open AAA with HIT, severe beta-lactam allergy, pacemaker, ulcer, Cell Saver -> defaults to ICU + rich safety tasks
        p = PreopPatient.objects.create(
            patient_name='ჰენდოფ დაფაძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1',
            'ulcer': '1',
            'pacemaker': '1',
            'allergy_other': '1',
            'allergy_chips': ['hit_heparin', 'beta_lactam'],
            'med_aspirin': '1',
            'aaa_mm': '62', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'aaa_mm': '62', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '132', 'bp_dia': '78',
            'hb': '135', 'plt': '195', 'inr': '1.04', 'creat': '138',
            'nt_probnp': '92', 'hs_ctn': '11',
            'blood_group': 'O (I) Rh (+)', 'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'cell_saver_ready': '1',
            'blood_reserved': '1',
        })

        resp = self.client.post(reverse('preop-admit-board', args=[p.pk]), {'bed': '3'})
        bp = BoardPatient.objects.get(name='ჰენდოფ დაფაძე')
        self.assertRedirects(resp, reverse('board-patient', args=[bp.pk]))
        self.assertEqual(bp.unit, BoardPatient.ICU)
        self.assertEqual(bp.bed, '3')

        p.refresh_from_db()
        self.assertEqual(merged_studies(p)['board_patient_id'], bp.pk)

        status_entries = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS))
        self.assertEqual(len(status_entries), 1)
        self.assertIn('[წინასაოპერაციო ჰენდოფი]', status_entries[0].text)
        self.assertIn('Hb 135', status_entries[0].text)
        self.assertIn('HIT ანამნეზში', status_entries[0].text)
        self.assertIn('Cell Saver + TXA', status_entries[0].text)

        tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        joined_tasks = ' | '.join(tasks)
        self.assertIn('ფონდაპარინუქსი / არგატრობანი', joined_tasks)
        self.assertNotIn('LMWH პროფილაქტიკური დოზით', joined_tasks)
        self.assertIn('ვანკომიცინით ან კლინდამიცინით', joined_tasks)
        self.assertIn('ომეპრაზოლი / პანტოპრაზოლი (PPI)', joined_tasks)
        self.assertIn('CIED', joined_tasks)
        self.assertIn('ნეფროპროტექცია', joined_tasks)

        # Idempotent repeat POST: does NOT duplicate BoardPatient, STATUS, or TASK entries!
        task_count_before = BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).count()
        self.client.post(reverse('preop-admit-board', args=[p.pk]), {'bed': '3ა'})
        self.assertEqual(BoardPatient.objects.filter(name='ჰენდოფ დაფაძე').count(), 1)
        self.assertEqual(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS).count(), 1)
        self.assertEqual(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).count(), task_count_before)
        bp.refresh_from_db()
        self.assertEqual(bp.bed, '3ა')

        # Preop workspace header now renders direct link to the active BoardPatient
        preop_page = self.client.get(reverse('preop-patient', args=[p.pk]))
        self.assertContains(preop_page, 'დაფაზეა')
        self.assertContains(preop_page, reverse('board-patient', args=[bp.pk]))

        # 2. PAD_BYPASS with VOYAGER PAD defaults to WARD and transfers VOYAGER PAD duty task
        byp = PreopPatient.objects.create(
            patient_name='ვოიაჯერ პალატაძე', age=66, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire', 'side': 'R', 'abf_rest_pain': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage2', 'post_antithrombotic_plan': 'voyager',
        })
        self.client.post(reverse('preop-admit-board', args=[byp.pk]), {'bed': '12'})
        bp_ward = BoardPatient.objects.get(name='ვოიაჯერ პალატაძე')
        self.assertEqual(bp_ward.unit, BoardPatient.WARD)
        ward_tasks = ' | '.join(BoardEntry.objects.filter(patient=bp_ward, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertIn('VOYAGER PAD', ward_tasks)

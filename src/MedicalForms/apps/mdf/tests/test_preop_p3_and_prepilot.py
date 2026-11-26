"""Tests for Pre-op Stage P3 continuity hardening and pre-pilot simulation integration."""

from django.test import Client
from django.urls import reverse

from .helpers import PrivateMediaTest


class PreopStageP3ReferenceHardeningTests(PrivateMediaTest):
    """Stage P3 Reference-Grade (Level 5/5) tests: pharmacology/neuraxial safety, target-limb hemodynamics, hard contraindications, and full-cycle continuity."""

    def test_p3_1_pharmacology_neuraxial_epidural_abw_crcl_insulin_metformin_and_voyager_cea_guards(self):
        import datetime

        from ..models import PreopPatient
        from ..preop import calc_crcl, evaluate

        # 1. Devine Adjusted Body Weight (ABW) CrCl prevents renal DOAC overdose in morbid obesity (BMI >= 30)
        crcl_abw = calc_crcl(130, 74, sex='F', weight_kg=130, height_cm=160)
        self.assertEqual(crcl_abw, 44)
        # Raw Cockcroft-Gault with 130kg would falsely overestimate ~69 mL/min (>50); Devine ABW brings it to 44 (<50 renal threshold)!
        self.assertLess(crcl_abw, 50)

        # 2. Open AAA with 'epidural' chip in anesth_lines_chips (while anesthesia_plan='general') triggers ASRA 72h DOAC hold & DoS INR <= 1.4!
        p_open = PreopPatient.objects.create(
            patient_name='ეპიდურალ უსაფრთხოძე', age=72, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            questionnaire={
                'filled': True, 'dm': True, 'dm_insulin': True, 'metformin': True,
                'med_doac': True, 'med_chips': ['amlodipine', 'amiodarone'],
                'cad': True, 'cad_chips': ['hf'],
                'med_chips_extra': [],
            },
            studies={
                'anesthesia_plan': 'general',
                'anesth_lines_chips': ['art_line', 'epidural'],
                'planned_surgery_date': '2026-12-10',
                'creat': 85, 'weight_kg': 82, 'height_cm': 174,
                'dos_inr': 1.46,
            },
        )
        p_open.questionnaire['med_chips'] = ['ccb', 'amiodarone', 'acei_arb']
        p_open.save()

        ev_open_norm = evaluate(p_open, today=datetime.date(2026, 12, 1))
        self.assertTrue(ev_open_norm['has_neuraxial_plan'])
        self.assertIn('72 საათით', {m['drug']: m for m in ev_open_norm['med_schedule']}['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        # Escalate to severe CKD (creat=240 -> eGFR < 30 / CrCl < 30): DOAC becomes >=96h & Metformin becomes 'red'
        p_open.studies['creat'] = 240
        p_open.save()
        ev_open = evaluate(p_open, today=datetime.date(2026, 12, 1))
        self.assertTrue(ev_open['has_neuraxial_plan'])
        self.assertTrue(ev_open['dos_hold_danger'])
        self.assertTrue(any('1.4' in w for w in ev_open['dos_warnings']))
        sched_open = {m['drug']: m for m in ev_open['med_schedule']}
        self.assertIn('≥96 სთ', sched_open['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertIn('ინსულინი', sched_open)
        self.assertIn('75–80%', sched_open['ინსულინი']['rule'])
        self.assertIn('მეტფორმინი', sched_open)
        self.assertEqual(sched_open['მეტფორმინი']['tone'], 'red')  # eGFR < 30 -> stop immediately!
        self.assertIn('ამლოდიპინი / CCB', sched_open)
        self.assertIn('SBP ≥ 110', sched_open['ამლოდიპინი / CCB']['rule'])
        self.assertIn('ამიოდარონი', sched_open)
        self.assertEqual(sched_open['ამიოდარონი']['tone'], 'ok')
        # HFrEF ('hf' chip) keeps ACEi/ARB instead of blind 24h hold!
        self.assertIn('HFrEF', sched_open['ACEi / ARB (პრილი/სარტანი)']['rule'])

        # 3. CEA on OAC alone (no aspirin/P2Y12) triggers needs_cea_antiplatelet=True, UNLESS brain CT shows hemorrhage!
        p_cea = PreopPatient.objects.create(
            patient_name='კაროტიდ ანტითრომბაძე', age=69, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'cea_symp_event': True, 'med_doac': True},
            studies={'carotid_r': 80, 'brain_imaging': 'normal_small'},
        )
        ev_cea = evaluate(p_cea)
        self.assertTrue(ev_cea['needs_cea_antiplatelet'])

        p_cea.studies['brain_imaging'] = 'large_or_hemorrhage'
        p_cea.save()
        ev_cea_hem = evaluate(p_cea)
        self.assertFalse(ev_cea_hem['needs_cea_antiplatelet'])
        self.assertTrue(ev_cea_hem['has_hard_contraindication'])

        # 4. VOYAGER PAD blocked on dialysis / prior stroke, and manual VOYAGER + full OAC raises triple-therapy Red Flag!
        p_pad = PreopPatient.objects.create(
            patient_name='ვოიაჯერ გვარდაძე', age=67, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            questionnaire={'filled': True, 'on_dialysis': True, 'neuro': True},
            studies={'abi_r': 0.35, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate'},
        )
        ev_pad = evaluate(p_pad)
        self.assertNotIn('VOYAGER', ev_pad['post_antithrombotic_label'])

        p_pad.questionnaire = {'filled': True, 'med_doac': True}
        p_pad.studies['post_antithrombotic_plan'] = 'voyager'
        p_pad.save()
        ev_pad_oac = evaluate(p_pad)
        self.assertTrue(any('VOYAGER' in rf for rf in ev_pad_oac['red_flags']))

    def test_p3_2_target_limb_tbi_venous_c6_hard_contraindications_nan_safety_and_self_fill_112(self):
        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, evaluate, make_self_fill_token

        # 1. Right-sided EVLA with non-compressible right ABI (1.55) + normal left TP (78) MUST NOT clear tbi_measured or compression_ready!
        p_ven = PreopPatient.objects.create(
            patient_name='ტარგეტ კიდურაძე', age=42, sex='F', side='R',
            pathway='venous', op_subtype='evla', surgeon=self.user,
            questionnaire={'filled': True, 'ven_ceap': 'C6'},
            studies={
                'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 8.0,
                'hb': 132, 'plt': 240, 'weight_kg': 60,
                'abi_r': 1.55, 'abi_l': 1.05, 'tp_l': 78,
                'compression_ready': True,
            },
        )
        ev_ven = evaluate(p_ven)
        # C6 venous ulcer in a 42yo non-diabetic MUST still trigger mandatory ABI screening!
        self.assertTrue(ev_ven['needs_abi_check'])
        self.assertFalse(ev_ven['tbi_measured'])
        self.assertFalse(ev_ven['tbi_comp_safe'])
        comp_task = next(t for t in ev_ven['tasks'] if t['key'] == 'comp')
        self.assertFalse(comp_task['done'])
        # EVLA max tumescent lidocaine with epinephrine for 60kg = 420 mg (min(500, 60*7))
        self.assertEqual(ev_ven['lidocaine_max_mg'], 420)

        # Entering ipsilateral tp_r=72 clears tbi_measured and compression_ready!
        p_ven.studies['tp_r'] = 72
        p_ven.save()
        ev_ven_ok = evaluate(p_ven)
        self.assertTrue(ev_ven_ok['tbi_measured'])
        self.assertTrue(ev_ven_ok['tbi_comp_safe'])
        self.assertTrue(next(t for t in ev_ven_ok['tasks'] if t['key'] == 'comp')['done'])

        # 2. Hard contraindications (100% ICA occlusion & acute_dvt) block STATUS_READY, dos_ready, and surgeon approve!
        p_occ = PreopPatient.objects.create(
            patient_name='ოკლუზია ბლოკაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'cea_symp_event': True, 'med_aspirin': True},
            studies={
                'carotid_r': 100, 'carotid_l': 30, 'cea_cta_status': 'concordant',
                'brain_imaging': 'normal_small', 'cta_disc': True,
                'hb': 140, 'plt': 220, 'inr': 1.0, 'creat': 85, 'glucose': 5.4, 'k': 4.2,
                'lvef': 58, 'ecg': 'normal', 'neuro_consult_done': True, 'anesth_done': True,
                'icu_bed_booked': True,
                'dos_identity_consent': True, 'dos_site_marked': True, 'dos_fasting_confirmed': True,
                'dos_med_hold_verified': True, 'dos_abx_ready': True, 'dos_blood_implant_ready': True,
            },
        )
        p_occ.cardio_decision = 'cleared'
        p_occ.save()
        ev_occ = evaluate(p_occ)
        self.assertTrue(ev_occ['has_hard_contraindication'])
        self.assertEqual(ev_occ['status'], STATUS_ALERT)
        self.assertFalse(ev_occ['dos_ready'])

        # Posting approve MUST be rejected on hard contraindication!
        self.client.post(reverse('preop-patient', args=[p_occ.pk]), {
            'section': 'signoff', 'action': 'approve', 'anamnesis': 'ტესტი',
        })
        p_occ.refresh_from_db()
        self.assertIsNone(p_occ.approved_at)

        # 3. NaN / Inf / negative-zero fuzz inputs on Stage 1 NEVER raise HTTP 500!
        resp_nan = self.client.post(reverse('preop-patient', args=[p_ven.pk]), {
            'section': 'stage1',
            'hb': 'nan', 'creat': 'inf', 'k': '-inf', 'abi_r': 'NaN', 'ven_gsv_mm': 'Infinity',
        })
        self.assertIn(resp_nan.status_code, (200, 302))
        p_ven.refresh_from_db()
        self.assertIsNone(p_ven.studies.get('hb'))

        # 4. Self-Fill saves active_infection across pathways, saves dm_insulin additively, renders 112 banner on aaa_pain, and locks post-op!
        p_sf = PreopPatient.objects.create(
            patient_name='თვითშევსება სასწრაფოძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='open',
        )
        tok = make_self_fill_token(p_sf)
        anon = Client()
        r_post = anon.post(reverse('preop-self-fill', args=[tok]), {
            'age': '71', 'sex': 'M', 'aaa_pain': '1', 'active_infection': '1', 'dm_insulin': '1',
        }, follow=True)
        self.assertEqual(r_post.status_code, 200)
        self.assertContains(r_post, '112')
        p_sf.refresh_from_db()
        self.assertTrue(p_sf.questionnaire.get('aaa_pain'))
        self.assertTrue(p_sf.questionnaire.get('active_infection'))
        self.assertTrue(p_sf.questionnaire.get('dm_insulin'))

        # Operated patient locks Self-Fill!
        p_sf.questionnaire['nurse_verified'] = False
        p_sf.studies = {'case_outcome_status': 'operated'}
        p_sf.save()
        r_locked = anon.post(reverse('preop-self-fill', args=[tok]), {'aaa_pain': '0'})
        self.assertIn('locked=1', r_locked['Location'])

    def test_p3_3a_case_plan_same_name_board_isolation_and_archive_prefill(self):
        from ..models import BoardPatient, Case, PreopPatient
        from ..preop import detect_case_preop, resolve_preop_for_board

        # 1. Conference Case routing: "PLAN: ენდოვასკულური" -> PAD_PTA; "საჩინო ვენით შუნტირება" -> PAD_BYPASS (not VENOUS!)
        c_endo = Case.objects.create(
            patient_name='ენდო გეგმაძე', diagnosis='მარჯვენა SFA ოკლუზია, კრიტიკული იშემია',
            history='გეგმა (PLAN): ენდოვასკულური რევასკულარიზაცია (PTA)', created_by=self.user,
        )
        self.assertEqual(detect_case_preop(c_endo), ('pta_clti', 'R'))

        c_byp = Case.objects.create(
            patient_name='აუტოვენა შუნტაძე', diagnosis='მარცხენა ფემორო-პოპლიტეალური შუნტირება დიდი საჩინო ვენით',
            created_by=self.user,
        )
        self.assertEqual(detect_case_preop(c_byp), ('bypass_claud', 'L'))

        # 2. Two distinct patients named "გიორგი ბერიძე" NEVER merge or hijack each other's BoardPatient cards!
        g1 = PreopPatient.objects.create(
            patient_name='გიორგი ბერიძე', age=74, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'htn': True},
            studies={'person_id': '01001001001', 'birth_date': '1952-03-10', 'blood_group': 'O (I) Rh (+)'},
        )
        g2 = PreopPatient.objects.create(
            patient_name='გიორგი ბერიძე', age=45, sex='M', side='L',
            pathway='venous', op_subtype='phlebectomy', surgeon=self.user,
            questionnaire={'filled': True},
            studies={'person_id': '01002002002', 'birth_date': '1981-07-22', 'blood_group': 'B (III) Rh (-)'},
        )
        self.client.post(reverse('preop-admit-board', args=[g1.pk]), {'unit': 'icu', 'bed': '1'})
        self.client.post(reverse('preop-admit-board', args=[g2.pk]), {'unit': 'ward', 'bed': '8'})
        g1.refresh_from_db()
        g2.refresh_from_db()
        bp1_id, bp2_id = g1.studies.get('board_patient_id'), g2.studies.get('board_patient_id')
        self.assertIsNotNone(bp1_id)
        self.assertIsNotNone(bp2_id)
        self.assertNotEqual(bp1_id, bp2_id)
        bp1 = BoardPatient.objects.get(pk=bp1_id)
        self.assertIn('CEA', bp1.diagnosis)

        # 3. Archiving PreopPatient preserves resolve_preop_for_board + ?board=<pk> NIV & BloodOrder prefill + intact niv.neuro=True!
        g1.archived = True
        g1.save()
        self.assertEqual(resolve_preop_for_board(bp1).pk, g1.pk)

        niv_from_board = self.client.get(reverse('niv-cover') + f'?board={bp1.pk}')
        self.assertEqual(niv_from_board.status_code, 200)
        niv_g1 = niv_from_board.context['niv']
        self.assertEqual(niv_g1.person_id, '01001001001')
        self.assertTrue(niv_g1.neuro)

        blood_from_board = self.client.get(reverse('blood-order') + f'?board={bp1.pk}')
        self.assertEqual(blood_from_board.status_code, 200)
        self.assertEqual(blood_from_board.context['form'].initial.get('person_id'), '01001001001')
        self.assertEqual(blood_from_board.context['form'].initial.get('blood_group'), 'O (I) Rh (+)')

    def test_p3_3b_hold_pause_operated_week_decongestion_and_sticky_chips(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import prep_days_elapsed

        today = timezone.localdate()

        # 4. HOLD pause subtracts deferred days; operated freezes clock & auto-decongests active week.html (?outcome=done shows it)!
        p_clock = PreopPatient.objects.create(
            patient_name='საათ პაუზაძე', age=66, sex='M',
            pathway='abf', op_subtype='abf_claud', surgeon=self.user,
            visit_date=today, started_on=today - datetime.timedelta(days=10),
            studies={'hold_paused_days': 6},
        )
        self.assertEqual(prep_days_elapsed(p_clock, today=today), 5)

        self.client.post(reverse('preop-patient', args=[p_clock.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1',
            'case_outcome_status': 'operated',
            'operated_date': (today - datetime.timedelta(days=2)).isoformat(),
        })
        p_clock.refresh_from_db()
        self.assertNotContains(self.client.get(reverse('preop') + f'?week={today.isoformat()}'), 'საათ პაუზაძე')
        self.assertContains(self.client.get(reverse('preop') + f'?week={today.isoformat()}&outcome=done'), 'საათ პაუზაძე')

        # 5. Sticky chip fix, non-frozen Day-1 ack_alerts anamnesis, side-change site-mark reset, and 0.0 ABI passport rendering!
        p_ui = PreopPatient.objects.create(
            patient_name='ჩიპ სისუფთავეძე', age=64, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            questionnaire={'filled': True, 'abf_rest_pain': True},
            studies={'abi_r': 0.0, 'tp_r': 0, 'dos_site_marked': True},
        )
        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'stage2', 'nurse_note_chips': ['waiting_labs'], 'nurse_note_custom': '',
        })
        p_ui.refresh_from_db()
        self.assertIn('ანალიზის პასუხს', p_ui.studies.get('nurse_note', ''))

        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'stage2', 'nurse_note_custom': '',
        })
        p_ui.refresh_from_db()
        self.assertEqual(p_ui.studies.get('nurse_note', ''), '')

        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'signoff', 'action': 'ack_alerts', 'anamnesis': 'დღე 1 ტექსტი',
        })
        p_ui.refresh_from_db()
        self.assertEqual(p_ui.anamnesis, '')

        pp_resp = self.client.get(reverse('preop-passport', args=[p_ui.pk]))
        self.assertEqual(pp_resp.status_code, 200)
        self.assertContains(pp_resp, 'ABI 0.0/')
        self.assertContains(pp_resp, 'TP 0/')
        self.assertContains(pp_resp, 'TP min 0 mmHg')

        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'header', 'patient_name': 'ჩიპ სისუფთავეძე', 'sex': 'M',
            'op_subtype': 'bypass_clti', 'side': 'L',
        })
        p_ui.refresh_from_db()
        self.assertFalse(p_ui.studies.get('dos_site_marked'))


class PreopPrePilotRefinementAndSimulationTests(PrivateMediaTest):
    """Pre-Pilot Phase 1-4 tests: Nurse UX, 2-Page Specialist Dossier, Risk Indices & Simulation Objectivity."""

    def test_pre_pilot_1_nurse_ergonomics_jump_chips_self_fill_reconciliation_and_pre_q_alerts(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_ALERT, STATUS_NO_Q, evaluate, make_self_fill_token, merged_q

        today = timezone.localdate()

        # 1. Pre-Questionnaire Critical Alert Visibility: lab/duplex red flag BEFORE questionnaire MUST trigger STATUS_ALERT!
        p_pre = PreopPatient.objects.create(
            patient_name='ლაბ ადრეულაძე', age=68, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.assertEqual(evaluate(p_pre, today=today)['status'], STATUS_NO_Q)
        self.assertEqual(evaluate(p_pre, today=today)['next_step_hint'], 'აკლია: კითხვარი')

        # Week calendar renders nurse next_step_hint directly on active patient row
        week_resp = self.client.get(reverse('preop') + f'?week={today.isoformat()}')
        self.assertContains(week_resp, 'აკლია: კითხვარი')

        # Entering critical hyperkalemia (K+ 6.1) before Tab 1 immediately promotes status to STATUS_ALERT!
        self.client.post(reverse('preop-patient', args=[p_pre.pk]), {
            'section': 'stage1', 'k': '6.1',
        })
        p_pre.refresh_from_db()
        self.assertEqual(evaluate(p_pre, today=today)['status'], STATUS_ALERT)

        # 2. Patient Self-Fill -> Positive-Finding Summary Card + 1-Tap Nurse Verification + Guided Jump Chips
        p_sf = PreopPatient.objects.create(
            patient_name='ბმულ გადამოწმებაძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        tok = make_self_fill_token(p_sf)
        anon = Client()
        anon.post(reverse('preop-self-fill', args=[tok]), {
            'mets_ok': '1', 'htn': '1', 'dm': '1', 'dm_insulin': '1',
            'med_doac': '1', 'glp1': '1', 'allergy_iodine': '1', 'person_id': '01000000003',
        })
        p_sf.refresh_from_db()
        ev_sf = evaluate(p_sf, today=today)
        self.assertTrue(len(ev_sf['self_fill_highlights']) >= 4)
        self.assertTrue(any(m['field'] == 'btn-verify-self' for m in ev_sf['missing_items']))

        card_resp = self.client.get(reverse('preop-patient', args=[p_sf.pk]))
        for marker in ('po-sf-summary', 'btn-verify-self', 'po-jump-chip', 'po-sticky-save', 'po-edit-drawer'):
            self.assertContains(card_resp, marker)

        # Nurse clicks 1-tap verify & confirm on Tab 1
        self.client.post(reverse('preop-patient', args=[p_sf.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'htn': '1', 'dm': '1', 'dm_insulin': '1',
            'med_doac': '1', 'glp1': '1', 'allergy_iodine': '1',
        })
        p_sf.refresh_from_db()
        self.assertTrue(merged_q(p_sf)['nurse_verified'])
        ev_after = evaluate(p_sf, today=today)
        self.assertFalse(any(m['field'] == 'btn-verify-self' for m in ev_after['missing_items']))
        self.assertIn('hb', ev_after['s1_required_fields'])
        self.assertIn('aaa_mm', ev_after['s1_required_fields'])

    def test_pre_pilot_2a_academic_two_page_dossier_and_risk_indices(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()

        # 1. Lone Controlled AFib on EVAR -> ESC 2022 Table 5 non-inflation: RCRI = 0/6, VSG-CRI IHD/CHF = 0!
        p_af = PreopPatient.objects.create(
            patient_name='რიტმ ობიექტურაძე', age=72, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            cardio_decision='cleared', visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'cad': True, 'cad_chips': ['afib'],
                'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True,
            },
            studies={
                'aaa_mm': 58, 'aaa_neck_status': 'friendly_ifu', 'popliteal_screen_status': 'normal',
                'cta_disc': True, 'hb': 140, 'plt': 225, 'creat': 88, 'creat_unit': 'umol',
                'inr': 1.05, 'lvef': 58, 'nt_probnp': 110, 'ecg': 'afib_controlled',
            },
        )
        ev_af = evaluate(p_af, today=today)
        self.assertEqual(ev_af['rcri']['score'], 0)
        self.assertEqual(ev_af['vsg_cri']['score'], 3)

        # 2. High-acuity Open AAA -> ASA IV, RCRI 5, ARISCAT High, Cefazolin 3g, 2-Page A4 Dossier
        p_open = PreopPatient.objects.create(
            patient_name='აკადემიურ დოსიეძე', age=76, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'cad': True, 'cad_chips': ['mi', 'cabg'],
                'dm': True, 'dm_insulin': True, 'neuro': True,
                'med_aspirin': True, 'med_chips': ['statin', 'beta_blocker'], 'mets_ok': True,
            },
            studies={
                'weight_kg': 132, 'height_cm': 174, 'aaa_mm': 64, 'cta_disc': True,
                'bp_sys': 148, 'bp_dia': 84, 'bp_sys_l': 122, 'bp_dia_l': 76, 'spo2': 89,
                'hb': 96, 'plt': 185, 'creat': 2.3, 'creat_unit': 'mgdl', 'inr': 1.06,
                'albumin': 31, 'lvef': 46, 'ecg': 'normal', 'blood_group': 'A (II) Rh (+)',
            },
        )
        ev_op = evaluate(p_open, today=today)
        self.assertEqual(ev_op['auto_asa'], 'IV')
        self.assertEqual(ev_op['rcri']['score'], 5)
        self.assertEqual(ev_op['rcri']['tier'], 'high')
        self.assertEqual(ev_op['ariscat']['tier'], 'high')
        self.assertIn('ცეფაზოლინი 3 გ IV', ev_op['surgical_brief']['abx_regimen'])
        self.assertIn('მარჯვენა მკლავზე', ev_op['anesthetic_brief']['bp_corridor'])
        self.assertIn('Hb ≥ 80–90 გ/ლ', ev_op['anesthetic_brief']['transfusion_pbm'])

        pp = self.client.get(reverse('preop-passport', args=[p_open.pk]))
        self.assertEqual(pp.status_code, 200)
        for token in (
            'pp-page-1', 'pp-page-2', 'გვერდი 1 / 2', 'გვერდი 2 / 2',
            'RCRI (Lee / ESC)', 'VSG-CRI (SVS VQI)', 'ARISCAT (ფილტვი)',
            '2A. ქირურგიული და ანატომიური გეგმა', '2B. ანესთეზიოლოგიური და ჰემოდინამიკური გეგმა',
            'ცეფაზოლინი 3 გ IV',
        ):
            self.assertContains(pp, token)

    def test_pre_pilot_2b_neuraxial_p2y12_ckd_gentamicin_and_cea_thrombolysis_briefings(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()

        # 3. ASRA 5th Ed Uninterrupted-P2Y12 Neuraxial Barrier + Severe CKD Beta-Lactam Gentamicin Guard + CEA Thrombolysis Heparin Guard
        p_nx = PreopPatient.objects.create(
            patient_name='სპინალ დაცვაძე', age=67, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'abf_rest_pain': True, 'recent_stent': True,
                'med_aspirin': True, 'med_clopidogrel': True,
                'allergy_other': True, 'allergy_chips': ['beta_lactam'], 'mets_ok': True,
            },
            studies={
                'abi_r': 0.38, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate',
                'hb': 128, 'plt': 215, 'inr': 1.02, 'creat': 260, 'creat_unit': 'umol',
                'ecg': 'normal', 'anesthesia_plan': 'neuraxial', 'med_hold_agreed': True,
            },
        )
        ev_nx = evaluate(p_nx, today=today)
        self.assertIn('უკუნაჩვენებია', ev_nx['anesthetic_brief']['neuraxial_gate'])
        self.assertTrue(any('სპინალური ჰემატომის' in rf for rf in ev_nx['red_flags']))
        self.assertIn('გენტამიცინს მოერიდეთ', ev_nx['surgical_brief']['abx_regimen'])

        p_cea = PreopPatient.objects.create(
            patient_name='თრომბოლიზ კაროტიდაძე', age=69, sex='F', side='L',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'cea_symp_event': True,
                'cea_event_date': (today - datetime.timedelta(days=3)).isoformat(),
                'cea_thrombolysis': True, 'mets_ok': True,
            },
            studies={'carotid_r': 35, 'carotid_l': 85, 'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small'},
        )
        ev_cea = evaluate(p_cea, today=today)
        self.assertFalse(ev_cea['needs_cea_antiplatelet'])
        self.assertIn('ჰემორაგიული ტრანსფორმაციის', ev_cea['surgical_brief']['heparin_plan'])
        self.assertIn('SBP < 140–150 mmHg', ev_cea['anesthetic_brief']['bp_corridor'])

    def test_pre_pilot_3_simulation_invariants_and_multi_pathway_e2e_lifecycle(self):
        import datetime

        from django.utils import timezone

        from ..models import BoardPatient, Case, PreopPatient
        from ..preop import STATUS_READY, evaluate
        from .sim import run_simulation

        # 1. Fast CI Regression Slice of the Synthetic Simulation Engine (1,500 scenarios + 250 metamorphic pairs = 2,000 evaluations)
        sim_report = run_simulation(n=1500, n_mono=250, passport_sample=100, seed=2026)
        self.assertEqual(sim_report['exceptions_count'], 0)
        self.assertEqual(sim_report['invariant_violations_total'], 0)
        self.assertEqual(sim_report['metamorphic_violations'], {})
        for oracle_name, metrics in sim_report['guideline_oracles'].items():
            self.assertEqual(metrics['fn'], 0, msg=f'Missed positive in {oracle_name}: {metrics}')
            self.assertEqual(metrics['fp'], 0, msg=f'False positive in {oracle_name}: {metrics}')

        # 2. Full End-to-End HTTP/DB Lifecycle across Conference -> Preop -> Passport -> DoS -> Board -> Discharge -> VQI Audit
        today = timezone.localdate()
        case = Case.objects.create(
            patient_name='სრული ციკლაძე',
            age=68,
            sex='M',
            side='R',
            diagnosis='მარჯვენა შიგნითა საძილე არტერიის სიმპტომური სტენოზი 85%',
            proposal='მარჯვენა კაროტიდული ენდარტერექტომია (CEA)',
            created_by=self.user,
        )
        r_create = self.client.get(reverse('preop-new') + f'?case={case.pk}')
        p = PreopPatient.objects.get(case=case)
        self.assertEqual(r_create.status_code, 302)
        self.assertEqual((p.op_subtype, p.side), ('cea_symp', 'R'))

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'header', 'patient_name': p.patient_name, 'age': '68', 'sex': 'M',
            'op_subtype': 'cea_symp', 'side': 'R', 'person_id': '01000000004',
            'birth_date': '1958-05-10', 'slot_kind': 'dated', 'visit_date': today.isoformat(),
            'planned_surgery_date': (today + datetime.timedelta(days=2)).isoformat(),
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cta_disc': '1',
            'cea_symp_event': '1', 'cea_event_date': (today - datetime.timedelta(days=4)).isoformat(),
            'cea_event_chips': ['tia'], 'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1', 'bp_sys': '136', 'bp_dia': '80', 'hr': '72', 'spo2': '98',
            'hb': '142', 'plt': '230', 'inr': '1.02', 'creat': '84', 'creat_unit': 'umol',
            'glucose': '5.4', 'carotid_r': '85', 'carotid_l': '30',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'cta_disc': '1', 'ecg': 'normal', 'blood_group': 'O (I) Rh (+)',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'neuro_consult_done': '1', 'decolonization_ready': '1', 'anesth_done': '1',
        })
        p.refresh_from_db()
        ev_ready = evaluate(p, today=today)
        self.assertEqual(ev_ready['status'], STATUS_READY)
        self.assertEqual(ev_ready['clearance_seal']['code'], 'go')

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'signoff', 'action': 'approve',
        })
        pp_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertContains(pp_resp, 'GO — უსაფრთხოდ მზადაა ოპერაციისთვის')

        # Morning of surgery WHO Sign-In + Complete & Transfer to ICU Board
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1', 'action': 'complete_and_board',
            'unit': 'icu', 'bed': '2',
            'dos_bp_sys': '134', 'dos_bp_dia': '78', 'dos_glucose': '5.6', 'dos_inr': '1.02',
            'dos_identity_consent': '1', 'dos_site_marked': '1', 'dos_fasting_confirmed': '1',
            'dos_med_hold_verified': '1', 'dos_abx_ready': '1', 'dos_blood_implant_ready': '1',
            'icu_bed_booked': '1',
        })
        p.refresh_from_db()
        bp = BoardPatient.objects.get(pk=p.studies['board_patient_id'])
        bp.discharged_at = timezone.now()
        bp.save(update_fields=['discharged_at'])

        # Record 30-day VQI discharge bundle & uneventful 30d outcome, then verify Audit KPIs
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1', 'action': 'save_dos',
            'case_outcome_status': 'operated', 'operated_date': today.isoformat(),
            'vqi_discharge_antiplatelet': '1', 'vqi_discharge_statin': '1',
            'vqi_surveillance_scheduled': '1', 'vqi_30d_status': 'uneventful',
        })
        audit_resp = self.client.get(reverse('preop-audit'))
        self.assertEqual(audit_resp.status_code, 200)
        self.assertEqual(audit_resp.context['vqi_statin_ap_pct'], 100)
        self.assertEqual(audit_resp.context['cea_symp_within_14d_pct'], 100)
        self.assertEqual(audit_resp.context['vqi_uneventful_30d_pct'], 100)

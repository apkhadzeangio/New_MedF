"""Tests for Pre-op Stage P2 identifiers, calendar med schedule, anesthesia DoS, and VQI."""

from django.test import Client
from django.urls import reverse

from .helpers import PrivateMediaTest


class PreopStageP2AnesthDosAndVqiTests(PrivateMediaTest):
    """Stage P2 tests: Structured Anesthesiology, Exact Calendar Med-Hold, DoS WHO Sign-In Gate & 30-Day VQI."""

    def _setup_p2_calendar_patient(self):
        import datetime

        from ..models import PreopPatient
        from ..preop import merged_studies

        p = PreopPatient.objects.create(
            patient_name='პასპორტ კალენდარაძე', sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            visit_date=datetime.date(2026, 12, 1), started_on=datetime.date(2026, 12, 1),
        )
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'header', 'patient_name': 'პასპორტ კალენდარაძე', 'sex': 'M',
            'op_subtype': 'evar', 'person_id': '010-00-000-001-99', 'birth_date': '1958-04-15',
            'age': '', 'slot_kind': 'dated', 'visit_date': '2026-12-01',
            'planned_surgery_date': '2026-12-10', 'phone': '599778899',
        })
        p.refresh_from_db()
        s = merged_studies(p)
        self.assertEqual(s['person_id'], '01000000001')
        self.assertEqual(s['birth_date'], '1958-04-15')
        self.assertIsNotNone(p.age)
        self.assertGreaterEqual(p.age, 68)

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'mets_ok': '1', 'htn': '1', 'dm': '1',
            'med_aspirin': '1', 'med_clopidogrel': '1', 'med_doac': '1',
            'med_warfarin': '1', 'sglt2': '1', 'glp1': '1',
            'med_chips': ['statin', 'acei_arb'], 'weight_kg': '84', 'height_cm': '175',
            'aaa_mm': '58', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1', 'weight_kg': '84', 'height_cm': '175',
            'aaa_mm': '58', 'cta_months': '1', 'cta_disc': '1',
            'hb': '136', 'plt': '210', 'inr': '1.05', 'creat': '82',
            'nt_probnp': '88', 'blood_group': 'A (II) Rh (+)', 'ecg': 'normal',
        })
        p.refresh_from_db()
        return p

    def test_p2_1_identifiers_dob_auto_age_ecosystem_prefills_and_exact_calendar_med_schedule(self):
        import datetime

        from ..models import PreopPatient
        from ..preop import evaluate, make_self_fill_token, merged_studies

        p = self._setup_p2_calendar_patient()
        ev = evaluate(p, today=datetime.date(2026, 12, 1))
        sched = {m['drug']: m for m in ev['med_schedule']}
        self.assertIn('10.12', sched['ასპირინი (75–100 მგ)']['date_plan'])
        self.assertIn('05.12-დან', sched['კლოპიდოგრელი / P2Y12']['date_plan'])
        self.assertIn('08.12-დან', sched['DOAC (ქსარელტო / ელიქვისი)']['date_plan'])
        self.assertIn('05.12-დან', sched['ვარფარინი']['date_plan'])
        self.assertIn('INR კონტროლი 09.12', sched['ვარფარინი']['date_plan'])
        self.assertIn('07.12-დან', sched['SGLT2 (ჯარდიანსი / ფორსიგა)']['date_plan'])
        self.assertIn('03.12-დან', sched['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['date_plan'])
        self.assertIn('09.12 თხევადი დიეტა', sched['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['date_plan'])

        # Switching anesthesia_plan to neuraxial shifts DOAC hold to 72h (07.12) and Clopidogrel to 7d (03.12)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'anesthesia_plan': 'neuraxial', 'planned_surgery_date': '2026-12-10',
        })
        p.refresh_from_db()
        sched_neur = {m['drug']: m for m in evaluate(p, today=datetime.date(2026, 12, 1))['med_schedule']}
        self.assertIn('07.12-დან', sched_neur['DOAC (ქსარელტო / ელიქვისი)']['date_plan'])
        self.assertIn('03.12-დან', sched_neur['კლოპიდოგრელი / P2Y12']['date_plan'])

        # Ecosystem prefills: NIV-300-9/a and Blood Order receive person_id, dob (15.04.1958), bmi_score, blood_group
        niv_resp = self.client.get(reverse('niv-cover') + f'?preop={p.pk}')
        self.assertEqual(niv_resp.status_code, 200)
        niv_obj = niv_resp.context['niv']
        self.assertEqual((niv_obj.person_id, niv_obj.dob, niv_obj.bmi_score), ('01000000001', '15.04.1958', '27.4'))

        blood_resp = self.client.get(reverse('blood-order') + f'?preop={p.pk}')
        self.assertEqual(blood_resp.status_code, 200)
        self.assertEqual(blood_resp.context['form'].initial['person_id'], '01000000001')
        self.assertEqual(blood_resp.context['form'].initial['dob'], '15.04.1958')
        self.assertEqual(blood_resp.context['form'].initial['blood_group'], 'A (II) Rh (+)')

        # Printable A4 Passport renders person_id, DOB, planned surgery date, GLP-1 fasting, calendar dates & EVAR surveillance
        pp_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertEqual(pp_resp.status_code, 200)
        for token in ('01000000001', '15.04.1958', '10.12.2026', 'GLP-1 პროტოკოლი', '07.12-დან', 'ESVS 2024 EVAR მეთვალყურეობა'):
            self.assertContains(pp_resp, token)

        # Self-fill also supports optional person_id + birth_date -> auto-age
        sf_p = PreopPatient.objects.create(
            patient_name='თვით აიდიძე', sex='F', pathway='venous', op_subtype='evla', surgeon=self.user,
        )
        anon = Client()
        anon.post(reverse('preop-self-fill', args=[make_self_fill_token(sf_p)]), {
            'person_id': '01000000002', 'birth_date': '1986-02-10', 'sex': 'F', 'mets_ok': '1',
        })
        sf_p.refresh_from_db()
        self.assertEqual(merged_studies(sf_p)['person_id'], '01000000002')
        self.assertIsNotNone(sf_p.age)
        self.assertIn('მსუბუქი საუზმე დაშვებულია', evaluate(sf_p)['fasting_instruction'])
        sf_p.studies = {'anesthesia_plan': 'sedation'}
        self.assertIn('6 საათით ადრე', evaluate(sf_p)['fasting_instruction'])

    def test_p2_2_structured_anesthesiology_cabinet_asa_mallampati_stop_bang_cied_and_waiting_guard(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import STATUS_READY, evaluate, generate_anamnesis

        today = timezone.localdate()
        evla = PreopPatient.objects.create(
            patient_name='ჯანმრთელ ვენაძე', age=34, sex='F', side='R',
            pathway='venous', op_subtype='evla', surgeon=self.user,
            questionnaire={'filled': True, 'mets_ok': True},
        )
        self.assertEqual(evaluate(evla, today=today)['effective_asa'], 'ASA I')

        cea = PreopPatient.objects.create(
            patient_name='კაროტიდ ანესთეზიაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'cea_symp_event': '1',
            'cea_event_date': (today - datetime.timedelta(days=5)).isoformat(),
            'cea_event_chips': ['tia_limb'],
            'htn': '1', 'pulm': '1', 'pulm_chips': ['osa'],
            'pacemaker': '1', 'med_aspirin': '1', 'med_chips': ['statin'],
            'mets_ok': '1', 'cta_disc': '1',
            'weight_kg': '112', 'height_cm': '174',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1', 'labs_date': today.isoformat(),
            'bp_sys': '132', 'bp_dia': '78', 'hr': '72', 'spo2': '97',
            'weight_kg': '112', 'height_cm': '174',
            'hb': '142', 'plt': '230', 'inr': '1.02', 'creat': '88', 'urine_wbc': '2',
            'carotid_r': '85', 'carotid_l': '30',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'nt_probnp': '95', 'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        # Stage 2: set planned_surgery_date 12 days later (5 + 12 = 17 > 14d NVR target!), Mallampati IV, CIED magnet, lines
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage2',
            'anesth_cabinet_present': '1',
            'planned_surgery_date': (today + datetime.timedelta(days=12)).isoformat(),
            'asa_class': 'auto',
            'asa_emergency': '1',
            'airway_mallampati': 'IV_difficult',
            'stop_bang_risk': 'auto',
            'cied_plan': 'magnet_async',
            'anesth_lines_chips': ['art_line', 'cerebral_oximetry'],
            'neuro_consult_done': '1',
            'spiro_fev1': '78',
            'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
        })
        cea.refresh_from_db()
        ev = evaluate(cea, today=today)
        self.assertEqual(ev['auto_asa'], 'IV')
        self.assertEqual(ev['effective_asa'], 'ASA IV-E')
        self.assertTrue(ev['difficult_airway_alert'])
        self.assertEqual(ev['effective_stop_bang'], 'high')
        self.assertTrue(ev['needs_icu_bed'])
        # CIED plan automatically satisfied cied task, and ICU booking does NOT block Stage 2 STATUS_READY!
        self.assertEqual(ev['status'], STATUS_READY)
        joined_amber = ' | '.join(ev['amber_flags'])
        self.assertIn('Mallampati IV', joined_amber)
        self.assertIn('STOP-Bang', joined_amber)
        self.assertIn('14-დღიან ფანჯარას', joined_amber)

        anam = generate_anamnesis(cea, ev)
        self.assertIn('ASA IV-E', anam)
        self.assertIn('ცერებრული ოქსიმეტრია', anam)
        self.assertIn('Mallampati IV', anam)

    def test_p2_3_dos_who_signin_morning_vitals_stop_gate_and_one_click_complete_to_board_with_reverse_link(self):
        import datetime

        from ..models import BoardEntry, BoardPatient, PreopPatient
        from ..preop import evaluate, merged_studies

        today = datetime.date(2026, 12, 5)
        p = PreopPatient.objects.create(
            patient_name='დილა საოპერაციოძე', age=67, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={'filled': True, 'mets_ok': True, 'med_aspirin': True, 'med_warfarin': True},
            studies={'asa_class': 'IV', 'bp_sys': 130, 'bp_dia': 78, 'hr': 72, 'spo2': 98},
        )
        # 1. Dangerous morning BP (194/112) + high morning INR (1.8) triggers dos_hold_danger even if checks ticked
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'dos_bp_sys': '194', 'dos_bp_dia': '112',
            'dos_glucose': '15.2', 'dos_inr': '1.8',
            'dos_identity_consent': '1', 'dos_site_marked': '1',
            'dos_fasting_confirmed': '1', 'dos_med_hold_verified': '1',
            'dos_abx_ready': '1', 'dos_blood_implant_ready': '1', 'icu_bed_booked': '1',
        })
        p.refresh_from_db()
        ev_bad = evaluate(p, today=today)
        self.assertTrue(ev_bad['dos_hold_danger'])
        self.assertFalse(ev_bad['dos_ready'])
        self.assertEqual(len(ev_bad['dos_warnings']), 3)

        # 2. Stabilized morning vitals + complete_and_board marks operated & transfers ASA + surveillance to Board
        resp = self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'action': 'complete_and_board',
            'unit': 'icu', 'bed': '2',
            'dos_bp_sys': '134', 'dos_bp_dia': '78',
            'dos_glucose': '6.2', 'dos_inr': '1.12',
            'dos_identity_consent': '1', 'dos_site_marked': '1',
            'dos_fasting_confirmed': '1', 'dos_med_hold_verified': '1',
            'dos_abx_ready': '1', 'dos_blood_implant_ready': '1', 'icu_bed_booked': '1',
        })
        bp = BoardPatient.objects.get(name='დილა საოპერაციოძე')
        self.assertRedirects(resp, reverse('board-patient', args=[bp.pk]))
        p.refresh_from_db()
        ev_ok = evaluate(p, today=today)
        self.assertTrue(ev_ok['dos_ready'])
        self.assertEqual(merged_studies(p)['case_outcome_status'], 'operated')
        self.assertTrue(bool(merged_studies(p)['operated_date']))

        st_text = BoardEntry.objects.get(patient=bp, kind=BoardEntry.STATUS).text
        self.assertIn('ASA IV', st_text)
        tasks_joined = ' | '.join(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertIn('ESVS 2023 CEA მეთვალყურეობა', tasks_joined)

        # Reverse link on Board patient page points back to Preop ?tab=dos
        board_page = self.client.get(reverse('board-patient', args=[bp.pk]))
        self.assertContains(board_page, 'წინასაოპერაციო / VQI')
        self.assertContains(board_page, reverse('preop-patient', args=[p.pk]) + '?tab=dos')

    def test_p2_4_cancellation_audit_30d_svs_vqi_registry_and_executive_audit_kpis(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient

        today = timezone.localdate()
        # 1. Operated symptomatic CEA within 8 days (<=14d target), full VQI discharge bundle, uneventful 30d
        p1 = PreopPatient.objects.create(
            patient_name='ვიქიაი წარმატებაძე', age=65, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={
                'filled': True, 'cea_symp_event': True,
                'cea_event_date': (today - datetime.timedelta(days=10)).isoformat(),
            },
        )
        self.client.post(reverse('preop-patient', args=[p1.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'case_outcome_status': 'operated',
            'operated_date': (today - datetime.timedelta(days=2)).isoformat(),
            'vqi_discharge_antiplatelet': '1',
            'vqi_discharge_statin': '1',
            'vqi_smoking_cessation': '1',
            'vqi_surveillance_scheduled': '1',
            'vqi_30d_status': 'uneventful',
        })

        # 2. Operated EVAR with 30-day endoleak complication chip (auto-sets vqi_30d_status='complication')
        p2 = PreopPatient.objects.create(
            patient_name='ენდოლიკ მონიტორიძე', age=73, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            questionnaire={'filled': True},
        )
        self.client.post(reverse('preop-patient', args=[p2.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'case_outcome_status': 'operated',
            'operated_date': today.isoformat(),
            'vqi_complication_chips': ['endoleak'],
        })

        # 3. Cancelled on DoS due to uncontrolled BP & anticoagulation not held
        p3 = PreopPatient.objects.create(
            patient_name='გაუქმება მიზეზაძე', age=70, sex='F',
            pathway='abf', op_subtype='abf_claud', surgeon=self.user,
            questionnaire={'filled': True},
        )
        self.client.post(reverse('preop-patient', args=[p3.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'case_outcome_status': 'cancelled_medical',
            'cancel_reason_chips': ['uncontrolled_bp', 'anticoag_not_held'],
        })

        audit_resp = self.client.get(reverse('preop-audit'))
        self.assertEqual(audit_resp.status_code, 200)
        self.assertEqual(audit_resp.context['operated_total'], 2)
        self.assertEqual(audit_resp.context['cancelled_total'], 1)
        self.assertEqual(audit_resp.context['cancel_rate_pct'], 33.3)
        self.assertEqual(audit_resp.context['vqi_statin_ap_pct'], 50)
        self.assertEqual(audit_resp.context['cea_symp_within_14d_pct'], 100)
        self.assertEqual(audit_resp.context['vqi_uneventful_30d_pct'], 50)
        self.assertEqual(audit_resp.context['vqi_complication_total'], 1)
        self.assertContains(audit_resp, 'SVS VQI / NVR')
        self.assertContains(audit_resp, 'არაკონტროლირებადი ჰიპერტენზია')
        self.assertContains(audit_resp, 'ენდოლიკი')

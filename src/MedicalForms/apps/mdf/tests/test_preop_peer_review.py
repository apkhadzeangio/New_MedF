"""Tests for Pre-op peer-review clinical safety sprints 1-5."""

from django.test import Client
from django.urls import reverse

from .helpers import PrivateMediaTest


class PreopClaudePeerReviewFixesTests(PrivateMediaTest):
    """Regression suite for all 6 peer-review items: Calendar December bug, Self-Fill security, Risk Indices, Med Citations/Passport Gate."""

    def test_review_1_calendar_week_title_all_months_and_december_http_200(self):
        import datetime

        from ..duty import MONTHS
        from ..preop import _monday_of, _week_title

        # 23-29 Nov 2026 MUST say 'ნოემბერი 2026' (NOT 'დეკემბერი 2026')
        self.assertTrue(_week_title(datetime.date(2026, 11, 23)).startswith('ნოემბერი 2026'))
        # 7-13 Dec 2026 MUST say 'დეკემბერი 2026' and NEVER raise IndexError!
        self.assertTrue(_week_title(datetime.date(2026, 12, 7)).startswith('დეკემბერი 2026'))
        # Cross-year week 28 Dec 2026 - 3 Jan 2027 includes both years cleanly
        cross_yr = _week_title(datetime.date(2026, 12, 28))
        self.assertIn('2026', cross_yr)
        self.assertIn('2027', cross_yr)

        # HTTP GET PreopWeekView across all 12 months + every December week returns 200 with exact month name
        for m in range(1, 13):
            mid_day = datetime.date(2026, m, 10)
            mon = _monday_of(mid_day)
            sun = mon + datetime.timedelta(days=6)
            resp = self.client.get(reverse('preop') + f'?week={mid_day.isoformat()}')
            self.assertEqual(resp.status_code, 200, msg=f'Month {m} crashed!')
            if mon.month == sun.month:
                self.assertContains(resp, f'{MONTHS[mon.month - 1]} 2026')

        for dec_monday in ('2026-12-07', '2026-12-14', '2026-12-21', '2026-12-28'):
            resp_dec = self.client.get(reverse('preop') + f'?week={dec_monday}')
            self.assertEqual(resp_dec.status_code, 200)

    def test_review_2_self_fill_pii_masking_one_time_lock_revocable_nonce_and_privacy_headers(self):
        from ..models import PreopPatient
        from ..preop import make_self_fill_token, merged_q, merged_studies

        p = PreopPatient.objects.create(
            patient_name='დაცულ ბმულაძე', age=64, sex='M', phone='599000009',
            pathway='venous', op_subtype='evla', surgeon=self.user,
            studies={'person_id': '01000000009', 'birth_date': '1962-03-14'},
        )
        tok1 = make_self_fill_token(p)
        url1 = reverse('preop-self-fill', args=[tok1])
        anon = Client()

        # 1. Public GET masks person_id & phone, hides birth_date, and emits strict noindex / no-referrer headers
        r_get = anon.get(url1)
        self.assertEqual(r_get.status_code, 200)
        self.assertEqual(r_get['Referrer-Policy'], 'no-referrer')
        self.assertIn('noindex', r_get['X-Robots-Tag'])
        self.assertIn('no-store', r_get['Cache-Control'])
        self.assertContains(r_get, 'noindex, nofollow')
        self.assertContains(r_get, '010•••••009')
        self.assertNotContains(r_get, '01000000009')
        self.assertNotContains(r_get, '599000009')
        self.assertNotContains(r_get, '1962-03-14')

        # 2. First POST succeeds, does NOT overwrite existing clinic PII, and immediately locks the one-time link!
        r_post1 = anon.post(url1, {
            'person_id': '01000000999', 'phone': '555000000', 'birth_date': '1999-01-01',
            'htn': '1', 'mets_ok': '1',
        }, follow=True)
        self.assertEqual(r_post1.status_code, 200)
        self.assertContains(r_post1, 'კითხვარი შენახულია')
        p.refresh_from_db()
        self.assertEqual(merged_studies(p)['person_id'], '01000000009')
        self.assertEqual(p.phone, '599000009')
        self.assertTrue(merged_q(p)['htn'])

        # Subsequent GET or POST on the used one-time link is locked!
        r_get_after = anon.get(url1)
        self.assertContains(r_get_after, 'ერთჯერადი ბმული დახურულია')
        r_post2 = anon.post(url1, {'htn': '0'})
        self.assertIn('locked=1', r_post2['Location'])
        p.refresh_from_db()
        self.assertTrue(merged_q(p)['htn'])

        # 3. Nurse rotates link -> old forwarded URL returns 404, new URL works once; nurse revoke -> 404!
        self.client.post(reverse('preop-patient', args=[p.pk]), {'section': 'self_link', 'action': 'rotate'})
        p.refresh_from_db()
        self.assertEqual(anon.get(url1).status_code, 404)
        tok2 = make_self_fill_token(p)
        url2 = reverse('preop-self-fill', args=[tok2])
        self.assertEqual(anon.get(url2).status_code, 200)

        self.client.post(reverse('preop-patient', args=[p.pk]), {'section': 'self_link', 'action': 'revoke'})
        r_revoked = anon.get(url2)
        self.assertEqual(r_revoked.status_code, 404)
        self.assertEqual(r_revoked['Referrer-Policy'], 'no-referrer')

    def test_review_3_rcri_lee1999_vsg_cri_bertges2010_and_ariscat_clinician_duration(self):
        from ..models import PreopPatient
        from ..preop import evaluate

        # 1. RCRI (Lee 1999): infrainguinal bypass & symptomatic CEA do NOT get suprainguinal surgery +1 point
        p_byp = PreopPatient.objects.create(
            patient_name='ლი ინდექსაძე', age=64, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            questionnaire={'filled': True, 'abf_rest_pain': True, 'mets_ok': True},
            studies={'abi_r': 0.38, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate'},
        )
        ev_byp = evaluate(p_byp)
        self.assertEqual(ev_byp['rcri']['score'], 0)  # No suprainguinal surgery inflation!
        self.assertEqual(ev_byp['ariscat']['score'], 19)  # age 51-80 (+3) + auto 2-3h (+16)

        # Clinician sets surg_duration_est='gt_3h' on complex tibial bypass -> ARISCAT duration rises +16 -> +23 (score 26)
        self.client.post(reverse('preop-patient', args=[p_byp.pk]), {
            'section': 'stage2', 'surg_duration_est': 'gt_3h',
        })
        p_byp.refresh_from_db()
        ev_byp_long = evaluate(p_byp)
        self.assertEqual(ev_byp_long['ariscat']['score'], 26)
        self.assertEqual(ev_byp_long['ariscat']['tier'], 'intermediate')

        p_cea = PreopPatient.objects.create(
            patient_name='სიმპტომ კაროტიდაძე', age=66, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'cea_symp_event': True, 'mets_ok': True},
            studies={'carotid_r': 80, 'carotid_l': 25},
        )
        # Symptomatic CEA gets +1 for prior TIA/stroke ONLY (NOT +2 with high-risk surgery!)
        self.assertEqual(evaluate(p_cea)['rcri']['score'], 1)

        # 2. VSG-CRI (Bertges et al. 2010): exact 6-bucket MACE probabilities (4 pts -> 3.5%, 5 -> 6.0%, 6 -> 6.6%, 7 -> 8.9%, >=8 -> 14.3%)
        p_vsg = PreopPatient(
            patient_name='ბერტგეს ცხრილაძე', age=82, sex='M',
            pathway='aaa_infra', op_subtype='evar',
            questionnaire={'filled': True, 'mets_ok': True},
            studies={},
        )
        self.assertEqual(evaluate(p_vsg)['vsg_cri']['score'], 4)
        self.assertEqual(evaluate(p_vsg)['vsg_cri']['risk_pct'], '3.5%')
        p_vsg.questionnaire['smoker'] = True  # 5 pts
        self.assertEqual(evaluate(p_vsg)['vsg_cri']['risk_pct'], '6.0%')
        p_vsg.questionnaire['dm_insulin'] = True  # 6 pts
        self.assertEqual(evaluate(p_vsg)['vsg_cri']['risk_pct'], '6.6%')
        p_vsg.questionnaire['med_chips'] = ['beta_blocker']  # 7 pts
        self.assertEqual(evaluate(p_vsg)['vsg_cri']['risk_pct'], '8.9%')
        p_vsg.questionnaire['pulm'] = True  # 9 pts (>=8)
        self.assertEqual(evaluate(p_vsg)['vsg_cri']['risk_pct'], '14.3%')

    def test_review_4_asra_inr_1_4_pharmacology_citations_and_passport_physician_confirmation_gate(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()
        p = PreopPatient.objects.create(
            patient_name='ფარმაკო პასპორტაძე', age=70, sex='M', side='R',
            pathway='venous', op_subtype='phlebectomy', surgeon=self.user,
            questionnaire={
                'filled': True, 'med_ticagrelor': True, 'med_doac': True,
                'med_warfarin': True, 'glp1': True, 'mets_ok': True,
            },
            studies={
                'labs_date': today.isoformat(), 'spo2': 98,
                'weight_kg': 62, 'height_cm': 170, 'creat': 135, 'creat_unit': 'umol',
                'hb': 135, 'plt': 210, 'inr': 1.35, 'dos_inr': 1.35,
                'anesthesia_plan': 'neuraxial', 'planned_surgery_date': '2026-12-15',
            },
        )
        ev = evaluate(p)
        # ASRA neuraxial INR <= 1.4: INR 1.35 does NOT trigger neuraxial INR > 1.4 blocker or DoS hold danger!
        self.assertFalse(ev['dos_hold_danger'])
        self.assertNotIn('INR 1.35 >', ev['anesthetic_brief']['neuraxial_gate'])

        sched = {m['drug']: m for m in ev['med_schedule']}
        self.assertIn('5–7 დღით ადრე (ASRA 5th Ed: 5–7 დღე)', sched['ტიკაგრელორი (ბრილინტა)']['rule'])
        self.assertIn('INR ≤ 1.4', sched['ვარფარინი']['rule'])
        self.assertIn('Multi-Society 2024', sched['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['rule'])
        self.assertIn('უმეტეს პაციენტთან გრძელდება', sched['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['rule'])

        # Switch to general anesthesia with CrCl 30-49 -> cites EHRA 2021 >=48h vs 72h renal/ASRA buffer
        p.studies['anesthesia_plan'] = 'general'
        p.save()
        sched_gen = {m['drug']: m for m in evaluate(p)['med_schedule']}
        self.assertIn('EHRA 2021', sched_gen['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        # Unapproved Passport shows prominent draft warning + unconfirmed med-schedule notice
        pp_draft = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertContains(pp_draft, 'სამუშაო (დაუდასტურებელი) ასლი')
        self.assertContains(pp_draft, 'მოქმედებს მხოლოდ მკურნალი ექიმის დადასტურებით!')
        self.assertContains(pp_draft, 'ექიმთან ჯერ არ არის დადასტურებული')

        # Resolve lethal dual-OAC/ESC-III conflict & complete venous workup before surgeon sign-off
        p.questionnaire.update({'med_doac': False, 'med_ticagrelor': False})
        p.studies.update({
            'ven_duplex_status': 'deep_patent_reflux', 'ecg': 'normal',
            'compression_ready': True, 'vte_prophylaxis_planned': True,
            'med_hold_agreed': True, 'anesth_done': True,
        })
        p.save()
        # Surgeon approval clears draft banner & marks med schedule confirmed while keeping physician-only rule
        self.client.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve'})
        pp_ok = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertNotContains(pp_ok, 'სამუშაო (დაუდასტურებელი) ასლი')
        self.assertContains(pp_ok, 'მოქმედებს მხოლოდ მკურნალი ექიმის დადასტურებით!')
        self.assertContains(pp_ok, '[დადასტურებულია ექიმთან ✓]')

    def test_sprint1a_antithrombotic_stop_lock_mech_valve_and_cilostazol(self):
        from django.utils import timezone

        from ..duty import strip_astral
        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()

        # L7-F1: strip_astral removes ASCII C0 controls & Unicode BiDi overrides while keeping Georgian + \n + \t
        self.assertEqual(strip_astral('ვარფარინი\u202e\x00\x07\n5მგ\tდღეში'), 'ვარფარინი\n5მგ\tდღეში')

        # C1: STOP-seal locks antithrombotic discontinuation dates & displays STOP banner on Passport
        p_stop = PreopPatient.objects.create(
            patient_name='სტოპ კალენდარაძე', age=72, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={
                'filled': True, 'med_warfarin': True, 'med_clopidogrel': True,
                'sglt2': True, 'mets_ok': True,
            },
            studies={
                'carotid_r': 100, 'carotid_l': 30,  # 100% ICA occlusion -> hard contraindication -> STOP!
                'planned_surgery_date': '2026-12-20',
            },
        )
        ev_stop = evaluate(p_stop, today=today)
        self.assertEqual(ev_stop['clearance_seal']['code'], 'stop')
        sched_stop = {m['drug']: m for m in ev_stop['med_schedule']}
        self.assertIn('STOP — თვითნებურად არ შეწყვიტოთ!', sched_stop['ვარფარინი']['date_plan'])
        self.assertNotIn('ბოლო მიღება', sched_stop['ვარფარინი']['date_plan'])
        pp_stop = self.client.get(reverse('preop-passport', args=[p_stop.pk]))
        self.assertContains(pp_stop, 'ოპერაციაზე დაშვება დაბლოკილია (STOP)')

        # C2 + C3.1 + C3.2: Mechanical valve forbids DOAC & VOYAGER, bridges with IV bivalirudin on HIT or IV UFH on CrCl < 30
        p_mv = PreopPatient(
            patient_name='სარქველ ხიდაძე', age=68, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti',
            questionnaire={
                'filled': True, 'cad': True, 'cad_chips': ['mech_valve', 'afib'],
                'med_warfarin': True, 'med_dabigatran': True, 'med_ticagrelor': True,
                'allergy_other': True, 'allergy_chips': ['hit_heparin', 'clopidogrel_intol'],
            },
            studies={'weight_kg': 75, 'height_cm': 175, 'creat': 90, 'inr': 1.6, 'post_antithrombotic_plan': 'voyager'},
        )
        ev_mv = evaluate(p_mv, today=today)
        mv_reds = ' | '.join(ev_mv['red_flags'])
        self.assertIn('მექანიკურ სარქველზე DOAC', mv_reds)
        self.assertIn('სუბთერაპიულია', mv_reds)
        self.assertIn('მექანიკური სარქველი + HIT', mv_reds)
        self.assertIn('2 ან მეტი პერორალური ანტიკოაგულანტი', mv_reds)
        self.assertIn('ტიკაგრელორთან ან პრასუგრელთან', mv_reds)
        self.assertIn('უვადო ვარფარინი (VKA)', mv_reds)
        mv_sched = {m['drug']: m for m in ev_mv['med_schedule']}
        self.assertIn('დაბიგატრანი (პრადაქსა)', mv_sched)
        self.assertIn('ბივალირუდინი ან არგატრობანი', mv_sched['ვარფარინი']['rule'])

        # C3.4 + C3.5 + C3.6 + L1-F5: Cilostazol Black-Box on HFrEF, active stent without P2Y12, OAC + clopidogrel_intol uses aspirin
        p_c3 = PreopPatient(
            patient_name='ცილოსტაზოლ სტენტაძე', age=67, sex='M', side='L',
            pathway='pad_pta', op_subtype='pta_clti',
            questionnaire={
                'filled': True, 'cad': True, 'cad_chips': ['hf', 'afib'],
                'recent_stent': True, 'med_doac': True, 'med_chips': ['cilostazol'],
                'allergy_other': True, 'allergy_chips': ['clopidogrel_intol'],
            },
            studies={'lvef': 32, 'weight_kg': 78, 'height_cm': 174, 'creat': 88},
        )
        ev_c3 = evaluate(p_c3, today=today)
        c3_reds = ' | '.join(ev_c3['red_flags'])
        self.assertIn('ცილოსტაზოლი უკუნაჩვენებია', c3_reds)
        self.assertIn('აქტიური P2Y12 ანტიაგრეგანტის გარეშე', c3_reds)
        self.assertIn('ასპირინი 75–100 მგ', ev_c3['post_antithrombotic_label'])
        self.assertNotIn('OAC + ტიკაგრელორი (სამმაგი', ev_c3['post_antithrombotic_label'])

    def test_sprint1b_freetext_board_rehandoff_and_renal_hepatic_pharmacology(self):
        from django.utils import timezone

        from ..models import BoardEntry, BoardPatient, PreopPatient
        from ..preop import admit_preop_to_board, evaluate

        today = timezone.localdate()

        # C4.1-C4.4: Free-text allergies & high-risk meds in other_meds / allergy_text + C9.1 BoardEntry re-handoff
        p_ft = PreopPatient.objects.create(
            patient_name='ფრიტექსტ ალერგიაძე', age=65, sex='F', side='R',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            questionnaire={
                'filled': True, 'mets_ok': True,
                'other_meds': 'სინკუმარი 2მგ, ემპაგლიფლოზინი',
                'allergy_other': True,
                'allergy_text': 'ცეფტრიაქსონი ანაფილაქსია, კეტოროლაკი, იოდის კონტრასტი',
            },
            studies={'carotid_r': 85, 'carotid_l': 20, 'creat': 75, 'hb': 130, 'plt': 220, 'inr': 1.0, 'ecg': 'normal', 'cea_cta_status': 'concordant'},
        )
        ev_ft = evaluate(p_ft, today=today)
        ft_reds = ' | '.join(ev_ft['red_flags'])
        self.assertIn('სინკუმარი', ft_reds)
        self.assertIn('β-ლაქტამური ალერგია', ev_ft['surgical_brief']['abx_regimen'])
        # C5.1: Neck CEA must NOT receive groin Gentamicin 5 mg/kg!
        self.assertNotIn('გენტამიცინი 5 მგ/კგ', ev_ft['surgical_brief']['abx_regimen'])
        self.assertIn('NSAID', ev_ft['allergy_summary'])

        # C9.1: Re-running admit_preop_to_board when task text changes updates undone BoardEntry without AttributeError!
        bp, _, _ = admit_preop_to_board(p_ft, self.user, unit=BoardPatient.WARD, bed='5')
        p_ft.studies['carotid_r'] = 92
        p_ft.questionnaire['allergy_chips'] = ['hit_heparin', 'nsaid']
        p_ft.save()
        bp2, was_created2, _ = admit_preop_to_board(p_ft, self.user, unit=BoardPatient.WARD, bed='5')
        self.assertFalse(was_created2)
        self.assertEqual(bp.pk, bp2.pk)
        self.assertTrue(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK, text__icontains='NSAID').exists())

        # C5.2-C5.5: Frail elderly CrCl < 30 with normal eGFR, hypernatremia Na+ 154, liver 3x
        p_ren = PreopPatient(
            patient_name='კოკკროფტ ღვიძლაძე', age=86, sex='F', side='R',
            pathway='pad_pta', op_subtype='pta_clti',
            questionnaire={
                'filled': True, 'metformin': True, 'med_doac': True,
                'allergy_other': True, 'allergy_chips': ['beta_lactam', 'hit_heparin'],
            },
            studies={
                'weight_kg': 42, 'height_cm': 156, 'creat': 115, 'creat_unit': 'umol',
                'na': 154, 'liver_status': '3x',
            },
        )
        ev_ren = evaluate(p_ren, today=today)
        self.assertGreaterEqual(ev_ren['egfr'], 35)
        self.assertLess(ev_ren['crcl'], 30)
        self.assertTrue(ev_ren['co2_angio_advised'])
        ren_reds = ' | '.join(ev_ren['red_flags'])
        self.assertIn('მეტფორმინი უკუნაჩვენებია', ren_reds)
        self.assertIn('ჰიპერნატრიემია', ren_reds)
        self.assertIn('ღვიძლის მძიმე დისფუნქცია', ren_reds)
        self.assertIn('0.9% NaCl-ით დატვირთვა აკრძალულია', ev_ren['surgical_brief']['contrast_plan'])
        self.assertIn('გენტამიცინს მოერიდეთ', ev_ren['surgical_brief']['abx_regimen'])
        self.assertNotIn('სტატინის მიღება', ' | '.join(ev_ren['amber_flags']))

    def test_sprint2a_lab_normalizers_asra_neuraxial_and_cied(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import (
            evaluate,
            normalize_fibrinogen_gl,
            normalize_glucose_mmol,
            normalize_ldl_mmol,
        )

        today = timezone.localdate()

        # C8.2 / Track A3: Lab unit normalizers (glucose >35 mg/dL synced with JS, 34 stays 34.0 HHS, 40 -> 2.2 hypo; fibrinogen >15; LDL >20)
        self.assertEqual((normalize_glucose_mmol(34), normalize_glucose_mmol(40)), (34.0, 2.22))
        self.assertEqual(normalize_glucose_mmol(90), 5.0)
        self.assertEqual(normalize_fibrinogen_gl(140), 1.4)
        self.assertEqual(normalize_ldl_mmol(130), 3.36)

        # C6.1 + C6.3: Auto EVAR never contradicts neuraxial_gate; selecting neuraxial activates ASRA 5th Ed 72h DOAC & IV UFH >=60m rule;
        # when PLT < 100 in auto mode, auto switches to General Anesthesia ("ნეირაქსიული შეზღუდულია!")!
        p_evar = PreopPatient(
            patient_name='ასრა ევარაძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='evar',
            questionnaire={'filled': True, 'med_doac': True, 'mets_ok': True},
            studies={'weight_kg': 80, 'height_cm': 176, 'creat': 85, 'plt': 210, 'inr': 1.0, 'aaa_mm': 58},
        )
        ev_auto = evaluate(p_evar, today=today)
        self.assertNotIn('არ იგეგმება', ev_auto['anesthetic_brief']['neuraxial_gate'])
        self.assertIn('ASRA 72 სთ რეჟიმი', ev_auto['effective_anesth_label'])

        p_evar.studies['anesthesia_plan'] = 'neuraxial'
        ev_ok = evaluate(p_evar, today=today)
        self.assertTrue(ev_ok['has_neuraxial_plan'])
        self.assertIn('72 საათით', {m['drug']: m for m in ev_ok['med_schedule']}['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertIn('≥60 წთ', ev_ok['anesthetic_brief']['neuraxial_gate'])

        p_evar.studies['anesthesia_plan'] = 'auto'
        p_evar.studies['plt'] = 78
        ev_plt = evaluate(p_evar, today=today)
        self.assertFalse(ev_plt['has_neuraxial_plan'])
        self.assertIn('ნეირაქსიული შეზღუდულია', ev_plt['effective_anesth_label'])
        self.assertIn('ავტო-გადართვა ზოგადზე', ev_plt['anesthetic_brief']['neuraxial_gate'])

        # C6.4 + L1-F3 + C6.5: Solo neuraxial on CEA triggers anatomical mismatch STOP & blocks Signature #2; GLP-1 forces endotracheal GA
        p_cea = PreopPatient.objects.create(
            patient_name='სოლო სპინალაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            questionnaire={'filled': True, 'glp1': True, 'pacemaker': True, 'med_aspirin': True, 'mets_ok': True},
            studies={
                'anesthesia_plan': 'neuraxial', 'carotid_r': 82, 'carotid_l': 25,
                'cea_cta_status': 'concordant', 'hb': 136, 'plt': 200, 'inr': 1.0, 'creat': 82, 'ecg': 'normal',
            },
        )
        ev_cea = evaluate(p_cea, today=today)
        self.assertEqual(ev_cea['clearance_seal']['code'], 'stop')
        self.assertTrue(ev_cea['neuraxial_hard_blocked'])
        self.assertIn('CIED: რეჟიმი დასაზუსტებელია (!)', ev_cea['anesthetic_brief']['lines_and_cied'])
        pp_cea = self.client.get(reverse('preop-passport', args=[p_cea.pk]))
        self.assertContains(pp_cea, 'შესაცვლელია! (ნეირაქსიული შეზღუდულია — იხ. 2B)')

        p_cea.studies['anesthesia_plan'] = 'general'
        p_cea.save()
        ev_cea_ga = evaluate(p_cea, today=today)
        self.assertEqual(ev_cea_ga['effective_anesth_label'], 'ზოგადი ენდოტრაქეული ანესთეზია')
        self.assertNotIn('ლარინგეალური', ev_cea_ga['effective_anesth_label'])

    def test_sprint2b_vitals_shock_index_and_passport_visibility(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()

        # C8.1: Inverted cuff, Shock Index >= 1.0, tachysystolic AFib (HR > 110)
        p_vit = PreopPatient(
            patient_name='შოკ ინდექსაძე', age=70, sex='M',
            pathway='aaa_infra', op_subtype='open',
            questionnaire={'filled': True, 'mets_ok': True},
            studies={'bp_sys': 80, 'bp_dia': 115, 'bp_sys_l': 96, 'bp_dia_l': 80, 'hr': 118, 'ecg': 'afib_controlled', 'fibrinogen': 140},
        )
        ev_vit = evaluate(p_vit, today=today)
        vit_reds = ' | '.join(ev_vit['red_flags'])
        self.assertIn('SBP ≤ DBP', vit_reds)
        self.assertNotIn('>= 180/110', vit_reds)
        self.assertIn('შოკის ინდექსი', vit_reds)
        self.assertIn('ტაქისისტოლია', ev_vit['ecg_label'])
        self.assertIn('ჰიპოფიბრინოგენემია', vit_reds)
        self.assertIn('სასაზღვრო ჰიპოტენზია', ' | '.join(ev_vit['amber_flags']))
        self.assertIn('ვიწრო პულსური წნევა', ' | '.join(ev_vit['amber_flags']))

        # C9.2 + C9.3 + C9.4 + L1-F4: Passport shows BOTH LVEF & BNP, Page 1 cautions on cardio-cleared AS, and Section 6 dos_warnings!
        p_pp = PreopPatient.objects.create(
            patient_name='პასპორტ სიფრთხილაძე', age=74, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user, cardio_decision='cleared',
            questionnaire={'filled': True, 'med_warfarin': True, 'med_aspirin': True, 'mets_ok': True},
            studies={
                'aaa_mm': 74, 'lvef': 36, 'echo_valve_issue': True, 'nt_probnp': 890,
                'hb': 132, 'plt': 210, 'inr': 2.6, 'creat': 88, 'ecg': 'normal', 'cta_disc': True,
                'blood_reserved': True, 'med_hold_agreed': True, 'anesth_done': True,
                'dos_identity_consent': True, 'dos_fasting_confirmed': True,
                'dos_med_hold_verified': True, 'dos_abx_ready': True,
                'dos_blood_implant_ready': True, 'icu_bed_booked': True,
            },
        )
        ev_pp = evaluate(p_pp, today=today)
        # L1-F4: Baseline INR 2.6 with missing morning dos_inr blocks dos_ready!
        self.assertFalse(ev_pp['dos_ready'])
        self.assertTrue(ev_pp['dos_hold_danger'])
        pp_html = self.client.get(reverse('preop-passport', args=[p_pp.pk]))
        self.assertContains(pp_html, 'LVEF 36%')
        self.assertContains(pp_html, 'BNP 890')
        self.assertContains(pp_html, 'კარდიო-სიფრთხილე (დაშვებულია ✓)')
        self.assertContains(pp_html, 'საბაზისო INR (2.6)')

    def test_sprint3a_esvs_never_events_venous_deadlock_and_cta_age(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import cta_live_months_age, evaluate, generate_anamnesis

        today = timezone.localdate()

        # C10.1: Simultaneous bilateral CEA (side='B') -> STOP hard contraindication + blocked dos_site_marked;
        # unset side preserves contralateral critical stenosis (>=80%); >=80yo ASA IV asymptomatic CEA futility
        p_cea_b = PreopPatient(
            patient_name='ორმხრივ კაროტიდაძე', age=82, sex='M', side='B',
            pathway='cea', op_subtype='cea_asymp',
            questionnaire={'filled': True, 'mets_ok': True, 'med_aspirin': True, 'med_chips': ['statin']},
            studies={'carotid_r': 85, 'carotid_l': 82, 'lvef': 32, 'dos_site_marked': True},
        )
        ev_cea_b = evaluate(p_cea_b, today=today)
        self.assertTrue(ev_cea_b['has_hard_contraindication'])
        self.assertEqual(ev_cea_b['clearance_seal']['code'], 'stop')
        self.assertFalse(next(c for c in ev_cea_b['dos_checks'] if c['key'] == 'dos_site_marked')['done'])
        self.assertTrue(any('ასიმპტომური CEA ≥80 წლის ASA IV' in rf for rf in ev_cea_b['red_flags']))
        self.assertIn('SBP 110–160 mmHg', ev_cea_b['anesthetic_brief']['bp_corridor'])
        self.assertIn('SBP < 140–150 mmHg', ev_cea_b['anesthetic_brief']['bp_corridor'])

        p_cea_unset = PreopPatient(
            patient_name='უცნობ მხარაძე', age=68, sex='M', side='',
            pathway='cea', op_subtype='cea_symp',
            questionnaire={'filled': True, 'cea_symp_event': True, 'cea_days_since_event': 240, 'med_aspirin': True},
            studies={'carotid_r': 88, 'carotid_l': 84},
        )
        ev_unset = evaluate(p_cea_unset, today=today)
        self.assertEqual(ev_unset['contra_car'], 84)
        self.assertEqual(ev_unset['target_days'], 14)  # >180d -> asymptomatic target clock!
        self.assertEqual(ev_unset['auto_asa'], 'III')

        # C10.2 + C10.3 + L1-F1: Venous compression deadlock resolved on TP 42 (<50 hides comp) and TP 55 (50..59 Class I comp);
        # EVLA obesity + HFrEF reduces lidocaine ceiling <=300mg + Intralipid LAST note; no false DVT-denial in anamnesis
        p_ven = PreopPatient(
            patient_name='მონკებერგ ლიდოკაინაძე', age=72, sex='F', side='R',
            pathway='venous', op_subtype='evla',
            questionnaire={'filled': True, 'ven_ceap': 'C4', 'cad': True, 'cad_chips': ['hf'], 'mets_ok': True},
            studies={
                'weight_kg': 115, 'height_cm': 160, 'lvef': 34,
                'hb': 130, 'plt': 220, 'inr': 1.02, 'glucose': 5.5, 'ecg': 'normal',
                'ven_duplex_status': 'deep_patent_reflux', 'abi_r': 1.55, 'tp_r': 42, 'compression_ready': True,
            },
        )
        ev_tp42 = evaluate(p_ven, today=today)
        self.assertNotIn('comp', [t['key'] for t in ev_tp42['tasks']])
        self.assertLessEqual(ev_tp42['lidocaine_max_mg'], 300)
        self.assertIn('Intralipid', ev_tp42['anesthetic_brief']['bp_corridor'])

        p_ven.studies['tp_r'] = 55
        ev_tp55 = evaluate(p_ven, today=today)
        comp_55 = next(t for t in ev_tp55['tasks'] if t['key'] == 'comp')
        self.assertTrue(comp_55['done'])
        self.assertIn('I კლასის', comp_55['label'])

        p_ven.studies['ven_duplex_status'] = 'acute_dvt'
        ev_dvt = evaluate(p_ven, today=today)
        self.assertNotIn('უარყოფს', generate_anamnesis(p_ven, ev_dvt).split('ვენური და თრომბოემბოლიური')[1].split('\n')[0])

        # C10.4 + L3-F1: Exact calendar CTA age (2026-01-01 -> 2026-07-29 is >6m) & sub-threshold fusiform AAA Red Flag
        self.assertGreater(cta_live_months_age({'cta_date': '2026-01-01'}, today=datetime.date(2026, 7, 29)), 6)
        self.assertEqual(cta_live_months_age({'cta_date': '2026-01-01'}, today=datetime.date(2026, 7, 1)), 6)

    def test_sprint3b_rupture_fast_track_and_metabolic_guards(self):
        import datetime

        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate

        today = timezone.localdate()

        # C11.1 + C11.3: Rupture-suspect AAA (aaa_pain) auto ASA-E, bypasses outpatient tasks, warns against Andexanet before UFH,
        # and undated someday:conf acute patient surfaces in PreopWeekView overdue_patients!
        p_rup = PreopPatient.objects.create(
            patient_name='რუპტურა სასწრაფოძე', age=73, sex='M',
            pathway='aaa_infra', op_subtype='open', someday_list='conf', visit_date=None, surgeon=self.user,
            questionnaire={
                'filled': True, 'aaa_pain': True, 'pulm': True, 'med_doac': True,
                'dental_infection': True, 'mets_ok': False,
            },
            studies={
                'aaa_mm': 68, 'bp_sys': 84, 'bp_dia': 52, 'hr': 118,
                'hb': 108, 'creat': 130, 'creat_unit': 'umol', 'glucose': 12.6, 'inr': 1.1,
                'ecg': 'normal', 'lvef': 50, 'labs_date': (today - datetime.timedelta(days=20)).isoformat(),
            },
        )
        ev_rup = evaluate(p_rup, today=today)
        self.assertTrue(ev_rup['effective_asa'].endswith('-E'))
        rup_task_keys = {t['key'] for t in ev_rup['tasks']}
        for bypassed in ('spiro', 'endo', 'hba1c', 'dental', 'anemia', 'stale_labs', 'cardio'):
            self.assertNotIn(bypassed, rup_task_keys)
        self.assertIn('პერმისიული ჰიპოტენზია SBP 70–90', ev_rup['anesthetic_brief']['bp_corridor'])
        self.assertIn('ანდექსანეტ ალფას', ev_rup['surgical_brief']['tactical_note'])
        self.assertIn('VRIII', ev_rup['anesthetic_brief']['glycemic_target'])
        wk_resp = self.client.get(reverse('preop'))
        self.assertIn(p_rup, wk_resp.context['overdue_patients'])

        # C12.2 + C12.3 + C12.4 + C10.5: SGLT2 dos_ketones_ok gate, undiagnosed DM (HbA1c 6.8%), hypothyroid_severe, GLP-1 POCUS, and reproductive pregnancy radiation guard
        p_met = PreopPatient.objects.create(
            patient_name='მეტაბოლ ფარისებრაძე', age=38, sex='F', side='R',
            pathway='pad_pta', op_subtype='pta_clti', surgeon=self.user,
            questionnaire={
                'filled': True, 'abf_rest_pain': True, 'sglt2': True, 'glp1': True,
                'pregnant_possible': True, 'med_aspirin': True, 'mets_ok': True,
            },
            studies={
                'hb': 128, 'plt': 240, 'inr': 1.0, 'creat': 72, 'glucose': 6.4, 'hba1c': 6.8,
                'abi_r': 0.36, 'pta_access_site': 'cfa_clean', 'ecg': 'normal', 'tsh_status': 'hypothyroid_severe',
            },
        )
        ev_met = evaluate(p_met, today=today)
        met_reds = ' | '.join(ev_met['red_flags'])
        self.assertIn('არადიაგნოსტირებული შაქრიანი დიაბეტი', met_reds)
        self.assertIn('მიქსედემური კომის', met_reds)
        self.assertIn('ნაყოფის დასხივებისა', met_reds)
        self.assertIn('GLP-1 ასპირაციის დაცვა', ev_met['anesthetic_brief']['airway_summary'])
        self.assertIn('dos_ketones_ok', [c['key'] for c in ev_met['dos_checks']])

    def test_sprint4_asa_rcri_frailty_coag_pbm_nice_esc_deescalation_and_nurse_seal(self):
        from django.utils import timezone

        from ..models import PreopPatient
        from ..preop import evaluate, generate_anamnesis

        today = timezone.localdate()

        # 1. C13: ASA IV mandate for needs_anesth, brain_imaging RCRI point, lone AFib non-IHD anamnesis,
        # BMI < 16 cachexia red flag, CFS >= 5 frailty/delirium, ESAIC 2023 targeted PBM + HIT platelet contraindication,
        # succinylcholine hyperkalemia guard, and dialysis AV fistula / latex DoS checks
        p_crit = PreopPatient.objects.create(
            patient_name='ფრეილ კოაგულაძე', age=79, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_clti', surgeon=self.user,
            questionnaire={
                'filled': True, 'abf_rest_pain': True, 'cfs_level': '5_plus',
                'cad': True, 'cad_chips': ['afib'], 'on_dialysis': True,
                'med_aspirin': True, 'med_clopidogrel': True,
                'allergy_other': True, 'allergy_chips': ['hit_heparin', 'latex_chlorhex'],
                'mets_ok': True,
            },
            studies={
                'weight_kg': 43, 'height_cm': 170,  # BMI 14.9 < 16.0 -> severe cachexia + ASA IV!
                'bp_sys': 148, 'bp_dia': 92, 'hr': 84,  # objective BP >=140/90 -> STOP-Bang P point!
                'hb': 108, 'plt': 65, 'fibrinogen': 1.4, 'inr': 1.75, 'k': 5.7,
                'abi_r': 0.35, 'pta_access_site': 'cfa_clean', 'ecg': 'normal',
                'brain_imaging': 'large_or_hemorrhage',
            },
        )
        ev_crit = evaluate(p_crit, today=today)
        self.assertEqual(ev_crit['auto_asa'], 'IV')
        self.assertTrue(ev_crit['needs_anesth'])  # ASA IV forces needs_anesth even on PAD_PTA!
        self.assertTrue(any('ინსულტი / TIA' in f for f in ev_crit['rcri']['factors']))
        self.assertGreaterEqual(ev_crit['stop_bang_auto'], 3)
        self.assertTrue(ev_crit['needs_ppi'])
        self.assertTrue(any('პანტოპრაზოლი' in m['drug'] for m in ev_crit['med_schedule']))
        crit_reds = ' | '.join(ev_crit['red_flags'])
        self.assertIn('მძიმე კახექსია', crit_reds)
        pbm_txt = ev_crit['anesthetic_brief']['transfusion_pbm']
        self.assertIn('PLT <100 + HIT: პროფილაქტიკური თრომბოკონცენტრატის გადასხმა უკუნაჩვენებია', pbm_txt)
        self.assertIn('ფიბრინოგენის კონცენტრატი', pbm_txt)
        self.assertIn('ვიტამინი K 10 მგ IV', pbm_txt)
        self.assertIn('სუქცინილქოლინი უკუნაჩვენებია', ev_crit['anesthetic_brief']['airway_summary'])
        self.assertIn('AV ფისტულის მკლავის დაცვა', ev_crit['anesthetic_brief']['lines_and_cied'])
        self.assertIn('დელირიუმის პრევენცია', ev_crit['anesthetic_brief']['disposition'])
        self.assertIn('MINS მონიტორინგი', ev_crit['anesthetic_brief']['disposition'])
        dos_keys = [c['key'] for c in ev_crit['dos_checks']]
        self.assertIn('dos_k_verified', dos_keys)
        self.assertIn('dos_latex_free_or', dos_keys)
        self.assertNotIn('dos_abx_ready', dos_keys)
        self.assertIn('ESVS 2024: გართულების გარეშე პერკუტანულ PTA-ზე', ev_crit['surgical_brief']['abx_regimen'])
        anam_crit = generate_anamnesis(p_crit, ev_crit)
        self.assertIn('კარდიოლოგიური პათოლოგია', anam_crit)
        self.assertNotIn('გულის იშემიური დაავადება', anam_crit)

        # 2. C14: Pure local-anesthesia PAD_PTA with isolated CKD 3a (eGFR 52) omits needs_cardio;
        # Amiodarone on non-contrast CEA omits routine TSH; HF requires structural TTE (lvef) despite BNP < 125
        p_pta_lean = PreopPatient(
            patient_name='ლოკალ პტაძე', age=68, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_clti',
            questionnaire={'filled': True, 'abf_rest_pain': True, 'mets_ok': True, 'med_aspirin': True},
            studies={'creat': 128, 'creat_unit': 'umol', 'hb': 132, 'plt': 220, 'inr': 1.0, 'glucose': 5.4, 'abi_r': 0.42, 'pta_access_site': 'cfa_clean', 'ecg': 'normal'},
        )
        ev_pta_lean = evaluate(p_pta_lean, today=today)
        self.assertGreaterEqual(ev_pta_lean['egfr'], 45)
        self.assertLess(ev_pta_lean['egfr'], 60)
        self.assertFalse(ev_pta_lean['needs_cardio'])

        p_cea_amio = PreopPatient(
            patient_name='ამიო კაროტიდაძე', age=70, sex='M', side='R',
            pathway='cea', op_subtype='cea_asymp',
            questionnaire={'filled': True, 'cad': True, 'cad_chips': ['hf'], 'med_chips': ['amiodarone'], 'mets_ok': True},
            studies={'hb': 110, 'creat': 90, 'inr': 1.0, 'ecg': 'normal', 'nt_probnp': 90, 'carotid_r': 80, 'carotid_l': 25, 'cea_cta_status': 'concordant'},
        )
        ev_cea_amio = evaluate(p_cea_amio, today=today)
        self.assertFalse(ev_cea_amio['needs_tsh'])
        self.assertFalse(ev_cea_amio['s1_core_done'])  # HF requires structural TTE (lvef), BNP < 125 not enough!
        anemia_act = [r['action'] for r in ev_cea_amio['reflex_orders'] if r['key'] == 'anemia'][0]
        self.assertNotIn('განავალი ფარულ სისხლდენაზე', anemia_act)

        # 3. L1-F2: Unverified patient self-fill keeps clearance_seal == 'pending' until nurse verifies
        p_sf = PreopPatient.objects.create(
            patient_name='თვითშევსება ექთანაძე', age=36, sex='F', side='R',
            pathway='venous', op_subtype='evla', surgeon=self.user,
            questionnaire={'filled': True, 'filled_by': 'პაციენტი (ბმულით)', 'nurse_verified': False, 'mets_ok': True},
            studies={
                'bp_sys': 118, 'bp_dia': 74, 'hr': 72, 'spo2': 99, 'dos_bhcg_negative': True,
                'hb': 132, 'plt': 240, 'labs_date': today.isoformat(), 'ven_duplex_status': 'deep_patent_reflux', 'compression_ready': True,
            },
        )
        ev_sf_unver = evaluate(p_sf, today=today)
        self.assertEqual(ev_sf_unver['clearance_seal']['code'], 'pending')
        self.assertIn('ექთნის ვერიფიკაცია', ev_sf_unver['clearance_seal']['badge'])
        self.client.post(reverse('preop-patient', args=[p_sf.pk]), {'section': 'questionnaire', 'mets_ok': '1'})
        p_sf.refresh_from_db()
        self.assertEqual(evaluate(p_sf, today=today)['clearance_seal']['code'], 'go')

    def test_sprint5_http_lifecycle_invalidation_hold_clock_stage2_preserve_and_owasp(self):
        import datetime

        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils import timezone

        from ..models import PreopFile, PreopPatient
        from ..preop import evaluate, make_self_fill_token, merged_studies

        today = timezone.localdate()
        User = get_user_model()
        clerk = User.objects.create_user('clerk_s5', password='pw', is_staff=False)
        clerk_client = Client()
        clerk_client.force_login(clerk)
        anon = Client()

        # 1. L2-F1 + L2-F2: Changing op_subtype or side invalidates stale approved_at & frozen anamnesis
        p_op = PreopPatient.objects.create(
            patient_name='ლაიფსაიქლ ტესტაძე', age=66, sex='M', side='R',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={'filled': True, 'mets_ok': True, 'med_aspirin': True, 'med_chips': ['statin']},
            studies={'labs_date': today.isoformat(), 'bp_sys': 128, 'bp_dia': 78, 'hr': 72, 'spo2': 98, 'hb': 138, 'inr': 1.0, 'creat': 84, 'ecg': 'normal', 'carotid_r': 82, 'carotid_l': 25, 'cea_cta_status': 'concordant', 'anesth_done': True, 'dos_site_marked': True},
        )
        self.client.post(reverse('preop-patient', args=[p_op.pk]), {'section': 'signoff', 'action': 'approve', 'anamnesis': 'მარჯვენა CEA ანამნეზი'})
        p_op.refresh_from_db()
        self.assertIsNotNone(p_op.approved_at)

        self.client.post(reverse('preop-patient', args=[p_op.pk]), {
            'section': 'header', 'patient_name': p_op.patient_name, 'age': '66', 'sex': 'M',
            'op_subtype': 'cea_asymp', 'side': 'L', 'surgeon': str(self.user.pk), 'visit_date': today.isoformat(),
        })
        p_op.refresh_from_db()
        self.assertIsNone(p_op.approved_at)
        self.assertEqual(p_op.anamnesis, '')
        self.assertFalse(merged_studies(p_op)['dos_site_marked'])

        # 2. L2-F4: Pre-workup HOLD before started_on does not poison 7-day preparation clock
        p_hold = PreopPatient.objects.create(
            patient_name='ჰოლდ საათაძე', age=70, sex='M', pathway='aaa_infra', op_subtype='open',
            someday_list='hold', started_on=None,
            studies={'hold_since': (today - datetime.timedelta(days=20)).isoformat()},
        )
        start_d = today - datetime.timedelta(days=10)
        self.client.post(reverse('preop-move', args=[p_hold.pk]), {'slot': start_d.isoformat()})
        p_hold.refresh_from_db()
        self.assertEqual(merged_studies(p_hold)['hold_paused_days'], 0)
        self.assertEqual(evaluate(p_hold, today=today)['days_elapsed'], 11)

        # 3. L2-F5: Saving Stage 2 when conditional rows are hidden preserves prior pbm_iv_iron_done & ppi_started
        p_s2 = PreopPatient.objects.create(
            patient_name='შენახვა სტეიჯაძე', age=70, sex='M', pathway='aaa_infra', op_subtype='open',
            visit_date=today, started_on=today,
            questionnaire={'filled': True, 'mets_ok': True},
            studies={'hb': 94, 'ferritin': 45, 'inr': 1.0, 'creat': 85, 'ecg': 'normal', 'lvef': 58, 'aaa_mm': 58, 'pbm_iv_iron_done': True, 'ppi_started': True},
        )
        self.client.post(reverse('preop-patient', args=[p_s2.pk]), {'section': 'stage2', 'gastro_result': 'remission', 'blood_reserved': '1', 'anesth_done': '1'})
        p_s2.refresh_from_db()
        self.assertTrue(merged_studies(p_s2)['pbm_iv_iron_done'])
        self.assertTrue(merged_studies(p_s2)['ppi_started'])

        # 4. L7-F2 + L7-F3 + L7-F4: Rotated self-fill GET hides prior medical PHI; clerk blocked from forging surgeon approve & deleting operated patient; PDF CSP sandbox
        p_sec = PreopPatient.objects.create(
            patient_name='უსაფრთხო აუდიტაძე', age=68, sex='M', pathway='aaa_infra', op_subtype='open',
            surgeon=self.user, visit_date=today, started_on=today,
            questionnaire={'filled': True, 'nurse_verified': False, 'other_meds': 'მეტადონი 40მგ', 'allergy_other': True, 'allergy_text': 'პენიცილინი ანაფილაქსია'},
            studies={'aaa_mm': 60, 'hb': 135, 'inr': 1.0, 'creat': 85, 'ecg': 'normal', 'lvef': 58},
        )
        self.client.post(reverse('preop-patient', args=[p_sec.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_sec.refresh_from_db()
        rot_html = anon.get(reverse('preop-self-fill', args=[make_self_fill_token(p_sec)])).content.decode('utf-8')
        self.assertNotIn('მეტადონი 40მგ', rot_html)
        self.assertNotIn('პენიცილინი ანაფილაქსია', rot_html)

        clerk_client.post(reverse('preop-patient', args=[p_sec.pk]), {'section': 'signoff', 'action': 'approve'})
        p_sec.refresh_from_db()
        self.assertIsNone(p_sec.approved_at)

        s_op = merged_studies(p_sec)
        s_op['case_outcome_status'] = 'operated'
        p_sec.studies = s_op
        p_sec.save()
        clerk_client.post(reverse('preop-move', args=[p_sec.pk]), {'action': 'delete'})
        p_sec.refresh_from_db()
        self.assertTrue(p_sec.archived)

        valid_pdf = b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n'
        self.client.post(reverse('preop-files', args=[p_sec.pk]), {
            'category': 'labs',
            'file': [SimpleUploadedFile('sec.pdf', valid_pdf, content_type='application/pdf')],
        })
        f_rec = PreopFile.objects.filter(patient=p_sec, is_pdf=True).first()
        r_pdf = self.client.get(reverse('preop-file', args=[f_rec.pk]))
        self.assertTrue('sandbox' not in r_pdf['Content-Security-Policy'] and "object-src 'self'" in r_pdf['Content-Security-Policy'])
        self.assertIn('inline;', r_pdf['Content-Disposition'])
        self.assertEqual(r_pdf['X-Frame-Options'], 'SAMEORIGIN')

"""Regression tests for Stage-4 Round-28 (v1.2.27) NLP, rAAA/ALI emergency, WIfI I=0, OAC dedup, MV+HIT Board & Plain-KA fixes."""
import datetime
import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.mdf.models import BoardEntry, PreopPatient, StaffProfile
from apps.mdf.preop import evaluate
from apps.mdf.preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _positive_clauses_text,
)
from apps.mdf.preop.meds_freetext_nlp import (
    _EN_NEG_FOLLOW_STOP,
    _EN_STOPPED_RE,
    _split_inline_boundaries,
    _split_stop_verb_clauses,
)
from apps.mdf.preop.plain_ka import scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, match_rule_for_text
from apps.mdf.preop.views.board import _build_handoff_tasks
from apps.mdf.staging.suggestions import compute as compute_staging

TODAY = datetime.date(2026, 11, 28)
OP_DATE = (TODAY + datetime.timedelta(days=10)).isoformat()


def _mk(subtype='open', *, age=66, sex='M', side='', q=None, s=None):
    base_q = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if subtype in ('evla', 'phlebectomy'):
        base_q.setdefault('ven_ceap', 'C2')
        side = side or 'R'
    elif subtype.startswith(('cea', 'bypass', 'pta', 'ali')):
        side = side or 'R'
    base_s = {
        'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 76, 'height_cm': 174, 'hb': 136, 'wbc': 6.8, 'plt': 230,
        'inr': 1.02, 'fibrinogen': 3.1, 'creat': 84, 'creat_unit': 'umol',
        'k': 4.2, 'na': 140, 'glucose': 5.3, 'albumin': 42, 'ldl': 1.2,
        'labs_date': TODAY.isoformat(), 'ecg': 'normal', 'cxr': 'normal',
        'blood_group': 'O(I) Rh+', 'blood_reserved': True, 'anesth_done': True,
        'planned_surgery_date': OP_DATE,
    }
    if subtype in ('open', 'evar', 'tbd'):
        base_s.update({'aaa_mm': 58, 'cta_date': TODAY.isoformat(), 'cta_disc': True, 'popliteal_screen_status': 'normal'})
    elif subtype in ('evla', 'phlebectomy'):
        base_s.update({'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 7.0, 'compression_ready': True, 'vte_prophylaxis_planned': True})
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
        'ali_pta': PreopPatient.Pathway.PAD_PTA, 'ali_open': PreopPatient.Pathway.PAD_BYPASS,
        'evla': PreopPatient.Pathway.VENOUS, 'phlebectomy': PreopPatient.Pathway.VENOUS,
    }
    sub_map = {
        'ali_pta': PreopPatient.OpSubtype.PTA_CLTI,
        'ali_open': PreopPatient.OpSubtype.BYPASS_CLTI,
    }
    return PreopPatient(
        patient_name='Test R28', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=sub_map.get(subtype, subtype),
        questionnaire=base_q, studies=base_s, started_on=TODAY, visit_date=TODAY,
    )


class PreopStressFixesRound28Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r28_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R28 ქირურგი', full_name='ტესტ R28 ქირურგი')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)

    def test_r28_nlp_pack_counts_paren_dash_and_off(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.27')
        for txt, want in (
            ('ვარფარინი 5მგ No.30', {'warfarin'}),
            ('ქსარელტო 20მგ No.28', {'doac'}),
            ('პლავიქსი 75მგ No.28', {'clopidogrel'}),
            ('warfarin 5mg No.30', {'warfarin'}),
            ('clopidogrel 75mg No 28', {'clopidogrel'}),
            ('კლექსანი 0.4მლ No.10', {'parenteral_oac'}),
            ('warfarin (not taking) clopidogrel 75mg', {'clopidogrel'}),
            ('warfarin (no) aspirin 100mg', {'aspirin'}),
            ('warfarin - stopped 5 days ago apixaban 5mg', {'doac'}),
            ('warfarin — stopped 5 days ago apixaban 5mg', {'doac'}),
            ('ვარფარინი - მოხსნილია 5 დღის წინ ელიქვისი 5მგ', {'doac'}),
            ('ვარფარინი — შეწყვეტილია 3 დღის წინ კლოპიდოგრელი 75მგ', {'clopidogrel'}),
            ('stopped 5 days ago warfarin apixaban 5mg', {'doac'}),
            ('მოხსნილია 5 დღის წინ ვარფარინი ელიქვისი 5მგ', {'doac'}),
            ('off warfarin for 5 days, on apixaban 5mg', {'doac'}),
            ('warfarin (off), apixaban 5mg', {'doac'}),
            ('warfarin (no longer taking), apixaban 5mg', {'doac'}),
            ('warfarin - no longer on, apixaban 5mg', {'doac'}),
            ('apixaban 5mg (not on), warfarin 5mg', {'warfarin'}),
        ):
            got = _detect_freetext_high_risk_meds(txt, q={})['matched_keys']
            self.assertEqual(got, want, f'Mismatch on {txt!r}: got={got!r}')

        for txt in (
            'warfarin (not taking)', 'warfarin - not taking', 'warfarin: not taking',
            'warfarin (off)', 'warfarin - off', 'off warfarin',
            'patient is off warfarin and clopidogrel',
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], set(), txt)

        for txt in (
            'heparin (not allergic)', 'heparin - not allergic', 'heparin (negative)',
            'heparin - negative', 'heparin allergy negative', 'HIT negative', 'penicillin (allergy absent)',
        ):
            self.assertEqual(_detect_freetext_allergies(txt), set(), txt)

    def test_r28_nlp_double_neg_tod_freq_allergy_garda_and_lexicon(self):
        for txt, want in (
            ('warfarin is not to be stopped', {'warfarin'}),
            ('clopidogrel is not to be held', {'clopidogrel'}),
            ('warfarin was not to be discontinued', {'warfarin'}),
            ('warfarin is not being stopped', {'warfarin'}),
            ('warfarin is not being held', {'warfarin'}),
            ('clopidogrel cannot be safely stopped', {'clopidogrel'}),
            ('warfarin should not be abruptly stopped', {'warfarin'}),
            ('patient is not off warfarin', {'warfarin'}),
            ('კლოპიდოგრელი არავითარ შემთხვევაში არ შეწყვიტოთ', {'clopidogrel'}),
            ('ვარფარინი არ შეწყვეტილია', {'warfarin'}),
            ('ვარფარინი არ მოხსნილია', {'warfarin'}),
            ('ქსარელტო 20მგ საღამოს კლოპიდოგრელი შეწყვეტილია', {'doac'}),
            ('ასპირინი 100მგ დილას ვარფარინი მოხსნილია', {'aspirin'}),
            ('ასპირინი 100მგ ყოველდღე ვარფარინი მოხსნილია', {'aspirin'}),
            ('clopidogrel 75mg morning warfarin stopped', {'clopidogrel'}),
            ('aspirin 100mg evening clopidogrel discontinued', {'aspirin'}),
            ('ქსარელტო 20მგ ვარფარინი არა', {'doac'}),
            ('არ იღებს ვარფარინს ასპირინის გარდა', {'aspirin'}),
            ('პაციენტმა შეწყვიტა ვარფარინი ასპირინის გარდა', {'aspirin'}),
            ('იღებს ყველა წამალს ვარფარინის და კლოპიდოგრელის გარდა', set()),
            ('ელიქვისი და პრადაქსა გარდა ვარფარინისა მოხსნილია', {'warfarin'}),
            ('ელიქვისი და კლექსანი ვარფარინის გარდა მოხსნილია', {'warfarin'}),
        ):
            got = _detect_freetext_high_risk_meds(txt, q={})['matched_keys']
            self.assertEqual(got, want, f'Mismatch on {txt!r}: got={got!r}')

        for txt in (
            'არ იღებს ვარფარინს, არც ასპირინს', 'არასდროს მიმიღია ვარფარინი',
            'ვარფარინს არ ვღებულობ', 'ვარფარინს არ იყენებს', 'პაციენტი არ იღებდა ვარფარინს',
            'neither warfarin nor clopidogrel', 'warfarin withheld', 'warfarin paused',
            'clopidogrel omitted', 'apixaban suspended',
            "patient couldn't tolerate warfarin", "patient shouldn't take warfarin",
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], set(), txt)

        for txt in (
            'პაციენტს ალერგია არ აქვს ჰეპარინზე', 'მე ალერგია არ მაქვს ჰეპარინზე',
            'პაციენტს ალერგია არ აქვს იოდზე', 'პაციენტს ალერგია არ აქვს პენიცილინზე და იოდზე',
            'ალერგია ჰეპარინზე და იოდზე არ აქვს', 'არ აღნიშნავს ალერგიას ჰეპარინზე და იოდზე',
            'არ ჰქონია ალერგია პენიცილინზე და ჰეპარინზე',
            'არ აქვს ალერგია პენიცილინზე, არც იოდზე', 'არასდროს მქონია ალერგია ჰეპარინზე',
            'neither heparin nor penicillin', 'HIT ruled out',
        ):
            self.assertEqual(_detect_freetext_allergies(txt), set(), txt)

        self.assertEqual(_detect_freetext_allergies('არ აქვს ალერგია პენიცილინზე იოდის გარდა'), {'iodine'})
        self.assertEqual(_detect_freetext_allergies('იოდზე ალერგია და პენიცილინზე არ აქვს'), {'iodine'})

    def test_r28_nlp_strict_linear_scaling_200k(self):
        unit_ka = 'არ აქვს ალერგია '
        s_50k = (unit_ka * (50000 // len(unit_ka) + 1))[:50000]
        s_200k = (unit_ka * (200000 // len(unit_ka) + 1))[:200000]
        t0 = time.perf_counter()
        _split_inline_boundaries(s_50k)
        t_50 = time.perf_counter() - t0
        t0 = time.perf_counter()
        _split_inline_boundaries(s_200k)
        t_200 = time.perf_counter() - t0
        self.assertLessEqual(t_200 / max(t_50, 1e-6), 6.0)

        t0 = time.perf_counter()
        _positive_clauses_text(s_50k)
        tp_50 = time.perf_counter() - t0
        t0 = time.perf_counter()
        _positive_clauses_text(s_200k)
        tp_200 = time.perf_counter() - t0
        self.assertLessEqual(tp_200 / max(tp_50, 1e-6), 6.0)

        u_en = 'a stopped '
        t0 = time.perf_counter()
        _split_stop_verb_clauses((u_en * 5000)[:50000], _EN_STOPPED_RE, _EN_NEG_FOLLOW_STOP, lambda h: False)
        ts_50 = time.perf_counter() - t0
        t0 = time.perf_counter()
        _split_stop_verb_clauses((u_en * 20000)[:200000], _EN_STOPPED_RE, _EN_NEG_FOLLOW_STOP, lambda h: False)
        ts_200 = time.perf_counter() - t0
        self.assertLessEqual(ts_200 / max(ts_50, 1e-6), 6.0)

    def test_r28_raaa_failsafes_and_emergency_hyperthyroid_nonblocking(self):
        # rAAA with profound shock / missing SpO2 / missing labs_date / unasked iodine allergy / hyperthyroid TSH must NOT deadlock approval/Board!
        p = _mk('evar', q={'aaa_pain': True, 'allergy_iodine_asked': False}, s={
            'aaa_mm': 78, 'bp_sys': 78, 'bp_dia': 48, 'hr': 122, 'spo2': None,
            'labs_date': '', 'tsh_status': 'hyperthyroid', 'planned_surgery_date': TODAY.isoformat(),
        })
        p.surgeon = self.doc
        p.save()
        ev = evaluate(p, today=TODAY)
        self.assertTrue(ev['is_rupture_suspect'])
        self.assertNotEqual(ev['clearance_seal']['code'], 'stop')
        self.assertTrue(any('ჰიპერთირეოზი' in rf for rf in ev['red_flags']))
        for rf in ev['red_flags']:
            self.assertIsNotNone(match_rule_for_text(rf), f'Unattributed red flag: {rf}')

        r_app = self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve'})
        self.assertEqual(r_app.status_code, 302)
        p.refresh_from_db()
        self.assertIsNotNone(p.approved_at)

        r_brd = self.c_doc.post(reverse('preop-admit-board', args=[p.pk]), {'unit': 'icu', 'bed': 'ICU-R28'})
        self.assertEqual(r_brd.status_code, 302)
        p.refresh_from_db()
        self.assertTrue(BoardEntry.objects.filter(patient_id=p.studies.get('board_patient_id')).exists())

        # ALI + hyperthyroid also non-STOP red flag; elective EVAR + hyperthyroid remains STOP
        p_ali = _mk('ali_pta', q={'abf_duration': '< 2 კვირა'}, s={'tsh_status': 'hyperthyroid', 'planned_surgery_date': TODAY.isoformat()})
        ev_ali = evaluate(p_ali, today=TODAY)
        self.assertTrue(ev_ali['is_acute_emergency'])
        self.assertNotEqual(ev_ali['clearance_seal']['code'], 'stop')
        self.assertTrue(any('ჰიპერთირეოზი' in rf for rf in ev_ali['red_flags']))

        p_el = _mk('evar', s={'tsh_status': 'hyperthyroid'})
        ev_el = evaluate(p_el, today=TODAY)
        self.assertEqual(ev_el['clearance_seal']['code'], 'stop')

    def test_r28_wifi_ischemia0_plan_none_and_oac_dedup_and_riva_units(self):
        for risk in ('high', 'avg'):
            for vein in ('yes', 'no'):
                out = compute_staging({'w': 3, 'i': 0, 'fi': 2, 'fp': 4, 'ip': 3, 'risk': risk, 'vein': vein})
                self.assertEqual(out['plan'], 'none', f'I=0 must yield plan=none for risk={risk}, vein={vein}')

        # Canonical _count_oac_agents: 2-drug OAC combos must count as 2 (NOT 3 / no false triple-therapy red flag)
        for q_case in (
            {'other_meds': 'პრადაქსა 150მგ, ქსარელტო 2.5 მგ'},
            {'med_doac': True, 'other_meds': 'ელიქვისი 5მგ, ქსარელტო 20მგ'},
        ):
            ev_d = evaluate(_mk('bypass_clti', q=q_case), today=TODAY)
            self.assertEqual(ev_d['oac_count'], 2, q_case)
            self.assertFalse(any('≥3 ანტითრომბოზულ' in rf for rf in ev_d['red_flags']), q_case)

        # Single DOAC (chip + same freetext apixaban) -> 1 OAC, no duplicate-OAC red flag
        ev2 = evaluate(_mk('bypass_claud', q={'med_doac': True, 'other_meds': 'apixaban 5mg'}), today=TODAY)
        self.assertEqual(ev2['oac_count'], 1)
        self.assertFalse(any('დუბლირებ' in rf for rf in ev2['red_flags']))

        # VOYAGER PAD rivaroxaban 2.5mg + Georgian tablets/capsules/x20/en-dash -> NOT dual-Xa
        for txt in ('ქსარელტო 2.5მგ 20 ტაბლეტი', 'ქსარელტო 2.5მგ 10 კაფსულა', 'ქსარელტო 2.5მგ x20', 'xarelto 2.5mg 10–20 days'):
            ft = _detect_freetext_high_risk_meds(txt, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertFalse(ft['has_ft_dual_xa'], txt)
            self.assertTrue(ft['has_ft_riva_low'], txt)

    def test_r28_mv_hit_liver3x_board_and_hit_parenteral_briefing_and_plain_ka(self):
        # Mechanical valve + HIT + liver_3x + DOAC -> Board MUST preserve fondaparinux/DOAC ban + bivalirudin/VKA + no-dual-AC
        p_mv = _mk('evar', q={'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'med_doac': True}, s={'liver_status': '3x'})
        ev_mv = evaluate(p_mv, today=TODAY)
        eff_ren = ev_mv.get('crcl') if ev_mv.get('crcl') is not None else ev_mv.get('egfr')
        tasks_mv = ' '.join(t for _, t in _build_handoff_tasks(ev_mv, p_mv.questionnaire, p_mv.studies, p_mv.pathway, set(ev_mv['eff_allergy_chips']), eff_ren))
        self.assertIn('ფონდაპარინუქსი/DOAC არა!', tasks_mv)
        self.assertIn('ბივალირუდინი', tasks_mv)
        self.assertIn('ვარფარინი (VKA)', tasks_mv)
        self.assertIn('ორმაგი ანტიკოაგულაციის გარეშე', tasks_mv)

        # HIT + freetext parenteral (enoxaparin / fondaparinux / argatroban) -> surgical_brief.heparin_plan aligned with Caprini & Board
        for med_txt in ('clexane 0.4ml', 'fondaparinux 2.5mg', 'argatroban infusion'):
            p_hp = _mk('open', q={'allergy_chips': ['hit_heparin'], 'other_meds': med_txt})
            ev_hp = evaluate(p_hp, today=TODAY)
            hep = ev_hp['surgical_brief']['heparin_plan']
            self.assertIn('ორმაგი ანტიკოაგულაციის გარეშე', hep)
            self.assertIn('ერთიანი არაჰეპარინული სქემა', hep)
            self.assertNotIn('პოსტოპ. ფონდაპარინუქსი / არგატრობანი / ბივალირუდინი!', hep)

        # Plain-KA citation scrubbing (ASRA 2025, ESVS 2023-ით, ESAIC 2025, ISTH 2021, AAAAI 2022, IDSA 2019, ASA 2023/2024, ASH 2018)
        samples = (
            'ნეირაქსიული ბლოკადა ASRA 2025 გაიდლაინით (ISTH 2021)',
            'ESVS 2023-ით აუცილებელია დაკვირვება; ESAIC 2025 რეკომენდაციით',
            'AAAAI 2022 გაიდლაინით ცეფაზოლინი დასაშვებია; IDSA 2019; ASH 2018; (ASA 2024); (ASA 2023)',
        )
        for src in samples:
            cleaned = scrub_plain_ka(src)
            for leak in ('ASRA', 'ESVS', 'ESAIC', 'ISTH', 'AAAAI', 'IDSA', 'ASA', 'ASH', '2025', '2024', '2023', '2022', '2021', '2019', '2018'):
                self.assertNotIn(leak, cleaned, f'Leak {leak!r} in {cleaned!r}')
            self.assertTrue(all(ord(c) <= 0xFFFF for c in cleaned))

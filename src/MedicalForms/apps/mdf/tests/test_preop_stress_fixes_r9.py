"""Stage 4 Round-9 regression tests covering ALL Category (a) S1/S2/S3 findings from Round-8 3-agent re-verification."""
import datetime

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardEntry, PreopPatient, StaffProfile
from ..preop import (
    admit_preop_to_board,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
    nurse_plan,
    nurse_ui,
)
from ..preop.anamnesis import build_self_fill_highlights
from ..preop.calculators import _parse_plt_k
from ..preop.forms import apply_questionnaire_post, apply_self_fill_post
from ..preop.meds import _detect_freetext_high_risk_meds, _effective_allergy_state
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, STATUS_LOCAL, match_rule_for_text
from ..preop.utils import cta_live_months_age, is_valid_birth_date

TODAY = datetime.date(2026, 11, 26)
FUTURE_OP = (TODAY + datetime.timedelta(days=10)).isoformat()


def _go(sub='open', **kw):
    q = {'filled': True, 'nurse_verified': True, 'allergy_iodine_asked': True}
    q.update(kw.pop('q', {}))
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    s = {
        'labs_date': TODAY.isoformat(),
        'bp_sys': 125, 'bp_dia': 78, 'hr': 72, 'spo2': 98,
        'weight_kg': 80, 'height_cm': 175,
        'hb': 135, 'plt': 220, 'inr': 1.05, 'creat': 85, 'creat_unit': 'umol',
        'glucose': 5.4, 'k': 4.2, 'na': 140, 'ecg': 'normal', 'cxr': 'normal',
        'lvef': 58, 'aaa_mm': 58 if pw == 'aaa_infra' else None, 'cta_disc': True, 'aaa_neck_status': 'friendly_ifu',
        'aaa_iliac_access': 'ok_7mm', 'popliteal_screen_status': 'normal',
        'carotid_r': 75 if pw == 'cea' else None, 'carotid_l': 20 if pw == 'cea' else None, 'cea_cta_status': 'concordant',
        'brain_imaging': 'normal_small', 'abi_r': 1.05 if pw == 'venous' else 0.55, 'abi_l': 0.95,
        'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'patent',
        'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate', 'pad_gsv_mm': 4.0,
        'pta_lesion_site': 'femoropopliteal', 'pta_access_site': 'cfa_clean',
        'ven_duplex_status': 'deep_patent_reflux', 'blood_group': 'A+',
        'blood_reserved': True, 'evar_sized': True, 'decolonization_ready': True,
        'dapt_ready': True, 'compression_ready': True, 'vte_prophylaxis_planned': True,
        'med_hold_agreed': True, 'anesth_done': True, 'dos_bhcg_negative': True,
        'planned_surgery_date': FUTURE_OP,
    }
    s.update(kw.pop('s', {}))
    side = kw.pop('side', 'R' if pw in ('cea', 'pad_bypass', 'pad_pta', 'venous') else '')
    return PreopPatient(
        patient_name=kw.pop('patient_name', 'R9 Test'),
        age=kw.pop('age', 65),
        sex=kw.pop('sex', 'M'),
        side=side,
        op_subtype=sub,
        pathway=pw,
        cardio_decision=kw.pop('cardio_decision', 'cleared'),
        questionnaire=q,
        studies=s,
        **kw,
    )


class Round9ClinicalEngineTests(SimpleTestCase):
    """Engine & calculator regression tests for all Round-8 S1/S2/S3 clinical findings."""

    def test_s1_cta_live_months_age_strictly_monotonic_every_day(self):
        # Fuzz A-S1-1 & Clin S2-1: cta_live_months_age must NEVER drop backwards across month boundaries
        for cta_str in ('2025-09-20', '2025-11-27', '2024-02-29', '2026-01-31', '2026-01-15', '2025-12-20'):
            start = datetime.date.fromisoformat(cta_str)
            prev = 0
            for d in range(0, 420):
                cur = start + datetime.timedelta(days=d)
                m = cta_live_months_age({'cta_date': cta_str}, today=cur)
                self.assertGreaterEqual(m, prev, f'cta={cta_str} dropped {prev}->{m} on {cur}')
                prev = m
        # Exact stale-CTA preservation across Mar 31 -> Apr 1
        p = _go('open', s={'cta_date': '2025-09-20', 'labs_date': '2026-04-01', 'planned_surgery_date': '2026-04-15'})
        ev_31 = evaluate(p, today=datetime.date(2026, 3, 31))
        ev_01 = evaluate(p, today=datetime.date(2026, 4, 1))
        self.assertGreaterEqual(ev_01['cta_months'], ev_31['cta_months'])
        self.assertGreater(ev_01['cta_months'], 6)
        self.assertFalse(ev_01['workup_ready'])
        self.assertNotEqual(ev_01['clearance_seal']['code'], 'go')
        # Giant AAA >3m stays stale on Mar 1
        self.assertGreater(cta_live_months_age({'cta_date': '2025-11-27'}, today=datetime.date(2026, 3, 1)), 3)
        # Jan 31 -> Aug 28 (6m 28d) is >6m stale
        self.assertGreater(cta_live_months_age({'cta_date': '2026-01-31'}, today=datetime.date(2026, 8, 28)), 6)

    def test_s1_local_anesthetic_allergy_blocks_neuraxial_epidural_and_auto_phleb(self):
        # Clin S1-1 & Combos A-S1-1: LA allergy (marcain/bupivacaine/lidocaine) MUST block neuraxial & epidural
        ev_byp = evaluate(_go('bypass_clti', q={'allergy_text': 'marcain'}, s={'anesthesia_plan': 'neuraxial'}), today=TODAY)
        self.assertTrue(ev_byp['neuraxial_hard_blocked'])
        self.assertNotIn('✓', ev_byp['anesthetic_brief']['neuraxial_gate'])
        self.assertTrue(any('ადგილობრივ ანესთეტიკ' in rf or 'ლიდოკაინ' in rf for rf in ev_byp['red_flags']))

        ev_epi = evaluate(_go('open', q={'allergy_chips': ['lidocaine']}, s={'anesth_lines_chips': ['epidural']}), today=TODAY)
        self.assertTrue(ev_epi['neuraxial_hard_blocked'])
        self.assertNotIn('✓', ev_epi['anesthetic_brief']['neuraxial_gate'])

        ev_ph_nx = evaluate(_go('phlebectomy', q={'ven_lidocaine_allergy': True}, s={'anesthesia_plan': 'neuraxial'}), today=TODAY)
        self.assertTrue(ev_ph_nx['neuraxial_hard_blocked'])
        self.assertTrue(ev_ph_nx['has_hard_contraindication'])
        self.assertEqual(ev_ph_nx['clearance_seal']['code'], 'stop')

        ev_ev_nx = evaluate(_go('evla', q={'ven_lidocaine_allergy': True}, s={'anesthesia_plan': 'neuraxial'}), today=TODAY)
        self.assertTrue(ev_ev_nx['neuraxial_hard_blocked'])
        self.assertTrue(ev_ev_nx['has_hard_contraindication'])
        self.assertEqual(ev_ev_nx['clearance_seal']['code'], 'stop')

        # Auto phlebectomy + LA allergy switches to GA and MUST NOT retain has_neuraxial_plan=True or green ASRA
        ev_ph_auto = evaluate(_go('phlebectomy', q={'ven_lidocaine_allergy': True}, s={'anesthesia_plan': 'auto'}), today=TODAY)
        self.assertFalse(ev_ph_auto['has_neuraxial_plan'])
        self.assertNotIn('ASRA 5th Ed კრიტერიუმები დაცულია ✓', ev_ph_auto['anesthetic_brief']['neuraxial_gate'])

    def test_s1_segluromet_96h_metformin_unknown_creat_crescendo_bridging_and_ticagrelor_riva25(self):
        # Clin S1-3: Segluromet / სეგლურომეტი & Steglujan receive 96h (4d) ertugliflozin hold
        for brand in ('Segluromet 2.5/1000', 'სეგლურომეტი 2.5/1000', 'Steglujan 5/100'):
            ft = _detect_freetext_high_risk_meds(brand, q={})
            self.assertTrue(ft['ertugliflozin'], brand)
            self.assertIn('sglt2', ft['matched_keys'], brand)
            ev_s = evaluate(_go('open', q={'sglt2': True, 'metformin': True, 'other_meds': brand}), today=TODAY)
            sglt_row = next(r for r in ev_s['med_schedule'] if 'SGLT2' in r['drug'])
            self.assertEqual(sglt_row['mandatory_hold_days'], 4, brand)
            self.assertIn('96 სთ', sglt_row['date_plan'], brand)

        # Clin S1-4: Metformin MUST NOT get tone='ok' when creatinine is missing or ambiguous (creat=25, unit='')
        for sub in ('evla', 'cea_asymp', 'phlebectomy'):
            for sx in ({'creat': 25, 'creat_unit': ''}, {'creat': None, 'creat_unit': ''}):
                ev_m = evaluate(_go(sub, q={'metformin': True, 'dm': True}, s=sx), today=TODAY)
                m_row = next(r for r in ev_m['med_schedule'] if 'მეტფორმინ' in r['drug'])
                self.assertEqual(m_row['tone'], 'red', f'{sub} {sx}')
                self.assertTrue(m_row.get('reconcile_only'), f'{sub} {sx}')
                self.assertNotIn('48 სთ შეჩერება არ ესაჭიროება', m_row['rule'], f'{sub} {sx}')

        # Clin S1-5 & Combos A-S2-4: Active Crescendo TIA + old event date (200d) bridges Warfarin AFib & does not flag asymptomatic
        ev_cr = evaluate(
            _go('cea_symp', q={
                'cea_crescendo': True, 'cea_symp_event': True, 'cea_days_since_event': 200,
                'med_warfarin': True, 'cad_chips': ['afib'],
            }),
            today=TODAY,
        )
        vka_row = next(r for r in ev_cr['med_schedule'] if 'ვარფარინ' in r['drug'])
        self.assertIn('LMWH ხიდი', vka_row['rule'])
        self.assertNotIn('არ ტარდება', vka_row['rule'])
        self.assertFalse(any('ასიმპტომური სტენოზი' in af for af in ev_cr['amber_flags']))
        self.assertFalse(any('14-დღიანი ოპტიმალური ფანჯარა' in rf for rf in ev_cr['red_flags']))
        self.assertFalse(any('14-დღიან ფანჯარას' in af for af in ev_cr['amber_flags']))

        # Combos A-S1-2: Rivaroxaban 2.5 mg + clopidogrel_intol + dapt/voyager must NEVER recommend ticagrelor
        for sub in ('pta_claud', 'bypass_claud'):
            ev_rd = evaluate(
                _go(sub, q={'med_aspirin': True, 'med_chips': ['rivaroxaban_2_5'], 'allergy_chips': ['clopidogrel_intol']},
                    s={'post_antithrombotic_plan': 'dapt'}),
                today=TODAY,
            )
            self.assertTrue(any('რივაროქსაბან' in rf or 'სამმაგი თერაპიის' in rf for rf in ev_rd['red_flags']), sub)
            self.assertNotIn('DAPT: ასპირინი + ტიკაგრელორი', ev_rd['surgical_brief']['postop_antithrombotic'], sub)
            self.assertFalse(any('გამოიყენეთ ალტერნატიული P2Y12 (ტიკაგრელორი)' in af for af in ev_rd['amber_flags']), sub)

        ev_pv = evaluate(
            _go('pta_claud', q={'med_aspirin': True, 'allergy_chips': ['clopidogrel_intol']}, s={'post_antithrombotic_plan': 'voyager'}),
            today=TODAY,
        )
        self.assertFalse(any('შეათანხმეთ ალტერნატიული P2Y12 (ტიკაგრელორი)' in rf for rf in ev_pv['red_flags']))
        self.assertTrue(any('აკრძალულია' in rf and 'ტიკაგრელორი' in rf for rf in ev_pv['red_flags']))

    def test_s1_child_chips_blindness_forms_and_stale_abf_wet_infection(self):
        # Combos A-S1-3 & A-S3-3: Direct evaluation with parent=False + child chips must NOT be blind
        ev_dvt = evaluate(_go('evla', q={'ven_dvt_history': False, 'ven_dvt_chips': ['vte_recent_3m']}), today=TODAY)
        self.assertEqual(ev_dvt['effective_asa'], 'ASA III')
        anam_dvt = generate_anamnesis(_go('evla', q={'ven_dvt_history': False, 'ven_dvt_chips': ['vte_recent_3m']}), ev_dvt)
        self.assertNotIn('DVT/PE-ს, მწვავე თრომბოფლებიტსა და თრომბოფილიას უარყოფს', anam_dvt)
        self.assertIn('DVT/PE', anam_dvt)

        ev_neck = evaluate(_go('cea_asymp', q={'cea_prior_neck': False, 'cea_neck_chips': ['contra_cea']}), today=TODAY)
        self.assertTrue(ev_neck['needs_laryngoscopy'])
        self.assertIn('წინა ჩარევა', generate_anamnesis(_go('cea_asymp', q={'cea_prior_neck': False, 'cea_neck_chips': ['contra_cea']}), ev_neck))

        ev_ulc = evaluate(_go('bypass_claud', q={'abf_ulcer': False, 'ulcer_chips': ['deep_bone']}), today=TODAY)
        self.assertTrue(ev_ulc['needs_wound_care'])
        self.assertIn('წყლული', generate_anamnesis(_go('bypass_claud', q={'abf_ulcer': False, 'ulcer_chips': ['deep_bone']}), ev_ulc))

        ev_copd = evaluate(_go('open', q={'pulm': False, 'pulm_chips': ['copd']}), today=TODAY)
        self.assertTrue(ev_copd['needs_spirometry'])

        ev_str = evaluate(_go('cea_asymp', q={'cea_symp_event': False, 'cea_event_chips': ['minor_stroke']}), today=TODAY)
        self.assertTrue(ev_str['is_symp_cea'])
        self.assertTrue(ev_str['needs_neuro'])
        anam_str = generate_anamnesis(_go('cea_asymp', q={'cea_symp_event': False, 'cea_event_chips': ['minor_stroke']}), ev_str)
        self.assertIn('ნევროლოგიური სტატუსი: სიმპტომური', anam_str)
        self.assertNotIn('ინსულტს უარყოფს', anam_str)

        ev_abdo = evaluate(_go('open', q={'prev_abdo_surg': False, 'abdo_chips': ['midline_lap']}), today=TODAY)
        self.assertIn('მუცლის/საზარდულის ოპერაციები', generate_anamnesis(_go('open', q={'prev_abdo_surg': False, 'abdo_chips': ['midline_lap']}), ev_abdo))

        ev_rec = evaluate(_go('evla', q={'ven_recurrent': False, 'ven_rec_chips': ['prev_evla']}), today=TODAY)
        self.assertIn('რეციდივული ვარიკოზი', generate_anamnesis(_go('evla', q={'ven_recurrent': False, 'ven_rec_chips': ['prev_evla']}), ev_rec))

        # build_self_fill_highlights must not ignore child chips when parent=False
        self.assertTrue(build_self_fill_highlights({'filled': True, 'cad': False, 'cad_chips': ['hf']}))
        self.assertTrue(build_self_fill_highlights({'filled': True, 'pulm': False, 'pulm_chips': ['copd']}))
        self.assertTrue(build_self_fill_highlights({'filled': True, 'ven_dvt_history': False, 'ven_dvt_chips': ['vte_recent_3m']}))

        # Clin S1-2 & Combos A-S1-4: Tab 1 POST must NOT wipe cea_crescendo/cea_thrombolysis on cea_asymp
        p_cea = _go('cea_asymp', q={'filled': False})
        apply_questionnaire_post(QueryDict('cea_symp_event=0&cea_crescendo=1&cea_thrombolysis=1&mets_ok=1'), p_cea, 'Nurse A')
        self.assertTrue(p_cea.questionnaire['cea_crescendo'])
        self.assertTrue(p_cea.questionnaire['cea_thrombolysis'])
        ev_c2 = evaluate(p_cea, today=TODAY)
        self.assertTrue(ev_c2['is_symp_cea'])
        self.assertEqual(ev_c2['priority_tier'], 'urgent')

        # Combos A-S1-3: apply_self_fill_post must promote parents from child chips (not overwrite back to False)
        p_sf = _go('evla', q={'filled': False})
        apply_self_fill_post(QueryDict('ven_dvt_history=0&ven_dvt_chips=vte_recent_3m&mets_ok=1'), p_sf)
        self.assertTrue(p_sf.questionnaire['ven_dvt_history'])

        # Combos A-S1-5: Stale abf_wet_infection=True MUST NOT leak to AAA / CEA / EVLA
        ev_aaa_stale = evaluate(
            _go('open', q={'abf_wet_infection': True, 'dental_infection': True}, s={'hb': 95, 'ferritin': 12}),
            today=TODAY,
        )
        self.assertNotEqual(ev_aaa_stale['priority_tier'], 'urgent')
        t_keys = {t['key'] for t in ev_aaa_stale['tasks']}
        self.assertIn('dental', t_keys)
        self.assertIn('gastro', t_keys)
        self.assertTrue(ev_aaa_stale['pbm_iron_required'])
        self.assertNotEqual(evaluate(_go('evla', q={'abf_wet_infection': True}), today=TODAY)['priority_tier'], 'urgent')

    def test_s2_voyager_plt_creat_none_elapsed_compliant_bridging_donor_tbi_and_rupture(self):
        # Fuzz A-S2-2 & Clin S2-4: voyager_safe MUST be False when creatinine is unknown/ambiguous/mismatched/saturated
        for sx, age_v in (
            ({'creat': 20, 'creat_unit': '', 'post_antithrombotic_plan': 'voyager'}, 65),
            ({'creat': 88, 'creat_unit': 'mgdl', 'post_antithrombotic_plan': 'auto'}, 65),
            ({'creat': 250, 'creat_unit': 'umol', 'post_antithrombotic_plan': 'voyager'}, 125),
            ({'creat': None, 'creat_unit': '', 'post_antithrombotic_plan': 'auto'}, 65),
        ):
            ev_v = evaluate(_go('bypass_claud', age=age_v, s=sx), today=TODAY)
            if sx['post_antithrombotic_plan'] == 'voyager':
                self.assertTrue(any('VOYAGER' in rf for rf in ev_v['red_flags']), f'Missed VOYAGER RF: {sx}')
            else:
                self.assertNotIn('VOYAGER PAD (რივაროქსაბანი', ev_v['post_antithrombotic_label'], f'Auto VOYAGER on {sx}')

        # Fuzz A-S2-3: PLT 2501..2999 must return None in _parse_plt_k (not fabricated PLT=3 -> ASA IV)
        for pv in (2501, 2600, 2999):
            self.assertIsNone(_parse_plt_k(pv))
        ev_plt = evaluate(_go('evla', age=45, s={'plt': None, '_invalid_inputs': {'plt': '2600'}}), today=TODAY)
        self.assertNotEqual(ev_plt['effective_asa'], 'ASA IV')
        self.assertFalse(ev_plt['needs_icu_bed'])

        # Fuzz A-S2-4: Out-of-range creat unit mismatch must NOT render literal "None" in red flags
        ev_cn = evaluate(_go('open', s={'creat': None, 'creat_unit': 'mgdl', '_invalid_inputs': {'creat': '5000'}}), today=TODAY)
        self.assertFalse(any('(None ' in rf for rf in ev_cn['red_flags']))
        self.assertTrue(any('5000' in rf for rf in ev_cn['red_flags']))

        # Clin S2-2: Compliant approved Warfarin patient actively holding on D-4 MUST NOT be evicted from ready
        d_call = datetime.date(2026, 12, 1)
        d_hold = datetime.date(2026, 12, 7)
        d_op = datetime.date(2026, 12, 11)
        p_comp = _go('open', q={'med_warfarin': True}, s={'labs_date': d_hold.isoformat(), 'planned_surgery_date': d_op.isoformat(), 'med_hold_agreed': True})
        ev_d1 = evaluate(p_comp, today=d_call)
        p_comp.studies['nurse_med_called_at'] = '01.12.2026 12:30'
        p_comp.studies['nurse_med_called_sig'] = nurse_ui.med_schedule_signature(ev_d1)
        p_comp.studies['med_hold_agreed_date'] = d_call.isoformat()
        p_comp.approved_at = timezone.make_aware(datetime.datetime(2026, 12, 1, 13, 0))
        p_comp.alerts_ack_at = p_comp.approved_at
        p_comp.studies['alerts_ack_sig'] = ev_d1['alerts_sig']
        ev_d4 = evaluate(p_comp, today=d_hold)
        self.assertFalse(ev_d4['has_elapsed_hold_window'])
        self.assertEqual(ev_d4['status'], 'ready')
        self.assertTrue(ev_d4['workup_ready'])
        # Whereas uncounseled patient on D-4 MUST still trigger has_elapsed_hold_window=True
        p_un = _go('open', q={'med_warfarin': True}, s={'labs_date': d_hold.isoformat(), 'planned_surgery_date': d_op.isoformat(), 'med_hold_agreed': False})
        self.assertTrue(evaluate(p_un, today=d_hold)['has_elapsed_hold_window'])

        # Clin S2-3: Warfarin bridging includes ven_duplex_status='acute_dvt' on non-venous pathway
        ev_dvt_vka = evaluate(_go('open', q={'med_warfarin': True}, s={'ven_duplex_status': 'acute_dvt'}), today=TODAY)
        vka_r = next(r for r in ev_dvt_vka['med_schedule'] if 'ვარფარინ' in r['drug'])
        self.assertIn('LMWH ხიდი', vka_r['rule'])

        # Clin S2-5: PAD Bypass donor-limb guard checks contra_tbi (<0.30 RED)
        ev_dtbi = evaluate(
            _go('bypass_clti', side='R', s={
                'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'contra_gsv_ok',
                'abi_r': 0.35, 'abi_l': 1.60, 'tp_l': None, 'tbi_l': 0.20,
            }),
            today=TODAY,
        )
        self.assertTrue(any('კონტრალატერალურ კიდურზე მძიმე არტერიული იშემიაა' in rf for rf in ev_dtbi['red_flags']))

        # Clin S2-6: Dialysis Dabigatran sets mandatory_hold_days and triggers elapsed hold on D-2
        op_2d = (TODAY + datetime.timedelta(days=2)).isoformat()
        ev_ddab = evaluate(_go('open', q={'med_dabigatran': True, 'on_dialysis': True}, s={'planned_surgery_date': op_2d}), today=TODAY)
        self.assertTrue(ev_ddab['has_elapsed_hold_window'])

        # Clin S2-7: Ruptured AAA suppresses outpatient reflex_orders; urgent symp/mycotic AAA/wet ABF align dental reflex/task
        ev_rup = evaluate(
            _go('open', q={'aaa_pain': True, 'thyroid_disease': True, 'inf_chips': ['dysuria']},
                s={'aaa_mm': 78, 'bp_sys': 80, 'bp_dia': 50, 'hr': 118, 'hb': 92, 'creat': 190, 'glucose': 12.5, 'abi_r': 1.55}),
            today=TODAY,
        )
        self.assertTrue(ev_rup['is_rupture_suspect'])
        ro_keys_rup = {r['key'] for r in ev_rup['reflex_orders']}
        self.assertFalse(ro_keys_rup & {'tbi', 'anemia', 'nephro', 'hba1c', 'endo', 'tsh', 'uti'})

        ev_myc = evaluate(_go('open', q={'dental_infection': True}, s={'aaa_morphology': 'inflammatory_mycotic'}), today=TODAY)
        self.assertFalse(any(r['key'] == 'dental' for r in ev_myc['reflex_orders']))
        self.assertFalse(any('სავალდებულოა სტომატოლოგის სანაცია' in rf for rf in ev_myc['red_flags']))
        self.assertTrue(any('სტომატოლოგიური' in af for af in ev_myc['amber_flags']))

    def test_s2_s3_postpone_cta_cyp2c19_ft_preg_dos_dm_metf_hb_ambig_and_registry(self):
        # Clin S2-8: detect_postponement_impacts respects Giant AAA 3-month CTA ceiling
        p_g = _go('open', s={
            'aaa_mm': 72,
            'cta_date': (TODAY - datetime.timedelta(days=135)).isoformat(),
            'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=5)).isoformat(),
            'planned_surgery_date': (TODAY + datetime.timedelta(days=20)).isoformat(),
        })
        imp = nurse_plan.detect_postponement_impacts(p_g, evaluate(p_g, today=TODAY), TODAY)
        self.assertTrue(any('CTA' in i['text'] or 'კტ-ანგიოგრაფია' in i['text'] for i in imp['impacts']))

        # Combos A-S2-5 (engine part): Free-text Georgian brands trigger CYP2C19 amber alert
        ev_ft_cyp = evaluate(_go('pta_claud', q={'other_meds': 'ლოპირელი 75, ემანერა 40'}), today=TODAY)
        self.assertTrue(any('CYP2C19' in af for af in ev_ft_cyp['amber_flags']))

        # Combos A-S2-6: Pregnant acute emergency achieves dos_ready=True without checking dos_bhcg_negative
        p_em = _go('pta_clti', sex='F', age=34, q={'pregnant_possible': True, 'abf_duration': '< 2 კვირა'}, s={'dos_bhcg_negative': False})
        ev_em0 = evaluate(p_em, today=TODAY)
        self.assertFalse(any(c['key'] == 'dos_bhcg_negative' for c in ev_em0['dos_checks']))
        for chk in ev_em0['dos_checks']:
            p_em.studies[chk['key']] = True
        p_em.alerts_ack_at = timezone.now()
        p_em.studies['alerts_ack_sig'] = ev_em0['alerts_sig']
        ev_em1 = evaluate(p_em, today=TODAY)
        self.assertTrue(ev_em1['dos_ready'])

        # Combos A-S2-7: dm=False + metformin=True behaves identically to dm=True across PAD TBI, EVLA ABI/ECG/ASA, glycemic corridor
        ev_dm_clti = evaluate(_go('bypass_clti', q={'dm': False, 'metformin': True}, s={'abi_r': 1.15}), today=TODAY)
        self.assertTrue(ev_dm_clti['needs_tbi_check'])
        ev_dm_evla = evaluate(_go('evla', age=42, q={'dm': False, 'metformin': True, 'ven_ceap': 'C5'}), today=TODAY)
        self.assertEqual(ev_dm_evla['effective_asa'], 'ASA II')
        self.assertTrue(ev_dm_evla['needs_abi_check'])
        self.assertTrue(ev_dm_evla['needs_ecg_venous'])
        ev_dm_aaa = evaluate(_go('open', q={'dm': False, 'metformin': True}, s={'glucose': 6.5}), today=TODAY)
        self.assertIn('5.5–10.0', ev_dm_aaa['anesthetic_brief']['glycemic_target'])

        # Combos A-S2-8: STOP-locked routine reconcile_only meds MUST NOT enter urgent_meds
        p_stp = _go('evla', sex='F', age=65, q={'med_doac': True, 'glp1': True, 'ven_hormones': True}, s={'abi_r': 0.50})
        guide = nurse_plan.build_patient_plain_explanation(p_stp, evaluate(p_stp, today=TODAY), TODAY)
        self.assertTrue(guide['stop_locked'])
        self.assertEqual(guide['urgent_meds'], [])
        # Pregnancy + DOAC & hormones ARE genuinely contraindicated (Round-10/11) -> both enter urgent_meds, glp1 stays out
        p_preg_stp = _go('evla', sex='F', age=30, q={'pregnant_possible': True, 'med_doac': True, 'glp1': True, 'ven_hormones': True})
        guide_p = nurse_plan.build_patient_plain_explanation(p_preg_stp, evaluate(p_preg_stp, today=TODAY), TODAY)
        self.assertTrue(guide_p['stop_locked'])
        self.assertEqual(
            [m['raw_drug'] for m in guide_p['urgent_meds']],
            ['DOAC (ქსარელტო / ელიქვისი)', 'ჰორმონული პრეპარატი / კონტრაცეპტივი'],
        )

        # Combos A-S2-9: Hb 22.1..39.9 emits explicit unit-clarification alert on server
        ev_hb23 = evaluate(_go('open', s={'hb': 23}), today=TODAY)
        self.assertTrue(any('ერთეული' in f and '23' in f for f in ev_hb23['red_flags'] + ev_hb23['amber_flags']))
        self.assertIsNotNone(match_rule_for_text(next(f for f in ev_hb23['red_flags'] + ev_hb23['amber_flags'] if 'ერთეული' in f and '23' in f)))

        # Combos A-S2-3 (Part A): Contrast PTA with allergy_iodine_asked=False MUST NOT get green GO seal
        ev_unask = evaluate(_go('pta_clti', q={'allergy_iodine_asked': False}), today=TODAY)
        self.assertTrue(ev_unask['has_blocking_unknown_state'])
        self.assertNotEqual(ev_unask['clearance_seal']['code'], 'go')

        # Clin S3-1: EVLA + cilostazol + missing creat requires creat in S1
        ev_cilo_ev = evaluate(_go('evla', q={'med_chips': ['cilostazol']}, s={'creat': None}), today=TODAY)
        self.assertFalse(ev_cilo_ev['s1_core_done'])
        self.assertIn('creat', ev_cilo_ev['s1_required_fields'])

        # Clin S3-2: Age>=65 + steroid + prasugrel + stroke -> needs_ppi=True matching med_schedule PPI row
        ev_pr_ppi = evaluate(_go('open', age=70, q={'med_chips': ['steroid'], 'med_prasugrel': True, 'neuro': True}), today=TODAY)
        self.assertTrue(ev_pr_ppi['needs_ppi'])

        # Combos A-S3-1: Undated patient guide must NOT expose raw clinician jargon (EHRA/PAUSE/CrCl/Multi-Society/D+1 LMWH)
        p_nodt = _go('open', q={'med_doac': True, 'glp1': True, 'med_warfarin': True}, s={'planned_surgery_date': '', 'creat': 160, 'weight_kg': 60})
        g_nodt = nurse_plan.build_patient_plain_explanation(p_nodt, evaluate(p_nodt, today=TODAY), TODAY)
        for m in g_nodt['stop_meds'] + g_nodt['caution_meds'] + g_nodt['keep_meds']:
            for jargon in ('EHRA', 'PAUSE', 'CrCl', 'Multi-Society', 'D+1', 'LMWH', 'კონსილიუმის'):
                self.assertNotIn(jargon, m['spoken'], f'Jargon {jargon} in undated guide: {m}')

        # Combos A-S3-2: Anamnesis with cad=False + lvef=30% must NOT say 'მნიშვნელოვანი ქრონიკული დაავადებების გარეშე'
        p_ef = _go('open', q={'cad': False}, s={'lvef': 30})
        anam_ef = generate_anamnesis(p_ef, evaluate(p_ef, today=TODAY))
        self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', anam_ef)

        # Combos A-S3-4: Georgian Rocephin ('როცეფინი') detected as beta_lactam
        chips_roc, _, _ = _effective_allergy_state({'allergy_text': 'როცეფინი'})
        self.assertIn('beta_lactam', chips_roc)

        # Fuzz A-S3-3: is_valid_birth_date rejects age 0 (today / newborn) matching _age_from_dob
        self.assertFalse(is_valid_birth_date(TODAY, today=TODAY))
        self.assertTrue(is_valid_birth_date(datetime.date(2000, 1, 1), today=TODAY))

        # Clin S3-3: Registry v1.2.8 honest metadata
        self.assertGreaterEqual(tuple(int(x) for x in REGISTRY_VERSION.split('.')), (1, 2, 8))
        self.assertEqual(RULES_BY_ID['MED_GLP1_FASTING_PROTOCOL'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['LAB_NUTRITION_ALBUMIN_LIVER'].status, STATUS_LOCAL)
        self.assertIn('min(eGFR, CrCl)', RULES_BY_ID['SCORE_RENAL_CKDEPI_CG'].source_wording)


class Round9HttpWorkflowAndBoardTests(TestCase):
    """End-to-end HTTP, Board re-handoff, and cross-surface parity tests for Round-8 findings."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(username='r9doc', password='R9DocPassword!1', first_name='ნინო', last_name='ქირურგი', is_staff=True)
        StaffProfile.objects.create(user=cls.doc, display_name='ნინო ქირურგი')
        cls.nurse = User.objects.create_user(username='r9nurse', password='R9NursePassword!1', first_name='მარი', last_name='ექთანი')
        StaffProfile.objects.create(user=cls.nurse, display_name='მარი ექთანი')

    def setUp(self):
        self.client.force_login(self.doc)

    def _create(self, sub='open', **kw):
        p = _go(sub, **kw)
        p.surgeon = kw.pop('surgeon', self.doc)
        p.visit_date = timezone.localdate()
        p.started_on = timezone.localdate()
        p.studies['labs_date'] = timezone.localdate().isoformat()
        p.studies['planned_surgery_date'] = (timezone.localdate() + datetime.timedelta(days=10)).isoformat()
        p.save()
        return p

    def test_s1_unapprove_evicts_ready_and_header_cannot_retain_ineligible_ready(self):
        # Fuzz A-S1-2: signoff action=unapprove must evict someday_list='ready' -> 'conf'
        p = self._create('open')
        self.client.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p.refresh_from_db()
        self.assertEqual(p.someday_list, PreopPatient.SomedayList.READY)
        self.client.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'unapprove'})
        p.refresh_from_db()
        self.assertIsNone(p.approved_at)
        self.assertNotEqual(p.someday_list, PreopPatient.SomedayList.READY)

        # Header POST cannot retain someday:ready when labs aged >14d
        p2 = self._create('open')
        self.client.post(reverse('preop-patient', args=[p2.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p2.refresh_from_db()
        p2.studies['labs_date'] = (timezone.localdate() - datetime.timedelta(days=20)).isoformat()
        p2.studies['stale_labs_refreshed'] = False
        p2.save(update_fields=['studies'])
        self.client.post(reverse('preop-patient', args=[p2.pk]), {
            'section': 'header', 'patient_name': p2.patient_name, 'slot_kind': 'someday:ready', 'tab': 'q&evil=1#frag',
        })
        p2.refresh_from_db()
        self.assertNotEqual(p2.someday_list, PreopPatient.SomedayList.READY)

        # Fuzz A-S3-1: header redirect tab parameter allowlisted
        resp = self.client.post(reverse('preop-patient', args=[p2.pk]), {
            'section': 'header', 'patient_name': p2.patient_name, 'tab': 'q&injected=1#frag',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].endswith('?tab=q'))

    def test_s1_board_rehandoff_prefix_dedup_and_clinical_parity(self):
        # Fuzz A-S1-3: HIT discovered after initial handoff replaces VTE LMWH task (no conflicting active LMWH!)
        p_hit = self._create('open')
        bp, _, _ = admit_preop_to_board(p_hit, self.doc)
        p_hit.refresh_from_db()
        p_hit.questionnaire['allergy_chips'] = ['hit_heparin']
        p_hit.save(update_fields=['questionnaire'])
        admit_preop_to_board(p_hit, self.doc)
        tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertTrue(any('HIT ანამნეზში!' in t for t in tasks))
        self.assertFalse(any('LMWH პროფილაქტიკური' in t for t in tasks), tasks)

        # Fuzz A-S1-3: beta_lactam + vancomycin across eGFR 70 -> 35 replaces old gentamicin task (no duplicate!)
        p_abx = self._create('evar', q={'allergy_chips': ['beta_lactam', 'vancomycin']}, s={'creat': 95})
        bp2, _, _ = admit_preop_to_board(p_abx, self.doc)
        p_abx.refresh_from_db()
        p_abx.studies['creat'] = 180
        p_abx.save(update_fields=['studies'])
        admit_preop_to_board(p_abx, self.doc)
        tasks2 = [t for t in BoardEntry.objects.filter(patient=bp2, kind=BoardEntry.TASK).values_list('text', flat=True) if 'ვანკომიცინ' in t or 'ლაქტამ' in t]
        self.assertEqual(len(tasks2), 1, tasks2)
        self.assertIn('გენტამიცინი აკრძალულია', tasks2[0])

        # Fuzz A-S1-3: Nephroprotection re-handoff EVLA -> EVAR never duplicates
        p_nep = self._create('evla', s={'creat': 145})
        bp3, _, _ = admit_preop_to_board(p_nep, self.doc)
        p_nep.refresh_from_db()
        p_nep.op_subtype = 'evar'
        p_nep.save()
        admit_preop_to_board(p_nep, self.doc)
        nep_tasks = [t for t in BoardEntry.objects.filter(patient=bp3, kind=BoardEntry.TASK).values_list('text', flat=True) if t.startswith('ნეფროპროტექცია')]
        self.assertEqual(len(nep_tasks), 1, nep_tasks)

        # Combos A-S2-5 & Clin S2-9: Board mech_valve without warfarin, venous non-comp/borderline ABI, CYP2C19 PPI switch
        p_mv = self._create('open', q={'cad_chips': ['mech_valve'], 'med_warfarin': False})
        bp_mv, _, _ = admit_preop_to_board(p_mv, self.doc)
        mv_tasks = list(BoardEntry.objects.filter(patient=bp_mv, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertFalse(any('LMWH პროფილაქტიკური დოზით' in t for t in mv_tasks), mv_tasks)
        self.assertTrue(any('მექანიკურ' in t or 'ვარფარინ' in t for t in mv_tasks), mv_tasks)

        p_nc = self._create('evla', s={'abi_r': 1.45, 'tp_r': None, 'tbi_r': None})
        bp_nc, _, _ = admit_preop_to_board(p_nc, self.doc)
        nc_tasks = list(BoardEntry.objects.filter(patient=bp_nc, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertFalse(any('ელასტიური კომპრესია პროტოკოლით' in t for t in nc_tasks), nc_tasks)

        p_rc = self._create('evla', s={'abi_r': 1.45, 'tp_r': 55, 'tbi_r': 0.60})
        bp_rc, _, _ = admit_preop_to_board(p_rc, self.doc)
        rc_tasks = list(BoardEntry.objects.filter(patient=bp_rc, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertTrue(any('I კლასის' in t or '<20 mmHg' in t for t in rc_tasks), rc_tasks)

        p_cyp = self._create('pta_claud', q={'med_clopidogrel': True, 'med_chips': ['ppi']})
        bp_cyp, _, _ = admit_preop_to_board(p_cyp, self.doc)
        cyp_tasks = list(BoardEntry.objects.filter(patient=bp_cyp, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertTrue(any('პანტოპრაზოლი' in t for t in cyp_tasks), cyp_tasks)

    def test_s2_rotated_ceap_c6_new_ui_surgeon_venous_iodine_and_stage2_med_hold(self):
        # Fuzz A-S2-1: Rotated venous self-fill must NOT downgrade CEAP C6 -> C2
        p_c6 = self._create('evla', q={'ven_ceap': 'C6'})
        self.client.post(reverse('preop-patient', args=[p_c6.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_c6.refresh_from_db()
        tok = make_self_fill_token(p_c6)
        self.client.logout()
        r_get = self.client.get(reverse('preop-self-fill', args=[tok]))
        self.assertEqual(r_get.status_code, 200)
        self.assertNotContains(r_get, 'name="ven_ceap" value="C2"')
        self.client.post(reverse('preop-self-fill', args=[tok]), {'mets_ok': '1'})
        p_c6.refresh_from_db()
        self.assertEqual(p_c6.questionnaire['ven_ceap'], 'C6')

        # Combos A-S2-1: New Nurse UI renders valid surgeon <option value="pk"> and preserves surgeon on header save
        self.client.force_login(self.doc)
        self.client.post(reverse('preop-ui-mode'), {'mode': 'new', 'next': '/preop/'})
        p_nw = self._create('open', surgeon=self.doc)
        r_nw = self.client.get(reverse('preop-patient', args=[p_nw.pk]))
        self.assertContains(r_nw, f'<option value="{self.doc.pk}" selected>')
        self.assertContains(r_nw, 'ნინო ქირურგი')

        # Combos A-S2-2 & A-S2-3 (Part B): Venous iodine allergy resolvable; venous save does NOT fabricate allergy_iodine_asked on PTA
        p_vi = self._create('evla', q={'allergy_other': True, 'allergy_text': 'იოდი'})
        self.client.post(reverse('preop-ui-mode'), {'mode': 'old', 'next': '/preop/'})
        r_vq = self.client.get(reverse('preop-patient', args=[p_vi.pk]) + '?tab=q')
        self.assertContains(r_vq, 'name="allergy_iodine"')
        self.client.post(reverse('preop-patient', args=[p_vi.pk]), {
            'section': 'questionnaire', 'allergy_iodine_presented': '1', 'allergy_iodine': '1',
            'allergy_other': '1', 'allergy_text': 'იოდი', 'mets_ok': '1',
        })
        p_vi.refresh_from_db()
        self.assertTrue(p_vi.questionnaire['allergy_iodine'])
        self.assertFalse(evaluate(p_vi)['has_blocking_unknown_state'])

        p_vp = self._create('evla', q={'allergy_iodine_asked': False})
        self.client.post(reverse('preop-patient', args=[p_vp.pk]), {'section': 'questionnaire', 'mets_ok': '1'})
        p_vp.refresh_from_db()
        self.assertFalse(p_vp.questionnaire['allergy_iodine_asked'])

        # Fuzz A-S2-4: Passport must NOT render literal "None" on Stage 1 out-of-range creat unit mismatch
        p_cm = self._create('open')
        self.client.post(reverse('preop-patient', args=[p_cm.pk]), {'section': 'stage1', 'creat': '5000', 'creat_unit': 'mgdl', 's1_partial': '1'})
        r_pp = self.client.get(reverse('preop-passport', args=[p_cm.pk]))
        self.assertNotContains(r_pp, 'None (ერთეული')
        self.assertContains(r_pp, '5000')

        # Fuzz A-S2-5: stage2 POST must NOT persist med_hold_agreed=True when needs_med_hold=False
        d179 = (timezone.localdate() - datetime.timedelta(days=179)).isoformat()
        p_s2 = self._create('cea_symp', q={'med_clopidogrel': True, 'cea_symp_event': True, 'cea_event_date': d179}, s={'med_hold_agreed': False})
        self.client.post(reverse('preop-patient', args=[p_s2.pk]), {'section': 'stage2', 'med_hold_agreed': '1'})
        p_s2.refresh_from_db()
        self.assertFalse(p_s2.studies.get('med_hold_agreed'))

        # Fuzz A-S3-2: Updating labs_date from stale -> older stale clears stale_labs_refreshed
        p_sl = self._create('open', s={'labs_date': (timezone.localdate() - datetime.timedelta(days=16)).isoformat(), 'stale_labs_refreshed': True})
        self.client.post(reverse('preop-patient', args=[p_sl.pk]), {
            'section': 'stage1', 's1_partial': '1',
            'labs_date': (timezone.localdate() - datetime.timedelta(days=40)).isoformat(),
        })
        p_sl.refresh_from_db()
        self.assertFalse(p_sl.studies.get('stale_labs_refreshed'))

        # Combos A-S2-4: Cross-surface symptomatic parity on cea_asymp + cea_crescendo=True
        p_cc = self._create('cea_asymp', q={'cea_crescendo': True})
        for url_name in ('preop-passport', 'preop-patient-guide', 'preop-patient'):
            r_cc = self.client.get(reverse(url_name, args=[p_cc.pk]))
            self.assertEqual(r_cc.status_code, 200)
            self.assertContains(r_cc, 'სიმპტომური')
        self.client.post(reverse('preop-ui-mode'), {'mode': 'new', 'next': '/preop/'})
        r_cc_nw = self.client.get(reverse('preop-patient', args=[p_cc.pk]))
        self.assertContains(r_cc_nw, 'სიმპტომური')

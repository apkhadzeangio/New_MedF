"""Track A3 Published-Source Reference Tests & Track C 4-Bug Regression Suite.

Proves exact mathematical & clinical agreement against primary published tables:
- RCRI (Lee et al., Circulation 1999; 100:1043-1049)
- VSG-CRI (Bertges et al., J Vasc Surg 2010; 52:674-683)
- ARISCAT (Canet et al., Anesthesiology 2010; 113:1338-1350)
- CKD-EPI 2021 (Inker et al., NEJM 2021), Cockcroft-Gault ABW, Caprini, Gurm contrast ceiling
- Anticoagulation & Perioperative Pharmacology (PAUSE, CHEST 2022, EHRA 2021, ASRA 5th Ed 2025,
  ESC 2022, FDA SGLT2/Cilostazol, Multi-Society 2024 GLP-1, ESUR 2018 Metformin)
- Regression coverage for all 4 documented Track C clinical/technical fixes.
"""
import datetime
import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from ..models import PreopFile, PreopPatient
from ..preop import (
    apply_self_fill_post,
    build_med_schedule,
    calc_ariscat_pulm,
    calc_caprini,
    calc_crcl,
    calc_egfr,
    calc_max_contrast_ml,
    calc_rcri,
    calc_vsg_cri,
    evaluate,
    normalize_glucose_mmol,
)

REF_TODAY = datetime.date(2026, 11, 25)
REF_OP_DATE = '2026-12-10'


def _pt(op_subtype, pathway=None, age=68, sex='M', side='R', q=None, s=None):
    pw = pathway or PreopPatient.SUBTYPE_PATHWAY[op_subtype]
    return PreopPatient(
        patient_name='სატესტო ეტალონი',
        age=age, sex=sex, side=side,
        pathway=pw, op_subtype=op_subtype,
        visit_date=REF_TODAY, started_on=REF_TODAY,
        questionnaire=dict(q or {}),
        studies=dict(s or {}),
    )


def _drug_row(rows, needle):
    for r in rows:
        if needle in r['drug']:
            return r
    raise AssertionError(f'Missing medication row matching {needle!r} in {[r["drug"] for r in rows]}')


class PublishedScoreReferenceTests(SimpleTestCase):
    """Exact verification against published risk-index equations and validation tables."""

    def test_rcri_lee_1999_published_table_and_suprainguinal_rule(self):
        # High-risk surgery predictor applies STRICTLY to suprainguinal vascular surgery (open/TBD AAA, ABF)
        for sub, exp_pts in (
            (PreopPatient.OpSubtype.OPEN, 1),
            (PreopPatient.OpSubtype.TBD, 1),
            (PreopPatient.OpSubtype.ABF_CLTI, 1),
            (PreopPatient.OpSubtype.EVAR, 0),
            (PreopPatient.OpSubtype.CEA_ASYMP, 0),
            (PreopPatient.OpSubtype.BYPASS_CLTI, 0),
            (PreopPatient.OpSubtype.PTA_CLTI, 0),
            (PreopPatient.OpSubtype.EVLA, 0),
        ):
            r = calc_rcri(_pt(sub), {'filled': True}, {})
            self.assertEqual(r['score'], exp_pts, f'Unexpected RCRI surgery point for {sub}')

        # Cumulative progression 0 -> 1 -> 2 -> 3 -> 6 and exact Lee 1999 MACE validation table
        p_base = _pt(PreopPatient.OpSubtype.BYPASS_CLTI)
        r0 = calc_rcri(p_base, {'filled': True}, {'creat': 176.8, 'creat_unit': 'umol'})  # sCr == 2.0 mg/dL (not >2.0)
        self.assertEqual((r0['score'], r0['mace_pct'], r0['tier']), (0, '0.4%', 'low'))

        r1 = calc_rcri(p_base, {'filled': True, 'cad_chips': ['mi']}, {})
        self.assertEqual((r1['score'], r1['mace_pct'], r1['tier']), (1, '0.9%', 'low'))

        r2 = calc_rcri(p_base, {'filled': True, 'cad_chips': ['mi', 'hf']}, {})
        self.assertEqual((r2['score'], r2['mace_pct'], r2['tier']), (2, '6.6%', 'intermediate'))

        r3 = calc_rcri(p_base, {'filled': True, 'cad_chips': ['mi', 'hf'], 'neuro': True}, {})
        self.assertEqual((r3['score'], r3['mace_pct'], r3['tier']), (3, '≥11.0%', 'high'))

        r6 = calc_rcri(
            _pt(PreopPatient.OpSubtype.OPEN),
            {'filled': True, 'cad_chips': ['mi', 'hf'], 'neuro': True, 'dm_insulin': True},
            {'creat': 2.1, 'creat_unit': 'mgdl'},
        )
        self.assertEqual((r6['score'], r6['mace_pct'], r6['tier']), (6, '≥11.0%', 'high'))

    def test_vsg_cri_bertges_2010_published_9_predictors_and_6_mace_bands(self):
        # Venous returns None
        self.assertIsNone(calc_vsg_cri(_pt(PreopPatient.OpSubtype.EVLA), {'filled': True}, {}))

        # Age tiers: <60 (0), 60-69 (+2), 70-79 (+3), >=80 (+4)
        for age, pts in ((59, 0), (60, 2), (69, 2), (70, 3), (79, 3), (80, 4)):
            self.assertEqual(calc_vsg_cri(_pt(PreopPatient.OpSubtype.OPEN, age=age), {'filled': True}, {})['score'], pts)

        # Prior CABG/PCI gives -1 (floored at 0), but recent_stent does NOT subtract 1
        r_cabg = calc_vsg_cri(_pt(PreopPatient.OpSubtype.OPEN, age=65), {'filled': True, 'cad_chips': ['cabg']}, {})
        self.assertEqual(r_cabg['score'], 2 + 2 - 1)  # age +2, IHD +2, CABG -1 = 3
        r_recent = calc_vsg_cri(
            _pt(PreopPatient.OpSubtype.OPEN, age=65),
            {'filled': True, 'cad_chips': ['stent_old'], 'recent_stent': True}, {},
        )
        self.assertEqual(r_recent['score'], 4)  # recent stent cancels -1 credit

        # Verify all 6 published Bertges 2010 MACE bands: 0-3 (2.6%), 4 (3.5%), 5 (6.0%), 6 (6.6%), 7 (8.9%), >=8 (14.3%)
        band_cases = [
            (3, '2.6%', 'low', _pt(PreopPatient.OpSubtype.OPEN, age=72), {'filled': True}, {}),
            (4, '3.5%', 'low', _pt(PreopPatient.OpSubtype.OPEN, age=82), {'filled': True}, {}),
            (5, '6.0%', 'intermediate', _pt(PreopPatient.OpSubtype.OPEN, age=82), {'filled': True, 'smoker': True}, {}),
            (6, '6.6%', 'intermediate', _pt(PreopPatient.OpSubtype.OPEN, age=82), {'filled': True, 'smoker': True, 'dm_insulin': True}, {}),
            (7, '8.9%', 'high', _pt(PreopPatient.OpSubtype.OPEN, age=82), {'filled': True, 'smoker': True}, {'creat': 1.8, 'creat_unit': 'mgdl'}),
            (8, '14.3%', 'high', _pt(PreopPatient.OpSubtype.OPEN, age=82), {'filled': True, 'cad_chips': ['mi', 'hf']}, {}),
        ]
        for exp_score, exp_pct, exp_tier, pat, q, s in band_cases:
            res = calc_vsg_cri(pat, q, s)
            self.assertEqual((res['score'], res['risk_pct'], res['tier']), (exp_score, exp_pct, exp_tier))

    def test_ariscat_canet_2010_published_7_predictors_and_ppc_bands(self):
        # Low (<26 pts: 1.6%): 45yo EVLA, SpO2 99, Hb 140, <2h -> 0 pts
        r_low = calc_ariscat_pulm(_pt(PreopPatient.OpSubtype.EVLA, age=45), {'filled': True}, {'spo2': 99}, hb_gl=140)
        self.assertEqual((r_low['score'], r_low['ppc_pct'], r_low['tier']), (0, '1.6%', 'low'))

        # Boundary 25 vs 26: age 51 (+3) + duration 2-3h (+16) = 19 (low); + SpO2 95 (+8) = 27 (intermediate 13.3%)
        r_25 = calc_ariscat_pulm(_pt(PreopPatient.OpSubtype.CEA_ASYMP, age=51), {'filled': True}, {'spo2': 96}, hb_gl=130)
        self.assertEqual((r_25['score'], r_25['tier']), (19, 'low'))
        r_int = calc_ariscat_pulm(_pt(PreopPatient.OpSubtype.CEA_ASYMP, age=51), {'filled': True}, {'spo2': 95}, hb_gl=130)
        self.assertEqual((r_int['score'], r_int['ppc_pct'], r_int['tier']), (27, '13.3%', 'intermediate'))

        # High (>=45 pts: 42.1%): open aortic (+15) + >3h (+23) + age 68 (+3) + Hb 98 (+11) = 52
        r_hi = calc_ariscat_pulm(_pt(PreopPatient.OpSubtype.OPEN, age=68), {'filled': True}, {'spo2': 97}, hb_gl=98)
        self.assertEqual((r_hi['score'], r_hi['ppc_pct'], r_hi['tier']), (52, '42.1%', 'high'))

        # Maximum check: age >80 (+16) + SpO2 <=90 (+24) + resp inf (+17) + Hb <=100 (+11) + open aortic (+15) + >3h (+23) + emergency (+8) = 114
        r_max = calc_ariscat_pulm(
            _pt(PreopPatient.OpSubtype.OPEN, age=84),
            {'filled': True, 'inf_chips': ['fever_resp'], 'aaa_pain': True},
            {'spo2': 89, 'asa_emergency': True},
            hb_gl=92,
        )
        self.assertEqual((r_max['score'], r_max['tier']), (114, 'high'))

    def test_ckd_epi_2021_cockcroft_gault_abw_caprini_and_gurm_contrast(self):
        # CKD-EPI 2021 race-free exact values (Inker et al., NEJM 2021)
        self.assertEqual(calc_egfr(88.4, 70, 'M'), 81)   # sCr 1.0 mg/dL, 70M -> 142*(1/0.9)^-1.2*0.9938^70 = 80.96 -> 81
        self.assertEqual(calc_egfr(88.4, 70, 'F'), 61)   # sCr 1.0 mg/dL, 70F -> 142*(1/0.7)^-1.2*0.9938^70*1.012 = 60.60 -> 61
        self.assertEqual(calc_egfr(1.0, 70, 'M', creat_unit='mgdl'), 81)
        self.assertIsNone(calc_egfr(90, 70, 'M', on_dialysis=True))

        # Cockcroft-Gault elderly low-weight renal DOAC trap: 82F, 50 kg, sCr 1.1 mg/dL -> CrCl 31 vs eGFR 52
        self.assertEqual(calc_crcl(1.1, 82, 'F', weight_kg=50, creat_unit='mgdl'), 31)
        self.assertEqual(calc_egfr(1.1, 82, 'F', creat_unit='mgdl'), 50)

        # Obesity BMI >= 30 uses Adjusted Body Weight (IBW + 0.4*(actual - IBW))
        crcl_raw = calc_crcl(1.0, 60, 'M', weight_kg=130, creat_unit='mgdl')
        crcl_abw = calc_crcl(1.0, 60, 'M', weight_kg=130, creat_unit='mgdl', height_cm=175)
        self.assertGreater(crcl_raw, crcl_abw)
        self.assertEqual(crcl_abw, 105)

        # Gurm contrast ceiling tiers (<30: 1x min 15, 30-44: 1.5x, 45-59: 2x, >=60: 3x cap 250)
        self.assertEqual(calc_max_contrast_ml(10), 15)
        self.assertEqual(calc_max_contrast_ml(25), 25)
        self.assertEqual(calc_max_contrast_ml(40), 60)
        self.assertEqual(calc_max_contrast_ml(50), 100)
        self.assertEqual(calc_max_contrast_ml(100), 250)
        self.assertIsNone(calc_max_contrast_ml(25, on_dialysis=True))

        # Caprini tiers: low (0-2), moderate (3-4), high (>=5) + HIT forbids LMWH
        pts_lo, tier_lo, _ = calc_caprini(_pt(PreopPatient.OpSubtype.EVLA, age=35), {'filled': True})
        self.assertEqual((pts_lo, tier_lo), (2, 'დაბალი (0–2)'))
        pts_hi, tier_hi, adv_hit = calc_caprini(
            _pt(PreopPatient.OpSubtype.OPEN, age=76),
            {'filled': True, 'ven_dvt_history': True, 'allergy_chips': ['hit_heparin']},
            eff_renal=70,
        )
        self.assertGreaterEqual(pts_hi, 5)
        self.assertEqual(tier_hi, 'მაღალი (≥5)')
        self.assertIn('LMWH/ჰეპარინი აკრძალულია', adv_hit)


class PublishedPharmacologyReferenceTests(SimpleTestCase):
    """Exact verification of PAUSE / CHEST 2022 / EHRA 2021 / ASRA 5th Ed drug-hold matrix."""

    def test_warfarin_chest_2022_pause_bridging_and_asra_matrix(self):
        p_open = _pt(PreopPatient.OpSubtype.OPEN, s={'planned_surgery_date': REF_OP_DATE})
        # AFib without mechanical valve -> 5d hold, NO routine LMWH bridge (ESC Class III)
        r_afib = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_warfarin': True, 'cad_chips': ['afib']}, p_open.studies, 75, crcl=75), 'ვარფარინი')
        self.assertIn('5 დღით ადრე', r_afib['rule'])
        self.assertIn('ESC 2022 Class III', r_afib['rule'])

        # Mechanical valve CrCl >= 30 -> LMWH bridge (CHEST 2022: 50% D-1 morning, skip evening)
        r_mv = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_warfarin': True, 'cad_chips': ['mech_valve']}, p_open.studies, 75, crcl=75), 'ვარფარინი')
        self.assertIn('LMWH ხიდი (CHEST 2022 / PAUSE)', r_mv['rule'])
        self.assertIn('50%', r_mv['rule'])

        # Mechanical valve CrCl < 30 -> IV UFH bridge (NOT therapeutic LMWH!)
        r_mv_ckd = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_warfarin': True, 'cad_chips': ['mech_valve']}, p_open.studies, 24, crcl=24), 'ვარფარინი')
        self.assertIn('IV UFH ხიდი', r_mv_ckd['rule'])

        # Mechanical valve + HIT -> IV bivalirudin / argatroban (heparin/LMWH forbidden!)
        r_mv_hit = _drug_row(
            build_med_schedule(p_open, {'filled': True, 'med_warfarin': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin']}, p_open.studies, 75, crcl=75),
            'ვარფარინი',
        )
        self.assertIn('ბივალირუდინი', r_mv_hit['rule'])

        # Neuraxial block -> ASRA INR <= 1.4
        r_nx = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_warfarin': True}, p_open.studies, 75, crcl=75, has_neuraxial_override=True), 'ვარფარინი')
        self.assertIn('INR ≤ 1.4', r_nx['rule'])

        # EVLA without neuraxial -> continue if INR <= 3.0 (ESVS 2022)
        p_evla = _pt(PreopPatient.OpSubtype.EVLA, s={'planned_surgery_date': REF_OP_DATE})
        r_evla = _drug_row(build_med_schedule(p_evla, {'filled': True, 'med_warfarin': True}, p_evla.studies, 80, crcl=80), 'ვარფარინი')
        self.assertIn('INR ≤ 3.0', r_evla['rule'])

    def test_doac_and_dabigatran_ehra_2021_asra_5th_ed_crcl_matrix(self):
        p_open = _pt(PreopPatient.OpSubtype.OPEN, s={'planned_surgery_date': REF_OP_DATE})
        # Factor Xa DOAC non-neuraxial across CrCl tiers
        for crcl_v, expected_h in ((80, '48 საათით'), (40, '72 საათით'), (25, '≥96 სთ')):
            row = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_doac': True}, p_open.studies, crcl_v, crcl=crcl_v), 'DOAC')
            self.assertIn(expected_h, row['rule'])
        # Factor Xa DOAC + neuraxial -> ASRA 72h minimum even when CrCl == 85!
        r_doac_nx = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_doac': True}, p_open.studies, 85, crcl=85, has_neuraxial_override=True), 'DOAC')
        self.assertIn('72 საათით', r_doac_nx['rule'])

        # Dabigatran full 4-tier renal matrix × non-neuraxial (48/72/96/120h) vs ASRA neuraxial (72/96/120h/contra)
        for crcl_v, exp_std, exp_nx in (
            (85, '48 საათით', '72 საათით'),
            (60, '72 საათით', '96 საათით'),
            (40, '96 საათით', '120 საათით'),
            (25, '≥120 საათით', 'უკუნაჩვენებია'),
        ):
            r_std = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_dabigatran': True}, p_open.studies, crcl_v, crcl=crcl_v, has_neuraxial_override=False), 'დაბიგატრანი')
            r_nx = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_dabigatran': True}, p_open.studies, crcl_v, crcl=crcl_v, has_neuraxial_override=True), 'დაბიგატრანი')
            self.assertIn(exp_std, r_std['rule'])
            self.assertIn(exp_nx, r_nx['rule'])

    def test_p2y12_sglt2_glp1_metformin_and_cilostazol_reference_matrix(self):
        p_open = _pt(PreopPatient.OpSubtype.OPEN, s={'planned_surgery_date': REF_OP_DATE})
        # P2Y12 elective vs recent stent vs symptomatic CEA vs neuraxial
        r_clop = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_clopidogrel': True, 'med_aspirin': True}, p_open.studies, 70, crcl=70), 'კლოპიდოგრელი')
        self.assertIn('5–7 დღით', r_clop['rule'])
        r_stent = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_clopidogrel': True, 'recent_stent': True}, p_open.studies, 70, crcl=70), 'კლოპიდოგრელი')
        self.assertIn('აკრძალულია', r_stent['rule'])
        p_cea = _pt(PreopPatient.OpSubtype.CEA_SYMP, s={'planned_surgery_date': REF_OP_DATE})
        r_cea_clop = _drug_row(build_med_schedule(p_cea, {'filled': True, 'med_clopidogrel': True, 'cea_symp_event': True}, p_cea.studies, 70, crcl=70), 'კლოპიდოგრელი')
        self.assertIn('გრძელდება უწყვეტად', r_cea_clop['rule'])
        r_tica_nx = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_ticagrelor': True, 'med_aspirin': True}, p_open.studies, 70, crcl=70, has_neuraxial_override=True), 'ტიკაგრელორი')
        self.assertIn('5–7 დღით', r_tica_nx['rule'])
        r_pras = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_prasugrel': True, 'med_aspirin': True}, p_open.studies, 70, crcl=70), 'პრასუგრელი')
        self.assertIn('7–10 დღით', r_pras['rule'])

        # SGLT2: 72h standard vs 96h ertugliflozin; GLP-1: 24h clear liquid diet
        r_sglt_std = _drug_row(build_med_schedule(p_open, {'filled': True, 'sglt2': True}, p_open.studies, 75, crcl=75), 'SGLT2')
        self.assertIn('72 სთ', r_sglt_std['date_plan'])
        r_sglt_ertu = _drug_row(build_med_schedule(p_open, {'filled': True, 'sglt2': True, 'other_meds': 'ერტუგლიფლოზინი 5მგ'}, p_open.studies, 75, crcl=75), 'SGLT2')
        self.assertIn('96 სთ', r_sglt_ertu['date_plan'])
        r_glp1 = _drug_row(build_med_schedule(p_open, {'filled': True, 'glp1': True}, p_open.studies, 75, crcl=75), 'GLP-1')
        self.assertIn('24 სთ სუფთა თხევადი დიეტა', r_glp1['rule'])

        # Metformin: contraindicated <30 / liver 3x; 48h contrast <60; skip morning otherwise
        r_met_c = _drug_row(build_med_schedule(p_open, {'filled': True, 'metformin': True}, p_open.studies, 25, crcl=25), 'მეტფორმინი')
        self.assertEqual(r_met_c['tone'], 'red')
        p_evla = _pt(PreopPatient.OpSubtype.EVLA, s={'planned_surgery_date': REF_OP_DATE})
        r_met_ok = _drug_row(build_med_schedule(p_evla, {'filled': True, 'metformin': True}, p_evla.studies, 75, crcl=75), 'მეტფორმინი')
        self.assertEqual(r_met_ok['tone'], 'ok')

        # Cilostazol: 48h hold vs CHF Black Box red
        r_cilo_ok = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_chips': ['cilostazol']}, p_open.studies, 70, crcl=70), 'ცილოსტაზოლი')
        self.assertIn('48 საათით', r_cilo_ok['rule'])
        r_cilo_hf = _drug_row(build_med_schedule(p_open, {'filled': True, 'med_chips': ['cilostazol'], 'cad_chips': ['hf']}, p_open.studies, 70, crcl=70), 'ცილოსტაზოლი')
        self.assertEqual(r_cilo_hf['tone'], 'red')


class TrackA3FourBugFixesRegressionTests(TestCase):
    """Explicit regression suite proving all 4 documented Track C bugs are fixed."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='ref_doc', password='RefDocPassword!1',
            first_name='ლევან', last_name='ექიმი', is_staff=True,
        )

    def test_fix1_pdf_csp_allows_inline_browser_viewer_while_image_keeps_sandbox(self):
        self.client.force_login(self.doc)
        p = PreopPatient.objects.create(patient_name='ფაილ ტესტაძე', created_by=self.doc)
        img_buf = io.BytesIO()
        Image.new('RGB', (120, 90), (200, 210, 220)).save(img_buf, 'PNG')
        img_buf.seek(0)
        pdf_file = SimpleUploadedFile('report.pdf', b'%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n', content_type='application/pdf')
        img_file = SimpleUploadedFile('lab.png', img_buf.read(), content_type='image/png')
        self.client.post(reverse('preop-files', args=[p.pk]), {'category': 'labs', 'file': [pdf_file, img_file]})

        f_pdf = PreopFile.objects.get(patient=p, is_pdf=True)
        f_img = PreopFile.objects.get(patient=p, is_pdf=False)
        r_pdf = self.client.get(reverse('preop-file', args=[f_pdf.pk]))
        r_img = self.client.get(reverse('preop-file', args=[f_img.pk]))
        self.assertNotIn('sandbox', r_pdf['Content-Security-Policy'])
        self.assertIn("default-src 'none'", r_pdf['Content-Security-Policy'])
        self.assertIn("object-src 'self'", r_pdf['Content-Security-Policy'])
        self.assertIn('sandbox', r_img['Content-Security-Policy'])

    def test_fix2_glucose_36_to_44_mgdl_severe_hypoglycemia_converts_safely(self):
        self.assertEqual(normalize_glucose_mmol(35.0), 35.0)
        self.assertEqual(normalize_glucose_mmol(36.0), 2.0)
        self.assertEqual(normalize_glucose_mmol(40.0), 2.2)
        self.assertEqual(normalize_glucose_mmol(44.0), 2.4)
        p = _pt(PreopPatient.OpSubtype.EVAR, q={'filled': True}, s={'glucose': 40})
        ev = evaluate(p, today=REF_TODAY)
        self.assertEqual(ev['gl_mmol'], 2.2)
        self.assertTrue(any('2.2 მმოლ/ლ' in rf for rf in ev['red_flags']))

    def test_fix3_nice_ng45_rational_tsh_and_spirometry_non_overblocking(self):
        # Stable thyroid disease on non-contrast CEA or PAD bypass does NOT block on TSH
        ev_cea_thy = evaluate(_pt(PreopPatient.OpSubtype.CEA_ASYMP, q={'filled': True, 'thyroid_disease': True}), today=REF_TODAY)
        ev_byp_thy = evaluate(_pt(PreopPatient.OpSubtype.BYPASS_CLTI, q={'filled': True, 'thyroid_disease': True}), today=REF_TODAY)
        self.assertFalse(ev_cea_thy['needs_tsh'])
        self.assertFalse(ev_byp_thy['needs_tsh'])
        # Contrast EVAR/PTA and open AAA STILL require TSH on thyroid disease
        self.assertTrue(evaluate(_pt(PreopPatient.OpSubtype.EVAR, q={'filled': True, 'thyroid_disease': True}), today=REF_TODAY)['needs_tsh'])
        self.assertTrue(evaluate(_pt(PreopPatient.OpSubtype.OPEN, q={'filled': True, 'thyroid_disease': True}), today=REF_TODAY)['needs_tsh'])

        # Stable pulm disease on non-aortic bypass / CEA / EVAR / phlebectomy does NOT force spirometry
        for sub in (PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.CEA_ASYMP, PreopPatient.OpSubtype.EVAR, PreopPatient.OpSubtype.PHLEB):
            ev_s = evaluate(_pt(sub, q={'filled': True, 'pulm': True}, s={'spo2': 97}), today=REF_TODAY)
            self.assertFalse(ev_s['needs_spirometry'], f'Non-aortic stable {sub} should not require spirometry')
        # Open aortic with pulm or ANY patient with SpO2 < 94 STILL requires spirometry
        self.assertTrue(evaluate(_pt(PreopPatient.OpSubtype.OPEN, q={'filled': True, 'pulm': True}, s={'spo2': 97}), today=REF_TODAY)['needs_spirometry'])
        self.assertTrue(evaluate(_pt(PreopPatient.OpSubtype.BYPASS_CLTI, q={'filled': True}, s={'spo2': 92}), today=REF_TODAY)['needs_spirometry'])

    def test_fix4_reproductive_female_fluoroscopy_bhcg_and_selffill_auto_promotion(self):
        # 12-55F on EVAR/PTA gets automatic WHO Sign-In β-hCG check even if pregnant_possible is unchecked
        p_evar_f = _pt(PreopPatient.OpSubtype.EVAR, age=38, sex='F', q={'filled': True, 'pregnant_possible': False})
        ev_f = evaluate(p_evar_f, today=REF_TODAY)
        dos_keys = [c['key'] for c in ev_f['dos_checks']]
        self.assertIn('dos_bhcg_negative', dos_keys)
        # Male or post-menopausal female (>=56) does NOT get β-hCG check
        self.assertNotIn('dos_bhcg_negative', [c['key'] for c in evaluate(_pt(PreopPatient.OpSubtype.EVAR, age=60, sex='F', q={'filled': True}), today=REF_TODAY)['dos_checks']])
        self.assertNotIn('dos_bhcg_negative', [c['key'] for c in evaluate(_pt(PreopPatient.OpSubtype.EVAR, age=38, sex='M', q={'filled': True}), today=REF_TODAY)['dos_checks']])

        # Self-fill auto-promotes sglt2/metformin -> dm=True and recent_stent -> cad=True
        p_sf = _pt(PreopPatient.OpSubtype.BYPASS_CLTI, age=64, sex='M')
        apply_self_fill_post({'sglt2': '1', 'recent_stent': '1'}, p_sf)
        self.assertTrue(p_sf.questionnaire['dm'])
        self.assertTrue(p_sf.questionnaire['cad'])

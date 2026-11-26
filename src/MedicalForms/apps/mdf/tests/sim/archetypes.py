"""20 high-acuity clinical archetypes probing multi-system vascular & anesthesia traps."""
import datetime

from ...models import PreopPatient
from .cohort import SIM_TODAY, _make_unsaved_patient


def _arch_0_to_4(arch, idx):
    if arch == 0:
        # Frail 86yo 42kg woman: Creat 1.15 mg/dL -> eGFR ~47 but Cockcroft-Gault CrCl ~22 mL/min on Dabigatran + spinal!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.PHLEB, age=86, sex='F', side='R',
            q={'filled': True, 'med_dabigatran': True, 'htn': True, 'mets_ok': True},
            s={'weight_kg': 42, 'height_cm': 154, 'creat': 1.15, 'creat_unit': 'mgdl', 'hb': 124, 'plt': 210, 'inr': 1.1, 'glucose': 5.3, 'ecg': 'normal', 'ven_duplex_status': 'deep_patent_reflux', 'anesthesia_plan': 'neuraxial'},
        )
    if arch == 1:
        # Morbidly obese 148kg 156cm man: Open AAA, Cefazolin 3g, ABW CrCl, OSA
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.OPEN, age=67, sex='M',
            q={'filled': True, 'htn': True, 'dm': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'weight_kg': 148, 'height_cm': 156, 'aaa_mm': 62, 'cta_disc': True, 'hb': 142, 'plt': 240, 'creat': 112, 'creat_unit': 'umol', 'inr': 1.0, 'glucose': 6.4, 'lvef': 55, 'ecg': 'normal'},
        )
    if arch == 2:
        # Lone controlled AFib without IHD/CHF -> must NOT inflate RCRI or VSG-CRI IHD/CHF!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVAR, age=71, sex='M', cardio_decision='cleared',
            q={'filled': True, 'cad': True, 'cad_chips': ['afib'], 'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 58, 'aaa_neck_status': 'friendly_ifu', 'popliteal_screen_status': 'normal', 'cta_disc': True, 'hb': 138, 'plt': 220, 'creat': 88, 'creat_unit': 'umol', 'inr': 1.08, 'lvef': 58, 'nt_probnp': 110, 'ecg': 'afib_controlled'},
        )
    if arch == 3:
        # Clean healthy 32yo F EVLA -> lean Stage 1, ASA I, zero cardio/echo/CXR/spirometry
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVLA, age=32, sex='F', side='L',
            q={'filled': True, 'ven_ceap': 'C2', 'mets_ok': True},
            s={'weight_kg': 60, 'height_cm': 168, 'hb': 132, 'plt': 250, 'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 7},
        )
    # arch == 4: Symptomatic CEA with IV thrombolysis <=6d and NO antiplatelet -> antiplatelet reflex MUST be suppressed!
    return _make_unsaved_patient(
        idx, PreopPatient.OpSubtype.CEA_SYMP, age=69, sex='M', side='R',
        q={'filled': True, 'cea_symp_event': True, 'cea_event_date': (SIM_TODAY - datetime.timedelta(days=4)).isoformat(), 'cea_thrombolysis': True, 'med_chips': ['statin'], 'mets_ok': True},
        s={'carotid_r': 85, 'carotid_l': 40, 'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small', 'cta_disc': True, 'hb': 136, 'plt': 210, 'creat': 90, 'inr': 1.0, 'ecg': 'normal'},
    )


def _arch_5_to_9(arch, idx):
    if arch == 5:
        # Symptomatic CEA with intracranial hemorrhage on brain CT -> hard contraindication + no antiplatelet!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_SYMP, age=72, sex='F', side='L', approved=True,
            q={'filled': True, 'cea_symp_event': True, 'cea_event_date': (SIM_TODAY - datetime.timedelta(days=5)).isoformat(), 'mets_ok': True},
            s={'carotid_r': 30, 'carotid_l': 80, 'cea_cta_status': 'concordant', 'brain_imaging': 'large_or_hemorrhage', 'hb': 128, 'plt': 200, 'creat': 80, 'inr': 1.0, 'ecg': 'normal'},
        )
    if arch == 6:
        # CEA with 100% ipsilateral ICA occlusion -> hard contraindication even if approved!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_ASYMP, age=66, sex='M', side='R', approved=True,
            q={'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'carotid_r': 100, 'carotid_l': 50, 'cea_cta_status': 'concordant', 'cta_disc': True, 'hb': 142, 'plt': 230, 'creat': 85, 'inr': 1.0, 'ecg': 'normal'},
        )
    if arch == 7:
        # Multi-allergy storm: HIT + beta-lactam + latex/chlorhexidine + iodine + clopidogrel intolerance
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.BYPASS_CLTI, age=70, sex='M', side='R',
            q={'filled': True, 'abf_rest_pain': True, 'dental_infection': True, 'allergy_iodine': True, 'allergy_other': True, 'allergy_chips': ['hit_heparin', 'beta_lactam', 'latex_chlorhex', 'clopidogrel_intol'], 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'abi_r': 0.38, 'abi_l': 0.92, 'pad_runoff_status': 'tibial_single', 'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'no_autovein_prosthetic', 'hb': 128, 'plt': 165, 'creat': 96, 'inr': 1.05, 'ecg': 'normal', 'cta_disc': True},
        )
    if arch == 8:
        # ESRD on hemodialysis + OAC + Hb 94 -> renal anemia does NOT trigger routine EGD; VOYAGER blocked
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.PTA_CLTI, age=65, sex='M', side='L',
            q={'filled': True, 'on_dialysis': True, 'abf_ulcer': True, 'med_doac': True, 'med_aspirin': True, 'metformin': True, 'mets_ok': True},
            s={'abi_r': 0.95, 'abi_l': 1.62, 'tp_r': 78, 'pta_lesion_site': 'infrapopliteal', 'pta_access_site': 'cfa_clean', 'hb': 94, 'plt': 190, 'inr': 1.15, 'glucose': 6.2, 'k': 5.5, 'ecg': 'normal', 'post_antithrombotic_plan': 'voyager'},
        )
    # arch == 9: Unilateral venous EVLA with ipsilateral ABI 0.74 and contralateral ABI 1.12 -> must catch arterial insufficiency!
    return _make_unsaved_patient(
        idx, PreopPatient.OpSubtype.EVLA, age=68, sex='M', side='R',
        q={'filled': True, 'ven_ceap': 'C6', 'mets_ok': True},
        s={'abi_r': 0.74, 'abi_l': 1.12, 'hb': 138, 'plt': 225, 'glucose': 5.6, 'ven_duplex_status': 'deep_patent_reflux', 'ecg': 'normal'},
    )


def _arch_10_to_14(arch, idx):
    if arch == 10:
        # Pregnant patient scheduled for EVLA -> hard contraindication!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVLA, age=29, sex='F', side='L', approved=True,
            q={'filled': True, 'ven_ceap': 'C3', 'ven_pregnant': True, 'mets_ok': True},
            s={'hb': 122, 'plt': 240, 'ven_duplex_status': 'deep_patent_reflux'},
        )
    if arch == 11:
        # Venous acute DVT on duplex -> hard contraindication!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.PHLEB, age=54, sex='M', side='R', approved=True,
            q={'filled': True, 'ven_ceap': 'C4', 'mets_ok': True},
            s={'hb': 140, 'plt': 210, 'inr': 1.0, 'glucose': 5.4, 'creat': 84, 'ecg': 'normal', 'ven_duplex_status': 'acute_dvt'},
        )
    if arch == 12:
        # Claudication harm guards: BTK PTA & Prosthetic infrainguinal bypass
        sub = PreopPatient.OpSubtype.PTA_CLAUD if (idx // 20) % 2 == 0 else PreopPatient.OpSubtype.BYPASS_CLAUD
        return _make_unsaved_patient(
            idx, sub, age=62, sex='M', side='R',
            q={'filled': True, 'abf_claud_m': 120, 'abf_duration': '< 2 კვირა', 'ic_bmt_3m': False, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'abi_r': 0.64, 'abi_l': 0.98, 'pta_lesion_site': 'infrapopliteal', 'pta_access_site': 'cfa_clean', 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'no_autovein_prosthetic', 'hb': 142, 'plt': 230, 'inr': 1.0, 'glucose': 5.4, 'creat': 82, 'ecg': 'normal', 'cta_disc': True},
        )
    if arch == 13:
        # Cardiologist non-compliant CAG order on low-risk asymptomatic EVAR
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVAR, age=66, sex='M', cardio_decision='cag', cardiologist='ტესტ კარდიოლოგი',
            q={'filled': True, 'htn': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 57, 'aaa_neck_status': 'friendly_ifu', 'popliteal_screen_status': 'normal', 'cta_disc': True, 'hb': 144, 'plt': 230, 'creat': 82, 'creat_unit': 'umol', 'inr': 1.0, 'lvef': 60, 'nt_probnp': 65, 'ecg': 'normal'},
        )
    # arch == 14: IDSA 2019 asymptomatic leukocyturia (urine WBC 18, nitrite-, no dysuria) on Open AAA -> NO false UTI red flag!
    return _make_unsaved_patient(
        idx, PreopPatient.OpSubtype.OPEN, age=69, sex='M', cardio_decision='cleared',
        q={'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
        s={'aaa_mm': 60, 'popliteal_screen_status': 'normal', 'cta_disc': True, 'hb': 140, 'plt': 220, 'creat': 86, 'creat_unit': 'umol', 'inr': 1.0, 'urine_wbc': 18, 'urine_nitrite': False, 'lvef': 58, 'ecg': 'normal', 'blood_reserved': True, 'decolonization_ready': True, 'anesth_done': True},
    )


def _arch_15_to_19(arch, idx):
    if arch == 15:
        # Giant 76mm AAA over 7 days with 100% completed workup -> 2D triage unlocks STATUS_READY + priority_tier='urgent'!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.OPEN, age=70, sex='M', started_days_ago=9, cardio_decision='cleared',
            q={'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 76, 'popliteal_screen_status': 'normal', 'cta_date': (SIM_TODAY - datetime.timedelta(days=10)).isoformat(), 'cta_disc': True, 'hb': 138, 'plt': 210, 'creat': 88, 'creat_unit': 'umol', 'inr': 1.02, 'lvef': 56, 'ecg': 'normal', 'blood_group': 'A (II) Rh+', 'blood_reserved': True, 'decolonization_ready': True, 'anesth_done': True},
        )
    if arch == 16:
        # Bilateral BP asymmetry (R 172/92, L 138/80 -> diff 34 mmHg) + GLP-1 + SGLT2 + Pacemaker
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.CEA_ASYMP, age=71, sex='M', side='L',
            q={'filled': True, 'dm': True, 'glp1': True, 'sglt2': True, 'pacemaker': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'bp_sys': 172, 'bp_dia': 92, 'bp_sys_l': 138, 'bp_dia_l': 80, 'carotid_r': 40, 'carotid_l': 82, 'cea_cta_status': 'concordant', 'cta_disc': True, 'hb': 136, 'plt': 215, 'creat': 92, 'inr': 1.0, 'glucose': 6.5, 'hba1c': 7.1, 'ecg': 'normal', 'cied_plan': 'magnet_async', 'planned_surgery_date': (SIM_TODAY + datetime.timedelta(days=10)).isoformat()},
        )
    if arch == 17:
        # Post-MI stent 6-12m (`stent_6_12m` + `mi`) without recent_stent -> ESC 12m DAPT guard!
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.ABF_CLAUD, age=64, sex='M',
            q={'filled': True, 'cad': True, 'cad_chips': ['mi', 'stent_6_12m'], 'med_aspirin': True, 'med_clopidogrel': True, 'med_chips': ['statin'], 'ic_bmt_3m': True, 'abf_duration': '> 3 თვე', 'mets_ok': True},
            s={'abi_r': 0.55, 'abi_l': 0.58, 'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'patent', 'cta_disc': True, 'hb': 140, 'plt': 220, 'creat': 88, 'inr': 1.0, 'lvef': 52, 'ecg': 'normal'},
        )
    if arch == 18:
        # Creatinine unit ambiguity (creat=24 without unit -> blocked!)
        return _make_unsaved_patient(
            idx, PreopPatient.OpSubtype.EVAR, age=70, sex='M',
            q={'filled': True, 'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True},
            s={'aaa_mm': 58, 'creat': 24, 'creat_unit': '', 'hb': 135, 'plt': 210, 'inr': 1.05, 'ecg': 'normal'},
        )
    # arch == 19: Neuraxial morning INR 1.46 (> ASRA 1.4 limit) -> DoS STOP danger!
    return _make_unsaved_patient(
        idx, PreopPatient.OpSubtype.BYPASS_CLTI, age=68, sex='F', side='R', approved=True,
        q={'filled': True, 'abf_rest_pain': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
        s={'abi_r': 0.42, 'abi_l': 0.95, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate', 'cta_disc': True, 'hb': 126, 'plt': 205, 'creat': 76, 'inr': 1.08, 'ecg': 'normal', 'anesthesia_plan': 'neuraxial', 'dos_identity_consent': True, 'dos_site_marked': True, 'dos_fasting_confirmed': True, 'dos_med_hold_verified': True, 'dos_abx_ready': True, 'dos_blood_implant_ready': True, 'dos_bp_sys': 130, 'dos_bp_dia': 78, 'dos_glucose': 5.8, 'dos_inr': 1.46},
    )


def _archetype_patient(rng, idx):
    """20 high-acuity clinical archetypes probing multi-system vascular/anesthesia traps."""
    arch = idx % 20
    if arch < 5:
        return _arch_0_to_4(arch, idx)
    if arch < 10:
        return _arch_5_to_9(arch, idx)
    if arch < 15:
        return _arch_10_to_14(arch, idx)
    return _arch_15_to_19(arch, idx)

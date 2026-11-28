"""Caprini VTE risk score calculator and safe perioperative prophylaxis advice."""
from ..models import PreopPatient
from .meds_freetext import _effective_allergy_state
from .registry import rule_value
from .utils import _num, _resolve_pathway, _str_list, cea_live_days_since_event, is_pregnancy_eligible


def _caprini_points(patient, q, bmi, today, s_cur, cad_set, has_hit):
    from .calculators import _s_raw, _sat_age, _sat_bmi_from, _sat_num

    cap = rule_value('SCORE_CAPRINI_VTE')
    lvef_red = rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    pts = 0
    age = _sat_age(patient.age)
    pts += 3 if age >= cap['age_3'] else (2 if age >= cap['age_2'] else (1 if age >= cap['age_1'] else 0))

    pw = _resolve_pathway(patient)
    if pw == PreopPatient.Pathway.VENOUS:
        pts += 1  # varicose veins
        if q.get('ven_ceap') in ('C3', 'C4', 'C5', 'C6'):
            pts += 1  # leg edema / chronic venous stasis
        pts += 2 if patient.op_subtype == PreopPatient.OpSubtype.PHLEB else 1
    elif pw == PreopPatient.Pathway.PAD_PTA:
        pts += 1
    else:
        pts += 2  # major vascular surgery >45 min

    bmi_v = _sat_bmi_from(bmi, s_cur)
    if bmi_v is not None and bmi_v > cap['bmi_1']:
        pts += 1
    if (patient.sex or '') == 'F' and q.get('ven_hormones'):
        pts += 1
    if is_pregnancy_eligible(patient) and (q.get('ven_pregnant') or q.get('pregnant_possible')):
        pts += 1
    pulm_chips = set(_str_list(q.get('pulm_chips')))
    if ('copd' in pulm_chips) or (q.get('pulm') and not pulm_chips):
        pts += 1  # Caprini 2005 awards +1 for COPD, not isolated asthma

    cea_d = cea_live_days_since_event(q, today=today, s=s_cur)
    if (
        pw == PreopPatient.Pathway.CEA
        and cea_d is not None and cea_d <= cap['stroke_days']
        and ('minor_stroke' in _str_list(q.get('cea_event_chips')) or bool(q.get('cea_disabling_stroke')))
    ):
        pts += 5  # acute stroke <1 month (remote stroke >1m = 0 pts per Caprini 2005)

    lvef_c = _sat_num(_s_raw(s_cur, 'lvef'), 5, 95)
    if ('hf' in cad_set) or (lvef_c is not None and lvef_c < lvef_red):
        pts += 1
    if 'mi' in cad_set:
        pts += 1
    if q.get('ven_cancer_immob'):
        pts += 2
    if q.get('ven_dvt_history') or bool(_str_list(q.get('ven_dvt_chips'))):
        pts += 3
    if q.get('ven_thrombophilia'):
        pts += 3
    if has_hit:
        pts += 3  # HIT is a +3 Caprini hypercoagulable state AND contraindicates all heparins!
    return pts


def _oac_vte_advice(is_evla_vka, has_contra_oac, has_hit, severe_renal, severe_liver, hit_dtis, comp_sfx):
    evla_inr_lim = rule_value('MED_WARFARIN_HOLD_DAYS')['inr_evla_max']
    if is_evla_vka:
        if has_hit:
            return f'თერაპიული ვარფარინი (OAC) გრძელდება მოუხსნელად (INR ≤ {evla_inr_lim}){comp_sfx} — დამატებითი პროფილაქტიკა არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე! HIT: ჰეპარინი/LMWH აკრძალულია!)'
        return f'თერაპიული ვარფარინი (OAC) გრძელდება მოუხსნელად (INR ≤ {evla_inr_lim}){comp_sfx} — დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე!)'
    if has_contra_oac:
        ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
        if has_hit and severe_renal:
            return f'უკუნაჩვენები OAC არ განაახლოთ — HIT + CrCl/eGFR <{ren_red}: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია! (IPC / სტაციონარული {hit_dtis}, ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
        if has_hit and severe_liver:
            return f'უკუნაჩვენები OAC არ განაახლოთ — HIT + ღვიძლის ≥3x: ჰეპარინი, DOAC და ფონდაპარინუქსი აკრძალულია! (IPC / IV ბივალირუდინი, ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
        if has_hit:
            return f'უკუნაჩვენები OAC არ განაახლოთ — არაჰეპარინული სქემა (ფონდაპარინუქსი ან ერთი უსაფრთხო OAC კარდიოლოგ/ჰემატოლოგთან, ორმაგი ანტიკოაგულაციის გარეშე! HIT: ჰეპარინი/LMWH აკრძალულია!){comp_sfx}'
        if severe_renal and severe_liver:
            return f'უკუნაჩვენები OAC არ განაახლოთ — CrCl/eGFR <{ren_red} + ღვიძლის ≥3x: DOAC/ფონდაპარინუქსი აკრძალულია! შეარჩიეთ კანქვეშა UFH/IPC კოაგულოგრამის კონტროლით კონსილიუმით (ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
        if severe_renal:
            return f'უკუნაჩვენები OAC არ განაახლოთ — CrCl/eGFR <{ren_red}: DOAC/ფონდაპარინუქსი აკრძალულია! შეარჩიეთ კანქვეშა UFH ან VKA კარდიოლოგ/ნეფროლოგთან (ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
        if severe_liver:
            return f'უკუნაჩვენები OAC არ განაახლოთ — ღვიძლის ≥3x: DOAC აკრძალულია! შეარჩიეთ უსაფრთხო სქემა კარდიოლოგ/ჰეპატოლოგთან (ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
        return f'უკუნაჩვენები/დუბლირებული OAC არ განაახლოთ — შეარჩიეთ ერთი უსაფრთხო ანტიკოაგულაციის სქემა კარდიოლოგთან (ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
    if has_hit:
        return f'მუდმივი თერაპიული OAC-ის აღდგენა ჰემოსტაზით (+ IPC აღდგენამდე){comp_sfx} — დამატებითი პროფილაქტიკა არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე! HIT: ჰეპარინი/LMWH აკრძალულია!)'
    return f'მუდმივი თერაპიული OAC-ის აღდგენა ჰემოსტაზით (+ IPC აღდგენამდე){comp_sfx} — დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე!)'


def _par_vte_advice(has_hit, severe_renal, severe_liver, hit_dtis, comp_sfx):
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    if has_hit:
        return f'აქტიური პარენტერული ანტიკოაგულაცია + HIT: ჰეპარინი/LMWH კატეგორიულად აკრძალულია — შეათანხმეთ ერთიანი არაჰეპარინული სქემა ({hit_dtis}) ჰემატოლოგთან (ორმაგი ანტიკოაგულაციის გარეშე!){comp_sfx}'
    if severe_renal and severe_liver:
        return f'აქტიური პარენტერული ანტიკოაგულაცია + CrCl/eGFR <{ren_red} + ღვიძლის ≥3x: LMWH/ფონდაპარინუქსი კუმულირდება — კოაგულოგრამის კონტროლით შეათანხმეთ ერთიანი სქემა (UFH) ქირურგთან (დამატებითი პროფილაქტიკური LMWH/DOAC არ დაუმატოთ!){comp_sfx}'
    if severe_renal:
        return f'აქტიური პარენტერული ანტიკოაგულაცია + CrCl/eGFR <{ren_red}: LMWH/ფონდაპარინუქსი კუმულირდება — შეათანხმეთ ერთიანი სქემა (UFH) ქირურგთან (დამატებითი პროფილაქტიკური LMWH/DOAC არ დაუმატოთ!){comp_sfx}'
    if severe_liver:
        return f'აქტიური პარენტერული ანტიკოაგულაცია + ღვიძლის ≥3x: კოაგულოგრამის კონტროლით ერთიანი სქემა ქირურგთან (დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ!){comp_sfx}'
    return f'პაციენტი უკვე იღებს პარენტერულ ანტიკოაგულანტს — შეათანხმეთ ერთიანი პოსტოპ. სქემა ქირურგთან (დამატებითი პროფილაქტიკური LMWH/DOAC არ დაუმატოთ!){comp_sfx}'


def _preg_vte_advice(is_high, has_hit, severe_renal, severe_liver, has_ft_par, ren_red, comp_sfx):
    ren_hit_s = f' · CrCl/eGFR <{ren_red}/დიალიზი: ფონდაპარინუქსი აკრძალულია!' if (has_hit and severe_renal) else ''
    liv_s = ' · ღვიძლის ≥3x: კოაგულოგრამის კონტროლით!' if severe_liver else ''
    if has_ft_par:
        if has_hit:
            return f'აქტიური პარენტერული ანტიკოაგულაცია + ორსულობა + HIT: ჰეპარინი/UFH/LMWH და DOAC აკრძალულია — შეათანხმეთ ერთიანი არაჰეპარინული სქემა მეან-გინეკოლოგთან და ჰემატოლოგთან (დამატებითი პროფილაქტიკა არ დაუმატოთ — ორმაგი ანტიკოაგულაციის გარეშე!){ren_hit_s}{liv_s}{comp_sfx}'
        if severe_renal:
            return f'აქტიური პარენტერული ანტიკოაგულაცია + ორსულობა + CrCl/eGFR <{ren_red}/დიალიზი: LMWH/ფონდაპარინუქსი კუმულირდება, DOAC აკრძალულია — შეათანხმეთ ერთიანი UFH სქემა მეან-გინეკოლოგთან (დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ — ორმაგი ანტიკოაგულაციის გარეშე!){liv_s}{comp_sfx}'
        return f'აქტიური პარენტერული ანტიკოაგულაცია ორსულობისას — შეათანხმეთ ერთიანი სქემა მეან-გინეკოლოგთან (DOAC აკრძალულია; დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ — ორმაგი ანტიკოაგულაციის გარეშე!){liv_s}{comp_sfx}'
    if is_high:
        if has_hit:
            return f'მექანიკური პროფილაქტიკა (IPC) / არაჰეპარინული სქემა მეან-გინეკოლოგთან და ჰემატოლოგთან (ორსულობა + HIT — ჰეპარინი და DOAC აკრძალულია!){ren_hit_s}{liv_s}{comp_sfx}'
        if severe_renal:
            return f'ფარმაკოლოგიური პროფილაქტიკა: კანქვეშა UFH ან anti-Xa კონტროლირებადი LMWH (ორსულობა + CrCl<{ren_red} — DOAC უკუნაჩვენებია!){liv_s}{comp_sfx}'
        return f'ფარმაკოლოგიური პროფილაქტიკა (მხოლოდ LMWH / UFH 7–10 დღე — ორსულობაზე DOAC უკუნაჩვენებია!){liv_s}{comp_sfx}'
    if has_hit:
        return f'ადრეული მობილიზაცია{comp_sfx} + IPC (ორსულობა + HIT — ჰეპარინი და DOAC აკრძალულია!){ren_hit_s}{liv_s}'
    if severe_renal:
        return f'ადრეული მობილიზაცია{comp_sfx} ± კანქვეშა UFH მეან-გინეკოლოგის შეფასებით (ორსულობა + CrCl<{ren_red}: LMWH კუმულირდება, DOAC უკუნაჩვენებია!){liv_s}'
    return f'ადრეული მობილიზაცია{comp_sfx} ± LMWH / UFH მეან-გინეკოლოგის შეფასებით (ორსულობაზე DOAC უკუნაჩვენებია!){liv_s}'


def _caprini_priority_advice(
    is_high, has_mech_valve, has_hit, severe_renal, severe_liver,
    cea_bleed_block, eff_pregnant, active_gi_bleed, has_full_oac, is_evla_vka, has_ft_par,
    rtpa_d, ren_red, comp_sfx, hit_dtis, hit_gi_sfx, preg_mv_txt, oac_vte_txt,
):
    if cea_bleed_block:
        return (
            f'მხოლოდ მექანიკური პროფილაქტიკა (IPC{comp_sfx}) — ტვინის სისხლჩაქცევის / rt-PA ≤{rtpa_d} დღის ფონზე ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია!'
            if is_high else
            f'მხოლოდ ადრეული მობილიზაცია + IPC{comp_sfx} (ტვინის სისხლჩაქცევა / rt-PA ≤{rtpa_d} დღე — ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია!)'
        )
    if active_gi_bleed:
        return (
            f'მხოლოდ მექანიკური პროფილაქტიკა (IPC{comp_sfx}) — აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე!{hit_gi_sfx}'
            if is_high else
            f'მხოლოდ ადრეული მობილიზაცია + IPC{comp_sfx} (აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე!){hit_gi_sfx}'
        )
    if has_mech_valve and eff_pregnant:
        return preg_mv_txt
    if has_full_oac and is_evla_vka:
        return oac_vte_txt
    if has_mech_valve:
        if has_hit:
            return f'მექანიკური სარქველი + HIT: {"სტაციონარული " if is_high else ""}{hit_dtis} და უვადო VKA (ჰეპარინი/LMWH და DOAC აკრძალულია!){comp_sfx}'
        if severe_renal:
            return f'მექანიკური სარქველი + CrCl/eGFR <{ren_red}: IV UFH ხიდი aPTT კონტროლით და უვადო VKA (DOAC აკრძალულია!){comp_sfx}'
        return (
            f'მექანიკური სარქველი: ვარფარინის (VKA) აღდგენა ± LMWH ხიდი კარდიოლოგის სქემით (DOAC აკრძალულია!){comp_sfx}'
            if is_high else
            f'ადრეული მობილიზაცია{comp_sfx} + უვადო ვარფარინის (VKA) რეჟიმი (DOAC აკრძალულია!)'
        )
    if eff_pregnant:
        return _preg_vte_advice(is_high, has_hit, severe_renal, severe_liver, has_ft_par, ren_red, comp_sfx)
    if has_full_oac or has_ft_par:
        return oac_vte_txt
    return None


def _caprini_tier_and_advice(
    pts, has_mech_valve, has_hit, severe_renal, severe_liver, compression_contra,
    cea_bleed_block=False, eff_pregnant=False, active_gi_bleed=False, has_full_oac=False, is_evla_vka=False, has_contra_oac=False, has_ft_par=False,
):
    cap_cfg = rule_value('SCORE_CAPRINI_VTE')
    rtpa_d, ren_red = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS'), rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    comp_sfx = '' if compression_contra else ' + კომპრესია'
    comp_low = ' (კომპრესია უკუნაჩვენებია არტერიული უკმარისობის გამო!)' if compression_contra else ' + ელასტიური კომპრესია'
    hit_dtis = 'IV ბივალირუდინი (არგატრობანი ღვიძლის ≥3x-ზე ერიდეთ/დოზა -75%!)' if severe_liver else 'IV ბივალირუდინი/არგატრობანი'
    hit_gi_sfx = ' (HIT: ჰეპარინი/LMWH აკრძალულია!)' if has_hit else ''
    mv_ren_hit = f' · CrCl<{ren_red}/დიალიზი: ფონდაპარინუქსი აკრძალულია!' if (has_hit and severe_renal) else ''
    mv_liv = ' · ღვიძლის ≥3x: კოაგულოგრამის კონტროლით — უპირატესია ბივალირუდინი (არგატრობანი მოერიდეთ)!' if (severe_liver and has_hit) else (' · ღვიძლის ≥3x: კოაგულოგრამის კონტროლით!' if severe_liver else '')
    preg_mv_txt = (
        f'ორსულობა + მექანიკური სარქველი + HIT: UFH, LMWH და DOAC აკრძალულია — მეან-გინეკოლოგის, კარდიოლოგისა და ჰემატოლოგის კონსილიუმი (IPC / არაჰეპარინული IV სქემა){mv_ren_hit}{mv_liv}{comp_sfx}'
        if has_hit else (
            f'ორსულობა + მექანიკური სარქველი + CrCl<{ren_red}/დიალიზი: DOAC აკრძალულია, LMWH კუმულირდება — მეან-გინეკოლოგის, კარდიოლოგისა და ნეფროლოგის კონსილიუმი (IV/SC UFH ან ტრიმესტრით VKA){mv_liv}{comp_sfx}'
            if severe_renal else
            f'ორსულობა + მექანიკური სარქველი: მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმი (DOAC აკრძალულია; VKA vs LMWH/UFH ტრიმესტრით){mv_liv}{comp_sfx}'
        )
    )
    oac_vte_txt = _oac_vte_advice(is_evla_vka, has_contra_oac, has_hit, severe_renal, severe_liver, hit_dtis, comp_sfx) if has_full_oac else _par_vte_advice(has_hit, severe_renal, severe_liver, hit_dtis, comp_sfx)
    is_high = pts >= cap_cfg['high_min']
    prio = _caprini_priority_advice(
        is_high, has_mech_valve, has_hit, severe_renal, severe_liver,
        cea_bleed_block, eff_pregnant, active_gi_bleed, has_full_oac, is_evla_vka, has_ft_par,
        rtpa_d, ren_red, comp_sfx, hit_dtis, hit_gi_sfx, preg_mv_txt, oac_vte_txt,
    )
    if is_high:
        tier = f"მაღალი (≥{cap_cfg['high_min']})"
        if prio:
            advice = prio
        elif has_hit:
            advice = (
                f'მექანიკური თრომბოპროფილაქტიკა (IPC) / სტაციონარული {hit_dtis} (HIT + CrCl/eGFR <{ren_red}: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!){comp_sfx}' if severe_renal else (
                    f'არაჰეპარინული პროფილაქტიკა: IPC / IV ბივალირუდინი (HIT: ჰეპარინი აკრძალულია; ღვიძლის ≥3x-ზე DOAC და ფონდაპარინუქსი არარეკომენდებულია!){comp_sfx}' if severe_liver
                    else f'არაჰეპარინული პროფილაქტიკა: ფონდაპარინუქსი ან DOAC 7–10 დღე (HIT: LMWH/ჰეპარინი აკრძალულია!){comp_sfx}'
                )
            )
        elif severe_renal and severe_liver:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა: კანქვეშა UFH + IPC კოაგულოგრამის კონტროლით (CrCl/eGFR <{ren_red} + ღვიძლის ≥3x: DOAC/ფონდაპარინუქსი აკრძალულია, LMWH კუმულირდება!){comp_sfx}'
        elif severe_renal:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა: კანქვეშა UFH 5000 სე ×2–3 ან თირკმელზე კორექტირებული LMWH anti-Xa კონტროლით (CrCl/eGFR <{ren_red}: DOAC/ფონდაპარინუქსი აკრძალულია!){comp_sfx}'
        elif severe_liver:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა: LMWH / UFH კოაგულოგრამის კონტროლით (ღვიძლის ≥3x-ზე DOAC უკუნაჩვენებია!){comp_sfx}'
        else:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა (LMWH / DOAC 7–10 დღე){comp_sfx}'
    elif pts > cap_cfg['low_max']:
        tier = f"საშუალო ({cap_cfg['low_max'] + 1}–{cap_cfg['mod_max']})"
        if prio:
            advice = prio
        elif has_hit:
            advice = (
                f'ადრეული მობილიზაცია{comp_sfx} + IPC (HIT + CrCl/eGFR <{ren_red}: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!)' if severe_renal else (
                    f'ადრეული მობილიზაცია{comp_sfx} + IPC / ბივალირუდინი (HIT + ღვიძლის ≥3x: ჰეპარინი, DOAC და ფონდაპარინუქსი არა!)' if severe_liver
                    else f'ადრეული მობილიზაცია{comp_sfx} ± ფონდაპარინუქსი/DOAC (HIT: ჰეპარინი აკრძალულია!)'
                )
            )
        elif severe_renal and severe_liver:
            advice = f'ადრეული მობილიზაცია{comp_sfx} + IPC ± კანქვეშა UFH კოაგულოგრამის კონტროლით (CrCl/eGFR <{ren_red} + ღვიძლის ≥3x: DOAC/ფონდაპარინუქსი აკრძალულია, LMWH კუმულირდება!)'
        elif severe_renal:
            advice = f'ადრეული მობილიზაცია{comp_sfx} ± კანქვეშა UFH 5000 სე ×2–3 (LMWH კუმულირდება — მხოლოდ anti-Xa კონტროლით! CrCl/eGFR <{ren_red}: DOAC არა!)'
        elif severe_liver:
            advice = f'ადრეული მობილიზაცია{comp_sfx} ± LMWH ქირურგის შეფასებით (ღვიძლის ≥3x-ზე DOAC არა!)'
        else:
            advice = f'ადრეული მობილიზაცია{comp_sfx} ± LMWH/DOAC ქირურგის შეფასებით'
    else:
        tier = f"დაბალი (0–{cap_cfg['low_max']})"
        advice = prio or f'ადრეული სიარული{comp_low} (რუტინული ჰეპარინის გარეშე)'
    return tier, advice


def _oac_venous_vte_flags(patient, q_d, s_cur, severe_renal, severe_liver, active_gi_bleed=False, eff_preg=False):
    from .meds_freetext import _detect_freetext_high_risk_meds
    from .meds_helpers import _vka_inr_status
    ft_m = _detect_freetext_high_risk_meds(q_d.get('other_meds') or '', q=q_d)
    has_ft_full_oac = bool(ft_m.get('has_ft_full_oac'))
    ft_unstruct = ft_m.get('unstructured_keys') or set()
    has_full_oac = bool(q_d.get('med_warfarin') or q_d.get('med_doac') or q_d.get('med_dabigatran') or has_ft_full_oac)
    med_set = set(_str_list(q_d.get('med_chips')))
    oac_cnt = (
        sum([bool(q_d.get('med_warfarin')), bool(q_d.get('med_doac')), bool(q_d.get('med_dabigatran')), ('rivaroxaban_2_5' in med_set)])
        + len(ft_unstruct & {'warfarin', 'doac', 'dabigatran'})
        + (1 if (ft_m.get('has_ft_dual_xa') or (ft_m.get('has_ft_unstruct_doac') and ft_m.get('has_ft_unstruct_riva_low'))) else 0)
    )
    _, dos_inr_bad, base_inr_bad, inr_c = _vka_inr_status(patient, s_cur)
    inr_bad = dos_inr_bad or base_inr_bad
    evla_max_inr = rule_value('MED_WARFARIN_HOLD_DAYS')['inr_evla_max']
    mech_inr_min = rule_value('MED_MECH_VALVE_SAFETY')
    has_mv_c = 'mech_valve' in set(_str_list(q_d.get('cad_chips')))
    evla_subther_mv = bool(has_mv_c and inr_c is not None and inr_c < mech_inr_min)
    has_nx_plan = (s_cur.get('anesthesia_plan') or 'auto') == 'neuraxial' or ('epidural' in _str_list(s_cur.get('anesth_lines_chips')))
    has_ft_par = bool((ft_m.get('matched_keys') or set()) & {'parenteral_oac', 'parenteral_dti'})
    is_evla_vka = bool(
        patient.op_subtype == PreopPatient.OpSubtype.EVLA
        and not has_nx_plan and not active_gi_bleed and not eff_preg
        and q_d.get('med_warfarin') and oac_cnt == 1 and not has_ft_par and not severe_liver and not inr_bad and not evla_subther_mv
        and (inr_c is None or inr_c <= evla_max_inr)
    )
    ft_doac = bool(ft_unstruct & {'doac', 'dabigatran'}) and not ft_m.get('has_ft_riva_low')
    has_contra_oac = bool(
        oac_cnt >= 2
        or ((q_d.get('med_doac') or q_d.get('med_dabigatran') or ft_doac) and (severe_renal or severe_liver or has_ft_par))
        or (not is_evla_vka and (q_d.get('med_warfarin') or ft_m.get('has_ft_unstruct_warfarin')) and patient.op_subtype == PreopPatient.OpSubtype.EVLA and (inr_bad or evla_subther_mv or (inr_c is not None and inr_c > evla_max_inr)))
    )
    return has_full_oac, is_evla_vka, has_contra_oac, has_ft_par


def calc_caprini(patient, q, bmi=None, today=None, eff_renal=None, liver_status='normal', compression_contra=False):
    """Auto-calculate numeric Caprini VTE score (with HIT +3 & acute stroke <1m +5) and safe VTE advice."""
    q_d = q if isinstance(q, dict) else {}
    s_cur = patient.studies if isinstance(patient.studies, dict) else {}
    cad_set = set(_str_list(q_d.get('cad_chips')))
    eff_alg, _, _ = _effective_allergy_state(q_d)
    has_hit = 'hit_heparin' in eff_alg
    pts = _caprini_points(patient, q_d, bmi, today, s_cur, cad_set, has_hit)
    eff_ren_raw = _num(eff_renal)
    eff_ren_v = max(0.0, eff_ren_raw) if eff_ren_raw is not None else None
    ren_red, rtpa_d = rule_value('REN_CKD_NEPHRO_HYDRATION')['red'], rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
    severe_renal = bool(q_d.get('on_dialysis')) or (eff_ren_v is not None and eff_ren_v < ren_red)
    severe_liver = (liver_status or s_cur.get('liver_status') or 'normal') == '3x'
    cea_d = cea_live_days_since_event(q_d, today=today, s=s_cur)
    cea_bleed_block = _resolve_pathway(patient) == PreopPatient.Pathway.CEA and (
        s_cur.get('brain_imaging') == 'large_or_hemorrhage' or (bool(q_d.get('cea_thrombolysis')) and (cea_d is None or cea_d <= rtpa_d))
    )
    active_gi_bleed = bool(s_cur.get('gastro_result') == 'acute_ulcer' or (q_d.get('ulcer_active_symp') and s_cur.get('gastro_result') != 'remission'))
    eff_preg = is_pregnancy_eligible(patient) and bool(q_d.get('pregnant_possible') or q_d.get('ven_pregnant'))
    has_full_oac, is_evla_vka, has_contra_oac, has_ft_par = _oac_venous_vte_flags(
        patient, q_d, s_cur, severe_renal, severe_liver, active_gi_bleed=active_gi_bleed, eff_preg=eff_preg,
    )
    tier, advice = _caprini_tier_and_advice(
        pts, 'mech_valve' in cad_set, has_hit, severe_renal, severe_liver, compression_contra,
        cea_bleed_block=cea_bleed_block, eff_pregnant=eff_preg, active_gi_bleed=active_gi_bleed,
        has_full_oac=has_full_oac, is_evla_vka=is_evla_vka, has_contra_oac=has_contra_oac, has_ft_par=has_ft_par,
    )
    return pts, tier, advice

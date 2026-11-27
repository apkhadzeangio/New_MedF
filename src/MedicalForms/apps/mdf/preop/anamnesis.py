"""Structured Georgian preoperative anamnesis generator and Self-Fill highlights extractor."""
from ..board import person_name
from ..models import PreopPatient
from .utils import _int, _num, _str_list, cea_live_days_since_event, chip_summary, is_self_filled_q

_SF_SIMPLE_FLAGS = (
    ('aaa_pain', 'urgent', 'მუცლის/წელის ტკივილი (AAA)!'),
    ('cea_hoarseness', 'warn', 'ხმის ჩახლეჩა / დისფაგია (CEA)'),
    ('cea_prior_neck', 'warn', 'კისრის წინა ოპერაცია / დასხივება (CEA)'),
    ('abf_rest_pain', 'warn', 'მოსვენებითი იშემიური ტკივილი'),
    ('abf_ulcer', 'warn', 'ტერფის წყლული/ნეკროზი'),
    ('abf_wet_infection', 'urgent', 'ტერფის სველი ნეკროზი / ჩირქოვანი ინფექცია!'),
    ('pta_cant_lie_flat', 'warn', 'ზურგზე ვერ წვება (ორთოპნოე / ტკივილი)'),
    ('ven_acute_svt', 'urgent', 'მწვავე თრომბოფლებიტი!'),
    ('ven_skin_infection', 'urgent', 'კანის ინფექცია / სველი ვენური წყლული!'),
    ('ven_lidocaine_allergy', 'urgent', 'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე!'),
    ('ven_bleeding', 'urgent', 'სისხლდენა ვარიკოზული კვანძიდან!'),
    ('ven_dvt_history', 'warn', 'გადატანილი DVT / PE'),
    ('ven_thrombophilia', 'warn', 'თრომბოფილია'),
    ('ven_hormones', 'warn', 'ჰორმონული პრეპარატი / კონტრაცეპტივი'),
    ('ven_cancer_immob', 'warn', 'ონკოლოგია / იმობილიზაცია'),
    ('active_infection', 'urgent', 'სიცხე / აქტიური ინფექცია!'),
    ('dental_infection', 'warn', 'კარიესული/ჩირქოვანი კბილი'),
    ('anesth_issue', 'warn', 'ანესთეზიის გართულება ანამნეზში'),
    ('on_dialysis', 'urgent', 'პროგრამული დიალიზი'),
    ('htn', 'info', 'ჰიპერტენზია'),
    ('pulm', 'info', 'ფილტვების დაავადება / ასთმა'),
    ('thyroid_disease', 'info', 'ფარისებრი ჯირკვალი'),
    ('neuro', 'warn', 'გადატანილი ინსულტი'),
    ('pacemaker', 'warn', 'კარდიოსტიმულატორი / ICD'),
)
_SF_MED_BOOLS = (
    ('med_warfarin', 'ვარფარინი'), ('med_dabigatran', 'პრადაქსა'), ('med_doac', 'ქსარელტო/ელიქვისი'),
    ('med_prasugrel', 'პრასუგრელი'), ('med_ticagrelor', 'ტიკაგრელორი'), ('med_clopidogrel', 'კლოპიდოგრელი'),
    ('med_aspirin', 'ასპირინი'), ('sglt2', 'SGLT2 (ჯარდიანსი/ფორსიგა)'),
    ('glp1', 'GLP-1 (ოზემპიკი/მუნჯარო)'), ('metformin', 'მეტფორმინი'),
)
_SF_MED_CHIP_LABELS = (
    ('rivaroxaban_2_5', 'რივაროქსაბანი 2.5 მგ ×2'), ('cilostazol', 'ცილოსტაზოლი'),
    ('steroid', 'სტეროიდი'), ('amiodarone', 'ამიოდარონი'),
)


_SF_CHIP_FALLBACKS = {
    'cea_prior_neck': 'cea_neck_chips',
    'abf_ulcer': 'ulcer_chips',
    'ven_dvt_history': 'ven_dvt_chips',
    'active_infection': 'inf_chips',
    'pulm': 'pulm_chips',
}


def build_self_fill_highlights(q):
    """Extract concise positive findings reported by the patient via Self-Fill link for 5-second nurse reconciliation."""
    if not isinstance(q, dict) or not (q.get('filled') or is_self_filled_q(q)):
        return []
    items = []
    if q.get('cea_crescendo'):
        items.append(('urgent', 'განმეორებითი TIA/სიმპტომი <48 სთ!'))
    elif q.get('cea_symp_event') or q.get('cea_event_chips'):
        items.append(('warn', 'ნევროლოგიური სიმპტომი ანამნეზში'))
    if q.get('cea_thrombolysis'):
        from .registry import rule_value
        rtpa_d = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
        ev_d = cea_live_days_since_event(q)
        if ev_d is None or ev_d <= rtpa_d:
            items.append(('urgent', f'თრომბოლიზისი ≤{rtpa_d} დღეში!'))
        else:
            items.append(('warn', f'თრომბოლიზისი ანამნეზში (>{rtpa_d} დღე)'))
    if q.get('ven_pregnant') or q.get('pregnant_possible'):
        items.append(('urgent', 'ორსულობა / შესაძლო ორსულობა / ლაქტაცია!'))
    cad_ch = set(_str_list(q.get('cad_chips')))
    if q.get('recent_stent'):
        items.append(('urgent', 'კორონარული სტენტი <6 თვეში!'))
    elif q.get('cad') or (cad_ch - {'mech_valve'}):
        items.append(('warn', 'გულის დაავადება / სტენტი / არითმია'))
    if 'mech_valve' in cad_ch:
        items.append(('urgent', 'მექანიკური სარქველი!'))
    if not q.get('mets_ok', True):
        items.append(('warn', f"დატვირთვის შეზღუდვა ({'კიდურით' if q.get('mets_limitation') == 'limb' else '<4 METs ქოშინი/გული'})"))
    if q.get('dm'):
        items.append(('info', 'შაქრიანი დიაბეტი' + (' (ინსულინზე)' if q.get('dm_insulin') else '')))
    elif q.get('dm_insulin'):
        items.append(('warn', 'ინსულინი'))
    if q.get('ulcer_active_symp'):
        items.append(('urgent', 'აქტიური კუჭის ტკივილი / მელენა!'))
    elif q.get('ulcer'):
        items.append(('info', 'კუჭის წყლული ანამნეზში'))
    for key, lvl, label in _SF_SIMPLE_FLAGS:
        fb = _SF_CHIP_FALLBACKS.get(key)
        if q.get(key) or (fb and q.get(fb)):
            items.append((lvl, label))
    if q.get('abdo_chips') and not q.get('prev_abdo_surg'):
        items.append(('info', 'მუცლის/საზარდულის წინა ოპერაციები'))
    if q.get('ven_rec_chips') and not q.get('ven_recurrent'):
        items.append(('info', 'რეციდივული ვარიკოზი'))

    med_chips = set(_str_list(q.get('med_chips')))
    meds = [lbl for key, lbl in _SF_MED_BOOLS if q.get(key)]
    meds.extend(lbl for ck, lbl in _SF_MED_CHIP_LABELS if ck in med_chips)
    if meds:
        items.append(('warn', 'მედიკამენტები: ' + ', '.join(meds)))
    raw_other_meds = q.get('other_meds') if isinstance(q.get('other_meds'), str) else ''
    if raw_other_meds.strip():
        from .meds import _detect_freetext_high_risk_meds
        ft = _detect_freetext_high_risk_meds(raw_other_meds, q=q)
        lvl = 'urgent' if ft.get('unstructured_labels') else 'info'
        items.append((lvl, f'სხვა წამალი: {raw_other_meds}'))
    if q.get('allergy_iodine'):
        items.append(('urgent', 'ალერგია იოდზე / კონტრასტზე!'))
    raw_alg_txt = q.get('allergy_text') if isinstance(q.get('allergy_text'), str) else ''
    if q.get('allergy_other') or q.get('allergy_chips') or raw_alg_txt.strip():
        alg_txt = chip_summary(q.get('allergy_chips'), 'allergy_chips', raw_alg_txt) or 'მედიკამენტური ალერგია'
        items.append(('urgent', f'ალერგია: {alg_txt}'))
    if q.get('smoker'):
        items.append(('info', 'მწეველი'))
    return items


def _anam_cea(patient, ev, q, s, head_prefix, surgeon_str):
    from .registry import rule_value
    rtpa_d = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
    lines = []
    side_s = patient.side if isinstance(patient.side, str) else ''
    side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა '}.get(side_s, '')
    is_symp = bool(ev.get('is_symp_cea'))
    kind_word = 'სიმპტომური სტენოზის გამო' if is_symp else 'ასიმპტომური სტენოზის გამო'
    lines.append(f'{head_prefix}, დაგეგმილია {side_word}კაროტიდული ენდარტერექტომია ({kind_word}){surgeon_str}.')

    live_ev_d = ev.get('days_since_ev')
    extra_neuro = []
    if q.get('cea_crescendo'):
        extra_neuro.append('აღინიშნება მერყევი/განმეორებითი დეფიციტი ბოლო 24–48 სთ-ში!')
    if q.get('cea_thrombolysis'):
        if ev.get('active_thrombolysis_window') or live_ev_d is None or live_ev_d <= rtpa_d:
            extra_neuro.append(f'ჩატარებულია თრომბოლიზისი ბოლო {rtpa_d} დღეში!')
        else:
            extra_neuro.append(f'ანამნეზში ჩატარებულია თრომბოლიზისი (>{rtpa_d} დღის წინ)')
    if q.get('cea_disabling_stroke'):
        extra_neuro.append('ნარჩენი დეფიციტი mRS >= 3')
    if is_symp:
        ev_txt = chip_summary(q.get('cea_event_chips'), 'cea_event_chips', q.get('cea_event_text')) or 'TIA / ამავროზი / მცირე იშემიური ინსულტი'
        d_ago = f', ბოლო ეპიზოდი {live_ev_d} დღის წინ' if live_ev_d is not None else ''
        extra_str = f" ({'; '.join(extra_neuro)})" if extra_neuro else ''
        lines.append(f'ნევროლოგიური სტატუსი: სიმპტომური ({ev_txt}{d_ago}){extra_str}.')
    elif extra_neuro:
        lines.append(f"ნევროლოგიური სტატუსი: საყურადღებოა ({'; '.join(extra_neuro)}).")
    else:
        lines.append('ნევროლოგიური სტატუსი: ასიმპტომურია, ბოლო 6 თვეში TIA-ს, ამავროზსა და ინსულტს უარყოფს.')

    cta_map = {
        'concordant': 'დუპლექსთან შესაბამისი, სტანდარტული ბიფურკაცია',
        'high_calcified': 'მაღალი ბიფურკაცია / კალციფიკაცია / კინკინგი',
        'pending': 'ზუსტდება',
    }
    br_map = {
        'normal_small': 'მწვავე სისხლჩაქცევის გარეშე / მცირე კერა',
        'large_or_hemorrhage': 'ფართო ინფარქტი ან ჰემორაგია (!)',
        'pending': 'ზუსტდება',
    }
    disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
    car_r_c = _int(s.get('carotid_r'), 0, 100)
    car_l_c = _int(s.get('carotid_l'), 0, 100)
    lines.append(
        f"საძილეები და ვიზუალიზაცია (NASCET): R ICA {car_r_c if car_r_c is not None else '—'}% / "
        f"L ICA {car_l_c if car_l_c is not None else '—'}%; "
        f"კისრის CTA — {cta_map.get(s.get('cea_cta_status') or 'pending', 'ზუსტდება')}; "
        f"თავის ტვინის CT/MRI — {br_map.get(s.get('brain_imaging') or 'pending', 'ზუსტდება')} ({disc_str})."
    )

    neck_bits = []
    if q.get('cea_prior_neck') or q.get('cea_neck_chips'):
        neck_txt = chip_summary(q.get('cea_neck_chips'), 'cea_neck_chips', q.get('cea_prior_neck_text')) or 'აღნიშნავს'
        neck_bits.append(f'კისრის წინა ჩარევა/დასხივება: {neck_txt}')
    if q.get('cea_hoarseness'):
        neck_bits.append('აღნიშნავს ხმის ჩახლეჩას / დისფაგიას')
    if q.get('cea_neck_limited'):
        neck_bits.append('კისრის გაშლა შეზღუდულია')
    if (s.get('laryngoscopy_res') or 'pending') != 'pending':
        l_lbl = 'სახმო იოგები მოძრავია' if s.get('laryngoscopy_res') == 'normal' else 'სახმო იოგის პარეზი (!)'
        neck_bits.append(f'ლარინგოსკოპია: {l_lbl}')
    if neck_bits:
        lines.append('; '.join(neck_bits) + '.')
    return lines


def _anam_abf(patient, q, s, head_prefix, surgeon_str, abdo_txt, vasc_txt, ulcer_txt):
    lines = []
    sub_lbl = 'კრიტიკული იშემიის — CLTI გამო' if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI else 'ლერიშის სინდრომის / მძიმე კოჭლობის გამო'
    lines.append(f'{head_prefix}, დაგეგმილია აორტო-ბიფემორალური შუნტირება ({sub_lbl}){surgeon_str}.')

    claud_m_v = _int(q.get('abf_claud_m'), 0, 5000)
    claud_part = f'ხანგამოშვებითი კოჭლობა {claud_m_v} მ-ში' if claud_m_v is not None else 'ხანგამოშვებითი კოჭლობა'
    if q.get('abf_buttock_pain'):
        claud_part += ' (დუნდულოებისა და ბარძაყების არეში — ლერიშის სინდრომი)'
    if q.get('abf_duration'):
        claud_part += f", ხანგრძლივობა: {q['abf_duration']}"
        if q.get('abf_duration') == '< 2 კვირა':
            claud_part += ' — საეჭვოა მწვავე იშემია (ALI)!'
    rest_part = 'აღნიშნავს მოსვენებით/ღამის იშემიურ ტკივილს' if q.get('abf_rest_pain') else 'მოსვენებით ტკივილს უარყოფს'
    lines.append(f'სისხლძარღვოვანი ჩივილები: {claud_part}; {rest_part}.')

    if q.get('abf_ulcer') or q.get('ulcer_chips'):
        wet_str = 'სველი/ჩირქოვანი გამონადენით (!)' if q.get('abf_wet_infection') else 'მშრალი, მწვავე ჩირქოვანი ნიშნების გარეშე'
        lines.append(f"ტროფიკული სტატუსი: წყლული/ნეკროზი ({ulcer_txt or 'ტერფზე'}), {wet_str}.")
    groin_str = 'საზარდულის ნაკეცებში ინტერტრიგო/დაზიანება (!)' if q.get('abf_groin_issue') else 'საზარდულის კანი ინტაქტურია'
    surg_bits = [groin_str]
    if q.get('abf_prior_vasc') or q.get('vasc_chips'):
        surg_bits.append(f"წინა სისხლძარღვოვანი ჩარევა: {vasc_txt or 'აღნიშნავს'}")
    if q.get('prev_abdo_surg') or q.get('abdo_chips'):
        surg_bits.append(f"მუცლის ღრუს ოპერაცია: {abdo_txt or 'აღნიშნავს'}")
    lines.append('; '.join(surg_bits) + '.')

    clamp_map = {'infrarenal': 'ინფრარენალური საკლამპე ზონა', 'juxtarenal_calcified': 'იუქსტარენალური ოკლუზია / მძიმე კალციფიკაცია (!)', 'pending': 'ზუსტდება'}
    out_map = {'patent': 'CFA/Profunda ორმხრივ გამავალია', 'profunda_stenosis': 'CFA/Profunda სტენოზი (პროფუნდოპლასტიკა)', 'poor_outflow': 'მძიმე დისტალური დაზიანება (!)', 'pending': 'ზუსტდება'}
    disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
    abi_r_a = _num(s.get('abi_r'), 0.0, 3.0)
    abi_l_a = _num(s.get('abi_l'), 0.0, 3.0)
    lines.append(
        f"ჰემოდინამიკა და CTA: ABI R {abi_r_a if abi_r_a is not None else '—'} / "
        f"L {abi_l_a if abi_l_a is not None else '—'}; "
        f"აორტა — {clamp_map.get(s.get('abf_clamp_zone') or 'pending', 'ზუსტდება')}; "
        f"Outflow — {out_map.get(s.get('abf_outflow') or 'pending', 'ზუსტდება')} ({disc_str})."
    )
    return lines


def _anam_pad(patient, pathway, q, s, head_prefix, surgeon_str, ulcer_txt):
    from .registry import rule_value
    gsv_min = rule_value('PAD_GSV_MIN_MM')['gsv_min_mm']
    lines = []
    side_s = patient.side if isinstance(patient.side, str) else ''
    side_word = {'R': 'მარჯვენა ქვემო კიდურის ', 'L': 'მარცხენა ქვემო კიდურის ', 'B': 'ორმხრივი '}.get(side_s, '')
    is_clti = patient.op_subtype in (PreopPatient.OpSubtype.BYPASS_CLTI, PreopPatient.OpSubtype.PTA_CLTI)
    ind_lbl = 'კრიტიკული იშემიის — CLTI გამო' if is_clti else 'ხანგამოშვებითი კოჭლობის გამო'
    op_name = 'პერიფერიული არტერიული შუნტირება' if pathway == PreopPatient.Pathway.PAD_BYPASS else 'პერიფერიული ანგიოპლასტიკა / სტენტირება (PTA)'
    lines.append(f'{head_prefix}, დაგეგმილია {side_word}{op_name} ({ind_lbl}){surgeon_str}.')

    claud_m_p = _int(q.get('abf_claud_m'), 0, 5000)
    claud_part = f'კოჭლობა {claud_m_p} მ-ში' if claud_m_p is not None else 'ხანგამოშვებითი კოჭლობა'
    if q.get('abf_duration'):
        claud_part += f", ხანგრძლივობა: {q['abf_duration']}"
        if q.get('abf_duration') == '< 2 კვირა':
            claud_part += ' — საეჭვოა მწვავე იშემია (ALI)!'
    rest_part = 'მოსვენებითი ტკივილი (+)' if q.get('abf_rest_pain') else 'მოსვენებით ტკივილს უარყოფს'
    lines.append(f'სისხლძარღვოვანი ჩივილები: {claud_part}; {rest_part}.')
    if q.get('abf_ulcer') or q.get('ulcer_chips'):
        wet_str = 'სველი/ჩირქოვანი ინფექციით (!)' if q.get('abf_wet_infection') else 'მწვავე ჩირქოვანი გამონადენის გარეშე'
        lines.append(f"ტროფიკული სტატუსი: {ulcer_txt or 'ტერფის წყლული/ნეკროზი'}, {wet_str}.")

    abi_r_v = _num(s.get('abi_r'), 0.0, 3.0)
    abi_l_v = _num(s.get('abi_l'), 0.0, 3.0)
    abi_r_s = abi_r_v if abi_r_v is not None else '—'
    abi_l_s = abi_l_v if abi_l_v is not None else '—'
    if pathway == PreopPatient.Pathway.PAD_BYPASS:
        run_map = {'popliteal_patent': 'მუხლქვეშა არტერია გამავალია', 'tibial_single': 'წვივის არტერიაზე დისტალური მიმღები', 'desert_foot': 'არადამაკმაყოფილებელი კალაპოტი (!)', 'pending': 'ზუსტდება'}
        gsv_map = {'adequate': 'ვარგისია (≥3.0 მმ)', 'inadequate': 'უვარგისია (<3.0 მმ / ამოღებულია!)', 'pending': 'ზუსტდება'}
        gsv_val = _num(s.get('pad_gsv_mm'), 0.5, 30.0)
        gsv_stripped = 'vein_strip' in _str_list(q.get('vasc_chips'))
        gsv_eff_st = 'inadequate' if (gsv_stripped or (gsv_val is not None and gsv_val < gsv_min)) else (s.get('pad_gsv_status') or 'pending')
        gsv_d = f' ({gsv_val} მმ)' if gsv_val is not None else ''
        lines.append(
            f"ანატომია და აუტოვენა: ABI R {abi_r_s} / L {abi_l_s}; "
            f"Runoff — {run_map.get(s.get('pad_runoff_status') or 'pending', 'ზუსტდება')}; "
            f"იფსილატერალური GSV — {gsv_map.get(gsv_eff_st, 'ზუსტდება')}{gsv_d}."
        )
    else:
        acc_map = {'cfa_clean': 'საერთო ბარძაყის არტერია სუფთაა', 'cfa_calcified_scar': 'CFA ნაწიბური/კალციფიკაცია (ალტერნატიული მიდგომა)', 'pending': 'ზუსტდება'}
        lines.append(
            f"ჰემოდინამიკა და მიდგომა: ABI R {abi_r_s} / L {abi_l_s}; "
            f"პუნქციის ზონა — {acc_map.get(s.get('pta_access_site') or 'pending', 'ზუსტდება')}."
        )
    return lines


def _anam_venous(patient, ev, q, s, head_prefix, surgeon_str):
    lines = []
    side_s = patient.side if isinstance(patient.side, str) else ''
    side_word = {'R': 'მარჯვენა ქვემო კიდურზე ', 'L': 'მარცხენა ქვემო კიდურზე ', 'B': 'ორივე ქვემო კიდურზე '}.get(side_s, '')
    anesth_tag = ev.get('effective_anesth_label') or (
        'ტუმესცენტური ანესთეზია' if patient.op_subtype == PreopPatient.OpSubtype.EVLA else 'სპინალური / ზოგადი ანესთეზია'
    )
    op_lbl = (
        f'ენდოვენური ლაზერული აბლაცია (EVLA ± მინიფლებექტომია, {anesth_tag})'
        if patient.op_subtype == PreopPatient.OpSubtype.EVLA
        else f'კომბინირებული ფლებექტომია / კროსექტომია ({anesth_tag})'
    )
    lines.append(f'{head_prefix}, დაგეგმილია {side_word}{op_lbl}{surgeon_str}.')

    ceap = q.get('ven_ceap') or 'C2'
    vein_map = {
        'gsv': 'დიდი საჩინო ვენა (GSV)',
        'ssv': 'მცირე საჩინო ვენა (SSV)',
        'gsv_ssv': 'დიდი და მცირე საჩინო ვენები (GSV + SSV)',
        'aagsv_trib': 'წინა დამატებითი საჩინო ვენა / შენაკადები',
    }
    dup_map = {
        'deep_patent_reflux': 'ღრმა ვენები გამავალია, ზედაპირული სარქვლოვანი რეფლუქსი დადასტურებულია',
        'acute_dvt': 'მწვავე ღრმა ვენების თრომბოზი (DVT — უკუჩვენება!)',
        'deep_obstruction': 'ღრმა ვენების პოსტთრომბოზული ოკლუზია (უკუჩვენება!)',
        'superficial_thrombus': 'მწვავე აღმავალი თრომბოფლებიტი (!)',
        'pending': 'დუპლექსის მონაცემი შესატანია',
    }
    gsv_v_mm = _num(s.get('ven_gsv_mm'), 1.0, 50.0)
    diam_str = f', მაქს. დიამეტრი {gsv_v_mm} მმ' if gsv_v_mm is not None else ''
    lines.append(
        f"ვენური სტატუსი: CEAP {ceap} (Caprini ქულა: {ev['caprini_score']} — {ev['caprini_tier']}); "
        f"სამიზნე აუზი — {vein_map.get(s.get('ven_target_vein') or 'gsv', 'GSV')}{diam_str}; "
        f"დუპლექსი — {dup_map.get(s.get('ven_duplex_status') or 'pending', 'ზუსტდება')}."
    )

    ven_notes = []
    v_dup = s.get('ven_duplex_status') or 'pending'
    if v_dup == 'acute_dvt':
        ven_notes.append('დუპლექსით დადასტურებული მწვავე DVT (!)')
    elif v_dup == 'deep_obstruction':
        ven_notes.append('დუპლექსით ღრმა ვენების პოსტთრომბოზული ოკლუზია (!)')
    elif v_dup == 'superficial_thrombus':
        ven_notes.append('დუპლექსით მწვავე აღმავალი თრომბოფლებიტი (!)')
    if q.get('ven_dvt_history') or q.get('ven_dvt_chips'):
        dvt_txt = chip_summary(q.get('ven_dvt_chips'), 'ven_dvt_chips', q.get('ven_dvt_text')) or 'აღნიშნავს'
        ven_notes.append(f'ანამნეზში DVT/PE ({dvt_txt})')
    if q.get('ven_thrombophilia'):
        ven_notes.append('თრომბოფილია / ოჯახური VTE')
    if q.get('ven_acute_svt') and v_dup != 'superficial_thrombus':
        ven_notes.append('მწვავე თრომბოფლებიტი (!)')
    if q.get('ven_bleeding'):
        ven_notes.append('სისხლდენა კვანძიდან ანამნეზში')
    if q.get('ven_recurrent') or q.get('ven_rec_chips'):
        rec_txt = chip_summary(q.get('ven_rec_chips'), 'ven_rec_chips', q.get('ven_recurrent_text')) or 'წინა ჩარევის შემდეგ'
        ven_notes.append(f'რეციდივული ვარიკოზი ({rec_txt})')
    if q.get('ven_hormones'):
        ven_notes.append('ჰორმონული პრეპარატები/კონტრაცეპტივი')
    if q.get('ven_cancer_immob'):
        ven_notes.append('ონკოლოგია / ხანგრძლივი იმობილიზაცია')
    if q.get('ven_skin_infection'):
        ven_notes.append('მწვავე კანის ინფექცია / წითელი ქარი (!)')
    from .utils import is_pregnancy_eligible
    if (q.get('ven_pregnant') or q.get('pregnant_possible')) and is_pregnancy_eligible(patient):
        ven_notes.append('ორსულობა / ლაქტაცია (!)')
    if ven_notes:
        lines.append('ვენური და თრომბოემბოლიური ფაქტორები: ' + '; '.join(ven_notes) + '.')
    else:
        lines.append('DVT/PE-ს, მწვავე თრომბოფლებიტსა და თრომბოფილიას უარყოფს.')
    return lines


def _anam_aaa(patient, ev, q, s, head_prefix, surgeon_str, abdo_txt):
    lines = []
    op_map = {
        'open': 'მუცლის აორტის ინფრარენალური ანევრიზმის ღია რეზექცია / პროთეზირება',
        'evar': 'მუცლის აორტის ინფრარენალური ანევრიზმის ენდოვასკულური პროთეზირება (EVAR)',
        'tbd': 'მუცლის აორტის ინფრარენალური ანევრიზმის ოპერაციული მკურნალობა (ღია / EVAR)',
    }
    lines.append(f'{head_prefix}, დაგეგმილია {op_map.get(patient.op_subtype, "")}{surgeon_str}.')

    mm_str = f"{ev['aaa_mm']} მმ" if ev['aaa_mm'] is not None else 'ზუსტდება'
    morph_lbl = {
        'saccular': ', საკულარული მორფოლოგია (!)',
        'rapid_growth': ', სწრაფი ზრდის დინამიკა (!)',
        'inflammatory': ', სტერილური ანთებითი მორფოლოგია / პერიაორტიტი (!)',
        'inflammatory_mycotic': ', საეჭვო მიკოზური/ინფიცირებული მორფოლოგია (!)',
    }.get(s.get('aaa_morphology') or 'fusiform', '')
    live_cta_m = ev.get('cta_months')
    cta_str = f'{live_cta_m} თვის წინ' if live_cta_m is not None else 'თარიღი უცნობია'
    disc_str = 'DICOM დისკი ადგილზეა' if s.get('cta_disc') else 'DICOM დისკი მოსატანია'
    pain_str = (
        'აღნიშნავს მუცლის/წელის ტკივილს (საყურადღებოა სიმპტომური AAA!).'
        if q.get('aaa_pain') else 'ამჟამად ასიმპტომურია, მუცლისა და წელის ტკივილს უარყოფს.'
    )
    lines.append(f'ანევრიზმა: მაქს. დიამეტრი {mm_str}{morph_lbl} (CTA: {cta_str}, {disc_str}). {pain_str}')

    surg_extras = []
    if patient.op_subtype in ('evar', 'tbd') and (s.get('aaa_neck_status') or 'pending') != 'pending':
        neck_lbl = 'პროქსიმალური ყელი IFU-ს ფარგლებშია' if s.get('aaa_neck_status') == 'friendly_ifu' else 'Hostile Neck — IFU-ს გარეთ (!)'
        surg_extras.append(f'CTA ყელი: {neck_lbl}')
    if (s.get('popliteal_screen_status') or 'pending') != 'pending':
        pop_lbl = 'მუხლქვეშა ანევრიზმა გამოირიცხა' if s.get('popliteal_screen_status') == 'normal' else 'გამოვლინდა თანმხლები მუხლქვეშა/ბარძაყის ანევრიზმა (!)'
        surg_extras.append(pop_lbl)
    if q.get('prev_abdo_surg') or q.get('abdo_chips'):
        surg_extras.append(f"მუცლის/საზარდულის ოპერაციები: {abdo_txt or 'აღნიშნავს'}")
    if q.get('claudication'):
        surg_extras.append('აღნიშნავს ხანგამოშვებით კოჭლობას სიარულისას')
    if s.get('aaa_renal_vein_anom'):
        surg_extras.append('CTA: რეტროაორტული მარცხენა თირკმლის ვენა / ანატომიური ვარიანტი (!)')
    if surg_extras:
        lines.append('; '.join(surg_extras) + '.')
    return lines


def _anam_comorb_meds_allergy(pathway, ev, q):
    from .registry import rule_value
    hba1c_dm = rule_value('GLY_GLUCOSE_HBA1C')['hba1c_dm']
    lvef_red = rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    lines = []
    if q.get('mets_ok', True):
        mets_str = 'დამაკმაყოფილებელი (>= 4 METs, ადის 2 სართულზე გაუჩერებლად)'
    elif q.get('mets_limitation') == 'limb':
        mets_str = 'შეზღუდულია მხოლოდ ფეხის ტკივილის/კოჭლობის გამო (ქოშინსა და სტენოკარდიას უარყოფს)'
    else:
        mets_str = 'დაქვეითებული (< 4 METs — ქოშინი/დაღლა 2 სართულზე ასვლისას)'
    lines.append(f'ფუნქციური ამტანობა: {mets_str}.')

    comorb = []
    if q.get('htn'):
        comorb.append('არტერიული ჰიპერტენზია')
    cad_ch_set = set(_str_list(q.get('cad_chips')))
    lvef_c = _int((ev.get('s') or {}).get('lvef'), 5, 95)
    if q.get('cad') or q.get('recent_stent') or cad_ch_set:
        has_ihd_chip = bool((cad_ch_set & {'mi', 'angina', 'cabg', 'stent_old', 'stent_6_12m'}) or q.get('recent_stent') or not cad_ch_set)
        c_det = chip_summary(q.get('cad_chips'), 'cad_chips', q.get('cad_detail'))
        if q.get('recent_stent') and 'სტენტ' not in c_det:
            c_det = '; '.join([x for x in ('კორონარული სტენტი <6 თვ.', c_det) if x])
        cad_head = 'გულის იშემიური დაავადება' if has_ihd_chip else 'კარდიოლოგიური პათოლოგია'
        comorb.append(f"{cad_head}{f' ({c_det})' if c_det else ''}")
    elif lvef_c is not None and lvef_c < lvef_red:
        comorb.append(f'გულის უკმარისობა / დაქვეითებული განდევნის ფრაქცია (LVEF {lvef_c}%)')
    hba1c_a = _num((ev.get('s') or {}).get('hba1c'), 3.0, 25.0)
    if q.get('dm') or q.get('dm_insulin') or q.get('metformin'):
        dm_kind = 'ინსულინზე' if q.get('dm_insulin') else 'ტაბლეტებზე'
        comorb.append(f'შაქრიანი დიაბეტი ({dm_kind})')
    elif hba1c_a is not None and hba1c_a >= hba1c_dm:
        comorb.append(f'არადიაგნოსტირებული შაქრიანი დიაბეტი (HbA1c {hba1c_a}%)')
    if q.get('on_dialysis'):
        comorb.append('პროგრამული დიალიზი')
    if q.get('pulm') or q.get('pulm_chips'):
        p_det = chip_summary(q.get('pulm_chips'), 'pulm_chips', q.get('pulm_detail'))
        comorb.append(f"ფილტვების ქრ. დაავადება{f' ({p_det})' if p_det else ''}")
    if q.get('thyroid_disease'):
        comorb.append('ფარისებრი ჯირკვლის პათოლოგია')
    if q.get('neuro'):
        comorb.append('ანამნეზში ინსულტი/TIA')
    if q.get('ulcer') or q.get('ulcer_active_symp'):
        comorb.append('წყლულოვანი დაავადება (აქტიური ჩივილით)' if q.get('ulcer_active_symp') else 'წყლულოვანი დაავადება ანამნეზში')
    if q.get('active_infection') or q.get('inf_chips'):
        inf_det = chip_summary(q.get('inf_chips'), 'inf_chips', q.get('infection_text'))
        comorb.append(f"აქტიური ინფექციური კერა{f' ({inf_det})' if inf_det else ''} (!)")
    if q.get('smoker'):
        comorb.append('თამბაქოს მწეველი')
    lines.append(f"თანმხლები ფონი: {', '.join(comorb) if comorb else 'მნიშვნელოვანი ქრონიკული დაავადებების გარეშე'}.")

    med_pairs = (
        ('med_aspirin', 'ასპირინი'), ('med_clopidogrel', 'კლოპიდოგრელი/P2Y12'),
        ('med_ticagrelor', 'ტიკაგრელორი'), ('med_prasugrel', 'პრასუგრელი'),
        ('med_doac', 'პერორალური ანტიკოაგულანტი (DOAC)'), ('med_dabigatran', 'დაბიგატრანი (პრადაქსა)'),
        ('med_warfarin', 'ვარფარინი'), ('dm_insulin', 'ინსულინი'),
        ('metformin', 'მეტფორმინი'), ('sglt2', 'SGLT2 ინჰიბიტორი'),
        ('glp1', 'GLP-1 აგონისტი (ოზემპიკი/მუნჯარო)'),
    )
    meds = [lbl for key, lbl in med_pairs if q.get(key)]
    med_extra = chip_summary(q.get('med_chips'), 'med_chips', q.get('other_meds'))
    if med_extra:
        meds.append(med_extra)
    lines.append(f"მუდმივი მედიკამენტები: {', '.join(meds) if meds else 'არ იღებს'}.")

    allergies = []
    raw_alg_chips = _str_list(q.get('allergy_chips'))
    alg_txt_raw = (q.get('allergy_text') or '').strip()
    alg_txt_low = alg_txt_raw.lower()
    lido_in_txt = any(st in alg_txt_low for st in ('ლიდოკაინ', 'lidocain', 'ნოვოკაინ'))
    if q.get('ven_lidocaine_allergy') and 'lidocaine' not in raw_alg_chips and not lido_in_txt:
        allergies.append('ლიდოკაინი / ადგილობრივი ანესთეტიკი!')
    if q.get('allergy_iodine'):
        allergies.append('იოდი / კონტრასტი!')
    eff_alg_txt = '' if ('lidocaine' in raw_alg_chips and lido_in_txt and len(alg_txt_raw.split()) <= 2) else alg_txt_raw
    alg_extra = chip_summary(raw_alg_chips, 'allergy_chips', eff_alg_txt)
    if alg_extra:
        allergies.append(alg_extra)
    elif q.get('allergy_other'):
        allergies.append('მედიკამენტური ალერგია')
    if pathway == PreopPatient.Pathway.VENOUS:
        no_allergy_txt = 'უარყოფს (მ.შ. ლიდოკაინზე)'
    elif q.get('allergy_iodine_asked'):
        no_allergy_txt = 'უარყოფს (მ.შ. იოდზე/კონტრასტზე)'
    else:
        no_allergy_txt = 'უარყოფს (იოდზე ალერგია დასაზუსტებელია)'
    lines.append(f"ალერგია: {', '.join(allergies) if allergies else no_allergy_txt}.")
    if ev.get('abx_prophylaxis_note'):
        lines.append(f"პროფილაქტიკა: {ev['abx_prophylaxis_note']}")
    if ev.get('post_antithrombotic_label'):
        lines.append(f"პოსტოპ. ანტითრომბოზული გეგმა: {ev['post_antithrombotic_label']}.")
    return lines


def _anam_anesth_labs(pathway, ev, q, s):
    lines = []
    anesth_flags = [f"{ev['effective_asa']}, გაუტკივარება: {ev['effective_anesth_label']}"]
    if ev.get('anesth_lines_txt'):
        anesth_flags.append(f"მონიტორინგი: {ev['anesth_lines_txt']}")
    if ev.get('lidocaine_max_mg') is not None:
        anesth_flags.append(f"ტუმესცენტური ლიდოკაინის მაქს. დოზა: ≤ {ev['lidocaine_max_mg']} მგ (ადრენალინით)")
    if s.get('airway_mallampati') == 'IV_difficult':
        anesth_flags.append('Mallampati IV — მოსალოდნელი რთული ინტუბაცია!')
    if q.get('anesth_issue'):
        anesth_flags.append('გართულება წარსულ ანესთეზიაზე')
    if q.get('pacemaker'):
        cied_sfx = f" ({ev['cied_plan_label']})" if ev.get('cied_plan_label') else ''
        anesth_flags.append(f'კარდიოსტიმულატორი/იმპლანტი{cied_sfx}')
    if q.get('loose_teeth'):
        anesth_flags.append('პროთეზი / მერყევი კბილები')
    if q.get('dental_infection'):
        anesth_flags.append('სტომატოლოგიური სანაცია საჭიროა')
    if q.get('glp1') or ev.get('has_glp1_any'):
        anesth_flags.append('GLP-1 აგონისტი (კუჭის შენელებული დაცლის რისკი)')
    lines.append(f"ანესთეზიოლოგიური სტატუსი: {'; '.join(anesth_flags)}.")

    lab_bits = []
    bp_s = _int(s.get('bp_sys'), 30, 320)
    bp_d = _int(s.get('bp_dia'), 20, 220)
    bp_sl = _int(s.get('bp_sys_l'), 30, 320)
    bp_dl = _int(s.get('bp_dia_l'), 20, 220)
    if bp_s is not None and bp_d is not None and bp_s > bp_d:
        bp_str = f'BP {bp_s}/{bp_d}'
        if bp_sl is not None and bp_dl is not None and bp_sl > bp_dl:
            bp_str += f' (L: {bp_sl}/{bp_dl})'
        lab_bits.append(f'{bp_str} mmHg')
    elif bp_sl is not None and bp_dl is not None and bp_sl > bp_dl:
        lab_bits.append(f'BP (L) {bp_sl}/{bp_dl} mmHg')
    if ev.get('bmi') is not None:
        lab_bits.append(f"BMI {ev['bmi']}")
    if ev['hb_gl'] is not None:
        lab_bits.append(f"Hb {ev['hb_gl']} გ/ლ")
    if ev.get('plt') is not None:
        lab_bits.append(f"PLT {ev['plt']}")
    inr_a = _num(s.get('inr'), 0.4, 20.0)
    if inr_a is not None:
        lab_bits.append(f'INR {inr_a}')
    if ev.get('creat_unit_mismatch') or ev.get('creat_unit_ambiguous'):
        lab_bits.append('კრეატინინის ერთეული დასაზუსტებელია')
    elif ev['egfr'] is not None:
        crcl_part = f" (CrCl {ev['crcl']})" if ev.get('crcl') is not None else ''
        lab_bits.append(f"eGFR {ev['egfr']} მლ/წთ{crcl_part}")
    if ev.get('max_contrast_ml') is not None:
        lab_bits.append(f"მაქს. კონტრასტი ≤ {ev['max_contrast_ml']} მლ")
    if ev['alb_gl'] is not None:
        lab_bits.append(f"ალბუმინი {ev['alb_gl']} გ/ლ")
    if ev.get('ldl') is not None:
        lab_bits.append(f"LDL {ev['ldl']} მმოლ/ლ")
    lvef_a = _int(s.get('lvef'), 5, 95)
    if lvef_a is not None:
        lab_bits.append(f'LVEF {lvef_a}%')
    if ev.get('nt_probnp') is not None:
        lab_bits.append(f"NT-proBNP {ev['nt_probnp']} pg/mL")
    if ev.get('hs_ctn') is not None:
        lab_bits.append(f"hs-cTn {ev['hs_ctn']} ng/L")
    if ev.get('tsat') is not None:
        lab_bits.append(f"TSAT {ev['tsat']}%")
    car_r_a = _int(s.get('carotid_r'), 0, 100)
    car_l_a = _int(s.get('carotid_l'), 0, 100)
    if pathway not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA) and (car_r_a is not None or car_l_a is not None):
        car_r_s = car_r_a if car_r_a is not None else '—'
        car_l_s = car_l_a if car_l_a is not None else '—'
        lab_bits.append(f'საძილეები R {car_r_s}% / L {car_l_s}%')
    if ev.get('min_abi') is not None and pathway == PreopPatient.Pathway.VENOUS:
        lab_bits.append(f"ABI min {ev['min_abi']}")
    if ev.get('min_tp') is not None:
        lab_bits.append(f"TP min {ev['min_tp']} mmHg")
    elif ev.get('min_tbi') is not None:
        lab_bits.append(f"TBI min {ev['min_tbi']}")
    fev1_a = _int(s.get('spiro_fev1'), 10, 160)
    if fev1_a is not None:
        lab_bits.append(f'FEV1 {fev1_a}%')
    if lab_bits:
        lines.append(f"კვლევებით: {', '.join(lab_bits)}.")
    return lines


def generate_anamnesis(patient, ev=None):
    """Build a concise, structured Georgian preoperative anamnesis across all 6 vascular pathways."""
    if ev is None:
        from . import evaluate
        ev = evaluate(patient)
    q, s = ev['q'], ev['s']
    if not (q.get('filled') or is_self_filled_q(q)):
        return ''

    pathway = ev['pathway']
    sex_label = 'ქალი' if patient.sex == 'F' else ('მამაკაცი' if patient.sex == 'M' else 'პაციენტი')
    age_part = f'{patient.age} წ. ' if patient.age else ''
    head_prefix = f'{age_part}{sex_label}'
    surgeon_str = f' (ქირურგი: {person_name(patient.surgeon)})' if patient.surgeon else ''
    abdo_txt = chip_summary(q.get('abdo_chips'), 'abdo_chips', q.get('prev_abdo_text'))
    vasc_txt = chip_summary(q.get('vasc_chips'), 'vasc_chips', q.get('abf_prior_vasc_text'))
    ulcer_txt = chip_summary(q.get('ulcer_chips'), 'ulcer_chips', q.get('abf_ulcer_text'))

    if pathway == PreopPatient.Pathway.CEA:
        lines = _anam_cea(patient, ev, q, s, head_prefix, surgeon_str)
    elif pathway == PreopPatient.Pathway.ABF:
        lines = _anam_abf(patient, q, s, head_prefix, surgeon_str, abdo_txt, vasc_txt, ulcer_txt)
    elif pathway in (PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        lines = _anam_pad(patient, pathway, q, s, head_prefix, surgeon_str, ulcer_txt)
    elif pathway == PreopPatient.Pathway.VENOUS:
        lines = _anam_venous(patient, ev, q, s, head_prefix, surgeon_str)
    else:
        lines = _anam_aaa(patient, ev, q, s, head_prefix, surgeon_str, abdo_txt)

    lines.extend(_anam_comorb_meds_allergy(pathway, ev, q))
    lines.extend(_anam_anesth_labs(pathway, ev, q, s))
    return '\n'.join(lines)

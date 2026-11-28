"""Free-text high-risk medication and allergy detection helpers for preop."""
import re

from .constants import (
    _FREETEXT_ALLERGY_STEMS,
    _FREETEXT_HIGH_RISK_MED_RULES,
    _HIT_WORD_RE,
    _IODINE_WORD_RE,
)
from .meds_freetext_nlp import (  # noqa: F401
    _ALLERGY_NEGATION_WORDS,
    _DIGIT_RE,
    _KA_NEG_VERB_RE,
    _NEGATION_PHRASE_RE,
    _WS_RUN_RE,
    _has_neg_phrase,
    _normalize_nlp_text,
    _positive_clauses_text,
    _rewrite_en_except,
    _rewrite_ka_garda,
    _split_inline_boundaries,
    _split_neg_clauses,
)
from .utils import _str_list

_RIVA_STEM = r'(?:ქსარელტ\w*|კსარელტ\w*|xarelto|რივაროქს\w*|რივაროკს\w*|rivarox\w*)'
_RIVA_STEM_RE = re.compile(_RIVA_STEM)
_RIVA_LOW_RE = re.compile(
    rf'(?:{_RIVA_STEM}[^\d,;:\n]{{0,12}}(?<![\d.,])2[.,]5(?![\d.,])'
    rf'|(?<![\d.,])2[.,]5 ?(?:მგ|mg)?[^\w\n]{{0,8}}{_RIVA_STEM})'
)
_RIVA_NON_DOSE_UNIT = (
    r'(?:აბი|აბ|ტაბ|tab|tabs|tablets?|caps?|capsules?|pills?|დოზა|dose'
    r'|დღის|დღე|დღით|დღეა|დღეები|კვირის|კვირა|კვირაა|კვირით|თვის|თვე|თვეა|თვით|წლის|წელი|წელია|წლიდან|წლამდე|საათის|საათით|საათი|საათია|სთ|ჯერ|-ჯერ'
    r'|days?|weeks?|wks?|months?|mos?|years?|yrs?|hrs?|hours?|am|pm|d|h)(?![a-zა-ჰ])'
)
_RIVA_THER_DOSE_RE = re.compile(
    rf'(?<![\d.,:/№#])(?<!\d-)(?<!№ )(?<!# )(?:10|15|20)(?![\d.,:]|/\d|-\d| ?{_RIVA_NON_DOSE_UNIT})',
    re.IGNORECASE,
)
_RIVA_CLAUSE_SPLIT_RE = re.compile(r'[;\n+]|(?<!\d)[,/]|[,/](?!\d)| (?:და|ან|and|or) ')
_RIVA_ESCALATION_STEMS = (
    'დაუნიშნ', 'შეცვ', 'შეუცვ', 'გაზარდ', 'გაიზარდ', 'მოუმატ', 'მოიმატ', 'გადავიდ', 'გადაიყვან',
    'დოზ', 'შემდეგ', 'ახლა', 'ხოლო', 'მაგრამ', 'კარდიოლოგ', 'ექიმ', 'ადრე', 'ნაცვლ',
    'იღებ', 'ვიღებ', 'სვამ', 'ფიბრილ', 'თრომბ', 'ემბოლ', 'ინსულტ',
    'prescrib', 'switch', 'increas', 'chang', 'escalat', 'rais', 'dose', 'then', 'now', 'instead',
    'also', 'cardiol', 'doctor', 'physician', 'tak', 'us', 'afib', 'fibril', 'dvt', 'vte', 'pe', 'thromb', 'stroke',
)
_RIVA_NON_DRUG_WORDS = frozenset({
    'მგ', 'mg', 'x2', '2x', 'bid', 'qd', 'od', '×', 'x', 'tab', 'tabs', 'ტაბ', 'აბი', 'აბ',
    'მერე', 'მხოლოდ', 'only', 'daily', 'morning', 'evening', 'once', 'twice', 'up', 'has', 'been',
    'to', 'of', 'on', 'from', 'for', 'the', 'at', 'per', 'by', 'in', 'day', 'days', 'am', 'pm', 'a',
    'მდე', 'ზე', 'ით', 'დან', 'ს', 'ში', 'დღეში', 'დილას', 'საღამოს', 'ღამით', 'უკვე', 'წინ', 'გამო',
})
_ASPIRIN_FT_STEMS = (
    'ასპირინ', 'ასპიკარდ', 'კარდიომაგნ', 'კარდიო მაგნ', 'თრომბო-ას', 'თრომბოას', 'თრომბო ას',
    'დუოპლავინ', 'კოპლავიქს', 'კოპლავიკს',
    'aspirin', 'aspicard', 'cardioaspir', 'cardiomagn', 'trombo-ass', 'trombo ass', 'thrombo ass',
    'duoplavin', 'coplavix',
)
_ASA_WORD_RE = re.compile(r'(?<![a-zა-ჰ])asa(?![a-zა-ჰ])')
_FT_STATIN_STEMS = (
    'ატორვასტატინ', 'ატორვა', 'როზუვასტატინ', 'სიმვასტატინ', 'პრავასტატინ', 'პიტავასტატინ', 'ფლუვასტატინ',
    'სტატინ', 'კრესტორ', 'ლიპიტორ', 'ლიპრიმ', 'ტულიპ', 'როქსერ', 'მერტენილ', 'ატორის', 'ზოკორ',
    'statin', 'atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin',
    'crestor', 'lipitor', 'liprimar', 'roswera', 'mertenil', 'atoris',
)
_FT_ACEI_ARB_STEMS = (
    'რამიპრილ', 'ენალაპრილ', 'პერინდოპრილ', 'ლიზინოპრილ', 'კაპტოპრილ', 'ფოზინოპრილ',
    'ლოზარტან', 'ვალსარტან', 'კანდესარტან', 'ტელმისარტან', 'ირბესარტან', 'ოლმესარტან',
    'საკუბიტრილ', 'ენტრესტო', 'იუპერიო',
    'პრესტარიუმ', 'პრესტანს', 'ტრიტაცე', 'კოზაარ', 'დიოვან', 'მიკარდის', 'ატაკანდ', 'ენაპ', 'ნოლიპრელ', 'კო-პრენეს', 'პრენეს',
    'ramipril', 'enalapril', 'perindopril', 'lisinopril', 'captopril',
    'losartan', 'valsartan', 'candesartan', 'telmisartan', 'irbesartan', 'olmesartan',
    'sacubitril', 'entresto', 'uperio',
    'prestarium', 'prestans', 'tritace', 'cozaar', 'diovan', 'micardis', 'atacand', 'enap', 'noliprel',
)
_FT_AMIODARONE_STEMS = ('ამიოდარონ', 'კორდარონ', 'amiodaron', 'cordaron', 'paceron')
_FT_LMWH_STEMS = (
    'კლექსან', 'clexane', 'ლოვენოქს', 'lovenox', 'ფრაქსიპარ', 'fraxipar',
    'ენოქსაპარ', 'enoxapar', 'ნადროპარ', 'nadropar', 'ფრაგმინ', 'fragmin',
    'დალტეპარ', 'daltepar', 'ციბორ', 'zibor', 'ბემიპარ', 'bemipar',
    'თინზაპარ', 'tinzapar', 'ინოჰეპ', 'innohep', 'დაბალმოლეკულ', 'low molecular', 'low-molecular',
)
_LMWH_WORD_RE = re.compile(r'(?<![a-zა-ჰ])(?:lmwh|ლმვჰ)(?![a-zა-ჰ])')
_LMWH_HEPARIN_PHRASE_RE = re.compile(
    r'(?:დაბალმოლეკულ[ა-ჰ]*(?:[\s-]+წონის)?|low[\s-]+molecular(?:[\s-]+weight)?)[\s-]+(?:ჰეპარინ[ა-ჰ]*|heparin)'
)
_FT_UFH_STEMS = ('ჰეპარინ', 'heparin')
_UFH_WORD_RE = re.compile(r'(?<![a-zა-ჰ])(?:ufh|უფჰ)(?![a-zა-ჰ])')
_FT_HEPARIN_LMWH_STEMS = (*_FT_LMWH_STEMS, *_FT_UFH_STEMS)
_FT_FONDAPARINUX_STEMS = ('არიქსტრ', 'arixtra', 'ფონდაპარინუქს', 'ფონდაპარინუკს', 'fondaparinux')
_FT_ARGATROBAN_STEMS = ('არგატრობან', 'argatroban')
_FT_NSAID_STEMS = (
    'იბუპროფენ', 'ibuprofen', 'ნუროფენ', 'nurofen', 'კეტოროლაკ', 'ketorolac', 'კეტანოვ', 'ketanov',
    'დიკლოფენაკ', 'diclofenac', 'ვოლტარენ', 'voltaren', 'კეტონალ', 'ketonal', 'კეტოპროფენ', 'ketoprofen',
    'ნიმესილ', 'nimesil', 'ნიმესულიდ', 'nimesulid', 'ნაპროქსენ', 'naproxen', 'მელოქსიკამ', 'meloxicam',
    'მოვალიზ', 'movalis', 'ცელეკოქსიბ', 'celecoxib', 'ინდომეტაცინ', 'indomethacin', 'დექსალგინ', 'dexalgin',
    'დექსკეტოპროფენ', 'dexketoprofen',
)
_APIX_STEMS = ('ელიქვის', 'ელიკვის', 'აპიქსაბ', 'აპიკსაბ', 'eliquis', 'apixab')
_EDOX_STEMS = ('ლიქსიან', 'ედოქსაბ', 'ედოკსაბ', 'ედიქსან', 'სავაის', 'lixiana', 'edoxab', 'savaysa')


def _is_riva_escalation_words(seg):
    words = [w for w in re.findall(r'[a-zა-ჰ]+', seg) if w not in _RIVA_NON_DRUG_WORDS]
    return not words or all(any(st in w for st in _RIVA_ESCALATION_STEMS) for w in words)


def _riva_has_therapeutic_dose(low):
    """Detect therapeutic rivaroxaban (10/15/20 mg) in strictly O(N) time while ignoring unrelated co-med doses."""
    clauses = [c.strip() for c in _RIVA_CLAUSE_SPLIT_RE.split(low) if c.strip()]
    seen_riva = False
    for clause in clauses:
        riva_ms = list(_RIVA_STEM_RE.finditer(clause))
        ther_ms = list(_RIVA_THER_DOSE_RE.finditer(clause))
        if riva_ms:
            seen_riva = True
            if not ther_ms:
                continue
            r0_start, ti, tn = riva_ms[0].start(), 0, len(ther_ms)
            if ther_ms[0].end() <= r0_start:
                if _is_riva_escalation_words(clause[:r0_start]):
                    return True
                while ti < tn and ther_ms[ti].end() <= r0_start:
                    ti += 1
            for idx, rm in enumerate(riva_ms):
                span_end = riva_ms[idx + 1].start() if idx + 1 < len(riva_ms) else len(clause)
                while ti < tn and ther_ms[ti].start() < rm.end():
                    ti += 1
                pos = rm.end()
                while ti < tn and ther_ms[ti].start() < span_end:
                    if _is_riva_escalation_words(clause[pos:ther_ms[ti].start()]):
                        return True
                    break
                while ti < tn and ther_ms[ti].start() < span_end:
                    ti += 1
            continue
        if seen_riva and ther_ms and _is_riva_escalation_words(clause):
            return True
    return False


def _detect_freetext_allergies(text):
    """Detect structured allergy classes from free-text `q.allergy_text` (Georgian & Latin stems, negation-aware)."""
    low = _positive_clauses_text(text)
    if not low:
        return set()
    matched = set()
    for key, stems in _FREETEXT_ALLERGY_STEMS:
        if any(st in low for st in stems):
            matched.add(key)
    if _IODINE_WORD_RE.search(low):
        matched.add('iodine')
    if _HIT_WORD_RE.search(low) or _LMWH_WORD_RE.search(low):
        matched.add('hit_heparin')
    return matched


def _has_unrecognized_allergy_clause(text):
    """Return True if any non-negated clause in `allergy_text` matches no known allergy stem."""
    pos_low = _positive_clauses_text(text)
    if not pos_low:
        return False
    for raw_clause in _RIVA_CLAUSE_SPLIT_RE.split(pos_low):
        cleaned = re.sub(r'[\s.,;:!?()\[\]"\'—–-]+', '', raw_clause)
        if not cleaned or cleaned in _ALLERGY_NEGATION_WORDS:
            continue
        matched = any(any(st in raw_clause for st in stems) for _, stems in _FREETEXT_ALLERGY_STEMS)
        if not matched and not _HIT_WORD_RE.search(raw_clause) and not _LMWH_WORD_RE.search(raw_clause) and not _IODINE_WORD_RE.search(raw_clause):
            return True
    return False


def _effective_allergy_state(q):
    """Combine structured `allergy_chips` / `allergy_iodine` with `_detect_freetext_allergies(q.allergy_text)`."""
    q_d = q if isinstance(q, dict) else {}
    chips = set(_str_list(q_d.get('allergy_chips')))
    ft = _detect_freetext_allergies(q_d.get('allergy_text') or '')
    eff_chips = chips | (ft - {'iodine'})
    eff_iodine = bool(q_d.get('allergy_iodine')) or ('iodine' in ft)
    return eff_chips, eff_iodine, ft


def _detect_ft_xa_flags(low, med_chips, q_d):
    has_apix = any(st in low for st in _APIX_STEMS)
    has_edox = any(st in low for st in _EDOX_STEMS)
    has_riva = bool(_RIVA_STEM_RE.search(low))
    has_riva_low_raw = bool(_RIVA_LOW_RE.search(low))
    has_riva_ther = _riva_has_therapeutic_dose(low)
    has_ft_dual_xa = (int(has_apix) + int(has_edox) + int(has_riva) >= 2) or (has_riva_low_raw and has_riva_ther)
    has_ft_riva_2_5 = has_riva_low_raw and not has_riva_ther
    has_ft_riva_low = has_ft_riva_2_5 and not (has_apix or has_edox)
    riva_low_only = ('rivaroxaban_2_5' in med_chips) and has_ft_riva_low
    doac_struct_ok = (
        (bool(q_d.get('med_doac')) and not has_ft_dual_xa and not (has_ft_riva_2_5 and 'rivaroxaban_2_5' not in med_chips))
        or (riva_low_only and not has_ft_dual_xa)
    )
    return has_ft_dual_xa, has_ft_riva_2_5, has_ft_riva_low, doac_struct_ok


def _detect_freetext_high_risk_meds(text, q=None):
    """Detect high-risk anticoagulants, P2Y12, aspirin, NSAIDs, SGLT2i, GLP-1, insulin & teratogens in `q.other_meds`."""
    low = _positive_clauses_text(text)
    if not low:
        return {
            'matched_keys': set(), 'unstructured_keys': set(), 'unstructured_labels': [],
            'unstructured_antithrombotic': False, 'ertugliflozin': False, 'daily_glp1': False,
            'has_omeprazole': False, 'has_glp1': False, 'has_ft_statin': False,
            'has_ft_acei_arb': False, 'has_ft_amiodarone': False, 'has_ft_heparin_lmwh': False,
            'has_ft_lmwh': False, 'has_ft_ufh': False,
            'has_ft_fondaparinux': False, 'has_ft_argatroban': False, 'has_ft_nsaid': False,
            'has_ft_riva_low': False, 'has_ft_dual_xa': False, 'has_ft_full_oac': False,
            'has_ft_unstruct_warfarin': False, 'has_ft_unstruct_doac': False, 'has_ft_unstruct_riva_low': False,
        }
    q_d = q if isinstance(q, dict) else {}
    med_chips = set(_str_list(q_d.get('med_chips')))
    eff_alg_chips, _, _ = _effective_allergy_state(q_d)
    has_ft_dual_xa, has_ft_riva_2_5, has_ft_riva_low, doac_struct_ok = _detect_ft_xa_flags(low, med_chips, q_d)
    structured_map = {
        'warfarin': bool(q_d.get('med_warfarin')),
        'doac': doac_struct_ok,
        'dabigatran': bool(q_d.get('med_dabigatran')),
        'parenteral_oac': True,  # No structured checkbox exists; handled via dedicated parenteral row & flags (no Catch-22 deadlock)
        'parenteral_dti': True,
        'clopidogrel': bool(q_d.get('med_clopidogrel')),
        'ticagrelor': bool(q_d.get('med_ticagrelor')),
        'prasugrel': bool(q_d.get('med_prasugrel')),
        'cilostazol': 'cilostazol' in med_chips,
        'sglt2': bool(q_d.get('sglt2')),
        'glp1': bool(q_d.get('glp1')),
        'insulin': bool(q_d.get('dm_insulin')),
        'metformin': bool(q_d.get('metformin')),
        'steroid': 'steroid' in med_chips,
        'amiodarone': 'amiodarone' in med_chips,
        'omeprazole': True,  # Tracked via has_omeprazole for CYP2C19 clopidogrel check, not as unstructured blocker
    }
    matched_keys, unstructured_keys, unstructured_labels, unstructured_at = set(), set(), [], False
    for key, label, is_at, stems in _FREETEXT_HIGH_RISK_MED_RULES:
        if any(st in low for st in stems):
            matched_keys.add(key)
            if not structured_map.get(key, False):
                unstructured_keys.add(key)
                unstructured_labels.append(label)
                if is_at:
                    unstructured_at = True
    has_ft_lmwh = any(st in low for st in _FT_LMWH_STEMS) or bool(_LMWH_WORD_RE.search(low))
    ufh_low = _LMWH_HEPARIN_PHRASE_RE.sub(' ', low) if has_ft_lmwh else low
    has_ft_ufh = any(st in ufh_low for st in _FT_UFH_STEMS) or bool(_UFH_WORD_RE.search(low))
    if has_ft_lmwh or has_ft_ufh:
        matched_keys.add('parenteral_oac')
    if not q_d.get('med_aspirin') and (
        any(st in low for st in _ASPIRIN_FT_STEMS) or bool(_ASA_WORD_RE.search(low))
    ):
        matched_keys.add('aspirin')
        unstructured_keys.add('aspirin')
        unstructured_labels.append('ასპირინი (NSAID ალერგია!)' if 'nsaid' in eff_alg_chips else 'ასპირინი (75–100 მგ)')
        unstructured_at = True
    is_ertug = any(st in low for st in (
        'ერტუგლიფ', 'ertuglif', 'სტეგლატრ', 'steglatro',
        'სეგლურომეტ', 'segluromet', 'სტეგლუჯან', 'steglujan',
    ))
    is_daily_glp1 = any(st in low for st in (
        'საქსენდ', 'saxenda', 'ვიქტოზ', 'victoza', 'ლირაგლუტ', 'liraglut',
        'რიბელსუს', 'rybelsus', 'ბიეტა', 'byetta', 'ექსენატიდ', 'exenatid', 'ლიქსისენატიდ', 'lixisenatid',
    ))
    has_ft_unstruct_warfarin = 'warfarin' in unstructured_keys
    has_ft_unstruct_doac = ('dabigatran' in unstructured_keys) or (('doac' in unstructured_keys) and not has_ft_riva_low)
    has_ft_unstruct_riva_low = ('doac' in unstructured_keys) and has_ft_riva_2_5
    has_ft_full_oac = bool(has_ft_unstruct_warfarin or has_ft_unstruct_doac)
    return {
        'matched_keys': matched_keys,
        'unstructured_keys': unstructured_keys,
        'unstructured_labels': unstructured_labels,
        'unstructured_antithrombotic': unstructured_at,
        'ertugliflozin': is_ertug,
        'daily_glp1': is_daily_glp1,
        'has_omeprazole': 'omeprazole' in matched_keys,
        'has_glp1': 'glp1' in matched_keys,
        'has_ft_statin': any(st in low for st in _FT_STATIN_STEMS),
        'has_ft_acei_arb': any(st in low for st in _FT_ACEI_ARB_STEMS),
        'has_ft_amiodarone': any(st in low for st in _FT_AMIODARONE_STEMS),
        'has_ft_heparin_lmwh': has_ft_lmwh or has_ft_ufh,
        'has_ft_lmwh': has_ft_lmwh,
        'has_ft_ufh': has_ft_ufh,
        'has_ft_fondaparinux': any(st in low for st in _FT_FONDAPARINUX_STEMS),
        'has_ft_argatroban': any(st in low for st in _FT_ARGATROBAN_STEMS),
        'has_ft_nsaid': any(st in low for st in _FT_NSAID_STEMS),
        'has_ft_riva_low': has_ft_riva_low,
        'has_ft_dual_xa': has_ft_dual_xa,
        'has_ft_full_oac': has_ft_full_oac,
        'has_ft_unstruct_warfarin': has_ft_unstruct_warfarin,
        'has_ft_unstruct_doac': has_ft_unstruct_doac,
        'has_ft_unstruct_riva_low': has_ft_unstruct_riva_low,
    }


from .meds_helpers import (  # noqa: E402, F401
    _append_ft_parenteral_row,
    _collect_ft_contraindications,
    append_freetext_med_rows,
)

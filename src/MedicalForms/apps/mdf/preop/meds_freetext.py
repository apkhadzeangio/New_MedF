"""Free-text high-risk medication and allergy detection helpers for preop."""
import re

from .constants import (
    _FREETEXT_ALLERGY_STEMS,
    _FREETEXT_HIGH_RISK_MED_RULES,
    _HIT_WORD_RE,
    _IODINE_WORD_RE,
)
from .utils import _str_list

_RIVA_STEM = r'(?:ქსარელტ\w*|კსარელტ\w*|xarelto|რივაროქსაბ\w*|rivaroxab\w*)'
_RIVA_STEM_RE = re.compile(_RIVA_STEM)
_RIVA_LOW_RE = re.compile(
    rf'(?:{_RIVA_STEM}[^\d,;:\n]{{0,12}}(?<![\d.,])2[.,]5(?![\d.,])'
    rf'|(?<![\d.,])2[.,]5\s*(?:მგ|mg)?[^\w\n]{{0,8}}{_RIVA_STEM})'
)
_RIVA_THER_DOSE_RE = re.compile(r'(?<![\d.,])(?:10|15|20)(?![\d.,])')
_RIVA_CLAUSE_SPLIT_RE = re.compile(r'[;,\n/+]|\s+(?:და|ან|and|or)\s+')
_NEG_CLAUSE_SPLIT_RE = re.compile(
    r'[;,\n!]+|\.(?!\d)|\s+(?:მაგრამ|ხოლო|თუმცა|but)\s+'
    r'|\s+(?:და|ან|and)\s+(?=(?:სვამს|იღებს|ვიღებ|ღებულობს|უწერია|დაუნიშნ|takes?|taking|uses?|using|on\s))'
)
_RIVA_ESCALATION_STEMS = (
    'დაუნიშნ', 'შეცვ', 'გაიზარდ', 'გადაიყვან', 'დოზ', 'შემდეგ', 'ახლა', 'ხოლო',
    'მაგრამ', 'კარდიოლოგ', 'ექიმ', 'საღამო', 'დილ', 'დღეში', 'ადრე', 'ნაცვლ',
    'prescrib', 'switch', 'increas', 'chang', 'dose', 'then', 'now', 'instead', 'also',
)
_RIVA_NON_DRUG_WORDS = frozenset({
    'მგ', 'mg', 'x2', '2x', 'bid', 'qd', 'od', '×', 'x', 'tab', 'ტაბ', 'აბი', 'აბ',
    'მერე', 'მხოლოდ', 'only', 'daily', 'morning', 'evening', 'once', 'twice',
})
_ASPIRIN_FT_STEMS = (
    'ასპირინ', 'ასპიკარდ', 'კარდიომაგნ', 'კარდიო მაგნ', 'თრომბო-ას', 'თრომბოას', 'თრომბო ას',
    'დუოპლავინ', 'კოპლავიქს',
    'aspirin', 'aspicard', 'cardioaspir', 'cardiomagn', 'trombo-ass', 'trombo ass', 'thrombo ass',
    'duoplavin', 'coplavix',
)
_ASA_WORD_RE = re.compile(r'(?<![a-zა-ჰ])asa(?![a-zა-ჰ])')
_OTHER_XA_DOAC_STEMS = (
    'ელიქვის', 'ელიკვის', 'eliquis', 'აპიქსაბ', 'apixab', 'ედიქსან', 'ედოქსაბ', 'edoxab',
    'ლიქსიან', 'lixiana', 'სავაის', 'savaysa',
)
_ALLERGY_NEGATION_WORDS = frozenset({
    'არა', 'უარყოფს', 'არაქვს', 'არმაქვს', 'ალერგიაარაქვს', 'ალერგიაარმაქვს',
    'no', 'none', 'nil', 'nkda', 'nka', 'noallergy', 'noallergies',
})
_NEGATION_PHRASE_RE = re.compile(
    r'(?:არ\s+(?:აქვს|მაქვს|სვამს|იღებს|ვიღებ|ღებულობს|აღნიშნავს|ჰქონია)'
    r'|უარყოფს|მოხსნილია|შეწყვეტილია|გამორიცხულია'
    r'|(?<![a-z])(?:no|not|none|nil|nkda|nka|denied|denies|stopped|discontinued|without)\b)',
    re.IGNORECASE,
)
_FT_STATIN_STEMS = (
    'ატორვასტატინ', 'როზუვასტატინ', 'სიმვასტატინ', 'პრავასტატინ', 'პიტავასტატინ', 'ფლუვასტატინ',
    'სტატინ', 'კრესტორ', 'ლიპიტორ', 'ლიპრიმ', 'ტულიპ', 'როქსერ', 'მერტენილ', 'ატორის', 'ზოკორ',
    'statin', 'atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin',
    'crestor', 'lipitor', 'liprimar', 'roswera', 'atoris',
)
_FT_ACEI_ARB_STEMS = (
    'რამიპრილ', 'ენალაპრილ', 'პერინდოპრილ', 'ლიზინოპრილ', 'კაპტოპრილ', 'ფოზინოპრილ',
    'ლოზარტან', 'ვალსარტან', 'კანდესარტან', 'ტელმისარტან', 'ირბესარტან', 'ოლმესარტან',
    'პრესტარიუმ', 'პრესტანს', 'ტრიტაცე', 'კოზაარ', 'დიოვან', 'მიკარდის', 'ატაკანდ', 'ენაპ', 'ნოლიპრელ', 'კო-პრენეს', 'პრენეს',
    'ramipril', 'enalapril', 'perindopril', 'lisinopril', 'captopril',
    'losartan', 'valsartan', 'candesartan', 'telmisartan', 'irbesartan', 'olmesartan',
    'prestarium', 'prestans', 'tritace', 'cozaar', 'diovan', 'micardis', 'atacand', 'enap', 'noliprel',
)


def _positive_clauses_text(text):
    """Strip explicitly negated clauses ('არ აქვს', 'არ სვამს', 'no ...', 'stopped ...') before stem matching."""
    if not isinstance(text, str) or not text.strip():
        return ''
    pos = []
    for cl in _NEG_CLAUSE_SPLIT_RE.split(text.lower()):
        cl_s = cl.strip()
        if not cl_s:
            continue
        compact = re.sub(r'[\s.,;:!?()\[\]"\'—–-]+', '', cl_s)
        if not compact or compact in _ALLERGY_NEGATION_WORDS or _NEGATION_PHRASE_RE.search(cl_s):
            continue
        pos.append(cl_s)
    return ', '.join(pos)


def _riva_has_therapeutic_dose(low):
    """Detect therapeutic rivaroxaban (10/15/20 mg) while ignoring unrelated co-med doses like '+ 10 mg bisoprolol'."""
    clauses = [c.strip() for c in _RIVA_CLAUSE_SPLIT_RE.split(low) if c.strip()]
    seen_riva = False
    for clause in clauses:
        has_riva = bool(_RIVA_STEM_RE.search(clause))
        has_ther = bool(_RIVA_THER_DOSE_RE.search(clause))
        if has_riva:
            seen_riva = True
            if has_ther:
                return True
            continue
        if not seen_riva or not has_ther:
            continue
        if any(st in clause for st in _RIVA_ESCALATION_STEMS):
            return True
        words = [w for w in re.findall(r'[a-zა-ჰ]+', clause) if w not in _RIVA_NON_DRUG_WORDS]
        if not words:
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
    if _HIT_WORD_RE.search(low):
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
        if not matched and not _HIT_WORD_RE.search(raw_clause) and not _IODINE_WORD_RE.search(raw_clause):
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


def _detect_freetext_high_risk_meds(text, q=None):
    """Detect high-risk anticoagulants, P2Y12, aspirin, SGLT2i, GLP-1, insulin & pregnancy teratogens in `q.other_meds`."""
    low = _positive_clauses_text(text)
    if not low:
        return {
            'matched_keys': set(), 'unstructured_labels': [], 'unstructured_antithrombotic': False,
            'ertugliflozin': False, 'daily_glp1': False, 'has_omeprazole': False, 'has_glp1': False,
            'has_ft_statin': False, 'has_ft_acei_arb': False,
        }
    q_d = q if isinstance(q, dict) else {}
    med_chips = set(_str_list(q_d.get('med_chips')))
    eff_alg_chips, _, _ = _effective_allergy_state(q_d)
    riva_low_only = (
        'rivaroxaban_2_5' in med_chips
        and bool(_RIVA_LOW_RE.search(low))
        and not _riva_has_therapeutic_dose(low)
        and not any(st in low for st in _OTHER_XA_DOAC_STEMS)
    )
    structured_map = {
        'warfarin': bool(q_d.get('med_warfarin')),
        'doac': bool(q_d.get('med_doac')) or riva_low_only,
        'dabigatran': bool(q_d.get('med_dabigatran')),
        'parenteral_oac': False,
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
    matched_keys = set()
    unstructured_labels = []
    unstructured_at = False
    for key, label, is_at, stems in _FREETEXT_HIGH_RISK_MED_RULES:
        if any(st in low for st in stems):
            matched_keys.add(key)
            if not structured_map.get(key, False):
                unstructured_labels.append(label)
                if is_at:
                    unstructured_at = True
    if not q_d.get('med_aspirin') and (
        any(st in low for st in _ASPIRIN_FT_STEMS) or bool(_ASA_WORD_RE.search(low))
    ):
        matched_keys.add('aspirin')
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
    return {
        'matched_keys': matched_keys,
        'unstructured_labels': unstructured_labels,
        'unstructured_antithrombotic': unstructured_at,
        'ertugliflozin': is_ertug,
        'daily_glp1': is_daily_glp1,
        'has_omeprazole': 'omeprazole' in matched_keys,
        'has_glp1': 'glp1' in matched_keys,
        'has_ft_statin': any(st in low for st in _FT_STATIN_STEMS),
        'has_ft_acei_arb': any(st in low for st in _FT_ACEI_ARB_STEMS),
    }

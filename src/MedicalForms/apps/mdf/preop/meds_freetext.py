"""Free-text high-risk medication and allergy detection helpers for preop."""
import re

from .constants import (
    _FREETEXT_ALLERGY_STEMS,
    _FREETEXT_HIGH_RISK_MED_RULES,
    _HIT_WORD_RE,
    _IODINE_WORD_RE,
)
from .utils import _safe_display_str, _str_list

_RIVA_STEM = r'(?:ქსარელტ\w*|კსარელტ\w*|xarelto|რივაროქსაბ\w*|rivaroxab\w*)'
_RIVA_STEM_RE = re.compile(_RIVA_STEM)
_RIVA_LOW_RE = re.compile(
    rf'(?:{_RIVA_STEM}[^\d,;:\n]{{0,12}}(?<![\d.,])2[.,]5(?![\d.,])'
    rf'|(?<![\d.,])2[.,]5 ?(?:მგ|mg)?[^\w\n]{{0,8}}{_RIVA_STEM})'
)
_RIVA_THER_DOSE_RE = re.compile(r'(?<![\d.,])(?:10|15|20)(?![\d.,])')
_WS_RUN_RE = re.compile(r'[ \t\u00a0]+')
# All split regexes run AFTER _WS_RUN_RE collapses horizontal spaces to a single ' ', guaranteeing O(N) linear time.
_RIVA_CLAUSE_SPLIT_RE = re.compile(r'[;,\n/+]| (?:და|ან|and|or) ')
_STRONG_CLAUSE_SPLIT_RE = re.compile(
    r'[;,\n!()—–]+| - |\.(?!\d)| (?:მაგრამ|ხოლო|თუმცა|but) '
)
_CONJ_SPLIT_RE = re.compile(r' (?:და|ან|and) ')
_KA_VERBS = r'(?:იღებს|ვიღებ|სვამს|ვსვამ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღენიშნება|უფიქსირდება|აწუხებს)'
_PREFIX_NEG_START_RE = re.compile(
    rf'^(?:(?:არ|აღარ|არა|ვერ) {_KA_VERBS}'
    r'|უარყოფს\b|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*'
    r'|(?:no|not|without|stopped|discontinued|held|denied|denies)\b)',
    re.IGNORECASE,
)
_AFFIRM_PRED_RE = re.compile(
    rf'(?:^| )(?:{_KA_VERBS}|უწერია|დაუნიშნ[ა-ჰ]*'
    r'|takes?|taking|uses?|using|started|continues?|has|have|on |allergic)\b',
    re.IGNORECASE,
)
_ALLERGY_WORD_RE = re.compile(r'(?:ალერგი[ა-ჰ]*|\ballerg(?:y|ies|ic)\b)', re.IGNORECASE)
_INLINE_NEG_SPLIT_RE = re.compile(
    r' (?=(?:no|not|without|stopped|discontinued|held) [a-zა-ჰ])'
    r'|(?:(?<=\d)|(?<=მგ)|(?<=mg)|(?<=მკგ)|(?<=mcg)|(?<=გ)|(?<=g)) '
    rf'(?=(?:არ|აღარ|არა|ვერ) {_KA_VERBS} (?!(?:და|ან|მაგრამ|ხოლო|თუმცა|რომ)(?![ა-ჰ]))[a-zა-ჰ])'
    r'|(?<=ალერგია) (?=\S+ (?:არ|არა) (?:აქვს|მაქვს|აღენიშნება|უფიქსირდება|არის))'
    r'|(?:(?<=\d)|(?<=მგ)|(?<=mg)|(?<=მკგ)|(?<=mcg)|(?<=გ)|(?<=g)) '
    r'(?=[a-zა-ჰ0-9\-]+ (?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ) '
    rf'{_KA_VERBS}|მოხსნილია|შეწყვეტილია|შეჩერებულია|მოუხსნეს|შეუწყვიტეს))'
)
_KA_AFFIRM_VERB_AFTER_RE = re.compile(
    r'(?<![ა-ჰ])(?:სვამს|ვსვამ|იღებს|ვიღებ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღენიშნება|უფიქსირდება)'
    r' (?!(?:და|ან|მაგრამ|ხოლო|თუმცა|რომ)(?![ა-ჰ]))(?=[a-zა-ჰ0-9])'
)
_KA_NEG_PARTICLES = frozenset({'არ', 'აღარ', 'არა', 'ვერ'})
_DOUBLE_NEG_AFFIRM_RE = re.compile(
    r'\b(?:not (?:stopped|discontinued|held)|never stopped)\b'
    r'|არ (?:შეუწყვეტია|მოუხსნია|არის (?:შეწყვეტილი|მოხსნილი|შეჩერებული))',
    re.IGNORECASE,
)
_UNINTERRUPTED_AFFIRM_RE = re.compile(
    r'(?:შეწყვეტის|შეჩერების|მოხსნის) გარეშე'
    r'|\bwithout (?:stopping|interruption|discontinuing|holding)\b',
    re.IGNORECASE,
)
_NON_PREFIX_PROTECT_RE = re.compile(
    r'(?<![ა-ჰ])არა[- ]+(?=ანაფილაქსი|სტეროიდ|ინვაზიურ|სპეციფიკურ|ალკოჰოლ|ჰემორაგ|კარდი|ჰეპარინ)'
)
_SYMPTOM_NEG_RE = re.compile(
    r'(?:სისხლდენ[ა-ჰ]*|ჩივილ[ა-ჰ]*|გართულებ[ა-ჰ]*|ტკივილ[ა-ჰ]*|ანაფილაქსი[ა-ჰ]*) '
    r'(?:(?:არ|არა) (?:აქვს|მაქვს|აღნიშნავს|აღენიშნება|ჰქონია|უფიქსირდება|არის)|გარეშე)'
    r'|\b(?:without|no) (?:active )?(?:bleed[a-z]*|hemorrhag[a-z]*|pain|symptom[a-z]*|complication[a-z]*|anaphyla[a-z]*)\b',
    re.IGNORECASE,
)
_RIVA_ESCALATION_STEMS = (
    'დაუნიშნ', 'შეცვ', 'გაიზარდ', 'გადაიყვან', 'დოზ', 'შემდეგ', 'ახლა', 'ხოლო',
    'მაგრამ', 'კარდიოლოგ', 'ექიმ', 'ადრე', 'ნაცვლ',
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
    'არა', 'უარყოფს', 'არაქვს', 'არმაქვს', 'არააქვს', 'ალერგიაარაქვს', 'ალერგიაარმაქვს', 'ალერგიაარააქვს',
    'ალერგიისგარეშე', 'არანაირიალერგია', 'არავითარიალერგია', 'ალერგიაარარის',
    'no', 'none', 'nil', 'nkda', 'nka', 'noallergy', 'noallergies',
})
_NEGATION_PHRASE_RE = re.compile(
    r'(?:(?:არ|აღარ|არა|ვერ) (?:აქვს|მაქვს|სვამს|ვსვამ|იღებს|ვიღებ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|აღნიშნავს|ჰქონია|აღენიშნება|უფიქსირდება|არის|აწუხებს)'
    r'|(?<![ა-ჰ])(?:არაა?(?![ა-ჰ\-—–])|არაქვს(?![ა-ჰ])|არმაქვს(?![ა-ჰ])|არააქვს(?![ა-ჰ])|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*)'
    r'|უარყოფს|მოხსნილია|შეწყვეტილია|შეჩერებულია|მოუხსნეს|შეუწყვიტეს|გამორიცხულია|გარეშე'
    r'|(?<![a-z])(?:no|not|none|nil|nkda|nka|denied|denies|stopped|discontinued|held|without)\b)',
    re.IGNORECASE,
)
_FT_STATIN_STEMS = (
    'ატორვასტატინ', 'როზუვასტატინ', 'სიმვასტატინ', 'პრავასტატინ', 'პიტავასტატინ', 'ფლუვასტატინ',
    'სტატინ', 'კრესტორ', 'ლიპიტორ', 'ლიპრიმ', 'ტულიპ', 'როქსერ', 'მერტენილ', 'ატორის', 'ზოკორ',
    'statin', 'atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin',
    'crestor', 'lipitor', 'liprimar', 'roswera', 'mertenil', 'atoris',
)
_FT_ACEI_ARB_STEMS = (
    'რამიპრილ', 'ენალაპრილ', 'პერინდოპრილ', 'ლიზინოპრილ', 'კაპტოპრილ', 'ფოზინოპრილ',
    'ლოზარტან', 'ვალსარტან', 'კანდესარტან', 'ტელმისარტან', 'ირბესარტან', 'ოლმესარტან',
    'პრესტარიუმ', 'პრესტანს', 'ტრიტაცე', 'კოზაარ', 'დიოვან', 'მიკარდის', 'ატაკანდ', 'ენაპ', 'ნოლიპრელ', 'კო-პრენეს', 'პრენეს',
    'ramipril', 'enalapril', 'perindopril', 'lisinopril', 'captopril',
    'losartan', 'valsartan', 'candesartan', 'telmisartan', 'irbesartan', 'olmesartan',
    'prestarium', 'prestans', 'tritace', 'cozaar', 'diovan', 'micardis', 'atacand', 'enap', 'noliprel',
)
_FT_AMIODARONE_STEMS = ('ამიოდარონ', 'კორდარონ', 'amiodaron', 'cordaron', 'paceron')
_FT_HEPARIN_LMWH_STEMS = (
    'კლექსან', 'clexane', 'ლოვენოქს', 'lovenox', 'ფრაქსიპარ', 'fraxipar',
    'ენოქსაპარ', 'enoxapar', 'ნადროპარ', 'nadropar', 'ფრაგმინ', 'fragmin',
    'დალტეპარ', 'daltepar', 'ციბორ', 'zibor', 'ბემიპარ', 'bemipar',
    'ჰეპარინ', 'heparin',
)
_DIGIT_RE = re.compile(r'\d')


def _normalize_nlp_text(text):
    """Collapse whitespace to single spaces in O(N) and normalize affirmative/non-prefix clinical idioms."""
    low = _WS_RUN_RE.sub(' ', text.replace('\r\n', '\n').replace('\r', '\n').lower())
    low = _DOUBLE_NEG_AFFIRM_RE.sub(' ', low)
    low = _UNINTERRUPTED_AFFIRM_RE.sub(' ', low)
    low = _NON_PREFIX_PROTECT_RE.sub('არა-', low)
    low = _SYMPTOM_NEG_RE.sub(' ', low)
    return _WS_RUN_RE.sub(' ', low)


_GENERIC_SOV_HEADS = frozenset({'ალერგია', 'ალერგიას', 'ალერგიები', 'წამალს', 'წამლებს', 'მედიკამენტს', 'მედიკამენტებს', 'პრეპარატს'})


def _split_inline_boundaries(seg):
    """Split unpunctuated multi-clause utterances (inline negation first, then Georgian finite verb boundary)."""
    out = []
    for sub in _INLINE_NEG_SPLIT_RE.split(seg):
        sub_s = sub.strip()
        if not sub_s:
            continue
        last = 0
        for m in _KA_AFFIRM_VERB_AFTER_RE.finditer(sub_s):
            before = sub_s[last:m.start()].rstrip()
            prev_tok = before.rsplit(' ', 1)[-1] if before else ''
            if prev_tok in _KA_NEG_PARTICLES:
                before_neg = before[: -len(prev_tok)].strip()
                if not before_neg or before_neg in _GENERIC_SOV_HEADS:
                    continue
            verb_end = m.end() - 1
            piece = sub_s[last:verb_end].strip()
            if piece:
                out.append(piece)
            last = verb_end + 1
        tail = sub_s[last:].strip()
        if tail:
            out.append(tail)
    return out


def _split_neg_clauses(cleaned_txt):
    """Split text into negation-scoped clauses, handling Georgian/English prefix & SOV suffix compound negations."""
    out = []
    for chunk in _STRONG_CLAUSE_SPLIT_RE.split(cleaned_txt):
        chunk_s = chunk.strip()
        if not chunk_s:
            continue
        parts = [p.strip() for p in _CONJ_SPLIT_RE.split(chunk_s) if p.strip()]
        merged = [parts[0]]
        for nxt in parts[1:]:
            prev = merged[-1]
            prev_neg = bool(_NEGATION_PHRASE_RE.search(prev))
            nxt_neg = bool(_NEGATION_PHRASE_RE.search(nxt))
            prev_has_alg, nxt_has_alg = bool(_ALLERGY_WORD_RE.search(prev)), bool(_ALLERGY_WORD_RE.search(nxt))
            # 1) Compound prefix negation: merge when right side has no negation, no affirmative verb, no repeated
            #    allergy noun, and no new dose unless prev already carried a dose (e.g. 'stopped aspirin 100mg and clopidogrel 75mg').
            if (
                prev_neg and not nxt_neg and _PREFIX_NEG_START_RE.search(prev)
                and not _AFFIRM_PRED_RE.search(nxt)
                and not (prev_has_alg and nxt_has_alg)
                and (not _DIGIT_RE.search(nxt) or bool(_DIGIT_RE.search(prev)))
            ):
                merged[-1] = f'{prev} და {nxt}'
            # 2) Georgian SOV suffix compound negation ('ასპირინი და პლავიქსი მოხსნილია', 'პენიცილინზე და იოდზე ალერგია არ აქვს')
            elif (
                not prev_neg and nxt_neg and not _PREFIX_NEG_START_RE.search(nxt)
                and not _AFFIRM_PRED_RE.search(prev)
                and not prev_has_alg
                and not _DIGIT_RE.search(prev)
            ):
                merged[-1] = f'{prev} და {nxt}'
            else:
                merged.append(nxt)
        for seg in merged:
            out.extend(_split_inline_boundaries(seg))
    return out


def _positive_clauses_text(text):
    """Strip explicitly negated clauses ('არ აქვს', 'არ სვამს', 'no ...', 'stopped ...') before stem matching."""
    if not isinstance(text, str) or not text.strip():
        return ''
    cleaned_txt = _normalize_nlp_text(text)
    pos = []
    for cl_s in _split_neg_clauses(cleaned_txt):
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
            'matched_keys': set(), 'unstructured_keys': set(), 'unstructured_labels': [],
            'unstructured_antithrombotic': False, 'ertugliflozin': False, 'daily_glp1': False,
            'has_omeprazole': False, 'has_glp1': False, 'has_ft_statin': False,
            'has_ft_acei_arb': False, 'has_ft_amiodarone': False, 'has_ft_heparin_lmwh': False,
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
    matched_keys, unstructured_keys, unstructured_labels, unstructured_at = set(), set(), [], False
    for key, label, is_at, stems in _FREETEXT_HIGH_RISK_MED_RULES:
        if any(st in low for st in stems):
            matched_keys.add(key)
            if not structured_map.get(key, False):
                unstructured_keys.add(key)
                unstructured_labels.append(label)
                if is_at:
                    unstructured_at = True
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
        'has_ft_heparin_lmwh': any(st in low for st in _FT_HEPARIN_LMWH_STEMS),
    }


def append_freetext_med_rows(c, immed_stop_plan, gi_bleed_plan):
    """Append contraindicated/unstructured rows derived from free-text `q.other_meds`."""
    q, med_chips, add_row = c['q'], c['med_chips'], c['add_row']
    ft = c['ft_meds']
    raw_txt = _safe_display_str(q.get('other_meds'), max_len=160)
    if ft.get('has_ft_statin') and 'statin' not in med_chips:
        if c['repro_pregnant']:
            add_row('სტატინი (ტექსტიდან)', 'ორსულობის დროს სტატინი უკუნაჩვენებია (ტერატოგენული რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['liver_3x']:
            add_row('სტატინი (ტექსტიდან)', f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია სტატინი — ღვიძლის ფერმენტების ≥3x მატების ფონზე ჰეპატოტოქსიკურობის გამო სტატინი უკუნაჩვენებია, შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    if c['repro_pregnant'] and ft.get('has_ft_acei_arb') and 'acei_arb' not in med_chips:
        add_row('ACEi / ARB (ტექსტიდან)', 'ორსულობის დროს ACEi / ARB აბსოლუტურად უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობა — FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    if not ft['unstructured_labels']:
        return
    labels, is_at = ', '.join(ft['unstructured_labels']), bool(ft['unstructured_antithrombotic'])
    u_keys = ft.get('unstructured_keys') or set()
    contra_msgs, plan_override = [], ''
    if c['has_hit'] and ft.get('has_ft_heparin_lmwh'):
        contra_msgs.append('HIT ანამნეზში: ჰეპარინი/LMWH აბსოლუტურად უკუნაჩვენებია — დაუყოვნებლივ შეწყვიტეთ და ჩართეთ ჰემატოლოგი!')
        plan_override = 'STOP — HIT! ჰეპარინი/LMWH აბსოლუტურად აკრძალულია!'
    if is_at and c['is_cea_oac_p2y_stop']:
        contra_msgs.append('სისხლჩაქცევის/თრომბოლიზისის ფონზე დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!')
        plan_override = plan_override or 'დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if is_at and c['active_gi_bleed']:
        contra_msgs.append('აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან!')
        plan_override = plan_override or gi_bleed_plan
    if c['repro_pregnant'] and (u_keys & {'warfarin', 'doac', 'dabigatran'}):
        contra_msgs.append('ორსულობის დროს პერორალური OAC უკუნაჩვენებია — დაუყოვნებლივ შეათანხმეთ მეან-გინეკოლოგთან და კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if ('mech_valve' in c['cad_chips']) and (u_keys & {'doac', 'dabigatran'}):
        contra_msgs.append('მექანიკური სარქვლის ფონზე DOAC კატეგორიულად აკრძალულია (ESC Class III)!')
        plan_override = plan_override or immed_stop_plan
    if c['severe_renal'] and (u_keys & {'doac', 'dabigatran', 'metformin'}):
        contra_msgs.append('CrCl/eGFR <30 ან დიალიზზე DOAC/დაბიგატრანი/მეტფორმინი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if c['liver_3x'] and (u_keys & {'doac', 'dabigatran'}):
        contra_msgs.append('ღვიძლის ≥3x დისფუნქციაზე DOAC/დაბიგატრანი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if c['has_stroke_hx'] and ('prasugrel' in u_keys):
        contra_msgs.append('ინსულტის/TIA-ს ანამნეზში პრასუგრელი აბსოლუტურად უკუნაჩვენებია!')
        plan_override = plan_override or immed_stop_plan
    if ('nsaid' in c['eff_allergy_chips']) and ('aspirin' in u_keys):
        contra_msgs.append('ასპირინზე/აასს-ზე ალერგიის ფონზე ასპირინი უკუნაჩვენებია — დაუყოვნებლივ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    is_contra = bool(contra_msgs)
    ft_rule = (
        f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია მაღალი რისკის უკუნაჩვენები პრეპარატი — {" · ".join(contra_msgs)}'
        if is_contra else
        f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია მაღალი რისკის პრეპარატი — მონიშნეთ ჩამრთველი შეჩერების ზუსტი თარიღისთვის!'
    )
    add_row(
        f'დასაზუსტებელი წამალი ({labels})', ft_rule, 'red',
        plan_override if is_contra else 'დააზუსტეთ ექიმთან — არასტრუქტურირებულია!',
        hold_or_skip=(not is_contra), force_date_plan=True,
        contraindicated=is_contra, acute_hold=is_contra,
        unstructured=True, reconcile_only=True,
    )

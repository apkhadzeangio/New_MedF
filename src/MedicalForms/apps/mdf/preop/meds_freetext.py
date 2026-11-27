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
_CONJ_SPLIT_RE = re.compile(r' (?:და|ან|and|or) ')
_KA_VERBS = r'(?:იღებს|ვიღებ|სვამს|ვსვამ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღენიშნება|უფიქსირდება|აწუხებს)'
_PREFIX_NEG_START_RE = re.compile(
    rf'^(?:(?:არ|აღარ|არა|ვერ) {_KA_VERBS}'
    r'|უარყოფს\b|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*'
    r'|(?:no|not|without|stopped|discontinued|held|denied|denies)\b)',
    re.IGNORECASE,
)
_AFFIRM_PRED_RE = re.compile(
    rf'(?:^| )(?:{_KA_VERBS}|უწერია|დაუნიშნ[ა-ჰ]*|გადავიდა|გადაიყვან[ა-ჰ]*'
    r'|takes?|taking|uses?|using|started|continues?|switched|changed|has|have|on |allergic)\b',
    re.IGNORECASE,
)
_ALLERGY_WORD_RE = re.compile(r'(?:ალერგი[ა-ჰ]*|\ballerg(?:y|ies|ic)\b)', re.IGNORECASE)
_INLINE_NEG_SPLIT_RE = re.compile(
    r' (?=(?:no|not|without) [a-zა-ჰ])'
    r'| (?=(?:switched to|changed to|allergic to) [a-zა-ჰ])'
    r"|(?<!\bnot)(?<!\bno)(?<!never)(?<!n't)(?<!stopped)(?<!discontinued) (?=(?:taking|takes|started|continues) [a-zა-ჰ])"
    r"|(?<!\bnot)(?<!\bno)(?<!never)(?<!n't)(?<!allergy)(?<!allergies) (?=on [a-z])"
    r'|(?<=stopped) (?=(?:started|takes|switched|changed|continues|on ))'
    r'|(?<=discontinued) (?=(?:started|takes|switched|changed|continues|on ))'
    r'|(?<=held) (?=(?:started|takes|switched|changed|continues|on ))'
    r'|(?:(?<=შეწყვეტილია)|(?<=მოხსნილია)|(?<=შეჩერებულია)|(?<=შეწყვიტა)|(?<=მოხსნა)|(?<=შეაჩერა)|(?<=მოუხსნეს)|(?<=შეუწყვიტეს)) '
    r'(?!(?:და|ან|მაგრამ|ხოლო|თუმცა|რომ|გამო|წინ|ადრე|დღე|დღის|დღით|თვე|თვის|კვირ|წელ|წლის|წლიდან|საათ|სთ|უკვე|მანამდე|საერთოდ)(?![ა-ჰ]))(?=[a-zა-ჰ])'
    r'|(?<=ალერგია) (?=\S+ (?:არ|არა) (?:აქვს|მაქვს|აღენიშნება|უფიქსირდება|არის))'
    r'|(?:(?<=\d)|(?<=მგ)|(?<=mg)|(?<=მკგ)|(?<=mcg)|(?<=გ)|(?<=g)) '
    r'(?!(?:მგ|mg|მკგ|mcg|გ|g|სე|u|iu|მლ|ml|ტაბ|აბ|tab|x2|2x|bid|qd|od) )'
    r'(?=[a-zა-ჰ0-9\-]+ (?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ) '
    rf'{_KA_VERBS}|მოხსნილია|შეწყვეტილია|შეჩერებულია|მოუხსნეს|შეუწყვიტეს|შეწყვიტა|მოხსნა|შეაჩერა|stopped|discontinued|held))'
)
_EN_STOPPED_RE = re.compile(r'\b(?:stopped|discontinued|held)\b', re.IGNORECASE)
_DOSE_UNIT_END_RE = re.compile(r'(?:\d|მგ|mg|მკგ|mcg|გ|g)$', re.IGNORECASE)
_KA_AFFIRM_VERB_AFTER_RE = re.compile(
    r'(?<![ა-ჰ])(?:სვამს|ვსვამ|იღებს|ვიღებ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღენიშნება|უფიქსირდება|აწუხებს)'
    r' (?!(?:და|ან|მაგრამ|ხოლო|თუმცა|რომ)(?![ა-ჰ]))(?=[a-zა-ჰ0-9])'
)
_KA_VERB_WORD_RE = re.compile(
    r'(?<![ა-ჰ])(?:სვამს|ვსვამ|იღებს|ვიღებ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღენიშნება|უფიქსირდება|აწუხებს)(?![ა-ჰ])'
)
_KA_NEG_PARTICLES = frozenset({'არ', 'აღარ', 'არა', 'ვერ'})
_DOUBLE_NEG_AFFIRM_EN_RE = re.compile(
    r"\b(?:(?:did |has |have |was |were )?not (?:yet )?(?:been )?(?:stop|stopped|discontinue|discontinued|hold|held)"
    r"|(?:hasn't|haven't|didn't|hadn't|isn't|aren't|wasn't|weren't) (?:yet )?(?:been )?(?:stop|stopped|discontinue|discontinued|hold|held)"
    r"|never (?:been )?(?:stop|stopped|discontinue|discontinued|hold|held))\b",
    re.IGNORECASE,
)
_DOUBLE_NEG_AFFIRM_KA_RE = re.compile(
    r'არაა (?:შეწყვეტილი|მოხსნილი|შეჩერებული)'
    r'|(?:არ|არც|ვერ) (?:შეუწყვეტია|მოუხსნია|შეუჩერებია|შეწყვიტა|მოხსნა|შეაჩერა|მოუხსნეს|შეუწყვიტეს|შეაჩერეს'
    r'|(?:არის|აქვს|მაქვს) (?:შეწყვეტილი|მოხსნილი|შეჩერებული))',
)
_UNINTERRUPTED_AFFIRM_RE = re.compile(
    r'(?:შეწყვეტის|შეჩერების|მოხსნის) გარეშე'
    r'|\bwithout (?:stopping|interruption|discontinuing|holding)\b',
    re.IGNORECASE,
)
_NON_PREFIX_PROTECT_RE = re.compile(
    r'(?<![ა-ჰ])არა[- ]+(?=ანაფილაქსი|სტეროიდ|ინვაზიურ|სპეციფიკურ|ალკოჰოლ|ჰემორაგ|კარდი|ჰეპარინ|ფრაქციულ|ვიტამინ|სელექტიურ)'
)
_SYMPTOM_NEG_RE = re.compile(
    r'(?:სისხლდენ[ა-ჰ]*|ჩივილ[ა-ჰ]*|გართულებ[ა-ჰ]*|ტკივილ[ა-ჰ]*|ანაფილაქსი[ა-ჰ]*|შოკ[ა-ჰ]*|შეშუპებ[ა-ჰ]*'
    r'|დანიშნულებ[ა-ჰ]*|რეცეპტ[ა-ჰ]*|კონტროლ[ა-ჰ]*|მონიტორინგ[ა-ჰ]*|ექიმ[ა-ჰ]*|პრობლემ[ა-ჰ]*|ჩვენებ[ა-ჰ]*) '
    r'(?:(?:არ|არა) (?:აქვს|მაქვს|აღნიშნავს|აღენიშნება|ჰქონია|უფიქსირდება|არის)|გარეშე)'
    r'|(?:არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*) (?:სისხლდენ[ა-ჰ]*|ჩივილ[ა-ჰ]*|გართულებ[ა-ჰ]*|ტკივილ[ა-ჰ]*|ანაფილაქსი[ა-ჰ]*|შოკ[ა-ჰ]*|შეშუპებ[ა-ჰ]*)'
    r'|\b(?:without|no) (?:active )?(?:bleed[a-z]*|hemorrhag[a-z]*|pain|symptom[a-z]*|complication[a-z]*|anaphyla[a-z]*|shock|edema)\b',
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
    'დუოპლავინ', 'კოპლავიქს', 'კოპლავიკს',
    'aspirin', 'aspicard', 'cardioaspir', 'cardiomagn', 'trombo-ass', 'trombo ass', 'thrombo ass',
    'duoplavin', 'coplavix',
)
_ASA_WORD_RE = re.compile(r'(?<![a-zა-ჰ])asa(?![a-zა-ჰ])')
_OTHER_XA_DOAC_STEMS = (
    'ელიქვის', 'ელიკვის', 'eliquis', 'აპიქსაბ', 'აპიკსაბ', 'apixab', 'ედიქსან', 'ედოქსაბ', 'ედოკსაბ', 'edoxab',
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
    r'|უარყოფს|მოხსნილია|შეწყვეტილია|შეჩერებულია|მოუხსნეს|შეუწყვიტეს|შეწყვიტა|მოხსნა|შეაჩერა|გამორიცხულია'
    r'|(?<![ა-ჰ])გარეშე(?![ა-ჰ])'
    r"|(?<![a-z])(?:no|not|none|nil|nkda|nka|denied|denies|stopped|discontinued|held|without|never|cannot|doesn't|don't|isn't|aren't|didn't|hasn't|haven't|hadn't|wasn't|weren't|won't|wouldn't|can't)\b)",
    re.IGNORECASE,
)
_FT_STATIN_STEMS = (
    'ატორვასტატინ', 'ატორვა', 'როზუვასტატინ', 'სიმვასტატინ', 'პრავასტატინ', 'პიტავასტატინ', 'ფლუვასტატინ',
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
_FT_LMWH_STEMS = (
    'კლექსან', 'clexane', 'ლოვენოქს', 'lovenox', 'ფრაქსიპარ', 'fraxipar',
    'ენოქსაპარ', 'enoxapar', 'ნადროპარ', 'nadropar', 'ფრაგმინ', 'fragmin',
    'დალტეპარ', 'daltepar', 'ციბორ', 'zibor', 'ბემიპარ', 'bemipar',
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
_DIGIT_RE = re.compile(r'\d')
_COMPACT_STRIP_RE = re.compile(r'[\s.,;:!?()\[\]"\'—–-]+')
_NEG_FAST_HINT_RE = re.compile(
    r"\b(?:არ|აღარ|არა|ვერ|მოხსნ|შეწყვ|შეჩერ|შეაჩერ|გამორ|უარყ|გარეშე"
    r"|no|not|none|nil|nkda|nka|denied|denies|stopped|discontinued|held|without|never|cannot|doesn't|don't|isn't|aren't|didn't|hasn't|haven't|hadn't|wasn't|weren't|won't|wouldn't|can't)\b"
    r'|\b(?:არა|არაქვს|არმაქვს|არააქვს|არანაირ|არავითარ|უარყოფს|მოხსნ|შეწყვ|შეჩერ|შეაჩერ|მოუხსნ|შეუწყვ|გამორიცხ)',
    re.IGNORECASE,
)


def _has_neg_phrase(s):
    return bool(_NEG_FAST_HINT_RE.search(s) and _NEGATION_PHRASE_RE.search(s))


def _normalize_nlp_text(text):
    """Collapse whitespace to single spaces in O(N) and normalize affirmative/non-prefix clinical idioms."""
    low = _WS_RUN_RE.sub(' ', text.strip().replace('\r\n', '\n').replace('\r', '\n').lower())
    changed = False
    if 'not ' in low or 'never ' in low or "n't " in low:
        low, changed = _DOUBLE_NEG_AFFIRM_EN_RE.sub(' continues ', low), True
    if 'არ ' in low or 'არაა ' in low or 'არც ' in low or 'ვერ ' in low:
        low, changed = _DOUBLE_NEG_AFFIRM_KA_RE.sub(' უწერია ', low), True
    if 'გარეშე' in low or 'without ' in low:
        low, changed = _UNINTERRUPTED_AFFIRM_RE.sub(' ', low), True
    if 'არა ' in low or 'არა-' in low:
        low = _NON_PREFIX_PROTECT_RE.sub('არა-', low)
    if 'არ ' in low or 'არა ' in low or 'არანაირ' in low or 'არავითარ' in low or 'გარეშე' in low or 'without ' in low or 'no ' in low:
        low, changed = _SYMPTOM_NEG_RE.sub(' ', low), True
    if changed and '  ' in low:
        low = _WS_RUN_RE.sub(' ', low)
    return low.strip()


_GENERIC_SOV_HEADS = frozenset({'ალერგია', 'ალერგიას', 'ალერგიები', 'წამალს', 'წამლებს', 'მედიკამენტს', 'მედიკამენტებს', 'პრეპარატს'})


def _split_ka_two_verb_tail(piece):
    """Split unpunctuated Georgian neg->pos or pos->neg 2-verb clauses in O(N)."""
    matches = list(_KA_VERB_WORD_RE.finditer(piece))
    if len(matches) < 2:
        return [piece]
    m0, m_last = matches[0], matches[-1]
    pre0 = piece[:m0.start()].rstrip()
    tok0 = pre0.rsplit(' ', 1)[-1] if pre0 else ''
    pre_last = piece[:m_last.start()].rstrip()
    tok_last = pre_last.rsplit(' ', 1)[-1] if pre_last else ''
    is_neg_then_pos = (tok0 in _KA_NEG_PARTICLES and tok_last not in _KA_NEG_PARTICLES)
    is_pos_then_neg = (tok0 not in _KA_NEG_PARTICLES and tok_last in _KA_NEG_PARTICLES)
    if not (is_neg_then_pos or is_pos_then_neg):
        return [piece]
    end_limit = m_last.start() if is_neg_then_pos else piece.rfind(tok_last, m0.end(), m_last.start())
    if end_limit <= m0.end():
        return [piece]
    between = piece[m0.end():end_limit].strip()
    if not between:
        return [piece]
    b_toks = between.split(' ')
    if len(b_toks) == 1:
        if is_pos_then_neg:
            return [piece]
        left, right = piece[:m0.end()].strip(), piece[m0.end():].strip()
    else:
        cut_idx = len(b_toks) - 1
        while cut_idx > 1 and (_DIGIT_RE.search(b_toks[cut_idx]) or b_toks[cut_idx] in ('მგ', 'mg', 'მკგ', 'mcg', 'გ', 'g')):
            cut_idx -= 1
        cut_tok = b_toks[cut_idx]
        cut_pos = piece.rfind(' ' + cut_tok, m0.end(), end_limit)
        if cut_pos <= m0.end():
            return [piece]
        left, right = piece[:cut_pos].strip(), piece[cut_pos:].strip()
    return [x for x in (left, right) if x]


def _split_en_stopped(sub_s):
    """Split unpunctuated English 'stopped/discontinued/held' clauses cleanly in O(N)."""
    if ('stopped' not in sub_s and 'discontinued' not in sub_s and 'held' not in sub_s) or not _EN_STOPPED_RE.search(sub_s):
        return [sub_s]
    out, last = [], 0
    for m in _EN_STOPPED_RE.finditer(sub_s):
        before = sub_s[last:m.start()].strip()
        after = sub_s[m.end():].strip()
        if not before:
            continue
        if _AFFIRM_PRED_RE.search(before):
            out.append(before)
            last = m.start()
        elif after:
            out.append(sub_s[last:m.end()].strip())
            last = m.end()
    tail = sub_s[last:].strip()
    if tail:
        out.append(tail)
    return out or [sub_s]


def _split_inline_boundaries(seg):
    """Split unpunctuated multi-clause utterances in O(N) time."""
    if not _has_neg_phrase(seg):
        return [seg]
    out, seen_subs = [], set()
    for raw_sub in _INLINE_NEG_SPLIT_RE.split(seg):
        for sub in _split_en_stopped(raw_sub.strip()):
            sub_s = sub.strip()
            if not sub_s or sub_s in seen_subs:
                continue
            seen_subs.add(sub_s)
            if not _has_neg_phrase(sub_s):
                out.append(sub_s)
                continue
            verb_total = len(_KA_VERB_WORD_RE.findall(sub_s))
            last, pieces = 0, []
            for m in _KA_AFFIRM_VERB_AFTER_RE.finditer(sub_s):
                before = sub_s[last:m.start()].rstrip()
                if not before:
                    continue
                prev_tok = before.rsplit(' ', 1)[-1] if before else ''
                if prev_tok in _KA_NEG_PARTICLES:
                    before_neg = before[: -len(prev_tok)].strip()
                    if not before_neg or before_neg in _GENERIC_SOV_HEADS:
                        continue
                    if _KA_VERB_WORD_RE.search(before_neg) or (verb_total == 1 and _DOSE_UNIT_END_RE.search(before_neg)):
                        neg_pos = sub_s.rfind(prev_tok, last, m.start())
                        if neg_pos > last:
                            pieces.append(sub_s[last:neg_pos].strip())
                            last = neg_pos
                            continue
                if _PREFIX_NEG_START_RE.search(before):
                    pieces.append(before)
                    last = m.start()
                    continue
                verb_end = m.end() - 1
                piece = sub_s[last:verb_end].strip()
                if piece:
                    pieces.append(piece)
                last = verb_end + 1
            tail = sub_s[last:].strip()
            if tail:
                pieces.append(tail)
            for pc in pieces:
                out.extend(_split_ka_two_verb_tail(pc))
    return out


def _is_uniform_neg_chain(chunk_s):
    """Fast O(N) check for long pure prefix- or suffix-negated conjunction chains."""
    if _ALLERGY_WORD_RE.search(chunk_s) or _DIGIT_RE.search(chunk_s):
        return False
    m_pref = _PREFIX_NEG_START_RE.search(chunk_s)
    if m_pref:
        rest = chunk_s[m_pref.end():]
        if not _AFFIRM_PRED_RE.search(rest) and not _INLINE_NEG_SPLIT_RE.search(rest):
            return True
    if len(chunk_s) > 200:
        for conj in (' და ', ' ან ', ' and ', ' or '):
            r_idx = chunk_s.rfind(conj)
            if r_idx > 0:
                tail = chunk_s[r_idx + len(conj):].strip()
                if not _PREFIX_NEG_START_RE.search(tail) and _has_neg_phrase(tail):
                    head = chunk_s[:r_idx]
                    if not _has_neg_phrase(head) and not _AFFIRM_PRED_RE.search(head):
                        return True
    return False


def _split_neg_clauses(cleaned_txt):
    """Split text into negation-scoped clauses strictly in O(N) time (no O(N^2) string re-scans)."""
    out, seen_chunks, seen_segs = [], set(), set()
    for chunk in _STRONG_CLAUSE_SPLIT_RE.split(cleaned_txt):
        chunk_s = chunk.strip()
        if not chunk_s or chunk_s in seen_chunks:
            continue
        seen_chunks.add(chunk_s)
        if not _has_neg_phrase(chunk_s):
            out.append(chunk_s)
            continue
        if _is_uniform_neg_chain(chunk_s):
            continue
        parts = [p.strip() for p in _CONJ_SPLIT_RE.split(chunk_s) if p.strip()]
        if not parts:
            continue
        cur_buf = [parts[0]]
        cur_neg = _has_neg_phrase(parts[0])
        cur_pref_neg = cur_neg and bool(_PREFIX_NEG_START_RE.search(parts[0]))
        cur_alg = cur_dig = cur_aff = None
        groups = []
        for nxt in parts[1:]:
            nxt_neg = _has_neg_phrase(nxt)
            if cur_neg and not nxt_neg and cur_pref_neg:
                if cur_alg is None:
                    cur_alg = bool(_ALLERGY_WORD_RE.search(cur_buf[0]))
                if cur_dig is None:
                    cur_dig = bool(_DIGIT_RE.search(cur_buf[0]))
                nxt_aff = bool(_AFFIRM_PRED_RE.search(nxt))
                nxt_alg = bool(_ALLERGY_WORD_RE.search(nxt)) if cur_alg else False
                nxt_dig = bool(_DIGIT_RE.search(nxt)) if not cur_dig else False
                if not nxt_aff and not (cur_alg and nxt_alg) and (not nxt_dig or cur_dig):
                    cur_buf.append(nxt)
                    continue
            elif not cur_neg and nxt_neg and not _PREFIX_NEG_START_RE.search(nxt):
                if cur_aff is None:
                    cur_aff = bool(_AFFIRM_PRED_RE.search(cur_buf[0]))
                if cur_alg is None:
                    cur_alg = bool(_ALLERGY_WORD_RE.search(cur_buf[0]))
                if cur_dig is None:
                    cur_dig = bool(_DIGIT_RE.search(cur_buf[0]))
                nxt_dig = bool(_DIGIT_RE.search(nxt)) if cur_dig else False
                if not cur_aff and not cur_alg and (not cur_dig or nxt_dig):
                    cur_buf.append(nxt)
                    cur_neg = True
                    continue
            groups.append(' და '.join(cur_buf))
            cur_buf = [nxt]
            cur_neg = nxt_neg
            cur_pref_neg = nxt_neg and bool(_PREFIX_NEG_START_RE.search(nxt))
            cur_alg = cur_dig = cur_aff = None
        groups.append(' და '.join(cur_buf))
        for seg in groups:
            if seg in seen_segs:
                continue
            seen_segs.add(seg)
            out.extend(_split_inline_boundaries(seg))
    return out


def _positive_clauses_text(text):
    """Strip explicitly negated clauses ('არ აქვს', 'არ სვამს', 'no ...', 'stopped ...') before stem matching."""
    if not isinstance(text, str) or not text.strip():
        return ''
    cleaned_txt = _normalize_nlp_text(text)
    if not cleaned_txt:
        return ''
    if not _has_neg_phrase(cleaned_txt):
        if len(cleaned_txt) <= 40 and _COMPACT_STRIP_RE.sub('', cleaned_txt) in _ALLERGY_NEGATION_WORDS:
            return ''
        return cleaned_txt
    pos, seen_pos = [], set()
    for cl_s in _split_neg_clauses(cleaned_txt):
        if cl_s in seen_pos or _has_neg_phrase(cl_s):
            continue
        if len(cl_s) <= 40:
            compact = _COMPACT_STRIP_RE.sub('', cl_s)
            if not compact or compact in _ALLERGY_NEGATION_WORDS:
                continue
        seen_pos.add(cl_s)
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
            'has_ft_riva_low': False, 'has_ft_full_oac': False,
            'has_ft_unstruct_warfarin': False, 'has_ft_unstruct_doac': False, 'has_ft_unstruct_riva_low': False,
        }
    q_d = q if isinstance(q, dict) else {}
    med_chips = set(_str_list(q_d.get('med_chips')))
    eff_alg_chips, _, _ = _effective_allergy_state(q_d)
    has_ft_riva_low = bool(_RIVA_LOW_RE.search(low)) and not _riva_has_therapeutic_dose(low) and not any(st in low for st in _OTHER_XA_DOAC_STEMS)
    riva_low_only = ('rivaroxaban_2_5' in med_chips) and has_ft_riva_low
    structured_map = {
        'warfarin': bool(q_d.get('med_warfarin')),
        'doac': bool(q_d.get('med_doac')) or riva_low_only,
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
    has_ft_lmwh = any(st in low for st in _FT_LMWH_STEMS)
    has_ft_ufh = any(st in low for st in _FT_UFH_STEMS) or bool(_UFH_WORD_RE.search(low))
    if has_ft_ufh:
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
    has_ft_unstruct_riva_low = ('doac' in unstructured_keys) and has_ft_riva_low
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
        'has_ft_full_oac': has_ft_full_oac,
        'has_ft_unstruct_warfarin': has_ft_unstruct_warfarin,
        'has_ft_unstruct_doac': has_ft_unstruct_doac,
        'has_ft_unstruct_riva_low': has_ft_unstruct_riva_low,
    }


def _collect_ft_contraindications(c, ft, u_keys, is_at, immed_stop_plan, gi_bleed_plan):
    from .calculators import _sat_age
    from .registry import rule_value
    from .utils import _num
    contra_msgs, extra_notes, plan_override = [], [], ''
    if c.get('is_rupture_suspect') and ft.get('has_ft_full_oac'):
        contra_msgs.append('რუპტურირებული AAA-ის ეჭვზე აუცილებელია ანტიკოაგულაციის გადაუდებელი რევერსია (4F-PCC / იდარუციზუმაბი)!')
        plan_override = 'გადაუდებელი რევერსია (4F-PCC / იდარუციზუმაბი) — გადავადების გარეშე!'
    if is_at and c['is_cea_oac_p2y_stop']:
        contra_msgs.append('სისხლჩაქცევის/თრომბოლიზისის ფონზე დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!')
        plan_override = plan_override or 'დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if is_at and c['active_gi_bleed']:
        contra_msgs.append('აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან!')
        plan_override = plan_override or gi_bleed_plan
    if c['repro_pregnant'] and (u_keys & {'warfarin', 'doac', 'dabigatran', 'ticagrelor', 'prasugrel', 'sglt2', 'glp1'}):
        contra_msgs.append('ორსულობის დროს პერორალური OAC / ტიკაგრელორი / პრასუგრელი / SGLT2 / GLP-1 უკუნაჩვენებია — დაუყოვნებლივ შეათანხმეთ მეან-გინეკოლოგთან და ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if ('mech_valve' in c['cad_chips']) and (u_keys & {'doac', 'dabigatran'}):
        contra_msgs.append('მექანიკური სარქვლის ფონზე DOAC კატეგორიულად აკრძალულია (ESC Class III)!')
        plan_override = plan_override or immed_stop_plan
    if c['severe_renal'] and (u_keys & {'doac', 'dabigatran', 'metformin'}):
        contra_msgs.append('CrCl/eGFR <30 ან დიალიზზე DOAC/დაბიგატრანი/მეტფორმინი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if c.get('on_dialysis') and ('sglt2' in u_keys):
        contra_msgs.append('პროგრამულ დიალიზზე SGLT2 ინჰიბიტორი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ნეფროლოგთან/ენდოკრინოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if c['liver_3x'] and (u_keys & {'doac', 'dabigatran', 'metformin', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('ღვიძლის ≥3x დისფუნქციაზე DOAC/მეტფორმინი/ტიკაგრელორი/პრასუგრელი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if c['has_stroke_hx'] and (('prasugrel' in u_keys) or (ft.get('has_ft_riva_low') and 'doac' in u_keys)):
        contra_msgs.append('ინსულტის/TIA-ს ანამნეზში პრასუგრელი / დაბალდოზიანი რივაროქსაბანი უკუნაჩვენებია!')
        plan_override = plan_override or immed_stop_plan
    if ('nsaid' in c['eff_allergy_chips']) and (('aspirin' in u_keys) or (ft.get('has_ft_riva_low') and 'doac' in u_keys)):
        contra_msgs.append('ასპირინზე/აასს-ზე ალერგიის ფონზე ასპირინი / VOYAGER უკუნაჩვენებია — დაუყოვნებლივ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if ('clopidogrel_intol' in c['eff_allergy_chips']) and ('clopidogrel' in u_keys):
        contra_msgs.append('კლოპიდოგრელზე აუტანლობის ფონზე პრეპარატი უკუნაჩვენებია — შეარჩიეთ უსაფრთხო ალტერნატივა!')
        plan_override = plan_override or immed_stop_plan
    cilo_cfg = rule_value('MED_CILOSTAZOL_BLACKBOX')
    if ('cilostazol' in u_keys) and (
        c.get('is_cea_oac_p2y_stop') or c.get('chf_any')
        or (c.get('lvef_cur') is not None and c['lvef_cur'] < cilo_cfg['lvef_min'])
        or c.get('on_dialysis') or c.get('creat_extreme_hi')
        or (c.get('eff_renal') is not None and c['eff_renal'] <= cilo_cfg['crcl_min'])
        or (c['s'].get('ecg') == 'ischemia_arrhythmia')
        or (c.get('hr_cur') is not None and c['hr_cur'] > cilo_cfg['hr_max'])
        or c.get('liver_3x') or c.get('active_gi_bleed')
        or bool(c['q'].get('recent_stent')) or c.get('has_acs_stent_12m') or c.get('repro_pregnant')
    ):
        contra_msgs.append(f'გულის უკმარისობის / LVEF <{cilo_cfg["lvef_min"]}% / CrCl ≤{cilo_cfg["crcl_min"]} / ტაქიარითმიის / სტენტის / ორსულობის / თირკმელ-ღვიძლის უკმარისობის ფონზე ცილოსტაზოლი აბსოლუტურად უკუნაჩვენებია (FDA Black Box)!')
        plan_override = plan_override or immed_stop_plan
    has_any_oac = bool(c.get('has_full_oac') or ('rivaroxaban_2_5' in c['med_chips']) or ft.get('has_ft_full_oac') or ft.get('has_ft_unstruct_riva_low'))
    has_potent_p2y = bool(c['q'].get('med_ticagrelor') or c['q'].get('med_prasugrel') or (u_keys & {'ticagrelor', 'prasugrel'}))
    if has_any_oac and has_potent_p2y and (u_keys & {'warfarin', 'doac', 'dabigatran', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('OAC + ტიკაგრელორი/პრასუგრელი ESC Class III (ჰემორაგიის რისკი) — დაუყოვნებლივ შეათანხმეთ კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if c.get('oac_count', 0) >= 2 and (u_keys & {'warfarin', 'doac', 'dabigatran'}):
        contra_msgs.append('ორმაგი პერორალური ანტიკოაგულაცია (≥2 OAC / რივაროქსაბანი 2.5) — დაუყოვნებლივ დააზუსტეთ ქირურგთან/კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if c.get('p2y12_cnt', 0) >= 2 and (u_keys & {'clopidogrel', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('ორმაგი P2Y12 ანტიაგრეგანტი (ESC Class III ჰემორაგიის რისკი) — დაუყოვნებლივ შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if 'prasugrel' in u_keys:
        age_p = _sat_age(c['patient'].age) or 0
        wt_p = _num(c['s'].get('weight_kg'), lo=25, hi=260)
        if age_p >= 75:
            extra_notes.append('[≥75 წ. — FDA Boxed Warning: პრასუგრელი ზოგადად არ არის რეკომენდებული ფატალური/ქალასშიდა სისხლდენის რისკის გამო!]')
        if wt_p is not None and wt_p < 60:
            extra_notes.append('[<60 კგ — FDA Boxed Warning: სისხლდენის რისკი, განიხილეთ 5 მგ/დღ ან კლოპიდოგრელზე დეესკალაცია!]')
    if 'amiodarone' in u_keys:
        amio_ren = rule_value('MED_DOAC_HOLD_INTERVALS')['amio_renal']
        if c['q'].get('med_warfarin') or ft.get('has_ft_unstruct_warfarin'):
            extra_notes.append('[CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]')
        if (c['q'].get('med_doac') or c['q'].get('med_dabigatran') or 'rivaroxaban_2_5' in c['med_chips'] or ft.get('has_ft_unstruct_doac') or ft.get('has_ft_unstruct_riva_low')) and (c['on_dialysis'] or (c['eff_renal'] is not None and c['eff_renal'] < amio_ren)):
            extra_notes.append(f'[P-gp ურთიერთქმედება: DOAC/დაბიგატრანთან და CrCl <{amio_ren}/დიალიზზე ზრდის სისხლდენის რისკს!]')
    return contra_msgs, extra_notes, plan_override


def _append_ft_parenteral_row(c, ft, raw_txt, immed_stop_plan, gi_bleed_plan):
    matched = ft.get('matched_keys') or set()
    if not (matched & {'parenteral_oac', 'parenteral_dti'}):
        return
    u_keys = ft.get('unstructured_keys') or set()
    q, med_chips, add_row = c['q'], c['med_chips'], c['add_row']
    msgs, plan_ov = [], ''
    if c['has_hit'] and (ft.get('has_ft_lmwh') or ft.get('has_ft_ufh')):
        msgs.append('HIT ანამნეზში: ჰეპარინი/LMWH აბსოლუტურად უკუნაჩვენებია — დაუყოვნებლივ შეწყვიტეთ და ჩართეთ ჰემატოლოგი!')
        plan_ov = 'STOP — HIT! ჰეპარინი/LMWH აბსოლუტურად აკრძალულია!'
    if c['severe_renal'] and (ft.get('has_ft_lmwh') or ft.get('has_ft_fondaparinux')):
        msgs.append('CrCl/eGFR <30 ან დიალიზზე LMWH / ფონდაპარინუქსი აკუმულირდება და უკუნაჩვენებია (უპირატესია არაფრაქციული ჰეპარინი — UFH) — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['liver_3x'] and ft.get('has_ft_argatroban'):
        msgs.append('ღვიძლის ≥3x დისფუნქციაზე არგატრობანის დოზა მცირდება 75%-ით (უპირატესია ბივალირუდინი) — სასწრაფოდ შეათანხმეთ ჰემატოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['is_cea_oac_p2y_stop']:
        msgs.append('სისხლჩაქცევის/თრომბოლიზისის ფონზე დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!')
        plan_ov = plan_ov or 'დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if c['active_gi_bleed']:
        msgs.append('აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან!')
        plan_ov = plan_ov or gi_bleed_plan
    if c['repro_pregnant'] and (ft.get('has_ft_fondaparinux') or ('parenteral_dti' in matched)):
        msgs.append('ორსულობის დროს ფონდაპარინუქსი / IV DTI შეათანხმეთ მეან-გინეკოლოგთან და ჰემატოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['has_mech_valve'] and ft.get('has_ft_fondaparinux'):
        msgs.append('მექანიკურ სარქველზე ფონდაპარინუქსი აკრძალულია (თრომბოზის მაღალი რისკი) — დაუყოვნებლივ შეათანხმეთ კარდიოლოგთან და ჰემატოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['is_evla'] and q.get('med_warfarin'):
        msgs.append('ორმაგი ანტიკოაგულაცია EVLA-ზე (უწყვეტი ვარფარინი + პარენტერული ანტიკოაგულანტი) — დაუყოვნებლივ შეათანხმეთ ქირურგთან/კარდიოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    has_doac_conc = bool(q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips or (u_keys & {'doac', 'dabigatran'}))
    if has_doac_conc:
        msgs.append('ორმაგი ანტიკოაგულაცია (DOAC + პარენტერული ანტიკოაგულანტი) — დაუყოვნებლივ შეათანხმეთ ქირურგთან/კარდიოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    is_pure_dti = bool(('parenteral_dti' in matched) and not ft.get('has_ft_lmwh') and not ft.get('has_ft_ufh') and not ft.get('has_ft_fondaparinux'))
    is_pure_ufh = bool(ft.get('has_ft_ufh') and not ft.get('has_ft_lmwh') and not ft.get('has_ft_fondaparinux') and ('parenteral_dti' not in matched))
    if is_pure_dti:
        drug_lbl = 'არაჰეპარინული პარენტერული ანტიკოაგულანტი (არგატრობანი / ბივალირუდინი)'
    elif is_pure_ufh:
        drug_lbl = 'პარენტერული ანტიკოაგულანტი — არაფრაქციული ჰეპარინი (UFH)'
    else:
        drug_lbl = 'პარენტერული ანტიკოაგულანტი (LMWH / ფონდაპარინუქსი / ჰეპარინი)'
    if msgs:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია პარენტერული ანტიკოაგულანტი — {" · ".join(msgs)}',
            'red', plan_ov, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True,
        )
    elif is_pure_dti:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია არაჰეპარინული პარენტერული ანტიკოაგულანტი (IV DTI — არგატრობანი / ბივალირუდინი) — შეათანხმეთ შეჩერების დრო ჰემატოლოგთან/ქირურგთან (IV ინფუზიის შეჩერება −2–4 სთ aPTT კონტროლით; ASRA ნეირაქსიული ბლოკადა დაბლოკილია!).',
            'amber', 'შეათანხმეთ ქირურგთან (IV −2–4 სთ, aPTT)',
            hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=False, reconcile_only=True,
        )
    elif is_pure_ufh:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია არაფრაქციული ჰეპარინი (UFH) — შეათანხმეთ შეჩერების დრო ქირურგთან/ანესთეზიოლოგთან (IV UFH −4–6 სთ, SC UFH −12 სთ, ASRA ნეირაქსიული aPTT კონტროლით; CrCl <30/დიალიზზე დასაშვებია).',
            'amber', 'შეათანხმეთ ქირურგთან (IV −4–6 სთ / SC −12 სთ)',
            hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=False, reconcile_only=True,
        )
    else:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია პარენტერული ანტიკოაგულანტი — შეათანხმეთ შეჩერების დრო ქირურგთან/ანესთეზიოლოგთან (LMWH პროფილაქტ. −12 სთ / თერაპ. −24 სთ; ფონდაპარინუქსი −36–48 სთ; ASRA ნეირაქსიული კონტროლი!).',
            'amber', 'შეათანხმეთ ქირურგთან (−12–24 სთ)',
            hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=False, reconcile_only=True,
        )


def append_freetext_med_rows(c, immed_stop_plan, gi_bleed_plan):
    """Append contraindicated/unstructured rows derived from free-text `q.other_meds`."""
    q, med_chips, add_row = c['q'], c['med_chips'], c['add_row']
    ft = c['ft_meds']
    raw_txt = _safe_display_str(q.get('other_meds'), max_len=160)
    u_keys = ft.get('unstructured_keys') or set()
    if ft.get('has_ft_statin') and 'statin' not in med_chips:
        if c['repro_pregnant']:
            add_row('სტატინი (ტექსტიდან)', 'ორსულობის დროს სტატინი უკუნაჩვენებია (ტერატოგენული რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['liver_3x']:
            add_row('სტატინი (ტექსტიდან)', f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია სტატინი — ღვიძლის ფერმენტების ≥3x მატების ფონზე ჰეპატოტოქსიკურობის გამო სტატინი უკუნაჩვენებია, შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    if ft.get('has_ft_acei_arb') and 'acei_arb' not in med_chips:
        if c['repro_pregnant']:
            add_row('ACEi / ARB (ტექსტიდან)', 'ორსულობის დროს ACEi / ARB აბსოლუტურად უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობა — FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c.get('acei_acute_hold'):
            add_row('ACEi / ARB (ტექსტიდან)', f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია ACEi/ARB — დაბალი წნევის ან ჰიპერკალიემიის ფონზე ოპერაციის დილის დოზა გამოტოვეთ და შეათანხმეთ ექიმთან!', 'red', 'გამოტოვეთ დილას! (დაბალი წნევა / მაღალი K+)', hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=True)
    has_any_oac_ns = bool(c['has_full_oac'] or ('rivaroxaban_2_5' in med_chips) or c['has_ft_full_oac'] or ft.get('has_ft_unstruct_riva_low'))
    has_dapt_ns = bool((q.get('med_aspirin') or 'aspirin' in u_keys) and c['p2y12_cnt'] >= 1) or (c['p2y12_cnt'] >= 2)
    if ft.get('has_ft_nsaid') and (
        ('nsaid' in c['eff_allergy_chips']) or c['active_gi_bleed'] or c['severe_renal']
        or c['repro_pregnant'] or c['liver_3x'] or has_any_oac_ns or has_dapt_ns
    ):
        ns_reasons = [lbl for cond, lbl in (
            ('nsaid' in c['eff_allergy_chips'], 'აასს-ზე ალერგია'),
            (c['active_gi_bleed'], 'აქტიური GI სისხლდენა/წყლული'),
            (c['severe_renal'], 'თირკმლის მძიმე უკმარისობა (<30)'),
            (c['repro_pregnant'], 'ორსულობა'),
            (c['liver_3x'], 'ღვიძლის მძიმე დისფუნქცია (≥3x)'),
            (has_any_oac_ns, 'ანტიკოაგულანტთან ერთად სისხლდენის მაღალი რისკი'),
            (has_dapt_ns, 'ორმაგ ანტიაგრეგანტთან (DAPT) სისხლდენის მაღალი რისკი'),
        ) if cond]
        add_row(
            'ტკივილგამაყუჩებელი აასს (ტექსტიდან)',
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია არასტეროიდული აასს — უკუნაჩვენებია ({", ".join(ns_reasons)})! შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ექიმთან!',
            'red', gi_bleed_plan if c['active_gi_bleed'] else immed_stop_plan,
            hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True,
        )
    _append_ft_parenteral_row(c, ft, raw_txt, immed_stop_plan, gi_bleed_plan)
    if not ft['unstructured_labels']:
        return
    labels, is_at = ', '.join(ft['unstructured_labels']), bool(ft['unstructured_antithrombotic'])
    contra_msgs, extra_notes, plan_override = _collect_ft_contraindications(c, ft, u_keys, is_at, immed_stop_plan, gi_bleed_plan)
    is_contra = bool(contra_msgs)
    note_sfx = (' ' + ' '.join(extra_notes)) if extra_notes else ''
    ft_rule = (
        f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია მაღალი რისკის უკუნაჩვენები პრეპარატი — {" · ".join(contra_msgs)}{note_sfx}'
        if is_contra else
        f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია მაღალი რისკის პრეპარატი — მონიშნეთ ჩამრთველი შეჩერების ზუსტი თარიღისთვის!{note_sfx}'
    )
    add_row(
        f'დასაზუსტებელი წამალი ({labels})', ft_rule, 'red',
        plan_override if is_contra else 'დააზუსტეთ ექიმთან — არასტრუქტურირებულია!',
        hold_or_skip=(not is_contra), force_date_plan=True,
        contraindicated=is_contra, acute_hold=is_contra,
        unstructured=True, reconcile_only=True,
    )

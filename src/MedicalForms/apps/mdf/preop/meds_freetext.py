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
    r'|(?<=ალერგია) (?=\S+ (?:არ|არა) (?:აქვს|მაქვს|აღენიშნება|უფიქსირდება|არის))'
    r'|(?:(?<=\d)|(?<=მგ)|(?<=mg)|(?<=მკგ)|(?<=mcg)|(?<=გ)|(?<=g)|(?<=მლ)|(?<=ml)|(?<=სე)|(?<=iu)'
    r'|(?<=bid)|(?<=tid)|(?<=qid)|(?<=qd)|(?<=od)|(?<=daily)|(?<=x2)|(?<=2x)|(?<=ტაბ)|(?<=tab)|(?<=აბი)) '
    r'(?!(?:მგ|mg|მკგ|mcg|გ|g|სე|u|iu|მლ|ml|ტაბ|აბი|აბ|tab|x2|2x|bid|tid|qid|qd|od|once|twice|daily) )'
    r'(?=[a-zა-ჰ0-9\-]+ (?:\d+(?:[.,]\d+)?(?: ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu))? )?'
    rf'(?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ) {_KA_VERBS}|მოხსნილია|შეწყვეტილია|შეჩერებულია|მოუხსნეს|შეუწყვიტეს|შეწყვიტა|მოხსნა|შეაჩერა|stopped|discontinued|held))'
)
_KA_NEG_VERB_RE = re.compile(
    r'(?<![ა-ჰ])(?:შეწყვეტილია|მოხსნილია|შეჩერებულია|შეწყვიტა|მოხსნა|შეაჩერა|მოუხსნეს|შეუწყვიტეს)(?![ა-ჰ])'
)
_KA_NEG_FOLLOW_STOP = frozenset({
    'და', 'ან', 'მაგრამ', 'ხოლო', 'თუმცა', 'რომ', 'გამო', 'წინ', 'ადრე', 'უკვე', 'მანამდე', 'საერთოდ',
    'დღე', 'დღის', 'დღით', 'დღეა', 'დღეები', 'თვე', 'თვის', 'თვეა', 'კვირა', 'კვირის', 'კვირით', 'კვირაა',
    'წელ', 'წლის', 'წლიდან', 'წელია', 'საათ', 'საათის', 'საათით', 'სთ',
    'მგ', 'mg', 'მკგ', 'mcg', 'გ', 'g', 'მლ', 'ml', 'სე', 'iu', 'ტაბ', 'აბი', 'აბ', 'tab',
    'x2', '2x', 'bid', 'tid', 'qid', 'qd', 'od', 'once', 'twice', 'daily',
})
_KA_PREFIX_NEG_HEAD_RE = re.compile(
    r'^[a-zა-ჰ0-9\-]+(?: \d+(?:[.,]\d+)?(?: ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu))?)?(?= [a-zა-ჰ])'
)
_STOPPED_BUT_NOT_RE = re.compile(
    r'\b(stopped|discontinued|held|off) ([^,;.!?\n]{1,80}?),? but not (?=[a-zა-ჰ])',
    re.IGNORECASE,
)
_SUFFIX_STOPPED_BUT_NOT_RE = re.compile(
    r'([^,;.!?\n]{1,80}? (?:stopped|discontinued|held)),? but not (?=[a-zა-ჰ])',
    re.IGNORECASE,
)
_KA_STOPPED_BUT_NOT_RE = re.compile(
    r'([^,;.!?\n]{0,80}(?:შეწყვეტილია|მოხსნილია|შეჩერებულია|შეწყვიტა|მოხსნა|შეაჩერა|მოუხსნეს|შეუწყვიტეს)[^,;.!?\n]{0,40}),? მაგრამ არა (?=[a-zა-ჰ])'
)
_KA_STOPPED_BUT_SOV_NOT_RE = re.compile(
    r'([^,;.!?\n]{0,80}(?:შეწყვეტილია|მოხსნილია|შეჩერებულია|შეწყვიტა|მოხსნა|შეაჩერა|მოუხსნეს|შეუწყვიტეს)[^,;.!?\n]{0,40}),? მაგრამ ([a-zა-ჰ0-9 .\-]{1,40}?) არა(?=$|[,;.!?\n])'
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
    'თინზაპარ', 'tinzapar', 'ინოჰეპ', 'innohep', 'დაბალმოლეკულ', 'low molecular', 'low-molecular',
)
_LMWH_WORD_RE = re.compile(r'(?<![a-zა-ჰ])(?:lmwh|ლმვჰ)(?![a-zა-ჰ])')
_LMWH_HEPARIN_PHRASE_RE = re.compile(r'(?:დაბალმოლეკულ[ა-ჰ]*|low[\s-]+molecular(?:[\s-]+weight)?) (?:ჰეპარინ[ა-ჰ]*|heparin)')
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
    if ' but not ' in low:
        low = _STOPPED_BUT_NOT_RE.sub(r'\1 \2; ', low)
        low, changed = _SUFFIX_STOPPED_BUT_NOT_RE.sub(r'\1; ', low), True
    if ' მაგრამ ' in low:
        low = _KA_STOPPED_BUT_NOT_RE.sub(r'\1; ', low)
        low, changed = _KA_STOPPED_BUT_SOV_NOT_RE.sub(r'\1; \2', low), True
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


def _split_ka_neg_clauses(sub_s):
    """Split unpunctuated Georgian prefix/suffix negation verbs cleanly in O(N)."""
    if not _KA_NEG_VERB_RE.search(sub_s):
        return [sub_s]
    out, cur = [], sub_s
    while cur:
        m = _KA_NEG_VERB_RE.search(cur)
        if not m:
            out.append(cur)
            break
        before = cur[:m.start()].strip()
        after = cur[m.end():].strip()
        if not before:
            hm = _KA_PREFIX_NEG_HEAD_RE.match(after) if after else None
            if hm:
                rest = after[hm.end():].strip()
                nxt = rest.split(' ', 1)[0] if rest else ''
                if nxt and nxt not in _KA_NEG_FOLLOW_STOP and not _DIGIT_RE.match(nxt):
                    out.append(f'{cur[:m.end()]} {hm.group(0)}'.strip())
                    cur = rest
                    continue
            out.append(cur)
            break
        if _AFFIRM_PRED_RE.search(before):
            out.append(before)
            cur = cur[m.start():].strip()
            continue
        if after:
            nxt = after.split(' ', 1)[0]
            if nxt and nxt not in _KA_NEG_FOLLOW_STOP and not _DIGIT_RE.match(nxt):
                out.append(cur[:m.end()].strip())
                cur = after
                continue
        out.append(cur)
        break
    return out or [sub_s]


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
        for ka_sub in _split_ka_neg_clauses(raw_sub.strip()):
            for sub in _split_en_stopped(ka_sub.strip()):
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


from .meds_helpers import (  # noqa: E402, F401
    _append_ft_parenteral_row,
    _collect_ft_contraindications,
    append_freetext_med_rows,
)

"""O(N) negation-scoped clause normalization and splitting for Georgian/English clinical free-text."""
import re

_WS_RUN_RE = re.compile(r'[ \t\u00a0]+')
# All split regexes run AFTER _WS_RUN_RE collapses horizontal spaces to a single ' ', guaranteeing O(N) linear time.
_SENT_CLAUSE_SPLIT_RE = re.compile(
    r'[;\n!()—–]+| - |\.(?!\d)| (?:მაგრამ|ხოლო|თუმცა|but) '
)
_STRONG_CLAUSE_SPLIT_RE = re.compile(
    r'[;,\n!()—–]+| - |\.(?!\d)| (?:მაგრამ|ხოლო|თუმცა|but) '
)
_CONJ_SPLIT_RE = re.compile(r' (?:და|ან|and|or) ')
_KA_VERBS = r'(?:იღებს|ვიღებ|სვამს|ვსვამ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღენიშნება|უფიქსირდება|აწუხებს)'
_KA_STOP_VERBS = (
    r'(?:შეწყვეტილია|მოხსნილია|შეჩერებულია|შეწყვიტა|მოხსნა|შეაჩერა|მოუხსნეს|შეუწყვიტეს|შეაჩერეს'
    r'|მოხსნეს|შეწყვიტეს|შევწყვიტე|მოვხსენი|შევაჩერე|შევწყვიტეთ|მოვხსენით|შევაჩერეთ)'
)
_PREFIX_NEG_START_RE = re.compile(
    rf'^(?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ) {_KA_VERBS}'
    rf'|{_KA_STOP_VERBS}(?![ა-ჰ])'
    r'|უარყოფს\b|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*'
    r'|(?:no|not|without|stopped|discontinued|held|denied|denies)\b)',
    re.IGNORECASE,
)
_AFFIRM_PRED_RE = re.compile(
    rf'(?:^| )(?:{_KA_VERBS}|უწერია|დაუნიშნ[ა-ჰ]*|გადავიდა|გადაიყვან[ა-ჰ]*'
    r'|takes?|taking|uses?|using|started|continues?|continuing|switched|changed|has|have|on |allergic)\b',
    re.IGNORECASE,
)
_ALLERGY_WORD_RE = re.compile(r'(?:ალერგი[ა-ჰ]*|\ballerg(?:y|ies|ic)\b)', re.IGNORECASE)
_INLINE_NEG_SPLIT_RE = re.compile(
    r' (?=(?:no|not|without) [a-zა-ჰ])'
    r'| (?=(?:switched to|changed to|allergic to) [a-zა-ჰ])'
    r"|(?<!\bnot)(?<!\bno)(?<!never)(?<!n't)(?<!stopped)(?<!discontinued) (?=(?:taking|takes|using|uses|started|continues|continuing) [a-zა-ჰ])"
    r"|(?<!\bnot)(?<!\bno)(?<!never)(?<!n't)(?<!allergy)(?<!allergies) (?=on [a-z])"
    r'|(?<=stopped) (?=(?:started|takes|taking|uses|using|switched|changed|continues|continuing|on ))'
    r'|(?<=discontinued) (?=(?:started|takes|taking|uses|using|switched|changed|continues|continuing|on ))'
    r'|(?<=held) (?=(?:started|takes|taking|uses|using|switched|changed|continues|continuing|on ))'
    r'|(?<=ალერგია) (?=\S+ (?:არ|არა) (?:აქვს|მაქვს|აღენიშნება|უფიქსირდება|არის))'
    r'|(?:(?<=\d)|(?<=\dმგ)|(?<= მგ)|(?<=\dmg)|(?<= mg)|(?<=\dმკგ)|(?<= მკგ)|(?<=\dmcg)|(?<= mcg)'
    r'|(?<=\dგ)|(?<= გ)|(?<=\dg)|(?<= g)|(?<=\dმლ)|(?<= მლ)|(?<=\dml)|(?<= ml)|(?<=\dსე)|(?<= სე)|(?<=\diu)|(?<= iu)'
    r'|(?<= bid)|(?<= tid)|(?<= qid)|(?<= qd)|(?<= od)|(?<= daily)|(?<= x2)|(?<= 2x)|(?<= ტაბ)|(?<= tab)|(?<= აბი)) '
    r'(?!(?:\d\S*|მგ|mg|მკგ|mcg|გ|g|სე|u|iu|მლ|ml|ტაბ|აბი|აბ|tab|tabs|tablets?|caps?|capsules?|pills?'
    r'|x2|2x|bid|tid|qid|qd|od|qhs|qod|hs|prn|po|iv|sc|sq|im|once|twice|daily|per|day|days|d|h'
    r'|in|at|the|morning|evening|night|bedtime|am|pm|დღეში|დილას|საღამოს|ღამით|ძილის|წინ|წინა|ჯერ|დოზა'
    r'|ყოველდღე|უზმოზე|გუშინ|დღეს|ადრე|უკვე|მანამდე|საერთოდ'
    r'|yesterday|today|previously|already|recently|earlier|nightly|orally|weekly|before|prior|last)(?: |$))'
    r'(?=[a-zა-ჰ0-9\-]+ (?:\d+(?:[.,/]\d+)?(?: ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu))? )?'
    rf'(?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ) {_KA_VERBS}|{_KA_STOP_VERBS}|stopped|discontinued|held))'
)
_KA_NEG_VERB_RE = re.compile(rf'(?<![ა-ჰ]){_KA_STOP_VERBS}(?![ა-ჰ])')
_KA_NEG_PARTICLE_VERB_RE = re.compile(rf'(?<![ა-ჰ])(?:არ|აღარ|არა|ვერ) {_KA_VERBS}(?![ა-ჰ])')
_KA_NEG_FOLLOW_STOP = frozenset({
    'და', 'ან', 'მაგრამ', 'ხოლო', 'თუმცა', 'რომ', 'გამო', 'წინ', 'წინა', 'ადრე', 'უკვე', 'მანამდე', 'საერთოდ', 'გუშინ', 'დღეს',
    'ყოველდღე', 'უზმოზე',
    'and', 'or', 'but', 'in', 'the', 'at', 'for', 'ago',
    'დღე', 'დღის', 'დღით', 'დღეა', 'დღეები', 'თვე', 'თვის', 'თვეა', 'კვირა', 'კვირის', 'კვირით', 'კვირაა',
    'წელ', 'წლის', 'წლიდან', 'წელია', 'საათ', 'საათის', 'საათით', 'სთ', 'დილას', 'საღამოს', 'ღამით', 'ჯერ', 'დოზა',
    'მგ', 'mg', 'მკგ', 'mcg', 'გ', 'g', 'მლ', 'ml', 'სე', 'iu', 'ტაბ', 'აბი', 'აბ', 'tab', 'tabs', 'tablet', 'tablets',
    'x2', '2x', 'bid', 'tid', 'qid', 'qd', 'od', 'once', 'twice', 'daily', 'დღეში', 'd', 'h',
})
_KA_MULTIWORD_HEAD_PREFIXES = (
    'დაბალმოლეკულ', 'არაფრაქციულ', 'სისხლის', 'ანტითრომბ', 'ანტიკოაგულ', 'პერორალურ', 'ყველა', 'ორივე',
)
_EN_NEG_FOLLOW_STOP = frozenset({
    'and', 'or', 'but', 'და', 'ან', 'მაგრამ', 'ხოლო', 'თუმცა', 'the', 'am', 'pm', 'bedtime',
    'for', 'ago', 'day', 'days', 'week', 'weeks', 'wk', 'wks', 'month', 'months', 'mo', 'mos', 'year', 'years', 'yr', 'yrs', 'd', 'h',
    'hr', 'hrs', 'hour', 'hours', 'prior', 'before', 'due', 'because', 'by', 'on', 'in', 'at', 'per',
    'with', 'without', 'since', 'last', 'yesterday', 'today', 'after', 'already', 'recently', 'previously', 'earlier',
    'nightly', 'orally', 'weekly',
    'completely', 'all', 'both', 'med', 'meds', 'medication', 'medications', 'drug', 'drugs', 'therapy',
    'anticoagulant', 'anticoagulants', 'antiplatelet', 'antiplatelets', 'blood', 'thinner', 'thinners',
    'pill', 'pills', 'tablet', 'tablets', 'tabs', 'capsule', 'capsules', 'dose', 'doses', 'injection', 'injections', 'preop', 'pre-op',
    'surgery', 'operation', 'procedure', 'bleeding', 'bleed', 'doctor', 'physician', 'cardiologist',
    'mg', 'mcg', 'g', 'ml', 'iu', 'u', 'tab', 'caps', 'cap', 'x2', '2x', 'bid', 'tid', 'qid', 'qd', 'od', 'qhs', 'qod', 'hs', 'prn',
    'once', 'twice', 'daily', 'morning', 'evening', 'night', 'po', 'iv', 'sc', 'im', 'sq',
    'მგ', 'მკგ', 'გ', 'მლ', 'სე', 'ტაბ', 'აბი', 'აბ', 'დღეში', 'დილას', 'საღამოს', 'ღამით', 'დოზა',
})
_EN_NEG_HEAD_SKIP = frozenset({
    'all', 'both', 'my', 'the', 'his', 'her', 'any', 'no', 'blood', 'oral', 'low', 'molecular',
    'unfractionated', 'antiplatelet', 'anticoagulant', 'antithrombotic', 'dual', 'triple', 'beta',
    'calcium', 'proton', 'prior', 'previous', 'old', 'chronic', 'regular', 'daily', 'home', 'usual',
})
_MULTIWORD_DRUG_HEAD = (
    r'(?:low[- ]molecular(?:[- ]weight)? heparin'
    r'|unfractionated heparin'
    r'|acetylsalicylic acid'
    r'|დაბალმოლეკულ[ა-ჰ]*(?: წონის)? ჰეპარინ[ა-ჰ]*'
    r'|არაფრაქციულ[ა-ჰ]* ჰეპარინ[ა-ჰ]*'
    r'|აცეტილსალიცილ[ა-ჰ]* მჟავ[ა-ჰ]*)'
)
_MULTIWORD_DRUG_HEAD_RE = re.compile(rf'^{_MULTIWORD_DRUG_HEAD}(?=$| )', re.IGNORECASE)
_FREQ_FORM_TOK = (
    r'(?:x\d+|\d+x|\d+-?ჯერ|bid|tid|qid|qd|od|q\d+h|qhs|qod|hs|prn|po|iv|sc|sq|im'
    r'|once|twice|daily|nightly|orally|weekly|morning|evening|night|დილას|საღამოს|ღამით|დღეში|ყოველდღე|უზმოზე'
    r'|in the (?:morning|evening|am|pm)|at (?:night|bedtime)'
    r'|ტაბ|აბი|აბ|tab|tabs|tablets?|caps?|capsules?)(?![a-zა-ჰ])'
)
_DOSE_UNIT_TOK = (
    r'\d+(?:[.,/]\d+)? ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu)'
    r'(?:/(?:day|d|daily|24h|24hr|24hrs|დღეში|დღე|დღ|სთ))?(?![a-zა-ჰ])'
)
_COUNT_DUR_UNIT = (
    r'(?:აბი|აბ|ტაბ|tab|tabs|tablets?|caps?|capsules?|pills?|დოზა|dose'
    r'|დღის|დღე|დღით|დღეა|კვირის|კვირა|თვის|თვე|წლის|საათის|საათით|სთ'
    r'|days?|weeks?|wks?|months?|mos?|years?|yrs?|hrs?|hours?|d|h)'
)
_COUNT_OR_DUR_TOK = (
    r'(?:\d+(?:[.,/]\d+)? ?(?:აბი|აბ|ტაბ|tab|tabs|tablets?|caps?|capsules?|pills?|დოზა|dose)(?![a-zა-ჰ])'
    r'|(?:for )?\d+(?:[.,/]\d+)? ?(?:დღის|დღე|დღით|დღეა|კვირის|კვირა|თვის|თვე|წლის|საათის|საათით|სთ'
    r'|days?|weeks?|wks?|months?|mos?|years?|yrs?|hrs?|hours?|d|h)(?![a-zა-ჰ])(?: (?:წინ|ადრე|ago|prior|before)(?![a-zა-ჰ]))?)'
)
_TEMPORAL_ADV_TOK = (
    r'(?:yesterday|today|earlier|previously|recently|already|before|prior'
    r'|last (?:day|week|month|year)|ადრე|უკვე|გუშინ|დღეს|მანამდე|საერთოდ|წინა (?:კვირას|თვეს|დღეს))(?![a-zა-ჰ])'
)
_POST_STOP_TAIL_RE = re.compile(
    rf'^(?:(?:{_COUNT_OR_DUR_TOK}|{_TEMPORAL_ADV_TOK})(?: |$))+',
    re.IGNORECASE,
)
_BARE_NUM_TOK = rf'\d+(?:[.,/]\d+)?(?! ?{_COUNT_DUR_UNIT}(?![a-zა-ჰ]))'
_HEAD_MOD_TOK = rf'(?:{_DOSE_UNIT_TOK}|{_COUNT_OR_DUR_TOK}|{_TEMPORAL_ADV_TOK}|{_FREQ_FORM_TOK}|{_BARE_NUM_TOK})'
_EXCEPT_ITEM_HEAD = rf'(?:{_MULTIWORD_DRUG_HEAD}|[a-zა-ჰ0-9\-]+)(?: {_HEAD_MOD_TOK}){{0,6}}'
_KA_PREFIX_NEG_HEAD_RE = re.compile(rf'^{_EXCEPT_ITEM_HEAD}(?= [a-zა-ჰ])', re.IGNORECASE)
_FRONTED_EXCEPT_HEAD_RE = re.compile(
    rf'^{_EXCEPT_ITEM_HEAD}'
    r'(?: (?:and|or|და|ან) '
    r'(?!(?:all|both|no|not|nothing|none|stopped|discontinued|held|ყველა|ორივე|არ|არა|არაფერ|არცერთ|მოხსნ|შეწყვ|შეჩერ)(?![a-zა-ჰ]))'
    rf'{_EXCEPT_ITEM_HEAD}){{0,4}}(?=$| )',
    re.IGNORECASE,
)
_EN_BUT_NOT_RE = re.compile(r' but not (?=[a-zა-ჰ])', re.IGNORECASE)
_EN_EXCEPT_RE = re.compile(r'(?:^| )except (?:for )?(?=[a-zა-ჰ])', re.IGNORECASE)
_EN_STOP_WORD_RE = re.compile(r'\b(?:stopped|discontinued|held|off)\b', re.IGNORECASE)
_KA_BUT_NOT_RE = re.compile(r' მაგრამ არა (?=[a-zა-ჰ])')
_KA_BUT_SOV_NOT_RE = re.compile(
    r' მაგრამ ([a-zა-ჰ0-9 .\-]{1,40}?) არა(?=$|[,;.!?\n(\[—–]| [(\[—–-])'
)
_KA_GARDA_RE = re.compile(r'(?:^| )გარდა(?=$|[,;.!?\n ])')
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
    r'|(?:არ|არც|ვერ) (?:შეუწყვეტია|მოუხსნია|შეუჩერებია|შემიწყვეტია|მომიხსნია|შემიჩერებია'
    r'|შეგვიწყვეტია|მოგვიხსნია|შეგვიჩერებია|შეწყვიტა|მოხსნა|შეაჩერა|შევწყვიტე|მოვხსენი|შევაჩერე'
    r'|შევწყვიტეთ|მოვხსენით|შევაჩერეთ|მოუხსნეს|შეუწყვიტეს|შეაჩერეს|მოხსნეს|შეწყვიტეს'
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
_ALLERGY_NEGATION_WORDS = frozenset({
    'არა', 'უარყოფს', 'არაქვს', 'არმაქვს', 'არააქვს', 'ალერგიაარაქვს', 'ალერგიაარმაქვს', 'ალერგიაარააქვს',
    'ალერგიისგარეშე', 'არანაირიალერგია', 'არავითარიალერგია', 'ალერგიაარარის',
    'არაფერი', 'არაფერზე', 'არაფერს', 'არაფრის', 'არცერთი', 'არცერთზე', 'არცერთს',
    'არაფერსვიღებ', 'არაფერსვსვამ', 'არაფერსარვიღებ',
    'no', 'none', 'nothing', 'nil', 'nkda', 'nka', 'noallergy', 'noallergies',
})
_NEGATION_PHRASE_RE = re.compile(
    r'(?:(?:არ|აღარ|არა|ვერ) (?:აქვს|მაქვს|სვამს|ვსვამ|იღებს|ვიღებ|ღებულობს|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აღნიშნავს|ჰქონია|აღენიშნება|უფიქსირდება|არის|აწუხებს)'
    r'|(?<![ა-ჰ])(?:არაა?(?![ა-ჰ\-—–])|არაქვს(?![ა-ჰ])|არმაქვს(?![ა-ჰ])|არააქვს(?![ა-ჰ])|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*|არაფერ[ა-ჰ]*|არაფრის(?![ა-ჰ])|არცერთ[ა-ჰ]*)'
    rf'|უარყოფს|{_KA_STOP_VERBS}|გამორიცხულია'
    r'|(?<![ა-ჰ])გარეშე(?![ა-ჰ])'
    r"|(?<![a-z])(?:no|not|none|nothing|nil|nkda|nka|denied|denies|stopped|discontinued|held|without|never|cannot|doesn't|don't|isn't|aren't|didn't|hasn't|haven't|hadn't|wasn't|weren't|won't|wouldn't|can't)\b)",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r'\d')
_COMPACT_STRIP_RE = re.compile(r'[\s.,;:!?()\[\]"\'—–-]+')
_NEG_FAST_HINT_RE = re.compile(
    r"\b(?:არ|აღარ|არა|ვერ|მოხსნ|მოვხსნ|მოვხსენ|შეწყვ|შევწყვ|შეჩერ|შეაჩერ|შევაჩერ|გამორ|უარყ|გარეშე"
    r"|no|not|none|nothing|nil|nkda|nka|denied|denies|stopped|discontinued|held|without|never|cannot|doesn't|don't|isn't|aren't|didn't|hasn't|haven't|hadn't|wasn't|weren't|won't|wouldn't|can't)\b"
    r'|\b(?:არა|არაქვს|არმაქვს|არააქვს|არანაირ|არავითარ|არაფერ|არაფრის|არცერთ|უარყოფს|მოხსნ|მოვხსნ|მოვხსენ|შეწყვ|შევწყვ|შეჩერ|შეაჩერ|შევაჩერ|მოუხსნ|შეუწყვ|შემიწყვ|მომიხსნ|შემიჩერ|შეგვიწყვ|მოგვიხსნ|შეგვიჩერ|გამორიცხ)',
    re.IGNORECASE,
)
_GENERIC_SOV_HEADS = frozenset({'ალერგია', 'ალერგიას', 'ალერგიები', 'წამალს', 'წამლებს', 'მედიკამენტს', 'მედიკამენტებს', 'პრეპარატს'})
_DOSE_FREQ_WORDS = frozenset({
    'მგ', 'mg', 'მკგ', 'mcg', 'გ', 'g', 'მლ', 'ml', 'სე', 'iu', 'u', 'd', 'h',
    'ტაბ', 'აბი', 'აბ', 'tab', 'tabs', 'tablet', 'tablets', 'cap', 'caps', 'capsule', 'capsules', 'pill', 'pills',
    'daily', 'once', 'twice', 'bid', 'tid', 'qid', 'qd', 'od', 'qhs', 'qod', 'hs', 'prn', 'x2', '2x',
    'po', 'iv', 'sc', 'sq', 'im', 'in', 'at', 'the', 'per', 'day', 'days', 'morning', 'evening', 'night', 'bedtime', 'am', 'pm',
    'დღეში', 'დილას', 'საღამოს', 'ღამით', 'ძილის', 'წინ', 'წინა', 'ჯერ', 'დოზა',
    'ყოველდღე', 'უზმოზე', 'გუშინ', 'დღეს', 'ადრე', 'უკვე', 'მანამდე', 'საერთოდ',
    'yesterday', 'today', 'previously', 'already', 'recently', 'earlier', 'nightly', 'orally', 'weekly', 'before', 'prior', 'last',
})
_MULTIWORD_DRUG_PREV_MODS = frozenset({'low', 'molecular', 'weight', 'unfractionated', 'acetylsalicylic', 'დაბალმოლეკულური', 'არაფრაქციული', 'წონის'})


def _has_neg_phrase(s):
    return bool(_NEG_FAST_HINT_RE.search(s) and _NEGATION_PHRASE_RE.search(s))


def _rfind_sent_sep(text):
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch in (';', '!', '?', '\n'):
            return i
        if ch == '.' and not (0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()):
            return i
    return -1


def _find_sent_sep(text):
    n = len(text)
    for i, ch in enumerate(text):
        if ch in (';', '!', '?', '\n'):
            return i
        if ch == '.' and not (0 < i < n - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()):
            return i
    return -1


def _prev_clause(s, pos, window=120):
    end = pos - 1 if pos > 0 and s[pos - 1] == ',' else pos
    chunk = s[max(0, end - window):end]
    idx = _rfind_sent_sep(chunk)
    if idx != -1:
        chunk = chunk[idx + 1:]
    c_idx = chunk.rfind(',')
    return chunk[c_idx + 1:] if c_idx != -1 else chunk


def _prev_clause_info(s, pos, window=140):
    chunk = s[max(0, pos - window):pos]
    idx = _rfind_sent_sep(chunk)
    if idx != -1:
        chunk = chunk[idx + 1:]
    c_idx = chunk.rfind(',')
    return (chunk[c_idx + 1:], chunk[:c_idx]) if c_idx != -1 else (chunk, '')


def _prev_sentence(s, pos, window=140):
    chunk = s[max(0, pos - window):pos]
    idx = _rfind_sent_sep(chunk)
    if idx == -1:
        return ''
    before = chunk[:idx].rstrip(' ;.!?\n')
    prev_idx = _rfind_sent_sep(before)
    return before[prev_idx + 1:] if prev_idx != -1 else before


def _next_sent_chunk(s, pos, window=140):
    chunk = s[pos:min(len(s), pos + window)]
    idx = _find_sent_sep(chunk)
    return chunk[:idx] if idx != -1 else chunk


def _rewrite_en_except(low):
    """Rewrite fronted and inline English 'except [for]' exception clauses strictly in O(N)."""
    out, last = [], 0
    for m in _EN_EXCEPT_RE.finditer(low):
        if m.start() < last:
            continue
        prev_raw, before_comma = _prev_clause_info(low, m.start())
        prev_s = prev_raw.strip()
        if not prev_s and not before_comma.strip():
            seg = _next_sent_chunk(low, m.end())
            c_idx = seg.find(',')
            if c_idx != -1:
                obj, tail = seg[:c_idx].strip(), seg[c_idx + 1:]
                end_pos = m.end() + c_idx + 1
            else:
                hm = _FRONTED_EXCEPT_HEAD_RE.match(seg)
                obj_len = hm.end() if hm else len(seg.split(' ', 1)[0])
                obj, tail = seg[:obj_len].strip(), seg[obj_len:]
                end_pos = m.end() + obj_len
            gov_ctx = tail if tail.strip() else _prev_sentence(low, m.start())
            gov_neg = _has_neg_phrase(gov_ctx) or bool(_EN_STOP_WORD_RE.search(gov_ctx))
            out.append(low[last:m.start()])
            out.append(f'{obj};' if gov_neg else f'not {obj};')
            last = end_pos
            continue
        gov = prev_s or before_comma
        gov_neg = _has_neg_phrase(gov) or bool(_EN_STOP_WORD_RE.search(gov))
        out.append(low[last:m.start()])
        out.append('; ' if gov_neg else '; not ')
        last = m.end()
    out.append(low[last:])
    return ''.join(out)


def _rewrite_ka_garda(low):
    """Rewrite pre/post/fronted Georgian 'გარდა' exception clauses (with or without commas) in O(N)."""
    out, last = [], 0
    for m in _KA_GARDA_RE.finditer(low):
        if m.start() < last:
            continue
        prev_raw, before_comma = _prev_clause_info(low, m.start())
        prev = prev_raw.strip()
        nxt_ch = low[m.end():m.end() + 1]
        skip_comma = 1 if nxt_ch == ',' else 0
        after_sent = _next_sent_chunk(low, m.end() + skip_comma)
        after_c = after_sent.split(',', 1)[0]
        if not prev and not before_comma.strip():
            seg = _next_sent_chunk(low, m.end())
            lead_ws = len(seg) - len(seg.lstrip(' ,'))
            seg_s = seg[lead_ws:]
            c_idx = seg_s.find(',')
            if c_idx != -1:
                obj, tail = seg_s[:c_idx].strip(), seg_s[c_idx + 1:]
                end_pos = m.end() + lead_ws + c_idx + 1
            else:
                hm = _FRONTED_EXCEPT_HEAD_RE.match(seg_s)
                obj_len = hm.end() if hm else len(seg_s.split(' ', 1)[0])
                obj, tail = seg_s[:obj_len].strip(), seg_s[obj_len:]
                end_pos = m.end() + lead_ws + obj_len
            gov_ctx = tail if tail.strip() else _prev_sentence(low, m.start())
            out.append(low[last:m.start()])
            out.append(f'{obj};' if _has_neg_phrase(gov_ctx) else f'არ იღებს {obj};')
            last = end_pos
            continue
        prev_neg, bc_neg, after_neg = _has_neg_phrase(prev), _has_neg_phrase(before_comma), _has_neg_phrase(after_c)
        prev_sent_neg = (
            not prev_neg and not bc_neg and not after_neg
            and not before_comma.strip() and not after_c.strip()
            and not _AFFIRM_PRED_RE.search(prev)
            and _has_neg_phrase(_prev_sentence(low, m.start()))
        )
        at_end = nxt_ch in ('', ',', ';', '.', '!', '?', '\n')
        if prev_neg:
            neg_ms = list(_NEGATION_PHRASE_RE.finditer(prev_raw))
            obj = prev_raw[neg_ms[-1].end():].strip() if neg_ms else ''
            if obj:
                cut = m.start() - len(obj)
                if cut >= last:
                    out.append(low[last:cut].rstrip())
                    out.append(f'; {obj}')
                    last = m.end() + skip_comma
                    continue
            out.append(low[last:m.start()])
            out.append(';')
            last = m.end() + skip_comma
        elif bc_neg or after_neg or prev_sent_neg:
            out.append(low[last:m.start()])
            out.append(';')
            last = m.end() + skip_comma
        elif not at_end and ((not prev and before_comma.strip()) or _AFFIRM_PRED_RE.search(prev)):
            out.append(low[last:m.start()])
            out.append('; არ იღებს')
            last = m.end()
        elif prev and (before_comma.strip() or not _AFFIRM_PRED_RE.search(prev)):
            cut = m.start() - len(prev)
            if cut >= last:
                out.append(low[last:cut].rstrip())
                out.append(f'; არ იღებს {prev};')
                last = m.end() + skip_comma
        else:
            tok = prev.rsplit(' ', 1)[-1] if prev else ''
            cut = m.start() - len(tok) if tok else -1
            if tok and cut >= last:
                out.append(low[last:cut].rstrip())
                out.append(f'; არ იღებს {tok}')
                last = m.end() + skip_comma
    out.append(low[last:])
    return ''.join(out)


def _normalize_nlp_text(text):
    """Collapse whitespace to single spaces in O(N) and normalize affirmative/non-prefix clinical idioms."""
    low = _WS_RUN_RE.sub(' ', text.strip().replace('\r\n', '\n').replace('\r', '\n').lower())
    changed = False
    if 'except ' in low:
        src = low
        low = _rewrite_en_except(src)
        changed = changed or (low != src)
    if 'გარდა' in low:
        src = low
        low = _rewrite_ka_garda(src)
        changed = changed or (low != src)
    if ' but not ' in low:
        src = low
        low = _EN_BUT_NOT_RE.sub(
            lambda m: '; ' if _EN_STOP_WORD_RE.search(_prev_clause(src, m.start(), 100)) else m.group(0),
            src,
        )
        changed = changed or (low != src)
    if ' მაგრამ ' in low:
        src = low
        low = _KA_BUT_NOT_RE.sub(
            lambda m: '; ' if _KA_NEG_VERB_RE.search(_prev_clause(src, m.start())) else m.group(0),
            src,
        )
        src2 = low
        low = _KA_BUT_SOV_NOT_RE.sub(
            lambda m: f'; {m.group(1)}' if _KA_NEG_VERB_RE.search(_prev_clause(src2, m.start())) else m.group(0),
            src2,
        )
        changed = changed or (low != src)
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
        while cut_idx > 1 and (_DIGIT_RE.search(b_toks[cut_idx]) or b_toks[cut_idx] in _DOSE_FREQ_WORDS):
            cut_idx -= 1
        cut_tok = b_toks[cut_idx]
        cut_pos = piece.rfind(' ' + cut_tok, m0.end(), end_limit)
        if cut_pos <= m0.end():
            return [piece]
        left, right = piece[:cut_pos].strip(), piece[cut_pos:].strip()
    return [x for x in (left, right) if x]


def _split_affirm_before_stop(before, stop_tail):
    """Peel trailing suffix-stopped drug (+ dose/frequency/multi-word head) off an affirmative clause."""
    m_aff = list(_AFFIRM_PRED_RE.finditer(before))
    if not m_aff:
        return before, stop_tail
    aff_end = m_aff[-1].end()
    toks = before[aff_end:].strip().split(' ') if before[aff_end:].strip() else []
    if len(toks) < 2:
        return before, stop_tail
    cut = len(toks) - 1
    while cut > 0 and (_DIGIT_RE.search(toks[cut]) or toks[cut] in _DOSE_FREQ_WORDS):
        cut -= 1
    while cut > 0 and toks[cut - 1] in _MULTIWORD_DRUG_PREV_MODS:
        cut -= 1
    if cut < 1:
        return before, stop_tail
    return (before[:aff_end] + ' ' + ' '.join(toks[:cut])).strip(), (' '.join(toks[cut:]) + ' ' + stop_tail).strip()


def _peel_conj_affirm_stop(nxt):
    """When preceded by an affirmative conjunction ('taking A and B C stopped'), peel active B from suffix-stopped C."""
    for verb_re in (_KA_NEG_VERB_RE, _KA_NEG_PARTICLE_VERB_RE, _EN_STOPPED_RE):
        m = verb_re.search(nxt)
        if not m or m.start() == 0:
            continue
        before = nxt[:m.start()].strip()
        tail = nxt[m.start():].strip()
        if not before:
            continue
        synth = f'takes {before}'
        left, right = _split_affirm_before_stop(synth, tail)
        if left != synth and left.startswith('takes '):
            return [left[6:].strip(), right]
    return None


def _split_stop_verb_clauses(sub_s, verb_re, follow_stop, is_skip_fn):
    """Split unpunctuated prefix/suffix stop-verb clauses (including post-stop duration tails) cleanly in O(N)."""
    if not verb_re.search(sub_s):
        return [sub_s]
    out, cur = [], sub_s
    while cur:
        m = verb_re.search(cur)
        if not m:
            out.append(cur)
            break
        before, after = cur[:m.start()].strip(), cur[m.end():].strip()
        if not before:
            hm = _KA_PREFIX_NEG_HEAD_RE.match(after) if after else None
            if hm:
                head, rest = hm.group(0), after[hm.end():].strip()
                nxt = rest.split(' ', 1)[0] if rest else ''
                is_mw = bool(_MULTIWORD_DRUG_HEAD_RE.match(head))
                if nxt and (is_mw or not is_skip_fn(head)) and nxt not in follow_stop and not _DIGIT_RE.match(nxt):
                    out.append(f'{cur[:m.end()]} {head}'.strip())
                    cur = rest
                    continue
            out.append(cur)
            break
        nxt_a = after.split(' ', 1)[0] if after else ''
        if _AFFIRM_PRED_RE.search(before):
            is_pref_after = bool(nxt_a and nxt_a not in follow_stop and not _DIGIT_RE.match(nxt_a))
            left, right = (before, cur[m.start():].strip()) if is_pref_after else _split_affirm_before_stop(before, cur[m.start():].strip())
            if not is_pref_after and left == before and right == cur[m.start():].strip():
                out.append(cur)
                break
            out.append(left)
            cur = right
            continue
        if after:
            tm = _POST_STOP_TAIL_RE.match(after)
            if tm:
                dur_tail, rem = tm.group(0).strip(), after[tm.end():].strip()
                nxt_r = rem.split(' ', 1)[0] if rem else ''
                if nxt_r and nxt_r not in follow_stop and not _DIGIT_RE.match(nxt_r):
                    out.append(f'{cur[:m.end()]} {dur_tail}'.strip())
                    cur = rem
                    continue
            if nxt_a and nxt_a not in follow_stop and not _DIGIT_RE.match(nxt_a):
                out.append(cur[:m.end()].strip())
                cur = after
                continue
        out.append(cur)
        break
    return out or [sub_s]


def _split_ka_neg_clauses(sub_s):
    return _split_stop_verb_clauses(
        sub_s, _KA_NEG_VERB_RE, _KA_NEG_FOLLOW_STOP,
        lambda h: (not _DIGIT_RE.search(h)) and h.startswith(_KA_MULTIWORD_HEAD_PREFIXES),
    )


def _split_en_stopped(sub_s):
    if 'stopped' not in sub_s and 'discontinued' not in sub_s and 'held' not in sub_s:
        return [sub_s]
    return _split_stop_verb_clauses(
        sub_s, _EN_STOPPED_RE, _EN_NEG_FOLLOW_STOP,
        lambda h: h.split(' ', 1)[0] in _EN_NEG_HEAD_SKIP or h.split(' ', 1)[0].split('-', 1)[0] in _EN_NEG_HEAD_SKIP,
    )


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
    """Fast O(N) check for pure prefix- or suffix-negated conjunction chains."""
    if _ALLERGY_WORD_RE.search(chunk_s) or _DIGIT_RE.search(chunk_s):
        return False
    m_pref = _PREFIX_NEG_START_RE.search(chunk_s)
    if m_pref:
        rest = chunk_s[m_pref.end():]
        if _CONJ_SPLIT_RE.search(rest) and not _AFFIRM_PRED_RE.search(rest) and not _INLINE_NEG_SPLIT_RE.search(rest):
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


def _group_conj_parts(parts, sent_affirm):
    """Group conjunction-separated subclauses while preserving inherited affirmative-list polarity."""
    cur_buf = [parts[0]]
    cur_neg = _has_neg_phrase(parts[0])
    cur_pref_neg = cur_neg and bool(_PREFIX_NEG_START_RE.search(parts[0]))
    if len(parts) == 1 and sent_affirm and cur_neg and not cur_pref_neg and not _AFFIRM_PRED_RE.search(_KA_NEG_PARTICLE_VERB_RE.sub('', parts[0])):
        peeled = _peel_conj_affirm_stop(parts[0])
        if peeled:
            return peeled
    saw_affirm = (not cur_neg) and (sent_affirm or bool(_AFFIRM_PRED_RE.search(parts[0])))
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
                cur_aff = saw_affirm or bool(_AFFIRM_PRED_RE.search(cur_buf[0]))
            if cur_alg is None:
                cur_alg = bool(_ALLERGY_WORD_RE.search(cur_buf[0]))
            if cur_dig is None:
                cur_dig = bool(_DIGIT_RE.search(cur_buf[0]))
            nxt_dig = bool(_DIGIT_RE.search(nxt)) if cur_dig else False
            if cur_aff and not _AFFIRM_PRED_RE.search(_KA_NEG_PARTICLE_VERB_RE.sub('', nxt)):
                peeled = _peel_conj_affirm_stop(nxt)
                if peeled:
                    groups.append(' და '.join(cur_buf))
                    groups.append(peeled[0])
                    cur_buf, cur_neg, cur_pref_neg, saw_affirm = [peeled[1]], True, False, False
                    cur_alg = cur_dig = cur_aff = None
                    continue
            if not cur_aff and not cur_alg and (not cur_dig or nxt_dig):
                cur_buf.append(nxt)
                cur_neg = True
                saw_affirm = False
                continue
        groups.append(' და '.join(cur_buf))
        cur_buf = [nxt]
        cur_neg = nxt_neg
        cur_pref_neg = nxt_neg and bool(_PREFIX_NEG_START_RE.search(nxt))
        saw_affirm = False if nxt_neg else (saw_affirm or bool(_AFFIRM_PRED_RE.search(nxt)))
        cur_alg = cur_dig = cur_aff = None
    groups.append(' და '.join(cur_buf))
    return groups


def _split_neg_clauses(cleaned_txt):
    """Split text into negation-scoped clauses strictly in O(N) time (no O(N^2) string re-scans)."""
    out, seen_chunks, seen_segs = [], set(), set()
    for sent in _SENT_CLAUSE_SPLIT_RE.split(cleaned_txt):
        sent_affirm = False
        for chunk in sent.split(','):
            chunk_s = chunk.strip()
            if not chunk_s:
                continue
            key = (sent_affirm, chunk_s)
            if key in seen_chunks:
                if _has_neg_phrase(chunk_s):
                    sent_affirm = False
                elif _AFFIRM_PRED_RE.search(chunk_s):
                    sent_affirm = True
                continue
            seen_chunks.add(key)
            if not _has_neg_phrase(chunk_s):
                out.append(chunk_s)
                if _AFFIRM_PRED_RE.search(chunk_s):
                    sent_affirm = True
                continue
            if not sent_affirm and _is_uniform_neg_chain(chunk_s):
                sent_affirm = False
                continue
            parts = [p.strip() for p in _CONJ_SPLIT_RE.split(chunk_s) if p.strip()]
            if not parts:
                sent_affirm = False
                continue
            for seg in _group_conj_parts(parts, sent_affirm):
                if seg in seen_segs:
                    continue
                seen_segs.add(seg)
                out.extend(_split_inline_boundaries(seg))
            sent_affirm = False
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

"""Compiled O(N) regex and lexicon patterns for Georgian/English clinical free-text NLP."""
import re

_WS_RUN_RE = re.compile(r'[ \t\u00a0]+')
# All split regexes run AFTER _WS_RUN_RE collapses horizontal spaces to a single ' ', guaranteeing O(N) linear time.
_SENT_CLAUSE_SPLIT_RE = re.compile(
    r'[;\n!()—–]+| - |\.(?!\d)| (?:მაგრამ|ხოლო|თუმცა|but) '
)
_CLAUSE_COMMA_SPLIT_RE = re.compile(r'(?<!\d),|,(?!\d)')
_CONJ_SPLIT_RE = re.compile(r' (?:და|ან|and|or) ')
_KA_VERBS = (
    r'(?:იღებს|ვიღებ|იღებდა|სვამს|ვსვამ|სვამდა|ღებულობს|ვღებულობ|ღებულობდა|იყენებს|ვიყენებ'
    r'|იკეთებს|ვიკეთებ|აგრძელებს|ვაგრძელებ|აქვს|მაქვს|აღნიშნავს|ჰქონია|მქონია|მიმიღია|აღენიშნება|უფიქსირდება|აწუხებს)'
)
_KA_STOP_VERBS = (
    r'(?:შეწყვეტილია|მოხსნილია|შეჩერებულია|შეწყვიტა|მოხსნა|შეაჩერა|მოუხსნეს|შეუწყვიტეს|შეაჩერეს'
    r'|მოხსნეს|შეწყვიტეს|შევწყვიტე|მოვხსენი|შევაჩერე|შევწყვიტეთ|მოვხსენით|შევაჩერეთ)'
)
_PACK_NO_DOT_RE = re.compile(r'\bno\. ?(?=\d)', re.IGNORECASE)
_PACK_NO_SPC_RE = re.compile(
    r'(?<=[a-zა-ჰ0-9]) no (?=\d+(?! ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu))(?![a-zა-ჰ]))',
    re.IGNORECASE,
)
_SUBJECT_PREFIX_PAT = (
    r'(?:(?:the )?(?:patient|pt|he|she|they|i|we)(?: (?:is|are|was|were|has|have|had|does|do|did|will|would|can|could))?'
    r'|პაციენტ[ა-ჰ]*|ავადმყოფ[ა-ჰ]*|მე|ის|მას|მან|ჩვენ)'
)
_SUBJECT_ONLY_RE = re.compile(rf'^{_SUBJECT_PREFIX_PAT}$', re.IGNORECASE)
_GENERIC_SOV_PAT = (
    r'(?:ალერგი[ა-ჰ]*|წამალს|წამლებს|მედიკამენტს|მედიკამენტებს|პრეპარატს'
    r'|არაფერი|არაფერს|არაფერზე|არაფრის|არცერთი|არცერთს|არცერთზე)'
)
_SUBJ_OR_GENERIC_HEAD_RE = re.compile(
    rf'^(?:{_SUBJECT_PREFIX_PAT}(?: {_GENERIC_SOV_PAT})?|{_GENERIC_SOV_PAT})$',
    re.IGNORECASE,
)
_LEADING_KA_ALLERGY_HEAD_RE = re.compile(rf'^(?:{_SUBJECT_PREFIX_PAT} )?ალერგი[ა-ჰ]* ', re.IGNORECASE)
_KA_GENITIVE_END_RE = re.compile(r'(?:ის|ისა)$')
_PREFIX_NEG_START_RE = re.compile(
    rf'^(?:{_SUBJECT_PREFIX_PAT} )?'
    rf'(?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ|არც) {_KA_VERBS}'
    rf'|{_KA_STOP_VERBS}(?![ა-ჰ])'
    r'|უარყოფს\b|არც(?![ა-ჰ])|არასდროს(?![ა-ჰ])|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*|არაფერ[ა-ჰ]*|არცერთ[ა-ჰ]*'
    r'|(?:no|not|never|neither|nor|without|stopped|discontinued|held|withheld|paused|omitted|suspended|denied|denies|off'
    r"|doesn't|don't|didn't|hasn't|haven't|hadn't|isn't|aren't|wasn't|weren't|won't|wouldn't|can't|cannot|couldn't|shouldn't|mustn't)\b)",
    re.IGNORECASE,
)
_AFFIRM_PRED_RE = re.compile(
    rf'(?:^| )(?:{_KA_VERBS}|უწერია|დაუნიშნ[ა-ჰ]*|გადავიდა|გადაიყვან[ა-ჰ]*'
    r'|takes?|taking|uses?|using|started|continues?|continuing|switched|changed|has|have|on |allergic)\b',
    re.IGNORECASE,
)
_ALLERGY_WORD_RE = re.compile(r'(?:ალერგი[ა-ჰ]*|\ballerg(?:y|ies|ic)\b)', re.IGNORECASE)
_EN_NEG_ADV_LB = (
    r"(?<!\bnot)(?<!\bno)(?<!never)(?<!n't)(?<!longer)(?<!been)(?<!being)(?<!\bbe)"
    r'(?<!denies)(?<!denied)(?<!without)(?<!currently)(?<!regularly)(?<!actually)'
    r'(?<!actively)(?<!presently)(?<!routinely)(?<!really)(?<!truly)(?<!recently)'
    r'(?<!consistently)(?<!strictly)(?<!officially)(?<!formally)(?<!previously)'
    r'(?<!ever)(?<!yet)(?<!still)(?<!known)(?<!proven)(?<!documented)'
)
_INLINE_NEG_SPLIT_RE = re.compile(
    r'(?<!\bpatient)(?<!\bpt)(?<!\bhe)(?<!\bshe)(?<!\bthey)(?<!\bi)(?<!\bwe)'
    r'(?<!\bis)(?<!\bare)(?<!\bwas)(?<!\bwere)(?<!\bhas)(?<!\bhave)(?<!\bhad)'
    r'(?<!\bdo)(?<!\bdoes)(?<!\bdid)(?<!\bcan)(?<!\bwill)(?<!\bwould)(?<!\bshould)(?<!\bmust)'
    r'(?<!\bcurrently)(?<!\bpreviously)(?<!\bdenies)(?<!\bdenied) '
    r'(?!not (?:(?:taken|used|reported|present|known|confirmed|documented|noted|active|started|prescribed|continued|resumed|restarted|indicated|given|administered|tolerated|detected|identified|found|observed|seen|recorded|yet)\b|(?:taking|using|on|allergic)(?=$|[,;.!?\n])))'
    r'(?!no longer (?:taking|using|on|allergic)(?=$|[,;.!?\n]))'
    r'(?=(?:no|not|without) [a-zა-ჰ])'
    rf'|{_EN_NEG_ADV_LB} (?=(?:switched to|changed to|allergic to) [a-zა-ჰ])'
    rf'|{_EN_NEG_ADV_LB}(?<!stopped)(?<!discontinued)(?<!held) (?=(?:taking|takes|using|uses|started|continues|continuing) [a-zა-ჰ])'
    rf'|{_EN_NEG_ADV_LB}(?<!allergy)(?<!allergies) (?=on [a-z])'
    r'|(?<=stopped) (?=(?:started|takes|uses|switched|changed|continues|continuing|on ))'
    r'|(?<=discontinued) (?=(?:started|takes|uses|switched|changed|continues|continuing|on ))'
    r'|(?<=held) (?=(?:started|takes|uses|switched|changed|continues|continuing|on ))'
    r'|(?<=[ა-ჰa-z0-9] ალერგია)(?<!პაციენტს ალერგია)(?<!პაციენტი ალერგია)(?<!ავადმყოფს ალერგია)(?<!ავადმყოფი ალერგია)(?<!მას ალერგია)(?<!მე ალერგია) '
    r'(?!(?:არაფერ|არცერთ|არანაირ|არავითარ))(?=\S+ (?:არ|არა) (?:აქვს|მაქვს|აღენიშნება|უფიქსირდება|არის))'
    r'|(?:(?<=\d)|(?<=\dმგ)|(?<= მგ)|(?<=\dmg)|(?<= mg)|(?<=\dმკგ)|(?<= მკგ)|(?<=\dmcg)|(?<= mcg)'
    r'|(?<=\dგ)|(?<= გ)|(?<=\dg)|(?<= g)|(?<=\dმლ)|(?<= მლ)|(?<=\dml)|(?<= ml)|(?<=\dსე)|(?<= სე)|(?<=\diu)|(?<= iu)'
    r'|(?<= bid)|(?<= tid)|(?<= qid)|(?<= qd)|(?<= od)|(?<= daily)|(?<= x2)|(?<= 2x)|(?<= ტაბ)|(?<= tab)|(?<= აბი)'
    r'|(?<= დილას)|(?<= საღამოს)|(?<= ღამით)|(?<= ყოველდღე)|(?<= დღეში)|(?<= უზმოზე)'
    r'|(?<= morning)|(?<= evening)|(?<= night)|(?<= nightly)|(?<= bedtime)|(?<= once)|(?<= twice)) '
    r'(?!(?:\d\S*|მგ|mg|მკგ|mcg|გ|g|სე|u|iu|მლ|ml|ტაბ|აბი|აბ|tab|tabs|tablets?|caps?|capsules?|pills?'
    r'|x2|2x|bid|tid|qid|qd|od|qhs|qod|hs|prn|po|iv|sc|sq|im|once|twice|daily|per|day|days|d|h'
    r'|in|at|the|morning|evening|night|bedtime|am|pm|დღეში|დილას|საღამოს|ღამით|ძილის|წინ|წინა|ჯერ|დოზა'
    r'|ყოველდღე|უზმოზე|გუშინ|დღეს|ადრე|უკვე|მანამდე|საერთოდ'
    r'|არ|აღარ|არა|ვერ|არც|no|not|off|never|none'
    r'|yesterday|today|previously|already|recently|earlier|nightly|orally|weekly|before|prior|last)(?: |$))'
    r'(?=[a-zა-ჰ0-9\-]+ (?:\d+(?:[.,/]\d+)?(?: ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu))? )?'
    rf'(?:(?:ალერგი[ა-ჰ]* )?(?:არ|აღარ|არა|ვერ) {_KA_VERBS}|{_KA_STOP_VERBS}|არა(?![ა-ჰ])|არაქვს|არმაქვს|არააქვს|(?:stopped|discontinued|held|withheld|paused|omitted|suspended|off|no)\b))'
)
_KA_NEG_VERB_RE = re.compile(rf'(?<![ა-ჰ]){_KA_STOP_VERBS}(?![ა-ჰ])')
_KA_NEG_PARTICLE_VERB_RE = re.compile(rf'(?<![ა-ჰ])(?:არ|აღარ|არა|ვერ|არც|არასდროს) {_KA_VERBS}(?![ა-ჰ])')
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
    'taking', 'using', 'being', 'having', 'getting', 'receiving', 'on',
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
    r'|დღის|დღე|დღით|დღეა|კვირის|კვირა|კვირაა|თვის|თვე|თვეა|წლის|წელია|წლიდან|საათის|საათით|სთ'
    r'|days?|weeks?|wks?|months?|mos?|years?|yrs?|hrs?|hours?|d|h)'
)
_COUNT_OR_DUR_TOK = (
    r'(?:\d+(?:[.,/]\d+)? ?(?:აბი|აბ|ტაბ|tab|tabs|tablets?|caps?|capsules?|pills?|დოზა|dose)(?![a-zა-ჰ])'
    r'|(?:for )?\d+(?:[.,/]\d+)? ?(?:დღის|დღე|დღით|დღეა|კვირის|კვირა|კვირაა|თვის|თვე|თვეა|წლის|წელია|წლიდან|საათის|საათით|სთ'
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
_EXCEPT_ITEM_HEAD = rf'(?:{_DOSE_UNIT_TOK} )?(?:{_MULTIWORD_DRUG_HEAD}|[a-zა-ჰ0-9\-]+)(?: {_HEAD_MOD_TOK}){{0,6}}'
_KA_PREFIX_NEG_HEAD_RE = re.compile(rf'^{_EXCEPT_ITEM_HEAD}(?= [a-zა-ჰ])', re.IGNORECASE)
_EXCEPT_LIST_STOP = (
    r'(?:all|both|no|not|nothing|none|stopped|discontinued|held|withheld|paused|omitted|suspended|off|takes?|taking|uses?|using|continues?|continuing'
    r'|started|switched|changed|is|are|was|were|be|been|has|have|patient|pt|he|she|they|i|we'
    r'|ყველა|ორივე|არ|არა|არც|არაფერ[ა-ჰ]*|არცერთ[ა-ჰ]*|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*|მოხსნ[ა-ჰ]*|შეწყვ[ა-ჰ]*|შეჩერ[ა-ჰ]*'
    r'|იღებს|ვიღებ|სვამს|ვსვამ|აგრძელებ[ა-ჰ]*|ვაგრძელებ[ა-ჰ]*|გადავიდ[ა-ჰ]*|დაუნიშნ[ა-ჰ]*|უწერია|აქვს|მაქვს|პაციენტ[ა-ჰ]*)'
)
_EXCEPT_LIST_STOP_START_RE = re.compile(rf'^{_EXCEPT_LIST_STOP}(?![a-zა-ჰ])', re.IGNORECASE)
_KA_PRE_GARDA_SUBJ_RE = re.compile(
    r'(?:ყველა|ორივე|წამალ|წამლებ|მედიკამენტ|პრეპარატ|ანტიკოაგულ|ანტიაგრეგ|ანტითრომბ|გამათხელებ|ალერგი|სხვა)'
)
_FRONTED_EXCEPT_HEAD_RE = re.compile(
    rf'^{_EXCEPT_ITEM_HEAD}'
    rf'(?:(?:, | (?:and|or|და|ან) )(?!{_EXCEPT_LIST_STOP}(?![a-zა-ჰ])){_EXCEPT_ITEM_HEAD}){{0,6}}(?=$|[ ,;.!?\n])',
    re.IGNORECASE,
)
_EN_BUT_NOT_RE = re.compile(r' but not (?=[a-zა-ჰ])', re.IGNORECASE)
_EN_EXCEPT_RE = re.compile(r'(?:^|(?<=[\s;,.\n!?]))except (?:for )?(?=[a-zა-ჰ0-9])', re.IGNORECASE)
_EN_STOP_WORD_RE = re.compile(r'\b(?:stopped|discontinued|held|withheld|paused|omitted|suspended|off)\b', re.IGNORECASE)
_KA_BUT_NOT_RE = re.compile(r' მაგრამ არა (?=[a-zა-ჰ])')
_KA_BUT_SOV_NOT_RE = re.compile(
    r' მაგრამ ([a-zა-ჰ0-9 .\-]{1,40}?) არა(?=$|[,;.!?\n(\[—–]| [(\[—–-])'
)
_KA_GARDA_RE = re.compile(r'(?:^|(?<=[\s;,.\n!?]))გარდა(?=$|[,;.!?\n ])')
_EN_STOPPED_RE = re.compile(r'\b(?:stopped|discontinued|held|withheld|paused|omitted|suspended)\b', re.IGNORECASE)
_DOSE_UNIT_END_RE = re.compile(r'(?:\d|მგ|mg|მკგ|mcg|გ|g)$', re.IGNORECASE)
_KA_AFFIRM_VERB_AFTER_RE = re.compile(
    rf'(?<![ა-ჰ]){_KA_VERBS} (?!(?:და|ან|მაგრამ|ხოლო|თუმცა|რომ)(?![ა-ჰ]))(?=[a-zა-ჰ0-9])'
)
_KA_VERB_WORD_RE = re.compile(rf'(?<![ა-ჰ]){_KA_VERBS}(?![ა-ჰ])')
_KA_NEG_PARTICLES = frozenset({'არ', 'აღარ', 'არა', 'ვერ', 'არც', 'არასდროს'})
_EN_STOP_FORMS = r'(?:stop|stopped|stopping|discontinue|discontinued|discontinuing|hold|held|holding|withhold|withheld|pause|paused|omit|omitted|suspend|suspended)'
_EN_DNEG_AUX = r'(?:(?:to|be|been|being) ){0,2}(?:(?:safely|abruptly|suddenly|completely|routinely|fully|prematurely|unnecessarily|easily|ever|yet) )?'
_DOUBLE_NEG_AFFIRM_EN_RE = re.compile(
    rf"\b(?:(?:(?:do|does|did|is|are|has|have|had|was|were|can|could|will|would|should|must) )?not (?:yet )?{_EN_DNEG_AUX}{_EN_STOP_FORMS}"
    rf"|(?:don't|doesn't|didn't|hasn't|haven't|hadn't|isn't|aren't|wasn't|weren't|can't|cannot|couldn't|won't|wouldn't|shouldn't|mustn't) (?:yet )?{_EN_DNEG_AUX}{_EN_STOP_FORMS}"
    rf"|never {_EN_DNEG_AUX}{_EN_STOP_FORMS}"
    r"|(?:(?:is|are|was|were) )?not off|(?:isn't|aren't|wasn't|weren't|never) off)\b",
    re.IGNORECASE,
)
_KA_STOP_PART = r'(?:შეწყვეტილი|მოხსნილი|შეჩერებული)'
_DOUBLE_NEG_AFFIRM_KA_RE = re.compile(
    r'(?:არავითარ შემთხვევაში |არანაირ შემთხვევაში |თვითნებურად )?(?:არ|არც|აღარ|ვერ) (?:შეწყვიტოთ|შეწყვიტოს|შეწყვიტო|მოხსნათ|მოხსნას|შეაჩეროთ|შეაჩეროს|შეწყვეტილია|მოხსნილია|შეჩერებულია)'
    rf'|(?:არაა|არა არის|არა აქვს|არა მაქვს|არაქვს|არმაქვს|არააქვს) {_KA_STOP_PART}'
    rf'|{_KA_STOP_PART} (?:არაა|არაქვს|არმაქვს|არააქვს|(?:არ|არც|ვერ|არა) (?:არის|აქვს|მაქვს|გვაქვს|ჰქონია))'
    r'|(?:არ|არც|ვერ) (?:შეუწყვეტია|მოუხსნია|შეუჩერებია|შემიწყვეტია|მომიხსნია|შემიჩერებია'
    r'|შეგვიწყვეტია|მოგვიხსნია|შეგვიჩერებია|შეწყვიტა|მოხსნა|შეაჩერა|შევწყვიტე|მოვხსენი|შევაჩერე'
    r'|შევწყვიტეთ|მოვხსენით|შევაჩერეთ|მოუხსნეს|შეუწყვიტეს|შეაჩერეს|მოხსნეს|შეწყვიტეს'
    rf'|(?:არის|აქვს|მაქვს|გვაქვს|ჰქონია) {_KA_STOP_PART})',
)
_PAREN_DOSE_UNWRAP_RE = re.compile(
    r'\(\s*(\d+(?:[.,/]\d+)? ?(?:მგ|mg|მკგ|mcg|გ|g|მლ|ml|სე|iu|ტაბ|აბი|აბ|tab|tabs|caps?|x\d+|\d+x|bid|tid|qid|qd|od|daily)?)\s*\)',
    re.IGNORECASE,
)
_PURE_NEG_ANNOT = (
    rf'(?:(?:ალერგი[ა-ჰ]* )?(?:არაა?|არაქვს|არმაქვს|არააქვს|უარყოფს|გამორიცხულია'
    rf'|(?:არ|აღარ|არა|ვერ) (?:{_KA_VERBS}|არის)'
    rf'|{_KA_STOP_VERBS}(?![ა-ჰ]))'
    r'|(?:no longer (?:taking|using|on|allergic)|no|none|nil|negative|absent|denied|denies|ruled out'
    r'|stopped|discontinued|held|withheld|paused|omitted|suspended|off'
    r'|not (?:taken|used|taking|using|on|active|reported|present|known|confirmed|allergic)'
    r'|allerg(?:y|ies|ic) (?:not (?:present|reported|known|confirmed)|denied|negative|absent|ruled out))\b)'
    rf'(?: (?:{_COUNT_OR_DUR_TOK}|{_TEMPORAL_ADV_TOK})){{0,3}}'
)
_PAREN_NEG_UNWRAP_RE = re.compile(rf'\s*\(\s*({_PURE_NEG_ANNOT})\s*\)', re.IGNORECASE)
_DASH_NEG_UNWRAP_RE = re.compile(rf'(?: ?[—–] ?| - |: )({_PURE_NEG_ANNOT})(?=$|[,;.!?\n])', re.IGNORECASE)
_DASH_DUR_TAIL_UNWRAP_RE = re.compile(
    rf'(?: ?[—–] ?| - |: )((?:{_KA_STOP_VERBS}(?![ა-ჰ])|(?:stopped|discontinued|held|withheld|paused|omitted|suspended|off)\b)(?: (?:{_COUNT_OR_DUR_TOK}|{_TEMPORAL_ADV_TOK})){{1,3}})(?= )',
    re.IGNORECASE,
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
    rf'(?:(?:არ|აღარ|არა|ვერ|არც) (?:{_KA_VERBS}|არის)'
    r'|(?<![ა-ჰ])(?:არაა?(?![ა-ჰ\-—–])|არაქვს(?![ა-ჰ])|არმაქვს(?![ა-ჰ])|არააქვს(?![ა-ჰ])|არც(?![ა-ჰ])|არასდროს(?![ა-ჰ])|არანაირ[ა-ჰ]*|არავითარ[ა-ჰ]*|არაფერ[ა-ჰ]*|არაფრის(?![ა-ჰ])|არცერთ[ა-ჰ]*)'
    rf'|უარყოფს|{_KA_STOP_VERBS}|გამორიცხულია'
    r'|(?<![ა-ჰ])გარეშე(?![ა-ჰ])'
    r"|(?<![a-z-])off(?![a-z-])"
    r"|(?<![a-z])(?:no|not|none|nothing|nil|negative|absent|nkda|nka|denied|denies|neither|nor|ruled out|stopped|discontinued|held|withheld|paused|omitted|suspended|without|never|cannot|doesn't|don't|isn't|aren't|didn't|hasn't|haven't|hadn't|wasn't|weren't|won't|wouldn't|can't|couldn't|shouldn't|mustn't)\b)",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r'\d')
_COMPACT_STRIP_RE = re.compile(r'[\s.,;:!?()\[\]"\'—–-]+')
_NEG_FAST_HINT_RE = re.compile(
    r"\b(?:არ|აღარ|არა|ვერ|არც|არასდროს|მოხსნ|მოვხსნ|მოვხსენ|შეწყვ|შევწყვ|შეჩერ|შეაჩერ|შევაჩერ|გამორ|უარყ|გარეშე"
    r"|no|not|none|nothing|nil|negative|absent|nkda|nka|denied|denies|neither|nor|ruled|stopped|discontinued|held|withheld|paused|omitted|suspended|off|without|never|cannot|doesn't|don't|isn't|aren't|didn't|hasn't|haven't|hadn't|wasn't|weren't|won't|wouldn't|can't|couldn't|shouldn't|mustn't)\b"
    r'|\b(?:არა|არაქვს|არმაქვს|არააქვს|არც|არასდროს|არანაირ|არავითარ|არაფერ|არაფრის|არცერთ|უარყოფს|მოხსნ|მოვხსნ|მოვხსენ|შეწყვ|შევწყვ|შეჩერ|შეაჩერ|შევაჩერ|მოუხსნ|შეუწყვ|შემიწყვ|მომიხსნ|შემიჩერ|შეგვიწყვ|მოგვიხსნ|შეგვიჩერ|გამორიცხ)',
    re.IGNORECASE,
)
_GENERIC_SOV_HEADS = frozenset({
    'ალერგია', 'ალერგიას', 'ალერგიები', 'წამალს', 'წამლებს', 'მედიკამენტს', 'მედიკამენტებს', 'პრეპარატს',
    'არაფერი', 'არაფერს', 'არაფერზე', 'არაფრის', 'არცერთი', 'არცერთს', 'არცერთზე',
})
_DOSE_FREQ_WORDS = frozenset({
    'მგ', 'mg', 'მკგ', 'mcg', 'გ', 'g', 'მლ', 'ml', 'სე', 'iu', 'u', 'd', 'h',
    'ტაბ', 'აბი', 'აბ', 'tab', 'tabs', 'tablet', 'tablets', 'cap', 'caps', 'capsule', 'capsules', 'pill', 'pills',
    'daily', 'once', 'twice', 'bid', 'tid', 'qid', 'qd', 'od', 'qhs', 'qod', 'hs', 'prn', 'x2', '2x',
    'po', 'iv', 'sc', 'sq', 'im', 'in', 'at', 'the', 'per', 'for', 'ago', 'day', 'days', 'week', 'weeks', 'wk', 'wks',
    'month', 'months', 'mo', 'mos', 'year', 'years', 'yr', 'yrs', 'hour', 'hours', 'hr', 'hrs',
    'morning', 'evening', 'night', 'bedtime', 'am', 'pm',
    'დღე', 'დღის', 'დღით', 'დღეა', 'დღეები', 'კვირა', 'კვირის', 'კვირით', 'თვე', 'თვის', 'წელი', 'წლის', 'საათი', 'საათის', 'სთ',
    'დღეში', 'დილას', 'საღამოს', 'ღამით', 'ძილის', 'წინ', 'წინა', 'ჯერ', 'დოზა',
    'ყოველდღე', 'უზმოზე', 'გუშინ', 'დღეს', 'ადრე', 'უკვე', 'მანამდე', 'საერთოდ',
    'yesterday', 'today', 'previously', 'already', 'recently', 'earlier', 'nightly', 'orally', 'weekly', 'before', 'prior', 'last',
})
_MULTIWORD_DRUG_PREV_MODS = frozenset({'low', 'molecular', 'weight', 'unfractionated', 'acetylsalicylic', 'დაბალმოლეკულური', 'არაფრაქციული', 'წონის'})


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


def _rfind_clause_comma(text):
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ',' and not (0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()):
            return i
    return -1


def _find_clause_comma(text):
    n = len(text)
    for i, ch in enumerate(text):
        if ch == ',' and not (0 < i < n - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()):
            return i
    return -1


def _prev_clause(s, pos, window=120):
    end = pos - 1 if pos > 0 and s[pos - 1] == ',' else pos
    chunk = s[max(0, end - window):end]
    idx = _rfind_sent_sep(chunk)
    if idx != -1:
        chunk = chunk[idx + 1:]
    c_idx = _rfind_clause_comma(chunk)
    return chunk[c_idx + 1:] if c_idx != -1 else chunk


def _prev_clause_info(s, pos, window=140):
    chunk = s[max(0, pos - window):pos]
    idx = _rfind_sent_sep(chunk)
    if idx != -1:
        chunk = chunk[idx + 1:]
    c_idx = _rfind_clause_comma(chunk)
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



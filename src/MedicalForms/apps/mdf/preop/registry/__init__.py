"""Central preoperative clinical rule registry (Track A1).

Every clinical threshold, risk index, drug-hold interval, STOP contraindication,
alert, and autonomous nurse reflex order has one canonical entry here with its
primary source, class/level of recommendation, value, verification date, and
status (`source-verified` or `local-decision`).
"""
from .rules_labs import LABS_RULES
from .rules_meds import MEDS_RULES
from .rules_scores import SCORES_AND_WORKFLOW_RULES
from .rules_vascular import VASCULAR_RULES
from .schema import (
    CHANGELOG,
    DOMAIN_LABELS,
    KIND_LABELS,
    REGISTRY_UPDATED_ON,
    REGISTRY_VERSION,
    STATUS_LABELS,
    STATUS_LOCAL,
    STATUS_VERIFIED,
    RuleEntry,
)

RULES = VASCULAR_RULES + LABS_RULES + MEDS_RULES + SCORES_AND_WORKFLOW_RULES


def _build_index(rules):
    index = {}
    for entry in rules:
        if entry.id in index:
            raise ValueError(f'Duplicate clinical rule id in registry: {entry.id}')
        index[entry.id] = entry
    return index


RULES_BY_ID = _build_index(RULES)

REFLEX_KEY_TO_RULE = {
    'aaa_abi': 'AAA_POPLITEAL_SCREEN',
    'cea_antiplatelet': 'CEA_ANTIPLATELET_STATIN_MANDATORY',
    'abf_gsv': 'ABF_ANATOMY_AND_INFECTION',
    'alt_vein': 'PAD_GSV_MIN_MM',
    'tbi': 'PAD_CLTI_HEMO_THRESHOLDS',
    'anemia': 'LAB_HB_PBM_THRESHOLDS',
    'pbm_iron': 'LAB_HB_PBM_THRESHOLDS',
    'nephro': 'REN_CKD_NEPHRO_HYDRATION',
    'hydration': 'REN_CKD_NEPHRO_HYDRATION',
    'hba1c': 'GLY_GLUCOSE_HBA1C',
    'endo': 'GLY_GLUCOSE_HBA1C',
    'tsh': 'END_THYROID_TSH',
    'uti': 'INF_UTI_VS_ASB',
    'dental': 'INF_DENTAL_DECOLONIZATION',
    'decol': 'INF_DENTAL_DECOLONIZATION',
    'stale_labs': 'INF_STALE_LABS_DAYS',
    'ppi': 'GI_CAROTID_CONCOMITANT_AND_PPI',
    'contrast_premed': 'ALLERGY_HIT_ABX_PROPHYLAXIS',
    'cied': 'ANES_CIED_MAGNET_SAFETY',
}


def rule_value(rule_id):
    """Return the canonical threshold/value configured in the rule registry."""
    return RULES_BY_ID[rule_id].value


def rule_meta(rule_id):
    """Return a template-friendly dictionary of metadata for `rule_id`."""
    return RULES_BY_ID[rule_id].to_meta()


def match_rule_for_text(text, domain_hint=None):
    """Deterministically link an emitted alert/med/DoS message to its canonical RuleEntry.

    Prefers the longest matching pattern so specific safety rules (e.g. mechanical valve
    DOAC prohibition, HIT, OAC+ticagrelor conflict) are never shadowed by shorter generic
    patterns (e.g. ``INR ``, ``თრომბოციტოპენია``, ``ვარფარინი``).
    """
    if not text:
        return None
    haystack = str(text)
    best_entry = None
    best_key = None
    for idx, entry in enumerate(RULES):
        max_len = 0
        for pat in entry.match_patterns:
            if pat in haystack and len(pat) > max_len:
                max_len = len(pat)
        if max_len:
            key = (max_len, 1 if (domain_hint and entry.domain == domain_hint) else 0, -idx)
            if best_key is None or key > best_key:
                best_key = key
                best_entry = entry
    return best_entry


def match_rule_for_reflex(reflex_key, fallback_text=''):
    """Resolve the canonical RuleEntry for a standing nurse reflex order."""
    rid = REFLEX_KEY_TO_RULE.get(reflex_key)
    if rid and rid in RULES_BY_ID:
        return RULES_BY_ID[rid]
    return match_rule_for_text(fallback_text)


def annotate_texts(messages, domain_hint=None):
    """Wrap plain alert strings with optional canonical rule metadata for UI citation popovers."""
    out = []
    for msg in messages or ():
        entry = match_rule_for_text(msg, domain_hint=domain_hint)
        out.append({
            'text': msg,
            'rule': entry.to_meta() if entry else None,
        })
    return out


def annotate_reflex_orders(orders):
    """Attach canonical rule metadata to each reflex order copy without mutating the input dicts."""
    out = []
    for ro in orders or ():
        entry = match_rule_for_reflex(ro.get('key'), f"{ro.get('trigger', '')} {ro.get('action', '')}")
        item = dict(ro)
        item['rule'] = entry.to_meta() if entry else None
        out.append(item)
    return out


def annotate_med_rows(rows):
    """Attach canonical rule metadata to each medication schedule row copy."""
    out = []
    for row in rows or ():
        entry = match_rule_for_text(f"{row.get('drug', '')} {row.get('rule', '')}", domain_hint='meds')
        item = dict(row)
        item['rule_meta'] = entry.to_meta() if entry else None
        out.append(item)
    return out


def list_rules(domain=None, status=None, query=''):
    """Filter registry entries by domain, verification status, or free-text search."""
    q_low = (query or '').strip().lower()
    out = []
    for entry in RULES:
        if domain and domain != 'all' and entry.domain != domain:
            continue
        if status and status != 'all' and entry.status != status:
            continue
        if q_low:
            blob = ' '.join((
                entry.id, entry.title_ka, entry.description_ka,
                entry.source, entry.section, entry.source_wording, entry.value_label,
            )).lower()
            if q_low not in blob:
                continue
        out.append(entry)
    return out


def registry_summary():
    """Return high-level counts and version info for the clinical rule registry."""
    verified = sum(1 for r in RULES if r.status == STATUS_VERIFIED)
    local = sum(1 for r in RULES if r.status == STATUS_LOCAL)
    return {
        'version': REGISTRY_VERSION,
        'updated_on': REGISTRY_UPDATED_ON,
        'total': len(RULES),
        'verified_count': verified,
        'local_count': local,
    }


__all__ = [
    'CHANGELOG',
    'DOMAIN_LABELS',
    'KIND_LABELS',
    'REFLEX_KEY_TO_RULE',
    'REGISTRY_UPDATED_ON',
    'REGISTRY_VERSION',
    'RULES',
    'RULES_BY_ID',
    'RuleEntry',
    'STATUS_LABELS',
    'STATUS_LOCAL',
    'STATUS_VERIFIED',
    'annotate_med_rows',
    'annotate_reflex_orders',
    'annotate_texts',
    'list_rules',
    'match_rule_for_reflex',
    'match_rule_for_text',
    'registry_summary',
    'rule_meta',
    'rule_value',
]

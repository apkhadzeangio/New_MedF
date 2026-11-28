"""O(N) negation-scoped clause normalization and splitting for Georgian/English clinical free-text."""
from .meds_freetext_nlp_patterns import (  # noqa: F401
    _AFFIRM_PRED_RE,
    _ALLERGY_NEGATION_WORDS,
    _ALLERGY_WORD_RE,
    _BARE_NUM_TOK,
    _CLAUSE_COMMA_SPLIT_RE,
    _COMPACT_STRIP_RE,
    _CONJ_SPLIT_RE,
    _COUNT_DUR_UNIT,
    _COUNT_OR_DUR_TOK,
    _DASH_NEG_UNWRAP_RE,
    _DIGIT_RE,
    _DOSE_FREQ_WORDS,
    _DOSE_UNIT_END_RE,
    _DOSE_UNIT_TOK,
    _DOUBLE_NEG_AFFIRM_EN_RE,
    _DOUBLE_NEG_AFFIRM_KA_RE,
    _EN_BUT_NOT_RE,
    _EN_EXCEPT_RE,
    _EN_NEG_FOLLOW_STOP,
    _EN_NEG_HEAD_SKIP,
    _EN_STOP_WORD_RE,
    _EN_STOPPED_RE,
    _EXCEPT_ITEM_HEAD,
    _EXCEPT_LIST_STOP,
    _EXCEPT_LIST_STOP_START_RE,
    _FREQ_FORM_TOK,
    _FRONTED_EXCEPT_HEAD_RE,
    _GENERIC_SOV_HEADS,
    _HEAD_MOD_TOK,
    _INLINE_NEG_SPLIT_RE,
    _KA_AFFIRM_VERB_AFTER_RE,
    _KA_BUT_NOT_RE,
    _KA_BUT_SOV_NOT_RE,
    _KA_GARDA_RE,
    _KA_MULTIWORD_HEAD_PREFIXES,
    _KA_NEG_FOLLOW_STOP,
    _KA_NEG_PARTICLE_VERB_RE,
    _KA_NEG_PARTICLES,
    _KA_NEG_VERB_RE,
    _KA_PRE_GARDA_SUBJ_RE,
    _KA_PREFIX_NEG_HEAD_RE,
    _KA_STOP_PART,
    _KA_STOP_VERBS,
    _KA_VERB_WORD_RE,
    _KA_VERBS,
    _MULTIWORD_DRUG_HEAD,
    _MULTIWORD_DRUG_HEAD_RE,
    _MULTIWORD_DRUG_PREV_MODS,
    _NEG_FAST_HINT_RE,
    _NEGATION_PHRASE_RE,
    _NON_PREFIX_PROTECT_RE,
    _PAREN_DOSE_UNWRAP_RE,
    _PAREN_NEG_UNWRAP_RE,
    _POST_STOP_TAIL_RE,
    _PREFIX_NEG_START_RE,
    _SENT_CLAUSE_SPLIT_RE,
    _SUBJECT_ONLY_RE,
    _SYMPTOM_NEG_RE,
    _TEMPORAL_ADV_TOK,
    _UNINTERRUPTED_AFFIRM_RE,
    _WS_RUN_RE,
)


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


def _negate_except_items(obj, neg_prefix):
    items = []
    for sub in _CLAUSE_COMMA_SPLIT_RE.split(obj):
        for part in _CONJ_SPLIT_RE.split(sub.strip()):
            p = part.strip()
            if p:
                items.append(f'{neg_prefix} {p}')
    return '; '.join(items) + ';' if items else ''


def _rewrite_fronted_except(low, m_start, seg_start, is_en):
    seg = _next_sent_chunk(low, seg_start)
    lead_ws = len(seg) - len(seg.lstrip(' ,'))
    seg_s = seg[lead_ws:]
    has_pred = _has_neg_phrase(seg_s) or bool(_AFFIRM_PRED_RE.search(seg_s)) or (is_en and bool(_EN_STOP_WORD_RE.search(seg_s)))
    if not has_pred:
        obj, tail, end_pos = seg_s.strip(), '', seg_start + lead_ws + len(seg_s)
    else:
        hm = _FRONTED_EXCEPT_HEAD_RE.match(seg_s)
        obj_len = hm.end() if hm else len(seg_s.split(' ', 1)[0])
        obj = seg_s[:obj_len].strip()
        skip_c = 1 if seg_s[obj_len:obj_len + 1] == ',' else 0
        tail = seg_s[obj_len + skip_c:]
        end_pos = seg_start + lead_ws + obj_len + skip_c
        if not (_has_neg_phrase(tail) or _AFFIRM_PRED_RE.search(tail) or (is_en and _EN_STOP_WORD_RE.search(tail))):
            obj, tail, end_pos = seg_s.strip(), '', seg_start + lead_ws + len(seg_s)
    gov_ctx = tail if tail.strip() else _prev_sentence(low, m_start)
    gov_neg = _has_neg_phrase(gov_ctx) or (is_en and bool(_EN_STOP_WORD_RE.search(gov_ctx)))
    repl = f'{obj};' if gov_neg else _negate_except_items(obj, 'not' if is_en else 'არ იღებს')
    return repl, end_pos


def _rewrite_inline_except_neg(low, seg_start, neg_prefix):
    seg = _next_sent_chunk(low, seg_start)
    lead_ws = len(seg) - len(seg.lstrip(' '))
    seg_s = seg[lead_ws:]
    has_pred = _has_neg_phrase(seg_s) or bool(_AFFIRM_PRED_RE.search(seg_s)) or (neg_prefix == 'not' and bool(_EN_STOP_WORD_RE.search(seg_s)))
    if not has_pred:
        return '; ' + _negate_except_items(seg_s.strip(), neg_prefix), seg_start + lead_ws + len(seg_s)
    hm = _FRONTED_EXCEPT_HEAD_RE.match(seg_s)
    if hm:
        return '; ' + _negate_except_items(seg_s[:hm.end()].strip(), neg_prefix), seg_start + lead_ws + hm.end()
    return f'; {neg_prefix} ', seg_start


def _try_mid_sent_passive_except(low, seg_start, is_en):
    """Match '<except_kw> <obj>[,] <negated_predicate>' inside a mid-sentence passive clause in O(N)."""
    seg = _next_sent_chunk(low, seg_start)
    lead_ws = len(seg) - len(seg.lstrip(' '))
    seg_s = seg[lead_ws:]
    if not seg_s or _EXCEPT_LIST_STOP_START_RE.match(seg_s):
        return None
    hm = _FRONTED_EXCEPT_HEAD_RE.match(seg_s)
    if not hm:
        return None
    obj = seg_s[:hm.end()].strip()
    skip_c = 1 if seg_s[hm.end():hm.end() + 1] == ',' else 0
    tail_full = seg_s[hm.end() + skip_c:]
    tc = _find_clause_comma(tail_full)
    tail_pred = tail_full[:tc] if tc != -1 else tail_full
    if not tail_pred.strip():
        return None
    is_neg = _has_neg_phrase(tail_pred) or (is_en and bool(_EN_STOP_WORD_RE.search(tail_pred)))
    if not is_neg:
        return None
    end_pos = seg_start + lead_ws + hm.end() + skip_c + len(tail_pred)
    return obj, tail_pred.strip(), end_pos


def _rewrite_en_except(low):
    """Rewrite fronted, fragment, passive mid-sentence, and inline English 'except [for]' clauses in O(N)."""
    out, last = [], 0
    for m in _EN_EXCEPT_RE.finditer(low):
        if m.start() < last:
            continue
        prev_raw, before_comma = _prev_clause_info(low, m.start())
        prev_s = prev_raw.strip()
        if not prev_s and not before_comma.strip():
            repl, end_pos = _rewrite_fronted_except(low, m.start(), m.end(), True)
            out.append(low[last:m.start()])
            out.append(repl)
            last = end_pos
            continue
        gov = prev_s or before_comma
        gov_neg = _has_neg_phrase(gov) or bool(_EN_STOP_WORD_RE.search(gov))
        if gov_neg:
            out.append(low[last:m.start()])
            out.append('; ')
            last = m.end()
            continue
        if not _AFFIRM_PRED_RE.search(gov):
            passive = _try_mid_sent_passive_except(low, m.end(), True)
            if passive:
                obj, tail_pred, end_pos = passive
                out.append(low[last:m.start()].rstrip(' ,'))
                out.append(f' {tail_pred}; {obj};')
                last = end_pos
                continue
        out.append(low[last:m.start()])
        repl, end_pos = _rewrite_inline_except_neg(low, m.end(), 'not')
        out.append(repl)
        last = end_pos
    out.append(low[last:])
    return ''.join(out)


def _rewrite_ka_garda(low):
    """Rewrite pre/post/fronted/passive/fragment Georgian 'გარდა' exception clauses (with or without commas) in O(N)."""
    out, last = [], 0
    for m in _KA_GARDA_RE.finditer(low):
        if m.start() < last:
            continue
        prev_raw, before_comma = _prev_clause_info(low, m.start())
        prev = prev_raw.strip()
        nxt_ch = low[m.end():m.end() + 1]
        skip_comma = 1 if nxt_ch == ',' else 0
        after_sent = _next_sent_chunk(low, m.end() + skip_comma)
        ac_idx = _find_clause_comma(after_sent)
        after_c = after_sent[:ac_idx] if ac_idx != -1 else after_sent
        if not prev and not before_comma.strip():
            repl, end_pos = _rewrite_fronted_except(low, m.start(), m.end(), False)
            out.append(low[last:m.start()])
            out.append(repl)
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
            cut = low.rfind(obj, last, m.start()) if obj else -1
            if obj and cut >= last:
                out.append(low[last:cut].rstrip())
                out.append(f'; {obj}')
                last = m.end() + skip_comma
                continue
            out.append(low[last:m.start()])
            out.append(';')
            last = m.end() + skip_comma
        elif not at_end and not bc_neg and not _AFFIRM_PRED_RE.search(prev or before_comma) and (not prev or bool(_KA_PRE_GARDA_SUBJ_RE.search(prev))):
            passive = _try_mid_sent_passive_except(low, m.end(), False)
            if passive:
                obj, tail_pred, end_pos = passive
                out.append(low[last:m.start()].rstrip(' ,'))
                out.append(f' {tail_pred}; {obj};')
                last = end_pos
                continue
            if after_neg or prev_sent_neg:
                out.append(low[last:m.start()])
                out.append(';')
                last = m.end() + skip_comma
            else:
                repl, end_pos = _rewrite_inline_except_neg(low, m.end(), 'არ იღებს')
                out.append(low[last:m.start()])
                out.append(repl)
                last = end_pos
        elif bc_neg or after_neg or prev_sent_neg:
            out.append(low[last:m.start()])
            out.append(';')
            last = m.end() + skip_comma
        elif not at_end and ((not prev and before_comma.strip()) or _AFFIRM_PRED_RE.search(prev)):
            repl, end_pos = _rewrite_inline_except_neg(low, m.end(), 'არ იღებს')
            out.append(low[last:m.start()])
            out.append(repl)
            last = end_pos
        elif prev and (before_comma.strip() or not _AFFIRM_PRED_RE.search(prev)):
            cut = low.rfind(prev, last, m.start())
            if cut >= last:
                out.append(low[last:cut].rstrip())
                out.append('; ' + _negate_except_items(prev, 'არ იღებს'))
                last = m.end() + skip_comma
        else:
            ptoks = prev.split(' ') if prev else []
            ci = len(ptoks) - 1
            while ci > 0 and (_DIGIT_RE.search(ptoks[ci]) or ptoks[ci] in _DOSE_FREQ_WORDS):
                ci -= 1
            while ci > 0 and ptoks[ci - 1] in _MULTIWORD_DRUG_PREV_MODS:
                ci -= 1
            tok = ' '.join(ptoks[ci:]) if ptoks else ''
            cut = low.rfind(tok, last, m.start()) if tok else -1
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
    if '(' in low:
        src = low
        low = _PAREN_DOSE_UNWRAP_RE.sub(r' \1 ', src)
        changed = changed or (low != src)
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
    if 'not ' in low or 'never ' in low or "n't " in low or 'cannot ' in low:
        low, changed = _DOUBLE_NEG_AFFIRM_EN_RE.sub(' continues ', low), True
    if 'არ ' in low or 'არა ' in low or 'არაა' in low or 'არც ' in low or 'ვერ ' in low or 'არაქვს' in low or 'არმაქვს' in low:
        low, changed = _DOUBLE_NEG_AFFIRM_KA_RE.sub(' უწერია ', low), True
    if '(' in low:
        src = low
        low = _PAREN_NEG_UNWRAP_RE.sub(r' \1 ', src)
        changed = changed or (low != src)
    if '-' in low or '—' in low or '–' in low:
        src = low
        low = _DASH_NEG_UNWRAP_RE.sub(r' \1', src)
        changed = changed or (low != src)
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
        min_idx = 0 if is_neg_then_pos else 1
        while cut_idx > min_idx and (_DIGIT_RE.search(b_toks[cut_idx]) or b_toks[cut_idx] in _DOSE_FREQ_WORDS):
            cut_idx -= 1
        while cut_idx > min_idx and b_toks[cut_idx - 1] in _MULTIWORD_DRUG_PREV_MODS:
            cut_idx -= 1
        if cut_idx == 0 and is_neg_then_pos:
            left, right = piece[:m0.end()].strip(), piece[m0.end():].strip()
        else:
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
        if not before or _SUBJECT_ONLY_RE.match(before):
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
                    if not before or _SUBJECT_ONLY_RE.match(before):
                        continue
                    prev_tok = before.rsplit(' ', 1)[-1] if before else ''
                    if prev_tok in _KA_NEG_PARTICLES:
                        before_neg = before[: -len(prev_tok)].strip()
                        after_tok = sub_s[m.end() - 1:].strip().split(' ', 1)[0]
                        if not before_neg or before_neg in _GENERIC_SOV_HEADS or _SUBJECT_ONLY_RE.match(before_neg) or after_tok in _GENERIC_SOV_HEADS:
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
        for chunk in _CLAUSE_COMMA_SPLIT_RE.split(sent):
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

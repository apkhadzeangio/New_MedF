"""Corkscrew collateral arcade generator bridging occluded arterial segment chains."""
import math

from .geometry import _PRECOMPUTED_SAMPLES, VESSEL_GEOMETRY


def _build_collaterals(norm_segs, segment_next):
    """Build corkscrew collateral arcades bridging occluded segment chains."""
    occluded = {k for k, v in norm_segs.items() if v.get('stenosis') == 'occlusion'}
    if not occluded:
        return []

    parents = {}
    for p, children in segment_next.items():
        for c in children:
            parents.setdefault(c, []).append(p)

    collaterals = []
    visited_chains = set()
    for key, *_ in VESSEL_GEOMETRY:
        if key not in occluded or key in visited_chains:
            continue
        if any(p in occluded for p in parents.get(key, ())):
            continue
        chain = [key]
        visited_chains.add(key)
        cur = key
        while segment_next.get(cur):
            nxt_occ = next((c for c in segment_next[cur] if c in occluded), None)
            if not nxt_occ:
                break
            chain.append(nxt_occ)
            visited_chains.add(nxt_occ)
            cur = nxt_occ

        last_seg = norm_segs.get(chain[-1], {})
        if last_seg.get('reconstitution') == 'none' and not segment_next.get(chain[-1]):
            continue

        # Profunda femoris serves as the anatomical collateral origin for proximal SFA CTO
        if chain[0] == 'sfa1' and 'pfa' not in occluded:
            start_smp = _PRECOMPUTED_SAMPLES['pfa'][14]
        else:
            start_smp = _PRECOMPUTED_SAMPLES[chain[0]][0]
        end_smp = _PRECOMPUTED_SAMPLES[chain[-1]][-1]
        x0, y0 = start_smp[0], start_smp[1]
        x1, y1 = end_smp[0], end_smp[1]
        bow_sign = 1.0 if chain[0].startswith('pt') or chain[0] == 'iia' else -1.0
        span = max(45.0, abs(y1 - y0))
        bow = bow_sign * min(44.0, max(22.0, span * 0.16))

        waves = []
        steps = 18
        for i in range(steps + 1):
            u = i / steps
            bx = x0 + (x1 - x0) * u + bow * math.sin(math.pi * u)
            by = y0 + (y1 - y0) * u
            wiggle = math.sin(u * math.pi * 6.0) * 5.5 * math.sin(math.pi * u)
            waves.append(f'{bx + wiggle:.1f},{by:.1f}')
        collaterals.append({
            'd': 'M' + ' L'.join(waves),
            'rx': round(x1, 1),
            'ry': round(y1, 1),
        })
    return collaterals

"""Doppler reports (Hadeco Smart-XT "Doppler Arterial Testing Report") -> data.

The device saves one page as a picture inside a PDF (806 x 1140 px) with a
fixed layout: 6 graphs per leg (femoral ... great toe), the pressure table
and ABI/TBI. Here the picture is read back:

- the red curve of every graph -> points (velocity in cm/s, or mV/V for the
  toe PPG), the scale from the y-axis labels and the zero line;
- numbers (heart rate, peak velocity, pressures, ABI/TBI) by comparing each
  character with the device's own characters (doppler_glyphs.py, made from
  sample reports; the text may sit 1-3 px higher or lower, which is allowed).

Only Pillow is used. Anything that cannot be read with confidence is left
empty and reported in "warnings", so the doctor can fill it in.
"""
import io
import re
import zlib

from PIL import Image

from . import doppler_glyphs

FORMAT = 'hadeco-smartxt'
WIDTH, HEIGHT = 806, 1140

# graph boxes: top border rows; per side the left border, the y-axis and the right border
BOX_TOPS = (225, 346, 466, 586, 706, 826)
BOX_HEIGHT = 80
SIDES = {'R': (51, 122, 407), 'L': (426, 497, 782)}
TICK_OFFSETS = (20, 40, 60)          # the three labelled ticks, px below the box top

SITES = (
    ('femoral', 'ბარძაყი', 'High Thigh / Femoral'),
    ('popliteal', 'მუხლზემოთ', 'Above Knee / Popliteal'),
    ('below_knee', 'მუხლქვემოთ', 'Below Knee'),
    ('ankle_pt', 'კოჭი — PT', 'Ankle / PT'),
    ('metatarsal_dp', 'ტერფი — DP', 'Transmetatarsal / DP'),
    ('toe', 'დიდი თითი', 'Great Toe (PPG)'),
)

# the pressure table: rows (top, bottom) and the two value columns
TABLE_ROWS = (('arm', 961, 980), ('pt', 982, 1002), ('dp', 1004, 1024), ('toe', 1026, 1046),
              ('abi_pt', 1054, 1074), ('abi_dp', 1076, 1096), ('tbi', 1098, 1117))
TABLE_COLS = {'R': (270, 364), 'L': (495, 589)}

INK = 70                              # darkness from which a pixel counts as ink (0..255)
MAX_GLYPH_DISTANCE = 15.0             # a real match is < 5; worse: "check this"
MAX_LABEL_DISTANCE = 30.0


class DopplerError(ValueError):
    pass


# --------------------------------------------------------------------------
# PDF -> picture (the device's PDF holds exactly one page-sized image)
# --------------------------------------------------------------------------

_OBJ = re.compile(rb'(\d+)\s+(\d+)\s+obj(.*?)endobj', re.S)


def image_from_pdf(data):
    best = None
    for match in _OBJ.finditer(data):
        body = match.group(3)
        if b'/Image' not in body or b'stream' not in body:
            continue
        head, _, rest = body.partition(b'stream')
        w = re.search(rb'/Width\s+(\d+)', head)
        h = re.search(rb'/Height\s+(\d+)', head)
        if not (w and h):
            continue
        w, h = int(w.group(1)), int(h.group(1))
        if best and best[0] * best[1] >= w * h:
            continue
        raw = rest.lstrip(b'\r\n') if rest[:1] in (b'\r', b'\n') else rest
        if raw.startswith(b'\r\n'):
            raw = raw[2:]
        raw = raw.rsplit(b'endstream', 1)[0].rstrip(b'\r\n')
        best = (w, h, head, raw)
    if not best:
        raise DopplerError('PDF-ში სურათი ვერ ვიპოვე.')
    w, h, head, raw = best
    if w * h > 20_000_000:
        raise DopplerError('სურათი ძალიან დიდია.')
    try:
        if b'DCTDecode' in head:
            return Image.open(io.BytesIO(raw)).convert('RGB')
        if b'FlateDecode' in head:
            inflater = zlib.decompressobj()
            raw = inflater.decompress(raw, w * h * 4 + 1024)
        bpc = re.search(rb'/BitsPerComponent\s+(\d+)', head)
        if bpc and int(bpc.group(1)) != 8:
            raise DopplerError('სურათის ფორმატი უცნობია.')
        if len(raw) >= w * h * 3:
            return Image.frombytes('RGB', (w, h), raw[:w * h * 3])
        if len(raw) >= w * h:
            return Image.frombytes('L', (w, h), raw[:w * h]).convert('RGB')
    except (zlib.error, OSError, ValueError) as e:
        raise DopplerError(f'PDF-ის სურათი ვერ წავიკითხე ({e}).')
    raise DopplerError('PDF-ის სურათი ვერ წავიკითხე.')


def load(uploaded):
    """An uploaded file (PDF, or a PNG of the same report) -> RGB picture."""
    data = uploaded.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        raise DopplerError('ფაილი ძალიან დიდია.')
    if data[:5] == b'%PDF-':
        return image_from_pdf(data), data
    try:
        return Image.open(io.BytesIO(data)).convert('RGB'), data
    except OSError:
        raise DopplerError('ეს არ არის PDF.')


# --------------------------------------------------------------------------
# pixels
# --------------------------------------------------------------------------

class Page:
    def __init__(self, image):
        if image.size != (WIDTH, HEIGHT):
            raise DopplerError(f'ფორმატი არ არის ნაცნობი (ზომა {image.size[0]}×{image.size[1]}). '
                               'ამოიცნობა მხოლოდ Smart-XT-ის „Doppler Arterial Testing Report“.')
        self.px = image.load()

    def dark(self, x, y):
        r, g, b = self.px[x, y]
        return 255 - min(r, g, b)

    def red(self, x, y):
        """The curve is pure red; the text's coloured (ClearType) edges are not."""
        r, g, b = self.px[x, y]
        return r >= 170 and g <= 40 and b <= 40

    def ink_or_red(self, x, y):
        return self.dark(x, y) > 90 or self.red(x, y)

    def grid(self, x, y, w, h, mask_red=True):
        """Darkness values; None where the red curve hides the text."""
        return [[None if mask_red and self.red(x + i, y + j) else self.dark(x + i, y + j) for i in range(w)]
                for j in range(h)]


def check_layout(page):
    """The graph boxes must be where the device draws them."""
    misses = 0
    for top in BOX_TOPS:
        for left, _, right in SIDES.values():
            for x in range(left + 20, right - 20, 20):
                if page.dark(x, top) < 100 and page.dark(x, top - 1) < 100:
                    misses += 1
    if misses > 6:
        raise DopplerError('ფორმატი არ არის ნაცნობი: გრაფიკების ჩარჩოები სხვა ადგილასაა. '
                           'ამოიცნობა მხოლოდ Smart-XT-ის „Doppler Arterial Testing Report“.')


# --------------------------------------------------------------------------
# characters
# --------------------------------------------------------------------------

def _tight(g):
    rows = [j for j, row in enumerate(g) if any((v or 0) > INK for v in row)]
    if not rows:
        return None
    cols = [i for i in range(len(g[0])) if any((g[j][i] or 0) > INK for j in rows)]
    return [row[cols[0]:cols[-1] + 1] for row in g[rows[0]:rows[-1] + 1]]


def _distance(x, t):
    """Mean difference of two glyphs on a common canvas, allowing a 1 px shift.

    Pixels hidden by the red curve (None) are skipped.
    """
    if x is None or t is None:
        return 0.0 if x is None and t is None else 255.0
    hx, wx, ht, wt = len(x), len(x[0]), len(t), len(t[0])
    if abs(hx - ht) > 2 or abs(wx - wt) > 2:
        return 200.0
    H, W = max(hx, ht) + 2, max(wx, wt) + 2
    best = 255.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            oyx, oxx, oyt, oxt = 1 + max(dy, 0), 1 + max(dx, 0), 1 + max(-dy, 0), 1 + max(-dx, 0)
            total = n = 0
            for j in range(H):
                jx, jt = j - oyx, j - oyt
                rx = x[jx] if 0 <= jx < hx else None
                rt = t[jt] if 0 <= jt < ht else None
                for i in range(W):
                    ix, it = i - oxx, i - oxt
                    vx = rx[ix] if rx is not None and 0 <= ix < wx else 0
                    if vx is None:
                        continue
                    vt = rt[it] if rt is not None and 0 <= it < wt else 0
                    if vt is None:
                        continue
                    total += abs(vx - vt)
                    n += 1
            if n:
                best = min(best, total / n)
    return best


def _templates(kind):
    return doppler_glyphs.templates(kind)


def classify(g, kind):
    """-> (character, distance). ' ' for an empty cell (or one with only stray dots)."""
    g = _tight(g)
    if g is None:
        return ' ', 0.0
    scored = sorted((_distance(g, t), ch) for ch, t in _templates(kind))
    if scored[0][0] > 2 * MAX_GLYPH_DISTANCE:
        return '?', scored[0][0]
    return scored[0][1], scored[0][0]


def _strip_lines(page, g, x, y):
    """Remove horizontal lines (zero line, borders) that cross a text field."""
    for j in range(len(g)):
        cover = sum(1 for i in range(-6, 58) if page.ink_or_red(x + i, y + j))
        if cover > 0.8 * 64:
            g[j] = [None] * len(g[j])
    return g


def read_cells(page, x, y, count, height, stop=' '):
    """Fixed-pitch (10 px) characters from (x, y) until an empty / non-number cell."""
    text, worst = '', 0.0
    for k in range(count):
        g = _strip_lines(page, page.grid(x + 10 * k, y, 10, height), x + 10 * k, y)
        ch, d = classify(g, 'cell')
        if ch not in '0123456789.-*':
            break
        text += ch
        worst = max(worst, d)
    return text, worst


def _runs(page, x0, x1, y0, y1, threshold=60):
    runs, start = [], None
    for x in range(x0, x1 + 1):
        has = x < x1 and any(page.dark(x, y) > threshold for y in range(y0, y1))
        if has and start is None:
            start = x
        if not has and start is not None:
            runs.append((start, x))
            start = None
    return runs


def read_table_cell(page, side, top, bottom):
    x0, x1 = TABLE_COLS[side]
    text, worst = '', 0.0
    for a, b in _runs(page, x0, x1, top + 1, bottom):
        g = page.grid(a, top + 1, b - a, bottom - top - 1, mask_red=False)
        ch, d = classify(g, 'table')
        text += ch.strip()
        worst = max(worst, d)
    return text, worst


def label_grid(page, side, box_top, tick):
    """The y-axis label left of a tick mark (the mark itself starts at axis-7)."""
    _, axis, _ = SIDES[side]
    y = box_top + tick - 6
    return _tight(page.grid(axis - 36, y, 28, 12, mask_red=False))


def read_label(page, side, box_top, tick):
    """The y-axis label at a tick -> text (whole-label templates), or None."""
    g = label_grid(page, side, box_top, tick)
    if g is None:
        return None
    scored = sorted((_distance(g, t), text) for text, t in _templates('label'))
    return scored[0][1] if scored and scored[0][0] <= MAX_LABEL_DISTANCE else None


def _number(text):
    if not text or '*' in text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# curves
# --------------------------------------------------------------------------

def _zero_row(page, side, top):
    _, axis, right = SIDES[side]
    width = right - axis - 1
    best, row = 0.0, None
    for j in range(2, BOX_HEIGHT - 1):
        cover = sum(1 for x in range(axis + 1, right) if page.ink_or_red(x, top + j)) / width
        if cover > best:
            best, row = cover, j
    return row if best > 0.9 else None


def _simplify(points, eps):
    """Ramer-Douglas-Peucker on [(x, y), ...]."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (xa, ya), (xb, yb) = points[a], points[b]
        dx, dy = xb - xa, yb - ya
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        far, idx = 0.0, None
        for k in range(a + 1, b):
            x, y = points[k]
            d = abs(dy * (x - xa) - dx * (y - ya)) / norm
            if d > far:
                far, idx = d, k
        if idx is not None and far > eps:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def read_curve(page, side, top, zero):
    """The red curve as [(x, y_px_above_zero), ...] (x: 0 .. plot width)."""
    _, axis, right = SIDES[side]
    points, last = [], None
    for i, x in enumerate(range(axis + 1, right)):
        ys = [j for j in range(1, BOX_HEIGHT - 1) if j != zero and page.red(x, top + j)]
        if not ys:
            if zero is not None and page.red(x, top + zero):
                ys = [zero]
            else:
                continue                        # hidden by text: the line simply joins over it
        lo, hi = min(ys), max(ys)
        order = (lo, hi) if last is None or abs(last - lo) <= abs(last - hi) else (hi, lo)
        for y in dict.fromkeys(order):
            points.append((i, y))
        last = order[-1]
    points = _simplify(points, 0.6)
    base = zero if zero is not None else BOX_HEIGHT // 2
    return [(x, base - y) for x, y in points], right - axis - 1


def _scale(page, side, top, zero, warnings, where):
    """Units per pixel from the axis labels (None when they can't be read)."""
    if zero is None:
        return None, None
    for tick in TICK_OFFSETS:
        label = read_label(page, side, top, tick)
        value = _number(label)
        if value is None:
            continue
        distance = zero - tick                    # px from this tick down to the zero line
        if value == 0 or distance == 0:
            continue
        upp = value / distance
        if upp > 0:
            return upp, [(_number(read_label(page, side, top, t)), zero - t) for t in TICK_OFFSETS]
    warnings.append(f'{where}: მასშტაბი ვერ წავიკითხე (მრუდის ფორმა ჩანს, ერთეულები — არა).')
    return None, None


# --------------------------------------------------------------------------
# the whole page
# --------------------------------------------------------------------------

def parse(image):
    page = Page(image)
    check_layout(page)
    warnings = []
    sites = []
    for row, (key, label, label_en) in enumerate(SITES):
        top = BOX_TOPS[row]
        for side, (left, _axis, _right) in SIDES.items():
            where = f'{"მარჯვენა" if side == "R" else "მარცხენა"} — {label}'
            hr_text, hr_d = read_cells(page, left + 265, top + 1, 3, 17)
            pv_text, pv_d = read_cells(page, left + 249, top + 59, 6, 18)
            hr, pv = _number(hr_text), _number(pv_text)
            if hr_d > MAX_GLYPH_DISTANCE and hr is not None:
                warnings.append(f'{where}: გულისცემა გადაამოწმეთ ({hr_text}).')
            if pv_d > MAX_GLYPH_DISTANCE and pv is not None:
                warnings.append(f'{where}: {"PP" if key == "toe" else "სიჩქარე"} გადაამოწმეთ ({pv_text}).')
            zero = _zero_row(page, side, top)
            wave, upp, ticks = [], None, []
            if zero is not None or key == 'toe':
                raw, width = read_curve(page, side, top, zero)
                if len(raw) > 4:
                    upp, ticks = _scale(page, side, top, zero, warnings, where)
                    wave = [[x, round(y * upp, 3) if upp else y] for x, y in raw]
            sites.append({
                'key': key, 'label': label, 'label_en': label_en, 'side': side,
                'unit': 'mV/V' if key == 'toe' else 'cm/s',
                'hr': int(hr) if hr is not None else None,
                'value': pv,                       # peak velocity (cm/s) or the toe's PP (mV/V)
                'wave': wave, 'width': 283, 'scaled': upp is not None,
                'ticks': [[v, round(px * upp, 3) if upp else px] for v, px in (ticks or []) if v is not None],
            })
    pressures, indices = {'R': {}, 'L': {}}, {'R': {}, 'L': {}}
    for key, y0, y1 in TABLE_ROWS:
        for side in ('R', 'L'):
            text, d = read_table_cell(page, side, y0, y1)
            value = _number(text)
            if text and value is None or (value is not None and d > MAX_GLYPH_DISTANCE):
                warnings.append(f'ცხრილი, {"მარჯვენა" if side == "R" else "მარცხენა"}, {key}: გადაამოწმეთ ({text}).')
            target = indices if key.startswith(('abi', 'tbi')) else pressures
            target[side][key] = value if key.startswith(('abi', 'tbi')) else (int(value) if value is not None else None)
    return {'format': FORMAT, 'version': 1, 'sites': sites, 'pressures': pressures,
            'indices': indices, 'warnings': warnings}


# --------------------------------------------------------------------------
# for the page: categories and SVG paths
# --------------------------------------------------------------------------

def abi_class(value):
    """Categories printed on the report (2011 ACCF/AHA)."""
    if value is None:
        return '', ''
    if value > 1.40:
        return 'nc', 'არაკომპრესირებადი'
    if value >= 1.00:
        return 'ok', 'ნორმა'
    if value >= 0.91:
        return 'border', 'საზღვარზე'
    return 'bad', 'პათოლოგია'


def tbi_class(value):
    if value is None:
        return '', ''
    return ('ok', 'ნორმა') if value > 0.70 else ('bad', 'პათოლოგია')


def chart(site, w=300, h=96, pad=6):
    """SVG geometry for one curve: the path, the zero line and the axis labels."""
    wave = site.get('wave') or []
    if not wave:
        return None
    ys = [p[1] for p in wave]
    tick_values = [t[1] for t in site.get('ticks') or []]
    lo = min(ys + [0] + tick_values)
    hi = max(ys + [0] + tick_values)
    if hi - lo < 1e-9:
        hi, lo = lo + 1, lo - 1
    span = hi - lo
    sx = (w - 2) / max(site.get('width') or 283, 1)

    def Y(v):
        return round(pad + (hi - v) / span * (h - 2 * pad), 1)

    d = 'M' + ' L'.join(f'{round(1 + x * sx, 1)},{Y(v)}' for x, v in wave)
    zero = Y(0)
    area = f'{d} L{round(1 + wave[-1][0] * sx, 1)},{zero} L{round(1 + wave[0][0] * sx, 1)},{zero} Z'
    labels = []
    if site.get('scaled'):
        for value, units in site.get('ticks') or []:
            labels.append({'top': round(Y(units) / h * 100, 1), 'text': f'{value:g}'})
    return {'w': w, 'h': h, 'd': d, 'area': area, 'zero': zero, 'labels': labels}

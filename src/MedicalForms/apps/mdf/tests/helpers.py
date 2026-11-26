"""Shared test helpers and base TestCase classes for `apps.mdf.tests`."""
import io

from django.contrib.auth.models import User
from django.core.cache import cache, caches
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import NivForm, StaffProfile


def hours(*letters):
    return {h: '+' for h in letters}


class BaseViewTest(TestCase):

    def setUp(self):
        cache.clear()
        caches['pdf'].clear()
        self.user = User.objects.create_user('doc', password='Secret-pass-123')
        self.client.force_login(self.user)

    def make_niv(self, **kwargs):
        data = dict(title='შაბლონი', patient_name='პაციენტი', m1='Aspirin', n1='1 ტაბ.',
                    r1=dict(d1=hours('a', 'b'), d2={}, d3={}))
        data.update(kwargs)
        return NivForm.objects.create(**data)


class BoardBase(BaseViewTest):
    """Two doctors with profiles and a helper to put a patient on the board."""

    def setUp(self):
        super().setUp()
        StaffProfile.objects.create(user=self.user, display_name='რამაზი', full_name='აფხაძე რამაზი')
        self.duty = User.objects.create_user('duty', password='x')
        StaffProfile.objects.create(user=self.duty, display_name='ია', full_name='ფარულავა ია')

    @staticmethod
    def at(text):
        import datetime
        from zoneinfo import ZoneInfo
        return datetime.datetime.strptime(text, '%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo('Asia/Tbilisi'))

    def add_patient(self, **kw):
        data = {'name': 'ბერიძე ნინო', 'bed': '4', 'diagnosis': 'მუცლის აორტის ანევრიზმა',
                'unit': 'icu', 'doctor': self.user.pk}
        data.update(kw)
        self.client.post(reverse('board-patient-new'), data)
        from ..models import BoardPatient
        return BoardPatient.objects.get(name=data['name'])


def _jpeg(size=(3000, 2000), orientation=None, color='red'):
    from PIL import Image
    img = Image.new('RGB', size, color)
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = 'PhoneMaker'                 # Make
    if orientation:
        exif[0x0112] = orientation              # Orientation
    img.save(out, 'JPEG', exif=exif.tobytes())
    out.seek(0)
    out.name = 'IMG_0001.jpg'
    return out


class PrivateMediaTest(BaseViewTest):
    """Patient files go to a temporary private folder."""

    def setUp(self):
        super().setUp()
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.private = override_settings(PRIVATE_MEDIA_ROOT=self._tmp.name)
        self.private.enable()

    def tearDown(self):
        self.private.disable()
        self._tmp.cleanup()
        super().tearDown()

    def make_case(self, **kw):
        from ..models import Case
        data = dict(patient_name='ბერიძე ნინო', age=67, diagnosis='კრიტიკული იშემია', created_by=self.user)
        data.update(kw)
        return Case.objects.create(**data)


def synthetic_doppler(hr='62', pv='31.6', arm='120', abi='0.55', blank_left=False):
    """A Smart-XT-like report drawn with the device's own character shapes (no patient data)."""
    import math

    from PIL import Image

    from .. import doppler as dp
    from ..doppler_glyphs import templates
    img = Image.new('RGB', (dp.WIDTH, dp.HEIGHT), 'white')
    px = img.load()

    def paint(kind, text, x, y, pitch=10):
        for k, ch in enumerate(text):
            g = next(t for c, t in templates(kind) if c == ch)
            for j, row in enumerate(g):
                for i, v in enumerate(row):
                    if v:
                        px[x + k * pitch + i, y + j] = (255 - v,) * 3

    def label(text, side, top, tick):
        g = next(t for c, t in templates('label') if c == text)
        axis = dp.SIDES[side][1]
        x0, y0 = axis - 9 - len(g[0]), top + tick - len(g) // 2
        for j, row in enumerate(g):
            for i, v in enumerate(row):
                if v:
                    px[x0 + i, y0 + j] = (255 - v,) * 3

    for row, top in enumerate(dp.BOX_TOPS):
        toe = row == 5
        for side, (left, axis, right) in dp.SIDES.items():
            for x in range(left, right + 1):
                px[x, top] = px[x, top + dp.BOX_HEIGHT] = (0, 0, 0)
            for y in range(top, top + dp.BOX_HEIGHT + 1):
                px[left, y] = px[right, y] = px[axis, y] = (0, 0, 0)
            if side == 'L' and blank_left:
                paint('cell', '***', left + 266, top + 5)
                continue
            zero = top + (40 if toe else 60)
            for x in range(axis + 1, right):
                px[x, zero] = (0, 0, 0)
            labels = ('0.25', '0', '-0.25') if toe else ('40', '20', '0')
            for tick, text in zip(dp.TICK_OFFSETS, labels, strict=True):
                label(text, side, top, tick)
            last = None
            for x in range(axis + 1, right):
                phase = (x - axis) / 47.0
                v = math.sin(phase * math.pi * 2) * (16 if toe else 0)
                if not toe:
                    v = max(0.0, math.sin(phase * math.pi * 2)) ** 3 * 30   # 30 px = 30 cm/s
                y = int(round(zero - v))
                for yy in range(min(y, last or y), max(y, last or y) + 1):
                    px[x, yy] = (255, 0, 0)
                last = y
            paint('cell', hr, left + 266, top + 5)
            paint('cell', pv, left + 250, top + 63)
    for key, y0, _y1 in dp.TABLE_ROWS:
        for side in ('R', 'L'):
            text = {'arm': arm if side == 'R' else '', 'pt': '66', 'dp': '68', 'toe': '39',
                    'abi_pt': abi, 'abi_dp': '1.06', 'tbi': '0.32'}[key]
            paint('table', text, dp.TABLE_COLS[side][0] + 30, y0 + 5)
    return img


def as_pdf(img):
    import zlib
    raw = zlib.compress(img.tobytes())
    head = (b'%%PDF-1.4\n1 0 obj\n<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB '
            b'/BitsPerComponent 8 /Filter [ /FlateDecode ] /Length %d >>\nstream\n' % (img.width, img.height, len(raw)))
    return head + raw + b'\nendstream\nendobj\ntrailer\n<< >>\n%%EOF\n'

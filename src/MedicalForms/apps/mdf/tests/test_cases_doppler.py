"""Tests for vascular conference cases, private photos, and Hadeco Doppler PDF parser."""
import io
import json
import shutil
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache, caches
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from ..models import DutyNote, Medicine, NivForm, PrescriptionTemplate, StaffProfile
from ..utils import validate_take_medicine
from ..views import NivDocumentView
from .helpers import BaseViewTest, BoardBase, PrivateMediaTest, _jpeg, as_pdf, synthetic_doppler


class CaseTests(PrivateMediaTest):

    def test_create_edit_and_list(self):
        from ..models import Case
        r = self.client.post(reverse('case-new'), dict(patient_name='  ბერიძე   ნინო ', age='67', sex='F', side='R',
                                                       diagnosis='კრიტიკული იშემია', proposal='შუნტირება',
                                                       conference_on='2026-11-26', doctor=str(self.user.pk)))
        case = Case.objects.get()
        self.assertRedirects(r, reverse('case', args=[case.pk]))
        self.assertEqual((case.patient_name, case.age, case.side, case.doctor, case.status),
                         ('ბერიძე ნინო', 67, 'R', self.user, 'open'))
        self.client.post(reverse('case-edit', args=[case.pk]), dict(patient_name='ბერიძე ნინო', age='abc', side='X'))
        case.refresh_from_db()
        self.assertEqual((case.age, case.side), (None, ''))
        page = self.client.get(reverse('cases'))
        self.assertContains(page, 'ბერიძე ნინო')
        self.assertContains(page, reverse('cases-present'))
        self.assertContains(self.client.get(reverse('case', args=[case.pk])), 'capture="environment"')
        # the name is required
        self.client.post(reverse('case-new'), dict(patient_name='  '))
        self.assertEqual(Case.objects.count(), 1)

    def test_prefill_from_board(self):
        from ..models import BoardPatient
        p = BoardPatient.objects.create(unit='icu', name='კაპანაძე გიორგი', diagnosis='ანევრიზმა', created_by=self.user)
        page = self.client.get(reverse('case-new'), {'board': p.pk})
        self.assertContains(page, 'value="კაპანაძე გიორგი"')
        self.assertContains(page, '>ანევრიზმა</textarea>')
        self.assertContains(self.client.get(reverse('board-patient', args=[p.pk])), f"{reverse('case-new')}?board={p.pk}")

    def test_photo_upload_is_upright_small_and_without_hidden_data(self):
        import os
        from PIL import Image
        case = self.make_case()
        r = self.client.post(reverse('case-photos', args=[case.pk]),
                             {'kind': 'angio', 'taken_on': '2026-11-20', 'photo': _jpeg(orientation=6)},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json(), {'added': 1, 'errors': []})
        photo = case.photos.get()
        self.assertEqual((photo.kind, str(photo.taken_on)), ('angio', '2026-11-20'))
        self.assertEqual((photo.width, photo.height), (1333, 2000))        # turned upright, longest side 2000
        path = os.path.join(self._tmp.name, photo.image.name)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(self._tmp.name)))
        with Image.open(path) as img:
            self.assertEqual(len(img.getexif()), 0)                         # no phone/location data left
        with Image.open(os.path.join(self._tmp.name, photo.thumb.name)) as img:
            self.assertEqual(max(img.size), 480)

    def test_photos_only_for_logged_in_users(self):
        from ..cases import add_photo
        case = self.make_case()
        photo = add_photo(case, _jpeg(), 'wound', self.user)
        r = self.client.get(reverse('case-photo', args=[photo.pk]))
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'image/jpeg'))
        self.assertIn('private', r['Cache-Control'])
        self.assertEqual(self.client.get(reverse('case-photo-thumb', args=[photo.pk])).status_code, 200)
        self.assertNotIn(self._tmp.name, str(settings.MEDIA_ROOT))
        self.client.logout()
        self.assertEqual(self.client.get(reverse('case-photo', args=[photo.pk])).status_code, 302)
        self.assertEqual(self.client.get('/media/' + photo.image.name).status_code, 404)   # not in the public folder

    def test_bad_file_and_photo_edit_delete(self):
        import os
        from ..cases import add_photo
        case = self.make_case()
        bad = io.BytesIO(b'not an image')
        bad.name = 'x.jpg'
        r = self.client.post(reverse('case-photos', args=[case.pk]), {'photo': bad}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400)
        self.assertIn('სურათად ვერ', r.json()['errors'][0])
        photo = add_photo(case, _jpeg(), 'wound', self.user)
        self.client.post(reverse('case-photo-edit', args=[photo.pk]), dict(caption='ტერფის ზურგი', kind='doppler', taken_on='2099-01-01'))
        photo.refresh_from_db()
        self.assertEqual((photo.caption, photo.kind), ('ტერფის ზურგი', 'doppler'))
        self.assertNotEqual(photo.taken_on.year, 2099)                      # no future dates
        files = [os.path.join(self._tmp.name, photo.image.name), os.path.join(self._tmp.name, photo.thumb.name)]
        self.client.post(reverse('case-photo-edit', args=[photo.pk]), dict(action='delete'))
        self.assertFalse(case.photos.exists())
        self.assertFalse(any(os.path.exists(f) for f in files))             # the files are gone too

    def test_decision_presentation_and_delete(self):
        import os
        from ..cases import add_photo
        from ..models import Case, CasePhoto
        a = self.make_case(patient_name='ბერიძე ნინო', proposal='შუნტირება')
        b = self.make_case(patient_name='კაპანაძე გიორგი')
        photo = add_photo(a, _jpeg(), 'angio', self.user)
        page = self.client.get(reverse('cases-present'), {'ids': f'{b.pk},{a.pk}'})
        self.assertContains(page, 'ბ. ნ.')                                  # initials for the anonymous mode
        body = page.content.decode()
        self.assertLess(body.index('კაპანაძე გიორგი'), body.index('ბერიძე ნინო'))   # the chosen order
        self.assertContains(page, reverse('case-photo', args=[photo.pk]))
        r = self.client.post(reverse('case-decision', args=[a.pk]), {'decision': ' ბარძაყ-მუხლქვეშა შუნტირება '},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json()['status'], 'decided')
        a.refresh_from_db()
        self.assertEqual((a.decision, a.status, a.decided_by), ('ბარძაყ-მუხლქვეშა შუნტირება', 'decided', self.user))
        # the default presentation shows only open cases
        self.assertNotContains(self.client.get(reverse('cases-present')), 'ბერიძე ნინო')
        self.assertContains(self.client.get(reverse('cases'), {'s': 'decided'}), 'ბარძაყ-მუხლქვეშა')
        self.client.post(reverse('case-decision', args=[a.pk]), {'action': 'reopen'})
        a.refresh_from_db()
        self.assertEqual(a.status, 'open')
        path = os.path.join(self._tmp.name, photo.image.name)
        self.client.post(reverse('case-delete', args=[a.pk]))
        self.assertFalse(Case.objects.filter(pk=a.pk).exists())
        self.assertFalse(CasePhoto.objects.exists())
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.client.get(reverse('cases-present'), {'ids': '999'}).status_code, 404)

    def test_backup_includes_cases(self):
        import gzip
        self.make_case()
        data = json.loads(gzip.decompress(b''.join(self.client.get(reverse('backup-download')).streaming_content)))
        self.assertTrue(any(o['model'] == 'mdf.case' for o in data))


class DopplerTests(PrivateMediaTest):

    def test_reads_numbers_curves_and_scale(self):
        from .. import doppler as dp
        img, raw = dp.load(io.BytesIO(as_pdf(synthetic_doppler())))
        self.assertEqual(img.size, (806, 1140))
        data = dp.parse(img)
        self.assertEqual(data['warnings'], [])
        fem = next(s for s in data['sites'] if s['key'] == 'femoral' and s['side'] == 'R')
        self.assertEqual((fem['hr'], fem['value'], fem['unit'], fem['scaled']), (62, 31.6, 'cm/s', True))
        peak = max(v for _, v in fem['wave'])
        self.assertAlmostEqual(peak, 30, delta=1.5)                  # 30 px at 1 cm/s per px (label 40 at 40 px)
        toe = next(s for s in data['sites'] if s['key'] == 'toe' and s['side'] == 'L')
        self.assertEqual(toe['unit'], 'mV/V')
        self.assertAlmostEqual(max(v for _, v in toe['wave']), 0.2, delta=0.02)   # 16 px at 0.25/20 px
        self.assertEqual(data['pressures']['R'], {'arm': 120, 'pt': 66, 'dp': 68, 'toe': 39})
        self.assertEqual(data['pressures']['L']['arm'], None)
        self.assertEqual(data['indices']['R'], {'abi_pt': 0.55, 'abi_dp': 1.06, 'tbi': 0.32})
        self.assertEqual(dp.abi_class(0.55)[0], 'bad')
        self.assertEqual(dp.abi_class(0.95)[0], 'border')
        self.assertEqual(dp.abi_class(1.2)[0], 'ok')
        self.assertEqual(dp.abi_class(1.5)[0], 'nc')
        self.assertEqual(dp.tbi_class(0.81)[0], 'ok')
        chart = dp.chart(fem)
        self.assertTrue(chart['d'].startswith('M'))
        self.assertEqual([l['text'] for l in chart['labels']], ['40', '20', '0'])
        self.assertLess(chart['labels'][0]['top'], chart['labels'][2]['top'])      # 40 above 0

    def test_no_signal_and_other_values(self):
        from .. import doppler as dp
        data = dp.parse(synthetic_doppler(hr='57', pv='-1.8', arm='152', abi='0.49', blank_left=True))
        right = next(s for s in data['sites'] if s['key'] == 'ankle_pt' and s['side'] == 'R')
        left = next(s for s in data['sites'] if s['key'] == 'ankle_pt' and s['side'] == 'L')
        self.assertEqual((right['hr'], right['value']), (57, -1.8))
        self.assertEqual((left['hr'], left['value'], left['wave']), (None, None, []))
        self.assertEqual((data['pressures']['R']['arm'], data['indices']['R']['abi_pt']), (152, 0.49))

    def test_unknown_files_are_refused(self):
        from PIL import Image
        from .. import doppler as dp
        with self.assertRaises(dp.DopplerError):
            dp.parse(Image.new('RGB', (800, 600), 'white'))              # another size
        with self.assertRaises(dp.DopplerError):
            dp.parse(Image.new('RGB', (806, 1140), 'white'))             # right size, no graphs
        with self.assertRaises(dp.DopplerError):
            dp.load(io.BytesIO(b'%PDF-1.4 nothing here'))

    def test_upload_show_edit_and_delete(self):
        import os
        from ..models import DopplerStudy
        case = self.make_case()
        f = io.BytesIO(as_pdf(synthetic_doppler()))
        f.name = 'report.pdf'
        r = self.client.post(reverse('case-doppler', args=[case.pk]), {'report': f, 'performed_on': '2026-11-04'})
        self.assertRedirects(r, reverse('case', args=[case.pk]) + '#doppler', fetch_redirect_response=False)
        study = DopplerStudy.objects.get()
        self.assertEqual(str(study.performed_on), '2026-11-04')
        path = os.path.join(self._tmp.name, study.file.name)
        self.assertTrue(os.path.exists(path))
        page = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(page, 'class="dp-line"')
        self.assertContains(page, '0.55')
        self.assertContains(page, 'პათოლოგია')
        self.assertContains(self.client.get(reverse('cases-present'), {'ids': case.pk}), 'pz-dop')
        pdf = self.client.get(reverse('doppler-file', args=[study.pk]))
        self.assertEqual((pdf.status_code, pdf['Content-Type']), (200, 'application/pdf'))
        # a doctor corrects a value
        data = {f'p_{s}_{k}': '' for s in 'RL' for k in ('arm', 'pt', 'dp', 'toe')}
        data.update({'p_R_pt': '70', 'i_R_abi_pt': '0,58', 'performed_on': '2026-11-05',
                     's_R_femoral': '33.0', 'h_R_femoral': '64'})
        self.client.post(reverse('doppler-edit', args=[study.pk]), data)
        study.refresh_from_db()
        fem = next(s for s in study.data['sites'] if s['key'] == 'femoral' and s['side'] == 'R')
        self.assertEqual((study.data['pressures']['R']['pt'], study.data['indices']['R']['abi_pt'], fem['value'], fem['hr'],
                          str(study.performed_on)), (70, 0.58, 33.0, 64, '2026-11-05'))
        # not readable -> a message, nothing saved
        bad = io.BytesIO(b'%PDF-1.4 nothing')
        bad.name = 'x.pdf'
        self.client.post(reverse('case-doppler', args=[case.pk]), {'report': bad})
        self.assertEqual(DopplerStudy.objects.count(), 1)
        # only for logged-in users; deleting removes the file
        self.client.logout()
        self.assertEqual(self.client.get(reverse('doppler-file', args=[study.pk])).status_code, 302)
        self.client.force_login(self.user)
        self.client.post(reverse('doppler-edit', args=[study.pk]), {'action': 'delete'})
        self.assertFalse(DopplerStudy.objects.exists())
        self.assertFalse(os.path.exists(path))

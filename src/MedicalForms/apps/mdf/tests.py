"""Tests for the NIV-300-9/a forms.

Run:  python manage.py test --settings=MedicalForms.settings.dev
"""
import json
import shutil
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import QueryDict
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Medicine, NivForm, PrescriptionTemplate, StaffProfile
from .utils import validate_take_medicine
from .views import NivDocumentView


def hours(*letters):
    return {h: '+' for h in letters}


class ValidateTakeMedicineTests(TestCase):

    def test_hours_are_grouped_by_row_and_day(self):
        data = QueryDict(mutable=True)
        data.setlist('r1-d1', ['a', 'c'])
        data.setlist('r1-d2', ['del-b'])
        data.setlist('gk-d3', ['l'])
        data['m1'] = 'Aspirin'
        result, keys = validate_take_medicine(data)
        self.assertEqual(result['r1'], {'d1': {'a': '+', 'c': '+'}, 'd2': {}})
        self.assertNotIn('gk', result)  # back side was removed
        self.assertNotIn('m1', result)


class BaseViewTest(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('doc', password='Secret-pass-123')
        self.client.force_login(self.user)

    def make_niv(self, **kwargs):
        data = dict(title='შაბლონი', patient_name='პაციენტი', m1='Aspirin', n1='1 ტაბ.',
                    r1=dict(d1=hours('a', 'b'), d2={}, d3={}))
        data.update(kwargs)
        return NivForm.objects.create(**data)


class LoginTests(TestCase):

    def setUp(self):
        cache.clear()
        User.objects.create_user('doc', password='Secret-pass-123')

    def test_login_and_safe_next(self):
        r = self.client.post('/', {'username': 'doc', 'password': 'Secret-pass-123',
                                   'next': '/niv-300-9-a/templates/'})
        self.assertRedirects(r, '/niv-300-9-a/templates/')

    def test_login_ignores_external_next(self):
        r = self.client.post('/', {'username': 'doc', 'password': 'Secret-pass-123',
                                   'next': 'https://evil.example.com/'})
        self.assertRedirects(r, reverse('niv-cover'))

    def test_wrong_password_shows_message(self):
        r = self.client.post('/', {'username': 'doc', 'password': 'nope'})
        self.assertContains(r, 'პაროლი არასწორია')

    def test_no_lockout_and_simple_passwords(self):
        User.objects.create_user('ia', password='test-pass-3')
        for _ in range(10):
            self.client.post('/', {'username': 'ia', 'password': 'nope'})
        r = self.client.post('/', {'username': 'ia', 'password': 'test-pass-3'})
        self.assertRedirects(r, reverse('niv-cover'))

    def test_session_is_persistent(self):
        self.client.post('/', {'username': 'doc', 'password': 'Secret-pass-123'})
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertGreaterEqual(self.client.session.get_expiry_age(), 300 * 24 * 3600)

    def test_login_page_shows_staff_profiles(self):
        user = User.objects.create_user('ina', password='test-pass-1')
        StaffProfile.objects.create(user=user, display_name='ინა', order=1)
        r = self.client.get('/')
        self.assertContains(r, 'data-username="ina"')
        self.assertContains(r, 'ინა')
        r = self.client.post('/', {'username': 'ina', 'password': 'test-pass-1'})
        self.assertRedirects(r, reverse('niv-cover'))

    def test_pages_require_login(self):
        for url in ('/niv-300-9-a/cover/', '/niv-300-9-a/templates/', '/api/medicines/?q=a'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)


class CoverPageTests(BaseViewTest):

    def test_cover_renders_all_rows_and_values(self):
        niv = self.make_niv()
        r = self.client.get(f'/niv-300-9-a/cover/?t={niv.pk}')
        self.assertEqual(r.status_code, 200)
        content = r.content.decode()
        for i in range(1, 17):
            self.assertIn(f'id="m{i}"', content)
            self.assertIn(f'id="r{i}-d1-m"', content)
            self.assertIn(f'id="r{i}-d3-l"', content)
        self.assertIn('id="form-r1-d1-a" name="r1-d1" value="a"', content)
        self.assertIn('<td scope="col" class="hour" id="r1-d1-a">+</td>', content)

    def test_quotes_in_values_are_escaped(self):
        niv = self.make_niv(m1='Sol. "NaCl" 0.9%', diagnosis='a"b<c>')
        r = self.client.get(f'/niv-300-9-a/cover/?t={niv.pk}')
        self.assertContains(r, 'value="Sol. &quot;NaCl&quot; 0.9%"')
        self.assertNotContains(r, '<c>')

    def test_new_and_unknown_template(self):
        self.assertEqual(self.client.get('/niv-300-9-a/cover/').status_code, 200)
        self.assertEqual(self.client.get('/niv-300-9-a/cover/?t=abc').status_code, 200)
        self.assertEqual(self.client.get('/niv-300-9-a/cover/?t=9999').status_code, 200)


class SaveTests(BaseViewTest):

    def post_data(self, **extra):
        data = {'title': 'ახალი', 'patient_name': 'ნინო', 'm1': 'Aspirin', 'n1': 'დილით',
                'r1-d1': ['a', 'c'], 'neuro': 'on', 'diabetes': '', 'allergy': ''}
        data.update(extra)
        return data

    def test_create(self):
        r = self.client.post(reverse('niv-create'), self.post_data())
        niv = NivForm.objects.get()
        self.assertRedirects(r, f"{reverse('niv-cover')}?t={niv.pk}")
        self.assertEqual(niv.r1['d1'], {'a': '+', 'c': '+'})
        self.assertTrue(niv.neuro)

    def test_update_removes_hours(self):
        niv = self.make_niv()
        r = self.client.post(reverse('niv-create'), self.post_data(
            t=niv.pk, action='save', title=niv.title, **{'r1-d1': ['del-a', 'del-b']}))
        self.assertRedirects(r, f"{reverse('niv-cover')}?t={niv.pk}")
        niv.refresh_from_db()
        self.assertEqual(niv.r1['d1'], {})

    def test_save_as_keeps_original(self):
        niv = self.make_niv(r16=dict(d1=hours('c'), d2={}, d3={}))
        self.client.post(reverse('niv-create'), self.post_data(
            t=niv.pk, action='save_as', title=niv.title, **{'as-title': 'ასლი', 'm1': 'Changed'}))
        niv.refresh_from_db()
        self.assertEqual(niv.m1, 'Aspirin')
        copy = NivForm.objects.get(title='ასლი')
        self.assertEqual(copy.m1, 'Changed')
        self.assertEqual(copy.r16['d1'], {'c': '+'})
        self.assertEqual(NivForm.objects.count(), 2)

    def test_invalid_data_shows_error_and_keeps_record(self):
        niv = self.make_niv()
        r = self.client.post(reverse('niv-create'), self.post_data(
            t=niv.pk, action='save', title=niv.title, person_id='1' * 20), follow=True)
        self.assertContains(r, 'დანიშნულება ვერ შეინახა')
        niv.refresh_from_db()
        self.assertEqual(niv.m1, 'Aspirin')

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse('niv-create')).status_code, 405)


class DeleteTests(BaseViewTest):

    def test_delete_requires_post(self):
        niv = self.make_niv()
        self.assertEqual(self.client.get(reverse('niv-delete', args=[niv.pk])).status_code, 405)
        self.assertTrue(NivForm.objects.filter(pk=niv.pk).exists())

    def test_delete_requires_csrf(self):
        niv = self.make_niv()
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        r = client.post(reverse('niv-delete', args=[niv.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(NivForm.objects.filter(pk=niv.pk).exists())

    def test_delete(self):
        niv = self.make_niv()
        r = self.client.post(reverse('niv-delete', args=[niv.pk]),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json(), {'deleted': True})
        self.assertFalse(NivForm.objects.filter(pk=niv.pk).exists())


class ListAndApiTests(BaseViewTest):

    def test_search(self):
        self.make_niv(title='კარდიოლოგია 1', person_id='01001012345')
        self.make_niv(title='ქირურგია')
        r = self.client.post(reverse('niv-templates'), {'q': '0100101'})
        self.assertContains(r, 'კარდიოლოგია 1')
        self.assertNotContains(r, 'ქირურგია')

    def test_out_of_range_page_is_not_404(self):
        self.make_niv()
        r = self.client.get(reverse('niv-templates') + '?page=7&q=შაბ')
        self.assertEqual(r.status_code, 200)

    def test_medicine_api_ranks_prefix_and_limits(self):
        Medicine.objects.create(name='Tab. Aspirin', note='1 ტაბ')
        Medicine.objects.create(name='Aspirin Cardio', note=None)
        Medicine.objects.create(name='Old aspirin', is_active=False)
        for i in range(40):
            Medicine.objects.create(name=f'Aspirin {i:02d}')
        data = self.client.get('/api/medicines/?q=aspirin').json()
        self.assertEqual(len(data), 30)
        self.assertTrue(data[0][0].startswith('Aspirin'))
        names = [d[0] for d in data]
        self.assertNotIn('Old aspirin', names)
        self.assertEqual(self.client.get('/api/medicines/?q=').json(), [])


class PrintTests(BaseViewTest):

    def test_print_context_keeps_official_keys(self):
        niv = self.make_niv(allergy=True, radial_pulse='სუსტი')
        view = NivDocumentView()
        view.request = self.client.get('/').wsgi_request
        view.request.GET = QueryDict(f't={niv.pk}')
        ctx = view.get_context_data()
        self.assertEqual(ctx['allergy'], '🗹 დიახ □ არა')
        self.assertEqual(ctx['diabetes'], '□ დიახ □ არა')
        self.assertEqual(ctx['radial_pulse'], '□ რეგულარული □ არარეგულარული 🗹 სუსტი')
        self.assertEqual(ctx['r1']['d1'], {'a': '+', 'b': '+'})
        self.assertEqual(ctx['r2'], {'d1': {}, 'd2': {}, 'd3': {}})
        json.dumps(ctx)

    def test_print_unknown_template_is_404(self):
        self.assertEqual(self.client.get('/niv-300-9-a/cover/print/?t=9999').status_code, 404)
        self.assertEqual(self.client.get('/niv-300-9-a/cover/print/').status_code, 404)

    @skipUnless(shutil.which('soffice') or shutil.which('libreoffice'), 'LibreOffice not installed')
    def test_print_returns_pdf(self):
        niv = self.make_niv()
        r = self.client.get(f'/niv-300-9-a/cover/print/?t={niv.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        body = b''.join(r.streaming_content) if r.streaming else r.content
        self.assertTrue(body.startswith(b'%PDF'))



class TemplateTests(BaseViewTest):

    def rows(self):
        return [{'m': 'Heparin', 'n': '5000 ე', 'r': {'d1': {'a': '+', 'zz': '+'}, 'd2': {}, 'd3': {'l': '+'}}}]

    def test_save_template_from_page(self):
        r = self.client.post(reverse('rx-template-save'), json.dumps({'name': 'ანევრიზმა', 'rows': self.rows()}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        tpl = PrescriptionTemplate.objects.get()
        self.assertEqual(len(tpl.rows), 16)
        self.assertEqual(tpl.rows[0]['r']['d1'], {'a': '+'})  # invalid hour dropped
        self.assertEqual(tpl.created_by, self.user)
        # same name -> 409, then overwrite
        r = self.client.post(reverse('rx-template-save'), json.dumps({'name': 'ანევრიზმა', 'rows': self.rows()}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 409)
        rows = self.rows()
        rows[0]['m'] = 'Clexane'
        r = self.client.post(reverse('rx-template-save'),
                             json.dumps({'name': 'ანევრიზმა', 'rows': rows, 'overwrite': True}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PrescriptionTemplate.objects.get().rows[0]['m'], 'Clexane')

    def test_empty_template_rejected(self):
        r = self.client.post(reverse('rx-template-save'), json.dumps({'name': 'x', 'rows': []}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_template_from_prescription_and_cover(self):
        niv = self.make_niv(title='ლერიში')
        self.client.post(reverse('rx-template-from', args=[niv.pk]), {'name': 'ლერიში'})
        tpl = PrescriptionTemplate.objects.get(name='ლერიში')
        self.assertEqual(tpl.rows[0]['m'], 'Aspirin')
        self.assertEqual(tpl.rows[0]['r']['d1'], {'a': '+', 'b': '+'})
        r = self.client.get(reverse('niv-cover') + f'?tpl={tpl.pk}')
        self.assertContains(r, 'id="rx-templates-data"')
        self.assertContains(r, 'data-apply="%d"' % tpl.pk)
        self.client.post(reverse('rx-template-used', args=[tpl.pk]))
        tpl.refresh_from_db()
        self.assertEqual(tpl.use_count, 1)
        self.assertContains(self.client.get(reverse('rx-templates')), 'ლერიში')

    def test_rename_and_delete(self):
        tpl = PrescriptionTemplate.objects.create(name='a', rows=self.rows())
        self.client.post(reverse('rx-template-rename', args=[tpl.pk]), {'name': 'ბარძაყ-მუხლქვეშა'})
        tpl.refresh_from_db()
        self.assertEqual(tpl.name, 'ბარძაყ-მუხლქვეშა')
        self.client.post(reverse('rx-template-delete', args=[tpl.pk]))
        self.assertFalse(PrescriptionTemplate.objects.exists())

    def test_nosology_analysis(self):
        aneurysm = dict(d1=hours('a', 'i'), d2={}, d3={})
        diagnoses = ['I71.4 მუცლის აორტის ანევრიზმა', 'მუცლის აორტის ანევრიზმა',
                     'Z95.8 ანევრიზმის მდგომარეობა', 'I72.4 ქვედა კიდურის არტერიის ანევრიზმით']
        for i, dx in enumerate(diagnoses):
            self.make_niv(title=f'პაციენტი {i}', diagnosis=dx,
                          m1='Heparin 5000', n1='კანქვეშ', r1=aneurysm,
                          m2='Aspirin' if i < 2 else 'Clopidogrel', n2='', r2={})
        self.make_niv(title='ლერიში', diagnosis='I74.0 ლერიშის სინდრომი', m1='Pentoxifylline')
        self.make_niv(title='x', diagnosis='ლერიში', m1='Heparin 5000', m2='heparin 5000')

        # words from the written diagnosis, word forms merged, ICD ignored
        r = self.client.get(reverse('rx-analysis'))
        groups = {g['key']: g for g in r.context['groups']}
        self.assertEqual(groups['ანევრიზმ']['count'], 4)
        self.assertEqual(groups['ლერიშ']['count'], 2)
        self.assertNotIn('მდგომარეობ', groups)     # stop word
        self.assertFalse(any(k.startswith('i7') for k in groups))

        r = self.client.get(reverse('rx-analysis') + '?by=full')
        labels = [g['label'] for g in r.context['groups']]
        self.assertIn('მუცლის აორტის ანევრიზმა', labels)
        self.assertEqual(r.context['groups'][0]['count'], 2)   # with and without ICD code

        r = self.client.get(reverse('rx-analysis') + '?q=ანევრიზმა')
        self.assertEqual(r.context['matched'], 4)
        meds = {m['name']: m for m in r.context['medicines']}
        self.assertEqual(meds['Heparin 5000']['count'], 4)
        self.assertEqual(meds['Heparin 5000']['percent'], 100)
        self.assertEqual(meds['Heparin 5000']['note'], 'კანქვეშ')
        self.assertEqual(meds['Heparin 5000']['hours_text'], 'დღე 1: 8, 24')
        self.assertEqual(meds['Aspirin']['percent'], 50)
        self.assertContains(r, 'value="heparin 5000" checked')

        r = self.client.get(reverse('rx-analysis') + '?q=ლერიშის')
        self.assertEqual(r.context['matched'], 2)
        self.assertEqual(r.context['medicines'][0]['count'], 1)   # counted once per prescription

        r = self.client.post(reverse('rx-template-from-analysis'), {
            'q': 'ანევრიზმა', 'in': 'diagnosis', 'name': 'ანევრიზმა',
            'med': ['heparin 5000', 'aspirin']})
        self.assertRedirects(r, reverse('rx-templates'))
        tpl = PrescriptionTemplate.objects.get(name='ანევრიზმა')
        self.assertEqual([row['m'] for row in tpl.rows if row['m']], ['Heparin 5000', 'Aspirin'])
        self.assertEqual(tpl.rows[0]['r']['d1'], {'a': '+', 'i': '+'})

    def test_csrf_failure_page(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        r = client.post(reverse('niv-create'), {'title': 'x'})
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, 'გვერდი მოძველდა', status_code=403)


class StaffAndBackupTests(TestCase):

    @override_settings()
    def test_setup_staff_and_backup(self):
        import os
        from io import StringIO
        os.environ['STAFF_USERS'] = 'ina:ინა:test-pass-1,gurami:გურამი:test-pass-2'
        try:
            call_command('setup_staff', stdout=StringIO())
            StaffProfile.objects.filter(user__username='gurami').update(order=0)  # changed in admin
            call_command('setup_staff', stdout=StringIO())  # repeatable, keeps the order
        finally:
            del os.environ['STAFF_USERS']
        self.assertTrue(self.client.login(username='ina', password='test-pass-1'))
        ina = User.objects.get(username='ina')
        self.assertTrue(ina.is_staff)
        self.assertTrue(ina.has_perm('mdf.change_medicine'))
        self.assertEqual(ina.profile.display_name, 'ინა')
        self.assertEqual(list(StaffProfile.objects.values_list('user__username', flat=True)), ['gurami', 'ina'])

        r = self.client.get(reverse('backup-download'))
        self.assertEqual(r['Content-Type'], 'application/gzip')
        import gzip
        data = json.loads(gzip.decompress(r.content))
        self.assertTrue(any(o['model'] == 'mdf.staffprofile' for o in data))

        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            for _ in range(3):
                call_command('backup_data', dir=tmp, keep=2, stdout=StringIO())
            import pathlib
            self.assertLessEqual(len(list(pathlib.Path(tmp).glob('*.json.gz'))), 2)

    def test_photo_upload(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        user = User.objects.create_user('ia', password='test-pass-3')
        self.client.force_login(user)
        buf = BytesIO()
        Image.new('RGB', (1200, 800), 'red').save(buf, 'PNG')
        with TemporaryDirectory() as tmp, self.settings(MEDIA_ROOT=tmp):
            r = self.client.post(reverse('my-photo'), {'photo': SimpleUploadedFile('a.png', buf.getvalue())})
            self.assertRedirects(r, reverse('niv-cover'))
            profile = StaffProfile.objects.get(user=user)
            self.assertEqual(Image.open(profile.photo.path).size, (400, 400))
            self.assertEqual(self.client.get(profile.photo.url).status_code, 200)


class AvatarTests(BaseViewTest):

    def test_disease_and_staff_avatars(self):
        from django.test import override_settings
        from apps.mdf import avatars
        avatars._static_file.cache_clear()
        url, key = avatars.disease_avatar('I71.4 მუცლის აორტის ანევრიზმით')
        if not url:
            self.skipTest('no test image in static/img/patients')
        self.assertEqual(key, 'aneurysm')
        self.assertEqual(avatars.disease_avatar('ჰიპერტენზია'), (None, None))
        self.make_niv(title='ბ', diagnosis='აორტის ანევრიზმა')
        self.assertContains(self.client.get(reverse('niv-templates')), 'img/patients/aneurysm.')
        self.assertContains(self.client.get(reverse('rx-analysis')), 'img/patients/aneurysm.')
        ina = User.objects.create_user('ina', password='x')
        profile = StaffProfile.objects.create(user=ina, display_name='ინა')
        self.assertTrue('img/staff/ina.' in profile.avatar_url)


class BloodOrderTests(BaseViewTest):

    def test_empty_form_has_doctor_and_now(self):
        StaffProfile.objects.create(user=self.user, display_name='ინა', full_name='შეროზია ინა')
        r = self.client.get(reverse('blood-order'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="შეროზია ინა"')
        from django.utils import timezone
        self.assertContains(r, timezone.localtime().strftime('%d.%m.%Y'))
        self.assertContains(r, 'value="ტრანსფუზიოლოგია"')

    def test_prefilled_from_prescription(self):
        niv = self.make_niv(patient_name='ბერიძე ნინო', person_id='01001012345', dob='12.03.1960',
                            diagnosis='ათეროსკლეროზი', card_number='1234/26')
        r = self.client.get(reverse('blood-order') + f'?t={niv.pk}')
        for value in ('ბერიძე ნინო', '01001012345', '12.03.1960', 'ათეროსკლეროზი', '1234/26'):
            self.assertContains(r, f'value="{value}"')
        self.assertContains(self.client.get(reverse('niv-templates')), f'/blood/?t={niv.pk}')

    def test_print_context(self):
        from .blood import print_context
        ctx = print_context({'patient_name': ' ბერიძე ', 'date': '21.11.2026', 'time': '15:40', 'nurse': ''})
        self.assertEqual(ctx['patient_name'], 'ბერიძე')
        self.assertEqual((ctx['day'], ctx['month'], ctx['year'], ctx['time']), ('21', '11', '2026', '15:40'))
        self.assertEqual(ctx['nurse_padded'], ' ' * 51)

    @skipUnless(shutil.which('soffice') or shutil.which('libreoffice'), 'LibreOffice not installed')
    def test_print_pdf_and_remember_doctor(self):
        StaffProfile.objects.create(user=self.user, display_name='ინა')
        r = self.client.post(reverse('blood-order-print'), {
            'patient_name': 'ბერიძე ნინო', 'diagnosis': 'A & B <C>', 'doctor': 'შეროზია ინა',
            'date': '21.11.2026', 'time': '15:40'})
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content) if r.streaming else r.content
        self.assertTrue(body.startswith(b'%PDF'))
        self.assertEqual(StaffProfile.objects.get(user=self.user).full_name, 'შეროზია ინა')

    def test_print_requires_post(self):
        self.assertEqual(self.client.get(reverse('blood-order-print')).status_code, 405)

    def test_profile_full_name(self):
        r = self.client.post(reverse('my-photo'), {'full_name': 'ქავთარაძე მირანდა'})
        self.assertRedirects(r, reverse('niv-cover'))
        self.assertEqual(StaffProfile.objects.get(user=self.user).full_name, 'ქავთარაძე მირანდა')

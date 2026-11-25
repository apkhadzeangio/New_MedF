"""Tests for the NIV-300-9/a forms.

Run:  python manage.py test --settings=MedicalForms.settings.dev
"""
import io
import json
import shutil
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache, caches
from django.http import QueryDict
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .models import DutyNote, Medicine, NivForm, PrescriptionTemplate, StaffProfile
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
        caches['pdf'].clear()
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
            t=niv.pk, action='save_as', **{'patient_name': 'ბერიძე ნინო', 'm1': 'Changed'}))
        niv.refresh_from_db()
        self.assertEqual(niv.m1, 'Aspirin')
        copy = NivForm.objects.exclude(pk=niv.pk).get()
        self.assertEqual(copy.title, 'ბერიძე ნინო')
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

    def test_no_separate_name_patient_name_is_used(self):
        data = self.post_data(patient_name='  ბერიძე   ნინო ', diagnosis='ანევრიზმა')
        del data['title']
        self.client.post(reverse('niv-create'), data)
        niv = NivForm.objects.get()
        self.assertEqual(niv.title, 'ბერიძე ნინო')
        # renaming the patient renames the prescription
        self.client.post(reverse('niv-create'), dict(data, t=niv.pk, action='save', patient_name='კაპანაძე გიორგი'))
        niv.refresh_from_db()
        self.assertEqual(niv.title, 'კაპანაძე გიორგი')
        # no name: the diagnosis, then the old name, then a dated name
        self.client.post(reverse('niv-create'), dict(data, t=niv.pk, action='save', patient_name=''))
        niv.refresh_from_db()
        self.assertEqual(niv.title, 'ანევრიზმა')
        self.client.post(reverse('niv-create'), dict(data, patient_name='', diagnosis=''))
        self.assertTrue(NivForm.objects.latest('id').title.startswith('დანიშნულება '))

    def test_page_has_no_name_dialogs(self):
        page = self.client.get(reverse('niv-cover'))
        self.assertNotContains(page, 'id="saveModal"')
        self.assertNotContains(page, 'დასახელება აუცილებელია')
        self.assertContains(page, 'id="updateBtn"')
        self.assertNotContains(page, 'id="saveAsBtn"')                 # nothing to copy yet
        niv = self.make_niv()
        self.assertContains(self.client.get(reverse('niv-cover') + f'?t={niv.pk}'), 'id="saveAsBtn"')


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
        self.assertNotIn('Content-Encoding', r)            # the browser saves the .gz as it is
        self.assertIn('attachment; filename="medf-backup-', r['Content-Disposition'])
        import gzip
        data = json.loads(gzip.decompress(b''.join(r.streaming_content)))
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

    def test_staff_avatar(self):
        from apps.mdf import avatars
        avatars._static_file.cache_clear()
        ina = User.objects.create_user('ina', password='x')
        profile = StaffProfile.objects.create(user=ina, display_name='ინა')
        self.assertIn('img/staff/ina.', profile.avatar_url)
        self.assertContains(self.client.post('/logout/', follow=True), 'img/staff/ina.')


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
        ctx = print_context({'patient_name': ' ბერიძე ', 'date': '21.11.2026', 'time': '15:40'})
        self.assertEqual(ctx['patient_name'], 'ბერიძე')
        self.assertEqual((ctx['day'], ctx['month'], ctx['year'], ctx['time']), ('21', '11', '2026', '15:40'))

    def test_print_page_and_remember_doctor(self):
        StaffProfile.objects.create(user=self.user, display_name='ინა')
        r = self.client.post(reverse('blood-order-print'), {
            'patient_name': 'ბერიძე ნინო', 'diagnosis': 'A & B <C>', 'doctor': 'შეროზია ინა',
            'rbc': '2', 'date': '21.11.2026', 'time': '15:40'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ბერიძე ნინო')
        self.assertContains(r, 'A &amp; B &lt;C&gt;')
        self.assertContains(r, 'size: A4 portrait')
        self.assertContains(r, '>21</span>')
        self.assertEqual(StaffProfile.objects.get(user=self.user).full_name, 'შეროზია ინა')

    def test_print_requires_post(self):
        self.assertEqual(self.client.get(reverse('blood-order-print')).status_code, 405)

    def test_profile_full_name(self):
        r = self.client.post(reverse('my-photo'), {'full_name': 'ქავთარაძე მირანდა'})
        self.assertRedirects(r, reverse('niv-cover'))
        self.assertEqual(StaffProfile.objects.get(user=self.user).full_name, 'ქავთარაძე მირანდა')


class DutyNoteTests(BaseViewTest):
    """On-duty doctor's notes ("მორიგე ექიმის ჩანაწერი")."""

    @staticmethod
    def at(text):
        import datetime
        from zoneinfo import ZoneInfo
        return datetime.datetime.strptime(text, '%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo('Asia/Tbilisi'))

    def test_duty_date_rule(self):
        import datetime
        from .duty import duty_date_for
        d = datetime.date
        cases = {
            '21.11.2026 00:00': d(2026, 11, 21),
            '21.11.2026 09:00': d(2026, 11, 21),
            '21.11.2026 16:59': d(2026, 11, 21),
            '21.11.2026 17:00': d(2026, 11, 22),
            '21.11.2026 23:30': d(2026, 11, 22),
            '21.11.2026 23:59': d(2026, 11, 22),
            '22.11.2026 08:00': d(2026, 11, 22),
            '30.11.2026 17:00': d(2026, 12, 1),      # end of month
            '31.12.2026 16:59': d(2026, 12, 31),
            '31.12.2026 17:00': d(2027, 1, 1),       # end of year
            '28.02.2027 18:00': d(2027, 3, 1),       # February
            '28.02.2028 18:00': d(2028, 2, 29),      # leap year
        }
        for moment, expected in cases.items():
            self.assertEqual(duty_date_for(self.at(moment)), expected, moment)
        # the same moment given in UTC: 13:00 UTC = 17:00 Tbilisi
        utc = datetime.datetime(2026, 11, 21, 13, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(duty_date_for(utc), d(2026, 11, 22))
        utc = datetime.datetime(2026, 11, 21, 12, 59, tzinfo=datetime.timezone.utc)
        self.assertEqual(duty_date_for(utc), d(2026, 11, 21))

    def create(self, moment, **data):
        from unittest import mock
        with mock.patch('django.utils.timezone.now', return_value=self.at(moment)):
            return self.client.post(reverse('duty-new'), data)

    def test_create_edit_keeps_duty_date(self):
        import datetime
        from unittest import mock
        r = self.create('21.11.2026 17:00', patient='ბერიძე ნინო, 3780/26', body='<p>ტემპ. 38.2</p><p>ღამე მშვიდად</p>')
        note = DutyNote.objects.get()
        self.assertRedirects(r, reverse('duty-note', args=[note.pk]) + '?saved=1')
        self.assertEqual(note.duty_date, datetime.date(2026, 11, 22))
        self.assertEqual(note.created_at, self.at('21.11.2026 17:00'))
        self.assertEqual(note.created_by, self.user)
        # edited the next day after 17:00: the duty date must not move
        with mock.patch('django.utils.timezone.now', return_value=self.at('22.11.2026 18:30')):
            self.client.post(reverse('duty-note', args=[note.pk]), {'patient': 'ბერიძე ნინო', 'body': '<p>შეცვლილი</p>'})
        note.refresh_from_db()
        self.assertEqual(note.duty_date, datetime.date(2026, 11, 22))
        self.assertEqual(note.body, '<p>შეცვლილი</p>')
        self.assertEqual(note.created_at, self.at('21.11.2026 17:00'))

    def test_many_patients_one_duty_and_listing(self):
        import datetime
        self.create('21.11.2026 18:00', patient='პაციენტი ა', body='ტექსტი ა')
        self.create('22.11.2026 02:00', patient='პაციენტი ბ', body='ტექსტი ბ')
        self.create('22.11.2026 16:59', patient='', body='<p>უსათაურო</p>')
        self.create('22.11.2026 17:00', patient='პაციენტი გ', body='ტექსტი გ')
        days = dict(DutyNote.objects.values_list('patient', 'duty_date'))
        self.assertEqual(days['პაციენტი ა'], datetime.date(2026, 11, 22))
        self.assertEqual(days['პაციენტი ბ'], datetime.date(2026, 11, 22))
        self.assertEqual(days[''], datetime.date(2026, 11, 22))
        self.assertEqual(days['პაციენტი გ'], datetime.date(2026, 11, 23))

        r = self.client.get(reverse('duty-index'))
        self.assertContains(r, '/duty/2026-11-22/')
        self.assertContains(r, '/duty/2026-11-23/')
        self.assertContains(r, '3 ჩანაწერი')
        cards = [c['iso'] for c in r.context['cards']]
        self.assertEqual(cards, ['2026-11-23', '2026-11-22'])                  # newest first
        r = self.client.get(reverse('duty-index') + '?order=old')
        self.assertEqual([c['iso'] for c in r.context['cards']], ['2026-11-22', '2026-11-23'])

        r = self.client.get(reverse('duty-day', args=['2026-11-22']))
        self.assertEqual(len(r.context['notes']), 3)
        for text in ('პაციენტი ა', 'პაციენტი ბ', 'უსათაურო ჩანაწერი', '21.11.2026 18:00'):
            self.assertContains(r, text)
        self.assertNotContains(r, 'პაციენტი გ')

        # date search and text search
        self.assertRedirects(self.client.get(reverse('duty-index') + '?date=2026-11-22'),
                             reverse('duty-day', args=['2026-11-22']), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse('duty-index') + '?date=22.11.2026'),
                             reverse('duty-day', args=['2026-11-22']), fetch_redirect_response=False)
        r = self.client.get(reverse('duty-index') + '?q=ტექსტი ბ')
        self.assertEqual([n.patient for n in r.context['found']], ['პაციენტი ბ'])

    def test_html_is_cleaned(self):
        self.create('21.11.2026 10:00', patient='x', body='<p onclick="evil()">ა<b>ბ</b><script>alert(1)</script>'
                                                         '<img src=x onerror=alert(2)><a href="javascript:x">ბმული</a></p>')
        note = DutyNote.objects.get()
        self.assertEqual(note.body, '<p>ა<b>ბ</b>ბმული</p>')
        r = self.client.get(reverse('duty-note-print', args=[note.pk]))
        self.assertNotContains(r, '<script>alert')
        self.assertNotContains(r, 'onerror')

    def test_emoji_removed(self):
        self.create('21.11.2026 10:00', patient='ნინო 😊', body='<p>კარგად 👍 არის</p>')
        note = DutyNote.objects.get()
        self.assertEqual((note.patient, note.body), ('ნინო', '<p>კარგად  არის</p>'))

    def test_empty_note_is_not_saved(self):
        r = self.create('21.11.2026 10:00', patient='  ', body='<p><br></p>')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ჩანაწერი ცარიელია')
        self.assertFalse(DutyNote.objects.exists())

    def test_print_single_and_whole_day(self):
        long_text = ''.join(f'<p>აბზაცი {i}: ' + 'ძალიან გრძელი ტექსტი ' * 40 + '</p>' for i in range(30))
        self.create('21.11.2026 20:00', patient='ბერიძე ნინო', body=long_text)
        self.create('21.11.2026 21:00', patient='კაპანაძე ნანა', body='<p>მეორე</p>')
        a, b = DutyNote.objects.order_by('created_at')
        r = self.client.get(reverse('duty-note-print', args=[a.pk]))
        self.assertContains(r, 'მორიგე ექიმის ჩანაწერი')
        self.assertContains(r, '22.11.2026')
        self.assertContains(r, 'ბერიძე ნინო')
        self.assertContains(r, 'აბზაცი 29')
        self.assertContains(r, 'size: A4 portrait')
        self.assertNotContains(r, 'app-nav')                 # no site menu on paper
        r = self.client.get(reverse('duty-day-print', args=['2026-11-22']))
        self.assertContains(r, 'ბერიძე ნინო')
        self.assertContains(r, 'კაპანაძე ნანა')
        self.assertContains(r, 'class="note"', count=2)
        self.assertEqual(self.client.get(reverse('duty-day-print', args=['2030-01-01'])).status_code, 404)

    def test_login_required(self):
        self.client.logout()
        for url in (reverse('duty-index'), reverse('duty-new'), reverse('duty-day', args=['2026-11-22'])):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_backup_includes_notes(self):
        import gzip
        self.create('21.11.2026 10:00', patient='x', body='y')
        data = json.loads(gzip.decompress(b''.join(self.client.get(reverse('backup-download')).streaming_content)))
        self.assertTrue(any(o['model'] == 'mdf.dutynote' for o in data))


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
        from .models import BoardPatient
        return BoardPatient.objects.get(name=data['name'])



class BoardTests(BoardBase):
    """Patients board: ICU / ward, tasks, state, duty report (kept one month)."""

    def test_login_required(self):
        self.client.logout()
        for name in ('board', 'board-patient-new', 'board-discharged', 'board-report', 'board-reports'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('?next=', response['Location'])

    def test_add_patient_shows_on_board_in_its_unit(self):
        from .models import BoardEntry
        p = self.add_patient()
        self.add_patient(name='კაპანაძე გიორგი', bed='12', unit='ward')
        self.assertEqual(p.unit, 'icu')
        self.assertEqual(p.doctor, self.user)
        self.assertTrue(BoardEntry.objects.filter(patient=p, kind='event').exists())
        page = self.client.get(reverse('board'))
        self.assertContains(page, 'ბერიძე ნინო')
        self.assertContains(page, 'კაპანაძე გიორგი')
        icu, ward = page.context['columns']
        self.assertEqual([x.name for x in icu['patients']], ['ბერიძე ნინო'])
        self.assertEqual([x.name for x in ward['patients']], ['კაპანაძე გიორგი'])
        # name is required
        response = self.client.post(reverse('board-patient-new'), {'name': '  ', 'unit': 'ward'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ჩაწერეთ პაციენტის სახელი')

    def test_beds_in_natural_order(self):
        for bed in ('10', '2', '', '1ა'):
            self.add_patient(name=f'პაციენტი {bed or "უსაწოლო"}', bed=bed, unit='ward')
        ward = self.client.get(reverse('board')).context['columns'][1]
        self.assertEqual([x.bed for x in ward['patients']], ['1ა', '2', '10', ''])

    def test_attending_gives_task_duty_marks_done(self):
        from .models import BoardEntry
        p = self.add_patient()
        # the attending doctor gets "task" preselected
        self.assertEqual(self.client.get(reverse('board-patient', args=[p.pk])).context['kind'], 'task')
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'task', 'text': '22:00 სისხლის ანალიზი'})
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'comment', 'text': 'ხვალ ოპერაცია'})
        task = BoardEntry.objects.get(kind='task')
        self.assertEqual(task.created_by, self.user)
        self.assertIsNotNone(task.created_at)
        self.assertContains(self.client.get(reverse('board')), '22:00 სისხლის ანალიზი')

        # the duty doctor: "state" preselected, writes state, marks the task done
        self.client.force_login(self.duty)
        self.assertEqual(self.client.get(reverse('board-patient', args=[p.pk])).context['kind'], 'status')
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': 'სტაბილური\nAD 130/80'})
        self.client.post(reverse('board-task-done', args=[task.pk]), {'next': reverse('board')})
        task.refresh_from_db()
        self.assertEqual(task.done_by, self.duty)
        self.assertIsNotNone(task.done_at)
        board = self.client.get(reverse('board'))
        self.assertNotContains(board, '22:00 სისხლის ანალიზი')         # done tasks leave the card
        self.assertContains(board, 'სტაბილური<br>AD 130/80')
        # undo (clicked by mistake)
        self.client.post(reverse('board-task-done', args=[task.pk]), {'undo': '1'})
        task.refresh_from_db()
        self.assertIsNone(task.done_at)

    def test_only_author_updates_entry_and_change_time_is_kept(self):
        from .models import BoardEntry
        p = self.add_patient()
        self.client.force_login(self.duty)
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': 'ძველი'})
        entry = BoardEntry.objects.get(kind='status')
        created = entry.created_at
        self.client.post(reverse('board-entry-edit', args=[entry.pk]), {'text': 'ახალი 🙂'})
        entry.refresh_from_db()
        self.assertEqual(entry.text, 'ახალი')                          # emoji dropped (old MySQL)
        self.assertEqual(entry.created_at, created)
        self.assertEqual(entry.updated_by, self.duty)
        self.assertIsNotNone(entry.updated_at)
        self.client.force_login(self.user)                               # not the author
        self.client.post(reverse('board-entry-edit', args=[entry.pk]), {'text': 'სხვისი'})
        entry.refresh_from_db()
        self.assertEqual(entry.text, 'ახალი')

    def test_text_is_escaped_and_empty_text_not_saved(self):
        from .models import BoardEntry
        p = self.add_patient()
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': '   '})
        self.assertFalse(BoardEntry.objects.filter(kind='status').exists())
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': '<script>alert(1)</script>'})
        page = self.client.get(reverse('board-patient', args=[p.pk]))
        self.assertNotContains(page, '<script>alert(1)')
        self.assertContains(page, '&lt;script&gt;')

    def test_transfer_discharge_restore(self):
        from .models import BoardEntry
        p = self.add_patient()
        self.client.post(reverse('board-patient-move', args=[p.pk, 'transfer']))
        p.refresh_from_db()
        self.assertEqual(p.unit, 'ward')
        self.assertTrue(BoardEntry.objects.filter(patient=p, kind='event', text__contains='→ სტაციონარი').exists())
        self.client.post(reverse('board-patient-move', args=[p.pk, 'discharge']))
        p.refresh_from_db()
        self.assertIsNotNone(p.discharged_at)
        self.client.get(reverse('board'))                                  # shows the flash message
        self.assertNotContains(self.client.get(reverse('board')), 'ბერიძე ნინო')
        self.assertContains(self.client.get(reverse('board-discharged') + '?q=ბერიძე'), 'ბერიძე ნინო')
        # nothing can be written on a discharged patient
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': 'x'})
        self.assertFalse(BoardEntry.objects.filter(patient=p, kind='status').exists())
        self.client.post(reverse('board-patient-move', args=[p.pk, 'restore']))
        p.refresh_from_db()
        self.assertIsNone(p.discharged_at)
        self.assertContains(self.client.get(reverse('board')), 'ბერიძე ნინო')

    def test_report_takes_only_this_duty_and_is_frozen(self):
        import datetime
        from unittest import mock
        from .board import build_report
        from .models import BoardEntry, DutyReport
        with mock.patch('django.utils.timezone.now', return_value=self.at('20.11.2026 10:00')):
            p = self.add_patient()
            BoardEntry.objects.create(patient=p, kind='status', text='წინა მორიგეობის', created_by=self.duty,
                                      created_at=self.at('20.11.2026 10:00'))
            old_task = BoardEntry.objects.create(patient=p, kind='task', text='ძველი დავალება', created_by=self.user,
                                                 created_at=self.at('20.11.2026 10:05'))
            self.add_patient(name='კაპანაძე გიორგი', bed='12', unit='ward')
        # duty 21.11: 20.11 17:00 ... 21.11 16:59
        BoardEntry.objects.create(patient=p, kind='status', text='ღამით სტაბილური',
                                  created_by=self.duty, created_at=self.at('20.11.2026 23:10'))
        BoardEntry.objects.create(patient=p, kind='task', text='დილით ანალიზი', created_by=self.user,
                                  created_at=self.at('20.11.2026 18:00'), done_by=self.duty,
                                  done_at=self.at('21.11.2026 08:00'))
        BoardEntry.objects.create(patient=p, kind='status', text='შემდეგი მორიგეობის',
                                  created_by=self.duty, created_at=self.at('21.11.2026 17:30'))
        r = build_report(datetime.date(2026, 11, 21), self.duty)
        icu, ward = r['units']
        row = icu['patients'][0]
        self.assertEqual([s['text'] for s in row['status']], ['ღამით სტაბილური'])
        self.assertEqual(row['status'][0]['by'], 'ფარულავა ია')
        self.assertEqual({t['text']: t['done'] for t in row['tasks']}, {'ძველი დავალება': False, 'დილით ანალიზი': True})
        self.assertEqual(ward['patients'][0]['status'], [])
        self.assertEqual(r['totals'], {'patients': 2, 'tasks': 2, 'tasks_done': 1, 'without_status': 1})
        self.assertEqual(r['duty_doctors'], ['ფარულავა ია'])

        # saving freezes it: later changes do not touch the saved report
        with mock.patch('django.utils.timezone.now', return_value=self.at('21.11.2026 09:00')):
            response = self.client.post(reverse('board-report'), {'date': '2026-11-21'})
            report = DutyReport.objects.get()
            self.assertRedirects(response, reverse('board-report-saved', args=[report.pk]), fetch_redirect_response=False)
            old_task.done_at, old_task.done_by = self.at('21.11.2026 09:30'), self.duty
            old_task.save()
            page = self.client.get(reverse('board-report-saved', args=[report.pk]))
            self.assertContains(page, 'ღამით სტაბილური')
            self.assertContains(page, 'არ შესრულებულა')
            self.assertNotContains(page, 'შემდეგი მორიგეობის')
            printed = self.client.get(reverse('board-report-print', args=[report.pk]))
            self.assertContains(printed, 'მორიგეობის შემაჯამებელი რეპორტი')
            self.assertContains(printed, 'window.print()')
            # making it again refreshes the same report, no duplicate
            self.client.post(reverse('board-report'), {'date': '2026-11-21'})
            self.assertEqual(DutyReport.objects.count(), 1)

    def test_reports_are_deleted_after_a_month(self):
        import datetime
        from unittest import mock
        from .models import DutyReport
        keep = DutyReport.objects.create(duty_date=datetime.date(2026, 11, 1), data={})
        DutyReport.objects.create(duty_date=datetime.date(2026, 10, 21), data={})
        with mock.patch('django.utils.timezone.now', return_value=self.at('21.11.2026 10:00')):
            self.client.get(reverse('board-reports'))
        self.assertEqual(list(DutyReport.objects.all()), [keep])
        self.assertEqual(keep.expires_on, datetime.date(2026, 12, 1))

    def test_mine_filter_and_backup(self):
        self.add_patient()
        self.add_patient(name='სხვისი პაციენტი', doctor=self.duty.pk)
        self.client.get(reverse('board'))                                  # shows the flash message
        page = self.client.get(reverse('board') + '?show=mine')
        self.assertContains(page, 'ბერიძე ნინო')
        self.assertNotContains(page, 'სხვისი პაციენტი')
        from .views import BACKUP_MODELS
        self.assertIn('mdf.boardpatient', BACKUP_MODELS)
        self.assertIn('mdf.boardentry', BACKUP_MODELS)
        self.assertNotIn('mdf.dutyreport', BACKUP_MODELS)

    def test_menu_and_prescription_page_unchanged(self):
        page = self.client.get(reverse('niv-cover'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse('board'))
        self.assertContains(page, 'პაციენტების დაფა')


class BoardLinksTests(BoardBase):
    """Board -> prescription / blood order, "new" marks, A4 board print."""

    def test_prescription_and_blood_are_prefilled_from_the_board(self):
        p = self.add_patient()
        page = self.client.get(reverse('niv-cover') + f'?board={p.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context['niv'].pk)                     # new, nothing saved yet
        self.assertContains(page, 'value="ბერიძე ნინო"')
        self.assertContains(page, 'value="მუცლის აორტის ანევრიზმა"')
        self.assertNotContains(page, reverse('print-niv-cover') + '?t=')
        self.assertEqual(NivForm.objects.count(), 0)
        blood = self.client.get(reverse('blood-order') + f'?board={p.pk}')
        self.assertEqual(blood.context['form'].initial['patient_name'], 'ბერიძე ნინო')
        self.assertEqual(blood.context['form'].initial['diagnosis'], 'მუცლის აორტის ანევრიზმა')
        # a wrong id is ignored
        self.assertIsNone(self.client.get(reverse('niv-cover') + '?board=999').context['niv'])

    def test_new_marks_until_the_patient_is_opened(self):
        p = self.add_patient()
        self.client.force_login(self.duty)
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': 'ღამით სტაბილური'})
        self.client.force_login(self.user)
        card = self.client.get(reverse('board')).context['columns'][0]['patients'][0]
        self.assertEqual(card.new_count, 1)                             # own texts never count
        page = self.client.get(reverse('board-patient', args=[p.pk]))
        self.assertTrue([e for g in page.context['groups'] for e in g['entries'] if e.is_new])
        card = self.client.get(reverse('board')).context['columns'][0]['patients'][0]
        self.assertEqual(card.new_count, 0)
        # "all seen" button
        self.client.force_login(self.duty)
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'status', 'text': 'კიდევ'})
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('board')).context['new_total'], 1)
        self.client.post(reverse('board-seen'))
        self.assertEqual(self.client.get(reverse('board')).context['new_total'], 0)

    def test_board_print(self):
        p = self.add_patient()
        self.client.post(reverse('board-entry-add', args=[p.pk]), {'kind': 'task', 'text': 'ანალიზი 22:00'})
        page = self.client.get(reverse('board-print'))
        self.assertContains(page, 'A4 landscape')
        self.assertContains(page, 'ბერიძე ნინო')
        self.assertContains(page, 'ანალიზი 22:00')
        self.assertContains(page, 'window.print()')


class RemindersAndMedicinesTests(BaseViewTest):
    """Monthly reminders (site expiry, backup) and duplicate medicine names."""

    def reminders(self):
        return [r['key'].split('-')[0] for r in self.client.get(reverse('board')).context['reminders']]

    def test_backup_reminder_until_downloaded(self):
        import datetime
        from unittest import mock
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 11, 10)):
            self.assertEqual(self.reminders(), ['backup'])
            self.client.get(reverse('backup-download'))
            self.assertEqual(self.reminders(), [])

    def test_month_end_reminder_without_api(self):
        import datetime
        from unittest import mock
        self.client.get(reverse('backup-download'))
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 11, 26)):
            page = self.client.get(reverse('board'), HTTP_HOST='medf.pythonanywhere.com')
            self.assertEqual([r['key'].split('-')[0] for r in page.context['reminders']], ['expiry'])

    def test_expiry_from_api_when_available(self):
        import datetime, io, json
        from unittest import mock
        from django.core.cache import cache
        self.client.get(reverse('backup-download'))
        cache.clear()
        answer = io.BytesIO(json.dumps({'expiry': '2026-11-15'}).encode())
        with mock.patch.dict('os.environ', {'API_TOKEN': 'x'}), \
                mock.patch('urllib.request.urlopen', return_value=answer) as urlopen, \
                mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 11, 10)):
            page = self.client.get(reverse('board'), HTTP_HOST='medf.pythonanywhere.com')
            self.assertEqual(urlopen.call_args[0][0].full_url,
                             'https://www.pythonanywhere.com/api/v0/user/medf/webapps/medf.pythonanywhere.com/')
            self.assertContains(page, '15.11.2026')
            self.client.get(reverse('board'), HTTP_HOST='medf.pythonanywhere.com')
            self.assertEqual(urlopen.call_count, 1)                    # cached
        cache.clear()

    def test_no_site_expiry_reminder_on_own_server(self):
        import datetime
        from unittest import mock
        self.client.get(reverse('backup-download'))
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 11, 26)):
            page = self.client.get(reverse('board'), HTTP_HOST='ramaz.ge')
        self.assertEqual(page.context['reminders'], [])

    def test_api_failure_never_breaks_a_page(self):
        from unittest import mock
        from django.core.cache import cache
        cache.clear()
        with mock.patch.dict('os.environ', {'API_TOKEN': 'x'}), \
                mock.patch('urllib.request.urlopen', side_effect=OSError('blocked')):
            page = self.client.get(reverse('niv-cover'), HTTP_HOST='medf.pythonanywhere.com')
        self.assertEqual(page.status_code, 200)
        cache.clear()

    def test_duplicates_hidden_prescriptions_untouched(self):
        from .medicines import name_key
        self.assertEqual(name_key('Aspirin 100mg'), name_key(' aspirin-100 MG.'))
        a = Medicine.objects.create(name='Aspirin 100mg', note='1 ტაბ.')
        b = Medicine.objects.create(name='aspirin 100 mg')
        Medicine.objects.create(name='Heparin')
        niv = self.make_niv(m1='aspirin 100 mg')
        self.assertEqual(self.client.get(reverse('medicine-duplicates')).status_code, 403)   # staff only
        self.user.is_staff = True
        self.user.save()
        page = self.client.get(reverse('medicine-duplicates'))
        (group,) = page.context['groups']
        self.assertEqual(group[0], b)                                   # the one used in a prescription first
        self.client.post(reverse('medicine-duplicates'), {f'keep-{a.pk}-{b.pk}': str(a.pk)})
        a.refresh_from_db(); b.refresh_from_db(); niv.refresh_from_db()
        self.assertTrue(a.is_active)
        self.assertFalse(b.is_active)
        self.assertEqual(niv.m1, 'aspirin 100 mg')
        self.assertEqual(self.client.get(reverse('medicine-duplicates')).context['groups'], [])


class StatsAndAppTests(BoardBase):
    """Monthly statistics from the board; home-screen icon files."""

    def test_month_stats(self):
        import datetime
        from .board import month_stats
        from .models import BoardEntry, BoardPatient
        a = BoardPatient.objects.create(name='ა', unit='icu', doctor=self.user, admitted_at=self.at('02.10.2026 10:00'),
                                        discharged_at=self.at('06.10.2026 10:00'))
        b = BoardPatient.objects.create(name='ბ', unit='ward', doctor=self.user, admitted_at=self.at('20.09.2026 10:00'))
        BoardPatient.objects.create(name='ძველი', unit='ward', admitted_at=self.at('01.08.2026 10:00'),
                                    discharged_at=self.at('10.09.2026 10:00'))
        BoardEntry.objects.create(patient=a, kind='status', text='x', created_by=self.duty, created_at=self.at('02.10.2026 20:00'))
        BoardEntry.objects.create(patient=a, kind='task', text='t', created_by=self.user, created_at=self.at('03.10.2026 09:00'),
                                  done_by=self.duty, done_at=self.at('03.10.2026 11:00'))
        BoardEntry.objects.create(patient=b, kind='task', text='t2', created_by=self.user, created_at=self.at('04.10.2026 09:00'))
        BoardEntry.objects.create(patient=a, kind='event', text='გადაყვანილია: რეანიმაცია → სტაციონარი',
                                  created_by=self.user, created_at=self.at('04.10.2026 12:00'))
        s = month_stats(datetime.date(2026, 10, 1), datetime.date(2026, 11, 1))
        self.assertEqual((s['admitted'], s['admitted_icu'], s['discharged']), (1, 1, 1))
        self.assertAlmostEqual(s['avg_stay'], 4.0)
        self.assertEqual((s['tasks'], s['tasks_done'], s['tasks_pct']), (2, 1, 50))
        self.assertAlmostEqual(s['median_hours'], 2.0)
        self.assertEqual(s['transfers'], 1)
        # 31 duties for "ბ" + 5 for "ა" (duties 02.10 - 06.10 touch 02.10 10:00 - 06.10 10:00); one has a state
        self.assertEqual((s['coverage_written'], s['coverage_needed']), (1, 36))
        self.assertAlmostEqual(s['avg_patients'], (31 + 4) / 31, places=2)
        names = {r['name']: r for r in s['people']}
        self.assertEqual(names['ფარულავა ია']['done'], 1)
        self.assertEqual(names['აფხაძე რამაზი']['tasks'], 2)
        page = self.client.get(reverse('board-stats') + '?month=2026-10')
        self.assertContains(page, 'ოქტომბერი 2026')
        self.assertEqual(self.client.get(reverse('board-stats') + '?month=bad').status_code, 200)

    def test_home_screen_files(self):
        self.client.logout()                            # the phone asks for these before logging in
        manifest = self.client.get(reverse('pwa-manifest')).json()
        self.assertEqual(manifest['start_url'], reverse('board'))
        self.assertEqual(manifest['display'], 'standalone')
        sw = self.client.get(reverse('pwa-sw'))
        self.assertEqual(sw['Content-Type'], 'application/javascript')
        self.assertNotIn('caches', sw.content.decode())          # nothing is stored on the phone


class PrescriptionToBoardTests(BoardBase):
    """"დაფაზე" on the prescription page: only name, bed and diagnosis go over."""

    def test_form_is_prefilled_with_three_fields_only(self):
        page = self.client.get(reverse('board-patient-new'),
                               {'name': ' ბერიძე ნინო ', 'bed': '4', 'diagnosis': 'ანევრიზმა', 'm1': 'Aspirin'})
        self.assertEqual(page.context['values']['name'], 'ბერიძე ნინო')
        self.assertEqual(page.context['values']['bed'], '4')
        self.assertEqual(page.context['values']['diagnosis'], 'ანევრიზმა')
        self.assertEqual(page.context['values']['doctor'], self.user.pk)
        self.assertNotContains(page, 'Aspirin')
        self.assertContains(page, 'მხოლოდ სახელი, პალატა და დიაგნოზი')
        self.assertIsNone(page.context['same'])

    def test_warns_when_already_on_board(self):
        p = self.add_patient()
        page = self.client.get(reverse('board-patient-new'), {'name': 'ბერიძე ნინო'})
        self.assertEqual(page.context['same'], p)
        self.assertContains(page, 'უკვე დაფაზეა')

    def test_button_on_prescription_page(self):
        self.assertContains(self.client.get(reverse('niv-cover')), 'id="to-board"')


class SeasonalTests(BaseViewTest):

    def test_script_is_loaded_and_never_blocks_clicks(self):
        from django.contrib.staticfiles import finders
        self.assertContains(self.client.get(reverse('niv-cover')), 'js/seasonal.js')
        source = open(finders.find('js/seasonal.js'), encoding='utf-8').read()
        self.assertIn('pointer-events:none', source)
        self.assertIn('@media print', source)
        self.assertIn('prefers-reduced-motion', source)


class ConsentTests(BaseViewTest):
    """Informed consent sheets (NIV-300-12/a)."""

    def docx(self, paragraphs):
        """A tiny .docx with (text, bold) paragraphs."""
        import io, zipfile
        from html import escape
        body = ''.join(
            f'<w:p><w:r>{"<w:rPr><w:b/></w:rPr>" if bold else ""}<w:t xml:space="preserve">{escape(t)}</w:t></w:r></w:p>'
            for t, bold in paragraphs)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('word/document.xml', f'<w:document><w:body>{body}</w:body></w:document>')
        buf.seek(0)
        buf.name = 'form.docx'
        return buf

    def test_seeded_sheet_and_list_order(self):
        from .models import ConsentForm
        first = ConsentForm.objects.get(name__startswith='აორტა-ბარძაყის')
        self.assertEqual(first.blocks[0]['left'][0], 'სისხლდენა')
        import importlib
        for mig in ('0022_consent_more', '0023_consent_veins_hybrid', '0024_consent_aorta_amputation',
                    '0025_consent_temporal_crile_phlebectomy', '0026_consent_more_arterial',
                    '0027_consent_sympathectomy_tracheostomy', '0028_consent_femoral_vein_ligation',
                    '0029_consent_av_fistula_upper_limb'):
            for name, _blocks in importlib.import_module(f'apps.mdf.migrations.{mig}').SHEETS:
                self.assertTrue(ConsentForm.objects.filter(name=name).exists(), name)
        other = ConsentForm.objects.create(name='კაროტიდული ენდარტერექტომია', blocks=[])
        for _ in range(3):
            self.client.get(reverse('consent-sheet', args=[other.pk]))
        page = self.client.get(reverse('consent-list'))
        self.assertEqual(page.context['forms'][0].pk, other.pk)                      # most used first
        self.assertContains(page, 'კაროტიდული ენდარტერექტომია')

    def test_sheet_has_fixed_page_one_and_editable_parts(self):
        from .models import ConsentForm
        form = ConsentForm.objects.order_by('id').first()
        page = self.client.get(reverse('consent-sheet', args=[form.pk]))
        self.assertContains(page, 'სამედიცინო დოკუმენტაციის ფორმა NIV-300-12/ა')
        self.assertContains(page, 'პაციენტის წერილობითი ინფორმირებული თანხმობა სამედიცინო')
        self.assertContains(page, 'ასევე ჩემთვის ცნობილია სამედიცინო მომსახურებაზე უარის შემთხვევაში')
        self.assertContains(page, 'id="cs-patient"')
        self.assertContains(page, f'value="{page.context["today"]}"')          # today's date, editable
        self.assertContains(page, 'id="cs-data"')

    def test_save_keeps_last_edit_and_is_cleaned(self):
        import json as _json
        from .models import ConsentForm
        form = ConsentForm.objects.order_by('id').first()
        data = {'name': 'ბარძაყ-მუხლქვეშა შუნტირება 🙂',
                'blocks': [{'title': '<b>გართულებები</b> შუნტირების დროს:', 'left': ['სისხლდენა', '  ', 'ინფექცია'],
                            'right': ['თრომბოზი</script>']}, 'junk']}
        r = self.client.post(reverse('consent-save', args=[form.pk]), _json.dumps(data), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        form.refresh_from_db()
        self.assertEqual(form.name, 'ბარძაყ-მუხლქვეშა შუნტირება')
        self.assertEqual(form.blocks, [{'title': '<b>გართულებები</b> შუნტირების დროს:',
                                        'left': ['სისხლდენა', 'ინფექცია'], 'right': ['თრომბოზი</script>']}])
        page = self.client.get(reverse('consent-sheet', args=[form.pk]))
        self.assertNotContains(page, 'თრომბოზი</script>')                      # json_script escapes it
        self.assertEqual(self.client.post(reverse('consent-save', args=[form.pk]), 'x',
                                          content_type='application/json').status_code, 400)

    def test_import_from_word_takes_only_complications(self):
        from .models import ConsentForm
        f = self.docx([
            ('მე              პაციენტის სახელი                 მივიღე ინფორმაცია', False),
            ('ასევე ჩემთვის ცნობილია სამედიცინო მომსახურებაზე უარის შემთხვევაში მოსალოდნელი შედეგების შესახებ.', False),
            ('გართულებები  საძილე არტერიის   ოპერაციის დროს:', True),
            ('სისხლდენა                         ინსულტი', False),
            ('არტერიული სისტემის   თრომბოზი', False),
            ('ინფექცია', False),
            ('პაციენტს აეხსნა ფინანსური და სოციალური საკითხები, ეცნობა ღირებულების შესახებ.', False),
            ('პაციენტს პასუხი გაეცა ყველა სხვა საინტერესო შეკითხვაზე.', False),
            ('პაციენტს და მის ახლობლებს აეხსნა ფინანსური და სოციალური საკითხები.', False),
            ('მკურნალი ექიმი______', False),
        ])
        r = self.client.post(reverse('consent-new'), {'docx': f})
        new = ConsentForm.objects.latest('id')
        self.assertRedirects(r, reverse('consent-sheet', args=[new.pk]), fetch_redirect_response=False)
        self.assertEqual(new.blocks, [{'title': 'გართულებები საძილე არტერიის ოპერაციის დროს:',
                                       'left': ['სისხლდენა', 'არტერიული სისტემის თრომბოზი', 'ინფექცია'],
                                       'right': ['ინსულტი']}])
        self.assertEqual(new.name, 'საძილე არტერიის ოპერაციის დროს')
        self.assertNotIn('პაციენტის სახელი', str(new.blocks))
        # not the right form -> a message, nothing created
        count = ConsentForm.objects.count()
        self.client.post(reverse('consent-new'), {'docx': self.docx([('სხვა ტექსტი', False)])})
        self.assertEqual(ConsentForm.objects.count(), count)

    def test_import_subheadings_without_bold(self):
        from .models import ConsentForm
        f = self.docx([
            ('ასევე ჩემთვის ცნობილია სამედიცინო მომსახურებაზე უარის შემთხვევაში.', False),
            ('გართულებები ბარძაყის ღრმა არტერიის პლასტიკის დროს:', True),
            ('ადრეული პოსტოპერაციული გართულებები:', False),
            ('სისხლდენა', False),
            ('შორეული პოსტოპერაციული გართულებები:', False),
            ('აროზიული სისხლდენა.', False),
            ('მკურნალი ექიმი ____', False),
        ])
        self.client.post(reverse('consent-new'), {'docx': f})
        self.assertEqual([b['title'] for b in ConsentForm.objects.latest('id').blocks],
                         ['გართულებები ბარძაყის ღრმა არტერიის პლასტიკის დროს:',
                          'ადრეული პოსტოპერაციული გართულებები:', 'შორეული პოსტოპერაციული გართულებები:'])

    def test_import_ends_at_surgeon_signature(self):
        from .models import ConsentForm
        f = self.docx([
            ('ასევე ჩემთვის ცნობილია სამედიცინო მომსახურებაზე უარის შემთხვევაში.', False),
            ('გართულებები ტრაქეოსტომიის დროს:', True),
            ('სისხლდენა', False),
            ('ექიმი ქირურგი: ______ ხელმოწერა ____', False),
            ('ექიმი, რეანიმატოლოგი: ______', False),
        ])
        self.client.post(reverse('consent-new'), {'docx': f})
        self.assertEqual(ConsentForm.objects.latest('id').blocks,
                         [{'title': 'გართულებები ტრაქეოსტომიის დროს:', 'left': ['სისხლდენა'], 'right': []}])

    def test_copy_and_delete(self):
        from .models import ConsentForm
        form = ConsentForm.objects.order_by('id').first()
        self.client.post(reverse('consent-new'), {'copy': form.pk})
        copy = ConsentForm.objects.latest('id')
        self.assertEqual(copy.blocks, form.blocks)
        self.assertTrue(copy.name.endswith('(ასლი)'))
        self.client.post(reverse('consent-delete', args=[copy.pk]))
        self.assertFalse(ConsentForm.objects.filter(pk=copy.pk).exists())

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('consent-list')).status_code, 302)


class ConsentUploadFormatsTests(BaseViewTest):

    def test_empty_file_gets_a_clear_message(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ConsentForm
        count = ConsentForm.objects.count()
        r = self.client.post(reverse('consent-new'), {'docx': SimpleUploadedFile('x.docx', b'')}, follow=True)
        self.assertContains(r, 'ცარიელია')
        self.assertEqual(ConsentForm.objects.count(), count)

    @skipUnless(shutil.which('soffice'), 'LibreOffice is needed for .rtf')
    def test_rtf_is_converted(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ConsentForm
        rtf = (r'{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}'
               r'\pard ასევე ჩემთვის ცნობილია შედეგების შესახებ.\par'
               r'\pard\b გართულებები ტესტის დროს:\b0\par'
               r'\pard სისხლდენა\par'
               r'\pard მკურნალი ექიმი ____\par}')
        # RTF keeps Georgian as \uN escapes
        rtf = ''.join(c if ord(c) < 128 else f'\\u{ord(c)}?' for c in rtf)
        self.client.post(reverse('consent-new'), {'docx': SimpleUploadedFile('x.rtf', rtf.encode())})
        new = ConsentForm.objects.latest('id')
        self.assertEqual(new.blocks, [{'title': 'გართულებები ტესტის დროს:', 'left': ['სისხლდენა'], 'right': []}])


class RosterTests(BaseViewTest):
    """Duty roster: Misha on 2 random days (>=10 apart), the rest shared evenly, weekends too."""

    ROTATION = ['რამაზი', 'ია', 'თეონა', 'გურამი', 'გიგა', 'დიმიტრი', 'თორნიკე']

    def gen(self, y, m, seed=1):
        from .roster import generate
        rows, _ = generate(y, m, seed)
        return rows

    def save(self, y, m, seed=1, **extra):
        return self.client.post(reverse('roster-generate'), dict(y=y, m=m, seed=seed, **extra))

    def test_seeded_people(self):
        from .models import RosterPerson
        rotation = [p.name for p in RosterPerson.objects.filter(in_rotation=True)]
        self.assertEqual(rotation, self.ROTATION)
        misha = RosterPerson.objects.get(name='მიშა')
        self.assertEqual((misha.in_rotation, misha.per_month, misha.min_gap, misha.fixed_weekday), (False, 2, 10, None))

    def test_misha_two_random_days_at_least_10_apart(self):
        seen = set()
        for seed in range(1, 25):
            days = [r['date'] for r in self.gen(2026, 11, seed) if r['person'].name == 'მიშა']
            self.assertEqual(len(days), 2, seed)
            self.assertGreaterEqual((days[1] - days[0]).days, 10, seed)
            seen.add(tuple(days))
        self.assertGreater(len(seen), 5)                              # really random
        self.assertEqual(self.gen(2026, 11, 7), self.gen(2026, 11, 7))  # the same seed -> the same schedule

    def test_every_month_length_equal_duties_and_weekends(self):
        from .roster import georgian_holidays, is_heavy
        for y, m, length in ((2027, 2, 28), (2028, 2, 29), (2026, 11, 30), (2026, 12, 31)):
            for seed in (1, 2, 3):
                rows = self.gen(y, m, seed)
                self.assertEqual(len(rows), length)
                hol = georgian_holidays(y)
                total, heavy = {}, {}
                previous = None
                for r in rows:
                    p = r['person']
                    self.assertIsNotNone(p, (y, m, seed))                   # every day has a doctor
                    self.assertFalse(previous and previous.pk == p.pk, (y, m, seed, r['date']))  # never 2 days in a row
                    previous = p
                    if p.name != 'მიშა':
                        total[p.name] = total.get(p.name, 0) + 1
                        heavy[p.name] = heavy.get(p.name, 0) + is_heavy(r['date'], hol)
                self.assertEqual(set(total), set(self.ROTATION))
                self.assertLessEqual(max(total.values()) - min(total.values()), 1, (y, m, seed))
                self.assertLessEqual(max(heavy.values()) - min(heavy.values()), 1, (y, m, seed))

    def test_weekends_even_out_over_months(self):
        from collections import Counter
        from .models import RosterDay
        from .roster import georgian_holidays, is_heavy
        for i, (y, m) in enumerate(((2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3), (2027, 4))):
            self.save(y, m, seed=100 + i)
        heavy = Counter()
        for d in RosterDay.objects.select_related('person'):
            if d.person.name != 'მიშა':
                heavy[d.person.name] += is_heavy(d.date, georgian_holidays(d.date.year))
        self.assertLessEqual(max(heavy.values()) - min(heavy.values()), 1, dict(heavy))

    def test_manual_change_is_locked_and_kept(self):
        import datetime
        from .models import RosterDay, RosterLog, RosterPerson
        self.save(2026, 11)
        gela = RosterPerson.objects.get(name='გიგა')
        r = self.client.post(reverse('roster-day', args=['2026-11-05']), {'action': 'set', 'person': gela.pk})
        self.assertEqual(r.json(), {'ok': True})
        day = RosterDay.objects.get(date=datetime.date(2026, 11, 5))
        self.assertTrue(day.locked)
        self.assertEqual(day.person, gela)
        self.assertTrue(RosterLog.objects.filter(text__contains='05.11').exists())
        # regenerating (any variant) keeps it
        self.save(2026, 11, seed=2)
        day.refresh_from_db()
        self.assertEqual(day.person, gela)
        rows = self.gen(2026, 11, 3)
        self.assertTrue(rows[4]['locked'])
        self.assertEqual(rows[4]['person'], gela)
        # unlock -> the automatic distribution may change it again
        self.client.post(reverse('roster-day', args=['2026-11-05']), {'action': 'unlock'})
        day.refresh_from_db()
        self.assertFalse(day.locked)

    def test_swap_two_days(self):
        import datetime
        from .models import RosterDay
        self.save(2026, 11)
        a = RosterDay.objects.get(date=datetime.date(2026, 11, 3)).person
        b = RosterDay.objects.get(date=datetime.date(2026, 11, 10)).person
        self.client.post(reverse('roster-day', args=['2026-11-03']), {'action': 'swap', 'other': '2026-11-10'})
        self.assertEqual(RosterDay.objects.get(date=datetime.date(2026, 11, 3)).person, b)
        self.assertEqual(RosterDay.objects.get(date=datetime.date(2026, 11, 10)).person, a)

    def test_preview_saves_nothing_until_confirmed(self):
        from .models import RosterDay
        page = self.client.get(reverse('roster-generate') + '?y=2026&m=11')
        self.assertContains(page, 'განრიგის დადასტურება')
        self.assertContains(page, 'სხვა ვარიანტი')
        self.assertFalse(RosterDay.objects.exists())
        # what is saved is exactly the preview the doctor saw
        seed = page.context['seed']
        preview = [(r['date'], r['person'].pk) for r in page.context['rows']]
        self.save(2026, 11, seed=seed)
        self.assertEqual([(d.date, d.person_id) for d in RosterDay.objects.order_by('date')], preview)

    def test_month_page_and_holidays(self):
        import datetime
        from .roster import georgian_holidays
        h = georgian_holidays(2026)
        self.assertEqual(h[datetime.date(2026, 4, 12)], 'აღდგომა')
        self.assertEqual(h[datetime.date(2026, 11, 23)], 'გიორგობა')
        self.save(2026, 11)
        for view in ('calendar', 'table'):
            page = self.client.get(reverse('roster') + f'?y=2026&m=11&view={view}')
            self.assertContains(page, 'გიორგობა')
            self.assertContains(page, 'ყველა დღე დაფარულია')
        self.assertEqual(self.client.get(reverse('roster') + '?y=abc&m=99').status_code, 200)

    def test_print_has_only_the_title_and_table(self):
        self.save(2026, 11)
        page = self.client.get(reverse('roster-print') + '?y=2026&m=11')
        html = page.content.decode()
        self.assertIn('ქირურგიული მორიგეობის განრიგი', html)
        self.assertIn('ნოემბერი 2026', html)
        self.assertEqual(html.count('<tr class='), 30)
        self.assertNotIn('app-nav', html)
        self.assertIn('size: A4 portrait', html)
        self.assertIn('window.print()', html)
        land = self.client.get(reverse('roster-print') + '?y=2026&m=11&orient=landscape&preview=1').content.decode()
        self.assertIn('size: A4 landscape', land)
        self.assertNotIn('window.print(); }, 150', land)

    def test_people_edit_and_login_required(self):
        from .models import RosterPerson
        p = RosterPerson.objects.get(name='თეონა')
        data = {f'p{x.pk}-{k}': v for x in RosterPerson.objects.all() for k, v in (
            ('name', x.name), ('order', x.order), ('weekday', '' if x.fixed_weekday is None else x.fixed_weekday),
            ('weeks', x.fixed_weeks), ('active', 'on'), *((('rotation', 'on'),) if x.in_rotation else ()))}
        data[f'p{p.pk}-active'] = ''
        data['new-name'] = 'ლევანი'
        self.client.post(reverse('roster-people'), data)
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertTrue(RosterPerson.objects.filter(name='ლევანი', in_rotation=True).exists())
        names = [r['person'].name for r in self.gen(2026, 11)]
        self.assertNotIn('თეონა', names)
        self.assertIn('ლევანი', names)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('roster')).status_code, 302)


class RosterBorderTests(BaseViewTest):
    """The previous month counts: no two days in a row across the border, Misha's gap too."""

    def save(self, y, m, seed):
        self.client.post(reverse('roster-generate'), {'y': y, 'm': m, 'seed': seed})

    def test_no_back_to_back_across_the_month_border(self):
        import datetime
        from .models import RosterDay
        for seed in range(1, 21):
            RosterDay.objects.all().delete()
            self.save(2026, 11, seed)
            self.save(2026, 12, seed + 100)
            last_nov = RosterDay.objects.get(date=datetime.date(2026, 11, 30)).person
            first_dec = RosterDay.objects.get(date=datetime.date(2026, 12, 1)).person
            self.assertNotEqual(last_nov, first_dec, seed)

    def test_misha_gap_counts_from_previous_month(self):
        import datetime
        from .models import RosterDay, RosterPerson
        misha = RosterPerson.objects.get(name='მიშა')
        RosterDay.objects.create(date=datetime.date(2026, 11, 28), person=misha, locked=True, source='manual')
        for seed in range(1, 21):
            from .roster import generate
            rows, _ = generate(2026, 12, seed)
            days = [r['date'] for r in rows if r['person'] == misha]
            self.assertEqual(len(days), 2, seed)
            self.assertGreaterEqual((days[0] - datetime.date(2026, 11, 28)).days, 10, seed)   # not before 8 Dec
            self.assertGreaterEqual((days[1] - days[0]).days, 10, seed)


class RosterNeverAdjacentTests(BaseViewTest):
    """Ia and Tornike never hand over to each other (rule kept in the code only)."""

    PAIR = {'ია', 'თორნიკე'}

    def assert_apart(self, rows, msg):
        import datetime
        by_day = {r['date']: r['person'] for r in rows}
        for day, person in by_day.items():
            nxt = by_day.get(day + datetime.timedelta(days=1))
            if person and nxt:
                self.assertNotEqual({person.name, nxt.name}, self.PAIR, f'{msg} {day}')
                self.assertNotEqual(person, nxt, f'{msg} {day}')     # still no two days in a row

    def test_never_adjacent_in_a_month(self):
        from .roster import generate
        for y, m in [(2026, 11), (2026, 12), (2027, 2), (2027, 5), (2027, 8)]:
            for seed in range(1, 31):
                rows, _ = generate(y, m, seed)
                self.assertTrue(all(r['person'] for r in rows))
                self.assert_apart(rows, f'{y}-{m} seed {seed}')

    def test_never_adjacent_across_the_month_border(self):
        import datetime
        from .models import RosterDay
        for seed in range(1, 16):
            RosterDay.objects.all().delete()
            for i, (y, m) in enumerate([(2026, 12), (2027, 1), (2027, 2)]):
                self.client.post(reverse('roster-generate'), {'y': y, 'm': m, 'seed': seed * 10 + i})
            rows = [{'date': d.date, 'person': d.person} for d in RosterDay.objects.order_by('date')]
            self.assertEqual(len(rows), 31 + 31 + 28)
            self.assert_apart(rows, f'seed {seed}')

    def test_when_the_one_pass_placement_gets_stuck_the_search_still_keeps_every_rule(self):
        import collections
        from unittest import mock
        from . import roster
        for y, m in [(2026, 12), (2027, 1), (2027, 11)]:
            for seed in range(1, 11):
                normal, _ = roster.generate(y, m, seed)
                with mock.patch.object(roster, '_place', return_value=None):     # greedy always stuck
                    rows, _ = roster.generate(y, m, seed)
                self.assertTrue(all(r['person'] for r in rows))
                self.assert_apart(rows, f'{y}-{m} seed {seed}')
                order = [p.name for p in roster.rotation_people()]
                seq = [r['person'].name for r in rows if r['person'].name in order]
                self.assertFalse(all(seq[i] == order[i % len(order)] for i in range(len(seq))),
                                 'fell back to filling in turn')
                # the same number of duties and of weekend duties per doctor as the normal pass
                hol = roster.georgian_holidays(y)
                count = lambda rr: collections.Counter(
                    (r['person'].name, roster.is_heavy(r['date'], hol)) for r in rr)
                self.assertEqual(count(rows), count(normal), f'{y}-{m} seed {seed}')

    def test_impossible_month_relaxes_instead_of_filling_in_turn(self):
        import datetime
        from .models import RosterDay, RosterPerson
        from . import roster
        ia = RosterPerson.objects.get(name='ია')
        # many hand-locked days for one doctor: the quotas cannot be kept exactly
        for day in (1, 3, 5, 7, 9, 11, 13, 15):
            RosterDay.objects.create(date=datetime.date(2027, 3, day), person=ia, locked=True, source='manual')
        rows, _ = roster.generate(2027, 3, 5)
        self.assertTrue(all(r['person'] for r in rows))
        for a, b in zip(rows, rows[1:]):
            self.assertNotEqual(a['person'], b['person'], a['date'])

    def test_rule_is_not_shown_on_the_site(self):
        import datetime
        for url in (reverse('roster') + '?y=2026&m=12', reverse('roster-generate') + '?y=2026&m=12&seed=3'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, 'NEVER_ADJACENT')
            self.assertNotContains(r, 'ერთმანეთის')


class QuoteTests(BaseViewTest):
    """Quote of the day on the prescription page."""

    def setUp(self):
        super().setUp()
        from .models import Quote
        self.assertEqual(Quote.objects.count(), 50)      # the first 50 come with the site
        Quote.objects.all().delete()

    LINES = ('1 | პირველი ციტატის ტექსტი | ავტორი ერთი | https://example.ge/a\n'
             '2 | „მეორე ციტატის ტექსტი“ | ავტორი ორი | https://example.ge/b\n'
             'ეს სტრიქონი არ გაირჩევა\n'
             '3 | პირველი ციტატის ტექსტი | ავტორი ერთი |\n'
             '| მესამე ციტატა ნომრის გარეშე | ავტორი სამი')

    def test_import_list(self):
        from .models import Quote
        r = self.client.post(reverse('quotes'), {'action': 'import', 'lines': self.LINES}, follow=True)
        self.assertEqual(Quote.objects.count(), 3)                  # the duplicate is skipped
        q = Quote.objects.get(author='ავტორი ორი')
        self.assertEqual(q.text, 'მეორე ციტატის ტექსტი')             # Georgian quotation marks removed
        self.assertEqual(q.source, 'https://example.ge/b')
        self.assertContains(r, 'დაემატა 3 ციტატა')
        self.assertContains(r, '1 უკვე იყო')
        self.assertContains(r, 'ვერ გავარჩიე')

    def test_one_quote_a_day_in_turn(self):
        import datetime
        from .models import Quote
        from .quotes import quote_of_the_day
        for i in range(5):
            Quote.objects.create(text=f'ციტატა {i}', author='ავტორი')
        day = datetime.date(2026, 11, 22)
        seen = [quote_of_the_day(day + datetime.timedelta(days=k)).pk for k in range(5)]
        self.assertEqual(len(set(seen)), 5)                          # no repeats within 5 days
        self.assertEqual(quote_of_the_day(day).pk, quote_of_the_day(day + datetime.timedelta(days=5)).pk)

    def test_shown_on_prescription_page_only_when_there_are_quotes(self):
        from .models import Quote
        page = self.client.get(reverse('niv-cover'))
        self.assertNotContains(page, 'დღის ციტატა')
        Quote.objects.create(text='ციტატა <b>ტესტი</b>', author='ავტორი')
        page = self.client.get(reverse('niv-cover'))
        self.assertContains(page, 'დღის ციტატა')
        self.assertContains(page, 'ციტატა &lt;b&gt;ტესტი&lt;/b&gt;')    # escaped
        self.assertContains(page, 'rx-quote d-print-none')

    def test_edit_hide_delete(self):
        from .models import Quote
        q = Quote.objects.create(text='ძველი', author='ა')
        self.client.post(reverse('quotes'), {'action': 'save', 'id': q.pk, 'text': 'ახალი', 'author': 'ბ'})
        q.refresh_from_db()
        self.assertEqual((q.text, q.author, q.is_active), ('ახალი', 'ბ', False))
        from .quotes import quote_of_the_day
        self.assertIsNone(quote_of_the_day())                          # hidden ones are not shown
        self.client.post(reverse('quotes'), {'action': 'delete', 'id': q.pk})
        self.assertFalse(Quote.objects.exists())


class RefineBatch1Tests(BaseViewTest):
    """Small fixes found in the code review (each failed before its fix)."""

    def test_roster_people_bad_order_value_is_not_an_error(self):            # A5
        from .models import RosterPerson
        p = RosterPerson.objects.get(name='ია')
        data = {f'p{x.pk}-{k}': v for x in RosterPerson.objects.all() for k, v in (
            ('name', x.name), ('order', x.order), ('min_gap', x.min_gap), ('active', 'on'))}
        data[f'p{p.pk}-order'] = 'abc'
        r = self.client.post(reverse('roster-people'), data)
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.order, 2)                                          # kept as it was

    def test_board_next_only_to_own_pages(self):                              # A7
        from .board import back_to
        from django.test import RequestFactory
        rf = RequestFactory()
        for bad in ('/\\evil.com', '//evil.com', 'https://evil.com', '/\\/evil.com'):
            req = rf.post('/', {'next': bad})
            self.assertEqual(back_to(req, '/board/'), '/board/', bad)
        self.assertEqual(back_to(rf.post('/', {'next': '/board/?show=mine'}), '/x/'), '/board/?show=mine')

    def test_consent_import_refuses_huge_document(self):                       # A9
        import io, zipfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ConsentForm
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('word/document.xml', '<w:document>' + ' ' * (25 * 1024 * 1024) + '</w:document>')
        count = ConsentForm.objects.count()
        r = self.client.post(reverse('consent-new'), {'docx': SimpleUploadedFile('big.docx', buf.getvalue())}, follow=True)
        self.assertEqual(ConsentForm.objects.count(), count)
        self.assertContains(r, 'ძალიან დიდია')

    def test_mark_seen_twice_keeps_one_row(self):                              # A4
        from .board import mark_seen
        from .models import BoardPatient, BoardSeen
        p = BoardPatient.objects.create(name='ტესტი')
        self.assertIsNone(mark_seen(self.user, p.pk))
        self.assertIsNotNone(mark_seen(self.user, p.pk))
        self.assertEqual(BoardSeen.objects.filter(user=self.user, patient=p).count(), 1)

    def test_old_reports_purged_once_a_day(self):                              # B2
        import datetime
        from .board import purge_old_reports
        from .models import DutyReport
        old = datetime.date(2020, 1, 1)
        purge_old_reports()
        DutyReport.objects.create(duty_date=old, data={})
        purge_old_reports()                       # the same day: not again
        self.assertTrue(DutyReport.objects.filter(duty_date=old).exists())
        purge_old_reports(force=True)             # the daily backup task
        self.assertFalse(DutyReport.objects.filter(duty_date=old).exists())


class RefineBatch3Tests(BaseViewTest):

    def test_a8_logout_needs_post(self):
        page = self.client.get('/logout/')              # a link/bookmark only asks
        self.assertContains(page, 'method="post"')
        self.assertIn('_auth_user_id', self.client.session)
        home = self.client.get('/', follow=True)
        self.assertContains(home, 'action="/logout/"')  # the menu posts
        self.client.post('/logout/')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a6_roster_confirm_saves_only_what_was_previewed(self):
        import datetime as dt
        from .models import RosterDay, RosterPerson
        page = self.client.get(reverse('roster-generate'), dict(y=2026, m=12, seed=5))
        fp = page.context['fingerprint']
        # meanwhile someone fixes 10 December by hand
        other = RosterPerson.objects.get(name='გიგა')
        RosterDay.objects.create(date=dt.date(2026, 12, 10), person=other, locked=True, source='manual')
        resp = self.client.post(reverse('roster-generate'), dict(y=2026, m=12, seed=5, fp=fp))
        self.assertRedirects(resp, reverse('roster-generate') + '?y=2026&m=12&seed=5', fetch_redirect_response=False)
        self.assertEqual(RosterDay.objects.filter(date__year=2026, date__month=12).count(), 1)   # nothing saved
        # the fresh preview saves fine
        fp = self.client.get(reverse('roster-generate'), dict(y=2026, m=12, seed=5)).context['fingerprint']
        self.client.post(reverse('roster-generate'), dict(y=2026, m=12, seed=5, fp=fp))
        self.assertEqual(RosterDay.objects.filter(date__year=2026, date__month=12).count(), 31)
        self.assertEqual(RosterDay.objects.get(date=dt.date(2026, 12, 10)).person, other)

    def test_a3_parallel_edit_keeps_both_versions(self):
        niv = self.make_niv(patient_name='ბერიძე ნინო', m1='Aspirin')
        page = self.client.get(reverse('niv-cover'), {'t': niv.pk})
        version = niv.version
        self.assertContains(page, f'value="{version}" name="v"')
        # a colleague saves first
        ina = User.objects.create_user('ina', password='x')
        other = Client()
        other.force_login(ina)
        other.post(reverse('niv-create'), dict(t=niv.pk, v=version, action='save', patient_name='ბერიძე ნინო', m1='Heparin'))
        niv.refresh_from_db()
        self.assertEqual((niv.m1, niv.updated_by), ('Heparin', ina))
        # now this doctor saves the old page: the colleague's version stays, a copy is made
        r = self.client.post(reverse('niv-create'), dict(t=niv.pk, v=version, action='save', patient_name='ბერიძე ნინო', m1='Clopidogrel'))
        copy = NivForm.objects.exclude(pk=niv.pk).get()
        self.assertRedirects(r, f"{reverse('niv-cover')}?t={copy.pk}", fetch_redirect_response=False)
        niv.refresh_from_db()
        self.assertEqual((niv.m1, copy.m1, copy.updated_by), ('Heparin', 'Clopidogrel', self.user))
        self.assertContains(self.client.get(r.url), 'ცალკე ასლად')
        # an up-to-date page saves in place as before
        r = self.client.post(reverse('niv-create'), dict(t=niv.pk, v=niv.version, action='save', patient_name='ბერიძე ნინო', m1='Warfarin'))
        self.assertEqual(NivForm.objects.count(), 2)
        niv.refresh_from_db()
        self.assertEqual(niv.m1, 'Warfarin')

    def test_a3_print_context_unchanged(self):
        from .views import NivDocumentView
        niv = self.make_niv()
        view = NivDocumentView()
        view.request = RequestFactory().get('/', {'t': niv.pk})
        keys = set(view.get_context_data())
        self.assertNotIn('updated_at', keys)
        self.assertNotIn('updated_by', keys)

    def test_b1_pdf_cached_until_saved(self):
        from unittest import mock
        from django_docxtpl import DocxTemplateView
        niv = self.make_niv()
        real = DocxTemplateView.get
        with mock.patch.object(DocxTemplateView, 'get', autospec=True, side_effect=real) as render:
            first = self.client.get(reverse('print-niv-cover'), {'t': niv.pk})
            again = self.client.get(reverse('print-niv-cover'), {'t': niv.pk})
            self.assertEqual(render.call_count, 1)                   # LibreOffice ran once
            self.assertEqual(first.content, again.content)
            self.assertEqual(again['Content-Type'], 'application/pdf')
            self.client.post(reverse('niv-create'), dict(t=niv.pk, v=niv.version, action='save',
                                                          patient_name=niv.patient_name, m1='Heparin'))
            self.client.get(reverse('print-niv-cover'), {'t': niv.pk})
            self.assertEqual(render.call_count, 2)                   # changed -> printed afresh

    def test_b3_analysis_cached_until_prescriptions_change(self):
        from . import analysis
        niv = self.make_niv(diagnosis='აორტის ანევრიზმა', m1='Heparin')
        self.make_niv(diagnosis='აორტის ანევრიზმა', m1='Aspirin')
        first = analysis.medicines_for('ანევრიზმა', 'diagnosis')
        self.assertEqual(first[0], 2)
        with self.assertNumQueries(2):                       # per call only the "did anything change?" query
            self.assertEqual(analysis.medicines_for('ანევრიზმა', 'diagnosis'), first)
            analysis.medicines_for('ანევრიზმა', 'diagnosis')
        niv.m1 = 'Warfarin'
        niv.save()
        names = [m['name'] for m in analysis.medicines_for('ანევრიზმა', 'diagnosis')[1]]
        self.assertIn('Warfarin', names)
        niv.delete()
        self.assertEqual(analysis.medicines_for('ანევრიზმა', 'diagnosis')[0], 1)
        groups, _ = analysis.overview('terms', '')
        self.assertEqual(groups[0]['count'], 1)

    def test_a10_today_duty_runs_from_9_to_9(self):
        import datetime as dt
        from unittest import mock
        from zoneinfo import ZoneInfo
        from .models import RosterDay, RosterPerson
        from .quotes import today_panel
        RosterDay.objects.create(date=dt.date(2026, 12, 5), person=RosterPerson.objects.get(name='ია'))
        RosterDay.objects.create(date=dt.date(2026, 12, 6), person=RosterPerson.objects.get(name='გიგა'))
        evening = dt.datetime(2026, 12, 5, 20, 0, tzinfo=ZoneInfo('Asia/Tbilisi'))
        with mock.patch('django.utils.timezone.now', return_value=evening):
            self.assertEqual(today_panel()['duty'].name, 'ია')       # who is written for 5 December
        for hour, minute, who in ((8, 59, 'ია'), (9, 0, 'გიგა')):    # next morning: until 9:00 still ია
            moment = dt.datetime(2026, 12, 6, hour, minute, tzinfo=ZoneInfo('Asia/Tbilisi'))
            with mock.patch('django.utils.timezone.now', return_value=moment):
                panel = today_panel()
                self.assertEqual((panel['duty'].name, panel['date']), (who, dt.date(2026, 12, 6)))


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
        from .models import Case
        data = dict(patient_name='ბერიძე ნინო', age=67, diagnosis='კრიტიკული იშემია', created_by=self.user)
        data.update(kw)
        return Case.objects.create(**data)


class CaseTests(PrivateMediaTest):

    def test_create_edit_and_list(self):
        from .models import Case
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
        from .models import BoardPatient
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
        from .cases import add_photo
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
        from .cases import add_photo
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
        from .cases import add_photo
        from .models import Case, CasePhoto
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


def synthetic_doppler(hr='62', pv='31.6', arm='120', abi='0.55', blank_left=False):
    """A Smart-XT-like report drawn with the device's own character shapes (no patient data)."""
    import math
    from PIL import Image
    from . import doppler as dp
    from .doppler_glyphs import templates
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
            for tick, text in zip(dp.TICK_OFFSETS, labels):
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
    for key, y0, y1 in dp.TABLE_ROWS:
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


class DopplerTests(PrivateMediaTest):

    def test_reads_numbers_curves_and_scale(self):
        from . import doppler as dp
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
        from . import doppler as dp
        data = dp.parse(synthetic_doppler(hr='57', pv='-1.8', arm='152', abi='0.49', blank_left=True))
        right = next(s for s in data['sites'] if s['key'] == 'ankle_pt' and s['side'] == 'R')
        left = next(s for s in data['sites'] if s['key'] == 'ankle_pt' and s['side'] == 'L')
        self.assertEqual((right['hr'], right['value']), (57, -1.8))
        self.assertEqual((left['hr'], left['value'], left['wave']), (None, None, []))
        self.assertEqual((data['pressures']['R']['arm'], data['indices']['R']['abi_pt']), (152, 0.49))

    def test_unknown_files_are_refused(self):
        from PIL import Image
        from . import doppler as dp
        with self.assertRaises(dp.DopplerError):
            dp.parse(Image.new('RGB', (800, 600), 'white'))              # another size
        with self.assertRaises(dp.DopplerError):
            dp.parse(Image.new('RGB', (806, 1140), 'white'))             # right size, no graphs
        with self.assertRaises(dp.DopplerError):
            dp.load(io.BytesIO(b'%PDF-1.4 nothing here'))

    def test_upload_show_edit_and_delete(self):
        import os
        from .models import DopplerStudy
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


class StagingTablesTests(TestCase):
    """GLASS / WIfI / PLAN against the guideline (Conte et al. 2019, PMC author manuscript)."""

    def test_wifi_stage_table_3_5(self):
        from .staging import WIFI_STAGE
        self.assertEqual(len(WIFI_STAGE), 64)
        cases = {(0, 0, 0): 1, (0, 0, 1): 1, (1, 1, 0): 1, (0, 0, 2): 2, (0, 3, 0): 2, (2, 0, 1): 2,
                 (0, 0, 3): 3, (1, 3, 1): 3, (3, 0, 1): 3, (2, 1, 0): 3, (2, 2, 1): 4,   # the paper's example: WIfI 221 -> 4
                 (2, 3, 0): 4, (3, 1, 0): 4, (3, 0, 2): 4, (1, 2, 2): 4,
                 (0, 1, 2): 3, (0, 2, 1): 2}                                               # the two cells the print leaves unclear
        for key, stage in cases.items():
            self.assertEqual(WIFI_STAGE[key], stage, key)
        for (w, i, f), stage in WIFI_STAGE.items():            # never lower when any grade rises
            for nxt in ((w + 1, i, f), (w, i + 1, f), (w, i, f + 1)):
                if nxt in WIFI_STAGE:
                    self.assertGreaterEqual(WIFI_STAGE[nxt], stage, ((w, i, f), nxt))

    def test_ischemia_table_3_3(self):
        from .staging import grade_abi, grade_ap, grade_tp, suggest_ischemia
        self.assertEqual([grade_abi(v) for v in (0.8, 0.79, 0.6, 0.59, 0.4, 0.39)], [0, 1, 1, 2, 2, 3])
        self.assertEqual([grade_ap(v) for v in (101, 100, 70, 69, 50, 49)], [0, 1, 1, 2, 2, 3])
        self.assertEqual([grade_tp(v) for v in (60, 59, 40, 39, 30, 29)], [0, 1, 1, 2, 2, 3])
        self.assertEqual(suggest_ischemia(abi=0.95, ap=114, tp=25), (3, 'TP'))      # TP decides
        self.assertEqual(suggest_ischemia(abi=0.55, ap=66), (2, 'ABI/AP'))         # the worse of the two
        self.assertEqual(suggest_ischemia(abi=1.5, ap=180), (0, 'AP'))             # incompressible ABI
        self.assertEqual(suggest_ischemia(), (None, ''))

    def test_glass_table_5_3_and_calcification(self):
        from .staging import GLASS_MATRIX, compute
        self.assertEqual(GLASS_MATRIX[0], (None, 'I', 'I', 'II', 'III'))
        self.assertEqual(GLASS_MATRIX[2], ('I', 'II', 'II', 'II', 'III'))
        self.assertEqual(GLASS_MATRIX[3], ('II', 'II', 'II', 'III', 'III'))
        self.assertEqual(set(GLASS_MATRIX[4]), {'III'})
        r = compute({'fp': 2, 'ip': 2, 'fp_calc': True})
        self.assertEqual((r['fp_final'], r['ip_final'], r['glass']), (3, 2, 'II'))
        self.assertEqual(compute({'fp': 4, 'ip': 3, 'fp_calc': True, 'ip_calc': True})['fp_final'], 4)   # max 4
        self.assertIsNone(compute({'fp': 0, 'ip': 0})['glass'])

    def test_plan_fig_6_4_and_benefit_fig_6_3(self):
        from .staging import compute
        base = {'risk': 'avg', 'vein': 'yes'}
        def plan(fp, ip, w, i, fi, **kw):
            return compute({**base, 'fp': fp, 'ip': ip, 'w': w, 'i': i, 'fi': fi, **kw})
        r = plan(1, 0, 1, 2, 0)                                  # GLASS I, WIfI stage 2
        self.assertEqual((r['glass'], r['wifi'], r['plan']), ('I', 2, 'endo'))
        r = plan(4, 4, 2, 2, 1)                                  # GLASS III, WIfI 4
        self.assertEqual((r['glass'], r['wifi'], r['plan'], r['benefit']), ('III', 4, 'open', 4))
        self.assertEqual(plan(3, 3, 1, 2, 1)['plan'], 'open')    # GLASS III, stage 3
        self.assertEqual(plan(4, 0, 0, 2, 0)['plan'], 'unsure')  # GLASS III, stage 2
        self.assertEqual(plan(2, 2, 1, 2, 1)['plan'], 'unsure')  # GLASS II, stage 3
        self.assertEqual(plan(2, 2, 0, 2, 0)['plan'], 'endo')    # GLASS II, stage 2
        self.assertEqual(plan(4, 4, 0, 0, 0)['plan'], 'none')    # WIfI stage 1
        self.assertEqual(plan(4, 4, 0, 0, 0)['benefit'], 1)
        self.assertEqual(plan(2, 2, 0, 2, 0)['benefit'], 2)      # I2, stage 2
        self.assertEqual(plan(2, 2, 3, 3, 3, unsalvageable=True)['plan'], 'unsalvageable')
        self.assertIn('6.41', plan(4, 4, 2, 2, 1, vein='no')['plan_label'])
        self.assertIn('6.33', plan(4, 4, 2, 2, 1, risk='high')['plan_label'])
        self.assertIn('6.37', plan(4, 4, 2, 2, 1, risk='high')['plan_label'])
        self.assertIn('6.34', plan(1, 0, 1, 2, 0, risk='high')['plan_label'])
        self.assertTrue(any('P2' in n for n in plan(1, 0, 1, 2, 0, pedal='P2')['notes']))
        self.assertTrue(any('Inflow' in n for n in plan(1, 0, 1, 2, 0, ai='I')['notes']))

    def test_ischemia_from_doppler_and_clean(self):
        from .staging import clean, compute, doppler_measurements
        study = {'indices': {'R': {'abi_pt': 0.55, 'abi_dp': 0.56}}, 'pressures': {'R': {'pt': 66, 'dp': 68, 'toe': 39}}}
        m = doppler_measurements(study, 'R')
        self.assertEqual(m, {'abi': 0.56, 'ap': 68, 'tp': 39})
        r = compute({'w': 1, 'fi': 0, 'fp': 1, 'ip': 1}, m)
        self.assertEqual((r['i'], r['i_auto'], r['i_from'], r['wifi']), (2, True, 'TP', 2))
        limb = clean({'segments': {'sfa2': {'s': 'oc', 'ca': True, 'x': 1}, 'bogus': {'s': 'oc'}, 'pop1': {'s': 'n'}},
                      'fp': '7', 'ip': '2', 'w': 'a', 'risk': 'weird', 'tap': 'pt', 'abi': '0,6'})
        self.assertEqual(limb['v'], 2)
        self.assertEqual(limb['segments'], {'sfa2': {'stenosis': 'occlusion', 'calc': 3}})
        self.assertEqual((limb['fp'], limb['ip'], limb['w'], limb['risk'], limb['tap'], limb['abi']),
                         (None, 2, None, 'avg', 'pt', 0.6))


class StagingSchemaV2Tests(TestCase):
    """Schema v2: 26 segments, lossless v1 -> v2 upgrade, validation, and shared infrarenal aorta."""

    def test_26_segments_and_anatomical_paths(self):
        from .staging import SEGMENTS, SEGMENT_KEYS, map_context, segment_path
        self.assertEqual(len(SEGMENTS), 26)
        for key in ('aorta', 'cia', 'iia', 'eia', 'cfa', 'pfa', 'sfa1', 'pop1', 'pop2', 'pop3',
                    'tpt', 'at1', 'pt1', 'per1', 'dp', 'mpa', 'lpa', 'arch'):
            self.assertIn(key, SEGMENT_KEYS)
        self.assertNotIn('plantar', SEGMENT_KEYS)
        self.assertEqual(segment_path('cfa', 'pop3'), ['cfa', 'sfa1', 'sfa2', 'sfa3', 'pop1', 'pop2', 'pop3'])
        self.assertEqual(segment_path('pop3', 'lpa'), ['pop3', 'tpt', 'pt1', 'pt2', 'pt3', 'lpa'])
        self.assertEqual(segment_path('contralateral_cfa', 'cfa'), ['cfa'])
        right = map_context({}, 'R')
        left = map_context({}, 'L')
        self.assertEqual(len(right['segments']), 26)
        self.assertEqual(len(left['segments']), 26)

    def test_v1_upgrade_is_lossless_and_idempotent(self):
        import copy
        from .staging import clean, compute, map_context, upgrade_limb
        v1 = {
            'segments': {
                'cia': {'s': 'st', 'rx': True},
                'eia': {'rx': True},                    # connected to cia across iia in SEGMENTS
                'sfa2': {'s': 'oc', 'ca': True, 'rx': True},
                'sfa3': {'s': 'oc', 'rx': True},
                'cfa': {'by': True},
                'plantar': {'s': 'mild', 'by': True},   # legacy plantar -> lpa
                'pop2': {'s': 'st'},
            },
            'fp': 3, 'ip': 2, 'fp_calc': True, 'pedal': 'P1', 'tap': 'pt',
            'ai': 'I', 'cfa': 'A', 'w': 2, 'i': 2, 'fi': 1, 'risk': 'avg', 'vein': 'yes',
        }
        snapshot = copy.deepcopy(v1)
        up = upgrade_limb(v1)
        self.assertEqual(v1, snapshot)                  # never mutates caller's dict
        self.assertEqual(up['v'], 2)
        self.assertEqual(up['segments'], {
            'cia': {'stenosis': 'severe'},
            'sfa2': {'stenosis': 'occlusion', 'calc': 3},
            'sfa3': {'stenosis': 'occlusion'},
            'lpa': {'stenosis': 'mild'},
            'pop2': {'stenosis': 'severe'},
        })
        self.assertEqual(up['interventions'], [
            {'id': 'iv1', 'type': 'stent', 'subtype': 'unspecified', 'segments': ['cia', 'eia'],
             'performed_on': '', 'status': 'isr_1', 'note': ''},
            {'id': 'iv2', 'type': 'stent', 'subtype': 'unspecified', 'segments': ['sfa2', 'sfa3'],
             'performed_on': '', 'status': 'occluded', 'note': ''},
            {'id': 'iv3', 'type': 'bypass', 'conduit_group': 'unspecified', 'conduit': '', 'cuff': '',
             'proximal': 'cfa', 'distal': 'lpa', 'performed_on': '', 'status': 'patent', 'site': '', 'note': ''},
        ])
        cleaned = clean(v1)
        self.assertEqual(clean(cleaned), cleaned)       # idempotent
        self.assertEqual(compute(cleaned), compute(v1)) # clinical staging unchanged
        by_key = {s['key']: s for s in map_context(cleaned['segments'], 'R', cleaned['interventions'])['segments']}
        self.assertEqual(by_key['sfa2']['css'], 'sg sg-oc sg-ca sg-rx sg-by')
        self.assertEqual(by_key['eia']['css'], 'sg sg-n sg-rx')
        self.assertEqual(by_key['lpa']['css'], 'sg sg-mild sg-by')

    def test_v2_segment_and_intervention_validation(self):
        import datetime
        from .staging import clean, clean_interventions, clean_partial_date, clean_segments
        today = datetime.date(2026, 11, 24)
        self.assertEqual(clean_partial_date('2021', today=today), '2021')
        self.assertEqual(clean_partial_date('2026-11', today=today), '2026-11')
        self.assertEqual(clean_partial_date('2026-11-24', today=today), '2026-11-24')
        self.assertEqual(clean_partial_date('2026-12', today=today), '')        # future month
        self.assertEqual(clean_partial_date('2026-11-25', today=today), '')     # future day
        self.assertEqual(clean_partial_date('2024-02-30', today=today), '')     # invalid calendar date
        self.assertEqual(clean_partial_date('1920', today=today), '')

        segs = clean_segments({
            'aorta': {'stenosis': 'moderate', 'length_cm': '4,26', 'calc': 4, 'morph': 'eccentric',
                      'flush': True, 'reconstitution': 'yes', 'aneurysm': True},
            'sfa1': {'stenosis': 'occlusion', 'length_cm': 12, 'calc': 0, 'flush': True,
                     'reconstitution': 'delayed', 'coral': True, 'thrombus': True},
            'cia': {'stenosis': 'none', 'aneurysm': True, 'length_cm': 3.5, 'small_caliber': True, 'morph': 'concentric'},
            'pop3': {'stenosis': 'severe', 'calc': True, 'trifurcation': True, 'ulcerated': True},
            'eia': {'stenosis': 'none', 'length_cm': 8.0, 'flush': True},       # normal with stale CTO/length -> dropped
        })
        # flush/reconstitution stripped on non-CTO aorta; length rounded to 1 decimal
        self.assertEqual(segs['aorta'], {'stenosis': 'moderate', 'length_cm': 4.3, 'calc': 4,
                                         'morph': 'eccentric', 'aneurysm': True})
        self.assertEqual(segs['sfa1'], {'stenosis': 'occlusion', 'length_cm': 12.0, 'flush': True,
                                        'reconstitution': 'delayed', 'coral': True, 'thrombus': True})
        self.assertEqual(segs['cia'], {'length_cm': 3.5, 'aneurysm': True, 'small_caliber': True})
        self.assertEqual(segs['pop3'], {'stenosis': 'severe', 'trifurcation': True, 'ulcerated': True})
        self.assertNotIn('eia', segs)

        ivs = clean_interventions([
            {'type': 'bypass', 'conduit': 'gsv_reversed', 'cuff': 'miller', 'proximal': 'cfa', 'distal': 'pt3',
             'performed_on': '2023-05', 'status': 'stenosed', 'site': 'distal', 'note': '  ანასტომოზი  '},
            {'type': 'bypass', 'conduit_group': 'prosthetic', 'conduit': 'ptfe_ringed',
             'proximal': 'contralateral_cfa', 'distal': 'cfa', 'status': 'patent', 'site': 'distal'},
            {'type': 'stent', 'subtype': 'des', 'segments': ['sfa3', 'sfa2', 'sfa3'], 'status': 'isr_2'},
            {'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at2'], 'status': 'restenosed'},
            {'type': 'endarterectomy', 'subtype': 'patch_bovine', 'segments': ['cfa', 'pfa'], 'status': 'patent'},
            {'type': 'amputation', 'subtype': 'toes', 'status': 'healed', 'performed_on': '2024'},
            {'type': 'thrombectomy', 'subtype': 'fogarty', 'segments': ['pop1'], 'status': 'reoccluded'},
            {'type': 'bypass', 'proximal': 'cfa', 'distal': 'contralateral_cfa'},   # invalid distal -> skipped
            {'type': 'stent', 'segments': []},                                       # empty segments -> skipped
            {'type': 'amputation', 'subtype': 'unknown'},                            # invalid subtype -> skipped
        ], today=today)
        self.assertEqual(len(ivs), 7)
        self.assertEqual((ivs[0]['conduit_group'], ivs[0]['cuff'], ivs[0]['site'], ivs[0]['note']),
                         ('vein', 'miller', 'distal', 'ანასტომოზი'))
        self.assertEqual(ivs[1]['site'], '')                                    # site cleared when status != stenosed
        self.assertEqual(ivs[2]['segments'], ['sfa2', 'sfa3'])                  # deduplicated & proximal->distal
        self.assertEqual(ivs[6]['status'], 'occluded')                          # reoccluded normalized to occluded

        limb = clean({'fp': True, 'wound_angiosome': 'medial_plantar'}, today=today)
        self.assertIsNone(limb['fp'])                                           # booleans rejected as integers
        self.assertEqual(limb['wound_angiosome'], 'medial_plantar')

    def test_lumen_sync_dates_and_bypass_note(self):
        import datetime
        from .staging import clean, clean_partial_date, compute, format_partial_date, interventions_view
        today = datetime.date(2026, 11, 24)
        self.assertEqual(clean_partial_date('06.2023', today=today), '2023-06')
        self.assertEqual(clean_partial_date('5.6.2023', today=today), '2023-06-05')
        self.assertEqual(clean_partial_date('05/2022', today=today), '2022-05')
        self.assertEqual(format_partial_date('2023-06-05'), '05.06.2023')
        self.assertEqual(format_partial_date('2023-06'), '06.2023')
        self.assertEqual(format_partial_date('2023'), '2023')

        limb = clean({
            'segments': {
                'cfa': {'stenosis': 'occlusion', 'calc': 3, 'flush': True},
                'at1': {'stenosis': 'occlusion', 'reconstitution': 'yes'},
            },
            'interventions': [
                # Occluded stent -> marks sfa2 & sfa3 occluded automatically
                {'type': 'stent', 'subtype': 'des', 'segments': ['sfa2', 'sfa3'], 'status': 'occluded'},
                # Restenosed DCB -> downgrades at1 from occlusion to severe and clears CTO flags
                {'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at1'], 'status': 'restenosed'},
                # Patent patch endarterectomy -> clears cfa occlusion while preserving calc=3
                {'type': 'endarterectomy', 'subtype': 'patch_bovine', 'segments': ['cfa'], 'status': 'patent'},
                # Newer 2024 patent thrombectomy entered BEFORE older 2021 occluded stent -> 2024 wins
                {'type': 'thrombectomy', 'subtype': 'fogarty', 'segments': ['pop1'],
                 'performed_on': '06.2024', 'status': 'patent'},
                {'type': 'stent', 'subtype': 'bms', 'segments': ['pop1'],
                 'performed_on': '2021', 'status': 'occluded'},
            ],
        }, today=today)
        self.assertEqual(limb['segments']['sfa2'], {'stenosis': 'occlusion'})
        self.assertEqual(limb['segments']['sfa3'], {'stenosis': 'occlusion'})
        self.assertEqual(limb['segments']['at1'], {'stenosis': 'severe'})
        self.assertEqual(limb['segments']['cfa'], {'calc': 3})
        self.assertNotIn('pop1', limb['segments'])                              # patent 2024 thrombectomy beat 2021 stent

        # Functioning bypass triggers GLASS reminder note; occluded bypass does not
        r_pat = compute({'fp': 3, 'ip': 1, 'interventions': [
            {'type': 'bypass', 'conduit': 'gsv_reversed', 'proximal': 'cfa', 'distal': 'pop3', 'status': 'patent'},
        ]})
        self.assertTrue(any('ფუნქციონირებს შუნტი' in n for n in r_pat['notes']))
        r_occ = compute({'fp': 3, 'ip': 1, 'interventions': [
            {'type': 'bypass', 'conduit': 'ptfe', 'proximal': 'cfa', 'distal': 'pop1', 'status': 'occluded'},
        ]})
        self.assertFalse(any('ფუნქციონირებს შუნტი' in n for n in r_occ['notes']))

        rows = interventions_view([
            {'type': 'bypass', 'conduit': 'ptfe_ringed', 'cuff': 'miller', 'proximal': 'cfa', 'distal': 'pt3',
             'performed_on': '2022-06', 'status': 'stenosed', 'site': 'distal', 'note': '75%'},
            {'type': 'amputation', 'subtype': 'tma', 'performed_on': '2024-03-10', 'status': 'nonhealing'},
        ])
        self.assertEqual((rows[0]['title'], rows[0]['date_display'], rows[0]['tone']),
                         ('შუნტირება: CFA → PT დისტ.', '06.2022', 'warn'))
        self.assertIn('Miller-ის მანჟეტი', rows[0]['detail'])
        self.assertIn('დისტალური ანასტომოზი', rows[0]['status_label'])
        self.assertEqual((rows[1]['title'], rows[1]['date_display'], rows[1]['tone']),
                         ('ტრანსმეტატარზული (TMA)', '10.03.2024', 'bad'))


class StagingPageTests(PrivateMediaTest):

    def test_page_preview_save_and_show(self):
        case = self.make_case(side='R')
        url = reverse('case-staging', args=[case.pk, 'R'])
        page = self.client.get(url)
        self.assertContains(page, 'data-seg="aorta"')
        self.assertContains(page, 'data-seg="sfa2"')
        self.assertContains(page, 'data-seg="pop3"')
        self.assertContains(page, 'data-seg="mpa"')
        self.assertContains(page, 'id="st-iv-editor"')
        self.assertNotContains(page, 'data-flag="rx"')
        self.assertNotContains(page, 'data-flag="by"')
        self.assertContains(page, 'FP 4')
        self.assertEqual(self.client.get(reverse('case-staging', args=[case.pk, 'X'])).status_code, 404)
        data = {'segments': json.dumps({'sfa2': {'s': 'oc'}, 'pop1': {'s': 'st', 'ca': True}}),
                'fp': '3', 'ip': '1', 'pedal': 'P0', 'tap': 'at', 'w': '2', 'i': '2', 'fi': '1',
                'risk': 'avg', 'vein': 'yes', 'cfa': 'A'}
        preview = self.client.post(url, {**data, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('id="st-res"', preview['html'])
        self.assertIn('plan-unsure', preview['html'])               # FP3 + IP1 = GLASS II; WIfI 221 = 4 -> indeterminate (Fig 6.4)
        case.refresh_from_db()
        self.assertEqual(case.staging, {})                          # a preview saves nothing
        self.client.post(url, data)
        case.refresh_from_db()
        self.assertEqual(case.staging['R']['v'], 2)
        self.assertEqual(case.staging['R']['segments'],
                         {'sfa2': {'stenosis': 'occlusion'}, 'pop1': {'stenosis': 'severe', 'calc': 3}})
        detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(detail, 'sg sg-oc')
        self.assertContains(detail, 'GLASS')
        self.assertContains(self.client.get(reverse('cases-present'), {'ids': case.pk}), 'pz-stage-slide')

    def test_shared_aorta_syncs_across_limbs_and_v1_interventions_persist(self):
        from .cases import staging_views
        case = self.make_case(side='B')
        r_url = reverse('case-staging', args=[case.pk, 'R'])
        l_url = reverse('case-staging', args=[case.pk, 'L'])

        # 1. Saving R with infrarenal aorta disease does not create a phantom L limb,
        #    but opening L pre-populates the shared aorta automatically.
        self.client.post(r_url, {
            'segments': json.dumps({'aorta': {'stenosis': 'occlusion', 'calc': 4, 'flush': True},
                                    'sfa1': {'stenosis': 'severe'}}),
            'fp': '2', 'ip': '0', 'cfa': 'A',
        })
        case.refresh_from_db()
        self.assertEqual(list(case.staging.keys()), ['R'])
        self.assertEqual([v['side'] for v in staging_views(case)], ['R'])

        l_page = self.client.get(l_url)
        self.assertEqual(l_page.context['segments_json'],
                         {'aorta': {'stenosis': 'occlusion', 'calc': 4, 'flush': True}})

        # 2. Updating aorta on L syncs back onto R while preserving R's limb-specific segments.
        self.client.post(l_url, {
            'segments': json.dumps({'aorta': {'stenosis': 'moderate', 'calc': 2}, 'pt1': {'stenosis': 'occlusion'}}),
            'fp': '0', 'ip': '3', 'cfa': 'A',
        })
        case.refresh_from_db()
        self.assertEqual(case.staging['R']['segments'],
                         {'aorta': {'stenosis': 'moderate', 'calc': 2}, 'sfa1': {'stenosis': 'severe'}})
        self.assertEqual(case.staging['L']['segments'],
                         {'aorta': {'stenosis': 'moderate', 'calc': 2}, 'pt1': {'stenosis': 'occlusion'}})

        # 3. Clearing aorta on R clears it on L too.
        self.client.post(r_url, {'segments': json.dumps({'sfa1': {'stenosis': 'severe'}}), 'cfa': 'A'})
        case.refresh_from_db()
        self.assertNotIn('aorta', case.staging['R']['segments'])
        self.assertNotIn('aorta', case.staging['L']['segments'])
        self.assertEqual(case.staging['L']['segments'], {'pt1': {'stenosis': 'occlusion'}})

        # 4. Existing v1 record with rx/by keeps migrated interventions when re-saved without interventions field.
        v1_case = self.make_case(side='R', staging={
            'R': {'segments': {'sfa2': {'s': 'oc', 'rx': True}, 'cfa': {'by': True}, 'pop1': {'by': True}}, 'fp': 3, 'ip': 1}
        })
        self.client.post(reverse('case-staging', args=[v1_case.pk, 'R']),
                         {'segments': json.dumps({'sfa2': {'stenosis': 'occlusion'}}), 'fp': '3', 'ip': '1', 'cfa': 'A'})
        v1_case.refresh_from_db()
        self.assertEqual(len(v1_case.staging['R']['interventions']), 2)

    def test_interventions_crud_summary_and_presentation(self):
        case = self.make_case(side='R', past_history='კორონარული შუნტირება 2019')
        url = reverse('case-staging', args=[case.pk, 'R'])
        interventions = [
            {'id': 'iv1', 'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
             'proximal': 'cfa', 'distal': 'pop3', 'performed_on': '06.2022',
             'status': 'stenosed', 'site': 'distal', 'note': 'ანასტომოზის 75% სტენოზი'},
            {'id': 'iv2', 'type': 'stent', 'subtype': 'des', 'segments': ['sfa2', 'sfa3'],
             'performed_on': '2021', 'status': 'isr_2', 'note': ''},
            {'id': 'iv3', 'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at2'],
             'performed_on': '2023-09', 'status': 'patent', 'note': ''},
            {'id': 'iv4', 'type': 'endarterectomy', 'subtype': 'patch_bovine', 'segments': ['cfa', 'pfa'],
             'performed_on': '06.2022', 'status': 'patent', 'note': ''},
            {'id': 'iv5', 'type': 'amputation', 'subtype': 'toes', 'performed_on': '12.10.2024',
             'status': 'healed', 'note': 'I თითი'},
            {'id': 'iv6', 'type': 'thrombectomy', 'subtype': 'fogarty', 'segments': ['pop1'],
             'performed_on': '2024-01', 'status': 'occluded', 'note': ''},
        ]
        payload = {
            'segments': json.dumps({}),
            'interventions': json.dumps(interventions),
            'fp': '3', 'ip': '1', 'pedal': 'P0', 'tap': 'at',
            'w': '2', 'i': '2', 'fi': '0', 'risk': 'avg', 'vein': 'yes', 'cfa': 'A',
        }
        # Live preview includes the functioning-bypass GLASS reminder note
        preview = self.client.post(url, {**payload, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('ფუნქციონირებს შუნტი', preview['html'])

        self.client.post(url, payload)
        case.refresh_from_db()
        self.assertEqual(case.past_history, 'კორონარული შუნტირება 2019')     # card free-text untouched
        saved = case.staging['R']
        self.assertEqual(len(saved['interventions']), 6)
        # Stent ISR-2 and occluded thrombectomy automatically synced onto native segment lumen
        self.assertEqual(saved['segments']['sfa2']['stenosis'], 'severe')
        self.assertEqual(saved['segments']['sfa3']['stenosis'], 'severe')
        self.assertEqual(saved['segments']['pop1']['stenosis'], 'occlusion')

        # Shown on both Case detail page and Conference Presentation slide
        for page in (self.client.get(reverse('case', args=[case.pk])),
                     self.client.get(reverse('cases-present'), {'ids': case.pk})):
            self.assertContains(page, 'წარსული ჩარევები')
            self.assertContains(page, 'შუნტირება: CFA → P3')
            self.assertContains(page, '06.2022')
            self.assertContains(page, 'Tosaka II')
            self.assertContains(page, 'ქსენოპერიკარდიუმის პატჩით')
            self.assertContains(page, 'თით(ებ)ის ამპუტაცია')
            self.assertContains(page, 'Fogarty')

        # Deleting interventions leaves only remaining items
        self.client.post(url, {**payload, 'interventions': json.dumps([interventions[4]])})
        case.refresh_from_db()
        self.assertEqual([iv['id'] for iv in case.staging['R']['interventions']], ['iv5'])


class StagingVectorMapTests(PrivateMediaTest):
    """T3: pure-vector surgical anatomy, 3-layer tubular vessels, pathology, grafts, and mirroring."""

    def test_vector_layers_pathology_grafts_and_left_leg_mirroring(self):
        from .staging import map_context
        segments = {
            'cia': {'stenosis': 'moderate', 'length_cm': 4.0, 'calc': 2, 'morph': 'eccentric', 'aneurysm': True},
            'sfa1': {'stenosis': 'occlusion', 'calc': 4, 'flush': True, 'coral': True},
            'sfa2': {'stenosis': 'occlusion', 'reconstitution': 'yes', 'thrombus': True},
            'pop2': {'stenosis': 'severe', 'length_cm': 3.0, 'morph': 'concentric', 'ulcerated': True},
        }
        interventions = [
            {'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
             'proximal': 'cfa', 'distal': 'pop3', 'status': 'patent'},
            {'type': 'bypass', 'conduit_group': 'prosthetic', 'conduit': 'ptfe_ringed', 'cuff': 'miller',
             'proximal': 'contralateral_cfa', 'distal': 'cfa', 'status': 'stenosed', 'site': 'distal'},
            {'type': 'stent', 'subtype': 'covered', 'segments': ['eia'], 'status': 'fractured'},
            {'type': 'endarterectomy', 'subtype': 'patch_vein', 'segments': ['cfa'], 'status': 'patent'},
            {'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at1'], 'status': 'patent'},
            {'type': 'amputation', 'subtype': 'tma', 'status': 'healed'},
        ]
        right = map_context(segments, 'R', interventions)
        left = map_context(segments, 'L', interventions)

        # 26 adventitial tubes, plaque/CTO beds, collateral arcade, unilateral + bilateral PACSS plates
        self.assertEqual(len(right['adventitia']), 26)
        self.assertEqual(len(right['plaque_beds']), 4)
        self.assertTrue(len(right['collaterals']) >= 1)
        self.assertEqual(len(right['calc_plates']), 3)          # PACSS 2 (1 wall) + PACSS 4 (2 walls)
        self.assertTrue(len(right['coral_nodules']) >= 1)
        self.assertEqual(len(right['thrombi']), 3)

        # Interventions: covered fractured stent, patch + sutures, balloon marks, 2 bypasses, TMA line
        self.assertEqual(len(right['iv']['stents']), 1)
        self.assertTrue(bool(right['iv']['stents'][0]['sleeve']))
        self.assertIsNotNone(right['iv']['stents'][0]['fracture'])
        self.assertEqual(len(right['iv']['patches']), 1)
        self.assertTrue(len(right['iv']['patches'][0]['ticks']) >= 2)
        self.assertEqual(len(right['iv']['bypasses']), 2)
        self.assertTrue(len(right['iv']['bypasses'][1]['rings']) > 0)
        self.assertIsNotNone(right['iv']['bypasses'][1]['miller'])
        self.assertEqual(len(right['iv']['amputations']), 1)

        # Left leg mirrors coordinates around axis=300 and flips start<->end anchors without flipping text
        self.assertEqual(right['axis'], 300)
        for r_lbl, l_lbl in zip(right['labels'], left['labels']):
            self.assertEqual(r_lbl['text'], l_lbl['text'])
            self.assertAlmostEqual(r_lbl['x'] + l_lbl['x'], 300.0, places=1)
            self.assertTrue(-58 <= l_lbl['x'] <= 358, l_lbl)
            if r_lbl['anchor'] in ('start', 'end'):
                self.assertNotEqual(r_lbl['anchor'], l_lbl['anchor'])

    def test_rendered_svg_has_no_raster_image_and_updates_in_preview(self):
        case = self.make_case(side='B')
        url_l = reverse('case-staging', args=[case.pk, 'L'])
        page = self.client.get(url_l)
        self.assertNotContains(page, 'leg-anterior.webp')       # pure vector, no raster image
        self.assertContains(page, 'class="am-limb"')
        self.assertContains(page, 'class="am-bone"')
        self.assertContains(page, 'ადუქტორული ხვრელი')
        self.assertContains(page, 'translate(300,0) scale(-1,1)')

        preview = self.client.post(url_l, {
            'segments': json.dumps({'sfa1': {'stenosis': 'occlusion', 'calc': 4}}),
            'interventions': json.dumps([{'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
                                          'proximal': 'cfa', 'distal': 'pop3', 'status': 'patent'}]),
            'cfa': 'A', 'preview': '1',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('map_html', preview)
        self.assertIn('am-cto-core', preview['map_html'])
        self.assertIn('am-coll', preview['map_html'])
        self.assertIn('am-bypass vein', preview['map_html'])


class StagingFootAngiosomesTests(PrivateMediaTest):
    """T4: lateral-oblique pedal arch inset, 6 Attinger angiosomes, and symmetric 4-zone zoom."""

    def test_angiosome_direct_indirect_and_arch_evaluation(self):
        from .staging import angiosome_status, compute

        # Unspecified or 'none' -> no angiosome status
        self.assertIsNone(angiosome_status({'wound_angiosome': ''}))
        self.assertIsNone(angiosome_status({'wound_angiosome': 'none', 'tap': 'pt'}))
        self.assertNotIn('angiosome', compute({'wound_angiosome': 'none', 'tap': 'pt', 'fp': 2, 'ip': 1}))

        # Selected angiosome before TAP is chosen -> pending hint naming the source artery
        pending = angiosome_status({'wound_angiosome': 'medial_plantar'})
        self.assertEqual((pending['status'], pending['source_tap'], pending['direct']), ('pending', 'pt', None))

        # 1. Direct revascularization: PT -> medial plantar with patent MPA
        direct = compute({'wound_angiosome': 'medial_plantar', 'tap': 'pt', 'pedal': 'P0'})['angiosome']
        self.assertEqual((direct['status'], direct['direct'], direct['tone']), ('direct', True, 'ok'))

        # 2. Matching tibial TAP, but terminal foot branch (MPA) is occluded -> collateral/arch dependent
        coll = compute({
            'wound_angiosome': 'medial_plantar', 'tap': 'pt',
            'segments': {'mpa': {'stenosis': 'occlusion'}},
        })['angiosome']
        self.assertEqual((coll['status'], coll['direct'], coll['tone']), ('collateral', False, 'warn'))

        # 3. Indirect revascularization via intact pedal arch (AT -> medial plantar, arch patent, P0)
        ind_ok = compute({'wound_angiosome': 'medial_plantar', 'tap': 'at', 'pedal': 'P0'})['angiosome']
        self.assertEqual((ind_ok['status'], ind_ok['direct'], ind_ok['tone']), ('indirect_arch', False, 'warn'))

        # 4. Indirect revascularization with diseased/absent pedal arch (arch occluded OR P1/P2)
        ind_bad = compute({
            'wound_angiosome': 'lateral_heel', 'tap': 'at',
            'segments': {'arch': {'stenosis': 'occlusion'}},
        })['angiosome']
        self.assertEqual((ind_bad['status'], ind_bad['direct'], ind_bad['tone']), ('indirect_poor', False, 'bad'))
        ind_p1 = compute({'wound_angiosome': 'dorsal', 'tap': 'pt', 'pedal': 'P1'})['angiosome']
        self.assertEqual(ind_p1['status'], 'indirect_poor')

    def test_foot_inset_zoom_zones_and_page_integration(self):
        from .staging import foot_context, zoom_zones

        # Every zoom viewBox is centered on axis=300 so R and mirrored L legs stay centered
        zones = zoom_zones()
        self.assertEqual([z[0] for z in zones], ['all', 'inflow', 'fp', 'btk'])
        for _key, _label, vbox in zones:
            vx, _vy, vw, _vh = (float(x) for x in vbox.split())
            self.assertAlmostEqual(2.0 * vx + vw, 300.0, places=3)

        # Foot context: 7 pedal/ankle segments, peroneal communicating branches, Miller hood & TMA line
        fm_pat = foot_context(
            {'mpa': {'stenosis': 'severe', 'calc': 3}},
            'R',
            [{'type': 'bypass', 'conduit_group': 'prosthetic', 'conduit': 'ptfe', 'cuff': 'miller',
              'proximal': 'cfa', 'distal': 'dp', 'status': 'patent'},
             {'type': 'amputation', 'subtype': 'tma', 'status': 'nonhealing'}],
            'medial_plantar',
        )
        self.assertEqual([s['key'] for s in fm_pat['segments']], ['at3', 'pt3', 'per3', 'dp', 'mpa', 'lpa', 'arch'])
        self.assertEqual(len(fm_pat['peroneal_comm']), 2)
        self.assertIsNotNone(fm_pat['angiosome'])
        self.assertEqual(len(fm_pat['distal_hoods']), 1)
        self.assertTrue(fm_pat['distal_hoods'][0]['miller'])
        self.assertEqual(len(fm_pat['amputations']), 1)
        self.assertFalse(fm_pat['amputations'][0]['healed'])

        # Occluding per3 hides peroneal communicating branches
        fm_occ = foot_context({'per3': {'stenosis': 'occlusion'}}, 'R')
        self.assertEqual(fm_occ['peroneal_comm'], [])

        # Angiography page has interactive foot map without WIfI/Doppler inputs; WIfI page has wound_angiosome
        case = self.make_case(side='R')
        url = reverse('case-staging', args=[case.pk, 'R'])
        page = self.client.get(url)
        self.assertContains(page, 'class="am am-foot am-edit"')
        self.assertContains(page, 'data-zoom="btk"')
        self.assertNotContains(page, 'name="wound_angiosome"')
        wifi_page = self.client.get(reverse('case-wifi', args=[case.pk, 'R']))
        self.assertContains(wifi_page, 'name="wound_angiosome"')

        payload = {
            'segments': json.dumps({'arch': {'stenosis': 'none'}, 'mpa': {'stenosis': 'moderate'}}),
            'interventions': json.dumps([]),
            'wound_angiosome': 'medial_plantar',
            'tap': 'pt', 'fp': '2', 'ip': '2', 'pedal': 'P0',
            'w': '2', 'i': '2', 'fi': '1', 'risk': 'avg', 'vein': 'yes', 'cfa': 'A',
        }
        preview = self.client.post(url, {**payload, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('foot_html', preview)
        self.assertIn('am-angio-zone', preview['foot_html'])
        self.assertIn('პირდაპირი (ანგიოსომაზე მიმართული) რევასკულარიზაცია', preview['html'])

        self.client.post(url, payload)
        case.refresh_from_db()
        self.assertEqual(case.staging['R']['wound_angiosome'], 'medial_plantar')

        for rendered in (self.client.get(reverse('case', args=[case.pk])),
                         self.client.get(reverse('cases-present'), {'ids': case.pk})):
            self.assertContains(rendered, 'am am-foot')
            self.assertContains(rendered, 'am-angio-zone')
            self.assertContains(rendered, 'პირდაპირი (ანგიოსომაზე მიმართული) რევასკულარიზაცია')


class StagingSuggestionAndInspectorTests(PrivateMediaTest):
    """T5: suggest_from_map() (GVG Figs 5.1-5.3, Table 5.2, SVS Runoff 1-19), inspector & manual override safety."""

    def test_suggest_from_map_inflow_fp_and_calc_boundaries(self):
        from .staging import suggest_from_map

        empty = suggest_from_map({})
        self.assertFalse(empty['has_map'])
        self.assertEqual((empty['svs_runoff'], empty['foot_runoff'], empty['runoff_count']), (1.0, 1.0, 3))

        # Inflow Table 5.2
        self.assertEqual(suggest_from_map({'segments': {'cia': {'stenosis': 'occlusion'}}})['ai'], 'I')
        self.assertEqual(suggest_from_map({'segments': {'cia': {'stenosis': 'occlusion'},
                                                        'eia': {'stenosis': 'occlusion'}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'aorta': {'stenosis': 'occlusion'}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'cia': {'aneurysm': True}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'eia': {'small_caliber': True}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'interventions': [
            {'type': 'stent', 'subtype': 'bms', 'segments': ['eia'], 'status': 'isr_2'},
        ]})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'cfa': {'stenosis': 'moderate'}}})['cfa'], 'B')

        # GLASS FP Fig 5.2 boundaries
        fp_of = lambda segs: suggest_from_map({'segments': segs})['fp']
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'mild'}}), 0)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'severe', 'length_cm': 4.0}}), 1)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'occlusion', 'length_cm': 3.0}}), 1)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'occlusion', 'length_cm': 3.0, 'flush': True}}), 3)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'occlusion', 'length_cm': 2.0},
                                'sfa3': {'stenosis': 'occlusion', 'length_cm': 2.0}}), 2)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'occlusion', 'length_cm': 7.5}}), 2)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'severe', 'length_cm': 15.0}}), 2)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'occlusion', 'length_cm': 15.0}}), 3)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'severe', 'length_cm': 25.0}}), 3)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'occlusion', 'length_cm': 25.0}}), 4)
        self.assertEqual(fp_of({'pop2': {'stenosis': 'severe', 'length_cm': 1.5}}), 2)
        self.assertEqual(fp_of({'pop2': {'stenosis': 'severe', 'length_cm': 3.0}}), 3)
        self.assertEqual(fp_of({'pop2': {'stenosis': 'severe', 'length_cm': 6.0}}), 4)
        self.assertEqual(fp_of({'pop3': {'stenosis': 'severe', 'length_cm': 1.5, 'trifurcation': True}}), 4)
        self.assertEqual(fp_of({'pop1': {'stenosis': 'occlusion', 'length_cm': 1.5}}), 4)

        # PACSS >= 3 or coral -> +1 calcification modifier
        self.assertFalse(suggest_from_map({'segments': {'sfa2': {'stenosis': 'severe', 'calc': 2}}})['fp_calc'])
        self.assertTrue(suggest_from_map({'segments': {'sfa2': {'stenosis': 'severe', 'calc': 3}}})['fp_calc'])
        self.assertTrue(suggest_from_map({'segments': {'pop2': {'coral': True}}})['fp_calc'])

    def test_suggest_from_map_ip_tap_pedal_and_svs_runoff(self):
        from .staging import suggest_from_map

        # TPT CTO makes PT & PER IP 4 while leaving AT IP 0 -> suggests AT and reflects TPT loss in SVS runoff
        s_tpt = suggest_from_map({'segments': {'tpt': {'stenosis': 'occlusion'}}})
        self.assertEqual(s_tpt['ip_by_tap']['at']['ip'], 0)
        self.assertEqual(s_tpt['ip_by_tap']['pt']['ip'], 4)
        self.assertEqual(s_tpt['ip_by_tap']['per']['ip'], 4)
        self.assertEqual(s_tpt['tap'], 'at')
        self.assertEqual((s_tpt['svs_runoff'], s_tpt['runoff_count'], s_tpt['estimated_length']), (6.0, 1, False))

        # Tibial focal <3 cm stenosis (IP 1), non-ostial focal CTO <3 cm (IP 2), ostial flush CTO <3 cm (IP 3)
        self.assertEqual(suggest_from_map({'segments': {'at2': {'stenosis': 'severe', 'length_cm': 2.0}}})
                         ['ip_by_tap']['at']['ip'], 1)
        self.assertEqual(suggest_from_map({'segments': {'at2': {'stenosis': 'occlusion', 'length_cm': 2.0}}})
                         ['ip_by_tap']['at']['ip'], 2)
        self.assertEqual(suggest_from_map({'segments': {'at1': {'stenosis': 'occlusion', 'length_cm': 2.0, 'flush': True}}})
                         ['ip_by_tap']['at']['ip'], 3)
        self.assertEqual(suggest_from_map({'segments': {'tpt': {'stenosis': 'severe', 'length_cm': 2.0}}})
                         ['ip_by_tap']['pt']['ip'], 2)

        # Wound angiosome breaks tie when AT and PT have equal IP grade
        s_tie = suggest_from_map({
            'segments': {'at2': {'stenosis': 'severe', 'length_cm': 2.0},
                         'pt2': {'stenosis': 'severe', 'length_cm': 2.0}},
            'wound_angiosome': 'medial_plantar',
        })
        self.assertEqual(s_tie['tap'], 'pt')
        self.assertEqual(s_tie['pedal'], 'P0')

        # When both AT and PT have severe IP >= 3 disease, patent peroneal becomes TAP
        self.assertEqual(suggest_from_map({
            'segments': {'at1': {'stenosis': 'occlusion'}, 'pt1': {'stenosis': 'occlusion'}},
        })['tap'], 'per')

        # Pedal P1 when arch is occluded; P2 when no artery crosses ankle into foot
        self.assertEqual(suggest_from_map({'segments': {'arch': {'stenosis': 'occlusion'}}})['pedal'], 'P1')
        self.assertEqual(suggest_from_map({'segments': {
            'dp': {'stenosis': 'occlusion'}, 'mpa': {'stenosis': 'occlusion'}, 'lpa': {'stenosis': 'occlusion'},
        }})['pedal'], 'P2')

        # Modified SVS Runoff (1.0 - 19.0) & Foot Score (1.0 - 10.0) (Stoner et al. 2016)
        worst = suggest_from_map({'segments': {
            'pop1': {'stenosis': 'occlusion'}, 'pop2': {'stenosis': 'occlusion'}, 'pop3': {'stenosis': 'occlusion'},
            'at1': {'stenosis': 'occlusion'}, 'at2': {'stenosis': 'occlusion'},
            'pt1': {'stenosis': 'occlusion'}, 'pt2': {'stenosis': 'occlusion'},
            'per1': {'stenosis': 'occlusion'}, 'per2': {'stenosis': 'occlusion'},
            'dp': {'stenosis': 'occlusion'}, 'mpa': {'stenosis': 'occlusion'}, 'lpa': {'stenosis': 'occlusion'},
        }})
        self.assertEqual((worst['svs_runoff'], worst['foot_runoff'], worst['runoff_count']), (19.0, 10.0, 0))

        mixed = suggest_from_map({'segments': {
            'pop1': {'stenosis': 'occlusion'},       # 4 / 12 cm <= 0.5 -> 2.5 * 3 + 1 = 8.5
            'at2': {'stenosis': 'moderate'},         # + 2.0
            'pt2': {'stenosis': 'mild'},             # + 1.0 (PER = 0.0)
        }})
        self.assertEqual((mixed['svs_runoff'], mixed['svs_runoff_text'], mixed['runoff_count']), (11.5, '11.5', 3))

    def test_manual_selections_never_silently_overwritten_and_page_renders_inspector(self):
        case = self.make_case(side='R')
        url = reverse('case-staging', args=[case.pk, 'R'])
        page = self.client.get(url)
        self.assertContains(page, 'id="st-seg-inspector"')
        self.assertContains(page, '+ მორფოლოგია')
        self.assertContains(page, 'data-inspect="1"')

        # Map points to FP 4 & TAP AT, while surgeon explicitly selected FP 1, IP 1, TAP PT -> GLASS I preserved!
        payload = {
            'segments': json.dumps({'pop2': {'stenosis': 'occlusion'}, 'tpt': {'stenosis': 'occlusion'}}),
            'interventions': json.dumps([]),
            'tap': 'pt', 'fp': '1', 'ip': '1', 'pedal': 'P0',
            'w': '1', 'i': '2', 'fi': '0', 'risk': 'avg', 'vein': 'yes', 'cfa': 'A',
        }
        preview = self.client.post(url, {**payload, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('glass-I', preview['html'])
        self.assertIn('id="st-apply-sug"', preview['html'])
        self.assertIn('რუკიდან შევსება', preview['html'])
        self.assertIn('SVS ქულა:', preview['html'])

        self.client.post(url, payload)
        case.refresh_from_db()
        saved = case.staging['R']
        self.assertEqual((saved['fp'], saved['ip'], saved['tap']), (1, 1, 'pt'))

        # Read-only detail & presentation show SVS runoff summary, never the edit-only suggestion button
        for rendered in (self.client.get(reverse('case', args=[case.pk])),
                         self.client.get(reverse('cases-present'), {'ids': case.pk})):
            self.assertContains(rendered, 'SVS ქულა:')
            self.assertNotContains(rendered, 'id="st-apply-sug"')


class StagingDsaPresentAndPrintTests(PrivateMediaTest):
    """T6: Anatomical <-> DSA toggle, dedicated per-limb presentation slides, D on all slides, and A4 print."""

    def test_dsa_toggle_and_print_controls_on_staging_and_detail(self):
        case = self.make_case(side='R')
        unstaged_detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(unstaged_detail, 'cs-print-doc')
        self.assertContains(unstaged_detail, 'id="cs-print-btn"')
        self.assertNotContains(unstaged_detail, 'data-map-view="dsa"')

        staging_page = self.client.get(reverse('case-staging', args=[case.pk, 'R']))
        self.assertContains(staging_page, 'cs-print-doc')
        self.assertContains(staging_page, 'data-map-view="anat"')
        self.assertContains(staging_page, 'data-map-view="dsa"')

        self.client.post(reverse('case-staging', args=[case.pk, 'R']), {
            'segments': json.dumps({'sfa2': {'stenosis': 'occlusion'}}),
            'fp': '2', 'ip': '0', 'pedal': 'P0', 'tap': 'at', 'cfa': 'A',
        })
        staged_detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(staged_detail, 'data-map-view="dsa"')

    def test_bilateral_presentation_renders_separate_limb_slides_and_decision_footers(self):
        from .models import DopplerStudy
        from .staging import save_limb

        st_map = {}
        st_map = save_limb(st_map, 'R', {'segments': {'sfa1': {'stenosis': 'occlusion'}}, 'fp': 3, 'ip': 1, 'tap': 'at'})
        st_map = save_limb(st_map, 'L', {'segments': {'pt2': {'stenosis': 'severe'}}, 'fp': 0, 'ip': 2, 'tap': 'pt'})
        case = self.make_case(side='B', staging=st_map)
        DopplerStudy.objects.create(
            case=case, performed_on='2026-11-20', created_by=self.user,
            data={'indices': {'R': {'abi_dp': 0.52}, 'L': {'abi_pt': 0.68}}, 'pressures': {}, 'waveforms': {}},
        )

        pres = self.client.get(reverse('cases-present'), {'ids': case.pk})
        html = pres.content.decode()
        self.assertContains(pres, 'id="pz-dsa"')
        self.assertContains(pres, 'data-side="R"')
        self.assertContains(pres, 'data-side="L"')
        # Two separate staging slides (one per limb)
        self.assertEqual(html.count('class="pz-slide pz-stage-slide"'), 2)
        # Decision footer present on main slide + 2 staging slides + 1 Doppler slide = 4 footers
        self.assertEqual(html.count('class="pz-decide-toggle"'), 4)
        self.assertEqual(html.count(f'data-decision="{reverse("case-decision", args=[case.pk])}"'), 4)


class StagingSplitWorkflowAndProtocolTests(PrivateMediaTest):
    """Split entry windows (Angiography vs WIfI/PLAN vs Doppler), mandatory presentation strip, PLAN in proposal, and protocol generator."""

    def test_three_separate_windows_preserve_each_others_fields(self):
        case = self.make_case(side='R', proposal='ბარძაყ-წვივის აუტოვენური შუნტირება')
        angio_url = reverse('case-staging', args=[case.pk, 'R'])
        wifi_url = reverse('case-wifi', args=[case.pk, 'R'])
        doppler_url = reverse('case-doppler', args=[case.pk])

        # 1. Angiography window has zero Doppler and zero WIfI input fields
        angio_page = self.client.get(angio_url)
        self.assertContains(angio_page, 'id="st-proto-text"')
        self.assertContains(angio_page, 'id="st-copy-proto"')
        for forbidden in ('name="abi"', 'name="tbi"', 'name="ap"', 'name="tp"',
                          'name="w"', 'name="i"', 'name="fi"', 'name="wound_angiosome"'):
            self.assertNotContains(angio_page, forbidden)

        # 2. WIfI window has W, I, fI, angiosome, risk & vein, and zero Doppler or segment inputs
        wifi_page = self.client.get(wifi_url)
        for expected in ('name="w"', 'name="i"', 'name="fi"', 'name="wound_angiosome"', 'name="risk"', 'name="vein"'):
            self.assertContains(wifi_page, expected)
        for forbidden in ('name="abi"', 'name="tbi"', 'name="segments"', 'name="fp"'):
            self.assertNotContains(wifi_page, forbidden)
        self.assertEqual(self.client.get(reverse('case-wifi', args=[case.pk, 'X'])).status_code, 404)

        # 3. Doppler window GET + manual POST saves ABI, TBI, AP, and Toe pressure without creating a fake map slide
        doppler_page = self.client.get(doppler_url)
        self.assertContains(doppler_page, 'name="abi_R"')
        self.assertContains(doppler_page, 'name="tbi_R"')
        self.assertContains(doppler_page, 'name="ap_R"')
        self.assertContains(doppler_page, 'name="tp_R"')

        r_dop = self.client.post(doppler_url, {
            'action': 'manual',
            'abi_R': '0,48', 'tbi_R': '0,24', 'ap_R': '58', 'tp_R': '24',
            'abi_L': '', 'tbi_L': '', 'ap_L': '', 'tp_L': '',
        })
        self.assertRedirects(r_dop, reverse('case', args=[case.pk]) + '#doppler', fetch_redirect_response=False)
        case.refresh_from_db()
        self.assertEqual(
            (case.staging['R']['abi'], case.staging['R']['tbi'], case.staging['R']['ap'], case.staging['R']['tp']),
            (0.48, 0.24, 58.0, 24.0),
        )
        self.assertNotIn('L', case.staging)     # untouched side stays absent
        # Standalone Doppler numbers do not create a blank artery-map slide
        pres_after_dop = self.client.get(reverse('cases-present'), {'ids': case.pk})
        self.assertNotContains(pres_after_dop, 'class="pz-slide pz-stage-slide"')
        self.assertContains(pres_after_dop, '0.48')
        self.assertContains(pres_after_dop, '0.24')
        self.assertContains(pres_after_dop, '24')

        # 4. WIfI window saves W2, auto-ischemia (from TP=24 -> I3), fI1, risk=avg, vein=yes -> WIfI stage 4
        r_wifi = self.client.post(wifi_url, {
            'w': '2', 'i': '', 'fi': '1', 'wound_angiosome': 'medial_plantar',
            'risk': 'avg', 'vein': 'yes', 'note': 'მედიალური ქუსლისა და ძირის წყლული',
        })
        self.assertRedirects(r_wifi, reverse('case', args=[case.pk]) + '#staging', fetch_redirect_response=False)
        case.refresh_from_db()
        self.assertEqual((case.staging['R']['w'], case.staging['R']['fi'], case.staging['R']['abi'], case.staging['R']['tbi']),
                         (2, 1, 0.48, 0.24))

        # 5. Angiography window (_form='angio') saves segments + GLASS III while preserving WIfI & Doppler!
        r_angio = self.client.post(angio_url, {
            '_form': 'angio',
            'segments': json.dumps({'sfa1': {'stenosis': 'occlusion', 'calc': 4, 'flush': True},
                                    'sfa2': {'stenosis': 'occlusion'},
                                    'sfa3': {'stenosis': 'occlusion', 'reconstitution': 'yes'}}),
            'interventions': json.dumps([]),
            'tap': 'pt', 'fp': '4', 'fp_calc': 'on', 'ip': '2', 'pedal': 'P0', 'ai': '', 'cfa': 'A',
        })
        self.assertRedirects(r_angio, reverse('case', args=[case.pk]) + '#staging', fetch_redirect_response=False)
        case.refresh_from_db()
        limb = case.staging['R']
        self.assertEqual((limb['fp'], limb['ip'], limb['w'], limb['fi'], limb['wound_angiosome']),
                         (4, 2, 2, 1, 'medial_plantar'))
        self.assertEqual((limb['abi'], limb['tbi'], limb['ap'], limb['tp']), (0.48, 0.24, 58.0, 24.0))

        # 6. Conference presentation Slide 1 shows WIfI, GLASS, ABI, TBI, Toe pressure AND PLAN inside 'შეთავაზება'
        pres = self.client.get(reverse('cases-present'), {'ids': case.pk})
        self.assertContains(pres, 'class="pz-vitals"')
        self.assertContains(pres, '<small>WIfI</small>')
        self.assertContains(pres, '<small>GLASS</small>')
        self.assertContains(pres, '<small>ABI</small>')
        self.assertContains(pres, '<small>TBI</small>')
        self.assertContains(pres, '<small>Toe pressure</small>')
        self.assertContains(pres, '0.48')
        self.assertContains(pres, '0.24')
        # Doctor's own proposal AND automatic PLAN recommendation both appear inside 'შეთავაზება'
        self.assertContains(pres, 'ბარძაყ-წვივის აუტოვენური შუნტირება')
        self.assertContains(pres, 'PLAN ალგორითმი')
        self.assertContains(pres, 'ღია შუნტირება — საშუალო რისკი, ვარგისი ვენა')

        # Detail card & Case edit form also surface PLAN recommendation and 1-click insert button
        detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(detail, 'PLAN ალგორითმი')
        self.assertContains(detail, 'ღია შუნტირება — საშუალო რისკი, ვარგისი ვენა')
        edit_form = self.client.get(reverse('case-edit', args=[case.pk]))
        self.assertContains(edit_form, 'id="cs-insert-plan"')
        self.assertContains(edit_form, 'PLAN: ღია შუნტირება')

    def test_angiography_protocol_single_and_bilateral_generation(self):
        from .staging import angiography_protocol, angiography_protocol_bilateral, save_limb

        # 1. Normal right limb
        normal_txt = angiography_protocol({}, 'R')
        self.assertIn('ქვედა კიდურის არტერიოგრაფია — მარჯვენა კიდური', normal_txt)
        self.assertIn('ინფრარენული აორტა: გამავალია', normal_txt)
        self.assertIn('ჰემოდინამიკურად მნიშვნელოვანი სტენოზისა და ოკლუზიის გარეშე', normal_txt)
        self.assertNotIn('პუნქცია', normal_txt)
        self.assertNotIn('კონტრასტ', normal_txt)

        # 2. Multi-level disease + prior bypass -> grouped anatomical prose + GLASS conclusion
        diseased_r = {
            'segments': {
                'aorta': {'stenosis': 'moderate', 'calc': 3},
                'sfa2': {'stenosis': 'occlusion', 'calc': 4},
                'sfa3': {'stenosis': 'occlusion', 'reconstitution': 'yes'},
                'at1': {'stenosis': 'severe', 'length_cm': 2.5},
                'arch': {'stenosis': 'occlusion'},
            },
            'interventions': [
                {'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
                 'proximal': 'cfa', 'distal': 'pop3', 'performed_on': '06.2023', 'status': 'stenosed', 'site': 'distal'},
            ],
            'fp': 3, 'ip': 1, 'pedal': 'P1', 'tap': 'pt',
        }
        r_txt = angiography_protocol(diseased_r, 'R')
        self.assertIn('ინფრარენული აორტა: 50–69%-იანი სტენოზი', r_txt)
        self.assertIn('შუა და დისტალურ მესამედში — ოკლუზია', r_txt)
        self.assertIn('დისტალური სეგმენტი აღდგება კოლატერალებით', r_txt)
        self.assertIn('ტერფის რკალი შეკრული არ არის', r_txt)
        self.assertIn('შუნტირება: CFA → P3', r_txt)
        self.assertIn('GLASS სტადია II (FP 3, IP 1; P1; TAP — უკანა წვივის არტერია (PT))', r_txt)

        # 3. Bilateral protocol & live AJAX preview on second leg
        st_map = save_limb({}, 'R', diseased_r)
        st_map = save_limb(st_map, 'L', {'segments': {'cia': {'stenosis': 'occlusion', 'flush': True}}, 'fp': 0, 'ip': 0})
        bi_txt = angiography_protocol_bilateral(st_map)
        self.assertIn('ქვედა კიდურების არტერიოგრაფია', bi_txt)
        self.assertEqual(bi_txt.count('• ინფრარენული აორტა:'), 1)
        self.assertIn('მარჯვენა ქვედა კიდური:', bi_txt)
        self.assertIn('მარცხენა ქვედა კიდური:', bi_txt)
        self.assertIn('საერთო თეძოს არტერია — სათავის (flush) ოკლუზია', bi_txt)

        case = self.make_case(side='B', staging=st_map)
        preview = self.client.post(
            reverse('case-staging', args=[case.pk, 'L']),
            {'_form': 'angio', 'segments': json.dumps({'pt2': {'stenosis': 'severe'}}), 'fp': '0', 'ip': '2', 'preview': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).json()
        self.assertIn('უკანა წვივის არტერია: შუა მესამედში — 70–99%-იანი კრიტიკული სტენოზი', preview['protocol_text'])
        self.assertIn('მარჯვენა ქვედა კიდური:', preview['protocol_bilateral_text'])
        self.assertIn('უკანა წვივის არტერია: შუა მესამედში — 70–99%-იანი კრიტიკული სტენოზი', preview['protocol_bilateral_text'])



class PreopTests(PrivateMediaTest):
    """Tweek-style preoperative calendar, Infrarenal AAA nurse POA, and Cardiology Audit."""

    def test_tweek_calendar_case_transfer_and_clock_persistence(self):
        import datetime
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import evaluate

        # Menu shows "წინასაოპერაციო"
        cal = self.client.get(reverse('preop'))
        self.assertEqual(cal.status_code, 200)
        self.assertContains(cal, 'წინასაოპერაციო')
        self.assertContains(cal, 'ინფრარენალური AAA')

        # 1-click transfer from a Conference Case -> Someday "conf" list (no duplicate on second click)
        case = self.make_case(patient_name='ბერიძე გიორგი', age=69, sex='M', decision='EVAR')
        r1 = self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertRedirects(r1, reverse('preop-patient', args=[p.pk]))
        self.assertEqual((p.op_subtype, p.someday_list), ('evar', 'conf'))
        r2 = self.client.get(reverse('preop-new'), {'case': case.pk})
        self.assertEqual(PreopPatient.objects.filter(case=case).count(), 1)
        self.assertRedirects(r2, reverse('preop-patient', args=[p.pk]))

        # Inline quick-add onto a visit date 9 days ago -> overdue (>7 days) Red Flag
        nine_days_ago = (timezone.localdate() - datetime.timedelta(days=8)).isoformat()
        self.client.post(reverse('preop-new'), {
            'patient_name': 'მაისურაძე ოთარი',
            'slot': nine_days_ago,
            'op_subtype': 'open',
            'surgeon': str(self.user.pk),
        })
        late = PreopPatient.objects.get(patient_name='მაისურაძე ოთარი')
        self.assertEqual(evaluate(late)['days_elapsed'], 9)

        # Drag-and-drop move to tomorrow does NOT reset started_on or the 7-day rupture clock
        tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        mv = self.client.post(
            reverse('preop-move', args=[late.pk]),
            {'slot': tomorrow},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).json()
        late.refresh_from_db()
        self.assertEqual(str(late.visit_date), tomorrow)
        self.assertEqual(mv['days_elapsed'], 9)

    def test_aaa_thresholds_two_stage_flow_anamnesis_and_cardiology_audit(self):
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_NO_Q, STATUS_PROGRESS, STATUS_READY, evaluate
        from .views import BACKUP_MODELS

        self.assertIn('mdf.preoppatient', BACKUP_MODELS)
        self.assertIn('mdf.preopfile', BACKUP_MODELS)

        today = timezone.localdate().isoformat()
        self.client.post(reverse('preop-new'), {
            'patient_name': 'კაპანაძე თამაზი',
            'age': '70',
            'sex': 'M',
            'slot': today,
            'op_subtype': 'open',
            'surgeon': str(self.user.pk),
        })
        p = PreopPatient.objects.get(patient_name='კაპანაძე თამაზი')
        self.assertEqual(evaluate(p)['status'], STATUS_NO_Q)

        # Step 1: Nurse fills questionnaires (asymptomatic AAA 60mm, >=4 METs, active ulcer symptoms, COPD/pulm, clopidogrel)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1',
            'htn': '1',
            'ulcer': '1',
            'ulcer_active_symp': '1',
            'pulm': '1',
            'smoker': '1',
            'med_aspirin': '1',
            'med_clopidogrel': '1',
            'other_meds': 'პერინდოპრილი 5 მგ',
            'aaa_mm': '60',
            'cta_months': '1',
            'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_PROGRESS)
        self.assertTrue(ev['needs_gastro'])
        self.assertTrue(ev['needs_spirometry'])
        self.assertTrue(ev['needs_med_hold'])

        # Step 2: Stage 1 labs with pyuria + bacteriuria (urine WBC 18 + nitrite+) -> triggers UTI prosthetic-graft Red Flag
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '135', 'bp_dia': '82', 'hr': '70', 'spo2': '97',
            'hb': '13.6',           # g/dL auto-normalized to 136 g/L
            'wbc': '7.5', 'plt': '210', 'inr': '1.05',
            'creat': '88.4',        # 1.0 mg/dL -> normal eGFR >= 60
            'k': '4.2', 'na': '140', 'glucose': '5.5', 'albumin': '39',
            'urine_wbc': '18', 'urine_nitrite': '1',
            'carotid_r': '30', 'carotid_l': '25',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '56',
            'blood_group': 'A (II) Rh (+)',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['hb_gl'], 136.0)
        self.assertGreaterEqual(ev['egfr'], 60)
        self.assertTrue(ev['uti_suspected'])
        self.assertEqual(ev['status'], STATUS_ALERT)

        # Clean urinalysis, but cardiologist orders CAG in asymptomatic >=4 METs LVEF 56% patient -> ESC 2022 Class III audit flag!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '135', 'bp_dia': '82', 'hr': '70', 'spo2': '97',
            'hb': '136', 'wbc': '7.5', 'plt': '210', 'inr': '1.05',
            'creat': '88.4', 'k': '4.2', 'na': '140', 'glucose': '5.5', 'albumin': '39',
            'urine_wbc': '2',
            'carotid_r': '30', 'carotid_l': '25',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '56',
            'blood_group': 'A (II) Rh (+)',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'spiro_fev1': '78',
            'gastro_result': 'remission',
            'blood_reserved': '1',
            'med_hold_agreed': '1',
            'anesth_done': '1',
            'cardiologist': 'გარე კარდიოლოგი',
            'cardio_decision': 'cag',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['cardio_non_compliant'])
        self.assertEqual(ev['status'], STATUS_ALERT)

        audit_page = self.client.get(reverse('preop-audit'))
        self.assertContains(audit_page, 'ESC 2022 Class III')
        self.assertContains(audit_page, 'ლიკა ინჯია')

        # Lika Injia clears the patient -> all tasks complete -> STATUS_READY!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'spiro_fev1': '78',
            'gastro_result': 'remission',
            'blood_reserved': '1',
            'med_hold_agreed': '1',
            'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertFalse(ev['cardio_non_compliant'])
        self.assertEqual(ev['status'], STATUS_READY)

        # Auto-anamnesis & Surgeon sign-off moves patient to Ready Someday list
        page = self.client.get(reverse('preop-patient', args=[p.pk]), {'tab': 'doc'})
        self.assertContains(page, '60 მმ')
        self.assertContains(page, 'კლოპიდოგრელი')
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'signoff',
            'action': 'approve',
            'move_ready': '1',
            'anamnesis': 'დადასტურებული მოკლე ანამნეზი.',
        })
        p.refresh_from_db()
        self.assertIsNotNone(p.approved_at)
        self.assertEqual(p.someday_list, 'ready')

        # Handoff prefills to Prescription and Blood Order
        rx = self.client.get(reverse('niv-cover'), {'preop': p.pk})
        self.assertContains(rx, 'კაპანაძე თამაზი')
        blood = self.client.get(reverse('blood-order'), {'preop': p.pk})
        self.assertContains(blood, 'A (II) Rh (+)')

    def test_private_photo_and_pdf_uploads_and_cleanup(self):
        import os
        from PIL import Image
        from .models import PreopFile, PreopPatient

        p = PreopPatient.objects.create(patient_name='გელაშვილი ზურაბი', created_by=self.user)
        buf = io.BytesIO()
        Image.new('RGB', (400, 300), (200, 210, 220)).save(buf, 'PNG')
        buf.seek(0)
        buf.name = 'ecg.png'

        pdf_buf = io.BytesIO(b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n')
        pdf_buf.name = 'labs.pdf'

        self.client.post(reverse('preop-files', args=[p.pk]), {
            'category': 'labs',
            'caption': 'სისხლის საერთო',
            'file': [buf, pdf_buf],
        })
        self.assertEqual(PreopFile.objects.filter(patient=p).count(), 2)
        img_rec = PreopFile.objects.get(patient=p, is_pdf=False)
        pdf_rec = PreopFile.objects.get(patient=p, is_pdf=True)
        img_path = img_rec.file.path
        pdf_path = pdf_rec.file.path

        self.assertEqual(self.client.get(reverse('preop-file', args=[img_rec.pk]))['Content-Type'], 'image/jpeg')
        self.assertEqual(self.client.get(reverse('preop-file', args=[pdf_rec.pk]))['Content-Type'], 'application/pdf')

        # Unauthenticated users cannot read private preoperative files
        self.client.logout()
        self.assertEqual(self.client.get(reverse('preop-file', args=[pdf_rec.pk])).status_code, 302)

        # Deleting patient deletes underlying private files on disk
        self.client.force_login(self.user)
        self.client.post(reverse('preop-move', args=[p.pk]), {'action': 'delete'})
        self.assertFalse(PreopPatient.objects.filter(pk=p.pk).exists())
        self.assertFalse(os.path.exists(img_path))
        self.assertFalse(os.path.exists(pdf_path))


class PreopCeaAndAbfTests(PrivateMediaTest):
    """Carotid Endarterectomy (CEA — ESVS 2023) & Aortobifemoral Bypass (ABF — ESVS 2024 / GVG) tests."""

    def test_cea_esvs_2023_symptomatic_flow_laryngoscopy_antiplatelet_and_14day_window(self):
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_READY, evaluate, generate_anamnesis

        # 1-click Case transfer auto-detects Symptomatic Right CEA and 5-day urgent target
        case = self.make_case(
            patient_name='ნოზაძე მერაბი', age=68, sex='M', side='R',
            diagnosis='მარჯვენა შიგნითა საძილე არტერიის სიმპტომური სტენოზი (TIA)',
            decision='კაროტიდული ენდარტერექტომია',
        )
        r = self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertRedirects(r, reverse('preop-patient', args=[p.pk]))
        self.assertEqual((p.pathway, p.op_subtype, p.side), ('cea', 'cea_symp', 'R'))
        self.assertEqual(evaluate(p)['target_days'], 5)

        # Questionnaire: 16 days since TIA (>14d window), prior thyroid surgery, NO antiplatelet
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'cea_symp_event': '1',
            'cea_days_since_event': '16',
            'cea_event_text': 'მარცხენა მტევნის გარდამავალი სისუსტე',
            'cea_prior_neck': '1',
            'cea_prior_neck_text': 'თირეოიდექტომია 2019',
            'mets_ok': '1',
            'htn': '1',
            'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_neuro'])
        self.assertTrue(ev['needs_laryngoscopy'])
        self.assertTrue(ev['needs_brain_ct'])
        self.assertFalse(ev['needs_spirometry'])
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('14-დღიანი', joined_red)
        self.assertIn('ანტიაგრეგანტს', joined_red)

        # Within 14-day window (5d ago) + Aspirin, but Stage 1 has BP 186/112 & contralateral 85% stenosis
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'cea_symp_event': '1',
            'cea_days_since_event': '5',
            'cea_event_text': 'მარცხენა მტევნის გარდამავალი სისუსტე',
            'cea_prior_neck': '1',
            'cea_prior_neck_text': 'თირეოიდექტომია 2019',
            'mets_ok': '1',
            'htn': '1',
            'med_aspirin': '1',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '186', 'bp_dia': '112', 'hr': '68', 'spo2': '98',
            'hb': '142', 'wbc': '6.8', 'plt': '230', 'inr': '1.02',
            'creat': '84', 'k': '4.3', 'na': '140', 'glucose': '5.4',
            'urine_wbc': '1',
            'carotid_r': '82', 'carotid_l': '85',
            'cea_cta_status': 'concordant',
            'brain_imaging': 'normal_small',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '58',
            'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['s1_core_done'])
        self.assertEqual(ev['key_badge'], 'R 82%')
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('ჰიპერპერფუზიის', joined_red)
        self.assertIn('კონტრალატერალური', joined_red)

        # Ipsilateral 100% occlusion triggers non-operative occlusion Red Flag
        p.studies['carotid_r'] = 100
        p.save()
        self.assertIn('სრული ოკლუზია (100%)', ' | '.join(evaluate(p)['red_flags']))

        # Stabilized BP + contralateral 35%; vocal cord palsy triggers Red Flag, normal clears to READY
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '136', 'bp_dia': '80', 'hr': '68', 'spo2': '98',
            'hb': '142', 'wbc': '6.8', 'plt': '230', 'inr': '1.02',
            'creat': '84', 'k': '4.3', 'na': '140', 'glucose': '5.4',
            'urine_wbc': '1',
            'carotid_r': '82', 'carotid_l': '35',
            'cea_cta_status': 'concordant',
            'brain_imaging': 'normal_small',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '58',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'neuro_consult_done': '1',
            'laryngoscopy_res': 'palsy',
            'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
        })
        p.refresh_from_db()
        self.assertIn('სახმო იოგის პარეზი', ' | '.join(evaluate(p)['red_flags']))

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'neuro_consult_done': '1',
            'laryngoscopy_res': 'normal',
            'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_READY)
        anam = generate_anamnesis(p, ev)
        self.assertIn('მარჯვენა კაროტიდული ენდარტერექტომია', anam)
        self.assertIn('R ICA 82%', anam)
        self.assertIn('სახმო იოგები მოძრავია', anam)

        # Prescription prefill marks neuro=True and formats carotid diagnosis
        rx = self.client.get(reverse('niv-cover'), {'preop': p.pk})
        self.assertContains(rx, 'მარჯვენა შიგნითა საძილე არტერიის სიმპტომური სტენოზი (82%)')
        self.assertTrue(rx.context['niv'].neuro)

    def test_abf_esvs_2024_clti_wet_gangrene_groin_outflow_spirometry_and_pathway_filters(self):
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_READY, evaluate, generate_anamnesis

        # Case transfer auto-detects ABF CLTI with 7-day target
        case = self.make_case(
            patient_name='ლომიძე ვახტანგი', age=64, sex='M', side='B',
            diagnosis='ლერიშის სინდრომი, ქვემო კიდურების კრიტიკული იშემია (CLTI)',
            decision='აორტო-ბიფემორალური შუნტირება',
        )
        self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertEqual((p.pathway, p.op_subtype), ('abf', 'abf_clti'))
        self.assertEqual(evaluate(p)['target_days'], 7)

        # Step 1: Wet foot infection + groin intertrigo -> synthetic graft sepsis Red Flags
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'abf_claud_m': '25',
            'abf_duration': '8 თვე',
            'abf_buttock_pain': '1',
            'abf_rest_pain': '1',
            'abf_ulcer': '1',
            'abf_ulcer_text': 'მარჯვენა I თითის წყლული',
            'abf_wet_infection': '1',
            'abf_groin_issue': '1',
            'abf_prior_vasc': '1',
            'abf_prior_vasc_text': 'მარჯვენა CIA სტენტი 2022',
            'mets_ok': '1',
            'pulm': '1',
            'smoker': '1',
            'med_aspirin': '1',
            'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_spirometry'])
        self.assertTrue(ev['needs_blood'])
        self.assertTrue(ev['needs_wound_care'])
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('სველი ნეკროზი', joined_red)
        self.assertIn('ინტერტრიგო', joined_red)

        # Step 2: Dry ulcer + clean groin, but Stage 1 has juxtarenal occlusion, poor outflow & albumin 27
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'abf_claud_m': '25',
            'abf_duration': '8 თვე',
            'abf_buttock_pain': '1',
            'abf_rest_pain': '1',
            'abf_ulcer': '1',
            'abf_ulcer_text': 'მარჯვენა I თითის მშრალი ნეკროზი',
            'abf_prior_vasc': '1',
            'abf_prior_vasc_text': 'მარჯვენა CIA სტენტი 2022',
            'mets_ok': '1',
            'pulm': '1',
            'smoker': '1',
            'med_aspirin': '1',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'abi_r': '0.32', 'abi_l': '0.41',
            'abf_clamp_zone': 'juxtarenal_calcified',
            'abf_outflow': 'poor_outflow',
            'bp_sys': '140', 'bp_dia': '84', 'hr': '76', 'spo2': '96',
            'hb': '132', 'wbc': '7.9', 'plt': '245', 'inr': '1.04',
            'creat': '90', 'k': '4.4', 'na': '139', 'glucose': '5.8', 'albumin': '27',
            'urine_wbc': '3',
            'carotid_r': '35', 'carotid_l': '20',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '55',
            'cta_disc': '1',
            'blood_group': 'O (I) Rh (+)',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['s1_core_done'])
        self.assertEqual(ev['key_badge'], 'ABI 0.32')
        joined_red = ' | '.join(ev['red_flags'])
        self.assertIn('იუქსტარენალური', joined_red)
        self.assertIn('Outflow', joined_red)
        self.assertIn('ჰიპოალბუმინემია', joined_red)

        # Step 3: Infrarenal clamp + profunda stenosis (Amber profundaplasty note) + albumin 38 + Stage 2 done -> READY
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'abi_r': '0.32', 'abi_l': '0.41',
            'abf_clamp_zone': 'infrarenal',
            'abf_outflow': 'profunda_stenosis',
            'bp_sys': '140', 'bp_dia': '84', 'hr': '76', 'spo2': '96',
            'hb': '132', 'wbc': '7.9', 'plt': '245', 'inr': '1.04',
            'creat': '90', 'k': '4.4', 'na': '139', 'glucose': '5.8', 'albumin': '38',
            'urine_wbc': '3',
            'carotid_r': '35', 'carotid_l': '20',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '55',
            'cta_disc': '1',
            'blood_group': 'O (I) Rh (+)',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'spiro_fev1': '74',
            'wound_care_done': '1',
            'blood_reserved': '1',
            'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_READY)
        self.assertIn('პროფუნდოპლასტიკა', ' | '.join(ev['amber_flags']))
        anam = generate_anamnesis(p, ev)
        self.assertIn('აორტო-ბიფემორალური შუნტირება', anam)
        self.assertIn('ABI R 0.32', anam)

        # Pathway filters on Tweek weekly view and Cardiology Audit view
        cal_abf = self.client.get(reverse('preop'), {'pathway': 'abf'})
        self.assertContains(cal_abf, 'ლომიძე ვახტანგი')
        cal_cea = self.client.get(reverse('preop'), {'pathway': 'cea'})
        self.assertNotContains(cal_cea, 'ლომიძე ვახტანგი')

        audit_abf = self.client.get(reverse('preop-audit'), {'pathway': 'abf'})
        self.assertContains(audit_abf, 'ლომიძე ვახტანგი')

        blood = self.client.get(reverse('blood-order'), {'preop': p.pk})
        self.assertContains(blood, 'აორტო-ილიაკური ოკლუზიური დაავადება')
        self.assertContains(blood, 'O (I) Rh (+)')

    def test_cea_crescendo_thrombolysis_brain_hemorrhage_and_cardio_audit_stroke_window(self):
        import datetime
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import evaluate

        six_days_ago = (timezone.localdate() - datetime.timedelta(days=5)).isoformat()
        self.client.post(reverse('preop-new'), {
            'patient_name': 'ჭანტურია ნინო',
            'age': '71',
            'sex': 'F',
            'side': 'L',
            'slot': six_days_ago,
            'op_subtype': 'cea_symp',
            'surgeon': str(self.user.pk),
        })
        p = PreopPatient.objects.get(patient_name='ჭანტურია ნინო')
        self.assertEqual((p.pathway, p.side), ('cea', 'L'))

        # Crescendo TIA + thrombolysis <6d + disabling stroke + >5 prep days -> all flagged
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'L',
            'cea_symp_event': '1',
            'cea_days_since_event': '4',
            'cea_crescendo': '1',
            'cea_thrombolysis': '1',
            'cea_disabling_stroke': '1',
            'mets_ok': '1',
            'med_aspirin': '1',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '130', 'bp_dia': '80', 'hr': '72', 'spo2': '97',
            'hb': '128', 'wbc': '7.1', 'plt': '215', 'inr': '1.0',
            'creat': '75', 'k': '4.1', 'na': '140', 'glucose': '5.2',
            'urine_wbc': '2',
            'carotid_r': '20', 'carotid_l': '78',
            'cea_cta_status': 'high_calcified',
            'brain_imaging': 'large_or_hemorrhage',
            'cxr': 'normal', 'ecg': 'normal', 'lvef': '60',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'cardiologist': 'გარე კარდიოლოგი',
            'cardio_decision': 'stress',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['over_target_days'])
        self.assertTrue(ev['cardio_non_compliant'])
        red_blob = ' | '.join(ev['red_flags'])
        self.assertIn('Crescendo TIA', red_blob)
        self.assertIn('თრომბოლიზისი', red_blob)
        self.assertIn('mRS >= 3', red_blob)
        self.assertIn('>1/3 MCA', red_blob)
        self.assertIn('ESC 2022 Class III', red_blob)

        page = self.client.get(reverse('preop-patient', args=[p.pk]), {'tab': 's2'})
        self.assertContains(page, '14-დღიანი თერაპიული ფანჯრის')

        # Elective Claudication ABF uses 14-day prep target; Asymptomatic CEA uses 10-day prep target
        p_asymp = PreopPatient.objects.create(patient_name='ასიმპტომური ტესტი', op_subtype='cea_asymp', side='L')
        self.assertEqual((p_asymp.pathway, evaluate(p_asymp)['target_days']), ('cea', 10))
        p_claud = PreopPatient.objects.create(patient_name='ლერიშის ტესტი', op_subtype='abf_claud')
        self.assertEqual((p_claud.pathway, evaluate(p_claud)['target_days']), ('abf', 14))


class PreopVenousTests(PrivateMediaTest):
    """EVLA & Phlebectomy (ESVS 2022 / ESC 2022) lean day-surgery pathway & Patient Self-Fill Link."""

    def test_evla_lean_day_surgery_and_signed_patient_self_fill_link(self):
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_READY, evaluate, generate_anamnesis, make_self_fill_token

        self.client.post(reverse('preop-new'), {
            'patient_name': 'ჯაფარიძე სალომე',
            'slot': timezone.localdate().isoformat(),
            'op_subtype': 'evla',
            'side': 'L',
            'surgeon': str(self.user.pk),
        })
        p = PreopPatient.objects.get(patient_name='ჯაფარიძე სალომე')
        self.assertEqual((p.pathway, p.op_subtype, p.side, p.sex), ('venous', 'evla', 'L', 'F'))

        # Unauthenticated patient fills own questionnaire via signed token; bad token gets 404
        token = make_self_fill_token(p)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('preop-self-fill', args=['tampered-token'])).status_code, 404)

        sf_get = self.client.get(reverse('preop-self-fill', args=[token]))
        self.assertEqual(sf_get.status_code, 200)
        self.assertContains(sf_get, 'ჯაფარიძე სალომე')

        sf_post = self.client.post(reverse('preop-self-fill', args=[token]), {
            'age': '34',
            'weight_kg': '62',
            'height_cm': '168',
            'side': 'L',
            'ven_ceap': 'C2',
            'mets_ok': '1',
        }, follow=True)
        self.assertContains(sf_post, 'კითხვარი შენახულია')

        p.refresh_from_db()
        ev = evaluate(p)
        self.assertTrue(ev['q']['filled'])
        self.assertEqual(ev['q']['filled_by'], 'პაციენტი (ბმულით)')
        # Healthy young EVLA does NOT demand routine ECG, Spirometry, Cardiology, or Anesthesiology!
        self.assertFalse(ev['needs_ecg_venous'])
        self.assertFalse(ev['needs_spirometry'])
        self.assertFalse(ev['needs_cardio'])
        self.assertFalse(ev['needs_anesth'])
        self.assertEqual(ev['total_count'], 3)  # Q + Stage 1 + Class II Compression Stocking

        # Nurse enters lean Stage 1 + Compression Stocking ready -> STATUS_READY!
        self.client.force_login(self.user)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'hb': '132', 'wbc': '6.4', 'plt': '245', 'inr': '1.01', 'glucose': '4.9',
            'ven_duplex_status': 'deep_patent_reflux',
            'ven_target_vein': 'gsv',
            'ven_gsv_mm': '7.5',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'compression_ready': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_READY)
        self.assertEqual(ev['done_count'], 3)
        anam = generate_anamnesis(p, ev)
        self.assertIn('ენდოვენური ლაზერული აბლაცია', anam)
        self.assertIn('დიდი საჩინო ვენა (GSV)', anam)
        self.assertIn('7.5 მმ', anam)

        # ESVS 2022 Class IIa: Warfarin with INR 2.4 on EVLA raises Amber coordination note, NOT coagulation Red Flag
        p.questionnaire['med_warfarin'] = True
        p.studies['inr'] = 2.4
        p.save()
        ev_w = evaluate(p)
        self.assertFalse(any('INR > 1.35' in m for m in ev_w['red_flags']))
        self.assertTrue(any('ESVS 2022' in m for m in ev_w['amber_flags']))

    def test_phlebectomy_contraindications_dvt_lidocaine_pregnancy_abi_and_vte_prophylaxis(self):
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_READY, evaluate

        # Case transfer auto-detects Phlebectomy
        case = self.make_case(
            patient_name='კიკნაძე ნანა', age=62, sex='F', side='R',
            diagnosis='მარჯვენა ქვემო კიდურის ვენების ვარიკოზული დაავადება',
            decision='კომბინირებული ფლებექტომია',
        )
        self.client.get(reverse('preop-new'), {'case': case.pk})
        p = PreopPatient.objects.get(case=case)
        self.assertEqual((p.pathway, p.op_subtype, p.side), ('venous', 'phlebectomy', 'R'))

        # Contraindications: lidocaine allergy + pregnancy + skin infection + DVT + DOAC on spinal phlebectomy
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'ven_ceap': 'C4',
            'ven_dvt_history': '1',
            'ven_lidocaine_allergy': '1',
            'ven_skin_infection': '1',
            'ven_pregnant': '1',
            'claudication': '1',
            'med_doac': '1',
            'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'weight_kg': '94', 'height_cm': '165',
            'hb': '128', 'plt': '220', 'inr': '1.05', 'glucose': '5.3', 'creat': '74',
            'ven_duplex_status': 'acute_dvt',
            'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'abi_r': '0.72', 'abi_l': '0.95',
        })
        p.refresh_from_db()
        ev = evaluate(p)
        self.assertEqual(ev['status'], STATUS_ALERT)
        self.assertTrue(ev['needs_vte_prophylaxis'])
        self.assertTrue(ev['needs_abi_check'])
        self.assertTrue(ev['needs_anesth'])
        red_blob = ' | '.join(ev['red_flags'])
        amber_blob = ' | '.join(ev['amber_flags'])
        self.assertIn('DVT', red_blob)
        self.assertIn('ლიდოკაინზე', red_blob)
        self.assertIn('ორსულობა', red_blob)
        self.assertIn('წითელი ქარი', red_blob)
        self.assertIn('ABI 0.72 < 0.80', red_blob)
        self.assertIn('სპინალური ანესთეზიისა და ფლებექტომიის', amber_blob)

        # NIV prescription prefill carries lidocaine allergy -> allergy=True and CEAP C4 diagnosis
        rx = self.client.get(reverse('niv-cover'), {'preop': p.pk})
        self.assertTrue(rx.context['niv'].allergy)
        self.assertContains(rx, 'CEAP C4')

        # Resolve contraindications: deep veins patent, normal ABI 1.05, VTE prophylaxis + med hold + anesth + compression -> READY
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'ven_ceap': 'C4',
            'ven_dvt_history': '1',
            'claudication': '1',
            'med_doac': '1',
            'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'weight_kg': '94', 'height_cm': '165',
            'hb': '128', 'plt': '220', 'inr': '1.05', 'glucose': '5.3', 'creat': '74',
            'ven_duplex_status': 'deep_patent_reflux',
            'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'abi_r': '1.05', 'abi_l': '1.02',
            'vte_prophylaxis_planned': '1',
            'compression_ready': '1',
            'med_hold_agreed': '1',
            'anesth_done': '1',
        })
        p.refresh_from_db()
        self.assertEqual(evaluate(p)['status'], STATUS_READY)


class PreopReflexZeroTextAndPeripheralTests(PrivateMediaTest):
    """Standing Reflex Protocols, Zero-Free-Text Chips, Med Schedule, PAD Bypass, PAD PTA, Passport & Consent."""

    def test_standing_reflex_orders_zero_text_chips_med_schedule_passport_and_consent(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import PreopPatient, StaffProfile
        from .preop import (
            STATUS_ALERT, STATUS_PROGRESS, STATUS_READY,
            consent_url_for_preop, evaluate, generate_anamnesis,
        )
        StaffProfile.objects.update_or_create(user=self.user, defaults={'full_name': 'გიორგი აფხაძე'})

        today = timezone.localdate()
        p = PreopPatient.objects.create(
            patient_name='რეფლექს ტესტაძე',
            age=71,
            sex='M',
            pathway='aaa_infra',
            op_subtype='evar',
            surgeon=self.user,
            visit_date=today,
            started_on=today,
        )

        # 1. Nurse fills questionnaire purely via 1-tap chips (Zero Free-Text)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'aaa_pain': '0',
            'cfs_level': '1_3',
            'aaa_pedal_pulse': 'reduced',
            'htn': '1',
            'cad': '1',
            'cad_chips': ['mi', 'afib'],
            'dm': '1',
            'thyroid_disease': '1',
            'dental_infection': '1',
            'med_aspirin': '1',
            'med_doac': '1',
            'sglt2': '1',
            'metformin': '1',
            'med_chips': ['statin', 'beta_blocker'],
            'allergy_iodine': '1',
            'allergy_other': '1',
            'allergy_chips': ['beta_lactam'],
            'mets_ok': '1',
        })

        # 2. Nurse enters Stage 1 labs: Hb 108 (<115), Glucose 8.4 (>=7.0, no HbA1c yet),
        #    Creatinine 168 (eGFR < 45 & < 60), stale labs (>14 days ago)
        stale_dt = (today - timedelta(days=18)).isoformat()
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '136', 'bp_dia': '82', 'hr': '68', 'spo2': '97',
            'hb': '108', 'wbc': '7.1', 'plt': '215', 'inr': '1.08', 'fibrinogen': '3.4',
            'creat': '168', 'k': '4.4', 'na': '139', 'glucose': '8.4', 'albumin': '39', 'ldl': '1.9',
            'labs_date': stale_dt,
            'urine_wbc': '3', 'liver_status': 'normal',
            'carotid_r': '20', 'carotid_l': '25', 'lvef': '56', 'aaa_mm': '59',
            'cxr': 'normal', 'ecg': 'normal',
            'aaa_iliac_access': 'ok_7mm',
            'cta_disc': '1',
        })
        p.refresh_from_db()
        ev = evaluate(p)

        # Verify every autonomous Standing Reflex Order activated automatically
        reflex_keys = {r['key'] for r in ev['reflex_orders']}
        self.assertTrue({'aaa_abi', 'anemia', 'nephro', 'hydration', 'hba1c', 'tsh', 'dental', 'contrast_premed', 'stale_labs'}.issubset(reflex_keys))
        self.assertIn('ვანკომიცინი ან კლინდამიცინი', ev['abx_prophylaxis_note'])

        # Verify Automated Perioperative Medication Schedule (DOAC -72h because eGFR < 50, SGLT2 -72h, Aspirin/Statin continue)
        sched_by_drug = {m['drug']: m for m in ev['med_schedule']}
        self.assertIn('72 საათით', sched_by_drug['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertIn('72 საათით (3 დღით) ადრე', sched_by_drug['SGLT2 (ჯარდიანსი / ფორსიგა)']['rule'])
        self.assertEqual(sched_by_drug['ასპირინი (75–100 მგ)']['tone'], 'ok')
        self.assertEqual(sched_by_drug['სტატინი']['tone'], 'ok')

        # Verify Tab 3 renders the medication schedule rule text cleanly
        tab_s2 = self.client.get(reverse('preop-patient', args=[p.pk]) + '?tab=s2')
        self.assertContains(tab_s2, 'DOAC (ქსარელტო / ელიქვისი)')
        self.assertContains(tab_s2, '72 საათით (3 დღით) ადრე')

        # Verify Zero-Free-Text auto-generated Georgian anamnesis contains chip labels
        anam = generate_anamnesis(p, ev)
        self.assertIn('გადატანილი ინფარქტი', anam)
        self.assertIn('მოციმციმე არითმია', anam)
        self.assertIn('პენიცილინი / ცეფაზოლინი (β-ლაქტამი)', anam)

        # 3. Nurse enters HbA1c = 9.1% (>=8.5%) + contrast premedication -> escalates to Endocrinology reflex (STATUS_PROGRESS);
        #    completes all reflex items + 1-tap cardio_note_chips & nurse_note_chips -> STATUS_READY!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'hba1c': '9.1',
            'contrast_premed_done': '1',
        })
        p.refresh_from_db()
        self.assertTrue(evaluate(p)['needs_endo_reflex'])
        self.assertEqual(evaluate(p)['status'], STATUS_PROGRESS)

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'stale_labs_refreshed': '1',
            'ferritin': '42',
            'anemia_panel_done': '1',
            'hba1c': '9.1',
            'endo_consult_done': '1',
            'nephro_panel_done': '1',
            'hydration_planned': '1',
            'tsh_status': 'euthyroid',
            'dental_sanitation_done': '1',
            'contrast_premed_done': '1',
            'abi_r': '0.94', 'abi_l': '0.91',
            'gastro_result': 'remission',
            'evar_sized': '1',
            'decolonization_ready': '1',
            'med_hold_agreed': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
            'cardio_note_chips': ['cleared_rx', 'added_bb_statin'],
            'nurse_note_chips': ['fasting_instructed', 'family_contacted'],
            'anesth_done': '1',
        })
        p.refresh_from_db()
        ev_done = evaluate(p)
        self.assertEqual(ev_done['status'], STATUS_READY)
        self.assertIn('დაშვებულია მიმდინარე თერაპიის ფონზე', p.cardio_note)
        self.assertIn('უზმოდ მოსვლა და წამლების სქემა განემარტა', p.studies['nurse_note'])

        # 4. 1-Page Printable Preop Passport (/preop/<pk>/passport/) & Direct Consent Prefill
        passport_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertEqual(passport_resp.status_code, 200)
        self.assertContains(passport_resp, 'წინასაოპერაციო სამედიცინო პასპორტი')
        self.assertContains(passport_resp, 'რეფლექს ტესტაძე')
        self.assertContains(passport_resp, 'ვანკომიცინი ან კლინდამიცინი')

        c_url = consent_url_for_preop(p)
        self.assertIn(f'preop={p.pk}', c_url)
        consent_resp = self.client.get(c_url)
        self.assertEqual(consent_resp.status_code, 200)
        self.assertContains(consent_resp, 'რეფლექს ტესტაძე')
        self.assertContains(consent_resp, 'გიორგი აფხაძე')

        # 5. HIT heparin allergy chip triggers immediate Red Flag
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'allergy_other': '1',
            'allergy_chips': ['hit_heparin'],
            'mets_ok': '1',
        })
        p.refresh_from_db()
        ev_hit = evaluate(p)
        self.assertEqual(ev_hit['status'], STATUS_ALERT)
        self.assertTrue(any('HIT' in rf for rf in ev_hit['red_flags']))

    def test_bilateral_arm_bp_asymmetry_on_cea_and_venous_caprini_score(self):
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import evaluate

        today = timezone.localdate()
        cea = PreopPatient.objects.create(
            patient_name='კაროტიდ წნევაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_asymp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1',
            'bp_sys': '154', 'bp_dia': '86',
            'bp_sys_l': '122', 'bp_dia_l': '74',
            'hb': '140', 'creat': '88', 'inr': '1.0', 'urine_wbc': '1',
            'carotid_r': '80', 'carotid_l': '30', 'cea_cta_status': 'concordant',
            'lvef': '60', 'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        cea.refresh_from_db()
        ev_cea = evaluate(cea)
        self.assertEqual(ev_cea['bp_arm_diff'], 32)
        self.assertTrue(any('ლავიწქვეშა არტერიის სტენოზი' in af for af in ev_cea['amber_flags']))

        ven = PreopPatient.objects.create(
            patient_name='ვენა კაპრინიძე', age=66, sex='F', side='L',
            pathway='venous', op_subtype='evla', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'ven_ceap': 'C4', 'ven_dvt_history': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage1',
            'weight_kg': '96', 'height_cm': '162',
            'hb': '130', 'plt': '240', 'inr': '1.0', 'glucose': '5.2',
            'ven_duplex_status': 'deep_patent_reflux', 'ven_epifascial': '1', 'ecg': 'normal',
        })
        ven.refresh_from_db()
        ev_ven = evaluate(ven)
        self.assertGreaterEqual(ev_ven['caprini_score'], 7)
        self.assertTrue(ev_ven['needs_vte_prophylaxis'])
        self.assertTrue(any('ეპიფასციური' in af for af in ev_ven['amber_flags']))

    def test_pad_bypass_vein_mapping_reflex_and_pad_pta_endovascular_pathway(self):
        from .models import PreopPatient
        from .preop import STATUS_PROGRESS, STATUS_READY, evaluate

        # 1. Case transfer auto-maps femoropopliteal bypass -> pad_bypass
        c_byp = self.make_case(
            patient_name='შუნტ მუხლქვეშაძე', age=67, sex='M', side='R',
            diagnosis='ქვემო კიდურის კრიტიკული იშემია, მოსვენებითი ტკივილი',
            proposal='ბარძაყ-მუხლქვეშა აუტოვენური შუნტირება',
        )
        self.client.get(reverse('preop-new'), {'case': c_byp.pk})
        byp = PreopPatient.objects.get(case=c_byp)
        self.assertEqual((byp.pathway, byp.op_subtype), ('pad_bypass', 'bypass_clti'))

        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_rest_pain': '1',
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        # Ipsilateral GSV is 2.3 mm (< 3.0 mm) -> automatically triggers contralateral/arm vein mapping!
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage1',
            'hb': '136', 'creat': '86', 'inr': '1.02', 'urine_wbc': '2',
            'abi_r': '0.34', 'abi_l': '0.88',
            'pad_runoff_status': 'tibial_single',
            'pad_gsv_status': 'inadequate', 'pad_gsv_mm': '2.3',
            'carotid_r': '25', 'carotid_l': '20', 'lvef': '58',
            'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        byp.refresh_from_db()
        ev_byp = evaluate(byp)
        self.assertTrue(ev_byp['s1_core_done'])
        self.assertTrue(ev_byp['needs_alt_vein'])
        self.assertEqual(ev_byp['status'], STATUS_PROGRESS)

        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage2',
            'alt_vein_mapped': 'contra_gsv_ok',
            'blood_reserved': '1',
            'decolonization_ready': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        byp.refresh_from_db()
        self.assertEqual(evaluate(byp)['status'], STATUS_READY)

        # 2. Case transfer auto-maps SFA angioplasty/stenting -> pad_pta
        c_pta = self.make_case(
            patient_name='სტენტ ბალონაძე', age=64, sex='M', side='L',
            diagnosis='ქვემო კიდურის არტერიების ათეროსკლეროზი, კოჭლობა',
            proposal='ზედაპირული ბარძაყის არტერიის ბალონური ანგიოპლასტიკა და სტენტირება (PTA)',
        )
        self.client.get(reverse('preop-new'), {'case': c_pta.pk})
        pta = PreopPatient.objects.get(case=c_pta)
        self.assertEqual((pta.pathway, pta.op_subtype), ('pad_pta', 'pta_claud'))

        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'abf_claud_m': '100', 'pta_cant_lie_flat': '1',
            'med_aspirin': '1', 'med_clopidogrel': '1', 'mets_ok': '1',
        })
        # Normal eGFR (creat 82) + pta_cant_lie_flat -> lean endovascular workup: no cardiology needed, sedation anesthesia triggered
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage1',
            'hb': '138', 'plt': '230', 'inr': '1.01', 'glucose': '5.6', 'creat': '82',
            'abi_r': '0.96', 'abi_l': '0.54',
            'pta_lesion_site': 'femoropopliteal',
            'pta_access_site': 'cfa_clean',
            'ecg': 'normal',
        })
        pta.refresh_from_db()
        ev_pta = evaluate(pta)
        self.assertTrue(ev_pta['s1_core_done'])
        self.assertFalse(ev_pta['needs_hydration'])
        self.assertTrue(ev_pta['needs_anesth'])
        self.assertFalse(ev_pta['needs_cardio'])

        # CKD3 (creat 128 -> eGFR 52 < 60) -> activates contrast hydration reflex + RCRI cardiology
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage1',
            'hb': '138', 'plt': '230', 'inr': '1.01', 'glucose': '5.6', 'creat': '128',
            'abi_r': '0.96', 'abi_l': '0.54',
            'pta_lesion_site': 'femoropopliteal',
            'pta_access_site': 'cfa_clean',
            'ecg': 'normal',
        })
        pta.refresh_from_db()
        self.assertTrue(evaluate(pta)['needs_hydration'])
        self.assertTrue(evaluate(pta)['needs_cardio'])

        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage2',
            'hydration_planned': '1',
            'dapt_ready': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        pta.refresh_from_db()
        self.assertEqual(evaluate(pta)['status'], STATUS_READY)


class PreopStageP0SafetyTests(PrivateMediaTest):
    """Stage P0 multi-agent international safety, pharmacology, UX, and 2D triage tests."""

    def test_p0_1_pharmacology_asra_neuraxial_crcl_cea_clopidogrel_glp1_hit_and_cied(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_READY, calc_caprini, evaluate

        today = timezone.localdate()

        # 1. Symptomatic CEA: Clopidogrel continues uninterrupted (ESVS 2023 Class I/IIa), needs_med_hold=False
        cea = PreopPatient.objects.create(
            patient_name='სიმპტომურ კაროტიდაძე', age=70, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'cea_symp_event': '1',
            'cea_event_chips': ['minor_stroke'],
            'cea_event_date': (today - timedelta(days=5)).isoformat(),
            'med_clopidogrel': '1',
            'med_chips': ['statin'],
            'mets_ok': '1',
        })
        cea.refresh_from_db()
        ev_cea = evaluate(cea, today=today)
        sched_cea = {m['drug']: m for m in ev_cea['med_schedule']}
        self.assertEqual(sched_cea['კლოპიდოგრელი / P2Y12']['tone'], 'ok')
        self.assertIn('გრძელდება უწყვეტად', sched_cea['კლოპიდოგრელი / P2Y12']['rule'])
        self.assertFalse(ev_cea['needs_med_hold'])
        # Acute stroke <=30d adds +5 Caprini points
        self.assertGreaterEqual(ev_cea['caprini_score'], 9)

        # Missing antiplatelet on CEA raises Red Flag until cea_antiplatelet_started is checked in Stage 2
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'cea_symp_event': '1',
            'cea_event_date': (today - timedelta(days=4)).isoformat(),
            'med_chips': ['statin'],
            'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1',
            'hb': '142', 'creat': '84', 'inr': '1.0', 'urine_wbc': '1',
            'carotid_r': '85', 'carotid_l': '30',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'lvef': '58', 'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        cea.refresh_from_db()
        ev_no_ap = evaluate(cea, today=today)
        self.assertEqual(ev_no_ap['status'], STATUS_ALERT)
        self.assertTrue(ev_no_ap['needs_cea_antiplatelet'])
        self.assertTrue(any('არ იღებს ანტიაგრეგანტს' in rf for rf in ev_no_ap['red_flags']))

        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage2',
            'cea_antiplatelet_started': '1',
            'neuro_consult_done': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        cea.refresh_from_db()
        self.assertEqual(evaluate(cea, today=today)['status'], STATUS_READY)

        # 2. ASRA 5th Ed Neuraxial vs General Hold + Unknown-Creatinine Guard + Dabigatran + Prasugrel + GLP-1 + CIED + HIT
        aaa = PreopPatient.objects.create(
            patient_name='ფარმაკო უსაფრთხოძე', age=74, sex='M',
            pathway='aaa_infra', op_subtype='evar', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'questionnaire',
            'med_doac': '1',
            'med_dabigatran': '1',
            'med_prasugrel': '1',
            'med_clopidogrel': '1',
            'glp1': '1',
            'pacemaker': '1',
            'allergy_other': '1',
            'allergy_chips': ['hit_heparin'],
            'mets_ok': '1',
        })
        aaa.refresh_from_db()
        # Before creatinine is entered -> DOAC & Dabigatran MUST show red guard, NEVER default to 48h!
        ev_no_cr = evaluate(aaa, today=today)
        sched_no_cr = {m['drug']: m for m in ev_no_cr['med_schedule']}
        self.assertEqual(sched_no_cr['DOAC (ქსარელტო / ელიქვისი)']['tone'], 'red')
        self.assertIn('შეიყვანეთ კრეატინინი', sched_no_cr['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertNotIn('48 საათით', sched_no_cr['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        # Clopidogrel monotherapy hold warns to substitute Aspirin 75-100 mg
        self.assertIn('ასპირინი 75–100 მგ', sched_no_cr['კლოპიდოგრელი / P2Y12']['rule'])
        self.assertIn('7–10 დღით', sched_no_cr['პრასუგრელი (ეფიენტი)']['rule'])
        self.assertIn('24 სთ სუფთა თხევადი დიეტა', sched_no_cr['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['rule'])
        self.assertTrue(ev_no_cr['needs_cied'])
        self.assertTrue(ev_no_cr['needs_anesth'])
        # HIT guard: Caprini advice NEVER recommends LMWH
        self.assertIn('ფონდაპარინუქსი', ev_no_cr['caprini_advice'])
        self.assertIn('აკრძალულია', ev_no_cr['caprini_advice'])

        # Enter weight=82, creat=85 (CrCl >= 50): General surgery -> DOAC 48h; Neuraxial -> DOAC 72h (ASRA 5th Ed)!
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage1',
            'weight_kg': '82', 'height_cm': '176',
            'hb': '138', 'creat': '85', 'inr': '1.05', 'urine_wbc': '1',
            'carotid_r': '20', 'carotid_l': '20', 'lvef': '55', 'aaa_mm': '58',
            'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage2', 'anesthesia_plan': 'general',
        })
        aaa.refresh_from_db()
        sched_gen = {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}
        self.assertIn('48 საათით', sched_gen['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage2', 'anesthesia_plan': 'neuraxial',
        })
        aaa.refresh_from_db()
        sched_neur = {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}
        self.assertIn('72 საათით', sched_neur['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        # Lower CrCl to 30-49 (weight=62, creat=135 -> CrCl ~36): Dabigatran 96h general vs 120h neuraxial!
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage1',
            'weight_kg': '62', 'height_cm': '170',
            'hb': '138', 'creat': '135', 'inr': '1.05', 'urine_wbc': '1',
            'carotid_r': '20', 'carotid_l': '20', 'lvef': '55', 'aaa_mm': '58',
            'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        aaa.refresh_from_db()
        sched_dab_neur = {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}
        self.assertIn('120 საათით', sched_dab_neur['დაბიგატრანი (პრადაქსა)']['rule'])
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage2', 'anesthesia_plan': 'general',
        })
        aaa.refresh_from_db()
        sched_dab_gen = {m['drug']: m for m in evaluate(aaa, today=today)['med_schedule']}
        self.assertIn('96 საათით', sched_dab_gen['დაბიგატრანი (პრადაქსა)']['rule'])

    def test_p0_2_self_fill_data_wipe_protection_post_verification_lock_and_passport(self):
        from django.utils import timezone
        from .models import PreopPatient, StaffProfile
        from .preop import make_self_fill_token, merged_q, merged_studies

        StaffProfile.objects.update_or_create(
            user=self.user, defaults={'display_name': 'გიორგი აფხაძე', 'full_name': 'გიორგი აფხაძე'}
        )
        today = timezone.localdate()

        p = PreopPatient.objects.create(
            patient_name='თვითშევსება დაცვაძე', age=69, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={'dm_insulin': True, 'loose_teeth': True, 'pacemaker': True},
            studies={'cta_disc': True, 'aaa_mm': 62, 'ecg': 'afib_controlled', 'cxr': 'normal'},
        )
        token = make_self_fill_token(p)
        url = reverse('preop-self-fill', args=[token])

        # Public unauthenticated client submits Self-Fill form
        from django.test import Client
        anon = Client()
        resp_get = anon.get(url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, 'კითხვები საძილე არტერიის შესახებ')
        self.assertContains(resp_get, 'ქსარელტო')

        resp_post = anon.post(url, {
            'age': '69', 'sex': 'M', 'phone': '599112233',
            'cea_symp_event': '1',
            'cea_event_chips': ['amaurosis'],
            'cea_event_date': (today - timezone.timedelta(days=3)).isoformat(),
            'htn': '1',
            'med_aspirin': '1',
            'allergy_iodine': '1',
            'mets_ok': '1',
        })
        self.assertEqual(resp_post.status_code, 302)
        p.refresh_from_db()
        q = merged_q(p)
        s = merged_studies(p)
        # Nurse-only fields MUST remain untouched (zero data wipe!)
        self.assertTrue(s['cta_disc'])
        self.assertEqual(s['aaa_mm'], 62)
        self.assertTrue(q['dm_insulin'])
        self.assertTrue(q['loose_teeth'])
        self.assertTrue(q['pacemaker'])
        # Patient-submitted fields captured cleanly
        self.assertTrue(q['cea_symp_event'])
        self.assertTrue(q['allergy_iodine'])
        self.assertEqual(q['filled_by'], 'პაციენტი (ბმულით)')
        self.assertFalse(q['nurse_verified'])

        # Nurse verifies Tab 1 -> locks Self-Fill link against further public overwrites!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'htn': '1', 'dm_insulin': '1', 'pacemaker': '1',
            'med_aspirin': '1', 'allergy_iodine': '1', 'cta_disc': '1', 'mets_ok': '1',
        })
        p.refresh_from_db()
        self.assertTrue(merged_q(p)['nurse_verified'])

        locked_get = anon.get(url)
        self.assertContains(locked_get, 'კითხვარი უკვე დადასტურებულია სამედიცინო გუნდის მიერ')
        anon.post(url, {'htn': '0', 'med_aspirin': '0', 'phone': '000000'})
        p.refresh_from_db()
        self.assertEqual(p.phone, '599112233')
        self.assertTrue(merged_q(p)['med_aspirin'])

        # Passport renders Georgian ECG/CXR labels (never raw 'afib_controlled'/'normal') and nurse verifier name
        pass_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertContains(pass_resp, 'ნორმოსისტოლური მოციმციმე არითმია')
        self.assertContains(pass_resp, 'ნორმა (კეროვან-ინფილტრაციული ცვლილების გარეშე)')
        self.assertContains(pass_resp, 'გიორგი აფხაძე')

    def test_p0_3_2d_triage_state_machine_specialist_signoff_hold_and_alert_ack_stability(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_READY, evaluate

        today = timezone.localdate()
        p = PreopPatient.objects.create(
            patient_name='გიგანტ ანევრიზმაძე', age=72, sex='M',
            pathway='aaa_infra', op_subtype='open',
            visit_date=today - timedelta(days=9),
            started_on=today - timedelta(days=9),
        )
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'aaa_pain': '0', 'htn': '1', 'cad': '1', 'pulm': '1',
            'med_aspirin': '1', 'med_chips': ['statin', 'beta_blocker'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'bp_sys': '134', 'bp_dia': '78', 'hr': '78', 'irregular_hr': '1', 'spo2': '96',
            'hb': '136', 'creat': '88', 'inr': '1.04', 'urine_wbc': '2',
            'carotid_r': '25', 'carotid_l': '20', 'lvef': '36', 'aaa_mm': '72',
            'cxr': 'emphysema', 'ecg': 'afib_controlled', 'cta_disc': '1',
        })
        p.refresh_from_db()
        ev1 = evaluate(p, today=today)
        self.assertEqual(ev1['status'], STATUS_ALERT)
        self.assertEqual(ev1['priority_tier'], 'urgent')
        self.assertFalse(ev1['workup_ready'])

        # Alert acknowledgement signature stays stable across day 9 -> day 10!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'signoff', 'action': 'ack_alerts',
        })
        p.refresh_from_db()
        self.assertTrue(evaluate(p, today=today)['alerts_acked'])
        self.assertTrue(evaluate(p, today=today + timedelta(days=1))['alerts_acked'])

        # Putting patient on SomedayList.HOLD pauses over_target_days
        p.someday_list = PreopPatient.SomedayList.HOLD
        p.visit_date = None
        p.save()
        self.assertFalse(evaluate(p, today=today)['over_target_days'])
        p.someday_list = ''
        p.visit_date = today - timedelta(days=9)
        p.save()

        # Specialist sign-off (cardio_decision='cleared' + anesth_done='1') + completing remaining tasks ->
        # 72 mm urgent AAA reaches STATUS_READY with priority_tier == 'urgent'!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'spiro_fev1': '46',
            'blood_reserved': '1',
            'decolonization_ready': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        p.refresh_from_db()
        ev_ready = evaluate(p, today=today)
        self.assertTrue(ev_ready['workup_ready'])
        self.assertEqual(ev_ready['red_flags'], [])
        self.assertEqual(ev_ready['status'], STATUS_READY)
        self.assertEqual(ev_ready['priority_tier'], 'urgent')
        self.assertTrue(any('[სასწრაფო პრიორიტეტი]' in af for af in ev_ready['amber_flags']))

    def test_p0_4_unit_guards_live_dates_non_compressible_abi_tbi_and_case_auto_import(self):
        from datetime import timedelta
        from django.core.files.base import ContentFile
        from django.utils import timezone
        from .models import DopplerStudy, PreopPatient
        from .preop import (
            STATUS_ALERT, STATUS_PROGRESS, STATUS_READY,
            calc_egfr, evaluate, merged_q, merged_studies,
            normalize_glucose_mmol, normalize_hb_gl,
        )

        today = timezone.localdate()

        # 1. Unit guards: Hb 29 stays 29 g/L (severe anemia), Hb 13.4 -> 134 g/L; Glucose 38 stays 38 mmol/L, 180 -> 10 mmol/L
        self.assertEqual(normalize_hb_gl(29), 29.0)
        self.assertEqual(normalize_hb_gl(13.4), 134.0)
        self.assertEqual(normalize_glucose_mmol(38), 38.0)
        self.assertEqual(normalize_glucose_mmol(180), 10.0)
        # Missing age never substitutes age 68
        self.assertIsNone(calc_egfr(90, None, 'M'))

        # Ambiguous creatinine (22 without unit) blocks eGFR & Stage 1 until creat_unit is chosen; PLT/WBC auto-scale
        p_u = PreopPatient.objects.create(
            patient_name='ერთეულ საზღვრაძე', age=65, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_claud', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[p_u.pk]), {
            'section': 'questionnaire', 'side': 'R', 'med_aspirin': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p_u.pk]), {
            'section': 'stage1',
            'hb': '29', 'wbc': '7800', 'plt': '245000', 'inr': '1.0', 'glucose': '38', 'creat': '22',
            'abi_r': '0.62', 'abi_l': '0.95', 'pta_access_site': 'cfa_clean', 'ecg': 'normal',
        })
        p_u.refresh_from_db()
        s_u = merged_studies(p_u)
        self.assertEqual(s_u['wbc'], 7.8)
        self.assertEqual(s_u['plt'], 245)
        ev_u = evaluate(p_u, today=today)
        self.assertTrue(ev_u['creat_unit_ambiguous'])
        self.assertIsNone(ev_u['egfr'])
        self.assertFalse(ev_u['s1_core_done'])
        self.assertTrue(any('Hb 29 გ/ლ < 100' in rf for rf in ev_u['red_flags']))
        self.assertTrue(any('კრიტიკული გლიკემია (გლუკოზა 38 მმოლ/ლ)' in rf for rf in ev_u['red_flags']))

        # Clarifying creat_unit='mgdl' resolves ambiguity and computes severe renal failure eGFR (<15)
        self.client.post(reverse('preop-patient', args=[p_u.pk]), {
            'section': 'stage1',
            'hb': '132', 'wbc': '7.8', 'plt': '245', 'inr': '1.0', 'glucose': '5.4',
            'creat': '22', 'creat_unit': 'mgdl',
            'abi_r': '0.62', 'abi_l': '0.95', 'pta_access_site': 'cfa_clean', 'ecg': 'normal',
        })
        p_u.refresh_from_db()
        ev_mgdl = evaluate(p_u, today=today)
        self.assertFalse(ev_mgdl['creat_unit_ambiguous'])
        self.assertLess(ev_mgdl['egfr'], 10)

        # 2. Live calendar dates: cea_event_date ticks past 14-day ESVS window dynamically!
        cea_live = PreopPatient.objects.create(
            patient_name='კალენდარ ცოცხლაძე', age=67, sex='F', side='L',
            pathway='cea', op_subtype='cea_symp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea_live.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'cea_symp_event': '1',
            'cea_event_date': (today - timedelta(days=5)).isoformat(),
            'med_aspirin': '1', 'med_clopidogrel': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        cea_live.refresh_from_db()
        self.assertEqual(evaluate(cea_live, today=today)['days_since_ev'], 5)
        ev_future = evaluate(cea_live, today=today + timedelta(days=11))
        self.assertEqual(ev_future['days_since_ev'], 16)
        self.assertTrue(any('14-დღიანი ოპტიმალური ფანჯარა' in rf for rf in ev_future['red_flags']))

        # 3. Non-compressible ABI > 1.40 on VENOUS blocks compression_ready until Toe Pressure >= 60 or TBI >= 0.70
        ven = PreopPatient.objects.create(
            patient_name='მონკებერგ ვენაძე', age=68, sex='F', side='R',
            pathway='venous', op_subtype='evla', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'ven_ceap': 'C5', 'dm': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage1',
            'hb': '130', 'plt': '230', 'inr': '1.0', 'glucose': '6.1',
            'ven_duplex_status': 'deep_patent_reflux', 'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage2',
            'abi_r': '1.52', 'abi_l': '1.10',
            'compression_ready': '1',
            'vte_prophylaxis_planned': '1',
        })
        ven.refresh_from_db()
        ev_ven_nc = evaluate(ven, today=today)
        self.assertTrue(ev_ven_nc['abi_non_compressible'])
        self.assertTrue(ev_ven_nc['needs_tbi_check'])
        self.assertEqual(ev_ven_nc['status'], STATUS_PROGRESS)

        self.client.post(reverse('preop-patient', args=[ven.pk]), {
            'section': 'stage2',
            'abi_r': '1.52', 'abi_l': '1.10',
            'tp_r': '68', 'tp_l': '74',
            'compression_ready': '1',
            'vte_prophylaxis_planned': '1',
        })
        ven.refresh_from_db()
        self.assertEqual(evaluate(ven, today=today)['status'], STATUS_READY)

        # 4. Conference Case -> Preop 1-click Auto-Import of Doppler (ABI/TBI/TP) + WIfI + GLASS + GSV status
        c_imp = self.make_case(
            patient_name='იმპორტ კონფერენციაძე', age=69, sex='M', side='R',
            diagnosis='ქვემო კიდურის ათეროსკლეროზი',
            proposal='ბარძაყ-მუხლქვეშა შუნტირება',
        )
        c_imp.staging = {
            'R': {
                'w': 2, 'i': 2, 'fi': 2,
                'vein': 'no',
                'cfa': 'B',
                'segments': {
                    'sfa1': {'stenosis': 'occlusion', 'calc': False},
                    'pop1': {'stenosis': 'severe', 'calc': False},
                    'at1': {'stenosis': 'occlusion', 'calc': False},
                    'pt1': {'stenosis': 'occlusion', 'calc': False},
                    'per1': {'stenosis': 'mild', 'calc': False},
                },
            },
        }
        c_imp.save()
        ds = DopplerStudy(
            case=c_imp,
            performed_on=today,
            data={
                'pressures': {'R': {'brachial': 140, 'pt': 220, 'dp': 215, 'toe': 26}, 'L': {}},
                'indices': {'R': {'abi_pt': 1.55, 'abi_dp': 1.48, 'tbi': 0.28}, 'L': {'abi_pt': 0.96}},
                'sites': [],
            },
        )
        ds.file.save('dop.pdf', ContentFile(b'%PDF-1.4 test'), save=True)

        self.client.get(reverse('preop-new'), {'case': c_imp.pk})
        p_imp = PreopPatient.objects.get(case=c_imp)
        # Auto-upgraded from claudication to CLTI because WIfI Stage >= 2 & w=2/fi=2!
        self.assertEqual(p_imp.op_subtype, 'bypass_clti')
        q_imp = merged_q(p_imp)
        s_imp = merged_studies(p_imp)
        self.assertTrue(q_imp['abf_ulcer'])
        self.assertTrue(q_imp['abf_wet_infection'])
        self.assertEqual(s_imp['abi_r'], 1.55)
        self.assertEqual(s_imp['tbi_r'], 0.28)
        self.assertEqual(s_imp['tp_r'], 26)
        self.assertEqual(s_imp['pad_gsv_status'], 'inadequate')
        self.assertEqual(s_imp['pad_runoff_status'], 'tibial_single')
        self.assertEqual(s_imp['pta_access_site'], 'cfa_calcified_scar')
        self.assertIn('WIfI Stage', s_imp['case_summary_badge'])



class PreopStageP1RationalAndBridgeTests(PrivateMediaTest):
    """Stage P1 rational testing (NICE NG45 / ESC 2022 / ESVS 2023-2024 / IDSA 2019 / ESAIC 2024),
    Claudication Class III appropriateness + VOYAGER PAD, AAA/CEA anatomy + PBM, and 1-click Preop->Board bridge."""

    def test_p1_1_rational_testing_fast_track_cea_bnp_ppi_asb_spiro_evla_and_claudication_mets_audit(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_READY, evaluate

        today = timezone.localdate()

        # 1. Fast-Track Symptomatic CEA ("Time is Brain"): reaches READY without routine CXR, TTE, Urine, or Cardiology!
        cea = PreopPatient.objects.create(
            patient_name='სწრაფ კაროტიდაძე', age=67, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R',
            'cea_symp_event': '1',
            'cea_event_chips': ['tia'],
            'cea_event_date': (today - timedelta(days=3)).isoformat(),
            'med_aspirin': '1',
            'med_chips': ['statin'],
            'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1',
            'bp_sys': '134', 'bp_dia': '78', 'hr': '68', 'spo2': '98',
            'hb': '142', 'creat': '82', 'inr': '1.01',
            'carotid_r': '84', 'carotid_l': '25',
            'cea_cta_status': 'concordant',
            'brain_imaging': 'normal_small',
            'ecg': 'normal',
            'cta_disc': '1',
        })
        cea.refresh_from_db()
        ev_cea = evaluate(cea, today=today)
        self.assertFalse(ev_cea['needs_cxr'])
        self.assertFalse(ev_cea['needs_echo_or_bnp'])
        self.assertFalse(ev_cea['needs_cardio'])
        self.assertTrue(ev_cea['s1_core_done'])

        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage2',
            'neuro_consult_done': '1',
            'anesth_done': '1',
        })
        cea.refresh_from_db()
        self.assertEqual(evaluate(cea, today=today)['status'], STATUS_READY)

        # 2. Open AAA with NT-proBNP < 125 pg/mL (satisfies cardiac screen without LVEF!), remote ulcer (PPI, no EGD),
        #    asymptomatic smoker (no spirometry), and isolated asymptomatic urine_wbc=18 (IDSA 2019 ASB -> no UTI Red Flag!)
        aaa = PreopPatient.objects.create(
            patient_name='რაციონალ აორტაძე', age=68, sex='M',
            pathway='aaa_infra', op_subtype='open', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1',
            'ulcer': '1',           # remote history, ulcer_active_symp NOT checked
            'smoker': '1',          # asymptomatic smoker, pulm NOT checked
            'med_aspirin': '1',
            'med_chips': ['statin'],
            'aaa_mm': '60',
            'cta_months': '1',
            'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage1',
            'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '130', 'bp_dia': '80', 'spo2': '97',
            'hb': '138', 'creat': '86', 'inr': '1.02',
            'nt_probnp': '84',      # < 125 pg/mL satisfies ESC 2022 Class I screen without TTE!
            'hs_ctn': '9',
            'urine_wbc': '18',      # < 25, nitrite-negative, asymptomatic -> ASB amber note only!
            'ecg': 'normal',
        })
        aaa.refresh_from_db()
        ev_aaa = evaluate(aaa, today=today)
        self.assertFalse(ev_aaa['needs_carotid_duplex'])
        self.assertFalse(ev_aaa['needs_cxr'])
        self.assertTrue(ev_aaa['needs_echo_or_bnp'])
        self.assertTrue(ev_aaa['s1_core_done'])
        self.assertTrue(ev_aaa['needs_ppi'])
        self.assertFalse(ev_aaa['needs_gastro'])
        self.assertFalse(ev_aaa['needs_spirometry'])
        self.assertFalse(ev_aaa['uti_suspected'])
        sched_drugs = {m['drug'] for m in ev_aaa['med_schedule']}
        self.assertIn('ომეპრაზოლი / პანტოპრაზოლი (PPI)', sched_drugs)

        # Elevated NT-proBNP 125-299 -> amber flag; >=300 -> Red Flag
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage1',
            'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'hb': '138', 'creat': '86', 'inr': '1.02', 'urine_wbc': '2',
            'nt_probnp': '210', 'ecg': 'normal',
        })
        aaa.refresh_from_db()
        self.assertTrue(any('NT-proBNP (210' in af for af in evaluate(aaa, today=today)['amber_flags']))
        self.client.post(reverse('preop-patient', args=[aaa.pk]), {
            'section': 'stage1',
            'aaa_mm': '60', 'cta_months': '1', 'cta_disc': '1',
            'hb': '138', 'creat': '86', 'inr': '1.02', 'urine_wbc': '2',
            'nt_probnp': '410', 'ecg': 'normal',
        })
        aaa.refresh_from_db()
        self.assertTrue(any('NT-proBNP (410' in rf for rf in evaluate(aaa, today=today)['red_flags']))

        # 3. Claudication METs Cardiology Audit Loophole Closed (mets_ok='0' + mets_limitation='limb' -> ESC 2022 Class III!)
        byp = PreopPatient.objects.create(
            patient_name='კოჭლობა აუდიტაძე', age=65, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_rest_pain': '1',
            'mets_ok': '0',
            'mets_limitation': 'limb',
            'med_aspirin': '1', 'med_chips': ['statin'],
        })
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage1',
            'hb': '136', 'creat': '80', 'inr': '1.0',
            'abi_r': '0.35', 'abi_l': '0.90',
            'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate',
            'ecg': 'normal', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage2',
            'cardiologist': 'გარე კარდიოლოგი',
            'cardio_decision': 'cag',
        })
        byp.refresh_from_db()
        ev_byp = evaluate(byp, today=today)
        self.assertFalse(ev_byp['guideline_justifies_extra_cardio'])
        self.assertTrue(ev_byp['cardio_non_compliant'])
        self.assertEqual(ev_byp['status'], STATUS_ALERT)
        self.assertTrue(any('ESC 2022 Class III' in rf for rf in ev_byp['red_flags']))

        # If limitation is cardiac dyspnea ('cardiac'), stress/CAG IS guideline-justified
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_rest_pain': '1',
            'mets_ok': '0',
            'mets_limitation': 'cardiac',
            'med_aspirin': '1', 'med_chips': ['statin'],
        })
        byp.refresh_from_db()
        ev_byp_card = evaluate(byp, today=today)
        self.assertTrue(ev_byp_card['guideline_justifies_extra_cardio'])
        self.assertFalse(ev_byp_card['cardio_non_compliant'])

        # 4. Lean ASA I EVLA (age 34, C2): reaches s1_core_done without INR, glucose, creatinine, or ECG!
        evla = PreopPatient.objects.create(
            patient_name='მსუბუქ ლაზერაძე', age=34, sex='F', side='L',
            pathway='venous', op_subtype='evla', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[evla.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'ven_ceap': 'C2', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[evla.pk]), {
            'section': 'stage1',
            'hb': '130', 'plt': '240',
            'ven_duplex_status': 'deep_patent_reflux',
        })
        evla.refresh_from_db()
        self.assertTrue(evaluate(evla, today=today)['s1_core_done'])

    def test_p1_2_claudication_class3_appropriateness_oac_pta_guard_and_voyager_pad(self):
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import evaluate, generate_anamnesis

        today = timezone.localdate()

        # 1. BTK / Infrapopliteal PTA for claudication -> ESVS 2024 / ACC-AHA 2024 Class III Red Flag + <3m BMT amber warning
        pta = PreopPatient.objects.create(
            patient_name='წვივ კოჭლობაძე', age=63, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_claud', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_claud_m': '150', 'abf_duration': '< 2 კვირა',
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[pta.pk]), {
            'section': 'stage1',
            'hb': '140', 'plt': '230', 'inr': '1.0', 'glucose': '5.4', 'creat': '84',
            'abi_r': '0.62', 'abi_l': '0.98',
            'pta_lesion_site': 'infrapopliteal',
            'pta_access_site': 'cfa_clean',
            'ecg': 'normal',
        })
        pta.refresh_from_db()
        ev_btk = evaluate(pta, today=today)
        self.assertTrue(any('ESVS 2024 / ACC-AHA 2024 Class III' in rf and 'BTK' in rf for rf in ev_btk['red_flags']))
        self.assertTrue(any('≥3 თვიანი' in af for af in ev_btk['amber_flags']))

        # 2. Prosthetic infrainguinal bypass for claudication -> ESVS 2024 Class III Red Flag
        byp_ic = PreopPatient.objects.create(
            patient_name='პროთეზ კოჭლობაძე', age=64, sex='M', side='L',
            pathway='pad_bypass', op_subtype='bypass_claud', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[byp_ic.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'abf_claud_m': '80', 'ic_bmt_3m': '1',
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp_ic.pk]), {
            'section': 'stage1',
            'hb': '138', 'creat': '82', 'inr': '1.0',
            'abi_r': '0.95', 'abi_l': '0.52',
            'pad_runoff_status': 'popliteal_patent',
            'pad_gsv_status': 'inadequate',
            'ecg': 'normal', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp_ic.pk]), {
            'section': 'stage2',
            'alt_vein_mapped': 'no_autovein_prosthetic',
            'post_antithrombotic_plan': 'voyager',
        })
        byp_ic.refresh_from_db()
        ev_pros = evaluate(byp_ic, today=today)
        self.assertTrue(any('სინთეზური ინფრაინგვინალური შუნტირება უკუნაჩვენებია' in rf for rf in ev_pros['red_flags']))
        self.assertIn('VOYAGER PAD', ev_pros['post_antithrombotic_label'])
        self.assertIn('VOYAGER PAD', generate_anamnesis(byp_ic, ev_pros))
        self.assertContains(self.client.get(reverse('preop-passport', args=[byp_ic.pk])), 'VOYAGER PAD')

        # 3. OAC + PTA Guard: prevents triple antithrombotic therapy (OAC + ASA + Clopidogrel)
        pta_oac = PreopPatient.objects.create(
            patient_name='სამმაგ სისხლდენაძე', age=72, sex='M', side='R',
            pathway='pad_pta', op_subtype='pta_clti', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[pta_oac.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'abf_rest_pain': '1',
            'med_doac': '1', 'med_aspirin': '1', 'med_clopidogrel': '1',
            'mets_ok': '1',
        })
        pta_oac.refresh_from_db()
        ev_oac = evaluate(pta_oac, today=today)
        self.assertTrue(ev_oac['has_full_oac'])
        self.assertTrue(any('სამმაგი ანტითრომბოზული თერაპია' in rf for rf in ev_oac['red_flags']))
        dapt_tasks = [t['label'] for t in ev_oac['tasks'] if t['key'] == 'dapt']
        self.assertEqual(len(dapt_tasks), 1)
        self.assertIn('სამმაგი თერაპიის გარეშე', dapt_tasks[0])

    def test_p1_3_aaa_cea_anatomical_depth_pbm_iv_iron_and_contrast_budget(self):
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import evaluate

        today = timezone.localdate()

        # 1. EVAR with Hostile Neck + Popliteal Aneurysm + CKD4 Contrast Budget (<=30 mL + CO2/IVUS)
        evar = PreopPatient.objects.create(
            patient_name='ანატომია ევარაძე', age=73, sex='M',
            pathway='aaa_infra', op_subtype='evar', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[evar.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'med_aspirin': '1', 'med_chips': ['statin'],
            'aaa_mm': '48', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[evar.pk]), {
            'section': 'stage1',
            'aaa_mm': '48',
            'aaa_morphology': 'saccular',
            'aaa_neck_status': 'hostile_neck',
            'popliteal_screen_status': 'aneurysm_found',
            'hb': '134', 'creat': '240', 'creat_unit': 'umol', 'inr': '1.0', 'urine_wbc': '1',
            'ecg': 'normal', 'cta_disc': '1',
        })
        evar.refresh_from_db()
        ev_evar = evaluate(evar, today=today)
        joined_red = ' | '.join(ev_evar['red_flags'])
        joined_amb = ' | '.join(ev_evar['amber_flags'])
        self.assertIn('Hostile Neck', joined_red)
        self.assertIn('მუხლქვეშა/ბარძაყის ანევრიზმა', joined_red)
        # Saccular 48 mm AAA suppresses the standard "<55 mm conservative" warning!
        self.assertNotIn('< 55 მმ', joined_amb)
        self.assertIn('საკულარული მორფოლოგია', joined_amb)
        self.assertIsNotNone(ev_evar['max_contrast_ml'])
        self.assertLess(ev_evar['max_contrast_ml'], 30)
        self.assertTrue(ev_evar['co2_angio_advised'])

        # 2. CEA High Surgical Risk (prior neck radiation) -> CAS/TCAR consideration advisory
        cea_rad = PreopPatient.objects.create(
            patient_name='დასხივება კაროტიდაძე', age=69, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea_rad.pk]), {
            'section': 'questionnaire',
            'side': 'L', 'cea_prior_neck': '1', 'cea_neck_chips': ['neck_rad'],
            'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        cea_rad.refresh_from_db()
        ev_rad = evaluate(cea_rad, today=today)
        self.assertTrue(ev_rad['cea_high_surg_risk'])
        self.assertTrue(any('CAS / TCAR' in af for af in ev_rad['amber_flags']))

        # 3. Elective PBM Iron-Deficiency Anemia (Hb 108 + ferritin 18 + TSAT 14%) + Cell Saver + TXA
        aaa_pbm = PreopPatient.objects.create(
            patient_name='რკინა სეივერაძე', age=68, sex='M',
            pathway='aaa_infra', op_subtype='open', visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[aaa_pbm.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'med_aspirin': '1', 'med_chips': ['statin'],
            'aaa_mm': '59', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[aaa_pbm.pk]), {
            'section': 'stage1',
            'aaa_mm': '59', 'cta_months': '1', 'cta_disc': '1',
            'hb': '108', 'ferritin': '18', 'tsat': '14',
            'creat': '84', 'inr': '1.0', 'urine_wbc': '1',
            'nt_probnp': '70', 'ecg': 'normal',
        })
        aaa_pbm.refresh_from_db()
        ev_pbm = evaluate(aaa_pbm, today=today)
        self.assertTrue(ev_pbm['pbm_iv_iron_indicated'])
        self.assertTrue(any(t['key'] == 'pbm_iron' and not t['done'] for t in ev_pbm['tasks']))

        self.client.post(reverse('preop-patient', args=[aaa_pbm.pk]), {
            'section': 'stage2',
            'anemia_panel_done': '1',
            'pbm_iv_iron_done': '1',
            'cell_saver_ready': '1',
            'blood_reserved': '1',
            'decolonization_ready': '1',
            'cardiologist': 'ლიკა ინჯია', 'cardio_decision': 'cleared',
            'anesth_done': '1',
        })
        aaa_pbm.refresh_from_db()
        self.assertTrue(any(t['key'] == 'pbm_iron' and t['done'] for t in evaluate(aaa_pbm, today=today)['tasks']))
        self.assertContains(self.client.get(reverse('preop-passport', args=[aaa_pbm.pk])), 'Cell Saver')

    def test_p1_4_seamless_preop_to_board_one_click_handoff_bridge(self):
        from django.utils import timezone
        from .models import BoardEntry, BoardPatient, PreopPatient
        from .preop import merged_studies

        today = timezone.localdate()

        # 1. Open AAA with HIT, severe beta-lactam allergy, pacemaker, ulcer, Cell Saver -> defaults to ICU + rich safety tasks
        p = PreopPatient.objects.create(
            patient_name='ჰენდოფ დაფაძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1',
            'ulcer': '1',
            'pacemaker': '1',
            'allergy_other': '1',
            'allergy_chips': ['hit_heparin', 'beta_lactam'],
            'med_aspirin': '1',
            'aaa_mm': '62', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'aaa_mm': '62', 'cta_months': '1', 'cta_disc': '1',
            'bp_sys': '132', 'bp_dia': '78',
            'hb': '135', 'plt': '195', 'inr': '1.04', 'creat': '138',
            'nt_probnp': '92', 'hs_ctn': '11',
            'blood_group': 'O (I) Rh (+)', 'ecg': 'normal',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2',
            'cell_saver_ready': '1',
            'blood_reserved': '1',
        })

        resp = self.client.post(reverse('preop-admit-board', args=[p.pk]), {'bed': '3'})
        bp = BoardPatient.objects.get(name='ჰენდოფ დაფაძე')
        self.assertRedirects(resp, reverse('board-patient', args=[bp.pk]))
        self.assertEqual(bp.unit, BoardPatient.ICU)
        self.assertEqual(bp.bed, '3')

        p.refresh_from_db()
        self.assertEqual(merged_studies(p)['board_patient_id'], bp.pk)

        status_entries = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS))
        self.assertEqual(len(status_entries), 1)
        self.assertIn('[წინასაოპერაციო ჰენდოფი]', status_entries[0].text)
        self.assertIn('Hb 135', status_entries[0].text)
        self.assertIn('HIT ანამნეზში', status_entries[0].text)
        self.assertIn('Cell Saver + TXA', status_entries[0].text)

        tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        joined_tasks = ' | '.join(tasks)
        self.assertIn('ფონდაპარინუქსი / არგატრობანი', joined_tasks)
        self.assertNotIn('LMWH პროფილაქტიკური დოზით', joined_tasks)
        self.assertIn('ვანკომიცინით ან კლინდამიცინით', joined_tasks)
        self.assertIn('ომეპრაზოლი / პანტოპრაზოლი (PPI)', joined_tasks)
        self.assertIn('CIED', joined_tasks)
        self.assertIn('ნეფროპროტექცია', joined_tasks)

        # Idempotent repeat POST: does NOT duplicate BoardPatient, STATUS, or TASK entries!
        task_count_before = BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).count()
        self.client.post(reverse('preop-admit-board', args=[p.pk]), {'bed': '3ა'})
        self.assertEqual(BoardPatient.objects.filter(name='ჰენდოფ დაფაძე').count(), 1)
        self.assertEqual(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS).count(), 1)
        self.assertEqual(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).count(), task_count_before)
        bp.refresh_from_db()
        self.assertEqual(bp.bed, '3ა')

        # Preop workspace header now renders direct link to the active BoardPatient
        preop_page = self.client.get(reverse('preop-patient', args=[p.pk]))
        self.assertContains(preop_page, 'დაფაზეა')
        self.assertContains(preop_page, reverse('board-patient', args=[bp.pk]))

        # 2. PAD_BYPASS with VOYAGER PAD defaults to WARD and transfers VOYAGER PAD duty task
        byp = PreopPatient.objects.create(
            patient_name='ვოიაჯერ პალატაძე', age=66, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'questionnaire', 'side': 'R', 'abf_rest_pain': '1', 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[byp.pk]), {
            'section': 'stage2', 'post_antithrombotic_plan': 'voyager',
        })
        self.client.post(reverse('preop-admit-board', args=[byp.pk]), {'bed': '12'})
        bp_ward = BoardPatient.objects.get(name='ვოიაჯერ პალატაძე')
        self.assertEqual(bp_ward.unit, BoardPatient.WARD)
        ward_tasks = ' | '.join(BoardEntry.objects.filter(patient=bp_ward, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertIn('VOYAGER PAD', ward_tasks)


class PreopStageP2AnesthDosAndVqiTests(PrivateMediaTest):
    """Stage P2 tests: Structured Anesthesiology, Exact Calendar Med-Hold, DoS WHO Sign-In Gate & 30-Day VQI."""

    def test_p2_1_identifiers_dob_auto_age_ecosystem_prefills_and_exact_calendar_med_schedule(self):
        import datetime
        from .models import PreopPatient
        from .preop import evaluate, make_self_fill_token, merged_studies
        from django.test import Client

        # 1. Header saves 11-digit person_id, birth_date -> auto-computes age, and planned_surgery_date
        p = PreopPatient.objects.create(
            patient_name='პასპორტ კალენდარაძე', sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            visit_date=datetime.date(2026, 12, 1), started_on=datetime.date(2026, 12, 1),
        )
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'header',
            'patient_name': 'პასპორტ კალენდარაძე',
            'sex': 'M',
            'op_subtype': 'evar',
            'person_id': '010-24-055-891-99',
            'birth_date': '1958-04-15',
            'age': '',
            'slot_kind': 'dated',
            'visit_date': '2026-12-01',
            'planned_surgery_date': '2026-12-10',
            'phone': '599778899',
        })
        p.refresh_from_db()
        s = merged_studies(p)
        self.assertEqual(s['person_id'], '01024055891')
        self.assertEqual(s['birth_date'], '1958-04-15')
        self.assertIsNotNone(p.age)
        self.assertGreaterEqual(p.age, 68)

        # Enter meds + labs so exact calendar hold/continue dates are computed for 2026-12-10
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'htn': '1', 'dm': '1',
            'med_aspirin': '1', 'med_clopidogrel': '1', 'med_doac': '1',
            'med_warfarin': '1', 'sglt2': '1', 'glp1': '1',
            'med_chips': ['statin', 'acei_arb'],
            'weight_kg': '84', 'height_cm': '175',
            'aaa_mm': '58', 'cta_months': '1', 'cta_disc': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1',
            'weight_kg': '84', 'height_cm': '175',
            'aaa_mm': '58', 'cta_months': '1', 'cta_disc': '1',
            'hb': '136', 'plt': '210', 'inr': '1.05', 'creat': '82',
            'nt_probnp': '88', 'blood_group': 'A (II) Rh (+)', 'ecg': 'normal',
        })
        p.refresh_from_db()
        ev = evaluate(p, today=datetime.date(2026, 12, 1))
        sched = {m['drug']: m for m in ev['med_schedule']}
        self.assertIn('10.12', sched['ასპირინი (75–100 მგ)']['date_plan'])
        self.assertIn('05.12-დან', sched['კლოპიდოგრელი / P2Y12']['date_plan'])
        self.assertIn('08.12-დან', sched['DOAC (ქსარელტო / ელიქვისი)']['date_plan'])
        self.assertIn('05.12-დან', sched['ვარფარინი']['date_plan'])
        self.assertIn('INR კონტროლი 09.12', sched['ვარფარინი']['date_plan'])
        self.assertIn('07.12-დან', sched['SGLT2 (ჯარდიანსი / ფორსიგა)']['date_plan'])
        self.assertIn('03.12-დან', sched['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['date_plan'])
        self.assertIn('09.12 თხევადი დიეტა', sched['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['date_plan'])

        # Switching anesthesia_plan to neuraxial shifts DOAC hold to 72h (07.12) and Clopidogrel to 7d (03.12)
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'anesthesia_plan': 'neuraxial', 'planned_surgery_date': '2026-12-10',
        })
        p.refresh_from_db()
        ev_neur = evaluate(p, today=datetime.date(2026, 12, 1))
        sched_neur = {m['drug']: m for m in ev_neur['med_schedule']}
        self.assertIn('07.12-დან', sched_neur['DOAC (ქსარელტო / ელიქვისი)']['date_plan'])
        self.assertIn('03.12-დან', sched_neur['კლოპიდოგრელი / P2Y12']['date_plan'])

        # Ecosystem prefills: NIV-300-9/a and Blood Order receive person_id, dob (15.04.1958), bmi_score, blood_group
        niv_resp = self.client.get(reverse('niv-cover') + f'?preop={p.pk}')
        self.assertEqual(niv_resp.status_code, 200)
        niv_obj = niv_resp.context['niv']
        self.assertEqual(niv_obj.person_id, '01024055891')
        self.assertEqual(niv_obj.dob, '15.04.1958')
        self.assertEqual(niv_obj.bmi_score, '27.4')

        blood_resp = self.client.get(reverse('blood-order') + f'?preop={p.pk}')
        self.assertEqual(blood_resp.status_code, 200)
        self.assertEqual(blood_resp.context['form'].initial['person_id'], '01024055891')
        self.assertEqual(blood_resp.context['form'].initial['dob'], '15.04.1958')
        self.assertEqual(blood_resp.context['form'].initial['blood_group'], 'A (II) Rh (+)')

        # Printable A4 Passport renders person_id, DOB, planned surgery date, GLP-1 fasting, calendar dates & EVAR surveillance
        pp_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertEqual(pp_resp.status_code, 200)
        self.assertContains(pp_resp, '01024055891')
        self.assertContains(pp_resp, '15.04.1958')
        self.assertContains(pp_resp, '10.12.2026')
        self.assertContains(pp_resp, 'GLP-1 პროტოკოლი')
        self.assertContains(pp_resp, '07.12-დან')
        self.assertContains(pp_resp, 'ESVS 2024 EVAR მეთვალყურეობა')

        # Self-fill also supports optional person_id + birth_date -> auto-age
        sf_p = PreopPatient.objects.create(
            patient_name='თვით აიდიძე', sex='F', pathway='venous', op_subtype='evla', surgeon=self.user,
        )
        anon = Client()
        tok = make_self_fill_token(sf_p)
        anon.post(reverse('preop-self-fill', args=[tok]), {
            'person_id': '60001122334', 'birth_date': '1986-02-10', 'sex': 'F', 'mets_ok': '1',
        })
        sf_p.refresh_from_db()
        self.assertEqual(merged_studies(sf_p)['person_id'], '60001122334')
        self.assertIsNotNone(sf_p.age)
        self.assertIn('6 საათით ადრე', evaluate(sf_p)['fasting_instruction'])

    def test_p2_2_structured_anesthesiology_cabinet_asa_mallampati_stop_bang_cied_and_waiting_guard(self):
        import datetime
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_READY, evaluate, generate_anamnesis

        today = timezone.localdate()
        evla = PreopPatient.objects.create(
            patient_name='ჯანმრთელ ვენაძე', age=34, sex='F', side='R',
            pathway='venous', op_subtype='evla', surgeon=self.user,
            questionnaire={'filled': True, 'mets_ok': True},
        )
        self.assertEqual(evaluate(evla, today=today)['effective_asa'], 'ASA I')

        cea = PreopPatient.objects.create(
            patient_name='კაროტიდ ანესთეზიაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'questionnaire',
            'side': 'R', 'cea_symp_event': '1',
            'cea_event_date': (today - datetime.timedelta(days=5)).isoformat(),
            'cea_event_chips': ['tia_limb'],
            'htn': '1', 'pulm': '1', 'pulm_chips': ['osa'],
            'pacemaker': '1', 'med_aspirin': '1', 'med_chips': ['statin'],
            'mets_ok': '1', 'cta_disc': '1',
            'weight_kg': '112', 'height_cm': '174',
        })
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage1',
            'weight_kg': '112', 'height_cm': '174',
            'hb': '142', 'plt': '230', 'inr': '1.02', 'creat': '88', 'urine_wbc': '2',
            'carotid_r': '85', 'carotid_l': '30',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'nt_probnp': '95', 'cxr': 'normal', 'ecg': 'normal', 'cta_disc': '1',
        })
        # Stage 2: set planned_surgery_date 12 days later (5 + 12 = 17 > 14d NVR target!), Mallampati IV, CIED magnet, lines
        self.client.post(reverse('preop-patient', args=[cea.pk]), {
            'section': 'stage2',
            'anesth_cabinet_present': '1',
            'planned_surgery_date': (today + datetime.timedelta(days=12)).isoformat(),
            'asa_class': 'auto',
            'asa_emergency': '1',
            'airway_mallampati': 'IV_difficult',
            'stop_bang_risk': 'auto',
            'cied_plan': 'magnet_async',
            'anesth_lines_chips': ['art_line', 'cerebral_oximetry'],
            'neuro_consult_done': '1',
            'spiro_fev1': '78',
            'anesth_done': '1',
            'cardiologist': 'ლიკა ინჯია',
            'cardio_decision': 'cleared',
        })
        cea.refresh_from_db()
        ev = evaluate(cea, today=today)
        self.assertEqual(ev['auto_asa'], 'IV')
        self.assertEqual(ev['effective_asa'], 'ASA IV-E')
        self.assertTrue(ev['difficult_airway_alert'])
        self.assertEqual(ev['effective_stop_bang'], 'high')
        self.assertTrue(ev['needs_icu_bed'])
        # CIED plan automatically satisfied cied task, and ICU booking does NOT block Stage 2 STATUS_READY!
        self.assertEqual(ev['status'], STATUS_READY)
        joined_amber = ' | '.join(ev['amber_flags'])
        self.assertIn('Mallampati IV', joined_amber)
        self.assertIn('STOP-Bang', joined_amber)
        self.assertIn('14-დღიან ფანჯარას', joined_amber)

        anam = generate_anamnesis(cea, ev)
        self.assertIn('ASA IV-E', anam)
        self.assertIn('ცერებრული ოქსიმეტრია', anam)
        self.assertIn('Mallampati IV', anam)

    def test_p2_3_dos_who_signin_morning_vitals_stop_gate_and_one_click_complete_to_board_with_reverse_link(self):
        import datetime
        from .models import BoardEntry, BoardPatient, PreopPatient
        from .preop import evaluate, merged_studies

        today = datetime.date(2026, 12, 5)
        p = PreopPatient.objects.create(
            patient_name='დილა საოპერაციოძე', age=67, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={'filled': True, 'mets_ok': True, 'med_aspirin': True, 'med_warfarin': True},
            studies={'asa_class': 'IV'},
        )
        # 1. Dangerous morning BP (194/112) + high morning INR (1.8) triggers dos_hold_danger even if checks ticked
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'dos_bp_sys': '194', 'dos_bp_dia': '112',
            'dos_glucose': '15.2', 'dos_inr': '1.8',
            'dos_identity_consent': '1', 'dos_site_marked': '1',
            'dos_fasting_confirmed': '1', 'dos_med_hold_verified': '1',
            'dos_abx_ready': '1', 'dos_blood_implant_ready': '1', 'icu_bed_booked': '1',
        })
        p.refresh_from_db()
        ev_bad = evaluate(p, today=today)
        self.assertTrue(ev_bad['dos_hold_danger'])
        self.assertFalse(ev_bad['dos_ready'])
        self.assertEqual(len(ev_bad['dos_warnings']), 3)

        # 2. Stabilized morning vitals + complete_and_board marks operated & transfers ASA + surveillance to Board
        resp = self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'action': 'complete_and_board',
            'unit': 'icu', 'bed': '2',
            'dos_bp_sys': '134', 'dos_bp_dia': '78',
            'dos_glucose': '6.2', 'dos_inr': '1.12',
            'dos_identity_consent': '1', 'dos_site_marked': '1',
            'dos_fasting_confirmed': '1', 'dos_med_hold_verified': '1',
            'dos_abx_ready': '1', 'dos_blood_implant_ready': '1', 'icu_bed_booked': '1',
        })
        bp = BoardPatient.objects.get(name='დილა საოპერაციოძე')
        self.assertRedirects(resp, reverse('board-patient', args=[bp.pk]))
        p.refresh_from_db()
        ev_ok = evaluate(p, today=today)
        self.assertTrue(ev_ok['dos_ready'])
        self.assertEqual(merged_studies(p)['case_outcome_status'], 'operated')
        self.assertTrue(bool(merged_studies(p)['operated_date']))

        st_text = BoardEntry.objects.get(patient=bp, kind=BoardEntry.STATUS).text
        self.assertIn('ASA IV', st_text)
        tasks_joined = ' | '.join(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertIn('ESVS 2023 CEA მეთვალყურეობა', tasks_joined)

        # Reverse link on Board patient page points back to Preop ?tab=dos
        board_page = self.client.get(reverse('board-patient', args=[bp.pk]))
        self.assertContains(board_page, 'წინასაოპერაციო / VQI')
        self.assertContains(board_page, reverse('preop-patient', args=[p.pk]) + '?tab=dos')

    def test_p2_4_cancellation_audit_30d_svs_vqi_registry_and_executive_audit_kpis(self):
        import datetime
        from django.utils import timezone
        from .models import PreopPatient

        today = timezone.localdate()
        # 1. Operated symptomatic CEA within 8 days (<=14d target), full VQI discharge bundle, uneventful 30d
        p1 = PreopPatient.objects.create(
            patient_name='ვიქიაი წარმატებაძე', age=65, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={
                'filled': True, 'cea_symp_event': True,
                'cea_event_date': (today - datetime.timedelta(days=10)).isoformat(),
            },
        )
        self.client.post(reverse('preop-patient', args=[p1.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'case_outcome_status': 'operated',
            'operated_date': (today - datetime.timedelta(days=2)).isoformat(),
            'vqi_discharge_antiplatelet': '1',
            'vqi_discharge_statin': '1',
            'vqi_smoking_cessation': '1',
            'vqi_surveillance_scheduled': '1',
            'vqi_30d_status': 'uneventful',
        })

        # 2. Operated EVAR with 30-day endoleak complication chip (auto-sets vqi_30d_status='complication')
        p2 = PreopPatient.objects.create(
            patient_name='ენდოლიკ მონიტორიძე', age=73, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            questionnaire={'filled': True},
        )
        self.client.post(reverse('preop-patient', args=[p2.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'case_outcome_status': 'operated',
            'operated_date': today.isoformat(),
            'vqi_complication_chips': ['endoleak'],
        })

        # 3. Cancelled on DoS due to uncontrolled BP & anticoagulation not held
        p3 = PreopPatient.objects.create(
            patient_name='გაუქმება მიზეზაძე', age=70, sex='F',
            pathway='abf', op_subtype='abf_claud', surgeon=self.user,
            questionnaire={'filled': True},
        )
        self.client.post(reverse('preop-patient', args=[p3.pk]), {
            'section': 'dos_vqi',
            'dos_form_present': '1',
            'case_outcome_status': 'cancelled_medical',
            'cancel_reason_chips': ['uncontrolled_bp', 'anticoag_not_held'],
        })

        audit_resp = self.client.get(reverse('preop-audit'))
        self.assertEqual(audit_resp.status_code, 200)
        self.assertEqual(audit_resp.context['operated_total'], 2)
        self.assertEqual(audit_resp.context['cancelled_total'], 1)
        self.assertEqual(audit_resp.context['cancel_rate_pct'], 33.3)
        self.assertEqual(audit_resp.context['vqi_statin_ap_pct'], 50)
        self.assertEqual(audit_resp.context['cea_symp_within_14d_pct'], 100)
        self.assertEqual(audit_resp.context['vqi_uneventful_30d_pct'], 50)
        self.assertEqual(audit_resp.context['vqi_complication_total'], 1)
        self.assertContains(audit_resp, 'SVS VQI / NVR')
        self.assertContains(audit_resp, 'არაკონტროლირებადი ჰიპერტენზია')
        self.assertContains(audit_resp, 'ენდოლიკი')


class PreopStageP3ReferenceHardeningTests(PrivateMediaTest):
    """Stage P3 Reference-Grade (Level 5/5) tests: pharmacology/neuraxial safety, target-limb hemodynamics, hard contraindications, and full-cycle continuity."""

    def test_p3_1_pharmacology_neuraxial_epidural_abw_crcl_insulin_metformin_and_voyager_cea_guards(self):
        import datetime
        from .models import PreopPatient
        from .preop import evaluate, calc_crcl

        # 1. Devine Adjusted Body Weight (ABW) CrCl prevents renal DOAC overdose in morbid obesity (BMI >= 30)
        crcl_abw = calc_crcl(130, 74, sex='F', weight_kg=130, height_cm=160)
        self.assertEqual(crcl_abw, 44)
        # Raw Cockcroft-Gault with 130kg would falsely overestimate ~69 mL/min (>50); Devine ABW brings it to 44 (<50 renal threshold)!
        self.assertLess(crcl_abw, 50)

        # 2. Open AAA with 'epidural' chip in anesth_lines_chips (while anesthesia_plan='general') triggers ASRA 72h DOAC hold & DoS INR <= 1.2!
        p_open = PreopPatient.objects.create(
            patient_name='ეპიდურალ უსაფრთხოძე', age=72, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            questionnaire={
                'filled': True, 'dm': True, 'dm_insulin': True, 'metformin': True,
                'med_doac': True, 'med_chips': ['amlodipine', 'amiodarone'],
                'cad': True, 'cad_chips': ['hf'],
                'med_chips_extra': [],
            },
            studies={
                'anesthesia_plan': 'general',
                'anesth_lines_chips': ['art_line', 'epidural'],
                'planned_surgery_date': '2026-12-10',
                'creat': 85, 'weight_kg': 82, 'height_cm': 174,
                'dos_inr': 1.35,
            },
        )
        p_open.questionnaire['med_chips'] = ['ccb', 'amiodarone', 'acei_arb']
        p_open.save()

        ev_open_norm = evaluate(p_open, today=datetime.date(2026, 12, 1))
        self.assertTrue(ev_open_norm['has_neuraxial_plan'])
        self.assertIn('72 საათით', {m['drug']: m for m in ev_open_norm['med_schedule']}['DOAC (ქსარელტო / ელიქვისი)']['rule'])

        # Escalate to severe CKD (creat=240 -> eGFR < 30 / CrCl < 30): DOAC becomes >=96h & Metformin becomes 'red'
        p_open.studies['creat'] = 240
        p_open.save()
        ev_open = evaluate(p_open, today=datetime.date(2026, 12, 1))
        self.assertTrue(ev_open['has_neuraxial_plan'])
        self.assertTrue(ev_open['dos_hold_danger'])
        self.assertTrue(any('1.2' in w for w in ev_open['dos_warnings']))
        sched_open = {m['drug']: m for m in ev_open['med_schedule']}
        self.assertIn('≥96 სთ', sched_open['DOAC (ქსარელტო / ელიქვისი)']['rule'])
        self.assertIn('ინსულინი', sched_open)
        self.assertIn('75–80%', sched_open['ინსულინი']['rule'])
        self.assertIn('მეტფორმინი', sched_open)
        self.assertEqual(sched_open['მეტფორმინი']['tone'], 'red')  # eGFR < 30 -> stop immediately!
        self.assertIn('ამლოდიპინი / CCB', sched_open)
        self.assertIn('SBP ≥ 110', sched_open['ამლოდიპინი / CCB']['rule'])
        self.assertIn('ამიოდარონი', sched_open)
        self.assertEqual(sched_open['ამიოდარონი']['tone'], 'ok')
        # HFrEF ('hf' chip) keeps ACEi/ARB instead of blind 24h hold!
        self.assertIn('HFrEF', sched_open['ACEi / ARB (პრილი/სარტანი)']['rule'])

        # 3. CEA on OAC alone (no aspirin/P2Y12) triggers needs_cea_antiplatelet=True, UNLESS brain CT shows hemorrhage!
        p_cea = PreopPatient.objects.create(
            patient_name='კაროტიდ ანტითრომბაძე', age=69, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'cea_symp_event': True, 'med_doac': True},
            studies={'carotid_r': 80, 'brain_imaging': 'normal_small'},
        )
        ev_cea = evaluate(p_cea)
        self.assertTrue(ev_cea['needs_cea_antiplatelet'])

        p_cea.studies['brain_imaging'] = 'large_or_hemorrhage'
        p_cea.save()
        ev_cea_hem = evaluate(p_cea)
        self.assertFalse(ev_cea_hem['needs_cea_antiplatelet'])
        self.assertTrue(ev_cea_hem['has_hard_contraindication'])

        # 4. VOYAGER PAD blocked on dialysis / prior stroke, and manual VOYAGER + full OAC raises triple-therapy Red Flag!
        p_pad = PreopPatient.objects.create(
            patient_name='ვოიაჯერ გვარდაძე', age=67, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            questionnaire={'filled': True, 'on_dialysis': True, 'neuro': True},
            studies={'abi_r': 0.35, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate'},
        )
        ev_pad = evaluate(p_pad)
        self.assertNotIn('VOYAGER', ev_pad['post_antithrombotic_label'])

        p_pad.questionnaire = {'filled': True, 'med_doac': True}
        p_pad.studies['post_antithrombotic_plan'] = 'voyager'
        p_pad.save()
        ev_pad_oac = evaluate(p_pad)
        self.assertTrue(any('VOYAGER' in rf for rf in ev_pad_oac['red_flags']))

    def test_p3_2_target_limb_tbi_venous_c6_hard_contraindications_nan_safety_and_self_fill_112(self):
        from django.test import Client
        from .models import PreopPatient
        from .preop import evaluate, make_self_fill_token, STATUS_ALERT

        # 1. Right-sided EVLA with non-compressible right ABI (1.55) + normal left TP (78) MUST NOT clear tbi_measured or compression_ready!
        p_ven = PreopPatient.objects.create(
            patient_name='ტარგეტ კიდურაძე', age=42, sex='F', side='R',
            pathway='venous', op_subtype='evla', surgeon=self.user,
            questionnaire={'filled': True, 'ven_ceap': 'C6'},
            studies={
                'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 8.0,
                'hb': 132, 'plt': 240, 'weight_kg': 60,
                'abi_r': 1.55, 'abi_l': 1.05, 'tp_l': 78,
                'compression_ready': True,
            },
        )
        ev_ven = evaluate(p_ven)
        # C6 venous ulcer in a 42yo non-diabetic MUST still trigger mandatory ABI screening!
        self.assertTrue(ev_ven['needs_abi_check'])
        self.assertFalse(ev_ven['tbi_measured'])
        self.assertFalse(ev_ven['tbi_comp_safe'])
        comp_task = next(t for t in ev_ven['tasks'] if t['key'] == 'comp')
        self.assertFalse(comp_task['done'])
        # EVLA max tumescent lidocaine with epinephrine for 60kg = 420 mg (min(500, 60*7))
        self.assertEqual(ev_ven['lidocaine_max_mg'], 420)

        # Entering ipsilateral tp_r=72 clears tbi_measured and compression_ready!
        p_ven.studies['tp_r'] = 72
        p_ven.save()
        ev_ven_ok = evaluate(p_ven)
        self.assertTrue(ev_ven_ok['tbi_measured'])
        self.assertTrue(ev_ven_ok['tbi_comp_safe'])
        self.assertTrue(next(t for t in ev_ven_ok['tasks'] if t['key'] == 'comp')['done'])

        # 2. Hard contraindications (100% ICA occlusion & acute_dvt) block STATUS_READY, dos_ready, and surgeon approve!
        p_occ = PreopPatient.objects.create(
            patient_name='ოკლუზია ბლოკაძე', age=68, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'cea_symp_event': True, 'med_aspirin': True},
            studies={
                'carotid_r': 100, 'carotid_l': 30, 'cea_cta_status': 'concordant',
                'brain_imaging': 'normal_small', 'cta_disc': True,
                'hb': 140, 'plt': 220, 'inr': 1.0, 'creat': 85, 'glucose': 5.4, 'k': 4.2,
                'lvef': 58, 'ecg': 'normal', 'neuro_consult_done': True, 'anesth_done': True,
                'icu_bed_booked': True,
                'dos_identity_consent': True, 'dos_site_marked': True, 'dos_fasting_confirmed': True,
                'dos_med_hold_verified': True, 'dos_abx_ready': True, 'dos_blood_implant_ready': True,
            },
        )
        p_occ.cardio_decision = 'cleared'
        p_occ.save()
        ev_occ = evaluate(p_occ)
        self.assertTrue(ev_occ['has_hard_contraindication'])
        self.assertEqual(ev_occ['status'], STATUS_ALERT)
        self.assertFalse(ev_occ['dos_ready'])

        # Posting approve MUST be rejected on hard contraindication!
        self.client.post(reverse('preop-patient', args=[p_occ.pk]), {
            'section': 'signoff', 'action': 'approve', 'anamnesis': 'ტესტი',
        })
        p_occ.refresh_from_db()
        self.assertIsNone(p_occ.approved_at)

        # 3. NaN / Inf / negative-zero fuzz inputs on Stage 1 NEVER raise HTTP 500!
        resp_nan = self.client.post(reverse('preop-patient', args=[p_ven.pk]), {
            'section': 'stage1',
            'hb': 'nan', 'creat': 'inf', 'k': '-inf', 'abi_r': 'NaN', 'ven_gsv_mm': 'Infinity',
        })
        self.assertIn(resp_nan.status_code, (200, 302))
        p_ven.refresh_from_db()
        self.assertIsNone(p_ven.studies.get('hb'))

        # 4. Self-Fill saves active_infection across pathways, saves dm_insulin additively, renders 112 banner on aaa_pain, and locks post-op!
        p_sf = PreopPatient.objects.create(
            patient_name='თვითშევსება სასწრაფოძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='open',
        )
        tok = make_self_fill_token(p_sf)
        anon = Client()
        r_post = anon.post(reverse('preop-self-fill', args=[tok]), {
            'age': '71', 'sex': 'M', 'aaa_pain': '1', 'active_infection': '1', 'dm_insulin': '1',
        }, follow=True)
        self.assertEqual(r_post.status_code, 200)
        self.assertContains(r_post, '112')
        p_sf.refresh_from_db()
        self.assertTrue(p_sf.questionnaire.get('aaa_pain'))
        self.assertTrue(p_sf.questionnaire.get('active_infection'))
        self.assertTrue(p_sf.questionnaire.get('dm_insulin'))

        # Operated patient locks Self-Fill!
        p_sf.questionnaire['nurse_verified'] = False
        p_sf.studies = {'case_outcome_status': 'operated'}
        p_sf.save()
        r_locked = anon.post(reverse('preop-self-fill', args=[tok]), {'aaa_pain': '0'})
        self.assertIn('locked=1', r_locked['Location'])

    def test_p3_3_continuity_case_plan_same_name_board_isolation_archive_vqi_hold_pause_and_sticky_chips(self):
        import datetime
        from django.utils import timezone
        from .models import Case, PreopPatient, BoardPatient
        from .preop import detect_case_preop, evaluate, prep_days_elapsed, resolve_preop_for_board

        today = timezone.localdate()

        # 1. Conference Case routing: "PLAN: ენდოვასკულური" -> PAD_PTA; "საჩინო ვენით შუნტირება" -> PAD_BYPASS (not VENOUS!)
        c_endo = Case.objects.create(
            patient_name='ენდო გეგმაძე', diagnosis='მარჯვენა SFA ოკლუზია, კრიტიკული იშემია',
            history='გეგმა (PLAN): ენდოვასკულური რევასკულარიზაცია (PTA)', created_by=self.user,
        )
        sub_e, side_e = detect_case_preop(c_endo)
        self.assertEqual((sub_e, side_e), ('pta_clti', 'R'))

        c_byp = Case.objects.create(
            patient_name='აუტოვენა შუნტაძე', diagnosis='მარცხენა ფემორო-პოპლიტეალური შუნტირება დიდი საჩინო ვენით',
            created_by=self.user,
        )
        sub_b, side_b = detect_case_preop(c_byp)
        self.assertEqual((sub_b, side_b), ('bypass_claud', 'L'))

        # 2. Two distinct patients named "გიორგი ბერიძე" NEVER merge or hijack each other's BoardPatient cards!
        g1 = PreopPatient.objects.create(
            patient_name='გიორგი ბერიძე', age=74, sex='M', side='R',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            questionnaire={'filled': True, 'htn': True},
            studies={'person_id': '01001001001', 'birth_date': '1952-03-10', 'blood_group': 'O (I) Rh (+)'},
        )
        g2 = PreopPatient.objects.create(
            patient_name='გიორგი ბერიძე', age=45, sex='M', side='L',
            pathway='venous', op_subtype='phlebectomy', surgeon=self.user,
            questionnaire={'filled': True},
            studies={'person_id': '01002002002', 'birth_date': '1981-07-22', 'blood_group': 'B (III) Rh (-)'},
        )
        self.client.post(reverse('preop-admit-board', args=[g1.pk]), {'unit': 'icu', 'bed': '1'})
        self.client.post(reverse('preop-admit-board', args=[g2.pk]), {'unit': 'ward', 'bed': '8'})
        g1.refresh_from_db()
        g2.refresh_from_db()
        bp1_id = g1.studies.get('board_patient_id')
        bp2_id = g2.studies.get('board_patient_id')
        self.assertIsNotNone(bp1_id)
        self.assertIsNotNone(bp2_id)
        self.assertNotEqual(bp1_id, bp2_id)
        bp1 = BoardPatient.objects.get(pk=bp1_id)
        self.assertIn('CEA', bp1.diagnosis)

        # 3. Archiving PreopPatient preserves resolve_preop_for_board + ?board=<pk> NIV & BloodOrder prefill + intact niv.neuro=True!
        g1.archived = True
        g1.save()
        self.assertEqual(resolve_preop_for_board(bp1).pk, g1.pk)

        niv_from_board = self.client.get(reverse('niv-cover') + f'?board={bp1.pk}')
        self.assertEqual(niv_from_board.status_code, 200)
        niv_g1 = niv_from_board.context['niv']
        self.assertEqual(niv_g1.person_id, '01001001001')
        self.assertTrue(niv_g1.neuro)  # Intact neurology when cea_disabling_stroke is False!

        blood_from_board = self.client.get(reverse('blood-order') + f'?board={bp1.pk}')
        self.assertEqual(blood_from_board.status_code, 200)
        self.assertEqual(blood_from_board.context['form'].initial.get('person_id'), '01001001001')
        self.assertEqual(blood_from_board.context['form'].initial.get('blood_group'), 'O (I) Rh (+)')

        # 4. HOLD pause subtracts deferred days; operated freezes clock & auto-decongests active week.html (?outcome=done shows it)!
        p_clock = PreopPatient.objects.create(
            patient_name='საათ პაუზაძე', age=66, sex='M',
            pathway='abf', op_subtype='abf_claud', surgeon=self.user,
            visit_date=today, started_on=today - datetime.timedelta(days=10),
            studies={'hold_paused_days': 6},
        )
        self.assertEqual(prep_days_elapsed(p_clock, today=today), 5)

        self.client.post(reverse('preop-patient', args=[p_clock.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1',
            'case_outcome_status': 'operated',
            'operated_date': (today - datetime.timedelta(days=2)).isoformat(),
        })
        p_clock.refresh_from_db()
        # Operated patient is hidden from active week view, and visible in ?outcome=done!
        w_active = self.client.get(reverse('preop') + f'?week={today.isoformat()}')
        self.assertNotContains(w_active, 'საათ პაუზაძე')
        w_done = self.client.get(reverse('preop') + f'?week={today.isoformat()}&outcome=done')
        self.assertContains(w_done, 'საათ პაუზაძე')

        # 5. Sticky chip fix, non-frozen Day-1 ack_alerts anamnesis, side-change site-mark reset, and 0.0 ABI passport rendering!
        p_ui = PreopPatient.objects.create(
            patient_name='ჩიპ სისუფთავეძე', age=64, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            questionnaire={'filled': True, 'abf_rest_pain': True},
            studies={'abi_r': 0.0, 'tp_r': 0, 'dos_site_marked': True},
        )
        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'stage2', 'nurse_note_chips': ['waiting_labs'], 'nurse_note_custom': '',
        })
        p_ui.refresh_from_db()
        self.assertIn('ანალიზის პასუხს', p_ui.studies.get('nurse_note', ''))

        # Unchecking nurse_note_chips with empty nurse_note_custom MUST clear nurse_note completely!
        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'stage2', 'nurse_note_custom': '',
        })
        p_ui.refresh_from_db()
        self.assertEqual(p_ui.studies.get('nurse_note', ''), '')

        # Day-1 ack_alerts MUST NOT freeze patient.anamnesis, so later lab updates still appear in shown_anamnesis!
        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'signoff', 'action': 'ack_alerts', 'anamnesis': 'დღე 1 ტექსტი',
        })
        p_ui.refresh_from_db()
        self.assertEqual(p_ui.anamnesis, '')

        # Passport renders critical ischemic 0.0 ABI and 0 mmHg TP instead of '—' on target side R!
        pp_resp = self.client.get(reverse('preop-passport', args=[p_ui.pk]))
        self.assertEqual(pp_resp.status_code, 200)
        self.assertContains(pp_resp, 'ABI 0.0/')
        self.assertContains(pp_resp, 'TP 0/')
        self.assertContains(pp_resp, 'TP min 0 mmHg')

        # Changing side R -> L resets dos_site_marked to prevent wrong-side surgery!
        self.client.post(reverse('preop-patient', args=[p_ui.pk]), {
            'section': 'header', 'patient_name': 'ჩიპ სისუფთავეძე', 'sex': 'M',
            'op_subtype': 'bypass_clti', 'side': 'L',
        })
        p_ui.refresh_from_db()
        self.assertFalse(p_ui.studies.get('dos_site_marked'))


class PreopPrePilotRefinementAndSimulationTests(PrivateMediaTest):
    """Pre-Pilot Phase 1-4 tests: Nurse UX, 2-Page Specialist Dossier, Risk Indices & Simulation Objectivity."""

    def test_pre_pilot_1_nurse_ergonomics_jump_chips_self_fill_reconciliation_and_pre_q_alerts(self):
        from django.test import Client
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import STATUS_ALERT, STATUS_NO_Q, evaluate, make_self_fill_token, merged_q

        today = timezone.localdate()

        # 1. Pre-Questionnaire Critical Alert Visibility: lab/duplex red flag BEFORE questionnaire MUST trigger STATUS_ALERT!
        p_pre = PreopPatient.objects.create(
            patient_name='ლაბ ადრეულაძე', age=68, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        self.assertEqual(evaluate(p_pre, today=today)['status'], STATUS_NO_Q)
        self.assertEqual(evaluate(p_pre, today=today)['next_step_hint'], 'აკლია: კითხვარი')

        # Week calendar renders nurse next_step_hint directly on active patient row
        week_resp = self.client.get(reverse('preop') + f'?week={today.isoformat()}')
        self.assertContains(week_resp, 'აკლია: კითხვარი')

        # Entering critical hyperkalemia (K+ 6.1) before Tab 1 immediately promotes status to STATUS_ALERT!
        self.client.post(reverse('preop-patient', args=[p_pre.pk]), {
            'section': 'stage1', 'k': '6.1',
        })
        p_pre.refresh_from_db()
        self.assertEqual(evaluate(p_pre, today=today)['status'], STATUS_ALERT)

        # 2. Patient Self-Fill -> Positive-Finding Summary Card + 1-Tap Nurse Verification + Guided Jump Chips
        p_sf = PreopPatient.objects.create(
            patient_name='ბმულ გადამოწმებაძე', age=71, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            visit_date=today, started_on=today,
        )
        tok = make_self_fill_token(p_sf)
        anon = Client()
        anon.post(reverse('preop-self-fill', args=[tok]), {
            'mets_ok': '1',
            'htn': '1',
            'dm': '1',
            'dm_insulin': '1',
            'med_doac': '1',
            'glp1': '1',
            'allergy_iodine': '1',
            'person_id': '01099887766',
        })
        p_sf.refresh_from_db()
        ev_sf = evaluate(p_sf, today=today)
        self.assertTrue(len(ev_sf['self_fill_highlights']) >= 4)
        self.assertTrue(any(m['field'] == 'btn-verify-self' for m in ev_sf['missing_items']))

        card_resp = self.client.get(reverse('preop-patient', args=[p_sf.pk]))
        self.assertContains(card_resp, 'po-sf-summary')
        self.assertContains(card_resp, 'btn-verify-self')
        self.assertContains(card_resp, 'po-jump-chip')
        self.assertContains(card_resp, 'po-sticky-save')
        self.assertContains(card_resp, 'po-edit-drawer')

        # Nurse clicks 1-tap verify & confirm on Tab 1
        self.client.post(reverse('preop-patient', args=[p_sf.pk]), {
            'section': 'questionnaire',
            'mets_ok': '1', 'htn': '1', 'dm': '1', 'dm_insulin': '1',
            'med_doac': '1', 'glp1': '1', 'allergy_iodine': '1',
        })
        p_sf.refresh_from_db()
        self.assertTrue(merged_q(p_sf)['nurse_verified'])
        ev_after = evaluate(p_sf, today=today)
        self.assertFalse(any(m['field'] == 'btn-verify-self' for m in ev_after['missing_items']))
        self.assertIn('hb', ev_after['s1_required_fields'])
        self.assertIn('aaa_mm', ev_after['s1_required_fields'])

    def test_pre_pilot_2_academic_two_page_dossier_risk_indices_and_specialist_briefings(self):
        import datetime
        from django.utils import timezone
        from .models import PreopPatient
        from .preop import evaluate

        today = timezone.localdate()

        # 1. Lone Controlled AFib on EVAR -> ESC 2022 Table 5 non-inflation: RCRI = 0/6, VSG-CRI IHD/CHF = 0!
        p_af = PreopPatient.objects.create(
            patient_name='რიტმ ობიექტურაძე', age=72, sex='M',
            pathway='aaa_infra', op_subtype='evar', surgeon=self.user,
            cardio_decision='cleared', visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'cad': True, 'cad_chips': ['afib'],
                'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True,
            },
            studies={
                'aaa_mm': 58, 'aaa_neck_status': 'friendly_ifu', 'popliteal_screen_status': 'normal',
                'cta_disc': True, 'hb': 140, 'plt': 225, 'creat': 88, 'creat_unit': 'umol',
                'inr': 1.05, 'lvef': 58, 'nt_probnp': 110, 'ecg': 'afib_controlled',
            },
        )
        ev_af = evaluate(p_af, today=today)
        self.assertEqual(ev_af['rcri']['score'], 0)
        self.assertEqual(ev_af['vsg_cri']['score'], 3)  # age 70-79 (+3) only, zero CAD/CHF inflation!

        # 2. High-acuity Open AAA (132 kg, prior MI + CABG, insulin, Cr 2.3 mg/dL, SpO2 89%, Hb 96) -> ASA IV, RCRI 4, ARISCAT High, Cefazolin 3g!
        p_open = PreopPatient.objects.create(
            patient_name='აკადემიურ დოსიეძე', age=76, sex='M',
            pathway='aaa_infra', op_subtype='open', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'cad': True, 'cad_chips': ['mi', 'cabg'],
                'dm': True, 'dm_insulin': True, 'neuro': True,
                'med_aspirin': True, 'med_chips': ['statin', 'beta_blocker'], 'mets_ok': True,
            },
            studies={
                'weight_kg': 132, 'height_cm': 174, 'aaa_mm': 64, 'cta_disc': True,
                'bp_sys': 148, 'bp_dia': 84, 'bp_sys_l': 122, 'bp_dia_l': 76, 'spo2': 89,
                'hb': 96, 'plt': 185, 'creat': 2.3, 'creat_unit': 'mgdl', 'inr': 1.06,
                'albumin': 31, 'lvef': 46, 'ecg': 'normal', 'blood_group': 'A (II) Rh (+)',
            },
        )
        ev_op = evaluate(p_open, today=today)
        self.assertEqual(ev_op['auto_asa'], 'IV')
        self.assertEqual(ev_op['rcri']['score'], 5)  # Open AAA + IHD + Prior Stroke + Insulin + Cr>2.0
        self.assertEqual(ev_op['rcri']['tier'], 'high')
        self.assertEqual(ev_op['ariscat']['tier'], 'high')
        self.assertIn('ცეფაზოლინი 3 გ IV', ev_op['surgical_brief']['abx_regimen'])
        self.assertIn('მარჯვენა მკლავზე', ev_op['anesthetic_brief']['bp_corridor'])
        self.assertIn('Hb ≥ 80–90 გ/ლ', ev_op['anesthetic_brief']['transfusion_pbm'])

        # Render 2-Page A4 Passport and verify Page 1 + Page 2 structure & specialist sections
        pp = self.client.get(reverse('preop-passport', args=[p_open.pk]))
        self.assertEqual(pp.status_code, 200)
        self.assertContains(pp, 'pp-page-1')
        self.assertContains(pp, 'pp-page-2')
        self.assertContains(pp, 'გვერდი 1 / 2')
        self.assertContains(pp, 'გვერდი 2 / 2')
        self.assertContains(pp, 'RCRI (Lee / ESC)')
        self.assertContains(pp, 'VSG-CRI (SVS VQI)')
        self.assertContains(pp, 'ARISCAT (ფილტვი)')
        self.assertContains(pp, '2A. ქირურგიული და ანატომიური გეგმა')
        self.assertContains(pp, '2B. ანესთეზიოლოგიური და ჰემოდინამიკური გეგმა')
        self.assertContains(pp, 'ცეფაზოლინი 3 გ IV')

        # 3. ASRA 5th Ed Uninterrupted-P2Y12 Neuraxial Barrier + Severe CKD Beta-Lactam Gentamicin Guard + CEA Thrombolysis Heparin Guard
        p_nx = PreopPatient.objects.create(
            patient_name='სპინალ დაცვაძე', age=67, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'abf_rest_pain': True, 'recent_stent': True,
                'med_aspirin': True, 'med_clopidogrel': True,
                'allergy_other': True, 'allergy_chips': ['beta_lactam'], 'mets_ok': True,
            },
            studies={
                'abi_r': 0.38, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate',
                'hb': 128, 'plt': 215, 'inr': 1.02, 'creat': 260, 'creat_unit': 'umol',
                'ecg': 'normal', 'anesthesia_plan': 'neuraxial', 'med_hold_agreed': True,
            },
        )
        ev_nx = evaluate(p_nx, today=today)
        self.assertIn('უკუნაჩვენებია', ev_nx['anesthetic_brief']['neuraxial_gate'])
        self.assertTrue(any('სპინალური ჰემატომის' in rf for rf in ev_nx['red_flags']))
        self.assertIn('გენტამიცინს მოერიდეთ', ev_nx['surgical_brief']['abx_regimen'])

        p_cea = PreopPatient.objects.create(
            patient_name='თრომბოლიზ კაროტიდაძე', age=69, sex='F', side='L',
            pathway='cea', op_subtype='cea_symp', surgeon=self.user,
            visit_date=today, started_on=today,
            questionnaire={
                'filled': True, 'cea_symp_event': True,
                'cea_event_date': (today - datetime.timedelta(days=3)).isoformat(),
                'cea_thrombolysis': True, 'mets_ok': True,
            },
            studies={'carotid_r': 35, 'carotid_l': 85, 'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small'},
        )
        ev_cea = evaluate(p_cea, today=today)
        self.assertFalse(ev_cea['needs_cea_antiplatelet'])
        self.assertIn('ჰემორაგიული ტრანსფორმაციის', ev_cea['surgical_brief']['heparin_plan'])
        self.assertIn('SBP < 140–150 mmHg', ev_cea['anesthetic_brief']['bp_corridor'])

    def test_pre_pilot_3_simulation_invariants_and_multi_pathway_e2e_lifecycle(self):
        import datetime
        from django.utils import timezone
        from .models import BoardPatient, Case, PreopPatient
        from .preop import STATUS_READY, evaluate
        from .preop_sim import run_simulation

        # 1. Fast CI Regression Slice of the Synthetic Simulation Engine (1,500 scenarios + 250 metamorphic pairs = 2,000 evaluations)
        sim_report = run_simulation(n=1500, n_mono=250, passport_sample=100, seed=2026)
        self.assertEqual(sim_report['exceptions_count'], 0)
        self.assertEqual(sim_report['invariant_violations_total'], 0)
        self.assertEqual(sim_report['metamorphic_violations'], {})
        for oracle_name, metrics in sim_report['guideline_oracles'].items():
            self.assertEqual(metrics['fn'], 0, msg=f'Missed positive in {oracle_name}: {metrics}')
            self.assertEqual(metrics['fp'], 0, msg=f'False positive in {oracle_name}: {metrics}')

        # 2. Full End-to-End HTTP/DB Lifecycle across Conference -> Preop -> Passport -> DoS -> Board -> Discharge -> VQI Audit
        today = timezone.localdate()
        case = Case.objects.create(
            patient_name='სრული ციკლაძე',
            age=68,
            sex='M',
            side='R',
            diagnosis='მარჯვენა შიგნითა საძილე არტერიის სიმპტომური სტენოზი 85%',
            proposal='მარჯვენა კაროტიდული ენდარტერექტომია (CEA)',
            created_by=self.user,
        )
        r_create = self.client.get(reverse('preop-new') + f'?case={case.pk}')
        p = PreopPatient.objects.get(case=case)
        self.assertEqual(r_create.status_code, 302)
        self.assertEqual((p.op_subtype, p.side), ('cea_symp', 'R'))

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'header', 'patient_name': p.patient_name, 'age': '68', 'sex': 'M',
            'op_subtype': 'cea_symp', 'side': 'R', 'person_id': '01055667788',
            'birth_date': '1958-05-10', 'slot_kind': 'dated', 'visit_date': today.isoformat(),
            'planned_surgery_date': (today + datetime.timedelta(days=2)).isoformat(),
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'questionnaire', 'side': 'R', 'cta_disc': '1',
            'cea_symp_event': '1', 'cea_event_date': (today - datetime.timedelta(days=4)).isoformat(),
            'cea_event_chips': ['tia'], 'med_aspirin': '1', 'med_chips': ['statin'], 'mets_ok': '1',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1', 'bp_sys': '136', 'bp_dia': '80', 'hr': '72', 'spo2': '98',
            'hb': '142', 'plt': '230', 'inr': '1.02', 'creat': '84', 'creat_unit': 'umol',
            'glucose': '5.4', 'carotid_r': '85', 'carotid_l': '30',
            'cea_cta_status': 'concordant', 'brain_imaging': 'normal_small',
            'cta_disc': '1', 'ecg': 'normal', 'blood_group': 'O (I) Rh (+)',
        })
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage2', 'neuro_consult_done': '1', 'decolonization_ready': '1', 'anesth_done': '1',
        })
        p.refresh_from_db()
        ev_ready = evaluate(p, today=today)
        self.assertEqual(ev_ready['status'], STATUS_READY)
        self.assertEqual(ev_ready['clearance_seal']['code'], 'go')

        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'signoff', 'action': 'approve',
        })
        pp_resp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertContains(pp_resp, 'GO — უსაფრთხოდ მზადაა ოპერაციისთვის')

        # Morning of surgery WHO Sign-In + Complete & Transfer to ICU Board
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1', 'action': 'complete_and_board',
            'unit': 'icu', 'bed': '2',
            'dos_bp_sys': '134', 'dos_bp_dia': '78', 'dos_glucose': '5.6', 'dos_inr': '1.02',
            'dos_identity_consent': '1', 'dos_site_marked': '1', 'dos_fasting_confirmed': '1',
            'dos_med_hold_verified': '1', 'dos_abx_ready': '1', 'dos_blood_implant_ready': '1',
            'icu_bed_booked': '1',
        })
        p.refresh_from_db()
        bp = BoardPatient.objects.get(pk=p.studies['board_patient_id'])
        bp.discharged_at = timezone.now()
        bp.save(update_fields=['discharged_at'])

        # Record 30-day VQI discharge bundle & uneventful 30d outcome, then verify Audit KPIs
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1', 'action': 'save_dos',
            'case_outcome_status': 'operated', 'operated_date': today.isoformat(),
            'vqi_discharge_antiplatelet': '1', 'vqi_discharge_statin': '1',
            'vqi_surveillance_scheduled': '1', 'vqi_30d_status': 'uneventful',
        })
        audit_resp = self.client.get(reverse('preop-audit'))
        self.assertEqual(audit_resp.status_code, 200)
        self.assertEqual(audit_resp.context['vqi_statin_ap_pct'], 100)
        self.assertEqual(audit_resp.context['cea_symp_within_14d_pct'], 100)
        self.assertEqual(audit_resp.context['vqi_uneventful_30d_pct'], 100)




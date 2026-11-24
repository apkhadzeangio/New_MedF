"""Tests for the NIV-300-9/a forms.

Run:  python manage.py test --settings=MedicalForms.settings.dev
"""
import json
import shutil
from unittest import skipUnless

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

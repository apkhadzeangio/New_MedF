"""Tests for the NIV-300-9/a forms.

Run:  python manage.py test --settings=MedicalForms.settings.dev
"""
import json
import shutil
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Medicine, NivForm
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
        self.assertEqual(result['gk'], {'d3': {'l': '+'}})
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
        self.assertContains(r, 'მომხმარებელი ან პაროლი არასწორია')

    def test_lockout_after_too_many_failures(self):
        for _ in range(5):
            self.client.post('/', {'username': 'doc', 'password': 'nope'})
        r = self.client.post('/', {'username': 'doc', 'password': 'Secret-pass-123'})
        self.assertContains(r, 'ძალიან ბევრი წარუმატებელი მცდელობა')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_session_ends_with_browser_unless_remember_me(self):
        self.client.post('/', {'username': 'doc', 'password': 'Secret-pass-123'})
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.client.logout()
        self.client.post('/', {'username': 'doc', 'password': 'Secret-pass-123', 'remember_me': 'on'})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

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
        niv = self.make_niv(gk=dict(d1=hours('c'), d2={}, d3={}))
        self.client.post(reverse('niv-create'), self.post_data(
            t=niv.pk, action='save_as', title=niv.title, **{'as-title': 'ასლი', 'm1': 'Changed'}))
        niv.refresh_from_db()
        self.assertEqual(niv.m1, 'Aspirin')
        copy = NivForm.objects.get(title='ასლი')
        self.assertEqual(copy.m1, 'Changed')
        self.assertEqual(copy.gk['d1'], {'c': '+'})  # back-side data copied too
        self.assertEqual(NivForm.objects.count(), 2)

    def test_invalid_data_shows_error_and_keeps_record(self):
        niv = self.make_niv()
        r = self.client.post(reverse('niv-create'), self.post_data(
            t=niv.pk, action='save', title=niv.title, person_id='1' * 20), follow=True)
        self.assertContains(r, 'შაბლონი ვერ შეინახა')
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

    def test_back_print_page(self):
        niv = self.make_niv()
        r = self.client.get(f'/niv-300-9-a/back/print/?t={niv.pk}')
        self.assertEqual(r.status_code, 200)


@override_settings(DEBUG=False)
class PasswordChangeTests(BaseViewTest):

    def test_change_password(self):
        r = self.client.post(reverse('password-change'), {
            'old_password': 'Secret-pass-123',
            'new_password1': 'Another-pass-456',
            'new_password2': 'Another-pass-456',
        })
        self.assertRedirects(r, reverse('niv-cover'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Another-pass-456'))

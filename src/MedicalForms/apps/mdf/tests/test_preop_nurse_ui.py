"""Tests for Track B Nurse Workspace (per-user UI switch, 09:00 handover queues, 03:00 verdicts)."""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import PreopPatient, StaffProfile
from ..preop.nurse_ui import PREOP_UI_NEW, PREOP_UI_OLD, get_preop_ui_mode


class PreopNurseWorkspaceB1Tests(TestCase):
    """Verify per-user 'ახალი ხედი / ძველი ხედი' switch and 09:00 / 03:00 nurse handover screen."""

    def setUp(self):
        self.nurse = User.objects.create_user('nurse_b1', password='pw')
        self.nurse_profile = StaffProfile.objects.create(user=self.nurse, display_name='ნინო ექთანი')
        self.other_user = User.objects.create_user('doc_b1', password='pw')
        self.other_profile = StaffProfile.objects.create(user=self.other_user, display_name='გიორგი ექიმი')
        self.no_profile_user = User.objects.create_user('temp_nurse', password='pw')

    def test_ui_mode_switch_is_per_user_and_defaults_to_old(self):
        self.client.force_login(self.nurse)

        # Default view is classic 'ძველი ხედი' with a visible switch button to 'ახალი ხედი'
        r_old = self.client.get(reverse('preop'))
        self.assertEqual(r_old.status_code, 200)
        self.assertTemplateUsed(r_old, 'mdf/preop/week.html')
        self.assertContains(r_old, 'ახალი ხედი / ძველი ხედი')

        # Switch nurse to 'ახალი ხედი'
        r_sw = self.client.post(reverse('preop-ui-mode'), {'mode': 'new', 'next': reverse('preop')})
        self.assertRedirects(r_sw, reverse('preop'))
        self.nurse_profile.refresh_from_db()
        self.assertEqual(self.nurse_profile.preop_ui_mode, PREOP_UI_NEW)

        r_new = self.client.get(reverse('preop'))
        self.assertEqual(r_new.status_code, 200)
        self.assertTemplateUsed(r_new, 'mdf/preop/week_new.html')
        self.assertContains(r_new, 'წინასაოპერაციო — ექთნის სივრცე')
        self.assertContains(r_new, 'ძველი ხედი')

        # Other user remains on 'ძველი ხედი' (strictly per-user isolation)
        self.client.force_login(self.other_user)
        r_other = self.client.get(reverse('preop'))
        self.assertTemplateUsed(r_other, 'mdf/preop/week.html')
        self.other_profile.refresh_from_db()
        self.assertEqual(self.other_profile.preop_ui_mode, PREOP_UI_OLD)

        # Unsafe external redirect in `next` is rejected and falls back to /preop/
        self.client.force_login(self.nurse)
        r_bad_next = self.client.post(reverse('preop-ui-mode'), {'mode': 'old', 'next': 'https://evil.example.com/'})
        self.assertRedirects(r_bad_next, reverse('preop'))
        self.nurse_profile.refresh_from_db()
        self.assertEqual(self.nurse_profile.preop_ui_mode, PREOP_UI_OLD)

    def test_ui_mode_session_fallback_without_staff_profile(self):
        self.client.force_login(self.no_profile_user)
        self.assertTemplateUsed(self.client.get(reverse('preop')), 'mdf/preop/week.html')

        self.client.post(reverse('preop-ui-mode'), {'mode': 'new'})
        r_new = self.client.get(reverse('preop'))
        self.assertTemplateUsed(r_new, 'mdf/preop/week_new.html')
        self.assertEqual(get_preop_ui_mode(r_new.wsgi_request), PREOP_UI_NEW)

    def test_handover_queues_and_0300_verdicts(self):
        today = timezone.localdate()
        tomorrow = (today + datetime.timedelta(days=1)).isoformat()
        in_3_days = (today + datetime.timedelta(days=3)).isoformat()

        # 1. Tomorrow surgery + ready patient
        p_ready = PreopPatient.objects.create(
            patient_name='მზად მზადაძე', age=38, sex='F', side='R',
            pathway='venous', op_subtype='evla', visit_date=today,
            questionnaire={'filled': True, 'nurse_verified': True, 'ven_ceap': 'C2', 'mets_ok': True},
            studies={
                'planned_surgery_date': tomorrow, 'hb': 134, 'plt': 230, 'glucose': 5.2,
                'ven_duplex_status': 'deep_patent_reflux', 'compression_ready': True,
            },
        )
        # 2. Med-call due (warfarin 5d hold before surgery in 3 days -> hold already due!)
        p_med = PreopPatient.objects.create(
            patient_name='ვარფარინ დასარეკაძე', age=68, sex='M', side='R',
            pathway='pad_bypass', op_subtype='bypass_clti', visit_date=today,
            questionnaire={'filled': True, 'med_warfarin': True, 'mets_ok': True},
            studies={'planned_surgery_date': in_3_days, 'abi_r': 0.42, 'creat': 85, 'hb': 132},
        )
        # 3. Returned unverified self-fill
        p_sf = PreopPatient.objects.create(
            patient_name='ბმულ შევსებაძე', age=64, sex='M', side='L',
            pathway='cea', op_subtype='cea_asymp', visit_date=today,
            questionnaire={'filled': True, 'self_fill_submitted': True, 'nurse_verified': False, 'mets_ok': True},
            studies={'carotid_l': 80, 'carotid_r': 20},
        )
        # 4. STOP-blocked bilateral CEA
        p_stop = PreopPatient.objects.create(
            patient_name='სტოპ ბლოკაძე', age=70, sex='M', side='B',
            pathway='cea', op_subtype='cea_asymp', visit_date=today,
            questionnaire={'filled': True, 'mets_ok': True},
            studies={'carotid_r': 82, 'carotid_l': 80},
        )

        self.client.force_login(self.nurse)
        self.client.post(reverse('preop-ui-mode'), {'mode': 'new'})
        resp = self.client.get(reverse('preop'))
        self.assertEqual(resp.status_code, 200)
        nq = resp.context['nurse_queues']

        or_ids = [it['p'].pk for it in nq['or_urgent']]
        med_ids = [it['p'].pk for it in nq['med_calls']]
        sf_ids = [it['p'].pk for it in nq['self_returned']]

        self.assertIn(p_ready.pk, or_ids)
        self.assertIn(p_stop.pk, or_ids)
        self.assertIn(p_med.pk, med_ids)
        self.assertIn(p_sf.pk, sf_ids)

        verdicts = {it['p'].pk: it['verdict']['code'] for it in nq['directory']}
        self.assertEqual(verdicts[p_ready.pk], 'ready')
        self.assertEqual(verdicts[p_stop.pk], 'stop')
        self.assertContains(resp, 'მზადაა ✓')
        self.assertContains(resp, 'STOP — დაბლოკილია')
        self.assertContains(resp, 'ვარფარინი')

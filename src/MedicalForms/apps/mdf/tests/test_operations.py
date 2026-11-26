"""Tests for informed consent, duty roster, quotes, and UI refinement batches."""
import io
import shutil
from unittest import skipUnless

from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse

from ..models import NivForm
from ..views import NivDocumentView
from .helpers import BaseViewTest


class ConsentTests(BaseViewTest):
    """Informed consent sheets (NIV-300-12/a)."""

    def docx(self, paragraphs):
        """A tiny .docx with (text, bold) paragraphs."""
        import zipfile
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
        from ..models import ConsentForm
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
        from ..models import ConsentForm
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

        from ..models import ConsentForm
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
        from ..models import ConsentForm
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
        from ..models import ConsentForm
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
        from ..models import ConsentForm
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
        from ..models import ConsentForm
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

        from ..models import ConsentForm
        count = ConsentForm.objects.count()
        r = self.client.post(reverse('consent-new'), {'docx': SimpleUploadedFile('x.docx', b'')}, follow=True)
        self.assertContains(r, 'ცარიელია')
        self.assertEqual(ConsentForm.objects.count(), count)

    @skipUnless(shutil.which('soffice'), 'LibreOffice is needed for .rtf')
    def test_rtf_is_converted(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from ..models import ConsentForm
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
        from ..roster import generate
        rows, _ = generate(y, m, seed)
        return rows

    def save(self, y, m, seed=1, **extra):
        return self.client.post(reverse('roster-generate'), dict(y=y, m=m, seed=seed, **extra))

    def test_seeded_people(self):
        from ..models import RosterPerson
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
        from ..roster import georgian_holidays, is_heavy
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

        from ..models import RosterDay
        from ..roster import georgian_holidays, is_heavy
        for i, (y, m) in enumerate(((2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3), (2027, 4))):
            self.save(y, m, seed=100 + i)
        heavy = Counter()
        for d in RosterDay.objects.select_related('person'):
            if d.person.name != 'მიშა':
                heavy[d.person.name] += is_heavy(d.date, georgian_holidays(d.date.year))
        self.assertLessEqual(max(heavy.values()) - min(heavy.values()), 1, dict(heavy))

    def test_manual_change_is_locked_and_kept(self):
        import datetime

        from ..models import RosterDay, RosterLog, RosterPerson
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

        from ..models import RosterDay
        self.save(2026, 11)
        a = RosterDay.objects.get(date=datetime.date(2026, 11, 3)).person
        b = RosterDay.objects.get(date=datetime.date(2026, 11, 10)).person
        self.client.post(reverse('roster-day', args=['2026-11-03']), {'action': 'swap', 'other': '2026-11-10'})
        self.assertEqual(RosterDay.objects.get(date=datetime.date(2026, 11, 3)).person, b)
        self.assertEqual(RosterDay.objects.get(date=datetime.date(2026, 11, 10)).person, a)

    def test_preview_saves_nothing_until_confirmed(self):
        from ..models import RosterDay
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

        from ..roster import georgian_holidays
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
        from ..models import RosterPerson
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

        from ..models import RosterDay
        for seed in range(1, 21):
            RosterDay.objects.all().delete()
            self.save(2026, 11, seed)
            self.save(2026, 12, seed + 100)
            last_nov = RosterDay.objects.get(date=datetime.date(2026, 11, 30)).person
            first_dec = RosterDay.objects.get(date=datetime.date(2026, 12, 1)).person
            self.assertNotEqual(last_nov, first_dec, seed)

    def test_misha_gap_counts_from_previous_month(self):
        import datetime

        from ..models import RosterDay, RosterPerson
        misha = RosterPerson.objects.get(name='მიშა')
        RosterDay.objects.create(date=datetime.date(2026, 11, 28), person=misha, locked=True, source='manual')
        for seed in range(1, 21):
            from ..roster import generate
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
        from ..roster import generate
        for y, m in [(2026, 11), (2026, 12), (2027, 2), (2027, 5), (2027, 8)]:
            for seed in range(1, 31):
                rows, _ = generate(y, m, seed)
                self.assertTrue(all(r['person'] for r in rows))
                self.assert_apart(rows, f'{y}-{m} seed {seed}')

    def test_never_adjacent_across_the_month_border(self):
        from ..models import RosterDay
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

        from .. import roster
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
                def _count(rr, _hol=hol):
                    return collections.Counter(
                        (r['person'].name, roster.is_heavy(r['date'], _hol)) for r in rr
                    )
                self.assertEqual(_count(rows), _count(normal), f'{y}-{m} seed {seed}')

    def test_impossible_month_relaxes_instead_of_filling_in_turn(self):
        import datetime

        from .. import roster
        from ..models import RosterDay, RosterPerson
        ia = RosterPerson.objects.get(name='ია')
        # many hand-locked days for one doctor: the quotas cannot be kept exactly
        for day in (1, 3, 5, 7, 9, 11, 13, 15):
            RosterDay.objects.create(date=datetime.date(2027, 3, day), person=ia, locked=True, source='manual')
        rows, _ = roster.generate(2027, 3, 5)
        self.assertTrue(all(r['person'] for r in rows))
        for a, b in zip(rows, rows[1:], strict=False):
            self.assertNotEqual(a['person'], b['person'], a['date'])

    def test_rule_is_not_shown_on_the_site(self):
        for url in (reverse('roster') + '?y=2026&m=12', reverse('roster-generate') + '?y=2026&m=12&seed=3'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, 'NEVER_ADJACENT')
            self.assertNotContains(r, 'ერთმანეთის')


class QuoteTests(BaseViewTest):
    """Quote of the day on the prescription page."""

    def setUp(self):
        super().setUp()
        from ..models import Quote
        self.assertEqual(Quote.objects.count(), 50)      # the first 50 come with the site
        Quote.objects.all().delete()

    LINES = ('1 | პირველი ციტატის ტექსტი | ავტორი ერთი | https://example.ge/a\n'
             '2 | „მეორე ციტატის ტექსტი“ | ავტორი ორი | https://example.ge/b\n'
             'ეს სტრიქონი არ გაირჩევა\n'
             '3 | პირველი ციტატის ტექსტი | ავტორი ერთი |\n'
             '| მესამე ციტატა ნომრის გარეშე | ავტორი სამი')

    def test_import_list(self):
        from ..models import Quote
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

        from ..models import Quote
        from ..quotes import quote_of_the_day
        for i in range(5):
            Quote.objects.create(text=f'ციტატა {i}', author='ავტორი')
        day = datetime.date(2026, 11, 22)
        seen = [quote_of_the_day(day + datetime.timedelta(days=k)).pk for k in range(5)]
        self.assertEqual(len(set(seen)), 5)                          # no repeats within 5 days
        self.assertEqual(quote_of_the_day(day).pk, quote_of_the_day(day + datetime.timedelta(days=5)).pk)

    def test_shown_on_prescription_page_only_when_there_are_quotes(self):
        from ..models import Quote
        page = self.client.get(reverse('niv-cover'))
        self.assertNotContains(page, 'დღის ციტატა')
        Quote.objects.create(text='ციტატა <b>ტესტი</b>', author='ავტორი')
        page = self.client.get(reverse('niv-cover'))
        self.assertContains(page, 'დღის ციტატა')
        self.assertContains(page, 'ციტატა &lt;b&gt;ტესტი&lt;/b&gt;')    # escaped
        self.assertContains(page, 'rx-quote d-print-none')

    def test_edit_hide_delete(self):
        from ..models import Quote
        q = Quote.objects.create(text='ძველი', author='ა')
        self.client.post(reverse('quotes'), {'action': 'save', 'id': q.pk, 'text': 'ახალი', 'author': 'ბ'})
        q.refresh_from_db()
        self.assertEqual((q.text, q.author, q.is_active), ('ახალი', 'ბ', False))
        from ..quotes import quote_of_the_day
        self.assertIsNone(quote_of_the_day())                          # hidden ones are not shown
        self.client.post(reverse('quotes'), {'action': 'delete', 'id': q.pk})
        self.assertFalse(Quote.objects.exists())


class RefineBatch1Tests(BaseViewTest):
    """Small fixes found in the code review (each failed before its fix)."""

    def test_roster_people_bad_order_value_is_not_an_error(self):            # A5
        from ..models import RosterPerson
        p = RosterPerson.objects.get(name='ია')
        data = {f'p{x.pk}-{k}': v for x in RosterPerson.objects.all() for k, v in (
            ('name', x.name), ('order', x.order), ('min_gap', x.min_gap), ('active', 'on'))}
        data[f'p{p.pk}-order'] = 'abc'
        r = self.client.post(reverse('roster-people'), data)
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.order, 2)                                          # kept as it was

    def test_board_next_only_to_own_pages(self):                              # A7
        from django.test import RequestFactory

        from ..board import back_to
        rf = RequestFactory()
        for bad in ('/\\evil.com', '//evil.com', 'https://evil.com', '/\\/evil.com'):
            req = rf.post('/', {'next': bad})
            self.assertEqual(back_to(req, '/board/'), '/board/', bad)
        self.assertEqual(back_to(rf.post('/', {'next': '/board/?show=mine'}), '/x/'), '/board/?show=mine')

    def test_consent_import_refuses_huge_document(self):                       # A9
        import zipfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        from ..models import ConsentForm
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('word/document.xml', '<w:document>' + ' ' * (25 * 1024 * 1024) + '</w:document>')
        count = ConsentForm.objects.count()
        r = self.client.post(reverse('consent-new'), {'docx': SimpleUploadedFile('big.docx', buf.getvalue())}, follow=True)
        self.assertEqual(ConsentForm.objects.count(), count)
        self.assertContains(r, 'ძალიან დიდია')

    def test_mark_seen_twice_keeps_one_row(self):                              # A4
        from ..board import mark_seen
        from ..models import BoardPatient, BoardSeen
        p = BoardPatient.objects.create(name='ტესტი')
        self.assertIsNone(mark_seen(self.user, p.pk))
        self.assertIsNotNone(mark_seen(self.user, p.pk))
        self.assertEqual(BoardSeen.objects.filter(user=self.user, patient=p).count(), 1)

    def test_old_reports_purged_once_a_day(self):                              # B2
        import datetime

        from ..board import purge_old_reports
        from ..models import DutyReport
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

        from ..models import RosterDay, RosterPerson
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
        from .. import analysis
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

        from ..models import RosterDay, RosterPerson
        from ..quotes import today_panel
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

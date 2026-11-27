"""Preoperative assessment patient, private file, and clinical rule sign-off models."""
from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.utils import timezone

from . import private_storage
from .cases import Case, _delete_private_files


class PreopPatient(models.Model):
    """A patient scheduled for preoperative workup (AAA, CEA, ABF, PAD Bypass, PTA, Venous)."""

    class Pathway(models.TextChoices):
        AAA_INFRA = 'aaa_infra', 'ინფრარენალური მუცლის აორტის ანევრიზმა'
        CEA = 'cea', 'კაროტიდული ენდარტერექტომია'
        ABF = 'abf', 'აორტო-ბიფემორალური შუნტირება'
        PAD_BYPASS = 'pad_bypass', 'პერიფერიული არტერიული შუნტირება'
        PAD_PTA = 'pad_pta', 'პერიფერიული ანგიოპლასტიკა და სტენტირება (PTA)'
        VENOUS = 'venous', 'ქრონიკული ვენური უკმარისობა (EVLA / ფლებექტომია)'

    class OpSubtype(models.TextChoices):
        OPEN = 'open', 'ღია რეზექცია'
        EVAR = 'evar', 'ენდოვასკულური (EVAR)'
        TBD = 'tbd', 'ტაქტიკა ზუსტდება'
        CEA_SYMP = 'cea_symp', 'სიმპტომური (TIA / ინსულტი ≤6 თვ)'
        CEA_ASYMP = 'cea_asymp', 'ასიმპტომური'
        ABF_CLTI = 'abf_clti', 'კრიტიკული იშემია (CLTI)'
        ABF_CLAUD = 'abf_claud', 'მძიმე კოჭლობა (ლერიში)'
        BYPASS_CLTI = 'bypass_clti', 'შუნტირება — კრიტიკული იშემია (CLTI)'
        BYPASS_CLAUD = 'bypass_claud', 'შუნტირება — მძიმე კოჭლობა'
        PTA_CLTI = 'pta_clti', 'PTA / სტენტირება — კრიტიკული იშემია (CLTI)'
        PTA_CLAUD = 'pta_claud', 'PTA / სტენტირება — ხანგამოშვებითი კოჭლობა'
        EVLA = 'evla', 'ენდოვენური ლაზერული აბლაცია (EVLA)'
        PHLEB = 'phlebectomy', 'ფლებექტომია (კომბინირებული / კროსექტომია)'

    SUBTYPE_PATHWAY = {
        'open': 'aaa_infra',
        'evar': 'aaa_infra',
        'tbd': 'aaa_infra',
        'cea_symp': 'cea',
        'cea_asymp': 'cea',
        'abf_clti': 'abf',
        'abf_claud': 'abf',
        'bypass_clti': 'pad_bypass',
        'bypass_claud': 'pad_bypass',
        'pta_clti': 'pad_pta',
        'pta_claud': 'pad_pta',
        'evla': 'venous',
        'phlebectomy': 'venous',
    }

    class SomedayList(models.TextChoices):
        NONE = '', '—'
        CONF = 'conf', 'კონფერენციიდან ჩასაწერი'
        HOLD = 'hold', 'გადადებული / მკურნალობაზე'
        READY = 'ready', 'მზადაა — ელოდება ოპერაციის თარიღს'

    class CardioDecision(models.TextChoices):
        NONE = '', 'კონსულტაცია ჯერ არ ჩატარებულა'
        CLEARED = 'cleared', 'ოპერაცია ნებადართულია (დამატებითი კვლევის გარეშე)'
        STRESS = 'stress', 'მოითხოვა სტრეს-ტესტი / ჰოლტერი'
        CAG = 'cag', 'მოითხოვა კორონაროგრაფია (CAG / CT-CAG)'
        PCI = 'pci', 'მოითხოვა სტენტირება (PCI) / ოპერაციის გადადება'

    case = models.ForeignKey(
        Case, verbose_name='ქეისი', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='preops',
    )
    patient_name = models.CharField('პაციენტი', max_length=150)
    age = models.PositiveSmallIntegerField('ასაკი', null=True, blank=True)
    sex = models.CharField('სქესი', max_length=1, choices=Case.Sex.choices, blank=True, default='M')
    side = models.CharField('მხარე', max_length=1, choices=Case.Side.choices, blank=True, default='')
    phone = models.CharField('ტელეფონი', max_length=50, blank=True)

    pathway = models.CharField(
        'ნოზოლოგია', max_length=20, choices=Pathway.choices,
        default=Pathway.AAA_INFRA, db_index=True,
    )
    op_subtype = models.CharField(
        'ოპერაციის ტიპი', max_length=20, choices=OpSubtype.choices, default=OpSubtype.OPEN,
    )
    surgeon = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='ქირურგი', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    visit_date = models.DateField('კვლევებზე მოსვლის თარიღი', null=True, blank=True, db_index=True)
    someday_list = models.CharField(
        'უთარიღო სია', max_length=20, choices=SomedayList.choices,
        blank=True, default='', db_index=True,
    )
    started_on = models.DateField('მომზადების ათვლის თარიღი', null=True, blank=True)

    questionnaire = models.JSONField('კითხვარები', default=dict, blank=True)
    studies = models.JSONField('ანალიზები და კვლევები', default=dict, blank=True)

    cardiologist = models.CharField('კარდიოლოგი', max_length=100, blank=True, default='ლიკა ინჯია')
    cardio_decision = models.CharField(
        'კარდიოლოგის გადაწყვეტილება', max_length=20,
        choices=CardioDecision.choices, blank=True, default='',
    )
    cardio_date = models.DateField('კარდიოლოგის თარიღი', null=True, blank=True)
    cardio_note = models.CharField('კარდიოლოგის შენიშვნა', max_length=255, blank=True)

    anamnesis = models.TextField('ანამნეზი', blank=True)
    alerts_ack_at = models.DateTimeField('გაფრთხილება ნანახია', null=True, blank=True)
    alerts_ack_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    approved_at = models.DateTimeField('ექიმის დასტური', null=True, blank=True, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    archived = models.BooleanField('არქივშია', default=False, db_index=True)

    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        db_table = 'preop_patients'
        ordering = ('visit_date', 'id')
        verbose_name = 'წინასაოპერაციო პაციენტი'
        verbose_name_plural = 'წინასაოპერაციო პაციენტები'

    def __str__(self):
        return self.patient_name

    def save(self, *args, **kwargs):
        mapped = self.SUBTYPE_PATHWAY.get(self.op_subtype)
        if mapped:
            self.pathway = mapped
            uf = kwargs.get('update_fields')
            if uf is not None and 'op_subtype' in uf and 'pathway' not in uf:
                kwargs['update_fields'] = list(uf) + ['pathway']
        super().save(*args, **kwargs)

    @property
    def planned_operation_label(self):
        side_s = self.side if isinstance(self.side, str) else ''
        sub_s = self.op_subtype if isinstance(self.op_subtype, str) else ''
        side_lbl = {'R': ' (მარჯვ.)', 'L': ' (მარცხ.)', 'B': ' (ორმხრ.)'}.get(side_s, '')
        short = {
            'open': 'AAA — ღია რეზექცია',
            'evar': 'AAA — EVAR',
            'tbd': 'AAA — ტაქტიკა ზუსტდება',
            'cea_symp': f'CEA{side_lbl} — სიმპტომური',
            'cea_asymp': f'CEA{side_lbl} — ასიმპტომური',
            'abf_clti': 'ABF — კრიტიკული იშემია',
            'abf_claud': 'ABF — ლერიშის სინდრომი',
            'bypass_clti': f'პერიფ. შუნტირება{side_lbl} — CLTI',
            'bypass_claud': f'პერიფ. შუნტირება{side_lbl} — კოჭლობა',
            'pta_clti': f'PTA / სტენტირება{side_lbl} — CLTI',
            'pta_claud': f'PTA / სტენტირება{side_lbl} — კოჭლობა',
            'evla': f'EVLA{side_lbl} — ლაზერული აბლაცია',
            'phlebectomy': f'ფლებექტომია{side_lbl}',
        }
        return short.get(sub_s, self.get_op_subtype_display() if sub_s else '')

    @property
    def diagnosis_text(self):
        from ..preop.utils import _int, is_symp_cea_patient
        s = self.studies if isinstance(self.studies, dict) else {}
        q = self.questionnaire if isinstance(self.questionnaire, dict) else {}
        side_s = self.side if isinstance(self.side, str) else ''
        if self.pathway == self.Pathway.CEA:
            side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა '}.get(side_s, '')
            is_symp = is_symp_cea_patient(self, q=q, s=s, require_filled=False)
            symp_word = 'სიმპტომური ' if is_symp else ''
            raw_pct = s.get('carotid_r') if side_s == 'R' else (
                s.get('carotid_l') if side_s == 'L' else (s.get('carotid_r') or s.get('carotid_l'))
            )
            pct = _int(raw_pct, 0, 100)
            pct_str = f' ({pct}%)' if pct is not None else ''
            return f'{side_word}შიგნითა საძილე არტერიის {symp_word}სტენოზი{pct_str}'
        clti_symp = bool(q.get('filled')) and bool(
            q.get('abf_rest_pain') or q.get('abf_ulcer')
            or q.get('ulcer_chips') or q.get('abf_wet_infection')
        )
        if self.pathway == self.Pathway.ABF:
            is_clti = self.op_subtype == self.OpSubtype.ABF_CLTI or clti_symp
            stage = 'კრიტიკული იშემია (CLTI)' if is_clti else 'ხანგამოშვებითი კოჭლობა'
            return f'აორტო-ილიაკური ოკლუზიური დაავადება (ლერიშის სინდრომი), {stage}'
        if self.pathway in (self.Pathway.PAD_BYPASS, self.Pathway.PAD_PTA):
            side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა ', 'B': 'ორმხრივი '}.get(side_s, '')
            is_clti = self.op_subtype in (self.OpSubtype.BYPASS_CLTI, self.OpSubtype.PTA_CLTI) or clti_symp
            stage = 'კრიტიკული იშემია (CLTI)' if is_clti else 'ხანგამოშვებითი კოჭლობა'
            return f'{side_word}ქვემო კიდურის არტერიების მაობლიტერირებელი ათეროსკლეროზი, {stage}'
        if self.pathway == self.Pathway.VENOUS:
            import re as _re
            side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა ', 'B': 'ორმხრივი '}.get(side_s, '')
            raw_ceap = q.get('ven_ceap') if q.get('filled') else ''
            ceap = raw_ceap.strip() if (isinstance(raw_ceap, str) and _re.match(r'^C[0-6][a-zA-Z]{0,3}$', raw_ceap.strip())) else ''
            ceap_str = f' (CEAP {ceap})' if ceap else ''
            return f'{side_word}ქვემო კიდურების ვენების ვარიკოზული დაავადება{ceap_str}'
        mm = _int(s.get('aaa_mm'), 10, 200)
        base = 'მუცლის აორტის ინფრარენალური ანევრიზმა'
        return f'{base} ({mm} მმ)' if mm is not None else base


class PreopFile(models.Model):
    """An uploaded lab sheet, imaging report, or consultant note stored privately."""

    class Category(models.TextChoices):
        LABS = 'labs', 'ლაბორატორია / ანალიზები'
        CTA = 'cta', 'კტ / მრტ / ანგიოგრაფია'
        CARDIO = 'cardio', 'ეკგ / ექო / კარდიოლოგი'
        DUPLEX = 'duplex', 'დუპლექს-სკანირება / ABI'
        PULM = 'pulm', 'რენტგენი / სპირომეტრია'
        CONSULT = 'consult', 'ნევროლოგი / ლარინგოსკოპია / ანესთეზიოლოგი'

    patient = models.ForeignKey(PreopPatient, on_delete=models.CASCADE, related_name='files')
    category = models.CharField('კატეგორია', max_length=20, choices=Category.choices, default=Category.LABS)
    file = models.FileField(storage=private_storage, upload_to='preop/%Y/%m/')
    thumb = models.FileField(storage=private_storage, upload_to='preop/%Y/%m/', blank=True)
    is_pdf = models.BooleanField(default=False)
    original_name = models.CharField('ფაილი', max_length=200, blank=True)
    caption = models.CharField('წარწერა', max_length=200, blank=True)
    uploaded_on = models.DateField('თარიღი', default=timezone.localdate)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        db_table = 'preop_files'
        ordering = ('-uploaded_on', '-id')
        verbose_name = 'წინასაოპერაციო ფაილი'
        verbose_name_plural = 'წინასაოპერაციო ფაილები'


post_delete.connect(_delete_private_files, sender=PreopFile)


class PreopRuleSignoff(models.Model):
    """Clinician review & sign-off record for local clinical rules and synthetic vignettes."""

    class ItemKind(models.TextChoices):
        RULE = 'rule', 'კლინიკური წესი'
        VIGNETTE = 'vignette', 'კლინიკური ვინიეტა'

    class Verdict(models.TextChoices):
        APPROVED = 'approved', 'დადასტურებულია'
        FLAGGED = 'flagged', 'საჭიროებს გადახედვას'

    item_kind = models.CharField(
        'კატეგორია', max_length=16, choices=ItemKind.choices, default=ItemKind.RULE, db_index=True,
    )
    item_id = models.CharField('იდენტიფიკატორი', max_length=80, db_index=True)
    registry_version = models.CharField('რეესტრის ვერსია', max_length=24, blank=True, default='')
    verdict = models.CharField(
        'სტატუსი', max_length=16, choices=Verdict.choices, default=Verdict.APPROVED,
    )
    note = models.CharField('ექიმის შენიშვნა', max_length=300, blank=True, default='')
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    signed_at = models.DateTimeField('თარიღი', default=timezone.now)

    class Meta:
        db_table = 'preop_rule_signoffs'
        ordering = ('-signed_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('item_kind', 'item_id'),
                name='uniq_preop_rule_signoff_item',
            ),
        ]
        verbose_name = 'წინასაოპერაციო წესის ხელმოწერა'
        verbose_name_plural = 'წინასაოპერაციო წესების ხელმოწერები'

    def __str__(self):
        return f'{self.item_kind}:{self.item_id} ({self.verdict})'

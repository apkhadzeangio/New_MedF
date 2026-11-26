"""Vascular conference case, private photo, and Doppler study models."""
from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from . import private_storage


class Case(models.Model):
    """A patient to be presented at the conference, with photos and the decision."""

    class Status(models.TextChoices):
        OPEN = 'open', 'მოსახსენებელი'
        DECIDED = 'decided', 'გადაწყვეტილი'
        ARCHIVED = 'archived', 'არქივი'

    class Side(models.TextChoices):
        NONE = '', '—'
        LEFT = 'L', 'მარცხენა'
        RIGHT = 'R', 'მარჯვენა'
        BOTH = 'B', 'ორმხრივი'

    class Sex(models.TextChoices):
        NONE = '', '—'
        MALE = 'M', 'კაცი'
        FEMALE = 'F', 'ქალი'

    patient_name = models.CharField('პაციენტი', max_length=150)
    age = models.PositiveSmallIntegerField('ასაკი', null=True, blank=True)
    sex = models.CharField('სქესი', max_length=1, choices=Sex.choices, blank=True, default='')
    side = models.CharField('მხარე', max_length=1, choices=Side.choices, blank=True, default='')
    diagnosis = models.TextField('ძირითადი დიაგნოზი', blank=True)
    comorbidities = models.TextField('თანმხლები დიაგნოზები', blank=True)
    past_history = models.TextField('გადატანილი დაავადებები და ოპერაციები', blank=True)
    history = models.TextField('ანამნეზი და ჩივილები', blank=True)
    findings = models.TextField('კვლევები (დოპლერი, ანგიოგრაფია, ანალიზები)', blank=True)
    proposal = models.TextField('შეთავაზება (რას ვთავაზობთ)', blank=True)
    decision = models.TextField('კონფერენციის გადაწყვეტილება', blank=True)
    status = models.CharField('სტატუსი', max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    conference_on = models.DateField('მოხსენების დღე', null=True, blank=True)
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='მომხსენებელი ექიმი', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # GLASS / WIfI / PLAN per limb: {'R': {...}, 'L': {...}} (see staging package)
    staging = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'cases'
        ordering = ('-created_at', '-id')
        verbose_name = 'ქეისი'
        verbose_name_plural = 'ქეისები'

    def __str__(self):
        return self.patient_name

    @property
    def initials(self):
        """'ბერიძე ნინო' -> 'ბ. ნ.' (for presenting without the full name)."""
        return ' '.join(f'{word[0]}.' for word in self.patient_name.split()[:3]) or '—'


class CasePhoto(models.Model):
    """A photo of a case (wound, angiography screen, Doppler sheet...), stored privately."""

    class Kind(models.TextChoices):
        WOUND = 'wound', 'კიდური / ჭრილობა'
        ANGIO = 'angio', 'ანგიოგრაფია'
        DOPPLER = 'doppler', 'დოპლერი'
        CT = 'ct', 'კტ / მრტ'
        OTHER = 'other', 'სხვა'

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='photos')
    kind = models.CharField('სახე', max_length=10, choices=Kind.choices, default=Kind.WOUND)
    image = models.FileField(storage=private_storage, upload_to='cases/%Y/%m/')
    thumb = models.FileField(storage=private_storage, upload_to='cases/%Y/%m/')
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    caption = models.CharField('წარწერა', max_length=255, blank=True)
    taken_on = models.DateField('თარიღი', default=timezone.localdate)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'case_photos'
        ordering = ('taken_on', 'id')
        verbose_name = 'ქეისის ფოტო'
        verbose_name_plural = 'ქეისის ფოტოები'


class DopplerStudy(models.Model):
    """A Doppler report (Smart-XT PDF) of a case, read into data (see doppler.py)."""
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='dopplers')
    performed_on = models.DateField('კვლევის თარიღი', default=timezone.localdate)
    file = models.FileField(storage=private_storage, upload_to='doppler/%Y/%m/')
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'case_dopplers'
        ordering = ('performed_on', 'id')
        verbose_name = 'დოპლეროგრაფია'
        verbose_name_plural = 'დოპლეროგრაფიები'


@receiver(post_delete, sender=CasePhoto)
@receiver(post_delete, sender=DopplerStudy)
def _delete_private_files(sender, instance, **kwargs):
    """The files go together with the record (also when a whole case is deleted)."""
    for name in ('image', 'thumb', 'file'):
        field = getattr(instance, name, None)
        if field and field.name:
            field.storage.delete(field.name)

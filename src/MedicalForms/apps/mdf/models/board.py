"""Inpatient board (ICU/ward), duty notes, handover reports, and site state models."""
import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone


class DutyNote(models.Model):
    """On-duty doctor's free-text note about one patient ("მორიგე ექიმის ჩანაწერი").

    ``duty_date`` is set once, when the note is created (17:00 and later belong
    to the next day's duty, see apps.mdf.duty.duty_date_for) and never changes
    on later edits. ``created_at`` keeps the real creation time.
    """
    duty_date = models.DateField('მორიგეობის თარიღი', db_index=True, editable=False)
    patient = models.CharField('პაციენტი (სახელი, გვარი, ისტორიის N)', max_length=255, blank=True)
    body = models.TextField('ჩანაწერი', blank=True)
    created_at = models.DateTimeField('შეიქმნა', editable=False)
    updated_at = models.DateTimeField('განახლდა', auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='ავტორი', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='ბოლოს შეცვალა', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        db_table = 'duty_notes'
        ordering = ('duty_date', 'created_at')
        verbose_name = 'მორიგე ექიმის ჩანაწერი'
        verbose_name_plural = 'მორიგე ექიმის ჩანაწერები'

    def __str__(self):
        return f'{self.duty_date:%d.%m.%Y} — {self.patient or "ჩანაწერი"}'

    def save(self, *args, **kwargs):
        if self._state.adding:
            from ..duty import duty_date_for
            if not self.created_at:
                self.created_at = timezone.now()
            if not self.duty_date:
                self.duty_date = duty_date_for(self.created_at)
        super().save(*args, **kwargs)


class BoardPatient(models.Model):
    """A patient currently in the ward or the ICU (until discharged)."""
    ICU = 'icu'
    WARD = 'ward'
    UNITS = [(ICU, 'რეანიმაცია'), (WARD, 'სტაციონარი')]

    unit = models.CharField('განყოფილება', max_length=10, choices=UNITS, default=WARD)
    bed = models.CharField('პალატა / საწოლი', max_length=20, blank=True)
    name = models.CharField('პაციენტი', max_length=150)
    diagnosis = models.CharField('დიაგნოზი', max_length=255, blank=True)
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='მკურნალი ექიმი', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    admitted_at = models.DateTimeField('მიღებულია', default=timezone.now)
    discharged_at = models.DateTimeField('გაწერილია', null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_at = models.DateTimeField('განახლდა', auto_now=True)

    class Meta:
        db_table = 'board_patients'
        ordering = ('unit', 'bed', 'name')
        verbose_name = 'პაციენტი (დაფა)'
        verbose_name_plural = 'პაციენტების დაფა'

    def __str__(self):
        return f'{self.bed} {self.name}'.strip()

    @property
    def is_active(self):
        return self.discharged_at is None


class BoardEntry(models.Model):
    """A text written on a patient: state, task for the duty doctor, comment."""
    STATUS = 'status'
    TASK = 'task'
    COMMENT = 'comment'
    EVENT = 'event'
    KINDS = [
        (STATUS, 'მდგომარეობა'),
        (TASK, 'დავალება მორიგეს'),
        (COMMENT, 'კომენტარი'),
        (EVENT, 'მოძრაობა'),
    ]

    patient = models.ForeignKey(BoardPatient, on_delete=models.CASCADE, related_name='entries')
    kind = models.CharField('ტიპი', max_length=10, choices=KINDS)
    text = models.TextField('ტექსტი')
    created_at = models.DateTimeField('დაიწერა', default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_at = models.DateTimeField('შეიცვალა', null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    done_at = models.DateTimeField('შესრულდა', null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'board_entries'
        ordering = ('-created_at', '-id')
        verbose_name = 'ჩანაწერი (დაფა)'
        verbose_name_plural = 'ჩანაწერები (დაფა)'

    def __str__(self):
        return f'{self.get_kind_display()}: {self.text[:40]}'


class DutyReport(models.Model):
    """Snapshot of one duty: every patient's state. Kept for a month."""
    KEEP_DAYS = 30

    duty_date = models.DateField('მორიგეობა', db_index=True)
    data = models.JSONField('შიგთავსი', default=dict)
    created_at = models.DateTimeField('შეიქმნა', default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'duty_reports'
        ordering = ('-duty_date', '-created_at')
        verbose_name = 'მორიგეობის რეპორტი'
        verbose_name_plural = 'მორიგეობის რეპორტები'

    def __str__(self):
        return f'მორიგეობა {self.duty_date:%d.%m.%Y}'

    @property
    def expires_on(self):
        return self.duty_date + datetime.timedelta(days=self.KEEP_DAYS)


class BoardSeen(models.Model):
    """When a doctor last opened a patient (for the "new" marks on the board)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    patient = models.ForeignKey(BoardPatient, on_delete=models.CASCADE, related_name='+')
    seen_at = models.DateTimeField()

    class Meta:
        db_table = 'board_seen'
        unique_together = ('user', 'patient')


class SiteState(models.Model):
    """Small facts the site remembers (e.g. when the backup was last downloaded)."""
    key = models.CharField(max_length=50, unique=True)
    value = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'site_state'

    @classmethod
    def touch(cls, key, value=''):
        cls.objects.update_or_create(key=key, defaults={'value': value})

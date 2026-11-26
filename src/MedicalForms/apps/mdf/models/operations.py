"""Informed consent (NIV-300-12/a), duty roster, and motivational quote models."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ConsentForm(models.Model):
    """Patient's informed consent to an operation (form NIV-300-12/a).

    Page 1 is the same for every operation. What changes per operation is kept
    here: the name in the list and the complications on page 2 (blocks: a bold
    title and two columns of complications).
    """
    name = models.CharField('სახელი სიაში', max_length=200)
    blocks = models.JSONField('გართულებები', default=list, blank=True)
    use_count = models.PositiveIntegerField('გამოყენებულია', default=0)
    created_at = models.DateTimeField('შეიქმნა', default=timezone.now)
    updated_at = models.DateTimeField('შეიცვალა', auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'consent_forms'
        ordering = ('-use_count', 'name')
        verbose_name = 'თანხმობის ფურცელი'
        verbose_name_plural = 'თანხმობის ფურცლები'

    def __str__(self):
        return self.name


class RosterPerson(models.Model):
    """A doctor of the duty roster: in the rotation, or on fixed days (e.g. 2nd and 4th Friday)."""
    WEEKDAYS = [
        (0, 'ორშაბათი'), (1, 'სამშაბათი'), (2, 'ოთხშაბათი'), (3, 'ხუთშაბათი'),
        (4, 'პარასკევი'), (5, 'შაბათი'), (6, 'კვირა'),
    ]

    name = models.CharField('სახელი', max_length=100)
    order = models.PositiveSmallIntegerField('რიგი', default=0)
    in_rotation = models.BooleanField('რიგით მორიგეობს', default=True)
    fixed_weekday = models.PositiveSmallIntegerField('ფიქსირებული დღე', choices=WEEKDAYS, null=True, blank=True)
    fixed_weeks = models.CharField('რომელი კვირები (მაგ. 2,4)', max_length=20, blank=True)
    per_month = models.PositiveSmallIntegerField('თვეში (შემთხვევით დღეებზე)', null=True, blank=True)
    min_gap = models.PositiveSmallIntegerField('მინიმალური დაშორება (დღე)', default=10)
    is_active = models.BooleanField('აქტიური', default=True)

    class Meta:
        db_table = 'roster_people'
        ordering = ('order', 'id')
        verbose_name = 'მორიგე ექიმი'
        verbose_name_plural = 'მორიგე ექიმები'

    def __str__(self):
        return self.name


class RosterDay(models.Model):
    """Who is on duty on a date. Manual changes are locked: regeneration keeps them."""
    AUTO, FIXED, MANUAL = 'auto', 'fixed', 'manual'

    date = models.DateField('თარიღი', unique=True)
    person = models.ForeignKey(
        RosterPerson, verbose_name='მორიგე', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='days',
    )
    locked = models.BooleanField('ჩაკეტილი', default=False)
    source = models.CharField(max_length=10, default=AUTO)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'roster_days'
        ordering = ('date',)


class RosterLog(models.Model):
    """Every change of the roster: what, who, when."""
    date = models.DateField(null=True, blank=True)
    text = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'roster_log'
        ordering = ('-at', '-id')


class Quote(models.Model):
    """A motivational quote; one is shown on the prescription page each day."""
    text = models.TextField('ციტატა')
    author = models.CharField('ავტორი', max_length=150)
    source = models.URLField('წყარო', max_length=500, blank=True)
    is_active = models.BooleanField('ჩანს', default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'quotes'
        ordering = ('id',)
        verbose_name = 'ციტატა'
        verbose_name_plural = 'ციტატები'

    def __str__(self):
        return f'{self.text[:50]} — {self.author}'

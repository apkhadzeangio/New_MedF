import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import cached_property

from .enums import RadialPulse
from .utils import jsonfield_default_value

# Number of medicine rows on the printed NIV-300-9/a form.
ROW_COUNT = 16


class Medicine(models.Model):
    name = models.CharField(verbose_name='დასახელება', max_length=255)
    note = models.CharField(verbose_name='დანიშნულება', max_length=255, null=True, blank=True)
    is_active = models.BooleanField(verbose_name='აქტიური', default=True)

    class Meta:
        db_table = 'medicines'
        verbose_name = 'წამალი'
        verbose_name_plural = 'წამლები'

    def __str__(self):
        return self.name


class Note(models.Model):
    name = models.CharField(verbose_name='დანიშნულება', max_length=255)
    is_active = models.BooleanField(verbose_name='აქტიური', default=True)

    class Meta:
        db_table = 'notes'
        verbose_name = 'დანიშნულება'
        verbose_name_plural = 'დანიშნულებები'

    def __str__(self):
        return self.name


class NivForm(models.Model):
    title = models.CharField(verbose_name='დასახელება', max_length=255)

    doe = models.CharField(
        verbose_name='შემოსვლის თარიღი', max_length=50, null=True, blank=True
    )
    patient_name = models.CharField(
        verbose_name='პაციენტის სახელი', max_length=150, null=True, blank=True
    )
    dob = models.CharField(
        verbose_name='დაბ. თარიღი', max_length=50, null=True, blank=True
    )
    person_id = models.CharField('პაციენტის პირადი ნომერი', max_length=11, null=True, blank=True)
    department = models.CharField(
        verbose_name='განყოფილება', max_length=50, null=True, blank=True
    )
    card_number = models.CharField(
        verbose_name='ბარათის ნომერი', max_length=50, null=True, blank=True
    )
    chamber_number = models.CharField(
        verbose_name='პალატის ნომერი', max_length=50, null=True, blank=True
    )
    diagnosis = models.CharField(
        verbose_name='დიაგნოზი', max_length=512, null=True, blank=True
    )
    radial_pulse = models.CharField(choices=RadialPulse.choices, max_length=20, null=True, blank=True)
    neuro = models.BooleanField(default=False)
    diabetes = models.BooleanField(default=False)
    allergy = models.BooleanField(default=False)
    mews_score = models.CharField(null=True, blank=True, max_length=11, default='')
    bmi_score = models.CharField(null=True, blank=True, max_length=11, default='')
    gcs_score = models.CharField(null=True, blank=True, max_length=11, default='')
    risk_falling = models.CharField(null=True, blank=True, max_length=11, default='')

    day1 = models.CharField(
        verbose_name='დღე 1', max_length=15, null=True, blank=True
    )
    day2 = models.CharField(
        verbose_name='დღე 2', max_length=15, null=True, blank=True
    )
    day3 = models.CharField(
        verbose_name='დღე 3', max_length=15, null=True, blank=True
    )

    m1 = models.CharField(
        verbose_name='მედიკამენტი 1', max_length=512, null=True, blank=True
    )
    n1 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r1 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m2 = models.CharField(
        verbose_name='მედიკამენტი 2', max_length=512, null=True, blank=True
    )
    n2 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r2 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m3 = models.CharField(
        verbose_name='მედიკამენტი 3', max_length=512, null=True, blank=True
    )
    n3 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r3 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m4 = models.CharField(
        verbose_name='მედიკამენტი 4', max_length=512, null=True, blank=True
    )
    n4 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r4 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m5 = models.CharField(
        verbose_name='მედიკამენტი 5', max_length=512, null=True, blank=True
    )
    n5 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r5 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m6 = models.CharField(
        verbose_name='მედიკამენტი 6', max_length=512, null=True, blank=True
    )
    n6 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r6 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m7 = models.CharField(
        verbose_name='მედიკამენტი 7', max_length=512, null=True, blank=True
    )
    n7 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r7 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m8 = models.CharField(
        verbose_name='მედიკამენტი 8', max_length=512, null=True, blank=True
    )
    n8 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r8 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m9 = models.CharField(
        verbose_name='მედიკამენტი 9', max_length=512, null=True, blank=True
    )
    n9 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r9 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m10 = models.CharField(
        verbose_name='მედიკამენტი 10', max_length=512, null=True, blank=True
    )
    n10 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r10 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m11 = models.CharField(
        verbose_name='მედიკამენტი 11', max_length=512, null=True, blank=True
    )
    n11 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r11 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m12 = models.CharField(
        verbose_name='მედიკამენტი 12', max_length=512, null=True, blank=True
    )
    n12 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r12 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m13 = models.CharField(
        verbose_name='მედიკამენტი 13', max_length=512, null=True, blank=True
    )
    n13 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r13 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m14 = models.CharField(
        verbose_name='მედიკამენტი 14', max_length=512, null=True, blank=True
    )
    n14 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r14 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m15 = models.CharField(
        verbose_name='მედიკამენტი 15', max_length=512, null=True, blank=True
    )
    n15 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r15 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )
    m16 = models.CharField(
        verbose_name='მედიკამენტი 16', max_length=512, null=True, blank=True
    )
    n16 = models.CharField(
        verbose_name='დანიშნულება', max_length=512, null=True, blank=True
    )
    r16 = models.JSONField(
        verbose_name='დღეები, საათები', null=True, blank=True,
        default=jsonfield_default_value
    )

    # when / by whom it was last saved (warns when two doctors edit at once; not printed)
    updated_at = models.DateTimeField('ბოლოს შეცვლილია', auto_now=True, null=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='ბოლოს შეცვალა', null=True, blank=True,
                                   editable=False, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'niv_forms'
        verbose_name = 'დანიშნულება (NIV-300-9/ა)'
        verbose_name_plural = 'დანიშნულებები (NIV-300-9/ა)'

    def __str__(self):
        return self.title

    @property
    def version(self):
        """Changes on every save; the page sends it back to detect a parallel edit."""
        return f'{self.updated_at.timestamp():.6f}' if self.updated_at else '0'

    @cached_property
    def rows_as_string(self):
        rows = list()
        for i in range(1, ROW_COUNT + 1):
            m_x = self.serializable_value(f'm{i}')
            n_x = self.serializable_value(f'n{i}')
            if m_x:
                rows.append(m_x)
            if n_x:
                rows.append(n_x)

        return "\n".join(rows)


    def rows_data(self):
        """Medicine rows 1..16 as [{'m': .., 'n': .., 'r': {'d1': {..}, ..}}]."""
        rows = []
        for i in range(1, ROW_COUNT + 1):
            r = getattr(self, f'r{i}') or {}
            rows.append({
                'm': getattr(self, f'm{i}') or '',
                'n': getattr(self, f'n{i}') or '',
                'r': {d: dict(r.get(d) or {}) for d in ('d1', 'd2', 'd3')},
            })
        return rows


def medicine_key(names):
    """Order-independent key of a set of medicine names (for grouping)."""
    return tuple(sorted({' '.join(n.lower().split()) for n in names if n and n.strip()}))


class StaffProfile(models.Model):
    """A person shown on the login page (photo + name)."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile'
    )
    display_name = models.CharField('სახელი', max_length=50)
    full_name = models.CharField(
        'სახელი და გვარი (ბლანკებისთვის)', max_length=150, blank=True,
        help_text='მაგ. შეროზია ინა — იწერება სისხლის შეკვეთის ბლანკზე „ექიმი“-ს ველში.')
    photo = models.ImageField('ფოტო', upload_to='staff/', blank=True)
    order = models.PositiveSmallIntegerField('რიგითობა', default=0)
    show_on_login = models.BooleanField('ჩანს შესვლის გვერდზე', default=True)

    class Meta:
        db_table = 'staff_profiles'
        ordering = ('order', 'display_name')
        verbose_name = 'თანამშრომელი'
        verbose_name_plural = 'თანამშრომლები'

    def __str__(self):
        return self.display_name

    @property
    def doctor_name(self):
        return self.full_name or self.user.get_full_name() or self.display_name

    @property
    def initial(self):
        # no .upper(): it turns Georgian letters into Mtavruli (Ი instead of ი)
        return (self.display_name or self.user.username)[:1]

    @property
    def avatar_url(self):
        """Uploaded photo, else static/img/staff/<username>.png, else None."""
        if self.photo:
            return self.photo.url
        from .avatars import staff_avatar_url
        return staff_avatar_url(self.user.username)


class PrescriptionTemplate(models.Model):
    """Reusable set of medicines + hours (e.g. "ანევრიზმა", "ლერიში")."""
    name = models.CharField('შაბლონის სახელი', max_length=150)
    rows = models.JSONField('მედიკამენტები', default=list, blank=True)
    use_count = models.PositiveIntegerField('გამოყენებულია', default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='შექმნა', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    created_at = models.DateTimeField('შეიქმნა', auto_now_add=True)
    updated_at = models.DateTimeField('განახლდა', auto_now=True)

    class Meta:
        db_table = 'prescription_templates'
        ordering = ('name',)
        verbose_name = 'შაბლონი'
        verbose_name_plural = 'შაბლონები'

    def __str__(self):
        return self.name

    @property
    def medicines(self):
        return [row.get('m') for row in self.rows if row.get('m')]

    @property
    def medicine_key(self):
        return medicine_key(self.medicines)


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
        on_delete=models.SET_NULL, related_name='+')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='ბოლოს შეცვალა', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'duty_notes'
        ordering = ('duty_date', 'created_at')
        verbose_name = 'მორიგე ექიმის ჩანაწერი'
        verbose_name_plural = 'მორიგე ექიმის ჩანაწერები'

    def __str__(self):
        return f'{self.duty_date:%d.%m.%Y} — {self.patient or "ჩანაწერი"}'

    def save(self, *args, **kwargs):
        if self._state.adding:
            from django.utils import timezone
            from .duty import duty_date_for
            if not self.created_at:
                self.created_at = timezone.now()
            if not self.duty_date:
                self.duty_date = duty_date_for(self.created_at)
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------
# Patients board ("პაციენტების დაფა"): the ward whiteboard, digitised
# --------------------------------------------------------------------------

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
        null=True, blank=True, related_name='+')
    admitted_at = models.DateTimeField('მიღებულია', default=timezone.now)
    discharged_at = models.DateTimeField('გაწერილია', null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
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
    KINDS = [(STATUS, 'მდგომარეობა'), (TASK, 'დავალება მორიგეს'),
             (COMMENT, 'კომენტარი'), (EVENT, 'მოძრაობა')]

    patient = models.ForeignKey(BoardPatient, on_delete=models.CASCADE, related_name='entries')
    kind = models.CharField('ტიპი', max_length=10, choices=KINDS)
    text = models.TextField('ტექსტი')
    created_at = models.DateTimeField('დაიწერა', default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField('შეიცვალა', null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    done_at = models.DateTimeField('შესრულდა', null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

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
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'duty_reports'
        ordering = ('-duty_date', '-created_at')
        verbose_name = 'მორიგეობის რეპორტი'
        verbose_name_plural = 'მორიგეობის რეპორტები'

    def __str__(self):
        return f'მორიგეობა {self.duty_date:%d.%m.%Y}'

    @property
    def expires_on(self):
        import datetime
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
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'consent_forms'
        ordering = ('-use_count', 'name')
        verbose_name = 'თანხმობის ფურცელი'
        verbose_name_plural = 'თანხმობის ფურცლები'

    def __str__(self):
        return self.name


# --------------------------------------------------------------------------
# Duty roster ("მორიგეობის განრიგი"): one surgeon on duty per day
# --------------------------------------------------------------------------

class RosterPerson(models.Model):
    """A doctor of the duty roster: in the rotation, or on fixed days (e.g. 2nd and 4th Friday)."""
    WEEKDAYS = [(0, 'ორშაბათი'), (1, 'სამშაბათი'), (2, 'ოთხშაბათი'), (3, 'ხუთშაბათი'),
                (4, 'პარასკევი'), (5, 'შაბათი'), (6, 'კვირა')]

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
    person = models.ForeignKey(RosterPerson, verbose_name='მორიგე', on_delete=models.SET_NULL,
                               null=True, blank=True, related_name='days')
    locked = models.BooleanField('ჩაკეტილი', default=False)
    source = models.CharField(max_length=10, default=AUTO)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')

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


# --------------------------------------------------------------------------
# Cases for the vascular conference (after angiography: which operation)
# --------------------------------------------------------------------------

class PrivateStorage(FileSystemStorage):
    """Patient photos: a folder that no web server publishes (shown only through a login view).

    The folder is read from settings on every use, so tests can point it elsewhere.
    """

    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)


def private_storage():
    return PrivateStorage()


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
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='მომხსენებელი ექიმი', null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # GLASS / WIfI / PLAN per limb: {'R': {...}, 'L': {...}} (see staging.py)
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


# --------------------------------------------------------------------------
# Preoperative assessment ("წინასაოპერაციო"): Tweek-style calendar + nurse POA
# --------------------------------------------------------------------------

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
        on_delete=models.SET_NULL, related_name='preops'
    )
    patient_name = models.CharField('პაციენტი', max_length=150)
    age = models.PositiveSmallIntegerField('ასაკი', null=True, blank=True)
    sex = models.CharField('სქესი', max_length=1, choices=Case.Sex.choices, blank=True, default='M')
    side = models.CharField('მხარე', max_length=1, choices=Case.Side.choices, blank=True, default='')
    phone = models.CharField('ტელეფონი', max_length=50, blank=True)

    pathway = models.CharField(
        'ნოზოლოგია', max_length=20, choices=Pathway.choices,
        default=Pathway.AAA_INFRA, db_index=True
    )
    op_subtype = models.CharField(
        'ოპერაციის ტიპი', max_length=20, choices=OpSubtype.choices, default=OpSubtype.OPEN
    )
    surgeon = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='ქირურგი', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )

    visit_date = models.DateField('კვლევებზე მოსვლის თარიღი', null=True, blank=True, db_index=True)
    someday_list = models.CharField(
        'უთარიღო სია', max_length=20, choices=SomedayList.choices,
        blank=True, default='', db_index=True
    )
    started_on = models.DateField('მომზადების ათვლის თარიღი', null=True, blank=True)

    questionnaire = models.JSONField('კითხვარები', default=dict, blank=True)
    studies = models.JSONField('ანალიზები და კვლევები', default=dict, blank=True)

    cardiologist = models.CharField('კარდიოლოგი', max_length=100, blank=True, default='ლიკა ინჯია')
    cardio_decision = models.CharField(
        'კარდიოლოგის გადაწყვეტილება', max_length=20,
        choices=CardioDecision.choices, blank=True, default=''
    )
    cardio_date = models.DateField('კარდიოლოგის თარიღი', null=True, blank=True)
    cardio_note = models.CharField('კარდიოლოგის შენიშვნა', max_length=255, blank=True)

    anamnesis = models.TextField('ანამნეზი', blank=True)
    alerts_ack_at = models.DateTimeField('გაფრთხილება ნანახია', null=True, blank=True)
    alerts_ack_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    approved_at = models.DateTimeField('ექიმის დასტური', null=True, blank=True, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    archived = models.BooleanField('არქივშია', default=False, db_index=True)

    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
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
        side_lbl = {'R': ' (მარჯვ.)', 'L': ' (მარცხ.)', 'B': ' (ორმხრ.)'}.get(self.side, '')
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
        return short.get(self.op_subtype, self.get_op_subtype_display())

    @property
    def diagnosis_text(self):
        s = self.studies or {}
        if self.pathway == self.Pathway.CEA:
            side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა '}.get(self.side, '')
            symp_word = 'სიმპტომური ' if self.op_subtype == self.OpSubtype.CEA_SYMP else ''
            pct = s.get('carotid_r') if self.side == 'R' else (
                s.get('carotid_l') if self.side == 'L' else (s.get('carotid_r') or s.get('carotid_l'))
            )
            pct_str = f' ({pct}%)' if pct is not None and pct != '' else ''
            return f'{side_word}შიგნითა საძილე არტერიის {symp_word}სტენოზი{pct_str}'
        if self.pathway == self.Pathway.ABF:
            stage = 'კრიტიკული იშემია (CLTI)' if self.op_subtype == self.OpSubtype.ABF_CLTI else 'ხანგამოშვებითი კოჭლობა'
            return f'აორტო-ილიაკური ოკლუზიური დაავადება (ლერიშის სინდრომი), {stage}'
        if self.pathway in (self.Pathway.PAD_BYPASS, self.Pathway.PAD_PTA):
            side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა ', 'B': 'ორმხრივი '}.get(self.side, '')
            is_clti = self.op_subtype in (self.OpSubtype.BYPASS_CLTI, self.OpSubtype.PTA_CLTI)
            stage = 'კრიტიკული იშემია (CLTI)' if is_clti else 'ხანგამოშვებითი კოჭლობა'
            return f'{side_word}ქვემო კიდურის არტერიების მაობლიტერირებელი ათეროსკლეროზი, {stage}'
        if self.pathway == self.Pathway.VENOUS:
            side_word = {'R': 'მარჯვენა ', 'L': 'მარცხენა ', 'B': 'ორმხრივი '}.get(self.side, '')
            ceap = (self.questionnaire or {}).get('ven_ceap') or ''
            ceap_str = f' (CEAP {ceap})' if ceap else ''
            return f'{side_word}ქვემო კიდურების ვენების ვარიკოზული დაავადება{ceap_str}'
        mm = s.get('aaa_mm')
        base = 'მუცლის აორტის ინფრარენალური ანევრიზმა'
        return f'{base} ({mm} მმ)' if mm else base


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
        on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        db_table = 'preop_files'
        ordering = ('-uploaded_on', '-id')
        verbose_name = 'წინასაოპერაციო ფაილი'
        verbose_name_plural = 'წინასაოპერაციო ფაილები'


post_delete.connect(_delete_private_files, sender=PreopFile)
from .preop_models import PreopRuleSignoff  # noqa: E402,F401

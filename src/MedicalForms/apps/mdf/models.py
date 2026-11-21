from django.conf import settings
from django.db import models
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

    class Meta:
        db_table = 'niv_forms'
        verbose_name = 'დანიშნულება (NIV-300-9/ა)'
        verbose_name_plural = 'დანიშნულებები (NIV-300-9/ა)'

    def __str__(self):
        return self.title

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

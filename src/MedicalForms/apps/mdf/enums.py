import enum
from django.db import models

class Symbols(enum.Enum):
    CHECKED = '🗹'
    UNCHECKED = '□'
    LONG_DASH = '—'
    LARGE_PLUS = '➕'
    PLUS = '+'


class RadialPulse(models.TextChoices):
    REGULAR = 'რეგულარული', 'რეგულარული'
    NON_REGULAR = 'არარეგულარული', 'არარეგულარული'
    WEAK = 'სუსტი', 'სუსტი'

class YesNo(models.TextChoices):
    YES = 'დიახ'
    NO = 'არა'

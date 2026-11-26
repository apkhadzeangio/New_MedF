"""Additive models supporting Track A clinical reliability governance."""
from django.conf import settings
from django.db import models
from django.utils import timezone


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

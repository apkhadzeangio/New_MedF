"""Duplicate medicine names ("Aspirin 100mg" / "aspirin 100 mg").

Prescriptions keep the medicine name as text, so nothing printed changes:
merging only hides the extra spellings from the autocomplete list
(is_active = False). They can be shown again in the admin.
"""
import re
from collections import Counter

from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views import generic

from .models import ROW_COUNT, Medicine, NivForm

_JUNK = re.compile(r'[\s.,;:/\\\-_()\[\]"\'«»„“”+*]+')


def name_key(name):
    """Spelling-insensitive key: case, spaces and punctuation do not matter."""
    return _JUNK.sub('', (name or '').lower())


def usage_counts():
    """How many prescriptions use each exact medicine name."""
    fields = [f'm{i}' for i in range(1, ROW_COUNT + 1)]
    counts = Counter()
    for row in NivForm.objects.values_list(*fields):
        counts.update({(v or '').strip() for v in row if v and v.strip()})
    return counts


def duplicate_groups():
    groups = {}
    for med in Medicine.objects.filter(is_active=True).order_by('name', 'id'):
        key = name_key(med.name)
        if key:
            groups.setdefault(key, []).append(med)
    groups = [meds for meds in groups.values() if len(meds) > 1]
    if groups:
        used = usage_counts()
        for meds in groups:
            for med in meds:
                med.used = used.get(med.name.strip(), 0)
            meds.sort(key=lambda m: (-m.used, -len(m.note or ''), m.id))   # best guess first
    return sorted(groups, key=lambda meds: meds[0].name.lower())


class StaffOnly(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff


class DuplicatesView(StaffOnly, generic.TemplateView):
    template_name = 'mdf/medicine_duplicates.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['groups'] = duplicate_groups()
        return ctx

    def post(self, request, *args, **kwargs):
        hidden = 0
        for key, value in request.POST.items():
            if not key.startswith('keep-'):
                continue
            ids = [int(x) for x in key[5:].split('-') if x.isdigit()]
            try:
                keep = int(value)
            except ValueError:
                continue
            if keep not in ids:
                continue
            hidden += Medicine.objects.filter(id__in=ids, is_active=True).exclude(id=keep).update(is_active=False)
        if hidden:
            messages.success(request, f'დამალულია {hidden} ზედმეტი ჩანაწერი. დანიშნულებები არ შეცვლილა.')
        else:
            messages.info(request, 'არაფერი შეცვლილა.')
        return HttpResponseRedirect(reverse('medicine-duplicates'))

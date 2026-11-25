"""Printable 2-page A4 Preoperative Passport view."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import generic

from ...board import person_name
from ...models import PreopPatient
from ..anamnesis import generate_anamnesis


class PreopPassportView(LoginRequiredMixin, generic.TemplateView):
    """1-Page A4 Printable Preoperative Passport ('წინასაოპერაციო პასპორტი') for the patient chart."""
    template_name = 'mdf/preop/passport.html'

    def get_context_data(self, **kwargs):
        from .. import evaluate

        ctx = super().get_context_data(**kwargs)
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by'),
            pk=kwargs['pk'],
        )
        ev = evaluate(patient)
        auto_anam = generate_anamnesis(patient, ev)
        ctx.update({
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'shown_anamnesis': patient.anamnesis or auto_anam,
            'surgeon_name': person_name(patient.surgeon),
            'approved_by_name': person_name(patient.approved_by) if patient.approved_by else '',
            'printed_on': timezone.localtime().strftime('%d.%m.%Y %H:%M'),
        })
        return ctx

"""Blood order (სისხლის შეკვეთა): fillable form, printed as an A4 half-page
(templates/mdf/blood-print.html, printed from the browser).

Orders are not stored in the database: the page is filled (from a
prescription or empty) and printed as PDF.
"""
from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views import generic

from .mixins import GetObjectMixin
from .models import StaffProfile

BLOOD_GROUPS = [
    'O (I) Rh (+)', 'O (I) Rh (-)', 'A (II) Rh (+)', 'A (II) Rh (-)',
    'B (III) Rh (+)', 'B (III) Rh (-)', 'AB (IV) Rh (+)', 'AB (IV) Rh (-)',
]

DEFAULTS = {
    'indication': 'ოპერაცია',
    'receiver': 'ტრანსფუზიოლოგია',
    'sender': 'ანგიოლოგიური სტაციონარი',
}


class BloodOrderForm(forms.Form):
    patient_name = forms.CharField(label='პაციენტი (გვარი, სახელი)', max_length=150, required=False)
    person_id = forms.CharField(label='პირადი N', max_length=20, required=False)
    dob = forms.CharField(label='დაბადების თარიღი', max_length=20, required=False)
    blood_group = forms.CharField(label='ჯგუფი და რეზუს კუთვნილება', max_length=30, required=False)
    diagnosis = forms.CharField(label='დიაგნოზი', max_length=512, required=False)
    card_number = forms.CharField(label='ისტორიის #', max_length=50, required=False)
    phone = forms.CharField(label='პაციენტის ტელ. ნომერი', max_length=30, required=False)
    indication = forms.CharField(label='გადასხმის ჩვენება', max_length=200, required=False)
    rbc = forms.CharField(label='ერით მასა (დოზა)', max_length=20, required=False)
    cryo = forms.CharField(label='კრიოპლაზმა (დოზა)', max_length=20, required=False)
    platelets = forms.CharField(label='თრომბოც მასა (დოზა)', max_length=20, required=False)
    receiver = forms.CharField(label='მიმღები დაწესებულება', max_length=150, required=False)
    sender = forms.CharField(label='გამომგზავნი განყოფილება', max_length=150, required=False)
    doctor = forms.CharField(label='ექიმი', max_length=150, required=False)
    nurse = forms.CharField(label='მედდა', max_length=150, required=False)
    date = forms.CharField(label='თარიღი', max_length=10, required=False)
    time = forms.CharField(label='დრო', max_length=5, required=False)


def initial_values(request, niv=None):
    now = timezone.localtime()
    values = dict(DEFAULTS, date=now.strftime('%d.%m.%Y'), time=now.strftime('%H:%M'))
    profile = StaffProfile.objects.filter(user=request.user).first()
    values['doctor'] = profile.doctor_name if profile else (
        request.user.get_full_name() or request.user.username)
    if niv:
        values.update({
            'patient_name': niv.patient_name or '',
            'person_id': niv.person_id or '',
            'dob': niv.dob or '',
            'diagnosis': niv.diagnosis or '',
            'card_number': niv.card_number or '',
        })
    return values


def print_context(data):
    """Values for the printed blank."""
    ctx = {key: (data.get(key) or '').strip() for key in BloodOrderForm.base_fields}
    day = month = ''
    year = str(timezone.localtime().year)
    parts = ctx['date'].replace('/', '.').replace('-', '.').split('.')
    if len(parts) == 3 and all(p.strip() for p in parts):
        day, month, year = (p.strip() for p in parts)
    ctx.update(day=day, month=month, year=year)
    return ctx


def board_patient(request):
    """The board patient of ?board=<id>, or None."""
    from .models import BoardPatient
    try:
        return BoardPatient.objects.get(pk=int(request.GET.get('board', '')))
    except (TypeError, ValueError, BoardPatient.DoesNotExist):
        return None


class BloodOrderView(LoginRequiredMixin, GetObjectMixin, generic.FormView):
    """The fillable page: empty (menu) or filled from a prescription (?t=id)."""
    template_name = 'mdf/blood-order.html'
    form_class = BloodOrderForm

    def get_initial(self):
        values = initial_values(self.request, self.get_object())
        if not self.get_object() and self.request.GET.get('preop', '').isdigit():
            from .models import PreopPatient
            from .preop import _clean_person_id, _fmt_ka_date
            pre = PreopPatient.objects.filter(pk=int(self.request.GET['preop'])).first()
            if pre:
                s = pre.studies if isinstance(pre.studies, dict) else {}
                values.update(
                    patient_name=pre.patient_name,
                    person_id=_clean_person_id(s.get('person_id')),
                    dob=_fmt_ka_date(s.get('birth_date')),
                    diagnosis=pre.diagnosis_text,
                    blood_group=s.get('blood_group') or '',
                    phone=pre.phone or '',
                )
                return values
        board = board_patient(self.request)
        if board and not self.get_object():
            from .preop import _clean_person_id, _fmt_ka_date, resolve_preop_for_board
            pre = resolve_preop_for_board(board)
            if pre:
                s = pre.studies if isinstance(pre.studies, dict) else {}
                values.update(
                    patient_name=board.name,
                    person_id=_clean_person_id(s.get('person_id')),
                    dob=_fmt_ka_date(s.get('birth_date')),
                    diagnosis=board.diagnosis or pre.diagnosis_text,
                    blood_group=s.get('blood_group') or '',
                    phone=pre.phone or '',
                )
            else:
                values.update(patient_name=board.name, diagnosis=board.diagnosis)
        return values

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['niv'] = self.get_object()
        ctx['blood_groups'] = BLOOD_GROUPS
        return ctx


class BloodOrderPrintView(LoginRequiredMixin, generic.TemplateView):
    """POST the form -> the printable blank (opens the print dialog)."""
    template_name = 'mdf/blood-print.html'
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        form = BloodOrderForm(request.POST)
        form.is_valid()
        self._remember_doctor_name(form.cleaned_data.get('doctor', ''))
        return self.render_to_response({'d': print_context(form.cleaned_data)})

    def _remember_doctor_name(self, name):
        # First time a doctor prints, keep the typed full name for next time.
        name = (name or '').strip()
        profile = StaffProfile.objects.filter(user=self.request.user).first()
        if profile and name and not profile.full_name and name != profile.display_name:
            profile.full_name = name[:150]
            profile.save(update_fields=['full_name'])

"""Private preoperative file upload, authenticated viewing, and deletion endpoints."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ...cases import PhotoError
from ...models import PreopFile, PreopPatient
from ..forms import save_preop_file
from ..utils import _date


class PreopFileUploadView(LoginRequiredMixin, generic.View):
    """Upload one or more photos or PDF reports to a preoperative patient card."""

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        category = request.POST.get('category') or PreopFile.Category.LABS
        caption = request.POST.get('caption') or ''
        uploaded_on = _date(request.POST.get('uploaded_on'))
        if uploaded_on and uploaded_on > timezone.localdate():
            uploaded_on = None

        files = request.FILES.getlist('file')
        added, errors = 0, []
        for f in files[:20]:
            try:
                save_preop_file(patient, f, category, caption, request.user, uploaded_on)
                added += 1
            except PhotoError as e:
                errors.append(f'{f.name}: {e}')

        for err in errors:
            messages.error(request, err)
        if added:
            messages.success(request, f'დაემატა {added} ფაილი.')
        elif not errors:
            messages.error(request, 'ფაილი არ არის არჩეული.')
        tab = request.POST.get('tab') or 's1'
        if tab not in ('q', 's1', 's2', 'doc', 'dos'):
            tab = 's1'
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f'?tab={tab}#files')


class PreopFileView(LoginRequiredMixin, generic.View):
    """Serve a private preoperative photo or PDF to authenticated clinic staff only."""

    def get(self, request, pk, size='full'):
        rec = get_object_or_404(PreopFile, pk=pk)
        if size == 'thumb' and not rec.is_pdf and rec.thumb:
            field = rec.thumb
            ctype = 'image/jpeg'
            ext = 'jpg'
        else:
            field = rec.file
            ctype = 'application/pdf' if rec.is_pdf else 'image/jpeg'
            ext = 'pdf' if rec.is_pdf else 'jpg'
        try:
            fh = field.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404
        response = FileResponse(fh, content_type=ctype)
        response['Cache-Control'] = 'private, max-age=604800'
        response['Content-Disposition'] = f'inline; filename="preop-file-{rec.pk}.{ext}"'
        if ctype == 'application/pdf':
            response['Content-Security-Policy'] = "default-src 'none'; object-src 'self'; frame-ancestors 'self'"
        else:
            response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; sandbox"
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PreopFileDeleteView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        from ..utils import merged_studies
        from .parsers import is_doctor_user

        rec = get_object_or_404(PreopFile.objects.select_related('patient'), pk=pk)
        patient = rec.patient
        s_del = merged_studies(patient)
        is_audit_protected = (
            patient.archived
            or bool(patient.approved_at)
            or s_del.get('case_outcome_status') == 'operated'
            or bool(s_del.get('board_patient_id'))
        )
        if is_audit_protected and not (request.user.is_superuser or request.user.is_staff):
            messages.error(request, 'დადასტურებული, ნაოპერაციები, დაფაზე გადაყვანილი ან არქივირებული პაციენტის დოკუმენტის წაშლა შეზღუდულია.')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s1#files')
        if not is_doctor_user(request.user) and rec.created_by_id != request.user.pk:
            messages.error(request, 'სხვა თანამშრომლის ატვირთული ფაილის წაშლა შეუძლია მხოლოდ ექიმს ან ადმინისტრატორს.')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s1#files')
        patient_id = rec.patient_id
        rec.delete()
        messages.success(request, 'ფაილი წაიშალა.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient_id]) + '?tab=s1#files')

import json
from typing import Any

from django.contrib.auth import logout, authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import serializers
from django.core.paginator import Paginator
from django.db.models import Q
from django.forms import model_to_dict
from django.http import JsonResponse, HttpResponseRedirect, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views import generic
from django_docxtpl import DocxTemplateResponse, DocxTemplateView

from .backends import ModelFilterBackend
from .enums import Symbols, YesNo, RadialPulse
from .forms import LoginForm, NivFormFrom
from .mixins import LoginRedirectMixin, GetObjectMixin, CsrfExemptMixin
from .models import Medicine, Note, NivForm
from .utils import validate_take_medicine


class LoginView(LoginRedirectMixin, generic.FormView):
    template_name = 'mdf/login.html'
    form_class = LoginForm
    success_url = reverse_lazy('niv-cover')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['next'] = self.request.GET.get('next', '')
        return ctx

    def form_valid(self, form):
        user = authenticate(
            self.request,
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password']
        )
        if user:
            login(self.request, user)
            next_page = form.cleaned_data['next']
            if next_page and next_page != '/users/logout/':
                return HttpResponseRedirect(next_page)
        else:
            message = 'username or password is incorrect.'
            return self.render_to_response({'message': message})

        return super().form_valid(form)


class LogoutView(LoginRequiredMixin, generic.View):

    def get(self, request, *args, **kwargs):
        logout(request)
        return HttpResponseRedirect('/')


class NivFormCoverView(LoginRequiredMixin,
                       GetObjectMixin,
                       generic.TemplateView):
    template_name = 'mdf/niv.html'

    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['niv'] = self.get_object()
        if not ctx['niv']:
            ctx['niv'] = self.get_object(kwarg='as')
            ctx['can_update'] = False
        else:
            ctx['can_update'] = True
        return ctx


class AssignmentSheetView(LoginRequiredMixin,
                          GetObjectMixin,
                          generic.TemplateView):
    template_name = 'mdf/assignment_sheet.html'

    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['niv'] = self.get_object()
        if not ctx['niv']:
            ctx['niv'] = self.get_object(kwarg='as')
            ctx['can_update'] = False
        else:
            ctx['can_update'] = True
        return ctx


class NivFormBackView(NivFormCoverView):
    template_name = 'mdf/niv-back.html'


class PrintNivFormCoverView(NivFormCoverView):
    template_name = 'mdf/print-niv.html'


class PrintNivFormBackView(NivFormBackView):
    template_name = 'mdf/print-niv-back.html'


class BaseAPIView(LoginRequiredMixin, CsrfExemptMixin, generic.ListView):

    def post(self, request, *args, **kwargs):
        qs = ModelFilterBackend.filter_queryset(request, self.queryset)
        return JsonResponse({
            'results': list(qs.values_list('name', flat=True))
        })

    def get(self, request, *args, **kwargs):
        qs = ModelFilterBackend.filter_queryset(request, self.queryset)
        return JsonResponse(list(qs.values_list('name', flat=True)), safe=False)


class MedicineAPIView(BaseAPIView):
    model = Medicine
    queryset = Medicine.objects.filter(is_active=True).all()

    def get(self, request, *args, **kwargs):
        qs = ModelFilterBackend.filter_queryset(request, self.queryset)
        return JsonResponse(list(qs.values_list('name', 'note')), safe=False)


class NoteAPIView(BaseAPIView):
    model = Note
    queryset = Note.objects.filter(is_active=True).all()


class NivTemplateListView(LoginRequiredMixin, generic.ListView):
    model = NivForm
    queryset = NivForm.objects.order_by('-id')
    template_name = 'mdf/niv-templates.html'
    paginate_by = 50

    def get_queryset(self):
        qs = self.queryset
        q = self.request.POST.get(
            'q', self.request.GET.get('q', '')
        ).strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q) | Q(doe__icontains=q) |
                Q(patient_name__icontains=q) | Q(department__icontains=q) |
                Q(card_number__icontains=q) | Q(chamber_number__icontains=q) |
                Q(diagnosis__icontains=q) | Q(doctor__icontains=q) |
                Q(nurse__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        return ctx

    def post(self, request, *args, **kwargs):
        paginator, page, queryset, is_paginated = self.paginate_queryset(
            self.get_queryset(), self.paginate_by
        )
        context = {
            'paginator': paginator,
            'page_obj': page,
            'is_paginated': is_paginated,
            'object_list': queryset,
        }
        return self.render_to_response(context)

    def get_template_names(self):
        if self.request.method == 'POST':
            return ['mdf/niv-form-search.html']
        return ['mdf/niv-templates.html']


class NivTemplateCreateView(LoginRequiredMixin, GetObjectMixin, generic.TemplateView):
    template_name = 'mdf/niv-create.html'

    def post(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        data = request.POST
        data._mutable = True
        validated_data, keys = validate_take_medicine(data)
        data.update(validated_data)
        instance = self.get_object()
        save_as_title = request.POST.get('as-title')
        if instance:
            form = NivFormFrom(instance=instance, data=data)
        else:
            form = NivFormFrom(data)
        if form.is_valid():
            instance = form.save()
            if save_as_title:
                instance.pk = None
                instance.title = save_as_title
                instance.save()
            action = request.POST.get('action')
            if action == 'save':
                return HttpResponseRedirect(f"{reverse_lazy('niv-cover')}?t={instance.pk}")
            if action == 'save_and_new':
                return HttpResponseRedirect(reverse_lazy('niv-cover'))
            if action == 'save_as_continue':
                return HttpResponseRedirect(f"{reverse_lazy('niv-cover')}?t={instance.pk}")
            return HttpResponseRedirect(reverse_lazy('niv-templates'))

        context['errors'] = form.errors
        # return self.render_to_response(context)
        return HttpResponseRedirect(reverse_lazy('niv-templates'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['data'] = self.request.POST
        return ctx


class NivTemplateDeleteView(LoginRequiredMixin, CsrfExemptMixin, generic.View):
    success_url = reverse_lazy('niv-templates')

    def delete(self, request, *args, **kwargs):
        obj = get_object_or_404(NivForm, **kwargs)
        obj.delete()
        return HttpResponseRedirect(self.success_url)


class NivDocumentView(LoginRequiredMixin,
                      DocxTemplateView):
    # template_name = 'MDF.docx'
    template_name = 'MDFNew.docx'
    filename = "MDF"
    output_format = 'pdf'

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        response['Content-Type'] = 'application/pdf'
        response['Content-Disposition'] = f'inline; filename="{self.filename}.pdf"'
        return response

    def get_context_data(self, **kwargs)-> dict:
        tid = self.request.GET.get('t', self.request.POST.get('t'))
        niv = NivForm.objects.filter(id=tid)
        if not niv.count():
            tid = self.request.GET.get('as', self.request.POST.get('as'))
            niv = NivForm.objects.filter(id=tid)
        data = serializers.serialize("json", niv)
        data = json.loads(data)[0]['fields']
        for i in range(1, 17):
            row = data[f'r{i}']
            for d in ('d1', 'd2', 'd3'):
                if d not in row:
                    row[d] = {}

        data['allergy'] = self.get_yes_no(data['allergy'])
        data['diabetes'] = self.get_yes_no(data['diabetes'])
        data['neuro'] = self.get_yes_no(data['neuro'])
        data['radial_pulse'] = self.get_radial_pulse(data['radial_pulse'])
        return data

    @staticmethod
    def get_yes_no(value) -> str:
        if value:
            return f'{Symbols.CHECKED.value} {YesNo.YES} {Symbols.UNCHECKED.value} {YesNo.NO}'
        return f'{Symbols.UNCHECKED.value} {YesNo.YES} {Symbols.UNCHECKED.value} {YesNo.NO}'

    @staticmethod
    def get_radial_pulse(value)-> str:
        txt = ''
        for pulse in RadialPulse:
            if pulse == value:
                txt += f' {Symbols.CHECKED.value} {pulse}'
            else:
                txt += f' {Symbols.UNCHECKED.value} {pulse}'
        return txt.strip()

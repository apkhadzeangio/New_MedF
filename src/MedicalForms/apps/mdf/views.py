import json
import logging
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, authenticate, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import serializers
from django.core.cache import cache
from django.db.models import Case, IntegerField, Q, Value, When
from django.forms import model_to_dict
from django.http import (
    Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import generic
from django_docxtpl import DocxTemplateView

from .backends import ModelFilterBackend
from .enums import Symbols, YesNo, RadialPulse
from .forms import LoginForm, NivFormFrom
from .mixins import LoginRedirectMixin, GetObjectMixin
from .models import Medicine, Note, NivForm, ROW_COUNT
from .utils import validate_take_medicine

logger = logging.getLogger(__name__)


class LoginView(LoginRedirectMixin, generic.FormView):
    template_name = 'mdf/login.html'
    form_class = LoginForm
    success_url = reverse_lazy('niv-cover')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['next'] = self.request.GET.get('next', '')
        return ctx

    def _client_ip(self):
        forwarded = self.request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return self.request.META.get('REMOTE_ADDR', '')

    def _lock_key(self, username):
        return f'login-fail:{username.lower()}:{self._client_ip()}'

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(
            form=form, message='შეიყვანეთ მომხმარებელი და პაროლი.'
        ))

    def form_valid(self, form):
        username = form.cleaned_data['username']
        lock_key = self._lock_key(username)
        failures = cache.get(lock_key, 0)
        if failures >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            logger.warning('Login locked for %s', lock_key)
            return self.render_to_response(self.get_context_data(
                form=form,
                message='ძალიან ბევრი წარუმატებელი მცდელობა. სცადეთ 15 წუთში.',
            ))

        user = authenticate(
            self.request,
            username=username,
            password=form.cleaned_data['password']
        )
        if not user:
            cache.set(lock_key, failures + 1, settings.LOGIN_LOCKOUT_SECONDS)
            return self.render_to_response(self.get_context_data(
                form=form, message='მომხმარებელი ან პაროლი არასწორია.'
            ))

        cache.delete(lock_key)
        login(self.request, user)
        if not form.cleaned_data.get('remember_me'):
            # Log out automatically when the browser is closed.
            self.request.session.set_expiry(0)

        next_page = form.cleaned_data['next']
        if next_page and next_page != reverse('logout') and url_has_allowed_host_and_scheme(
            next_page,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return HttpResponseRedirect(next_page)
        return super().form_valid(form)


class LogoutView(LoginRequiredMixin, generic.View):

    def get(self, request, *args, **kwargs):
        logout(request)
        return HttpResponseRedirect('/')

    post = get


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = 'mdf/password-change.html'
    success_url = reverse_lazy('niv-cover')

    def form_valid(self, form):
        messages.success(self.request, 'პაროლი შეიცვალა.')
        return super().form_valid(form)


class NivFormCoverView(LoginRequiredMixin,
                       GetObjectMixin,
                       generic.TemplateView):
    template_name = 'mdf/niv.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['niv'] = self.get_object()
        if not ctx['niv']:
            ctx['niv'] = self.get_object(kwarg='as')
            ctx['can_update'] = False
        else:
            ctx['can_update'] = True
        ctx['rows'] = range(1, ROW_COUNT + 1)
        return ctx


class AssignmentSheetView(NivFormCoverView):
    template_name = 'mdf/assignment_sheet.html'


class NivFormBackView(NivFormCoverView):
    template_name = 'mdf/niv-back.html'


class PrintNivFormCoverView(NivFormCoverView):
    template_name = 'mdf/print-niv.html'


class PrintNivFormBackView(NivFormBackView):
    template_name = 'mdf/print-niv-back.html'


# How many suggestions the autocomplete returns at most.
AUTOCOMPLETE_LIMIT = 30


class BaseAPIView(LoginRequiredMixin, generic.View):
    queryset = None
    fields = ('name',)

    def get_queryset(self):
        q = str(self.request.GET.get('q', '')).strip()
        if not q:
            return self.queryset.none()
        qs = ModelFilterBackend.filter_queryset(self.request, self.queryset)
        # Names that *start* with the typed text are shown first.
        return qs.annotate(
            _rank=Case(
                When(name__istartswith=q, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by('_rank', 'name')[:AUTOCOMPLETE_LIMIT]

    def get(self, request, *args, **kwargs):
        qs = self.get_queryset()
        if len(self.fields) == 1:
            data = list(qs.values_list(self.fields[0], flat=True))
        else:
            data = list(qs.values_list(*self.fields))
        return JsonResponse(data, safe=False)


class MedicineAPIView(BaseAPIView):
    queryset = Medicine.objects.filter(is_active=True)
    fields = ('name', 'note')


class NoteAPIView(BaseAPIView):
    queryset = Note.objects.filter(is_active=True)


class NivTemplateListView(LoginRequiredMixin, generic.ListView):
    model = NivForm
    template_name = 'mdf/niv-templates.html'
    paginate_by = 50

    def get_search_term(self):
        return self.request.POST.get(
            'q', self.request.GET.get('q', '')
        ).strip()

    def get_queryset(self):
        # Only the columns the list needs (title + rows for "copy").
        columns = ['id', 'title']
        for i in range(1, ROW_COUNT + 1):
            columns += [f'm{i}', f'n{i}']
        qs = NivForm.objects.only(*columns).order_by('-id')
        q = self.get_search_term()
        if q:
            qs = qs.filter(
                Q(title__icontains=q) | Q(doe__icontains=q) |
                Q(patient_name__icontains=q) | Q(department__icontains=q) |
                Q(card_number__icontains=q) | Q(chamber_number__icontains=q) |
                Q(diagnosis__icontains=q) | Q(doctor__icontains=q) |
                Q(nurse__icontains=q) | Q(person_id__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.get_search_term()
        return ctx

    def paginate_queryset(self, queryset, page_size):
        # An out-of-range ?page= (e.g. after narrowing the search) shows the
        # last page instead of a 404 error.
        paginator = self.get_paginator(queryset, page_size)
        page = paginator.get_page(self.request.GET.get('page'))
        return paginator, page, page.object_list, page.has_other_pages()

    def post(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data())

    def get_template_names(self):
        if self.request.method == 'POST':
            return ['mdf/niv-form-search.html']
        return ['mdf/niv-templates.html']


class NivTemplateCreateView(LoginRequiredMixin, GetObjectMixin, generic.View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        data = request.POST.copy()
        validated_data, _ = validate_take_medicine(data)
        data.update(validated_data)

        action = request.POST.get('action')
        save_as_title = (request.POST.get('as-title') or '').strip()
        instance = self.get_object()

        if instance and save_as_title:
            # "Save As": create a copy with the edits, leave the original as it was.
            # Fields that are not on the page (e.g. back-side rows) are copied
            # from the original.
            for name, value in model_to_dict(instance).items():
                if name == 'id' or name in data or value in (None, False):
                    continue
                data[name] = json.dumps(value) if isinstance(value, (dict, list)) else value
            data['title'] = save_as_title
            form = NivFormFrom(data=data)
        elif instance:
            form = NivFormFrom(instance=instance, data=data)
        else:
            form = NivFormFrom(data=data)

        if not form.is_valid():
            logger.warning('NivForm save failed: %s', form.errors.as_json())
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else field
                messages.error(request, f'{label}: {" ".join(errors)}')
            messages.error(request, 'შაბლონი ვერ შეინახა. გაასწორეთ შეცდომა და სცადეთ თავიდან.')
            back = reverse('niv-cover')
            if instance:
                back = f'{back}?t={instance.pk}'
            return HttpResponseRedirect(back)

        instance = form.save()
        messages.success(request, f'შაბლონი „{instance.title}“ შენახულია.')

        cover = reverse('niv-cover')
        if action == 'save_and_new':
            return HttpResponseRedirect(cover)
        if action in ('save', 'save_as_continue'):
            return HttpResponseRedirect(f'{cover}?t={instance.pk}')
        if action == 'save_as':
            return HttpResponseRedirect(reverse('niv-templates'))
        # Plain "Save" of a new template: stay on it.
        return HttpResponseRedirect(f'{cover}?t={instance.pk}')


class NivTemplateDeleteView(LoginRequiredMixin, generic.View):
    """Delete a template. Requires POST (or DELETE) with a CSRF token."""
    http_method_names = ['post', 'delete']
    success_url = reverse_lazy('niv-templates')

    def post(self, request, *args, **kwargs):
        obj = get_object_or_404(NivForm, **kwargs)
        title = obj.title
        obj.delete()
        logger.info('NivForm %s (%s) deleted by %s', kwargs.get('pk'), title, request.user)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'deleted': True})
        messages.success(request, f'შაბლონი „{title}“ წაიშალა.')
        return HttpResponseRedirect(self.success_url)

    delete = post


class NivDocumentView(LoginRequiredMixin,
                      DocxTemplateView):
    # NOTE: the printed form (MDFNew.docx) is the officially approved layout.
    # Do not change the template or the context keys below.
    # template_name = 'MDF.docx'
    template_name = 'MDFNew.docx'
    filename = "MDF"
    output_format = 'pdf'

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        response = super().get(request, *args, **kwargs)
        response['Content-Type'] = 'application/pdf'
        response['Content-Disposition'] = f'inline; filename="{self.filename}.pdf"'
        return response

    @staticmethod
    def _find(value):
        try:
            return NivForm.objects.filter(id=int(value))
        except (TypeError, ValueError):
            return NivForm.objects.none()

    def get_context_data(self, **kwargs) -> dict:
        niv = self._find(self.request.GET.get('t', self.request.POST.get('t')))
        if not niv.exists():
            niv = self._find(self.request.GET.get('as', self.request.POST.get('as')))
        if not niv.exists():
            raise Http404('შაბლონი ვერ მოიძებნა.')
        data = serializers.serialize("json", niv)
        data = json.loads(data)[0]['fields']
        for i in range(1, 17):
            row = data[f'r{i}']
            if not isinstance(row, dict):
                row = data[f'r{i}'] = {}
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
    def get_radial_pulse(value) -> str:
        txt = ''
        for pulse in RadialPulse:
            if pulse == value:
                txt += f' {Symbols.CHECKED.value} {pulse}'
            else:
                txt += f' {Symbols.UNCHECKED.value} {pulse}'
        return txt.strip()

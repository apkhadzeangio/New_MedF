from django.contrib.auth.mixins import AccessMixin
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .models import NivForm


class GetObjectMixin(object):

    def get_object(self, kwarg='t'):
        try:
            tid = self.request.GET.get(kwarg, self.request.POST.get(kwarg))
            return NivForm.objects.get(id=tid)
        except (ValueError, NivForm.DoesNotExist):
            pass
        return NivForm.objects.none()


class LoginRedirectMixin(object):
    redirect_url = '/'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(reverse_lazy('niv-cover'))
        return super().dispatch(request, *args, **kwargs)


class CsrfExemptMixin(object):

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class ActiveTabMixin(object):
    active_class = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = self.active_class
        return ctx

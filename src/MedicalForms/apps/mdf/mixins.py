from django.http import HttpResponseRedirect
from django.urls import reverse_lazy

from .models import NivForm


class GetObjectMixin(object):

    def get_object(self, kwarg='t'):
        """Return the NivForm whose id is in ?<kwarg>=..., or None."""
        tid = self.request.GET.get(kwarg, self.request.POST.get(kwarg))
        try:
            return NivForm.objects.get(id=int(tid))
        except (TypeError, ValueError, NivForm.DoesNotExist):
            return None


class LoginRedirectMixin(object):
    redirect_url = '/'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(reverse_lazy('niv-cover'))
        return super().dispatch(request, *args, **kwargs)


class ActiveTabMixin(object):
    active_class = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = self.active_class
        return ctx

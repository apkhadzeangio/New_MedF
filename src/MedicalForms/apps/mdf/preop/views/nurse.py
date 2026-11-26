"""Track B Nurse Workspace views (per-user view switcher and nurse-specific fast actions)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import generic

from ..nurse_ui import PREOP_UI_NEW, PREOP_UI_OLD, set_preop_ui_mode


class PreopUiModeView(LoginRequiredMixin, generic.View):
    """Per-user switch between 'ახალი ხედი' (new nurse UI) and 'ძველი ხედი' (classic UI)."""

    def post(self, request):
        mode = request.POST.get('mode') or PREOP_UI_OLD
        chosen = set_preop_ui_mode(request, mode)
        if chosen == PREOP_UI_NEW:
            messages.success(request, 'ჩართულია ახალი საექთნო ხედი. ძველ ხედზე დაბრუნება ნებისმიერ წამს შეგიძლიათ.')
        else:
            messages.info(request, 'დაბრუნდით ძველ ხედზე.')
        next_url = (request.POST.get('next') or '').strip()
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return HttpResponseRedirect(next_url)
        return HttpResponseRedirect(reverse('preop'))

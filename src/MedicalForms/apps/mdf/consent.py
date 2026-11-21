"""Informed consent sheets for operations ("თანხმობის ფურცლები", NIV-300-12/a).

Page 1 is the same official text for every operation (only the patient's
name is written in). Page 2 has the complications of the operation: one or
more blocks, each a bold title and two columns. The name in the list, the
titles and the complications can be edited; the last edit is kept. The
patient's name, the doctor and the date are written on the sheet before
printing and are not stored.
"""
import html
import json
import re
import zipfile

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Q
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from .duty import strip_astral
from .models import ConsentForm, StaffProfile

MAX_BLOCKS = 10
MAX_ITEMS = 40
MAX_TEXT = 300


# --------------------------------------------------------------------------
# cleaning what comes from the page
# --------------------------------------------------------------------------

def _line(value, limit=MAX_TEXT):
    return ' '.join(strip_astral(str(value or '')).split())[:limit]


def clean_blocks(raw):
    """[{title, left: [..], right: [..]}] with plain text only."""
    blocks = []
    for block in (raw if isinstance(raw, list) else [])[:MAX_BLOCKS]:
        if not isinstance(block, dict):
            continue
        cleaned = {'title': _line(block.get('title'), 500)}
        for side in ('left', 'right'):
            items = block.get(side) if isinstance(block.get(side), list) else []
            cleaned[side] = [t for t in (_line(i) for i in items[:MAX_ITEMS]) if t]
        if cleaned['title'] or cleaned['left'] or cleaned['right']:
            blocks.append(cleaned)
    return blocks


def name_from_blocks(blocks):
    title = blocks[0]['title'] if blocks else ''
    title = re.sub(r'^\s*გართულებები\s*', '', title).rstrip(' :')
    # no .upper(): it turns Georgian letters into Mtavruli
    return title[:200] or 'ახალი თანხმობის ფურცელი'


# --------------------------------------------------------------------------
# reading the complications from a Word file (.docx) of the same form
# --------------------------------------------------------------------------

_P = re.compile(r'<w:p[ >].*?</w:p>', re.S)
_R = re.compile(r'<w:r[ >].*?</w:r>', re.S)
_T = re.compile(r'<w:t(?: [^>]*)?>(.*?)</w:t>', re.S)
_BOLD = re.compile(r'<w:b/>|<w:b w:val="(?:1|true|on)"/>')
START_MARK = 'ასევე ჩემთვის ცნობილია'      # the last paragraph of page 1
# sentences that some older sheets have near the complications: not complications
NOT_COMPLICATIONS = ('პაციენტს აეხსნა ფინანსური', 'პაციენტს პასუხი გაეცა')
END_MARK = 'მკურნალი ექიმი'                  # the signatures start


def _paragraphs(document_xml):
    for p in _P.findall(document_xml):
        text, bold_chars, chars = '', 0, 0
        for r in _R.findall(p):
            t = html.unescape(''.join(_T.findall(r)))
            if '<w:tab/>' in r:
                t = '\t' + t
            text += t
            n = len(t.strip())
            chars += n
            if _BOLD.search(r):
                bold_chars += n
        yield text, chars > 0 and bold_chars >= chars * 0.6


def parse_docx(fileobj):
    """Complication blocks from a consent .docx (only the text of page 2)."""
    with zipfile.ZipFile(fileobj) as z:
        xml = z.read('word/document.xml').decode('utf-8')
    paras = list(_paragraphs(xml))
    start = next((i for i, (t, _) in enumerate(paras) if START_MARK in t), None)
    end = next((i for i, (t, _) in enumerate(paras) if END_MARK in t and (start is None or i > start)), None)
    if start is None or end is None:
        raise ValueError('ფაილში ვერ ვიპოვე გართულებების ნაწილი (უნდა იყოს ამავე ფორმის თანხმობის ფურცელი).')
    blocks = []
    for text, bold in paras[start + 1:end]:
        if not text.strip() or any(m in text for m in NOT_COMPLICATIONS):
            continue
        if bold or not blocks:
            blocks.append({'title': text, 'left': [], 'right': []})
            if bold:
                continue
        # two columns in Word = a tab or a wide run of spaces (a few spaces are just a typo)
        parts = [p for p in re.split(r'\t+| {6,}', text.strip()) if p.strip()]
        if parts:
            blocks[-1]['left'].append(parts[0])
        if len(parts) > 1:
            blocks[-1]['right'].append(' '.join(parts[1:]))
    blocks = clean_blocks(blocks)
    if not blocks:
        raise ValueError('გართულებები ფაილში ვერ ვიპოვე.')
    return blocks


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------

class ConsentListView(LoginRequiredMixin, generic.ListView):
    """All sheets; the most used first."""
    template_name = 'mdf/consent/list.html'
    context_object_name = 'forms'

    def get_queryset(self):
        qs = ConsentForm.objects.order_by('-use_count', 'name')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(blocks__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '').strip()
        for form in ctx['forms']:
            form.subtitle = form.blocks[0]['title'] if form.blocks else ''
        return ctx


def _doctor_name(user):
    profile = StaffProfile.objects.filter(user=user).first()
    return profile.doctor_name if profile else (user.get_full_name() or user.username)


class ConsentSheetView(LoginRequiredMixin, generic.TemplateView):
    """The sheet itself: edit on the page, print with the browser."""
    template_name = 'mdf/consent/sheet.html'

    def get(self, request, *args, **kwargs):
        # a visit counts as a use: the list puts the most used sheets first
        ConsentForm.objects.filter(pk=kwargs['pk']).update(use_count=F('use_count') + 1)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = get_object_or_404(ConsentForm, pk=kwargs['pk'])
        ctx.update({
            'form': form,
            'today': timezone.localtime().strftime('%d.%m.%Y'),
            'doctor': _doctor_name(self.request.user),
        })
        return ctx


class ConsentSaveView(LoginRequiredMixin, generic.View):
    """Keep the last edit (name in the list and the complications)."""

    def post(self, request, pk):
        form = get_object_or_404(ConsentForm, pk=pk)
        try:
            data = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return HttpResponseBadRequest('bad data')
        blocks = clean_blocks(data.get('blocks'))
        form.name = _line(data.get('name'), 200) or name_from_blocks(blocks)
        form.blocks = blocks
        form.updated_by = request.user
        form.save()
        return JsonResponse({'ok': True, 'name': form.name,
                             'saved': timezone.localtime(form.updated_at).strftime('%H:%M')})


class ConsentCreateView(LoginRequiredMixin, generic.View):
    """A new sheet: a copy of another one, or from a Word file."""

    def post(self, request):
        upload = request.FILES.get('docx')
        if upload:
            if upload.size > 5 * 1024 * 1024 or not upload.name.lower().endswith('.docx'):
                messages.error(request, 'აირჩიეთ Word ფაილი (.docx), 5 MB-მდე.')
                return HttpResponseRedirect(reverse('consent-list'))
            try:
                blocks = parse_docx(upload)
            except (ValueError, KeyError, zipfile.BadZipFile) as exc:
                messages.error(request, str(exc) if isinstance(exc, ValueError) else 'ფაილი ვერ წავიკითხე.')
                return HttpResponseRedirect(reverse('consent-list'))
            form = ConsentForm.objects.create(name=name_from_blocks(blocks), blocks=blocks, updated_by=request.user)
            messages.success(request, 'ფურცელი შეიქმნა Word ფაილიდან. გადაამოწმეთ გართულებები და სახელი.')
        else:
            source = ConsentForm.objects.filter(pk=request.POST.get('copy') or 0).first()
            form = ConsentForm.objects.create(
                name=(f'{source.name} (ასლი)' if source else 'ახალი თანხმობის ფურცელი')[:200],
                blocks=source.blocks if source else [{'title': 'გართულებები … ოპერაციის დროს:', 'left': [], 'right': []}],
                updated_by=request.user)
        return HttpResponseRedirect(reverse('consent-sheet', args=[form.pk]))


class ConsentDeleteView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        form = get_object_or_404(ConsentForm, pk=pk)
        name = form.name
        form.delete()
        messages.success(request, f'„{name}“ წაიშალა.')
        return HttpResponseRedirect(reverse('consent-list'))

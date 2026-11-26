"""Private case photo processing, EXIF-stripping thumbnail creation, and upload/serving views."""
import datetime
import io
import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ..duty import strip_astral
from ..models import Case, CasePhoto

MAX_UPLOAD = 30 * 1024 * 1024        # one photo as it comes from the phone
FULL_SIZE = 2000                     # longest side kept (sharp on a projector)
THUMB_SIZE = 480


class PhotoError(ValueError):
    pass


def _date(value):
    try:
        return datetime.date.fromisoformat((value or '').strip())
    except ValueError:
        return None


def process_photo(uploaded):
    """Uploaded image -> (full JPEG, thumbnail JPEG, width, height).

    Turned upright, shrunk, re-encoded: no location or other hidden data stays in the file.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    if (getattr(uploaded, 'size', 0) or 0) > MAX_UPLOAD:
        raise PhotoError('ფაილი ძალიან დიდია (მაქს. 30 მბ).')
    try:
        image = Image.open(uploaded)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise PhotoError('ეს ფაილი სურათად ვერ წავიკითხე (JPG, PNG ან WEBP გამოიყენეთ).')
    image = ImageOps.exif_transpose(image)
    if image.mode in ('RGBA', 'LA', 'P'):
        image = image.convert('RGBA')
        back = Image.new('RGB', image.size, 'white')
        back.paste(image, mask=image.split()[-1])
        image = back
    else:
        image = image.convert('RGB')

    def jpeg(img, size, quality):
        img = img.copy()
        img.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=quality, optimize=True, progressive=True)
        return out.getvalue(), img.size

    full, (width, height) = jpeg(image, FULL_SIZE, 86)
    thumb, _ = jpeg(image, THUMB_SIZE, 80)
    return full, thumb, width, height


def add_photo(case, uploaded, kind, user, taken_on=None):
    full, thumb, width, height = process_photo(uploaded)
    name = uuid.uuid4().hex
    photo = CasePhoto(
        case=case, kind=kind, width=width, height=height, created_by=user,
        taken_on=taken_on or timezone.localdate(),
    )
    photo.image.save(f'{name}.jpg', ContentFile(full), save=False)
    photo.thumb.save(f'{name}-t.jpg', ContentFile(thumb), save=False)
    photo.save()
    Case.objects.filter(pk=case.pk).update(updated_at=timezone.now(), updated_by=user)
    return photo


def _photos_by_kind(case):
    groups = []
    photos = list(case.photos.all())
    for kind, label in CasePhoto.Kind.choices:
        items = [p for p in photos if p.kind == kind]
        if items:
            groups.append({'kind': kind, 'label': label, 'photos': items})
    return groups


class PhotoUploadView(LoginRequiredMixin, generic.View):
    """One or more photos. The site's script sends them one by one (already shrunk on the phone)."""

    def post(self, request, pk):
        case = get_object_or_404(Case, pk=pk)
        kind = request.POST.get('kind')
        if kind not in CasePhoto.Kind.values:
            kind = CasePhoto.Kind.WOUND
        taken_on = _date(request.POST.get('taken_on'))
        if taken_on and taken_on > timezone.localdate():
            taken_on = None
        files = request.FILES.getlist('photo')
        ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        added, errors = 0, []
        for f in files[:20]:
            try:
                add_photo(case, f, kind, request.user, taken_on)
                added += 1
            except PhotoError as e:
                errors.append(f'{f.name}: {e}')
        if ajax:
            return JsonResponse({'added': added, 'errors': errors}, status=200 if added or not errors else 400)
        for e in errors:
            messages.error(request, e)
        if added:
            messages.success(request, f'დაემატა {added} ფოტო.')
        elif not errors:
            messages.error(request, 'ფოტო არ არის არჩეული.')
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + f'?kind={kind}#photos')


class PhotoView(LoginRequiredMixin, generic.View):
    """The photo file itself, for logged-in users only."""

    def get(self, request, pk, size='full'):
        photo = get_object_or_404(CasePhoto, pk=pk)
        field = photo.thumb if size == 'thumb' else photo.image
        try:
            fh = field.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404
        response = FileResponse(fh, content_type='image/jpeg')
        response['Cache-Control'] = 'private, max-age=604800'
        return response


class PhotoEditView(LoginRequiredMixin, generic.View):
    """Change the caption / type / date of a photo, or delete it."""

    def post(self, request, pk):
        photo = get_object_or_404(CasePhoto, pk=pk)
        case = photo.case
        if request.POST.get('action') == 'delete':
            photo.delete()
            messages.success(request, 'ფოტო წაიშალა.')
        else:
            photo.caption = ' '.join(strip_astral(request.POST.get('caption', '')).split())[:255]
            if request.POST.get('kind') in CasePhoto.Kind.values:
                photo.kind = request.POST['kind']
            taken_on = _date(request.POST.get('taken_on'))
            if taken_on and taken_on <= timezone.localdate():
                photo.taken_on = taken_on
            photo.save(update_fields=['caption', 'kind', 'taken_on'])
        Case.objects.filter(pk=case.pk).update(updated_at=timezone.now(), updated_by=request.user)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True})
        return HttpResponseRedirect(reverse('case', args=[case.pk]) + '#photos')

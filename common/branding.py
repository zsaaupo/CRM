from io import BytesIO
from uuid import uuid4

from django.db import transaction
from django.db.models import F
from django.urls import reverse
from PIL import Image, ImageOps
from rest_framework import serializers

from .models import ERPBranding


VIEW_LOGO = 'common.view_erpbranding'
CHANGE_LOGO = (VIEW_LOGO, 'common.change_erpbranding')
LOGO_MAX_BYTES = 5 * 1024 * 1024
LOGO_MAX_DIMENSION = 4096
LOGO_RENDER_DIMENSION = 1024


def logo_url(revision, *, sidebar=False):
    route = 'common:sidebar-logo-image' if sidebar else 'common:logo-image'
    return reverse(route, args=[revision]) if revision else None


def current_logo_revision():
    return ERPBranding.objects.values_list('logo_revision', flat=True).first()


def logo_png(revision=None):
    records = ERPBranding.objects.filter(logo_revision__isnull=False)
    if revision is not None:
        records = records.filter(logo_revision=revision)
    return records.values_list('logo', flat=True).first()


def sidebar_logo_png(png):
    with Image.open(BytesIO(png)) as logo:
        bounds = logo.getchannel('A').getbbox()
        if bounds is None or bounds == (0, 0, *logo.size):
            return png
        output = BytesIO()
        logo.crop(bounds).save(output, format='PNG')
        return output.getvalue()


def normalize_logo(upload):
    if upload.size > LOGO_MAX_BYTES:
        raise serializers.ValidationError('Choose an image of 5 MB or less.')
    try:
        with Image.open(upload, formats=('PNG', 'JPEG', 'WEBP')) as image:
            if getattr(image, 'is_animated', False):
                raise serializers.ValidationError('Choose a still PNG, JPEG or WebP image.')
            if max(image.size) > LOGO_MAX_DIMENSION:
                raise serializers.ValidationError(f'Image dimensions must be {LOGO_MAX_DIMENSION} pixels or less per side.')
            logo = ImageOps.exif_transpose(image).convert('RGBA')
            logo.thumbnail((LOGO_RENDER_DIMENSION, LOGO_RENDER_DIMENSION), Image.Resampling.LANCZOS)
            logo.info.clear()
            output = BytesIO()
            logo.save(output, format='PNG')
            return output.getvalue()
    except (OSError, Image.DecompressionBombError, ValueError, SyntaxError) as exc:
        raise serializers.ValidationError('Choose a readable PNG, JPEG or WebP image.') from exc


@transaction.atomic
def set_logo(actor, png=b''):
    records = ERPBranding.objects.filter(pk=1)
    # Acquire a write lock before reading the singleton, including on SQLite.
    records.update(updated_at=F('updated_at'))
    branding = records.select_for_update().defer('logo').first()
    if not png and (branding is None or branding.logo_revision is None):
        return None
    username = actor.get_username()
    revision = uuid4() if png else None
    if branding is None:
        branding, created = records.get_or_create(pk=1, defaults={'logo': png, 'logo_revision': revision,
                                                                  'create_by': username, 'modified_by': username})
        if created:
            return revision
    branding.logo = png
    branding.logo_revision = revision
    branding.modified_by = username
    branding.save(update_fields=['logo', 'logo_revision', 'modified_by', 'updated_at'])
    return revision

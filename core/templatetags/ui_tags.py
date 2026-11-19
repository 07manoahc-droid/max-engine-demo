"""Filtres de gabarit pour les couleurs des statuts et des associés (lus dans `core/ui.py`).

Une clé inconnue donne l'apparence neutre (gris) : l'écran ne plante jamais pour une couleur.
"""

from urllib.parse import quote

from django import template
from django.conf import settings
from django.urls import reverse

from core.formatting import format_euros, format_integer
from core.ui import (
    NEUTRAL_STYLE,
    PART_STATUS_STYLES,
    PARTNER_STYLES,
    STATUS_STYLES,
    WARNING_BADGE,
    WARNING_TEXT,
)

register = template.Library()


@register.filter
def euros(amount) -> str:
    """`{{ montant|euros }}` → « 1 421,02 € » (montant déjà calculé par les services).
    Un montant absent (champ facultatif vide) s'affiche vide plutôt que de faire planter."""
    if amount is None or amount == "":
        return ""
    return format_euros(amount)


@register.filter
def resultat(signed) -> str:
    """`{{ benefice|resultat }}` → « 400,00 € » ou « Perte de 300,00 € », sans signe moins
    (`accounting.services.SignedAmount`, D61)."""
    if signed is None:
        return ""
    if signed.is_negative:
        return f"Perte de {format_euros(signed.shown)}"
    return format_euros(signed.amount)


@register.filter
def nombre(number) -> str:
    """`{{ km|nombre }}` → « 12 500 » ; vide si le nombre n'est pas renseigné."""
    if number is None or number == "":
        return ""
    return format_integer(int(number))


@register.filter
def status_label(status: str) -> str:
    """Libellé français d'un statut de véhicule (la clé elle-même si elle est inconnue)."""
    style = STATUS_STYLES.get(status)
    return style.label if style else str(status)


@register.filter
def status_badge(status: str) -> str:
    """Classes du badge d'un statut (fond et texte)."""
    return STATUS_STYLES.get(status, NEUTRAL_STYLE).badge


@register.filter
def status_dot(status: str) -> str:
    """Classe de la pastille d'un statut."""
    return STATUS_STYLES.get(status, NEUTRAL_STYLE).dot


@register.filter
def partner_badge(color: str) -> str:
    """Classes du badge ou de l'avatar d'un associé, d'après sa clé de couleur."""
    return PARTNER_STYLES.get(color, NEUTRAL_STYLE).badge


@register.filter
def part_status_label(status: str) -> str:
    """Libellé français du statut d'une pièce (vide pour une ligne qui n'est pas une pièce)."""
    style = PART_STATUS_STYLES.get(status)
    return style.label if style else str(status or "")


@register.filter
def part_status_badge(status: str) -> str:
    """Classes du badge du statut d'une pièce."""
    return PART_STATUS_STYLES.get(status, NEUTRAL_STYLE).badge


@register.simple_tag
def neutral_badge() -> str:
    """Classes d'un badge neutre (gris), pour une mention qui n'est pas un statut."""
    return NEUTRAL_STYLE.badge


@register.simple_tag
def warning_badge() -> str:
    """Classes d'une alerte ambre qui n'est pas un statut (fond et texte)."""
    return WARNING_BADGE


@register.simple_tag
def warning_text() -> str:
    """Classe du texte (ou de l'icône) d'une alerte ambre."""
    return WARNING_TEXT


@register.filter
def thumbnail_url(photo) -> str:
    """`{{ photo|thumbnail_url }}` → l'adresse de sa miniature (D92). Le nom du fichier y
    est ajouté : le cache du navigateur suit la photo, pas seulement son numéro."""
    adresse = reverse("core:photo_thumbnail", args=[photo.pk])
    return f"{adresse}?v={quote(photo.file.name)}"


@register.simple_tag
def max_files_per_send() -> int:
    """Fichiers envoyés d'un coup, au plus (`DATA_UPLOAD_MAX_NUMBER_FILES`, D92)."""
    return settings.DATA_UPLOAD_MAX_NUMBER_FILES

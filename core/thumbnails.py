"""Miniatures des photos (D92) : une petite image JPEG par photo, pour les cartes et la
galerie, au lieu de la photo d'origine (jusqu'à 20 Mo).

Les miniatures sont rangées à part, dans `media/miniatures/`, avec le chemin de leur photo
(`miniatures/vehicules/3/moto.jpg.jpg`). Elles ne sont ni dans la base ni dans la sauvegarde :
ce ne sont que des copies réduites, refaites quand elles manquent (après une restauration,
par exemple). En consultation (`READ_ONLY`), rien n'est écrit : la miniature est fabriquée
en mémoire à chaque demande.

Pillow n'est importé qu'au moment de faire une miniature : si une mise à jour s'arrête
avant d'avoir installé les bibliothèques, le logiciel démarre encore (sauvegarde, contrôle
des comptes) et montre les photos d'origine.
"""

import logging
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils._os import safe_join

from .models import Attachment

logger = logging.getLogger(__name__)

# Côté le plus long, en pixels : assez pour une carte sur un écran haute définition.
SIZE = 640
QUALITY = 82
TEMP_SUFFIX = ".partiel"
# Au-delà (après la lecture réduite d'un JPEG), l'image n'est pas décodée : elle prendrait
# trop de mémoire. La photo d'origine est alors montrée telle quelle.
MAX_PIXELS = 60_000_000


class ThumbnailRefused(ValueError):
    """Pas de miniature pour cette photo (image démesurée, ou consultation)."""


def thumbnail_path(photo: Attachment) -> Path:
    """Où est (ou sera) la miniature de cette photo. `safe_join` refuse un chemin qui
    sortirait de `MEDIA_ROOT` (`SuspiciousFileOperation`)."""
    return Path(
        safe_join(settings.MEDIA_ROOT, settings.THUMBNAILS_FOLDER, f"{photo.file.name}.jpg")
    )


def source_path(photo: Attachment) -> Path:
    """La photo d'origine, par le stockage de Django (qui vérifie aussi le chemin)."""
    return Path(photo.file.path)


def make_thumbnail(source: Path) -> bytes:
    """La miniature JPEG d'une photo : tournée comme sur le téléphone (EXIF), réduite à
    `SIZE` pixels au plus, sur fond blanc si elle avait de la transparence."""
    from PIL import Image, ImageOps

    with Image.open(source) as image:
        # Une photo JPEG se décode directement en plus petit : beaucoup plus rapide.
        image.draft("RGB", (SIZE * 2, SIZE * 2))
        largeur, hauteur = image.size
        if largeur * hauteur > MAX_PIXELS:
            raise ThumbnailRefused(f"Image trop grande pour une miniature ({largeur}×{hauteur}).")
        image = ImageOps.exif_transpose(image)
        image.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            image = image.convert("RGBA")
            fond = Image.new("RGB", image.size, "white")
            fond.paste(image, mask=image.getchannel("A"))
            image = fond
        elif image.mode != "RGB":
            image = image.convert("RGB")
        sortie = BytesIO()
        image.save(sortie, "JPEG", quality=QUALITY, optimize=True)
        return sortie.getvalue()


def ensure_thumbnail(photo: Attachment) -> Path:
    """La miniature de cette photo, faite si elle manque.

    Chaque demande écrit son propre fichier provisoire, puis le met en place : deux
    demandes en même temps (deux fenêtres) n'écrivent jamais une image à moitié, et la
    seconde garde la miniature de la première si Windows refuse le remplacement."""
    if settings.READ_ONLY:
        raise ThumbnailRefused("Consultation : rien n'est écrit.")
    chemin = thumbnail_path(photo)
    if chemin.is_file():
        return chemin
    donnees = make_thumbnail(source_path(photo))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_name(f"{chemin.name}.{uuid.uuid4().hex}{TEMP_SUFFIX}")
    try:
        provisoire.write_bytes(donnees)
        try:
            provisoire.replace(chemin)
        except PermissionError:
            if not chemin.is_file():
                raise
    finally:
        provisoire.unlink(missing_ok=True)
    return chemin


def thumbnail_bytes(photo: Attachment) -> bytes:
    """La miniature, lue sur le disque, faite et rangée si elle manque ; en consultation,
    faite en mémoire sans rien écrire (le dossier des fichiers est celui d'une sauvegarde)."""
    if settings.READ_ONLY:
        chemin = thumbnail_path(photo)
        return chemin.read_bytes() if chemin.is_file() else make_thumbnail(source_path(photo))
    return ensure_thumbnail(photo).read_bytes()


def prepare(photos) -> None:
    """Fait d'avance la miniature des photos qu'on vient d'envoyer. Elle est facultative :
    quoi qu'il arrive à Pillow (photo abîmée, EXIF étrange, mémoire), l'envoi réussit et la
    photo d'origine s'affiche ; l'erreur est seulement notée."""
    for photo in photos:
        try:
            ensure_thumbnail(photo)
        except Exception:
            logger.warning("Pas de miniature pour la photo n°%s.", photo.pk, exc_info=True)

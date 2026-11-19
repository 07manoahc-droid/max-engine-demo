"""Miniatures des photos (D92) : faites à l'envoi, refaites si elles manquent, rangées à part
dans `media/miniatures/`, jamais dans la base ni dans la sauvegarde ; jamais écrites en
consultation. Et le message clair au-delà de 100 photos envoyées d'un coup."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from django.conf import settings as settings_module
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from core import thumbnails
from core.backup import mirror_media
from core.models import Attachment
from core.services import SESSION_KEY
from vehicles import services


def image(largeur=3000, hauteur=2000, mode="RGB", format_="JPEG", couleur="red", **options):
    sortie = BytesIO()
    Image.new(mode, (largeur, hauteur), couleur).save(sortie, format_, **options)
    return sortie.getvalue()


def envoyer(vehicule, contenu, nom="moto.jpg"):
    (photo,), refusees = services.add_photos(
        vehicule, [SimpleUploadedFile(nom, contenu, content_type="image/jpeg")], "AFTER"
    )
    assert refusees == []
    return photo


def lire(chemin: Path) -> Image.Image:
    return Image.open(BytesIO(chemin.read_bytes()))


@pytest.fixture
def moto(make_vehicle):
    return make_vehicle(brand="GG", model="250")


# ---------------------------------------------------------------------------
# Fabrication
# ---------------------------------------------------------------------------


def test_thumbnail_is_made_at_upload_beside_the_photos(moto, settings):
    photo = envoyer(moto, image())
    chemin = thumbnails.thumbnail_path(photo)
    assert chemin == Path(settings.MEDIA_ROOT) / "miniatures" / f"{photo.file.name}.jpg"
    assert chemin.is_file()
    miniature = lire(chemin)
    assert miniature.format == "JPEG"
    assert miniature.size == (640, 427)
    # Bien plus petite que la photo d'origine.
    assert chemin.stat().st_size < Path(photo.file.path).stat().st_size


def test_phone_orientation_is_applied(moto):
    """Une photo prise en portrait par un téléphone (EXIF « tourner de 90° »)."""
    exif = Image.Exif()
    exif[0x0112] = 6
    photo = envoyer(moto, image(1200, 400, exif=exif))
    assert lire(thumbnails.thumbnail_path(photo)).size == (213, 640)


def test_transparent_png_gets_a_white_background(moto):
    photo = envoyer(moto, image(200, 100, "RGBA", "PNG", (0, 0, 0, 0)), nom="logo.png")
    miniature = lire(thumbnails.thumbnail_path(photo))
    assert miniature.mode == "RGB"
    assert miniature.getpixel((10, 10)) == (255, 255, 255)


def test_small_photo_is_not_enlarged(moto):
    photo = envoyer(moto, image(300, 200))
    assert lire(thumbnails.thumbnail_path(photo)).size == (300, 200)


def test_unreadable_photo_is_still_added(moto):
    """Des octets de JPEG sans image : la photo est ajoutée, sans miniature."""
    photo = envoyer(moto, b"\xff\xd8\xff\xe0" + b"\x00" * 60)
    assert not thumbnails.thumbnail_path(photo).exists()


def test_thumbnails_are_not_in_the_database(moto):
    """Rien de plus en base : une ligne par photo, et aucun champ ne parle de miniature."""
    envoyer(moto, image())
    assert Attachment.objects.count() == 1
    champs = {champ.name for champ in Attachment._meta.get_fields()}
    assert not {nom for nom in champs if "thumb" in nom or "miniature" in nom}


# ---------------------------------------------------------------------------
# Adresse de la miniature
# ---------------------------------------------------------------------------


def test_missing_thumbnail_is_made_again(as_mathis, moto):
    photo = envoyer(moto, image())
    chemin = thumbnails.thumbnail_path(photo)
    chemin.unlink()
    reponse = as_mathis.get(f"/photos/{photo.pk}/miniature/")
    assert reponse.status_code == 200
    assert reponse["Content-Type"] == "image/jpeg"
    assert "max-age" in reponse["Cache-Control"]
    assert chemin.is_file()
    assert reponse.content == chemin.read_bytes()


def test_unreadable_photo_shows_the_original(as_mathis, moto):
    photo = envoyer(moto, b"\xff\xd8\xff\xe0" + b"\x00" * 60)
    reponse = as_mathis.get(f"/photos/{photo.pk}/miniature/")
    assert reponse.status_code == 302
    assert reponse.url == photo.file.url


def test_missing_photo_file_is_not_found(as_mathis, moto):
    photo = envoyer(moto, image())
    thumbnails.thumbnail_path(photo).unlink()
    Path(photo.file.path).unlink()
    assert as_mathis.get(f"/photos/{photo.pk}/miniature/").status_code == 404


def test_only_photos_have_a_thumbnail(as_mathis, moto):
    facture = Attachment.objects.create(
        file=SimpleUploadedFile("facture.pdf", b"%PDF-1.7"),
        kind=Attachment.Kind.INVOICE,
        vehicle=moto,
    )
    assert as_mathis.get(f"/photos/{facture.pk}/miniature/").status_code == 404


def test_photo_in_the_trash_keeps_its_thumbnail(as_mathis, moto):
    photo = envoyer(moto, image())
    services.remove_photo(photo)
    assert as_mathis.get(f"/photos/{photo.pk}/miniature/").status_code == 200


def test_read_only_never_writes_a_thumbnail(as_mathis, moto, settings):
    """En consultation, `MEDIA_PATH` est le dossier d'une sauvegarde : rien n'y est écrit."""
    photo = envoyer(moto, image())
    chemin = thumbnails.thumbnail_path(photo)
    chemin.unlink()
    settings.READ_ONLY = True
    reponse = as_mathis.get(f"/photos/{photo.pk}/miniature/")
    assert reponse.status_code == 200
    assert Image.open(BytesIO(reponse.content)).size == (640, 427)
    # Rien d'écrit, pas même un fichier provisoire.
    assert not [p for p in (Path(settings.MEDIA_ROOT) / "miniatures").rglob("*") if p.is_file()]


# ---------------------------------------------------------------------------
# Sauvegarde
# ---------------------------------------------------------------------------


def test_backup_mirror_leaves_the_thumbnails_out(moto, settings, tmp_path):
    photo = envoyer(moto, image())
    miroir = tmp_path / "fichiers"
    resultat = mirror_media(Path(settings.MEDIA_ROOT), miroir)
    copies = sorted(p.relative_to(miroir).as_posix() for p in miroir.rglob("*") if p.is_file())
    assert copies == [photo.file.name]
    assert (resultat.copied, resultat.total) == (1, 1)
    assert not (miroir / "miniatures").exists()


# ---------------------------------------------------------------------------
# Plus de 100 photos d'un coup
# ---------------------------------------------------------------------------


def cent_une_photos():
    return [SimpleUploadedFile(f"p{i}.jpg", b"\xff\xd8\xff\xe0", "image/jpeg") for i in range(101)]


def test_more_than_100_photos_give_a_clear_message(as_mathis, moto):
    fiche = moto.get_absolute_url()
    reponse = as_mathis.post(
        f"/vehicules/{moto.number}/photos/",
        {"stage": "AFTER", "photos": cent_une_photos()},
        headers={"HX-Request": "true", "Referer": f"http://testserver{fiche}"},
    )
    assert reponse.status_code == 200
    assert reponse["HX-Redirect"] == f"http://testserver{fiche}"
    assert [str(m) for m in get_messages(reponse.wsgi_request)] == [
        "Tu as envoyé plus de 100 fichiers d'un coup : rien n'est enregistré. Envoie-les "
        "en plusieurs fois, 100 au plus à chaque envoi."
    ]
    assert not Attachment.objects.exists()


def test_the_message_comes_before_the_csrf_check(mathis, moto):
    """Dans le vrai logiciel, la protection CSRF lit l'envoi avant la vue : le refus doit
    déjà y donner le message, sans page d'erreur brute. Envoi comme HTMX : jeton dans
    l'en-tête, cookie CSRF posé par la fiche."""
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session[SESSION_KEY] = mathis.pk
    session.save()
    fiche = moto.get_absolute_url()
    client.get(fiche)
    reponse = client.post(
        f"/vehicules/{moto.number}/photos/",
        {"stage": "AFTER", "photos": cent_une_photos()},
        headers={
            "HX-Request": "true",
            "X-CSRFToken": client.cookies["csrftoken"].value,
            "Referer": f"http://testserver{fiche}",
        },
    )
    assert reponse.status_code == 200
    assert reponse["HX-Redirect"] == f"http://testserver{fiche}"
    assert not Attachment.objects.exists()


def test_screen_stops_more_than_100_photos_before_sending(as_mathis, moto, settings):
    """Le même seuil à l'écran qu'au serveur."""
    assert settings.DATA_UPLOAD_MAX_NUMBER_FILES == 100
    page = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert "fichiers.length > 100" in page
    assert "Tu as choisi plus de 100 photos : rien n'est envoyé." in page


def test_other_bad_requests_keep_the_usual_page(as_mathis):
    reponse = as_mathis.get("/", headers={"Host": "mechant.example"})
    assert reponse.status_code == 400


# ---------------------------------------------------------------------------
# Relecture
# ---------------------------------------------------------------------------


def test_thumbnail_address_carries_the_file_name(as_mathis, moto):
    """Après une restauration, un même numéro peut désigner une autre photo : l'adresse
    porte aussi le nom du fichier, pour que le navigateur ne les confonde pas."""
    photo = envoyer(moto, image())
    page = as_mathis.get("/vehicules/").content.decode()
    assert f'src="/photos/{photo.pk}/miniature/?v={photo.file.name}"' in page


def test_huge_image_keeps_its_original(as_mathis, moto, monkeypatch):
    monkeypatch.setattr(thumbnails, "MAX_PIXELS", 1000)
    photo = envoyer(moto, image(200, 100, format_="PNG"), nom="grande.png")
    assert not thumbnails.thumbnail_path(photo).exists()
    reponse = as_mathis.get(f"/photos/{photo.pk}/miniature/")
    assert (reponse.status_code, reponse.url) == (302, photo.file.url)


def test_unexpected_pillow_error_never_breaks_the_upload(as_mathis, moto, monkeypatch):
    """EXIF malformé, mémoire… : la photo est ajoutée une seule fois, l'original s'affiche."""

    def plante(source):
        raise TypeError("EXIF illisible")

    monkeypatch.setattr(thumbnails, "make_thumbnail", plante)
    photo = envoyer(moto, image())
    assert Attachment.objects.count() == 1
    reponse = as_mathis.get(f"/photos/{photo.pk}/miniature/")
    assert (reponse.status_code, reponse.url) == (302, photo.file.url)


def test_two_requests_at_once_give_one_whole_thumbnail(moto):
    """Deux fenêtres demandent la même miniature absente : chacune la reçoit entière, et
    aucun fichier provisoire ne reste."""
    photo = envoyer(moto, image())
    chemin = thumbnails.thumbnail_path(photo)
    chemin.unlink()
    with ThreadPoolExecutor(max_workers=6) as groupe:
        resultats = list(groupe.map(lambda _: thumbnails.ensure_thumbnail(photo), range(6)))
    assert set(resultats) == {chemin}
    assert Image.open(chemin).size == (640, 427)
    assert [p.name for p in chemin.parent.iterdir()] == [chemin.name]


def test_path_outside_the_media_folder_is_refused(as_mathis, moto):
    photo = envoyer(moto, image())
    Attachment.objects.filter(pk=photo.pk).update(file="../../ailleurs.jpg")
    assert as_mathis.get(f"/photos/{photo.pk}/miniature/").status_code == 400


@pytest.mark.parametrize(
    "origine",
    ["http://mechant.example/vehicules/1/", "", "javascript:alert(1)"],
    ids=["autre site", "sans origine", "javascript"],
)
def test_too_many_files_goes_back_only_to_this_site(as_mathis, moto, origine):
    entetes = {"HX-Request": "true"}
    if origine:
        entetes["Referer"] = origine
    reponse = as_mathis.post(
        f"/vehicules/{moto.number}/photos/",
        {"stage": "AFTER", "photos": cent_une_photos()},
        headers=entetes,
    )
    assert reponse["HX-Redirect"] == "/"


def test_too_many_files_without_htmx_redirects(as_mathis, moto):
    fiche = moto.get_absolute_url()
    reponse = as_mathis.post(
        f"/vehicules/{moto.number}/photos/",
        {"stage": "AFTER", "photos": cent_une_photos()},
        headers={"Referer": f"http://testserver{fiche}"},
    )
    assert (reponse.status_code, reponse.url) == (302, f"http://testserver{fiche}")


SANS_PILLOW = """
import sys
sys.modules["PIL"] = None  # Pillow absent : « import PIL » échoue
import django
django.setup()
from django.core.management import call_command
call_command("check")
import core.backup, core.views, vehicles.services
print("démarre sans Pillow")
"""


def test_software_starts_without_pillow(tmp_path):
    """Gardien : une mise à jour arrêtée avant `uv sync` laisse le nouveau code sans Pillow.
    Le logiciel doit encore démarrer (sauvegarde, contrôle des comptes) ; seules les
    miniatures manquent, et les photos d'origine s'affichent."""
    env = os.environ | {
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DATABASE_PATH": str(tmp_path / "vide.sqlite3"),
        "PYTHONIOENCODING": "utf-8",
    }
    resultat = subprocess.run(
        [sys.executable, "-c", SANS_PILLOW],
        cwd=settings_module.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert "démarre sans Pillow" in resultat.stdout


def test_without_pillow_the_original_is_shown(as_mathis, moto, monkeypatch):
    photo = envoyer(moto, image())
    thumbnails.thumbnail_path(photo).unlink()
    monkeypatch.setitem(sys.modules, "PIL", None)
    reponse = as_mathis.get(f"/photos/{photo.pk}/miniature/")
    assert (reponse.status_code, reponse.url) == (302, photo.file.url)

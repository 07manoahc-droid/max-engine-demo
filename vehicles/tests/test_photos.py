"""Tests des photos : contrôle des fichiers, ajout par étape, couverture, corbeille, écrans.

Les fichiers sont écrits dans un dossier temporaire (fixture `media_in_temporary_folder`).
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Attachment
from vehicles import services

Stage = Attachment.Stage

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 60
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 60


def upload(name="moto.jpg", content=JPEG):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


# --- Contrôle des fichiers (fonctions pures) -----------------------------------------


@pytest.mark.parametrize(
    ("debut", "attendu"),
    [(JPEG, "jpeg"), (PNG, "png"), (WEBP, "webp"), (HEIC, "heic"), (b"%PDF-1.7", None)],
)
def test_image_format_reads_the_content(debut, attendu):
    assert services.image_format(debut[:16]) == attendu


@pytest.mark.parametrize("marque", [b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevm", b"mif1"])
def test_every_heic_variant_is_recognised(marque):
    assert services.image_format(b"\x00\x00\x00\x18ftyp" + marque) == "heic"


@pytest.mark.parametrize(
    ("nom", "taille", "debut", "message"),
    [
        ("moto.jpg", 1000, JPEG, None),
        ("MOTO.JPEG", 1000, JPEG, None),
        ("moto.png", 1000, PNG, None),
        ("moto.webp", 1000, WEBP, None),
        ("IMG_1234.HEIC", 1000, HEIC, services.HEIC_MESSAGE),
        ("IMG_1234.jpg", 1000, HEIC, services.HEIC_MESSAGE),
        ("facture.pdf", 1000, b"%PDF-1.7", "Ce n'est pas une photo lisible"),
        ("faux.jpg", 1000, b"%PDF-1.7", "Ce n'est pas une photo lisible"),
        ("enorme.jpg", 20 * 1024 * 1024 + 1, JPEG, "dépasse 20 Mo"),
    ],
)
def test_photo_problem(nom, taille, debut, message):
    probleme = services.photo_problem(nom, taille, debut[:16])
    if message is None:
        assert probleme is None
    else:
        assert message in probleme


def test_heic_message_is_the_agreed_one():
    assert services.HEIC_MESSAGE == (
        "Ce format n'est pas lu. Envoie la photo en JPEG "
        "(réglage « Le plus compatible » de l'iPhone)."
    )


# --- Ajout, galerie, couverture, corbeille -----------------------------------------


def test_add_photos_keeps_the_good_ones_and_explains_the_others(make_vehicle):
    vehicule = make_vehicle()
    ajoutees, refusees = services.add_photos(
        vehicule, [upload("a.jpg"), upload("IMG.HEIC", HEIC), upload("b.png", PNG)], "BEFORE"
    )
    assert [p.order for p in ajoutees] == [0, 1]
    assert all(p.kind == Attachment.Kind.PHOTO and p.stage == Stage.BEFORE for p in ajoutees)
    assert ajoutees[0].file.name.startswith(f"vehicules/{vehicule.number}/")
    assert refusees == [("IMG.HEIC", services.HEIC_MESSAGE)]


def test_add_photos_follows_the_existing_order(make_vehicle):
    vehicule = make_vehicle()
    services.add_photos(vehicule, [upload(), upload()], "AFTER")
    ajoutees, _ = services.add_photos(vehicule, [upload()], "AFTER")
    assert ajoutees[0].order == 2


def test_add_photos_shortens_very_long_names(make_vehicle):
    ajoutees, _ = services.add_photos(make_vehicle(), [upload("x" * 300 + ".JPG")], "AFTER")
    nom = ajoutees[0].file.name.rsplit("/", 1)[1]
    assert len(nom) <= services.FILE_NAME_MAX
    assert nom.endswith(".jpg")


@pytest.mark.parametrize(
    ("etape", "fichiers", "message"),
    [
        ("", [upload()], "Choisis l'étape"),
        ("PIRATE", [upload()], "Choisis l'étape"),
        ("AFTER", [], "au moins une photo"),
    ],
)
def test_add_photos_refuses_without_stage_or_file(make_vehicle, etape, fichiers, message):
    with pytest.raises(ValidationError, match=message):
        services.add_photos(make_vehicle(), fichiers, etape)


def test_photo_stages_follow_the_journey_and_skip_the_trash(make_vehicle):
    vehicule = make_vehicle()
    (apres,), _ = services.add_photos(vehicule, [upload()], "AFTER")
    (avant, jetee), _ = services.add_photos(vehicule, [upload(), upload()], "BEFORE")
    services.remove_photo(jetee)
    etapes = services.photo_stages(vehicule)
    assert [(e, label) for e, label, _ in etapes] == [
        ("LISTING", "Annonce"),
        ("BEFORE", "Avant"),
        ("DURING", "Pendant"),
        ("AFTER", "Après"),
    ]
    assert [photos for _, _, photos in etapes] == [[], [avant], [], [apres]]
    jetee.refresh_from_db()
    assert jetee.is_deleted


def test_cover_photo_is_the_first_of_the_most_advanced_stage(make_vehicle):
    avec_apres, avec_annonce, sans_photo = make_vehicle(), make_vehicle(), make_vehicle()
    services.add_photos(avec_apres, [upload()], "LISTING")
    (premiere, _), _ = services.add_photos(avec_apres, [upload(), upload()], "AFTER")
    (annonce,), _ = services.add_photos(avec_annonce, [upload()], "LISTING")
    couvertures = services.cover_photos([avec_apres, avec_annonce, sans_photo])
    assert couvertures == {avec_apres.pk: premiere, avec_annonce.pk: annonce}


def test_cover_photo_ignores_photos_in_the_trash(make_vehicle):
    vehicule = make_vehicle()
    (apres,), _ = services.add_photos(vehicule, [upload()], "AFTER")
    (avant,), _ = services.add_photos(vehicule, [upload()], "BEFORE")
    services.remove_photo(apres)
    assert services.cover_photos([vehicule]) == {vehicule.pk: avant}


# --- Écrans ----------------------------------------------------------------------


def post_photos(client, vehicule, fichiers, stage="BEFORE", htmx=True):
    en_tetes = {"HX-Request": "true"} if htmx else {}
    return client.post(
        f"/vehicules/{vehicule.number}/photos/",
        {"stage": stage, "photos": fichiers},
        headers=en_tetes,
    )


def test_detail_shows_four_drop_zones(as_mathis, make_vehicle):
    contenu = as_mathis.get(f"/vehicules/{make_vehicle().number}/").content.decode()
    assert contenu.count("Glisse tes photos ici") == 4
    assert contenu.count("Choisir des photos") == 4
    for etape in ("Annonce", "Avant", "Pendant", "Après"):
        assert f">{etape} <" in contenu


def test_upload_with_htmx_returns_the_gallery(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    response = post_photos(as_mathis, vehicule, [upload("a.jpg"), upload("b.jpg")])
    contenu = response.content.decode()
    assert response.status_code == 200
    assert contenu.lstrip().startswith('<section id="photos"')
    assert "2 photos ajoutées dans « Avant »." in contenu
    # La photo d'origine pour l'agrandir, la miniature dans la galerie (D92).
    assert contenu.count(f"/media/vehicules/{vehicule.number}/") == 2
    assert contenu.count("/miniature/") == 2
    assert Attachment.objects.filter(vehicle=vehicule).count() == 2


def test_upload_refuses_heic_with_the_advice(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    response = post_photos(as_mathis, vehicule, [upload("IMG_1234.HEIC", HEIC)])
    contenu = response.content.decode()
    assert "IMG_1234.HEIC n&#x27;a pas été ajoutée." in contenu
    assert "réglage « Le plus compatible » de l&#x27;iPhone" in contenu
    assert not Attachment.objects.exists()


def test_upload_without_file_explains(as_mathis, make_vehicle):
    response = post_photos(as_mathis, make_vehicle(), [])
    assert "Choisis au moins une photo." in response.content.decode()


def test_upload_without_htmx_goes_back_to_the_sheet(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    response = post_photos(as_mathis, vehicule, [upload()], stage="AFTER", htmx=False)
    assert response.status_code == 302
    assert response.url == f"/vehicules/{vehicule.number}/"
    assert Attachment.objects.get().stage == Stage.AFTER


def test_upload_needs_post(as_mathis, make_vehicle):
    assert as_mathis.get(f"/vehicules/{make_vehicle().number}/photos/").status_code == 405


def test_delete_photo_puts_it_in_the_trash(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    (photo,), _ = services.add_photos(vehicule, [upload()], "AFTER")
    response = as_mathis.post(
        f"/vehicules/{vehicule.number}/photos/{photo.pk}/corbeille/", headers={"HX-Request": "true"}
    )
    assert "La photo est dans la corbeille." in response.content.decode()
    photo.refresh_from_db()
    assert photo.is_deleted
    assert photo.file.storage.exists(photo.file.name)  # le fichier reste sur le disque


def test_delete_photo_of_another_vehicle_is_not_found(as_mathis, make_vehicle):
    (photo,), _ = services.add_photos(make_vehicle(), [upload()], "AFTER")
    autre = make_vehicle()
    response = as_mathis.post(f"/vehicules/{autre.number}/photos/{photo.pk}/corbeille/")
    assert response.status_code == 404
    photo.refresh_from_db()
    assert not photo.is_deleted


def test_delete_asks_for_confirmation(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    services.add_photos(vehicule, [upload()], "AFTER")
    contenu = as_mathis.get(f"/vehicules/{vehicule.number}/").content.decode()
    assert 'hx-confirm="Mettre cette photo à la corbeille ? Tu pourras la restaurer."' in contenu


def test_read_only_hides_upload_and_delete(as_mathis, make_vehicle, settings):
    vehicule = make_vehicle()
    services.add_photos(vehicule, [upload()], "AFTER")
    settings.READ_ONLY = True
    contenu = as_mathis.get(f"/vehicules/{vehicule.number}/").content.decode()
    assert "Glisse tes photos ici" not in contenu
    assert "à la corbeille" not in contenu
    assert f"/media/vehicules/{vehicule.number}/" in contenu


def test_card_shows_the_cover_photo(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    (photo,), _ = services.add_photos(vehicule, [upload()], "AFTER")
    response = as_mathis.get("/vehicules/")
    assert response.context["cards"][0]["cover"] == photo
    assert f'src="/photos/{photo.pk}/miniature/?v=' in response.content.decode()

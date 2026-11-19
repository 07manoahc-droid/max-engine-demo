"""Fichiers statiques et joints servis sans DEBUG (D85), et adresse `/sante/` du lanceur.

Les tests tournent sans DEBUG, comme le lanceur : ce sont les adresses de `config/urls.py`
qui servent les fichiers, pas le serveur de développement.
"""

import pytest
from django.conf import settings

from core.version import VERSION


@pytest.mark.parametrize(
    "adresse",
    [
        "/static/css/tailwind.css",
        "/static/max-engine.ico",
        "/static/vendor/tabler/sprite.svg",
        "/static/django_htmx/htmx-2.min.js",  # fichier d'une bibliothèque, sans collectstatic
    ],
)
@pytest.mark.django_db
def test_static_files_are_served_without_debug(client, adresse):
    assert settings.DEBUG is False
    reponse = client.get(adresse)
    assert reponse.status_code == 200
    assert b"".join(reponse.streaming_content)


@pytest.mark.django_db
def test_missing_static_file_gets_a_short_answer(client):
    reponse = client.get("/static/absent.css")
    assert reponse.status_code == 404
    assert reponse.content == b"Fichier introuvable."


@pytest.mark.django_db
def test_attachments_are_served_without_choosing_a_partner(client, settings, tmp_path):
    """Une photo s'affiche sans passer par « Qui es-tu ? » ni lire la base."""
    (tmp_path / "media" / "photos").mkdir(parents=True)
    (tmp_path / "media" / "photos" / "moto.jpg").write_bytes(b"photo")

    reponse = client.get("/media/photos/moto.jpg")
    assert reponse.status_code == 200
    assert b"".join(reponse.streaming_content) == b"photo"


@pytest.mark.django_db
def test_attachments_follow_media_path(client, settings, tmp_path):
    """`MEDIA_ROOT` est lu à chaque demande : `MEDIA_PATH` (consultation) est suivi."""
    autre = tmp_path / "fichiers"
    autre.mkdir()
    (autre / "facture.pdf").write_bytes(b"%PDF")
    settings.MEDIA_ROOT = autre
    assert client.get("/media/facture.pdf").status_code == 200


@pytest.mark.django_db
def test_attachment_path_cannot_leave_its_folder(client):
    assert client.get("/media/../manage.py").status_code in (400, 404)
    assert client.get("/media/%2e%2e/manage.py").status_code in (400, 404)


@pytest.mark.django_db
def test_error_page_finds_its_stylesheet(client):
    """La page 500 ne lit pas la base ; sa feuille de style doit être servie (D69, D85)."""
    from django.template.loader import render_to_string

    page = render_to_string("500.html")
    assert "/static/css/tailwind.css" in page
    assert client.get("/static/css/tailwind.css").status_code == 200


@pytest.mark.django_db
def test_health_says_it_is_max_engine(client):
    reponse = client.get("/sante/")
    assert reponse.status_code == 200
    assert reponse.content.decode() == f"MAx Engine {VERSION}"


@pytest.mark.django_db
def test_health_in_read_only_mode(client, settings):
    settings.READ_ONLY = True
    assert client.get("/sante/").content.decode().startswith("MAx Engine")

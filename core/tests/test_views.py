"""Tests des écrans communs : choix de l'associé, accueil provisoire, gabarit de base."""

import datetime as dt
import re
from decimal import Decimal as D

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from accounting.services import Payment
from core.models import Partner
from vehicles.services import record_purchase

CHOOSE_URL = "/qui-es-tu/"


@pytest.fixture
def mathis(db):
    return Partner.objects.get(name="Mathis")


@pytest.fixture
def axel(db):
    return Partner.objects.get(name="Axel")


@pytest.fixture
def as_mathis(client, mathis):
    """Client qui a déjà choisi Mathis."""
    client.post(CHOOSE_URL, {"partner": mathis.pk})
    return client


# --- Réglages ---------------------------------------------------------------


def test_settings_follow_the_project_rules():
    assert settings.LANGUAGE_CODE == "fr-fr"
    assert settings.TIME_ZONE == "Europe/Paris"
    assert settings.USE_TZ is True
    assert settings.SESSION_COOKIE_AGE == 365 * 24 * 3600


# --- Redirection tant qu'aucun associé n'est choisi -----------------------


def test_home_redirects_to_the_choice_when_nobody_is_chosen(client, db):
    response = client.get("/")
    assert response.status_code == 302
    assert response.url == f"{CHOOSE_URL}?next=%2F"


def test_htmx_request_without_partner_gets_a_client_redirect(client, db):
    response = client.get("/", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert response["HX-Redirect"] == CHOOSE_URL


def test_static_files_are_not_redirected(client, db):
    response = client.get("/static/css/tailwind.css")
    assert response.status_code != 302


def test_admin_is_not_redirected_to_the_choice(client, db):
    response = client.get("/admin/login/")
    assert response.status_code == 200


# --- Choix de l'associé -----------------------------------------------------


def test_choice_page_shows_both_partners(client, db):
    response = client.get(CHOOSE_URL)
    content = response.content.decode()
    assert response.status_code == 200
    assert "Qui es-tu ?" in content
    assert "Mathis" in content
    assert "Axel" in content


def test_valid_choice_remembers_the_partner_for_a_year(client, axel):
    response = client.post(CHOOSE_URL, {"partner": axel.pk})
    assert response.status_code == 302
    assert response.url == reverse("core:home")
    assert client.session["partner_id"] == axel.pk
    assert int(response.cookies["sessionid"]["max-age"]) == 365 * 24 * 3600


def test_valid_choice_returns_to_the_requested_page(client, mathis):
    response = client.post(CHOOSE_URL, {"partner": mathis.pk, "next": "/admin/"})
    assert response.url == "/admin/"


def test_choice_never_redirects_to_another_site(client, mathis):
    response = client.post(CHOOSE_URL, {"partner": mathis.pk, "next": "https://example.org/"})
    assert response.url == reverse("core:home")


@pytest.mark.parametrize("value", ["", "abc", "999999", "-1"])
def test_invalid_choice_shows_an_error_and_remembers_nobody(client, db, value):
    response = client.post(CHOOSE_URL, {"partner": value})
    assert response.status_code == 200
    assert "Choisis ton prénom pour continuer." in response.content.decode()
    assert "partner_id" not in client.session


def test_inactive_partner_cannot_be_chosen(client, axel):
    Partner.objects.filter(pk=axel.pk).update(is_active=False)
    response = client.post(CHOOSE_URL, {"partner": axel.pk})
    assert response.status_code == 200
    assert "partner_id" not in client.session


def test_partner_deactivated_later_must_choose_again(as_mathis, mathis):
    Partner.objects.filter(pk=mathis.pk).update(is_active=False)
    response = as_mathis.get("/")
    assert response.status_code == 302


def test_changing_partner_replaces_the_previous_choice(as_mathis, axel):
    as_mathis.post(CHOOSE_URL, {"partner": axel.pk})
    assert as_mathis.session["partner_id"] == axel.pk


# --- Accueil et gabarit de base ----------------------------------------------


def test_home_greets_the_chosen_partner(as_mathis):
    response = as_mathis.get("/")
    content = response.content.decode()
    assert response.status_code == 200
    assert "Bonjour Mathis" in content


def test_home_has_indicators_and_three_big_buttons(as_mathis):
    content = as_mathis.get("/").content.decode()
    annee = timezone.localdate().year
    assert "Coût des véhicules au garage" in content
    assert "Investi au garage" not in content
    assert "fiches à la corbeille" not in content
    assert f"Bénéfice {annee}" in content
    # Le lecteur d'écran lit « Bénéfice 2026 · Voir le bilan », pas « 2026Voir ».
    assert f'<span>Bénéfice {annee}</span><span class="sr-only"> · </span>' in content
    assert "Marge des ventes" not in content
    assert 'href="/bilan/"' in content
    for bouton in ("Ajouter un véhicule", "Véhicules au garage", "Véhicules vendus"):
        assert bouton in content
    assert "Aucun pour l'instant" in content


def test_home_garage_cost_matches_the_report_and_names_the_trash(as_mathis, mathis, make_vehicle):
    """D66 : une fiche à la corbeille dont l'achat ne l'est pas compte dans le coût au
    garage, comme dans le bilan, et l'accueil dit combien en vient."""
    for prix, a_la_corbeille in (("1500", False), ("200", True)):
        vehicule = make_vehicle()
        record_purchase(
            vehicule,
            price=D(prix),
            date=dt.date(2026, 11, 1),
            payments=[Payment(mathis, D(prix))],
            seller=None,
            partner=mathis,
        )
        if a_la_corbeille:
            vehicule.soft_delete()

    content = as_mathis.get("/").content.decode()
    assert "1\N{NO-BREAK SPACE}700,00\N{NO-BREAK SPACE}€" in content
    assert "dont 200,00\N{NO-BREAK SPACE}€ sur des fiches à la corbeille" in content


def test_home_hides_the_add_button_in_read_only_mode(as_mathis, settings):
    settings.READ_ONLY = True
    assert "Ajouter un véhicule" not in as_mathis.get("/").content.decode()


def test_sidebar_has_navigation_empty_state_and_theme_switch(as_mathis):
    content = as_mathis.get("/").content.decode()
    assert "Accueil" in content
    assert "Comptes" in content
    assert "Bilan" in content
    assert "Aucun véhicule au garage. Ajoute le premier." in content
    assert "Changer" in content
    assert "Mode sombre" in content
    # Tous les liens mènent à leur page : « Vendus » dans le menu et sur l'accueil,
    # « Bilan » dans le menu et sur la tuile du bénéfice.
    assert 'title="Bientôt disponible"' not in content
    assert 'href="/comptes/"' in content
    assert content.count('href="/bilan/"') == 2
    assert content.count('href="/vendus/"') == 2
    assert "bientôt</span>" not in content
    # D36 : la liste s'appelle « En cours », pas « Au garage » (doublon avec le menu).
    assert ">En cours</h2>" in content
    assert 'href="/vehicules/"' in content


def test_sidebar_can_be_collapsed(as_mathis):
    """Bouton de repli, état posé avant l'affichage (fenêtre étroite ou choix mémorisé),
    noms gardés pour les lecteurs d'écran, liste des véhicules masquée une fois repliée."""
    content = as_mathis.get("/").content.decode()
    assert 'aria-controls="barre-laterale"' in content
    assert "Replier le menu" in content
    assert "#icon-layout-sidebar" in content
    assert 'localStorage.getItem("sidebar") === "collapsed"' in content
    assert 'matchMedia("(max-width: 1023px)")' in content
    assert '<span class="collapsed:sr-only">Accueil</span>' in content
    assert (
        'aria-labelledby="sidebar-garage" class="flex flex-col gap-1 collapsed:hidden"' in content
    )


def test_no_template_comment_leaks_into_pages(as_mathis, client):
    """Un commentaire `{# #}` sur plusieurs lignes n'est pas reconnu par Django et
    s'afficherait tel quel : les commentaires longs passent par `{% comment %}`."""
    for html in (as_mathis.get("/").content.decode(), client.get(CHOOSE_URL).content.decode()):
        assert "{#" not in html and "#}" not in html


def test_pages_load_nothing_from_the_internet(client, as_mathis):
    """Aucun CDN : toute ressource est servie depuis /static/ (htmx, Alpine, CSS, icônes)."""
    pages = [as_mathis.get("/").content.decode(), client.get(CHOOSE_URL).content.decode()]
    for html in pages:
        assert not re.search(r"""(?:src|href)=["'](?:https?:)?//""", html)
        assert "/static/django_htmx/htmx-2.min.js" in html
        assert "/static/vendor/alpine/alpine.min.js" in html
        assert "/static/css/tailwind.css" in html


def test_csrf_token_is_sent_with_htmx_requests(as_mathis):
    content = as_mathis.get("/").content.decode()
    assert re.search(r"""hx-headers='\{"x-csrftoken": "[^"]+"\}'""", content)


# --- Sécurité et robustesse (relecture de la tâche 1.1) ------------------------


@pytest.mark.parametrize("target", ["//example.org/", "javascript:alert(1)", "https:evil"])
def test_choice_refuses_unsafe_next_values(client, mathis, target):
    response = client.post(CHOOSE_URL, {"partner": mathis.pk, "next": target})
    assert response.url == reverse("core:home")


def test_next_of_the_redirect_is_kept_in_the_form(client, db):
    content = client.get(f"{CHOOSE_URL}?next=/admin/").content.decode()
    assert '<input type="hidden" name="next" value="/admin/">' in content


def test_choice_form_requires_the_csrf_token(db):
    from django.test import Client

    strict_client = Client(enforce_csrf_checks=True)
    response = strict_client.post(CHOOSE_URL, {"partner": 1})
    assert response.status_code == 403


def test_unknown_partner_color_does_not_break_the_page(as_mathis, mathis):
    Partner.objects.filter(pk=mathis.pk).update(color="fuchsia")
    assert as_mathis.get("/").status_code == 200


def test_static_files_need_no_session_and_no_query(client, db, django_assert_num_queries):
    with django_assert_num_queries(0):
        client.get("/static/css/tailwind.css")


def test_missing_static_file_gets_a_short_answer(client, db, settings, django_assert_num_queries):
    """D69 : un fichier statique introuvable ne fabrique pas la page, sans aucune requête."""
    settings.DEBUG = False
    with django_assert_num_queries(0):
        reponse = client.get("/static/absent.css")
    assert reponse.status_code == 404
    assert reponse.content.decode() == "Fichier introuvable."


def test_choice_page_explains_when_no_partner_is_active(client, db):
    Partner.objects.update(is_active=False)
    content = client.get(CHOOSE_URL).content.decode()
    assert "Aucun associé" in content


def test_post_without_partner_is_redirected_without_next(client, db):
    response = client.post("/", {})
    assert response.status_code in (302, 403)


def test_toast_shows_django_messages():
    from django.template.loader import render_to_string

    html = render_to_string("components/toast.html", {"messages": ["Dépense ajoutée."]})
    assert "Dépense ajoutée." in html
    assert 'aria-live="polite"' in html


def test_read_only_flag_is_exposed_to_templates(as_mathis, settings):
    settings.READ_ONLY = True
    response = as_mathis.get("/")
    assert response.context["read_only"] is True


def test_the_partner_admin_cannot_delete_a_partner(admin_client, mathis):
    response = admin_client.get(f"/admin/core/partner/{mathis.pk}/delete/")
    assert response.status_code == 403


# --- Pages d'erreur (D69) ------------------------------------------------------------


def test_page_not_found_is_in_french_with_a_way_home(as_mathis, settings, make_vehicle):
    """DEBUG faux, comme avec le lanceur : une adresse inconnue ou une fiche à la corbeille
    donnent la page introuvable en français, avec la barre latérale et l'accueil."""
    settings.DEBUG = False
    jetee = make_vehicle()
    adresse = jetee.get_absolute_url()
    jetee.soft_delete()
    for url in ("/cette-page-n-existe-pas/", adresse):
        reponse = as_mathis.get(url)
        page = reponse.content.decode()
        assert reponse.status_code == 404
        assert "Cette page n'existe pas (ou plus)." in page
        assert "Revenir à l'accueil" in page
        assert 'href="/"' in page


def test_server_error_page_reads_nothing_from_the_database(db, django_assert_num_queries):
    """La page 500 s'affiche sans contexte et sans base : même une base abîmée l'affiche."""
    from django.template.loader import render_to_string

    with django_assert_num_queries(0):
        page = render_to_string("500.html")
    assert "Quelque chose s'est mal passé." in page
    assert "préviens Mathis" in page


def test_missing_file_gets_a_short_answer(as_mathis, settings):
    """Une photo ou une facture introuvable ne fabrique pas toute la page (barre latérale,
    lue en base) ; `test_static_files_need_no_session_and_no_query` couvre `/static/`."""
    settings.DEBUG = False
    reponse = as_mathis.get("/media/vehicules/9999/absente.jpg")
    assert reponse.status_code == 404
    assert reponse.content.decode() == "Fichier introuvable."

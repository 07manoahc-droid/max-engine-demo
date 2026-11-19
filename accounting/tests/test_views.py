"""Tests des écrans des comptes : solde, règlement, aperçu, annulation."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.utils import timezone

from accounting import settlements
from accounting.models import Settlement
from core.formatting import format_euros


@pytest.fixture
def settlement(axel_owes, mathis, axel):
    return settlements.record_settlement(
        from_partner=axel,
        to_partner=mathis,
        amount=D("100.00"),
        date=timezone.localdate(),
    ).settlement


def test_accounts_page_shows_the_balance(as_mathis, axel_owes):
    page = as_mathis.get("/comptes/").content.decode()

    assert f"Axel doit {format_euros(D('205.44'))} à Mathis" in page
    assert "Enregistrer le règlement" in page
    assert "Comment ce solde est calculé" in page
    assert "Aucun règlement pour l'instant" in page


def test_accounts_page_when_settled_has_no_settlement_button(as_mathis):
    page = as_mathis.get("/comptes/").content.decode()

    # D67 : le solde s'écrit « À l'équilibre », comme dans le bilan et l'export.
    assert "À l&#x27;équilibre" in page
    assert "Enregistrer le règlement" not in page


def test_settlement_form_is_prefilled_with_what_is_owed(as_mathis, axel_owes):
    page = as_mathis.get("/comptes/reglements/ajouter/").content.decode()

    assert 'value="205,44"' in page
    assert "verse à" in page


def test_settlement_form_when_settled_goes_back(as_mathis):
    reponse = as_mathis.get("/comptes/reglements/ajouter/")
    assert reponse.status_code == 302
    assert reponse.url == "/comptes/"


def test_recording_a_settlement(as_mathis, axel_owes, mathis, axel):
    reponse = as_mathis.post(
        "/comptes/reglements/ajouter/",
        {
            "amount": "205,44",
            "date": "2026-11-03",
            "notes": "virement",
            "from_partner": axel.pk,
            "to_partner": mathis.pk,
            "seen_due": "205,44",
        },
        follow=True,
    )

    reglement = Settlement.objects.get()
    assert (reglement.from_partner, reglement.to_partner) == (axel, mathis)
    assert reglement.amount == D("205.44")
    assert reponse.redirect_chain[-1][0] == "/comptes/"
    assert "Vous êtes à l&#x27;équilibre." in reponse.content.decode()


def test_recording_a_settlement_from_a_vehicle_goes_back_to_it(
    as_mathis,
    axel_owes,
    mathis,
    axel,
    make_vehicle,
):
    vehicule = make_vehicle()
    reponse = as_mathis.post(
        f"/comptes/reglements/ajouter/?vehicule={vehicule.number}",
        {
            "amount": "10",
            "date": "2026-11-03",
            "from_partner": axel.pk,
            "to_partner": mathis.pk,
            "seen_due": "205,44",
        },
    )
    assert reponse.url == vehicule.get_absolute_url()


def test_too_large_settlement_shows_the_refusal(as_mathis, axel_owes, mathis, axel):
    reponse = as_mathis.post(
        "/comptes/reglements/ajouter/",
        {
            "amount": "300",
            "date": "2026-11-03",
            "from_partner": axel.pk,
            "to_partner": mathis.pk,
            "seen_due": "205,44",
        },
    )
    assert reponse.status_code == 200
    assert "ne peut pas dépasser ce montant" in reponse.content.decode()
    assert not Settlement.objects.exists()


def test_preview_tells_what_remains(as_mathis, axel_owes):
    partiel = as_mathis.post("/comptes/reglements/apercu/", {"amount": "100"}).content.decode()
    complet = as_mathis.post("/comptes/reglements/apercu/", {"amount": "205,44"}).content.decode()
    vide = as_mathis.post("/comptes/reglements/apercu/", {"amount": ""}).content.decode()

    assert f"Axel devra encore {format_euros(D('105.44'))} à Mathis" in partiel
    assert "vous serez à l&#x27;équilibre" in complet
    assert vide == ""


def test_history_shows_cancelled_and_cancellation(as_mathis, settlement):
    settlements.cancel_settlement(settlement, reason="saisi en double")
    page = as_mathis.get("/comptes/").content.decode()

    assert "Annulé le" in page
    assert "Annulation du règlement du" in page
    assert "Motif : saisi en double" in page
    # Ni l'un ni l'autre ne s'annule plus.
    assert "/annuler/" not in page


def test_cancelling_a_settlement(as_mathis, settlement):
    page = as_mathis.get(f"/comptes/reglements/{settlement.pk}/annuler/").content.decode()
    assert "règlement inverse" in page

    sans_motif = as_mathis.post(f"/comptes/reglements/{settlement.pk}/annuler/", {"reason": ""})
    assert "Indique pourquoi tu annules ce règlement." in sans_motif.content.decode()

    reponse = as_mathis.post(f"/comptes/reglements/{settlement.pk}/annuler/", {"reason": "erreur"})
    assert reponse.url == "/comptes/"
    assert Settlement.objects.filter(cancels=settlement).exists()


def test_cancelling_twice_is_refused(as_mathis, settlement):
    settlements.cancel_settlement(settlement, reason="erreur")
    reponse = as_mathis.get(f"/comptes/reglements/{settlement.pk}/annuler/")
    assert reponse.status_code == 302
    assert Settlement.objects.count() == 2


def test_sidebar_links_to_accounts(as_mathis):
    page = as_mathis.get("/").content.decode()
    assert 'href="/comptes/"' in page


def envoyer(client, axel, mathis, **champs):
    donnees = {
        "amount": "100",
        "date": "2026-11-03",
        "from_partner": axel.pk,
        "to_partner": mathis.pk,
        "seen_due": "205,44",
        **champs,
    }
    return client.post("/comptes/reglements/ajouter/", donnees)


def test_second_partial_settlement_from_another_tab_is_refused(
    as_mathis, axel_owes, mathis, axel
):
    assert envoyer(as_mathis, axel, mathis).status_code == 302
    reponse = envoyer(as_mathis, axel, mathis)

    assert reponse.status_code == 200
    assert "Le solde a changé depuis l&#x27;ouverture de la page" in reponse.content.decode()
    assert Settlement.objects.count() == 1


def test_settlement_in_the_wrong_direction_is_refused_on_screen(
    as_mathis, axel_owes, mathis, axel
):
    reponse = envoyer(as_mathis, mathis, axel)
    assert "Le solde a changé" in reponse.content.decode()
    assert not Settlement.objects.exists()


def test_settlement_when_settled_is_refused_on_screen(as_mathis, mathis, axel):
    reponse = envoyer(as_mathis, axel, mathis)
    assert "il n&#x27;y a rien à régler" in reponse.content.decode()
    assert not Settlement.objects.exists()


def test_settlement_form_with_missing_hidden_fields_asks_to_reload(
    as_mathis, axel_owes, mathis, axel
):
    reponse = envoyer(as_mathis, axel, mathis, seen_due="")
    assert "Cette page n&#x27;est plus à jour. Recharge-la." in reponse.content.decode()
    assert not Settlement.objects.exists()


def test_preview_shows_the_refusal_above_what_is_owed(as_mathis, axel_owes):
    apercu = as_mathis.post("/comptes/reglements/apercu/", {"amount": "300"}).content.decode()
    assert "ne peut pas dépasser ce montant" in apercu


def test_cancelling_twice_by_post_is_refused(as_mathis, settlement):
    url = f"/comptes/reglements/{settlement.pk}/annuler/"
    as_mathis.post(url, {"reason": "erreur"})
    reponse = as_mathis.post(url, {"reason": "erreur"})

    assert reponse.status_code == 302
    assert Settlement.objects.count() == 2


def test_read_only_hides_settlement_actions(as_mathis, settlement, settings):
    settings.READ_ONLY = True
    page = as_mathis.get("/comptes/").content.decode()

    assert "Enregistrer le règlement" not in page
    assert "/annuler/" not in page


def test_settlement_dated_in_the_future_is_refused_on_screen(
    as_mathis, axel_owes, mathis, axel
):
    demain = (timezone.localdate() + dt.timedelta(days=1)).isoformat()
    reponse = envoyer(as_mathis, axel, mathis, date=demain)

    assert "ne peut pas être daté après aujourd&#x27;hui" in reponse.content.decode()
    assert not Settlement.objects.exists()


def test_settlement_form_offers_a_back_button(as_mathis, axel_owes):
    page = as_mathis.get("/comptes/reglements/ajouter/").content.decode()
    assert 'class="btn-secondary">Retour</a>' in page
    assert 'class="btn-secondary">Annuler</a>' not in page
    assert f'max="{timezone.localdate().isoformat()}"' in page


def test_odd_vehicle_number_is_ignored(as_mathis, axel_owes):
    """« ² » passe `isdigit()` mais pas `int()` : le formulaire s'ouvre sans fiche de retour."""
    reponse = as_mathis.get("/comptes/reglements/ajouter/?vehicule=²")
    assert reponse.status_code == 200
    assert reponse.context["return_vehicle"] is None

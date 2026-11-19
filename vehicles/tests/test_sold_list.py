"""Tests de la page « Véhicules vendus »."""

import datetime as dt
from decimal import Decimal as D

import pytest

from accounting.services import Payment
from core.formatting import NBSP
from sales import services as sale_services
from sales.tests.helpers import record_deposit_for
from vehicles import services
from vehicles.models import Vehicle

Status = Vehicle.Status


@pytest.fixture
def buy(make_vehicle, mathis):
    """Fabrique un véhicule acheté 1 500 € par Mathis (statut « Acheté »)."""

    def acheter(**champs):
        vehicule = make_vehicle(**champs)
        services.record_purchase(
            vehicule,
            price=D("1500"),
            date=dt.date(2026, 10, 1),
            payments=[Payment(mathis, D("1500"))],
            seller=None,
            partner=mathis,
        )
        vehicule.refresh_from_db()
        return vehicule

    return acheter


def sell(vehicule, partner, price="2600", date="2026-10-30"):
    vente = sale_services.record_sale(
        vehicule,
        price=D(price),
        date=date,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(partner, D(price))],
        partner=partner,
    )
    vehicule.refresh_from_db()
    return vente


def test_empty_page_invites_instead_of_apologizing(as_mathis):
    response = as_mathis.get("/vendus/")
    assert response.status_code == 200
    contenu = response.content.decode()
    assert "Aucun véhicule vendu pour l'instant" in contenu
    assert response.context["cards"] == []


def test_page_lists_sold_vehicles_most_recent_sale_first(as_mathis, buy, mathis):
    ancien = buy(brand="Honda", model="CRF")
    recent = buy(brand="Yamaha", model="YZF")
    sell(ancien, mathis, date="2026-10-10")
    sell(recent, mathis, date="2026-10-25")
    response = as_mathis.get("/vendus/")
    assert [c["vehicle"] for c in response.context["cards"]] == [recent, ancien]
    contenu = response.content.decode()
    assert f'href="/vehicules/{recent.number}/"' in contenu
    assert "Vendu le 25/10/2026" in contenu
    assert "Vendu le 10/10/2026" in contenu


def test_page_leaves_out_vehicles_that_are_not_sold(as_mathis, buy, make_vehicle, mathis):
    vendu = buy(brand="Honda", model="CRF")
    sell(vendu, mathis)
    buy(brand="Yamaha", model="AuGarage")
    make_vehicle(brand="Kawasaki", model="Abandonnee", status=Status.ABANDONED)
    cartes = as_mathis.get("/vendus/").context["cards"]
    assert [c["vehicle"] for c in cartes] == [vendu]


def test_page_leaves_out_a_deleted_vehicle(as_mathis, buy, mathis):
    vehicule = buy(brand="Honda", model="CRF")
    sell(vehicule, mathis)
    vehicule.soft_delete()
    assert as_mathis.get("/vendus/").context["cards"] == []


def test_page_leaves_out_a_vehicle_whose_sale_was_cancelled(as_mathis, buy, mathis):
    vehicule = buy(brand="Honda", model="CRF")
    vente = sell(vehicule, mathis)
    sale_services.cancel_sale(vente, None, mathis)
    vehicule.refresh_from_db()
    assert vehicule.status == Status.FOR_SALE
    assert as_mathis.get("/vendus/").context["cards"] == []


def test_card_shows_price_margin_and_each_partners_gain(as_mathis, buy, mathis):
    sell(buy(brand="Honda", model="CRF"), mathis, price="2600")
    contenu = as_mathis.get("/vendus/").content.decode()
    assert f"2{NBSP}600,00{NBSP}€" in contenu
    assert "Marge" in contenu
    assert f"1{NBSP}100,00{NBSP}€" in contenu
    assert f"550,00{NBSP}€" in contenu
    assert "Pour Mathis" in contenu
    assert "Pour Axel" in contenu
    assert "Perte" not in contenu


def test_card_shows_a_loss_without_a_minus_sign(as_mathis, buy, mathis):
    sell(buy(brand="Honda", model="CRF"), mathis, price="1200")
    contenu = as_mathis.get("/vendus/").content.decode()
    assert f"Perte de 300,00{NBSP}€" in contenu
    assert "Perte pour Mathis" in contenu
    assert f"150,00{NBSP}€" in contenu
    assert f"-300,00{NBSP}€" not in contenu
    assert f"-150,00{NBSP}€" not in contenu


def test_card_counts_a_kept_deposit_in_the_margin(as_mathis, buy, mathis):
    """D32 : un acompte conservé s'ajoute à la marge (2 600 + 200 − 1 500 = 1 300 €)."""
    vehicule = buy(brand="Honda", model="CRF")
    acompte = record_deposit_for(
        vehicule,
        amount=D("200"),
        date=dt.date(2026, 10, 20),
        received_by=mathis,
        buyer=None,
        partner=mathis,
    )
    sale_services.cancel_deposit(acompte, "KEPT", mathis)
    vehicule.refresh_from_db()
    sell(vehicule, mathis, price="2600")
    contenu = as_mathis.get("/vendus/").content.decode()
    assert f"1{NBSP}300,00{NBSP}€" in contenu
    assert f"650,00{NBSP}€" in contenu
    fiche = as_mathis.get(f"/vehicules/{vehicule.number}/").content.decode()
    assert f"1{NBSP}300,00{NBSP}€" in fiche


def test_card_without_two_active_partners_shows_the_margin_only(as_mathis, buy, mathis, axel):
    """Sans partage possible entre deux associés, la carte n'affiche pas de gain."""
    sell(buy(brand="Honda", model="CRF"), mathis, price="2600")
    axel.is_active = False
    axel.save()
    contenu = as_mathis.get("/vendus/").content.decode()
    assert f"1{NBSP}100,00{NBSP}€" in contenu
    assert "Pour Mathis" not in contenu


def test_sidebar_and_home_lead_to_the_page(as_mathis, buy, mathis):
    sell(buy(brand="Honda", model="CRF"), mathis)
    page = as_mathis.get("/vendus/").content.decode()
    assert 'aria-current="page"' in page
    accueil = as_mathis.get("/").content.decode()
    assert "1 vendu" in accueil
    assert accueil.count('href="/vendus/"') == 2

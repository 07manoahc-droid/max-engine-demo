"""Tests de la page « Véhicules abandonnés » (2.0, sous-étape 3) : la perte de chaque
véhicule, lue dans le bilan (D59), en lecture seule."""

import datetime as dt
from decimal import Decimal as D

import pytest

from accounting import queries
from accounting.services import (
    GROUP_ABANDONED,
    Payment,
    VehicleCost,
    VehicleReportRow,
    abandoned_display,
)
from core.formatting import format_euros
from sales import services as sale_services
from sales.models import Deposit
from vehicles import services
from vehicles.models import StatusChange, Vehicle

Status = Vehicle.Status


def acheter(vehicule, partner, prix="1000"):
    services.record_purchase(
        vehicule,
        price=D(prix),
        date=dt.date(2026, 10, 1),
        payments=[Payment(partner, D(prix))],
        seller=None,
        partner=partner,
    )
    vehicule.refresh_from_db()
    return vehicule


def abandonner(vehicule, quand=None):
    services.change_status(vehicule, Status.ABANDONED, None)
    if quand is not None:
        StatusChange.objects.filter(vehicle=vehicule, to_status=Status.ABANDONED).update(
            changed_at=dt.datetime.combine(quand, dt.time(12), tzinfo=dt.UTC)
        )
    vehicule.refresh_from_db()
    return vehicule


def ligne(cout, conserve="0"):
    perte = D(cout) - D(conserve)
    return VehicleReportRow(
        vehicle=1,
        group=GROUP_ABANDONED,
        cost=VehicleCost(total=D(cout)),
        kept_deposits=D(conserve),
        loss=perte,
    )


# ---------------------------------------------------------------------------
# La perte prête à afficher (fonction pure)
# ---------------------------------------------------------------------------


@pytest.mark.finance
def test_loss_is_split_in_half():
    """Cas 23 : achat 1 000 €, acompte de 300 € conservé : perte 700 €, 350 € chacun."""
    affiche = abandoned_display(ligne("1000.00", "300.00"), ["m", "a"])
    assert (affiche.cost, affiche.kept_deposits) == (D("1000.00"), D("300.00"))
    assert affiche.result.is_loss
    assert affiche.result.shown_margin == D("700.00")
    assert dict(affiche.result.shown_shares) == {"m": D("350.00"), "a": D("350.00")}


@pytest.mark.finance
def test_odd_loss_keeps_every_cent():
    affiche = abandoned_display(ligne("100.01"), ["m", "a"])
    assert sum(affiche.result.shown_shares.values()) == D("100.01")


@pytest.mark.finance
def test_kept_deposit_above_the_cost_is_a_gain():
    affiche = abandoned_display(ligne("200.00", "250.00"), ["m", "a"])
    assert not affiche.result.is_loss
    assert affiche.result.shown_margin == D("50.00")


@pytest.mark.finance
def test_vehicle_without_money_has_no_loss_and_no_partner_no_share():
    vide = abandoned_display(None, ["m", "a"])
    assert (vide.cost, vide.result.shown_margin, vide.result.is_loss) == (D("0"), D("0"), False)
    assert abandoned_display(ligne("300.00"), []).result.shown_shares == {}


# ---------------------------------------------------------------------------
# La page
# ---------------------------------------------------------------------------


def test_empty_page_invites_instead_of_apologizing(as_mathis):
    reponse = as_mathis.get("/abandonnes/")
    assert reponse.status_code == 200
    assert "Aucun véhicule abandonné." in reponse.content.decode()
    assert reponse.context["cards"] == []


def test_page_lists_only_abandoned_vehicles_most_recent_first(
    as_mathis, make_vehicle, mathis, axel
):
    ancien = abandonner(acheter(make_vehicle(brand="GG", model="250"), mathis), dt.date(2026, 3, 1))
    recent = abandonner(
        acheter(make_vehicle(brand="Honda", model="CR"), axel), dt.date(2026, 9, 1)
    )
    acheter(make_vehicle(brand="Yamaha", model="AuGarage"), mathis)
    jete = make_vehicle(brand="Sherco", model="80", status=Status.ABANDONED)
    services.trash_vehicle(jete)

    reponse = as_mathis.get("/abandonnes/")
    assert [c["card"].vehicle for c in reponse.context["cards"]] == [recent, ancien]
    contenu = reponse.content.decode()
    assert "Abandonné le 01/09/2026" in contenu
    assert "Abandonné le 01/03/2026" in contenu
    assert "2 véhicules" in contenu


@pytest.mark.finance
def test_card_gives_the_loss_of_the_report(as_mathis, make_vehicle, mathis):
    """Cas 23 de bout en bout : la carte et le total donnent la perte du bilan."""
    gg = acheter(make_vehicle(brand="GG", model="250"), mathis)
    Deposit.objects.create(vehicle=gg, status=Deposit.Status.KEPT)
    acompte = Deposit.objects.get()
    acompte.receipts.create(partner=mathis, amount=D("300"))
    abandonner(gg)

    reponse = as_mathis.get("/abandonnes/")
    (item,) = reponse.context["cards"]
    assert item["card"].loss.result.shown_margin == D("700.00")
    assert [(p.name, montant) for p, montant in item["shares"]] == [
        ("Mathis", D("350.00")),
        ("Axel", D("350.00")),
    ]
    rapport = queries.load_report()
    assert reponse.context["total"].shown_margin == rapport.buckets.losses == D("700.00")
    contenu = reponse.content.decode()
    assert format_euros(D("700.00")) in contenu
    assert "Perte pour Mathis" in contenu
    assert f"Acompte conservé déduit : {format_euros(D('300.00'))}" in contenu


def test_abandoned_vehicle_without_money_shows_no_loss(as_mathis, make_vehicle):
    make_vehicle(brand="Kawasaki", model="KLX", status=Status.ABANDONED)
    contenu = as_mathis.get("/abandonnes/").content.decode()
    assert "Aucune perte" in contenu
    assert "Perte pour" not in contenu


def test_vehicle_back_in_the_garage_leaves_the_list(as_mathis, make_vehicle, mathis):
    gg = abandonner(acheter(make_vehicle(brand="GG", model="250"), mathis))
    services.change_status(gg, Status.IN_REPAIR, None)
    assert as_mathis.get("/abandonnes/").context["cards"] == []


def test_sold_vehicle_never_shows_even_with_an_abandoned_status(as_mathis, make_vehicle, mathis):
    """Une vente non annulée l'emporte sur le statut (D22)."""
    gg = acheter(make_vehicle(brand="GG", model="250"), mathis)
    sale_services.record_sale(
        gg,
        price=D("1200"),
        date=dt.date(2026, 10, 20),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1200"))],
        partner=mathis,
    )
    Vehicle.objects.filter(pk=gg.pk).update(status=Status.ABANDONED)
    assert as_mathis.get("/abandonnes/").context["cards"] == []


def test_sidebar_links_to_the_page(as_mathis):
    page = as_mathis.get("/abandonnes/").content.decode()
    assert 'href="/abandonnes/"' in page
    assert ":title=\"replie ? 'Abandonnés' : null\"" in page
    assert 'aria-current="page"' in page


def test_page_has_no_action(as_mathis, make_vehicle, mathis, settings):
    """Lecture seule, même hors consultation : aucun formulaire, aucun bouton bleu."""
    abandonner(acheter(make_vehicle(brand="GG", model="250"), mathis))
    contenu = as_mathis.get("/abandonnes/").content.decode()
    principal = contenu.split("<main", 1)[1]
    assert 'method="post"' not in principal
    assert "btn-primary" not in principal
    settings.READ_ONLY = True
    assert as_mathis.get("/abandonnes/").status_code == 200


# ---------------------------------------------------------------------------
# Relecture
# ---------------------------------------------------------------------------


@pytest.mark.finance
def test_vehicle_abandoned_after_a_cancelled_sale_shows_its_loss(as_mathis, make_vehicle, mathis):
    """D34 et D22 : la vente annulée ne compte plus, le véhicule abandonné perd son coût."""
    gg = acheter(make_vehicle(brand="GG", model="250"), mathis)
    vente = sale_services.record_sale(
        gg,
        price=D("1200"),
        date=dt.date(2026, 10, 20),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1200"))],
        partner=mathis,
    )
    sale_services.cancel_sale(vente, None, mathis)
    gg.refresh_from_db()
    abandonner(gg)
    (item,) = as_mathis.get("/abandonnes/").context["cards"]
    assert item["card"].vehicle == gg
    assert item["card"].loss.result.shown_margin == D("1000.00")


@pytest.mark.finance
def test_trashed_abandoned_vehicle_with_money_is_out_of_the_cards_and_the_total(
    as_mathis, make_vehicle, mathis
):
    garde = abandonner(acheter(make_vehicle(brand="GG", model="250"), mathis, "400"))
    jete = abandonner(acheter(make_vehicle(brand="Honda", model="CR"), mathis, "900"))
    Vehicle.all_objects.filter(pk=jete.pk).update(
        deleted_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
    )
    reponse = as_mathis.get("/abandonnes/")
    assert [c["card"].vehicle for c in reponse.context["cards"]] == [garde]
    assert reponse.context["total"].shown_margin == D("400.00")


@pytest.mark.finance
def test_total_of_two_vehicles_is_the_loss_of_the_report(as_mathis, make_vehicle, mathis, axel):
    abandonner(acheter(make_vehicle(brand="GG", model="250"), mathis, "400"))
    abandonner(acheter(make_vehicle(brand="Honda", model="CR"), axel, "900.01"))
    total = as_mathis.get("/abandonnes/").context["total"]
    assert total.is_loss
    assert total.shown_margin == queries.load_report().buckets.losses == D("1300.01")


def test_without_status_change_the_date_is_the_sheet_creation(as_mathis, make_vehicle):
    """Repli du bilan (D59) : une fiche importée déjà abandonnée n'a pas de changement de
    statut ; sa date est celle de sa création."""
    klx = make_vehicle(brand="Kawasaki", model="KLX", status=Status.ABANDONED)
    Vehicle.objects.filter(pk=klx.pk).update(created_at=dt.datetime(2026, 2, 3, 12, tzinfo=dt.UTC))
    assert "Abandonné le 03/02/2026" in as_mathis.get("/abandonnes/").content.decode()


def test_gain_is_worded_as_a_gain(as_mathis, make_vehicle, mathis):
    gg = acheter(make_vehicle(brand="GG", model="250"), mathis, "200")
    acompte = Deposit.objects.create(vehicle=gg, status=Deposit.Status.KEPT)
    acompte.receipts.create(partner=mathis, amount=D("250"))
    abandonner(gg)
    contenu = as_mathis.get("/abandonnes/").content.decode()
    assert ">Gain</p>" in contenu
    assert ">Perte</p>" not in contenu
    assert "gain de tous les abandons" in contenu
    assert "Pour Mathis" in contenu

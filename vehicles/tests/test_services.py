"""Tests des services des véhicules : statuts permis, création, changement de statut, achat."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounting import queries
from accounting.services import Payment
from core.models import Contact, ExpenseCategory
from expenses.models import Expense, ExpenseLine, ExpensePayment
from vehicles import services
from vehicles.models import StatusChange, Vehicle

Status = Vehicle.Status
JOUR = dt.date(2026, 10, 20)


# ---------------------------------------------------------------------------
# Statuts permis (D35) : fonction pure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actuel", "achete", "attendu"),
    [
        (Status.WAITING_PURCHASE, False, [Status.ABANDONED]),
        (Status.ABANDONED, False, [Status.WAITING_PURCHASE]),
        # Achat mis à la corbeille (saisi par erreur) : retour possible en attente d'achat.
        (Status.PURCHASED, False, [Status.WAITING_PURCHASE, Status.ABANDONED]),
        (
            Status.PURCHASED,
            True,
            [Status.IN_REPAIR, Status.READY_FOR_SALE, Status.FOR_SALE, Status.ABANDONED],
        ),
        (
            Status.FOR_SALE,
            True,
            [Status.PURCHASED, Status.IN_REPAIR, Status.READY_FOR_SALE, Status.ABANDONED],
        ),
        (
            Status.ABANDONED,
            True,
            [Status.PURCHASED, Status.IN_REPAIR, Status.READY_FOR_SALE, Status.FOR_SALE],
        ),
        (Status.RESERVED, True, []),
        (Status.SOLD, True, []),
    ],
)
def test_allowed_statuses(actuel, achete, attendu):
    assert services.allowed_statuses(actuel, achete) == attendu


@pytest.mark.parametrize("actuel", list(Status))
def test_allowed_statuses_never_offer_reserved_sold_or_waiting_after_purchase(actuel):
    """Réservé et Vendu passent par l'acompte et la vente ; pas de retour avant l'achat."""
    interdits = {Status.RESERVED, Status.SOLD, Status.WAITING_PURCHASE}
    assert not interdits & set(services.allowed_statuses(actuel, purchased=True))
    assert not {Status.RESERVED, Status.SOLD} & set(services.allowed_statuses(actuel, False))


# ---------------------------------------------------------------------------
# Création et changement de statut
# ---------------------------------------------------------------------------


@pytest.fixture
def new_vehicle(make_vehicle, mathis):
    """Véhicule enregistré par le service, comme le fait l'écran « Ajouter un véhicule »."""
    moto = make_vehicle().vehicle_type
    return services.create_vehicle(Vehicle(vehicle_type=moto, brand="GG", model="MC250"), mathis)


def test_create_vehicle_starts_waiting_purchase_and_traces_it(new_vehicle, mathis):
    assert new_vehicle.status == Status.WAITING_PURCHASE
    assert new_vehicle.number == 2
    changement = StatusChange.objects.get(vehicle=new_vehicle)
    assert (changement.from_status, changement.to_status) == ("", Status.WAITING_PURCHASE)
    assert changement.changed_by == mathis


def test_create_vehicle_ignores_a_status_given_by_mistake(make_vehicle, axel):
    moto = make_vehicle().vehicle_type
    vehicule = Vehicle(vehicle_type=moto, brand="Sherco", model="80", status=Status.SOLD)
    assert services.create_vehicle(vehicule, axel).status == Status.WAITING_PURCHASE


def test_change_status_traces_the_change(make_vehicle, buy, axel):
    vehicule = buy(make_vehicle())
    services.change_status(vehicule, Status.IN_REPAIR, axel)

    vehicule.refresh_from_db()
    assert vehicule.status == Status.IN_REPAIR
    dernier = vehicule.status_changes.last()
    assert (dernier.from_status, dernier.to_status) == (Status.PURCHASED, Status.IN_REPAIR)
    assert dernier.changed_by == axel


def test_change_status_to_for_sale_sets_listed_on_once(make_vehicle, buy, mathis):
    vehicule = buy(make_vehicle())
    services.change_status(vehicule, Status.FOR_SALE, mathis)
    assert vehicule.listed_on == timezone.localdate()

    vehicule.listed_on = JOUR
    vehicule.save()
    services.change_status(vehicule, Status.IN_REPAIR, mathis)
    services.change_status(vehicule, Status.FOR_SALE, mathis)
    assert vehicule.listed_on == JOUR


@pytest.mark.parametrize(
    ("statut", "nouveau", "message"),
    [
        (Status.WAITING_PURCHASE, Status.PURCHASED, "enregistre l'achat"),
        (Status.WAITING_PURCHASE, Status.IN_REPAIR, "n'est pas possible"),
        (Status.SOLD, Status.FOR_SALE, "annuler la vente"),
        (Status.RESERVED, Status.FOR_SALE, "annuler l'acompte"),
        (Status.ABANDONED, Status.SOLD, "acompte ou une vente"),
        (Status.WAITING_PURCHASE, Status.WAITING_PURCHASE, "déjà ce statut"),
    ],
)
def test_change_status_refuses_with_an_explanation(make_vehicle, mathis, statut, nouveau, message):
    vehicule = make_vehicle(status=statut)
    with pytest.raises(ValidationError, match=message):
        services.change_status(vehicule, nouveau, mathis)
    assert not vehicule.status_changes.exists()


def test_purchased_vehicle_cannot_go_back_to_waiting_purchase(make_vehicle, buy, mathis):
    """D35 : la dépense d'achat existe, le retour en attente d'achat est refusé."""
    vehicule = buy(make_vehicle())
    with pytest.raises(ValidationError, match="ne peut plus revenir en attente d'achat"):
        services.change_status(vehicule, Status.WAITING_PURCHASE, mathis)


def test_purchase_in_trash_no_longer_counts(make_vehicle, buy, mathis):
    vehicule = buy(make_vehicle())
    Expense.objects.get().soft_delete()
    assert not services.has_purchase(vehicule)


def test_garage_vehicles_excludes_sold_and_abandoned(make_vehicle):
    gardes = [make_vehicle(status=s) for s in Status if s not in services.NOT_IN_GARAGE]
    make_vehicle(status=Status.SOLD)
    make_vehicle(status=Status.ABANDONED)
    make_vehicle(status=Status.IN_REPAIR).soft_delete()
    assert list(services.garage_vehicles()) == gardes


# ---------------------------------------------------------------------------
# Achat
# ---------------------------------------------------------------------------


@pytest.fixture
def buy(mathis):
    """Enregistre l'achat d'un véhicule (1 500 € payés par Mathis) et le renvoie."""

    def acheter(vehicule, price="1500", payments=None, seller=None):
        payments = payments or [Payment(mathis, D(price))]
        services.record_purchase(
            vehicule, price=D(price), date=JOUR, payments=payments, seller=seller, partner=mathis
        )
        vehicule.refresh_from_db()
        return vehicule

    return acheter


def test_record_purchase_creates_the_expense_and_marks_purchased(make_vehicle, buy, mathis, axel):
    paiements = [Payment(mathis, D("750.01")), Payment(axel, D("750.00"))]
    vehicule = buy(make_vehicle(), price="1500.01", payments=paiements)

    depense = Expense.objects.get()
    assert (depense.date, depense.total_paid, depense.is_personal) == (JOUR, D("1500.01"), False)
    ligne = ExpenseLine.objects.get()
    assert ligne.expense == depense and ligne.vehicle == vehicule
    assert ligne.category.code == ExpenseCategory.VEHICLE_PURCHASE
    assert (ligne.label, ligne.quantity, ligne.unit_price) == (
        "Achat du véhicule",
        D("1"),
        D("1500.01"),
    )
    assert ligne.status == ""
    assert {(p.partner, p.amount) for p in ExpensePayment.objects.all()} == {
        (mathis, D("750.01")),
        (axel, D("750.00")),
    }
    assert vehicule.status == Status.PURCHASED
    assert vehicule.purchased_on == JOUR
    dernier = vehicule.status_changes.last()
    assert (dernier.from_status, dernier.to_status) == (Status.WAITING_PURCHASE, Status.PURCHASED)


def test_record_purchase_creates_a_new_seller(make_vehicle, buy):
    vehicule = buy(make_vehicle(), seller=Contact(name="Jean Dupont", city="Lyon"))
    assert vehicule.seller == Contact.objects.get(name="Jean Dupont")


def test_record_purchase_keeps_an_existing_seller(make_vehicle, buy):
    connu = Contact.objects.create(name="Jean Dupont")
    vehicule = buy(make_vehicle(), seller=connu)
    assert vehicule.seller == connu
    assert Contact.objects.count() == 1


def test_record_purchase_without_seller_keeps_the_one_already_set(make_vehicle, buy):
    connu = Contact.objects.create(name="Jean Dupont")
    vehicule = buy(make_vehicle(seller=connu))
    assert vehicule.seller == connu


@pytest.mark.parametrize(
    ("price", "montants", "message"),
    [
        ("0", None, "plus grand que 0"),
        ("1500", ("1000", "400"), "Les parts payées font"),
    ],
)
def test_record_purchase_refuses_wrong_amounts_and_saves_nothing(
    make_vehicle, mathis, axel, price, montants, message
):
    vehicule = make_vehicle()
    paiements = (
        [Payment(mathis, D(montants[0])), Payment(axel, D(montants[1]))]
        if montants
        else [Payment(mathis, D(price))]
    )
    with pytest.raises(ValidationError, match=message):
        services.record_purchase(
            vehicule,
            price=D(price),
            date=JOUR,
            payments=paiements,
            seller=Contact(name="Jean"),
            partner=mathis,
        )
    _assert_nothing_saved(vehicule)


def test_record_purchase_refuses_an_inactive_partner(make_vehicle, mathis):
    mathis.is_active = False
    mathis.save()
    vehicule = make_vehicle()
    with pytest.raises(ValidationError, match="plus un associé actif"):
        services.record_purchase(
            vehicule,
            price=D("100"),
            date=JOUR,
            payments=[Payment(mathis, D("100"))],
            seller=None,
            partner=None,
        )
    _assert_nothing_saved(vehicule)


@pytest.mark.parametrize("statut", [s for s in Status if s != Status.WAITING_PURCHASE])
def test_record_purchase_only_when_waiting_purchase(make_vehicle, buy, statut):
    vehicule = make_vehicle(status=statut)
    with pytest.raises(ValidationError, match="en attente d'achat"):
        buy(vehicule)
    assert not Expense.objects.exists()


def test_record_purchase_is_all_or_nothing(make_vehicle, mathis, monkeypatch):
    """Une erreur au milieu de l'enregistrement n'en laisse aucune trace."""
    vehicule = make_vehicle()

    def panne(*args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(services, "_record_status_change", panne)
    with pytest.raises(RuntimeError):
        services.record_purchase(
            vehicule,
            price=D("100"),
            date=JOUR,
            payments=[Payment(mathis, D("100"))],
            seller=Contact(name="Jean"),
            partner=mathis,
        )
    _assert_nothing_saved(vehicule)


def _assert_nothing_saved(vehicule):
    assert not Expense.all_objects.exists()
    assert not ExpenseLine.all_objects.exists()
    assert not ExpensePayment.objects.exists()
    assert not Contact.all_objects.exists()
    vehicule.refresh_from_db()
    assert vehicule.status == Status.WAITING_PURCHASE
    assert vehicule.purchased_on is None


# ---------------------------------------------------------------------------
# Bandeau de l'accueil, lu en base
# ---------------------------------------------------------------------------


def test_garage_overview_after_purchases(make_vehicle, buy, mathis, axel):
    """Coût des véhicules au garage, comme le bilan ; un véhicule abandonné n'y compte
    plus, sa perte vient en moins du bénéfice de l'année (D59, D63, D66)."""
    premier = buy(make_vehicle(), price="1500")
    buy(make_vehicle(), price="800", payments=[Payment(axel, D("800"))])
    abandonne = buy(make_vehicle(), price="300")
    services.change_status(abandonne, Status.ABANDONED, mathis)

    apercu = queries.garage_overview(timezone.localdate().year)

    assert apercu.garage_cost == D("2300.00")
    assert apercu.in_trash == D("0.00")
    assert apercu.profit.amount == D("-300.00")
    assert apercu.costs[premier.pk].by_partner == {mathis.pk: D("1500.00")}


def test_garage_overview_counts_a_trashed_vehicle_like_the_report(make_vehicle, buy):
    """D66 : une fiche à la corbeille dont l'achat ne l'est pas garde son coût au garage,
    sur l'accueil comme dans le bilan, et l'accueil dit combien en vient."""
    buy(make_vehicle(), price="1500")
    jete = buy(make_vehicle(), price="200")
    jete.soft_delete()

    apercu = queries.garage_overview(timezone.localdate().year)

    assert apercu.garage_cost == D("1700.00")
    assert apercu.in_trash == D("200.00")
    assert apercu.garage_cost == queries.load_report().buckets.vehicles_in_garage


def test_record_purchase_twice_with_a_stale_vehicle_saves_one_purchase(make_vehicle, mathis):
    """Double-clic : deux envois avec le même véhicule chargé avant le premier achat."""
    vehicule = make_vehicle()
    perime = Vehicle.objects.get(pk=vehicule.pk)
    services.record_purchase(
        vehicule,
        price=D("100"),
        date=JOUR,
        payments=[Payment(mathis, D("100"))],
        seller=None,
        partner=mathis,
    )
    with pytest.raises(ValidationError, match="déjà enregistré"):
        services.record_purchase(
            perime,
            price=D("100"),
            date=JOUR,
            payments=[Payment(mathis, D("100"))],
            seller=None,
            partner=mathis,
        )
    assert Expense.objects.count() == 1
    assert vehicule.status_changes.filter(to_status=Status.PURCHASED).count() == 1


def test_record_purchase_refuses_when_a_purchase_line_already_exists(make_vehicle, buy, mathis):
    """Une dépense d'achat restaurée sur un véhicule revenu en attente : pas de second achat."""
    vehicule = buy(make_vehicle())
    depense = Expense.objects.get()
    depense.soft_delete()
    services.change_status(vehicule, Status.WAITING_PURCHASE, mathis)
    depense.restore()
    with pytest.raises(ValidationError, match="déjà une dépense « Achat véhicule »"):
        buy(vehicule)
    assert Expense.objects.count() == 1
    vehicule.refresh_from_db()
    assert vehicule.status == Status.WAITING_PURCHASE

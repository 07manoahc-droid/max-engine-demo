"""Tests de la logique des ventes : acomptes, statut « Réservé », vente et annulation."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounting import queries
from accounting import services as accounting
from core.models import Contact
from expenses.models import Expense
from sales import services
from sales.models import Deposit, DepositReceipt, Sale, SaleReceipt
from sales.tests.helpers import create_deposit, deposit_amount, record_deposit_for
from vehicles import services as vehicle_services
from vehicles.models import StatusChange, Vehicle

Status = Vehicle.Status
DepositStatus = Deposit.Status
LE_30 = dt.date(2026, 10, 30)


def take_deposit(vehicule, partner, amount="200", buyer=None):
    return record_deposit_for(
        vehicule, amount=D(amount), date=LE_30, received_by=partner, buyer=buyer, partner=partner
    )


def to_status(vehicule, status, partner):
    vehicle_services.change_status(vehicule, status, partner)


def sell(vehicule, receipts, partner, price="2600", **champs):
    """Vente au prix donné ; `receipts` : (associé, montant) ou liste de couples."""
    return services.record_sale(
        vehicule,
        price=D(price),
        date=champs.pop("date", LE_30),
        buyer=champs.pop("buyer", None),
        sold_where=champs.pop("sold_where", ""),
        notes=champs.pop("notes", ""),
        receipts=[accounting.Payment(associe, D(montant)) for associe, montant in receipts],
        partner=partner,
    )


def edit_sale(vente, receipts, price="2600", **champs):
    return services.update_sale(
        vente,
        price=D(price),
        date=champs.pop("date", LE_30),
        buyer=champs.pop("buyer", None),
        sold_where=champs.pop("sold_where", ""),
        notes=champs.pop("notes", ""),
        receipts=[accounting.Payment(associe, D(montant)) for associe, montant in receipts],
    )


# --- Enregistrer un acompte -----------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [Status.PURCHASED, Status.IN_REPAIR, Status.READY_FOR_SALE, Status.FOR_SALE],
)
def test_deposit_reserves_the_vehicle_from_each_allowed_status(purchased, mathis, status):
    """Acheté, En réparation, Prêt à vendre et En vente : l'acompte réserve le véhicule."""
    if status != purchased.status:
        to_status(purchased, status, mathis)

    acompte = take_deposit(purchased, mathis)

    purchased.refresh_from_db()
    assert purchased.status == Status.RESERVED
    assert acompte.status == DepositStatus.ACTIVE
    changement = purchased.status_changes.order_by("-id").first()
    assert (changement.from_status, changement.to_status) == (status, Status.RESERVED)
    assert changement.changed_by == mathis


def test_deposit_creates_a_new_buyer(purchased, mathis):
    acompte = take_deposit(purchased, mathis, buyer=Contact(name="Julie Martin"))
    assert acompte.buyer == Contact.objects.get(name="Julie Martin")


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (Status.WAITING_PURCHASE, "Enregistre d'abord l'achat"),
        (Status.ABANDONED, "Ce véhicule est abandonné"),
        (Status.SOLD, "Ce véhicule est vendu"),
    ],
)
def test_deposit_refused_from_other_statuses(purchased, mathis, status, message):
    Vehicle.objects.filter(pk=purchased.pk).update(status=status)
    purchased.refresh_from_db()
    with pytest.raises(ValidationError, match=message):
        take_deposit(purchased, mathis)
    assert not Deposit.objects.exists()


def test_deposit_refused_without_a_purchase(purchased, mathis):
    """Dépense d'achat à la corbeille : le véhicule n'a plus d'achat enregistré (D39)."""
    vehicle_services.purchase_lines(purchased).first().expense.soft_delete()
    with pytest.raises(ValidationError, match="Enregistre d'abord l'achat"):
        take_deposit(purchased, mathis)


def test_second_deposit_is_refused(purchased, mathis):
    take_deposit(purchased, mathis)
    with pytest.raises(ValidationError, match="déjà un acompte en cours"):
        take_deposit(purchased, mathis)
    assert Deposit.objects.count() == 1


def test_double_submit_records_one_deposit(purchased, mathis):
    """Deux envois partis de la même page : le second trouve l'acompte déjà en cours, même
    si le véhicule qu'il a lu est encore « Acheté »."""
    page_ouverte = Vehicle.objects.get(pk=purchased.pk)
    take_deposit(purchased, mathis)
    with pytest.raises(ValidationError, match="déjà un acompte en cours"):
        take_deposit(page_ouverte, mathis)
    assert Deposit.objects.count() == 1


def test_stale_vehicle_cannot_be_reserved_after_a_status_change(purchased, mathis):
    """Page ouverte avant l'abandon du véhicule : l'acompte est refusé, le statut ne bouge
    pas. Seule la garde en base lit le vrai statut : `page_ouverte` dit encore « Acheté »."""
    page_ouverte = Vehicle.objects.get(pk=purchased.pk)
    to_status(purchased, Status.ABANDONED, mathis)

    with pytest.raises(ValidationError, match="déjà changé de statut"):
        take_deposit(page_ouverte, mathis)

    purchased.refresh_from_db()
    assert purchased.status == Status.ABANDONED
    assert not Deposit.objects.exists()


def test_database_refuses_two_active_deposits(purchased, mathis):
    create_deposit(vehicle=purchased, amount=D("100"), received_by=mathis)
    with pytest.raises(IntegrityError), transaction.atomic():
        create_deposit(vehicle=purchased, amount=D("100"), received_by=mathis)


def test_zero_deposit_is_refused(purchased, mathis):
    with pytest.raises(ValidationError, match="plus grand que 0"):
        take_deposit(purchased, mathis, amount="0")
    purchased.refresh_from_db()
    assert purchased.status == Status.PURCHASED
    assert not Deposit.objects.exists()


def test_inactive_partner_cannot_receive_a_deposit(purchased, mathis, axel):
    axel.is_active = False
    axel.save()
    with pytest.raises(ValidationError, match="Axel n'est plus associé"):
        take_deposit(purchased, axel)
    assert not Deposit.objects.exists()


def test_deposit_received_by_both_partners(purchased, mathis, axel):
    """Un versement par associé, à la date de l'acompte (D89)."""
    acompte = services.record_deposit(
        purchased,
        date=LE_30,
        receipts=[accounting.Payment(mathis, D("200")), accounting.Payment(axel, D("100"))],
        buyer=None,
        partner=mathis,
    )
    versements = acompte.receipts.order_by("id")
    assert [(v.partner, v.amount, v.date) for v in versements] == [
        (mathis, D("200.00"), LE_30),
        (axel, D("100.00"), LE_30),
    ]
    assert deposit_amount(acompte) == D("300.00")
    assert acompte.legacy_amount is None and acompte.legacy_received_by is None


def test_same_partner_twice_is_refused(purchased, mathis):
    with pytest.raises(ValidationError, match="qu'une fois"):
        services.record_deposit(
            purchased,
            date=LE_30,
            receipts=[accounting.Payment(mathis, D("200")), accounting.Payment(mathis, D("1"))],
            buyer=None,
            partner=mathis,
        )
    assert not Deposit.objects.exists()


def test_correcting_a_receipt_keeps_the_old_value_in_history(purchased, mathis, axel):
    acompte = take_deposit(purchased, mathis)
    versement = acompte.receipts.get()

    services.update_deposit_receipt(versement, amount=D("350"), date=LE_30, received_by=axel)

    versement.refresh_from_db()
    assert (versement.amount, versement.partner) == (D("350.00"), axel)
    assert [h.amount for h in versement.history.order_by("history_id")] == [D("200"), D("350")]


def test_correcting_the_deposit_changes_its_date_and_buyer(purchased, mathis):
    acompte = take_deposit(purchased, mathis)
    julie = Contact(name="Julie Martin")
    services.update_deposit(acompte, date=dt.date(2026, 10, 29), buyer=julie)
    acompte.refresh_from_db()
    assert acompte.date == dt.date(2026, 10, 29)
    assert acompte.buyer == Contact.objects.get(name="Julie Martin")
    assert deposit_amount(acompte) == D("200.00")


def test_correction_keeps_a_former_partner(purchased, mathis, axel):
    """Un ancien associé reste possible sur un versement qu'il avait déjà reçu, pas sur un
    autre."""
    acompte = take_deposit(purchased, axel)
    axel.is_active = False
    axel.save()
    versement = acompte.receipts.get()
    services.update_deposit_receipt(versement, amount=D("350"), date=LE_30, received_by=axel)
    versement.refresh_from_db()
    assert versement.amount == D("350.00")
    with pytest.raises(ValidationError, match="Axel n'est plus associé"):
        services.add_deposit_receipts(
            acompte, date=LE_30, receipts=[accounting.Payment(axel, D("10"))]
        )


def test_cancelled_deposit_cannot_be_corrected(purchased, mathis):
    acompte = take_deposit(purchased, mathis)
    versement = acompte.receipts.get()
    services.cancel_deposit(acompte, DepositStatus.REFUNDED, mathis)
    with pytest.raises(ValidationError, match="plus en cours"):
        services.update_deposit(acompte, date=LE_30, buyer=None)
    with pytest.raises(ValidationError, match="plus en cours"):
        services.update_deposit_receipt(versement, amount=D("1"), date=LE_30, received_by=mathis)
    with pytest.raises(ValidationError, match="plus en cours"):
        services.add_deposit_receipts(
            acompte, date=LE_30, receipts=[accounting.Payment(mathis, D("1"))]
        )


# --- Versements (D89) -----------------------------------------------------------


def test_adding_receipts_completes_the_deposit(purchased, mathis, axel):
    acompte = take_deposit(purchased, mathis)
    ajoutes = services.add_deposit_receipts(
        acompte,
        date=dt.date(2026, 11, 2),
        receipts=[accounting.Payment(mathis, D("50")), accounting.Payment(axel, D("100"))],
    )
    assert [(v.partner, v.amount) for v in ajoutes] == [(mathis, D("50")), (axel, D("100"))]
    assert deposit_amount(acompte) == D("350.00")
    purchased.refresh_from_db()
    assert purchased.status == Status.RESERVED


def test_stale_page_cannot_add_to_a_deposit_applied_since(purchased, mathis):
    """Page ouverte avant la vente : le complément est refusé, rien n'est ajouté."""
    acompte = take_deposit(purchased, mathis)
    page_ouverte = Deposit.objects.get(pk=acompte.pk)
    sell(purchased, [(mathis, "2400")], mathis)
    with pytest.raises(ValidationError, match="plus en cours"):
        services.add_deposit_receipts(
            page_ouverte, date=LE_30, receipts=[accounting.Payment(mathis, D("10"))]
        )
    assert DepositReceipt.objects.count() == 1


def test_removing_a_receipt_sends_it_to_the_trash(purchased, mathis, axel):
    acompte = take_deposit(purchased, mathis)
    (ajoute,) = services.add_deposit_receipts(
        acompte, date=LE_30, receipts=[accounting.Payment(axel, D("100"))]
    )
    services.remove_deposit_receipt(ajoute)
    ajoute.refresh_from_db()
    assert ajoute.is_deleted
    assert deposit_amount(acompte) == D("200.00")
    # Un second clic : déjà retiré.
    with pytest.raises(ValidationError, match="déjà été retiré"):
        services.remove_deposit_receipt(ajoute)


def test_last_receipt_cannot_be_removed(purchased, mathis):
    acompte = take_deposit(purchased, mathis)
    with pytest.raises(ValidationError, match="seul versement"):
        services.remove_deposit_receipt(acompte.receipts.get())
    assert not acompte.receipts.get().is_deleted


def test_removed_receipt_cannot_be_corrected(purchased, mathis, axel):
    acompte = take_deposit(purchased, mathis)
    (ajoute,) = services.add_deposit_receipts(
        acompte, date=LE_30, receipts=[accounting.Payment(axel, D("100"))]
    )
    page_ouverte = DepositReceipt.objects.get(pk=ajoute.pk)
    services.remove_deposit_receipt(ajoute)
    with pytest.raises(ValidationError, match="déjà été retiré"):
        services.update_deposit_receipt(page_ouverte, amount=D("5"), date=LE_30, received_by=axel)
    page_ouverte.refresh_from_db()
    assert page_ouverte.is_deleted and page_ouverte.amount == D("100.00")


def test_sale_deducts_every_receipt(purchased, mathis, axel):
    """L'acompte de 300 € reçu par les deux est déduit en entier : il reste 2 300 €."""
    acompte = take_deposit(purchased, mathis)
    services.add_deposit_receipts(
        acompte, date=LE_30, receipts=[accounting.Payment(axel, D("100"))]
    )
    with pytest.raises(ValidationError) as erreur:
        sell(purchased, [(mathis, "2400")], mathis)
    assert "au lieu de 2 300,00" in erreur.value.messages[0]
    sell(purchased, [(mathis, "2300")], mathis)
    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.APPLIED


# --- Annuler un acompte ---------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [Status.PURCHASED, Status.IN_REPAIR, Status.READY_FOR_SALE, Status.FOR_SALE],
)
def test_cancelling_goes_back_to_the_status_before_reservation(purchased, mathis, status):
    """Le véhicule revient au statut d'avant la réservation, pas toujours en vente (D47)."""
    if status != purchased.status:
        to_status(purchased, status, mathis)
    acompte = take_deposit(purchased, mathis)

    services.cancel_deposit(acompte, DepositStatus.REFUNDED, mathis)

    purchased.refresh_from_db()
    assert purchased.status == status
    dernier = purchased.status_changes.order_by("-id").first()
    assert (dernier.from_status, dernier.to_status) == (Status.RESERVED, status)


def test_second_reservation_returns_to_the_latest_status(purchased, mathis):
    """Seule la dernière réservation compte, pas la première."""
    services.cancel_deposit(take_deposit(purchased, mathis), DepositStatus.REFUNDED, mathis)
    purchased.refresh_from_db()
    to_status(purchased, Status.FOR_SALE, mathis)

    services.cancel_deposit(take_deposit(purchased, mathis), DepositStatus.KEPT, mathis)

    purchased.refresh_from_db()
    assert purchased.status == Status.FOR_SALE


def test_status_before_reservation_defaults_to_for_sale(purchased):
    """Véhicule passé en « Réservé » sans trace de la réservation : retour en vente."""
    StatusChange.objects.filter(vehicle=purchased).delete()
    assert services.status_before_reservation(purchased) == Status.FOR_SALE


def test_stale_deposit_cannot_be_reactivated_by_a_correction(purchased, mathis):
    """Page ouverte avant l'annulation : la correction est refusée, l'acompte reste annulé."""
    acompte = take_deposit(purchased, mathis)
    page_ouverte = Deposit.objects.get(pk=acompte.pk)
    services.cancel_deposit(acompte, DepositStatus.REFUNDED, mathis)

    with pytest.raises(ValidationError, match="plus en cours"):
        services.update_deposit(page_ouverte, date=LE_30, buyer=None)
    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.REFUNDED
    assert deposit_amount(acompte) == D("200.00")


def test_stale_cancellation_keeps_a_corrected_date(purchased, mathis):
    """Page ouverte avant la correction : l'annulation n'écrase pas la nouvelle date."""
    acompte = take_deposit(purchased, mathis)
    page_ouverte = Deposit.objects.get(pk=acompte.pk)
    services.update_deposit(acompte, date=dt.date(2026, 10, 29), buyer=None)

    services.cancel_deposit(page_ouverte, DepositStatus.KEPT, mathis)

    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.KEPT
    assert acompte.date == dt.date(2026, 10, 29)


def test_cancel_outcome_applies_to_every_receipt(purchased, mathis, axel):
    """Conservé vaut pour tous les versements : chacun reste chez celui qui l'a reçu."""
    acompte = take_deposit(purchased, mathis)
    services.add_deposit_receipts(
        acompte, date=LE_30, receipts=[accounting.Payment(axel, D("100"))]
    )
    services.cancel_deposit(acompte, DepositStatus.KEPT, mathis)
    versements = queries.load_deposits([acompte.pk])
    assert {(v.partner, v.amount, v.status) for v in versements} == {
        (mathis.pk, D("200.00"), DepositStatus.KEPT),
        (axel.pk, D("100.00"), DepositStatus.KEPT),
    }


def test_reserved_vehicle_without_active_deposit_is_reported(purchased):
    Vehicle.objects.filter(pk=purchased.pk).update(status=Status.RESERVED)
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"{purchased.display_name} : réservé avec 0 acompte(s) en cours au lieu d'un seul"
    ]


def test_active_deposit_on_a_vehicle_not_reserved_is_reported(purchased, mathis):
    create_deposit(vehicle=purchased, amount=D("100"), received_by=mathis)
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"{purchased.display_name} : acompte en cours alors que le véhicule n'est pas réservé"
    ]


# --- Enregistrer une vente -------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [Status.PURCHASED, Status.IN_REPAIR, Status.READY_FOR_SALE, Status.FOR_SALE],
)
def test_sale_is_possible_from_each_allowed_status(purchased, mathis, status):
    if status != purchased.status:
        to_status(purchased, status, mathis)

    vente = sell(purchased, [(mathis, "2600")], mathis)

    purchased.refresh_from_db()
    assert purchased.status == Status.SOLD
    assert vente.receipts.get().amount == D("2600.00")
    changement = purchased.status_changes.order_by("-id").first()
    assert (changement.from_status, changement.to_status) == (status, Status.SOLD)


def test_sale_of_a_reserved_vehicle_applies_the_deposit(purchased, mathis, axel):
    """Cas 11 : l'acompte est déduit, seul le reste est encaissé."""
    acompte = take_deposit(purchased, mathis)
    purchased.refresh_from_db()

    vente = sell(purchased, [(axel, "2400")], axel)

    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.APPLIED
    assert acompte.sale == vente
    purchased.refresh_from_db()
    assert purchased.status == Status.SOLD
    assert queries.consistency_problems(queries.load_accounts()) == []


def test_sale_with_nothing_left_to_receive_has_no_receipt(purchased, mathis):
    """L'acompte couvre tout le prix : la vente n'a aucun encaissement."""
    take_deposit(purchased, mathis, amount="500")
    purchased.refresh_from_db()

    vente = sell(purchased, [], mathis, price="500")

    assert not vente.receipts.exists()
    assert queries.consistency_problems(queries.load_accounts()) == []


def test_sale_refuses_receipts_that_do_not_match_what_is_left(purchased, mathis):
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()

    with pytest.raises(ValidationError, match="Corrige l'un des montants"):
        sell(purchased, [(mathis, "2600")], mathis)

    # Tout ou rien : ni vente, ni changement de statut, acompte intact.
    purchased.refresh_from_db()
    assert purchased.status == Status.RESERVED
    assert not purchased.sales.exists()
    assert purchased.deposits.get().status == DepositStatus.ACTIVE


def test_sale_refuses_a_price_below_the_deposit(purchased, mathis):
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()
    with pytest.raises(ValidationError, match="Corrige le prix"):
        sell(purchased, [], mathis, price="150")


def test_sale_creates_the_new_buyer(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis, buyer=Contact(name="Paul"))
    assert vente.buyer.pk is not None


def test_failed_sale_does_not_create_the_buyer(purchased, mathis):
    with pytest.raises(ValidationError):
        sell(purchased, [(mathis, "100")], mathis, buyer=Contact(name="Paul"))
    assert not Contact.objects.filter(name="Paul").exists()


def test_sale_refused_for_an_inactive_partner(purchased, mathis, axel):
    axel.is_active = False
    axel.save()
    with pytest.raises(ValidationError, match="plus actif"):
        sell(purchased, [(axel, "2600")], mathis)


def test_sale_refusal(make_vehicle, purchased, mathis):
    attente = make_vehicle()
    assert "achat" in services.sale_refusal(attente)
    assert services.sale_refusal(purchased) is None

    to_status(purchased, Status.ABANDONED, mathis)
    assert "abandonné" in services.sale_refusal(purchased)
    to_status(purchased, Status.PURCHASED, mathis)

    sell(purchased, [(mathis, "2600")], mathis)
    purchased.refresh_from_db()
    assert "déjà vendu" in services.sale_refusal(purchased)


def test_sale_on_a_stale_vehicle_is_refused(purchased, mathis):
    """Deux onglets : le second envoi lit un véhicule périmé et ne crée pas de vente."""
    perime = Vehicle.objects.get(pk=purchased.pk)
    sell(purchased, [(mathis, "2600")], mathis)

    with pytest.raises(ValidationError):
        sell(perime, [(mathis, "2600")], mathis)
    assert purchased.sales.count() == 1


def test_vehicle_with_an_active_sale_but_another_status_is_refused(purchased, mathis):
    """État incohérent : un refus clair, pas l'erreur de la contrainte d'unicité."""
    sell(purchased, [(mathis, "2600")], mathis)
    Vehicle.objects.filter(pk=purchased.pk).update(status=Status.FOR_SALE)
    purchased.refresh_from_db()
    assert "déjà vendu" in services.sale_refusal(purchased)


# --- Modifier une vente ----------------------------------------------------------


def test_update_sale_changes_the_fields(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis)

    edit_sale(
        vente,
        [(mathis, "2500")],
        price="2500",
        date=dt.date(2026, 10, 29),
        sold_where="Leboncoin",
        notes="Remise",
        buyer=Contact(name="Paul"),
    )

    vente.refresh_from_db()
    assert vente.price == D("2500.00")
    assert vente.date == dt.date(2026, 10, 29)
    assert (vente.sold_where, vente.notes, vente.buyer.name) == ("Leboncoin", "Remise", "Paul")
    encaissement = vente.receipts.get()
    assert encaissement.amount == D("2500.00")
    assert encaissement.date == dt.date(2026, 10, 29)


def test_update_sale_keeps_the_old_values_in_history(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis)
    edit_sale(vente, [(mathis, "2500")], price="2500")

    assert vente.history.count() == 2
    assert vente.history.earliest("history_date").price == D("2600.00")
    assert vente.receipts.get().history.earliest("history_date").amount == D("2600.00")


def test_receipt_removed_from_a_sale_goes_to_the_trash(purchased, mathis, axel):
    """Règle 6 : un encaissement retiré reste en base, et ne compte plus (D49)."""
    vente = sell(purchased, [(mathis, "1300"), (axel, "1300")], mathis)

    edit_sale(vente, [(mathis, "2600")])

    assert vente.receipts.get().partner == mathis
    retire = SaleReceipt.all_objects.get(sale=vente, partner=axel)
    assert retire.is_deleted
    positions = queries.positions(queries.load_accounts())
    assert positions[axel.pk].received == D("0.00")
    assert positions[mathis.pk].received == D("2600.00")


def test_partner_put_back_on_a_sale_gets_a_new_receipt(purchased, mathis, axel):
    vente = sell(purchased, [(mathis, "1300"), (axel, "1300")], mathis)
    edit_sale(vente, [(mathis, "2600")])

    edit_sale(vente, [(mathis, "1300"), (axel, "1300")])

    assert vente.receipts.count() == 2
    assert SaleReceipt.all_objects.filter(sale=vente).count() == 3


def test_update_sale_merges_two_receipts_of_a_partner(purchased, mathis):
    """Le formulaire donne un montant par associé : le second encaissement part à la corbeille."""
    vente = sell(purchased, [(mathis, "2000")], mathis, price="2000")
    SaleReceipt.objects.create(sale=vente, partner=mathis, amount=D("600"))

    edit_sale(vente, [(mathis, "2600")])

    assert vente.receipts.get().amount == D("2600.00")


def test_update_sale_takes_the_applied_deposit_into_account(purchased, mathis, axel):
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()
    vente = sell(purchased, [(axel, "2400")], axel)

    with pytest.raises(ValidationError, match="Corrige l'un des montants"):
        edit_sale(vente, [(axel, "2600")])

    edit_sale(vente, [(axel, "2300")], price="2500")
    assert queries.consistency_problems(queries.load_accounts()) == []


def test_update_sale_refuses_a_price_below_the_deposit(purchased, mathis, axel):
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()
    vente = sell(purchased, [(axel, "2400")], axel)
    with pytest.raises(ValidationError, match="Corrige le prix"):
        edit_sale(vente, [], price="150")


def test_cancelled_sale_is_not_updated(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis)
    perimee = Sale.objects.get(pk=vente.pk)
    services.cancel_sale(vente, None, mathis)

    with pytest.raises(ValidationError, match="annulée"):
        edit_sale(perimee, [(mathis, "2500")], price="2500")
    vente.refresh_from_db()
    assert vente.price == D("2600.00")


def test_former_partner_stays_possible_on_a_receipt_already_held(purchased, mathis, axel):
    vente = sell(purchased, [(axel, "2600")], mathis)
    axel.is_active = False
    axel.save()

    edit_sale(vente, [(axel, "2500")], price="2500")

    assert vente.receipts.get().amount == D("2500.00")


# --- Annuler une vente -----------------------------------------------------------


@pytest.mark.finance
def test_cancelled_sale_counts_nowhere(purchased, mathis):
    """D34 : les encaissements ne comptent plus, aucune dépense n'est créée."""
    avant = Expense.objects.count()
    vente = sell(purchased, [(mathis, "2600")], mathis)

    services.cancel_sale(vente, None, mathis)

    vente.refresh_from_db()
    assert vente.is_cancelled
    assert vente.receipts.count() == 1
    assert Expense.objects.count() == avant
    data = queries.load_accounts()
    assert queries.positions(data)[mathis.pk].received == D("0.00")
    assert queries.vehicle_margins(queries.load_report(data)) == {}
    assert queries.consistency_problems(data) == []


def test_cancelled_sale_brings_the_vehicle_back_for_sale(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis)

    services.cancel_sale(vente, None, mathis)

    purchased.refresh_from_db()
    assert purchased.status == Status.FOR_SALE
    assert services.active_sale(purchased) is None
    assert list(services.cancelled_sales(purchased)) == [vente]
    changement = purchased.status_changes.order_by("-id").first()
    assert (changement.from_status, changement.to_status) == (Status.SOLD, Status.FOR_SALE)
    assert changement.changed_by == mathis


def test_cancelling_a_sale_keeps_a_trace_in_its_history(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis)
    services.cancel_sale(vente, None, mathis)
    assert vente.history.latest("history_date").cancelled_at is not None


def test_cancelling_a_sale_locks_a_reopened_vehicle_again(purchased, mathis):
    """Une fiche rouverte puis revendue ne doit pas rester déverrouillée."""
    vente = sell(purchased, [(mathis, "2600")], mathis)
    Vehicle.objects.filter(pk=purchased.pk).update(
        reopened_at=timezone.now(), reopen_reason="Facture oubliée"
    )

    services.cancel_sale(vente, None, mathis)

    purchased.refresh_from_db()
    assert purchased.reopened_at is None


@pytest.mark.parametrize(
    ("devenir", "recu"),
    [(DepositStatus.REFUNDED, D("0.00")), (DepositStatus.KEPT, D("200.00"))],
)
@pytest.mark.finance
def test_cancelling_a_sale_settles_the_applied_deposit(purchased, mathis, axel, devenir, recu):
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()
    vente = sell(purchased, [(axel, "2400")], axel)

    services.cancel_sale(vente, devenir, mathis)

    acompte = purchased.deposits.get()
    assert acompte.status == devenir
    assert acompte.sale == vente
    data = queries.load_accounts()
    assert queries.positions(data)[mathis.pk].received == recu
    assert queries.positions(data)[axel.pk].received == D("0.00")
    assert queries.consistency_problems(data) == []


def test_cancelling_a_sale_with_a_deposit_requires_its_outcome(purchased, mathis):
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()
    vente = sell(purchased, [(mathis, "2400")], mathis)

    with pytest.raises(ValidationError):
        services.cancel_sale(vente, None, mathis)

    # Tout ou rien : la vente reste valable, l'acompte reste déduit.
    vente.refresh_from_db()
    assert not vente.is_cancelled
    assert purchased.deposits.get().status == DepositStatus.APPLIED


def test_sale_cannot_be_cancelled_twice(purchased, mathis):
    vente = sell(purchased, [(mathis, "2600")], mathis)
    services.cancel_sale(vente, None, mathis)
    with pytest.raises(ValidationError, match="déjà annulée"):
        services.cancel_sale(vente, None, mathis)
    assert purchased.status_changes.filter(to_status=Status.FOR_SALE).count() == 1


@pytest.mark.finance
def test_vehicle_can_be_sold_again_after_a_cancelled_sale(purchased, mathis, axel):
    """D21 : vente annulée puis revente, l'acompte conservé compte dans la marge (D32)."""
    take_deposit(purchased, mathis)
    purchased.refresh_from_db()
    premiere = sell(purchased, [(mathis, "2400")], mathis)
    services.cancel_sale(premiere, DepositStatus.KEPT, mathis)
    purchased.refresh_from_db()

    # L'acompte conservé n'est pas déduit de la nouvelle vente.
    seconde = sell(purchased, [(axel, "2800")], axel, price="2800")

    assert services.active_sale(purchased) == seconde
    data = queries.load_accounts()
    marges = queries.vehicle_margins(queries.load_report(data))
    assert marges == {purchased.pk: D("2800") + D("200") - D("1500")}
    assert queries.consistency_problems(data) == []

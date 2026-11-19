"""Tests de la reprise d'un véhicule (D19, D51) : saisie, argent, annulation, refus, écrans."""

import datetime as dt
import re
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.management import call_command

from accounting import queries
from accounting import services as accounting
from accounting.services import Payment
from core.models import Contact, ExpenseCategory, VehicleType
from expenses import services as expense_services
from expenses.models import Expense, ExpenseLine
from expenses.services import LineEntry
from sales import services
from sales.models import Deposit, Sale
from sales.services import TradeIn
from sales.tests.helpers import record_deposit_for
from vehicles import services as vehicle_services
from vehicles.models import StatusChange, Vehicle

Status = Vehicle.Status
LE_30 = dt.date(2026, 10, 30)


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def run_command() -> str:
    sortie = StringIO()
    call_command("controle_comptes", stdout=sortie)
    return sortie.getvalue()


def solde() -> accounting.Balance:
    return accounting.balance(queries.positions(queries.load_accounts()))


def repris(brand="Yamaha", model="125", year=2015) -> Vehicle:
    """Le véhicule repris, pas encore enregistré."""
    return Vehicle(vehicle_type=VehicleType.objects.first(), brand=brand, model=model, year=year)


def sell_with_trade_in(vehicule, partner, value="700", price="2000", received=None, **champs):
    """Vente avec reprise. Par défaut, le reste est encaissé par `partner`."""
    reste = D(price) - D(value) if received is None else D(received)
    return services.record_sale(
        vehicule,
        price=D(price),
        date=champs.pop("date", LE_30),
        buyer=champs.pop("buyer", None),
        sold_where="",
        notes="",
        receipts=[Payment(partner, reste)] if reste else [],
        partner=partner,
        trade_in=TradeIn(champs.pop("trade_in_vehicle", None) or repris(), D(value)),
    )


@pytest.fixture
def vente(purchased, axel, mathis):
    """GG vendue 2 000 € : 1 300 € encaissés par Axel, une 125 reprise pour 700 € (cas 13)."""
    return sell_with_trade_in(purchased, axel)


@pytest.fixture
def reprise(vente):
    return vente.trade_in_vehicle


@pytest.fixture
def achat(reprise):
    return vehicle_services.purchase_lines(reprise).get().expense


def piece(vehicule, category, label="Câble", price="30.00"):
    return LineEntry(label, category, D(price), vehicle=vehicule, status="ORDERED")


def save_expense(expense, lines, payments, total, **champs):
    valeurs = {
        "date": LE_30,
        "supplier_name": "",
        "order_number": "",
        "total_paid": D(total),
        "is_personal": False,
        "notes": "",
    }
    return expense_services.save_expense(
        expense, lines=lines, payments=payments, **valeurs | champs
    )


def purchase_entry(expense, **champs) -> LineEntry:
    ligne = expense.lines.get()
    valeurs = {
        "label": ligne.label,
        "category": ligne.category,
        "unit_price": ligne.unit_price,
        "quantity": ligne.quantity,
        "vehicle": ligne.vehicle,
        "pk": ligne.pk,
    }
    return LineEntry(**valeurs | champs)


def payments_of(expense) -> list[Payment]:
    return [Payment(p.partner, p.amount) for p in expense.payments.select_related("partner")]


# --- Enregistrer une reprise ------------------------------------------------------------


@pytest.mark.finance
def test_case_13_trade_in_from_database(purchased, reprise, vente, achat, mathis, axel):
    """Cas 13 enregistré par `record_sale`, puis `controle_comptes`."""
    assert vente.trade_in_value == D("700.00")
    assert achat.total_paid == D("700.00")
    assert {p.partner: p.amount for p in achat.payments.all()} == {
        mathis: D("350.00"),
        axel: D("350.00"),
    }
    # Mathis a sorti 1 500 + 350 et reçu 350 ; Axel a sorti 350 et reçu 1 300 + 350.
    data = queries.load_accounts()
    positions = queries.positions(data)
    assert positions[mathis.pk].value == D("1500.00")
    assert positions[axel.pk].value == D("-1300.00")
    assert solde() == accounting.Balance(axel.pk, mathis.pk, D("1400.00"))

    couts = accounting.vehicle_costs(data.expenses)
    assert couts[reprise.pk].total == D("700.00")
    assert queries.vehicle_margins(queries.load_report(data)) == {purchased.pk: D("500.00")}
    assert queries.consistency_problems(data) == []
    assert "Les comptes sont cohérents." in run_command()


@pytest.mark.finance
def test_case_13_same_balance_as_without_a_trade_in(purchased, mathis, axel):
    """Même solde qu'une vente encaissée pour moitié, puis un achat payé pour moitié."""
    sell_with_trade_in(purchased, axel)
    equivalent = accounting.partner_positions(
        [mathis.pk, axel.pk],
        sales=[
            accounting.SaleInput(
                D("2000"),
                (
                    accounting.Receipt(axel.pk, D("1300")),
                    accounting.Receipt(mathis.pk, D("350")),
                    accounting.Receipt(axel.pk, D("350")),
                ),
            )
        ],
        expenses=[
            accounting.ExpenseInput(
                D("1500"),
                (Payment(mathis.pk, D("1500")),),
                (accounting.LineInput(D("1"), D("1500"), 1),),
            ),
            accounting.ExpenseInput(
                D("700"),
                (Payment(mathis.pk, D("350")), Payment(axel.pk, D("350"))),
                (accounting.LineInput(D("1"), D("700"), 2),),
            ),
        ],
    )
    assert solde() == accounting.balance(equivalent)


@pytest.mark.finance
def test_case_12_refunded_deposit_changes_nothing_in_database(purchased, mathis, axel):
    """Cas 12 : l'acompte rendu n'a aucun effet sur le solde, `controle_comptes` le confirme."""
    avant = solde()
    acompte = record_deposit_for(
        purchased, amount=D("200"), date=LE_30, received_by=mathis, buyer=None, partner=mathis
    )
    assert solde() != avant
    services.cancel_deposit(acompte, Deposit.Status.REFUNDED, mathis)
    assert solde() == avant
    assert "Les comptes sont cohérents." in run_command()


@pytest.mark.finance
def test_odd_trade_in_value_stays_neutral_in_database(purchased, mathis, axel):
    """700,01 € : le centime impair va au même associé des deux côtés."""
    vente = sell_with_trade_in(purchased, axel, value="700.01", price="2000.01")
    achat = vehicle_services.purchase_lines(vente.trade_in_vehicle).get().expense
    assert {p.partner: p.amount for p in achat.payments.all()} == {
        mathis: D("350.01"),
        axel: D("350.00"),
    }
    data = queries.load_accounts()
    positions = queries.positions(data)
    # Mêmes positions qu'une vente de 1 300 € sans reprise : la reprise ne pèse rien.
    assert positions[mathis.pk].value == D("1500.00")
    assert positions[axel.pk].value == D("-1300.00")
    assert queries.consistency_problems(data) == []
    assert "Les comptes sont cohérents." in run_command()


def test_trade_in_vehicle_arrives_purchased_from_the_buyer(purchased, axel):
    client = Contact(name="Julie Martin")
    vente = sell_with_trade_in(purchased, axel, buyer=client)
    reprise = Vehicle.objects.get(pk=vente.trade_in_vehicle_id)
    assert reprise.status == Status.PURCHASED
    assert reprise.seller == Contact.objects.get(name="Julie Martin")
    assert reprise.purchased_on == LE_30
    assert vente.buyer == reprise.seller
    assert list(reprise.status_changes.order_by("id").values_list("to_status", flat=True)) == [
        Status.WAITING_PURCHASE,
        Status.PURCHASED,
    ]
    assert reprise in vehicle_services.garage_vehicles()


def test_trade_in_can_cover_the_whole_price(purchased, axel):
    vente = sell_with_trade_in(purchased, axel, value="2000", price="2000")
    assert not vente.receipts.exists()


def test_trade_in_with_a_deposit_leaves_the_rest_to_receive(purchased, mathis, axel):
    record_deposit_for(
        purchased, amount=D("200"), date=LE_30, received_by=mathis, buyer=None, partner=mathis
    )
    purchased.refresh_from_db()
    vente = sell_with_trade_in(purchased, axel, received="1100")
    assert vente.receipts.get().amount == D("1100.00")
    assert queries.consistency_problems(queries.load_accounts()) == []


@pytest.mark.parametrize(
    ("value", "price", "received", "message"),
    [
        ("0.01", "2000", "1999.99", "ne se partage pas"),
        ("0", "2000", "2000", "plus grande que 0"),
        ("2500", "2000", "0", "Corrige le prix"),
        ("700", "2000", "1000", "Les parts encaissées"),
    ],
)
def test_a_refused_trade_in_saves_nothing(purchased, axel, value, price, received, message):
    avant = Vehicle.objects.count(), Expense.objects.count(), Contact.objects.count()
    with pytest.raises(ValidationError, match=message):
        sell_with_trade_in(
            purchased, axel, value=value, price=price, received=received, buyer=Contact(name="J")
        )
    assert (Vehicle.objects.count(), Expense.objects.count(), Contact.objects.count()) == avant
    assert not Sale.objects.exists()
    purchased.refresh_from_db()
    assert purchased.status == Status.PURCHASED


def test_trade_in_needs_two_active_partners(purchased, mathis, axel):
    axel.is_active = False
    axel.save()
    with pytest.raises(ValidationError, match="Préviens Mathis"):
        sell_with_trade_in(purchased, mathis)
    assert not Vehicle.objects.filter(brand="Yamaha").exists()


def test_double_submit_records_one_trade_in(purchased, axel):
    perime = Vehicle.objects.get(pk=purchased.pk)
    sell_with_trade_in(purchased, axel)
    with pytest.raises(ValidationError):
        sell_with_trade_in(perime, axel, trade_in_vehicle=repris(brand="Double"))
    assert not Vehicle.objects.filter(brand="Double").exists()
    assert Vehicle.objects.filter(brand="Yamaha").count() == 1


# --- La vente modifiée ne touche pas à la reprise ---------------------------------------


def test_update_sale_never_touches_the_trade_in(vente, reprise, achat, axel):
    """La reprise compte dans ce qui reste à encaisser, et l'achat repris ne suit pas."""
    client = Contact.objects.create(name="Paul Durand")
    services.update_sale(
        vente,
        price=D("2200"),
        date=dt.date(2026, 11, 1),
        buyer=client,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("1500"))],
    )
    vente.refresh_from_db()
    reprise.refresh_from_db()
    achat.refresh_from_db()
    assert vente.trade_in_vehicle == reprise
    assert vente.trade_in_value == D("700.00")
    assert achat.date == LE_30
    assert achat.total_paid == D("700.00")
    assert reprise.seller is None
    assert reprise.purchased_on == LE_30


def test_update_sale_still_counts_the_trade_in(vente, axel):
    with pytest.raises(ValidationError, match="Les parts encaissées"):
        services.update_sale(
            vente,
            price=D("2000"),
            date=LE_30,
            buyer=None,
            sold_where="",
            notes="",
            receipts=[Payment(axel, D("2000"))],
        )


# --- L'achat du véhicule repris tant que la vente tient ---------------------------------


@pytest.mark.parametrize(
    "changement",
    ["montant", "payeurs", "quantite", "ligne_ajoutee"],
)
def test_purchase_of_the_trade_in_keeps_its_money(
    achat, reprise, parts_category, mathis, changement
):
    lignes = [purchase_entry(achat)]
    paiements = payments_of(achat)
    total = "700"
    if changement == "montant":
        lignes = [purchase_entry(achat, unit_price=D("600"))]
        paiements = [Payment(p.partner, D("300")) for p in paiements]
        total = "600"
    elif changement == "payeurs":
        paiements = [Payment(mathis, D("700"))]
    elif changement == "quantite":
        lignes = [purchase_entry(achat, quantity=D("2"), unit_price=D("350"))]
    else:
        lignes.append(piece(reprise, parts_category, price="10"))
        paiements = [Payment(p.partner, p.amount + D("5")) for p in paiements]
        total = "710"
    with pytest.raises(ValidationError, match="annule la vente"):
        save_expense(achat, lignes, paiements, total)
    achat.refresh_from_db()
    assert achat.total_paid == D("700.00")
    assert achat.lines.count() == 1


def test_purchase_of_the_trade_in_keeps_its_date_supplier_and_notes_editable(achat, reprise):
    jour = dt.date(2026, 10, 15)
    save_expense(
        achat,
        [purchase_entry(achat)],
        payments_of(achat),
        "700",
        date=jour,
        supplier_name="Julie",
        notes="Carte grise reçue",
    )
    achat.refresh_from_db()
    reprise.refresh_from_db()
    assert achat.date == jour
    assert achat.supplier.name == "Julie"
    assert achat.notes == "Carte grise reçue"
    assert reprise.purchased_on == jour


def test_ordinary_purchase_stays_free(purchased, mathis):
    """La garde ne vaut que pour un véhicule repris : un achat ordinaire reste modifiable."""
    depense = vehicle_services.purchase_lines(purchased).get().expense
    assert expense_services.trade_in_sale(depense) is None
    save_expense(
        depense,
        [purchase_entry(depense, unit_price=D("1400"))],
        [Payment(mathis, D("1400"))],
        "1400",
    )
    depense.refresh_from_db()
    assert depense.total_paid == D("1400.00")


# --- Annuler une vente avec reprise -----------------------------------------------------


@pytest.mark.finance
def test_cancelling_trashes_the_trade_in_and_its_purchase(purchased, reprise, vente, achat, mathis):
    """Vente annulée : plus rien ne reste de la reprise dans les comptes."""
    services.cancel_sale(vente, None, mathis)

    assert not Vehicle.objects.filter(pk=reprise.pk).exists()
    assert not Expense.objects.filter(pk=achat.pk).exists()
    assert Vehicle.all_objects.get(pk=reprise.pk).deleted_at is not None
    assert Expense.all_objects.get(pk=achat.pk).deleted_at is not None
    assert reprise not in vehicle_services.garage_vehicles()

    purchased.refresh_from_db()
    assert purchased.status == Status.FOR_SALE
    # Il ne reste que l'achat de la GG : 1 500 € sortis par Mathis.
    data = queries.load_accounts()
    assert queries.positions(data)[mathis.pk].value == D("1500.00")
    assert queries.consistency_problems(data) == []
    assert "Les comptes sont cohérents." in run_command()


def test_a_sold_vehicle_can_be_resold_with_another_trade_in(purchased, vente, reprise, axel):
    services.cancel_sale(vente, None, axel)
    purchased.refresh_from_db()
    deuxieme = sell_with_trade_in(purchased, axel, trade_in_vehicle=repris(brand="Honda"))
    assert deuxieme.trade_in_vehicle.brand == "Honda"
    vente.refresh_from_db()
    assert vente.trade_in_vehicle == reprise
    assert queries.consistency_problems(queries.load_accounts()) == []


def refused(vente) -> str:
    with pytest.raises(ValidationError) as erreur:
        services.cancel_sale(vente, None, None)
    vente.refresh_from_db()
    assert vente.cancelled_at is None
    assert not vente.is_cancelled
    return erreur.value.messages[0]


def test_cancelling_is_refused_with_another_expense(vente, reprise, parts_category, mathis):
    save_expense(None, [piece(reprise, parts_category)], [Payment(mathis, D("30"))], "30.00")
    message = refused(vente)
    assert "d'autres dépenses" in message
    assert "Déplace d'abord leurs lignes" in message
    assert "« Modifier la dépense »" in message
    assert "Préviens Mathis" not in message
    assert Vehicle.objects.filter(pk=reprise.pk).exists()


def test_moving_the_other_expense_lets_the_sale_be_cancelled(
    vente, reprise, parts_category, mathis
):
    """Ce que dit le message suffit : la ligne déplacée sur le garage, la vente s'annule."""
    depense = save_expense(
        None, [piece(reprise, parts_category)], [Payment(mathis, D("30"))], "30.00"
    )
    ligne = depense.lines.get()
    entree = LineEntry(
        ligne.label, ligne.category, ligne.unit_price, vehicle=None, status="ORDERED", pk=ligne.pk
    )
    save_expense(depense, [entree], [Payment(mathis, D("30"))], "30.00")
    assert services.trade_in_cancel_refusal(vente) is None
    services.cancel_sale(vente, None, mathis)
    assert not Vehicle.objects.filter(pk=reprise.pk).exists()


def test_another_expense_shared_with_a_sold_vehicle_says_to_reopen_it(
    vente, reprise, parts_category, make_vehicle, mathis, axel
):
    autre = make_vehicle(brand="KTM", model="125SX", year=2012)
    vehicle_services.record_purchase(
        autre,
        price=D("900"),
        date=dt.date(2026, 10, 2),
        payments=[Payment(mathis, D("900"))],
        seller=None,
        partner=mathis,
    )
    save_expense(
        None,
        [piece(reprise, parts_category), piece(autre, parts_category, label="Levier")],
        [Payment(mathis, D("60"))],
        "60.00",
    )
    services.record_sale(
        autre,
        price=D("1200"),
        date=LE_30,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("1200"))],
        partner=axel,
    )
    message = refused(vente)
    assert "Déplace d'abord leurs lignes" in message
    assert f"Rouvre d'abord la fiche de {autre.display_name}, vendu." in message
    assert "Préviens Mathis" not in message


def test_cancelling_is_refused_with_a_part_to_order(vente, reprise):
    piece_notee = expense_services.add_part_to_order(label="Chaîne", vehicle=reprise)
    assert refused(vente).startswith("Retire d'abord les pièces à commander de")
    expense_services.remove_part_to_order(piece_notee)
    assert services.trade_in_cancel_refusal(vente) is None


def test_part_to_order_of_an_abandoned_trade_in_says_to_change_its_status(vente, reprise, mathis):
    expense_services.add_part_to_order(label="Chaîne", vehicle=reprise)
    vehicle_services.change_status(reprise, Status.ABANDONED, mathis)
    vente.refresh_from_db()
    assert "Change d'abord son statut" in refused(vente)


def test_cancelling_is_refused_with_a_second_line_on_the_purchase(
    vente, achat, reprise, parts_category
):
    ExpenseLine.objects.create(
        expense=achat, vehicle=reprise, category=parts_category, label="Ajout", unit_price=D("1")
    )
    message = refused(vente)
    assert "une autre ligne" in message
    assert "Préviens Mathis" in message


def test_cancelling_is_refused_with_a_refund(vente, achat, axel):
    ligne = achat.lines.get()
    retour = expense_services.record_refund(
        ligne, date=LE_30, amount=D("10.00"), refunded_to=axel
    )
    assert "Mets-le d'abord à la corbeille" in refused(vente)
    expense_services.remove_refund(retour)
    assert services.trade_in_cancel_refusal(vente) is None


@pytest.mark.parametrize("devenir", [Deposit.Status.REFUNDED, Deposit.Status.KEPT])
def test_cancelling_is_refused_after_a_deposit_even_finished(vente, reprise, mathis, devenir):
    acompte = record_deposit_for(
        reprise, amount=D("100"), date=LE_30, received_by=mathis, buyer=None, partner=mathis
    )
    assert "Préviens Mathis" in refused(vente)
    services.cancel_deposit(acompte, devenir, mathis)
    assert "Préviens Mathis" in refused(vente)


def test_cancelling_is_refused_after_a_sale_of_the_trade_in_even_cancelled(vente, reprise, mathis):
    revente = services.record_sale(
        reprise,
        price=D("900"),
        date=LE_30,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("900"))],
        partner=mathis,
    )
    assert "Préviens Mathis" in refused(vente)
    services.cancel_sale(revente, None, mathis)
    assert "Préviens Mathis" in refused(vente)


def test_cancelling_is_refused_without_the_purchase(vente, achat):
    achat.soft_delete()
    assert "n'est plus celui de la reprise" in refused(vente)


def test_a_refused_cancellation_touches_nothing(vente, reprise, purchased, mathis):
    avant = StatusChange.objects.count()
    expense_services.add_part_to_order(label="Chaîne", vehicle=reprise)
    refused(vente)
    purchased.refresh_from_db()
    assert purchased.status == Status.SOLD
    assert StatusChange.objects.count() == avant
    assert not vente.history.filter(cancelled_at__isnull=False).exists()


def test_cancelling_a_sale_without_trade_in_is_unchanged(purchased, mathis):
    vente = services.record_sale(
        purchased,
        price=D("2000"),
        date=LE_30,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("2000"))],
        partner=mathis,
    )
    assert services.trade_in_cancel_refusal(vente) is None
    services.cancel_sale(vente, None, mathis)
    assert Vehicle.objects.filter(pk=purchased.pk).exists()


# --- Écrans ---------------------------------------------------------------------------


def sale_data(partner, **champs) -> dict:
    return {
        "price": "2000",
        "date": "2026-10-30",
        "payer": str(partner.pk),
        "buyer": "",
        "sold_where": "",
        "notes": "",
        **champs,
    }


def trade_in_data(**champs) -> dict:
    return {
        "has_trade_in": "on",
        "trade_in_value": "700",
        "reprise-vehicle_type": str(VehicleType.objects.first().pk),
        "reprise-brand": "Yamaha",
        "reprise-model": "125",
        "reprise-year": "2015",
        **champs,
    }


def test_sale_form_offers_the_trade_in(as_mathis, purchased):
    page = as_mathis.get(f"/vehicules/{purchased.number}/vente/").content.decode()
    assert "paie en partie avec son véhicule" in page
    assert 'name="trade_in_value"' in page
    assert 'name="reprise-brand"' in page


def test_sale_with_a_trade_in_is_recorded_from_the_form(as_mathis, purchased, axel):
    donnees = sale_data(axel) | trade_in_data()
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 302
    vente = Sale.objects.get()
    assert vente.trade_in_value == D("700.00")
    assert vente.trade_in_vehicle.display_name.endswith("Yamaha 125 2015")
    assert vente.receipts.get().amount == D("1300.00")
    assert "arrive au garage" in messages_of(reponse)[0]


def test_sale_form_buyer_becomes_the_seller_of_the_trade_in(as_mathis, purchased, axel):
    donnees = sale_data(axel, buyer="nouveau", **{"acheteur-name": "Julie Martin"})
    as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees | trade_in_data())
    assert Sale.objects.get().trade_in_vehicle.seller.name == "Julie Martin"


def test_all_the_errors_show_at_once(as_mathis, purchased, axel):
    donnees = sale_data(axel) | trade_in_data(**{"reprise-brand": "", "trade_in_value": ""})
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 200
    page = reponse.content.decode()
    assert "Indique la valeur de reprise." in page
    assert reponse.context["trade_in_form"].errors["brand"]
    # La case reste cochée : les champs de la reprise et leurs erreurs restent visibles.
    assert "reprise: true" in page
    assert not Sale.objects.exists()


def test_unreadable_trade_in_and_missing_payer_show_both_errors(as_mathis, purchased, axel):
    """Relecture de la 1.5 : une valeur de reprise illisible n'empêche plus de signaler
    qu'aucun payeur n'est choisi ; les deux erreurs s'affichent d'un coup."""
    donnees = sale_data(axel, payer="") | trade_in_data(trade_in_value="pas un montant")
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 200
    erreurs = reponse.context["form"].errors
    assert erreurs["trade_in_value"]
    assert erreurs["payer"] == ["Choisis qui a encaissé l'argent."]
    assert not Sale.objects.exists()


def test_unticked_box_keeps_what_was_typed_after_a_refusal(as_mathis, purchased, axel):
    """Relecture de la 1.5 : case décochée, puis vente refusée (pas de prix) : la marque et
    le modèle tapés restent dans le formulaire, sans erreur, prêts si on recoche."""
    donnees = sale_data(axel, price="") | trade_in_data()
    del donnees["has_trade_in"]
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 200
    reprise = reponse.context["trade_in_form"]
    assert not reprise.is_bound
    page = reponse.content.decode()
    assert 'value="Yamaha"' in page
    assert 'value="125"' in page
    assert not Vehicle.objects.filter(brand="Yamaha").exists()


def test_form_refuses_a_cent_with_a_clear_message(as_mathis, purchased, axel):
    donnees = sale_data(axel, price="2000") | trade_in_data(trade_in_value="0,01")
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 200
    assert "ne se partage pas" in reponse.content.decode()
    assert not Vehicle.objects.filter(brand="Yamaha").exists()


def test_form_refuses_a_trade_in_above_the_price(as_mathis, purchased, axel):
    donnees = sale_data(axel, price="500") | trade_in_data()
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 200
    assert "Corrige le prix" in reponse.content.decode()


def test_unticked_box_ignores_what_was_typed(as_mathis, purchased, axel):
    """Une case décochée après saisie : les champs masqués ne bloquent pas la vente."""
    donnees = sale_data(axel) | trade_in_data(trade_in_value="pas un montant")
    del donnees["has_trade_in"]
    reponse = as_mathis.post(f"/vehicules/{purchased.number}/vente/", donnees)
    assert reponse.status_code == 302
    vente = Sale.objects.get()
    assert vente.trade_in_vehicle is None
    assert vente.trade_in_value == D("0.00")


def test_preview_reads_the_typed_trade_in(as_mathis, purchased, axel):
    url = f"/vehicules/{purchased.number}/vente/apercu/"
    sans = as_mathis.post(url, {"price": "2000"}).content.decode()
    assert "2 000,00" in sans

    avec = as_mathis.post(url, {"price": "2000", **trade_in_data()}).content.decode()
    assert "1 300,00" in avec

    decochee = {"price": "2000", "trade_in_value": "700"}
    assert "2 000,00" in as_mathis.post(url, decochee).content.decode()

    illisible = {"price": "2000", "has_trade_in": "on", "trade_in_value": "abc"}
    assert "2 000,00" in as_mathis.post(url, illisible).content.decode()


def test_sheet_shows_the_trade_in_with_a_link(as_mathis, purchased, reprise):
    page = as_mathis.get(purchased.get_absolute_url()).content.decode()
    assert "Reprise" in page
    assert reprise.get_absolute_url() in page
    assert "700,00" in page


def test_edit_form_shows_the_trade_in_read_only(as_mathis, vente, reprise, axel):
    page = as_mathis.get(f"/ventes/{vente.pk}/modifier/").content.decode()
    assert "Pour la corriger, annule la vente." in page
    assert reprise.get_absolute_url() in page
    assert 'name="has_trade_in"' not in page
    assert 'name="reprise-brand"' not in page


def test_edit_form_ignores_a_forged_trade_in(as_mathis, vente, reprise, axel):
    donnees = sale_data(axel, price="2000") | trade_in_data(trade_in_value="100")
    reponse = as_mathis.post(f"/ventes/{vente.pk}/modifier/", donnees)
    assert reponse.status_code == 302
    vente.refresh_from_db()
    assert vente.trade_in_value == D("700.00")
    assert vente.trade_in_vehicle == reprise
    assert Vehicle.objects.filter(brand="Yamaha").count() == 1


def test_expense_form_warns_about_a_trade_in_purchase(as_mathis, achat):
    page = as_mathis.get(f"/depenses/{achat.pk}/modifier/").content.decode()
    assert page.count("Cet achat vient de la reprise") == 1
    assert (
        "Tu peux modifier la date, le fournisseur, le n° de commande, la désignation, "
        "la référence, les notes et les factures." in page
    )


def test_expense_form_greys_out_the_trade_in_amount_payers_and_line(as_mathis, achat):
    """Relecture de la 1.5 : montant, payeurs et prix de la ligne sont grisés, au lieu
    d'être refusés à l'enregistrement ; on ne peut ni ajouter ni retirer de ligne."""
    page = as_mathis.get(f"/depenses/{achat.pk}/modifier/").content.decode()
    for nom in ("total_paid", "lignes-0-quantity", "lignes-0-unit_price"):
        champ = re.search(rf'<input[^>]*name="{nom}"[^>]*>', page).group(0)
        assert "disabled" in champ, nom
    assert re.search(r"<fieldset[^>]*disabled", page)  # les boutons « Qui a payé ? »
    assert "Ajouter une ligne" not in page
    assert "Retirer la ligne" not in page
    # La désignation reste modifiable.
    assert "disabled" not in re.search(r'<input[^>]*name="lignes-0-label"[^>]*>', page).group(0)


def _achat_data(achat, payeur, **lignes) -> dict:
    ligne = achat.lines.get()
    return {
        "date": "2026-10-30",
        "supplier_name": "",
        "order_number": "",
        "total_paid": "700",
        "payer": str(payeur.pk),
        "notes": "",
        "lignes-TOTAL_FORMS": "1",
        "lignes-INITIAL_FORMS": "1",
        "lignes-MIN_NUM_FORMS": "0",
        "lignes-MAX_NUM_FORMS": "1000",
        "lignes-0-line_id": str(ligne.pk),
        "lignes-0-label": "Achat corrigé",
        "lignes-0-quantity": "1",
        "lignes-0-unit_price": "700",
        **lignes,
    }


def test_forged_trade_in_amount_and_payers_are_ignored(as_mathis, achat, mathis):
    """Un envoi forgé (tout payé par Mathis, autre prix) ne change ni le montant, ni les
    payeurs, ni le prix : les champs grisés gardent leur valeur ; la désignation change."""
    paiements = sorted((p.partner_id, p.amount) for p in achat.payments.all())
    donnees = _achat_data(achat, mathis, total_paid="900", **{"lignes-0-unit_price": "900"})
    reponse = as_mathis.post(f"/depenses/{achat.pk}/modifier/", donnees)

    assert reponse.status_code == 302
    achat.refresh_from_db()
    assert achat.total_paid == D("700.00")
    assert sorted((p.partner_id, p.amount) for p in achat.payments.all()) == paiements
    ligne = achat.lines.get()
    assert (ligne.unit_price, ligne.label) == (D("700.00"), "Achat corrigé")
    assert queries.consistency_problems(queries.load_accounts()) == []


def _browser_data(achat, **champs) -> dict:
    """Ce qu'envoie vraiment le navigateur : les champs grisés (montant, payeurs, quantité,
    prix) n'y sont pas."""
    ligne = achat.lines.get()
    return {
        "date": "2026-10-29",
        "supplier_name": "Garage X",
        "order_number": "",
        "notes": "n",
        "lignes-TOTAL_FORMS": "1",
        "lignes-INITIAL_FORMS": "1",
        "lignes-MIN_NUM_FORMS": "0",
        "lignes-MAX_NUM_FORMS": "1000",
        "lignes-0-line_id": str(ligne.pk),
        "lignes-0-label": "Nouvelle désignation",
        "lignes-0-reference": "",
        "lignes-0-DELETE": "",
        **champs,
    }


@pytest.mark.parametrize("valeur", ["700", "700.01", "333.33"])
def test_browser_edit_of_a_trade_in_purchase_keeps_its_money(as_mathis, purchased, axel, valeur):
    """Envoi du navigateur, sans les champs grisés : la date, le fournisseur et la
    désignation changent ; le montant, les payeurs (valeur impaire comprise) et le prix non."""
    vente = sell_with_trade_in(purchased, axel, value=valeur)
    achat = vehicle_services.purchase_lines(vente.trade_in_vehicle).get().expense
    avant = sorted((p.partner_id, p.amount) for p in achat.payments.all())

    reponse = as_mathis.post(f"/depenses/{achat.pk}/modifier/", _browser_data(achat))

    assert reponse.status_code == 302
    achat.refresh_from_db()
    assert achat.total_paid == D(valeur)
    assert sorted((p.partner_id, p.amount) for p in achat.payments.all()) == avant
    ligne = achat.lines.get()
    assert (ligne.label, ligne.unit_price) == ("Nouvelle désignation", D(valeur))
    assert achat.date == dt.date(2026, 10, 29)
    assert queries.consistency_problems(queries.load_accounts()) == []


def test_preview_of_a_trade_in_purchase_shows_its_amount(as_mathis, achat):
    """Relecture de la 1b : l'aperçu reçoit l'envoi du navigateur, sans les champs grisés ;
    il doit montrer les 700,00 € de l'achat, pas 0,00 €."""
    page = as_mathis.post(f"/depenses/apercu/?depense={achat.pk}", _browser_data(achat))
    contenu = page.content.decode()
    montant = re.search(r"Montant payé.*?</dt>\s*<dd[^>]*>([^<]*)</dd>", contenu, re.S)
    assert montant.group(1) == "700,00 €"


def test_refused_trade_in_purchase_shows_the_sentence_once(as_mathis, achat, mathis):
    """Après un refus du service (seconde ligne ajoutée de force), la phrase n'est plus
    qu'en rouge : pas de doublon en gris."""
    seconde = {
        "lignes-TOTAL_FORMS": "2",
        "lignes-1-label": "Pièce",
        "lignes-1-quantity": "1",
        "lignes-1-unit_price": "10",
        "lignes-1-category": str(ExpenseCategory.objects.get(code=ExpenseCategory.PARTS).pk),
        "lignes-1-status": "ORDERED",
    }
    donnees = _achat_data(achat, mathis, total_paid="", **seconde)
    reponse = as_mathis.post(f"/depenses/{achat.pk}/modifier/", donnees)
    page = reponse.content.decode()
    assert reponse.status_code == 200
    assert page.count("Cet achat vient de la reprise") == 1
    assert 'role="alert">Cet achat vient de la reprise' in page
    achat.refresh_from_db()
    assert achat.total_paid == D("700.00")
    assert achat.lines.count() == 1


def test_sale_block_shows_a_trashed_trade_in_without_a_link(as_mathis, vente, reprise, purchased):
    """Relecture de la 1.5 : une fiche reprise mise à la corbeille par l'administration de
    secours n'a plus de page ; le bloc Vente la nomme sans lien."""
    lien = reprise.get_absolute_url()
    assert lien in as_mathis.get(purchased.get_absolute_url()).content.decode()

    reprise.soft_delete()
    page = as_mathis.get(purchased.get_absolute_url()).content.decode()
    assert reprise.display_name in page
    assert "(fiche à la corbeille)" in page
    assert lien not in page


def test_cancel_screen_announces_the_trash(as_mathis, vente, reprise):
    page = as_mathis.get(f"/ventes/{vente.pk}/annuler/").content.decode()
    assert reprise.display_name in page
    assert "partira à la corbeille" in page


def test_cancel_screen_refuses_as_soon_as_it_opens(as_mathis, vente, reprise, purchased):
    expense_services.add_part_to_order(label="Chaîne", vehicle=reprise)
    for reponse in (
        as_mathis.get(f"/ventes/{vente.pk}/annuler/"),
        as_mathis.post(f"/ventes/{vente.pk}/annuler/"),
    ):
        assert reponse.status_code == 302
        assert reponse.url == purchased.get_absolute_url()
    assert "Retire d'abord les pièces à commander" in messages_of(reponse)[0]
    vente.refresh_from_db()
    assert not vente.is_cancelled


def test_cancelling_from_the_screen(as_mathis, vente, reprise):
    reponse = as_mathis.post(f"/ventes/{vente.pk}/annuler/")
    assert reponse.status_code == 302
    assert "part à la corbeille" in messages_of(reponse)[0]
    assert not Vehicle.objects.filter(pk=reprise.pk).exists()


def test_cancelled_sale_shows_its_trade_in_without_a_link(as_mathis, purchased, vente, reprise):
    services.cancel_sale(vente, None, None)
    page = as_mathis.get(purchased.get_absolute_url()).content.decode()
    assert f"reprise de {reprise.display_name}" in page
    assert f'href="{reprise.get_absolute_url()}"' not in page


def test_trade_in_sheet_opens_and_works_like_any_vehicle(as_mathis, reprise):
    assert as_mathis.get(reprise.get_absolute_url()).status_code == 200


def test_expense_on_the_trade_in_is_free(reprise, parts_category, mathis):
    save_expense(None, [piece(reprise, parts_category)], [Payment(mathis, D("30"))], "30.00")
    assert queries.vehicle_cost(reprise.pk).total == D("730.00")

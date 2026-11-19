"""Tests des calculs d'argent : les cas de `regles-financieres/exemples.md` et les invariants.

Fonctions pures, sans base. Les montants sont exacts : un centime d'écart fait échouer.
"""

import dataclasses
import datetime as dt
import random
from decimal import Decimal as D

import pytest

from accounting.services import (
    DEPOSIT_ACTIVE,
    DEPOSIT_APPLIED,
    DEPOSIT_KEPT,
    DEPOSIT_REFUNDED,
    ZERO,
    Balance,
    DepositInput,
    ExpenseInput,
    LineInput,
    Payment,
    Position,
    PositionDetail,
    Receipt,
    RefundInput,
    SaleInput,
    SalePartnerShare,
    SettlementInput,
    VehicleCost,
    allocate_shipping,
    amounts_by_partner,
    balance,
    balance_after,
    balance_label,
    balance_sentence,
    combined_cost,
    deposit_problems,
    expense_line_costs,
    expense_preview,
    expense_problems,
    garage_expense_years,
    garage_expenses_for_year,
    is_fully_refunded,
    is_garage_expense,
    is_returned,
    kept_deposits_total,
    lines_sum,
    margin_display,
    margin_per_partner,
    partner_positions,
    partner_problems,
    partner_shares,
    payment_problem,
    position_details,
    recap_balance,
    receipts_problem,
    refund_left,
    refund_problem,
    refund_room,
    reservation_problems,
    sale_left_to_receive,
    sale_preview,
    sale_price_problem,
    sale_problems,
    sale_recap,
    settlement_problem,
    shipping_cost,
    sold_status_problems,
    split_by_largest_remainder,
    trade_in_problem,
    trade_in_problems,
    vehicle_costs,
    vehicle_margin,
)
from core.formatting import NBSP

from . import excel_reference as excel

pytestmark = pytest.mark.finance

M, A = "Mathis", "Axel"
PARTNERS = (M, A)
E = f"{NBSP}€"


def expense(total, payments, lines, is_personal=False):
    """Dépense en raccourci : `payments` = ((associé, montant), ...), `lines` = LineInput."""
    return ExpenseInput(
        D(total),
        tuple(Payment(associe, D(montant)) for associe, montant in payments),
        tuple(lines),
        is_personal=is_personal,
    )


def line(price, vehicle=1, quantity="1", refunds=(), key=None):
    return LineInput(D(quantity), D(price), vehicle, refunds=tuple(refunds), key=key)


def sale(price, receipts=(), vehicle=1, trade_in="0.00", cancelled=False):
    return SaleInput(
        D(price),
        tuple(Receipt(associe, D(montant)) for associe, montant in receipts),
        trade_in_value=D(trade_in),
        vehicle=vehicle,
        cancelled=cancelled,
    )


def solde(**donnees) -> Balance:
    return balance(partner_positions(PARTNERS, **donnees))


def owes(debtor, creditor, amount) -> Balance:
    return Balance(debtor=debtor, creditor=creditor, amount=D(amount))


SETTLED = Balance(debtor=None, creditor=None, amount=ZERO)

# Mêmes dépenses pour les cas 1 et 2 : achat 1 500 € et pièces 300 € par Mathis,
# pièces 200 € par Axel.
DEPENSES_CAS_1 = (
    expense("1500", [(M, "1500")], [line("1500")]),
    expense("300", [(M, "300")], [line("300")]),
    expense("200", [(A, "200")], [line("200")]),
)


# --- Cas 1 et 2 : vente simple, vente à perte ------------------------------------------


def test_case_1_simple_sale():
    vente = sale("2600", [(M, "2600")])
    cout = vehicle_costs(DEPENSES_CAS_1)[1]
    positions = partner_positions(PARTNERS, expenses=DEPENSES_CAS_1, sales=[vente])

    assert cout.total == D("2000.00")
    assert vehicle_margin(cout.total, [vente]) == D("600.00")
    assert positions[M].value == D("-800.00")
    assert positions[A].value == D("200.00")
    assert balance(positions) == owes(M, A, "500.00")
    # Vérification : une fois le règlement fait, chacun gagne 300 € (reçu − sorti).
    regle = partner_positions(
        PARTNERS,
        expenses=DEPENSES_CAS_1,
        sales=[vente],
        settlements=[SettlementInput(M, A, D("500.00"))],
    )
    assert {associe: -p.value for associe, p in regle.items()} == {M: D("300"), A: D("300")}
    assert margin_per_partner(D("600.00"), PARTNERS) == {M: D("300.00"), A: D("300.00")}


def test_case_2_sale_at_a_loss():
    vente = sale("1700", [(M, "1700")])
    cout = vehicle_costs(DEPENSES_CAS_1)[1].total
    marge = vehicle_margin(cout, [vente])

    assert marge == D("-300.00")
    assert margin_per_partner(marge, PARTNERS) == {M: D("-150.00"), A: D("-150.00")}
    assert solde(expenses=DEPENSES_CAS_1, sales=[vente]) == owes(M, A, "50.00")


# --- Cas 3 et 4 : outillage du garage, achat personnel ---------------------------------


def test_case_3_garage_tooling():
    caisse = expense("200", [(M, "200")], [line("200", vehicle=None)])

    assert solde(expenses=[caisse]) == owes(A, M, "100.00")
    couts = vehicle_costs([caisse])
    assert set(couts) == {None}
    assert couts[None].total == D("200.00")


def test_case_4_personal_purchase():
    caisse = expense("200", [(M, "200")], [line("200", vehicle=None)], is_personal=True)

    assert solde(expenses=[caisse]) == SETTLED
    assert vehicle_costs([caisse]) == {}


# --- Cas 5 : contrôle de l'Excel --------------------------------------------------------


def test_case_5_excel_has_18_expenses_and_22_part_lines():
    achats, pieces = excel.EXPENSES[:3], excel.EXPENSES[3:]
    assert len(excel.EXPENSES) == 18
    assert len(pieces) == 15
    assert sum(len(depense.lines) for depense in pieces) == 22
    assert all(len(depense.lines) == 1 for depense in achats)


def test_case_5_excel_shipping_per_order():
    par_commande = {depense.key: depense for depense in excel.EXPENSES if depense.key}
    assert set(par_commande) == set(excel.EXPECTED_SHIPPING)
    for commande, attendu in excel.EXPECTED_SHIPPING.items():
        depense = par_commande[commande]
        assert shipping_cost(depense) == attendu, commande
        assert sum(allocate_shipping(depense)) == depense.total_paid, commande
    # Les dépenses à une ligne, et le lot de deux roues, n'ont pas de frais de port.
    for depense in excel.EXPENSES:
        if not depense.key:
            assert shipping_cost(depense) == ZERO


def test_case_5_excel_positions_and_balance():
    positions = partner_positions(excel.PARTNERS, expenses=excel.EXPENSES)

    assert positions[M] == Position(paid_out=D("1564.15"), received=ZERO)
    assert positions[A] == Position(paid_out=D("1153.27"), received=ZERO)
    assert positions[M].paid_out + positions[A].paid_out == D("2717.42")
    assert balance(positions) == owes(A, M, "205.44")


def test_case_5_excel_vehicle_costs():
    couts = vehicle_costs(excel.EXPENSES)

    assert set(couts) == set(excel.EXPECTED_COSTS)
    for vehicule, (total, mathis, axel) in excel.EXPECTED_COSTS.items():
        assert couts[vehicule].total == total, vehicule
        assert couts[vehicule].by_partner == {M: mathis, A: axel}, vehicule


# --- Cas 6 à 9 : frais de port, centimes, dépense payée à deux --------------------------


def test_case_6_shipping_order_50017():
    commande = expense("86.00", [(M, "86.00")], [line("71.90"), line("8.40")])
    assert shipping_cost(commande) == D("5.70")
    assert allocate_shipping(commande) == [D("77.00"), D("9.00")]


def test_case_7_shipping_order_cmd50018():
    commande = expense(
        "48.20",
        [(A, "48.20")],
        [line("6.40", quantity="2"), line("19.90"), line("3.10", quantity="2")],
    )
    assert shipping_cost(commande) == D("9.30")
    assert allocate_shipping(commande) == [D("15.86"), D("24.66"), D("7.68")]


def test_case_8_cents_go_to_first_line_on_tie():
    commande = expense("10.00", [(M, "10.00")], [line("1.00"), line("1.00"), line("1.00")])
    assert shipping_cost(commande) == D("7.00")
    assert allocate_shipping(commande) == [D("3.34"), D("3.33"), D("3.33")]


def test_case_9_expense_paid_by_both():
    depense = expense("81.37", [(M, "40.69"), (A, "40.68")], [line("81.37")])
    (cout,) = expense_line_costs(depense)
    assert cout.shares == {M: D("40.69"), A: D("40.68")}
    assert sum(cout.shares.values()) == D("81.37")


def test_d31_partner_shares_add_up_both_ways():
    """D31 : 10,00 € payés 5,00 € chacun sur 3 lignes. Arrondies ligne par ligne, les parts
    de Mathis feraient 5,01 € ; ici elles somment exactement à son paiement."""
    depense = expense(
        "10.00", [(M, "5.00"), (A, "5.00")], [line("1.00"), line("1.00"), line("1.00")]
    )
    repartis = allocate_shipping(depense)
    parts = partner_shares(depense, repartis)

    assert [sum(p.values()) for p in parts] == repartis
    assert sum(p[M] for p in parts) == D("5.00")
    assert sum(p[A] for p in parts) == D("5.00")


def test_shipping_split_equally_when_lines_are_free():
    """Lignes à 0 € (somme nulle) : le montant payé est partagé à parts égales."""
    depense = expense("10.00", [(M, "10.00")], [line("0"), line("0"), line("0")])
    assert allocate_shipping(depense) == [D("3.34"), D("3.33"), D("3.33")]


def test_discount_gives_negative_shipping():
    """Une remise (montant payé < somme des lignes) est répartie de la même façon."""
    depense = expense("90.00", [(M, "90.00")], [line("60.00"), line("40.00")])
    assert shipping_cost(depense) == D("-10.00")
    assert allocate_shipping(depense) == [D("54.00"), D("36.00")]


# --- Cas 10 : demi-centime (D33) --------------------------------------------------------


def test_case_10_one_cent_gap_is_settled():
    """D33 : un écart d'un centime entre les positions est l'équilibre."""
    positions = {M: Position(paid_out=D("100.01")), A: Position(paid_out=D("100.00"))}
    assert balance(positions) == SETTLED


def test_odd_gap_rounds_in_favour_of_who_advanced():
    """Écart de 0,03 € : 0,015 arrondi à 0,02 € en faveur de Mathis, qui a avancé."""
    positions = {M: Position(paid_out=D("100.03")), A: Position(paid_out=D("100.00"))}
    assert balance(positions) == owes(A, M, "0.02")


@pytest.mark.parametrize("ecart", ["0.01", "0.02", "0.03", "0.05", "0.99", "205.45", "-0.07"])
def test_paying_what_is_owed_always_settles(ecart):
    """Invariant 4 : après le règlement du montant dû, le solde vaut 0,00 €, que l'écart
    entre les positions soit pair ou impair (D33)."""
    depenses = [
        expense(str(D("100.00") + D(ecart)), [(M, str(D("100.00") + D(ecart)))], [line("1")]),
        expense("100.00", [(A, "100.00")], [line("1")]),
    ]
    du = solde(expenses=depenses)
    reglements = []
    if not du.is_settled:
        reglements = [SettlementInput(du.debtor, du.creditor, du.amount)]
    assert solde(expenses=depenses, settlements=reglements) == SETTLED


# --- Cas 11 et 12 : acomptes ------------------------------------------------------------


def test_case_11_deposit_then_sale():
    depenses = [expense("2000", [(A, "2000")], [line("2000")])]
    vente = sale("2600", [(A, "2400")])
    acompte = DepositInput(M, D("200"), DEPOSIT_APPLIED, vehicle=1)
    positions = partner_positions(PARTNERS, expenses=depenses, sales=[vente], deposits=[acompte])

    assert positions[M].value == D("-200.00")
    assert positions[A].value == D("-400.00")
    assert balance(positions) == owes(A, M, "100.00")
    assert sale_problems(vente, [acompte]) == []
    # Une fois le règlement fait, chacun gagne 300 € (reçu − sorti).
    regle = partner_positions(
        PARTNERS,
        expenses=depenses,
        sales=[vente],
        deposits=[acompte],
        settlements=[SettlementInput(A, M, D("100.00"))],
    )
    assert {associe: -p.value for associe, p in regle.items()} == {M: D("300"), A: D("300")}


def test_case_11_active_deposit_already_counts():
    """Acompte en cours (véhicule réservé) : déjà encaissé par celui qui l'a reçu."""
    acompte = DepositInput(M, D("200"), DEPOSIT_ACTIVE, vehicle=1)
    assert solde(deposits=[acompte]) == owes(M, A, "100.00")


def test_case_12_refunded_deposit_has_no_effect():
    acompte = DepositInput(M, D("200"), DEPOSIT_REFUNDED, vehicle=1)
    assert solde(deposits=[acompte]) == SETTLED


# --- Vente annulée ----------------------------------------------------------------------


def test_cancelled_sale_no_longer_counts_and_kept_deposit_is_income():
    """Vente annulée : ni encaissement ni marge. L'acompte conservé (`KEPT`) compte
    comme un encaissement du véhicule, pour celui qui l'a reçu."""
    depenses = [expense("2000", [(A, "2000")], [line("2000")])]
    annulee = sale("2600", [(A, "2400")], cancelled=True)
    acompte = DepositInput(M, D("200"), DEPOSIT_KEPT, vehicle=1)
    cout = vehicle_costs(depenses)[1].total

    positions = partner_positions(PARTNERS, expenses=depenses, sales=[annulee], deposits=[acompte])
    assert positions[A] == Position(paid_out=D("2000.00"), received=ZERO)
    assert positions[M] == Position(paid_out=ZERO, received=D("200.00"))
    assert balance(positions) == owes(M, A, "1100.00")
    # Plus de vente non annulée : pas de marge, et la vente annulée n'est pas contrôlée.
    assert vehicle_margin(cout, [annulee], [acompte]) is None
    assert sale_problems(annulee) == []


def test_resale_after_cancellation_counts_kept_deposit_in_margin():
    """Revente après annulation : la marge compte le prix de la nouvelle vente et
    l'acompte conservé de la première (D32)."""
    annulee = sale("2600", [(A, "2400")], cancelled=True)
    revente = sale("2500", [(M, "2500")])
    acompte = DepositInput(M, D("200"), DEPOSIT_KEPT, vehicle=1)

    assert vehicle_margin(D("2000.00"), [annulee, revente], [acompte]) == D("700.00")


# --- Cas 13 : reprise -------------------------------------------------------------------


def test_case_13_trade_in_is_neutral_for_the_balance():
    GG, MOTO_125 = 2, 4
    vente = sale("2000", [(A, "1300")], vehicle=GG, trade_in="700")
    achat_125 = expense("700", [(M, "350"), (A, "350")], [line("700", vehicle=MOTO_125)])
    avec_reprise = solde(sales=[vente], expenses=[achat_125])

    # Même chose sans reprise : 1 300 € encaissés par Axel et 700 € à moitié chacun,
    # puis un achat de 700 € payé à moitié par chacun.
    vente_equivalente = sale("2000", [(A, "1300"), (A, "350"), (M, "350")], vehicle=GG)
    assert avec_reprise == solde(sales=[vente_equivalente], expenses=[achat_125])
    assert avec_reprise == owes(A, M, "650.00")
    assert sale_problems(vente) == []

    assert vehicle_margin(D("1421.02"), [vente]) == D("578.98")
    assert vehicle_costs([achat_125])[MOTO_125].total == D("700.00")


# --- Cas 14 et 15 : retour, transfert ---------------------------------------------------


def test_case_14_returned_part_refunded():
    retour = RefundInput(A, D("21.99"))
    depense = expense("21.99", [(A, "21.99")], [line("21.99", refunds=[retour])])
    (cout,) = expense_line_costs(depense)

    assert cout.net == ZERO
    assert solde(expenses=[depense]) == SETTLED
    assert vehicle_costs([depense])[1].by_partner == {A: ZERO}


def test_case_15_part_transfer():
    avant = expense("100", [(M, "100")], [line("100", vehicle=1)])
    apres = expense("100", [(M, "100")], [line("100", vehicle=3)])

    assert vehicle_costs([avant])[1].total == D("100.00")
    couts_apres = vehicle_costs([apres])
    assert 1 not in couts_apres
    assert couts_apres[3].total == D("100.00")
    assert couts_apres[3].by_partner == {M: D("100.00")}
    assert solde(expenses=[avant]) == solde(expenses=[apres])


# --- Cas 16 : règlement et annulation ---------------------------------------------------


def test_case_16_settlement_then_cancellation():
    reglement = SettlementInput(A, M, D("205.44"))
    annulation = SettlementInput(M, A, D("205.44"))

    assert solde(expenses=excel.EXPENSES) == owes(A, M, "205.44")
    assert solde(expenses=excel.EXPENSES, settlements=[reglement]) == SETTLED
    assert solde(expenses=excel.EXPENSES, settlements=[reglement, annulation]) == owes(
        A, M, "205.44"
    )


# --- Cas 17 : règlement partiel, et contrôle d'un règlement (D53) -----------------------

NOMS = {M: M, A: A}


def excel_positions(settlements=()):
    return partner_positions(PARTNERS, expenses=excel.EXPENSES, settlements=settlements)


def test_case_17_partial_settlement():
    """Axel doit 205,44 € et en verse 100 € : il doit encore 105,44 €."""
    acompte = SettlementInput(A, M, D("100.00"))

    assert balance_after(excel_positions(), acompte) == owes(A, M, "105.44")
    assert solde(expenses=excel.EXPENSES, settlements=[acompte]) == owes(A, M, "105.44")


def test_balance_after_settling_what_is_owed_is_settled():
    reglement = SettlementInput(A, M, D("205.44"))
    assert balance_after(excel_positions(), reglement) == SETTLED


def test_balance_after_an_odd_cent_is_settled():
    """D33 : 0,02 € dus sur un écart de 0,03 € ; après règlement, il reste un centime
    d'écart, c'est l'équilibre."""
    positions = {M: Position(paid_out=D("100.03")), A: Position(paid_out=D("100.00"))}
    assert balance_after(positions, SettlementInput(A, M, D("0.02"))) == SETTLED


def test_a_settlement_does_not_change_the_sum_of_positions():
    """Un règlement passe d'une poche à l'autre : le garage n'a ni plus ni moins d'argent,
    donc coûts, marges et bénéfice ne bougent pas."""
    avant = excel_positions()
    apres = excel_positions([SettlementInput(A, M, D("205.44"))])
    assert sum(p.value for p in avant.values()) == sum(p.value for p in apres.values())
    assert apres[A].value - avant[A].value == D("205.44")
    assert apres[M].value - avant[M].value == D("-205.44")


@pytest.mark.parametrize("montant", ["0.01", "100.00", "205.44"])
def test_settlement_up_to_what_is_owed_is_accepted(montant):
    du = owes(A, M, "205.44")
    assert settlement_problem(SettlementInput(A, M, D(montant)), du, NOMS) is None


def test_settlement_above_what_is_owed_is_refused():
    du = owes(A, M, "205.44")
    assert settlement_problem(SettlementInput(A, M, D("205.45")), du, NOMS) == (
        f"Axel doit 205,44{E} à Mathis : un règlement ne peut pas dépasser ce montant. "
        "Corrige le montant."
    )


def test_settlement_in_the_wrong_direction_is_refused():
    du = owes(A, M, "205.44")
    assert settlement_problem(SettlementInput(M, A, D("10")), du, NOMS) == (
        "Le solde a changé : c'est Axel qui doit de l'argent à Mathis. Recharge la page."
    )


def test_settlement_of_zero_is_refused():
    du = owes(A, M, "205.44")
    assert settlement_problem(SettlementInput(A, M, ZERO), du, NOMS) == (
        "Le règlement doit être plus grand que 0 €."
    )


def test_settlement_refused_when_what_is_owed_changed_since_the_page_opened():
    """Deux onglets ouverts sur 205,44 € dus : le premier règle 100 €, le second, qui avait
    vu 205,44 €, est refusé même si ses 100 € ne dépassent pas les 105,44 € restants."""
    reste = owes(A, M, "105.44")
    assert settlement_problem(SettlementInput(A, M, D("100")), reste, NOMS, D("205.44")) == (
        f"Le solde a changé depuis l'ouverture de la page : Axel doit maintenant "
        f"105,44{E} à Mathis. Recharge la page."
    )
    assert settlement_problem(SettlementInput(A, M, D("100")), reste, NOMS, D("105.44")) is None


def test_settlement_when_settled_is_refused():
    assert settlement_problem(SettlementInput(A, M, D("1")), SETTLED, NOMS) == (
        "Vous êtes à l'équilibre : il n'y a rien à régler."
    )


def test_position_details_sum_up_to_positions(scenario):
    """Le détail affiché à l'écran redonne exactement les positions du solde."""
    depenses, ventes, acomptes, reglements = scenario
    donnees = {
        "expenses": depenses,
        "sales": ventes,
        "deposits": acomptes,
        "settlements": reglements,
    }
    details = position_details(PARTNERS, **donnees)
    assert {a: d.position for a, d in details.items()} == partner_positions(PARTNERS, **donnees)


def test_position_details_by_heading():
    """Chaque montant va dans sa rubrique : dépense, remboursement, vente (encaissement,
    acompte, moitié de la reprise), règlements versés et reçus."""
    details = position_details(
        PARTNERS,
        expenses=[
            expense("100", [(M, "100")], [line("100", refunds=[RefundInput(M, D("10"))])]),
            expense("40", [(A, "40")], [line("40")]),
        ],
        sales=[sale("1000", [(A, "800")], trade_in="200")],
        deposits=[DepositInput(M, D("50"), DEPOSIT_KEPT)],
        settlements=[SettlementInput(A, M, D("30"))],
    )
    assert details[M] == PositionDetail(
        expenses_paid=D("100"),
        refunds_received=D("10"),
        sales_received=D("150"),
        settlements_received=D("30"),
    )
    assert details[A] == PositionDetail(
        expenses_paid=D("40"), sales_received=D("900"), settlements_paid=D("30")
    )


# --- Dépenses du garage (D57, D58) ------------------------------------------------------

LE_3_MARS = dt.date(2026, 3, 3)


def dated(depense, date=LE_3_MARS):
    return dataclasses.replace(depense, date=date)


def test_case_3_tooling_is_a_garage_expense_half_each():
    caisse = dated(expense("200", [(M, "200")], [line("200", vehicle=None)]))
    rapport = garage_expenses_for_year([caisse], 2026, PARTNERS)

    assert rapport.total == D("200.00")
    assert rapport.shares == {M: D("100.00"), A: D("100.00")}
    assert [d.cost for d in rapport.items] == [D("200.00")]
    assert rapport.items[0].shared_with_vehicle is False


def test_mixed_order_counts_only_its_garage_lines_with_their_shipping():
    """Payé 100 € : 60 € pour le véhicule 1, 30 € pour le garage, 10 € de port. Le port est
    réparti au prorata : le garage compte 33,33 € (le centime restant va au véhicule)."""
    commande = dated(
        expense(
            "100",
            [(A, "100")],
            [line("60", vehicle=1, key=1), line("30", vehicle=None, key=2)],
        )
    )
    (depense,) = garage_expenses_for_year([commande], 2026, PARTNERS).items

    assert depense.cost == D("33.33")
    assert depense.garage_lines == (2,)
    assert depense.shared_with_vehicle is True


def test_refund_lowers_the_garage_expense():
    perceuse = dated(
        expense("80", [(M, "80")], [line("80", vehicle=None, refunds=[RefundInput(M, D("30"))])])
    )
    assert garage_expenses_for_year([perceuse], 2026, PARTNERS).total == D("50.00")


def test_personal_purchase_is_listed_but_not_counted():
    perso = dated(expense("49.90", [(A, "49.90")], [line("49.90", vehicle=None)], True))
    rapport = garage_expenses_for_year([perso], 2026, PARTNERS)

    assert rapport.items == ()
    assert rapport.personal == (perso,)
    assert rapport.total == ZERO


def test_vehicle_only_expenses_and_other_years_are_left_out():
    pieces = dated(expense("50", [(M, "50")], [line("50", vehicle=1)]))
    ancienne = dated(expense("20", [(M, "20")], [line("20", vehicle=None)]), dt.date(2025, 12, 31))
    rapport = garage_expenses_for_year([pieces, ancienne], 2026, PARTNERS)

    assert rapport.items == ()
    assert garage_expenses_for_year([ancienne], 2025, PARTNERS).total == D("20.00")
    assert not is_garage_expense(pieces)


def test_garage_expense_years_most_recent_first():
    depenses = [
        dated(expense("20", [(M, "20")], [line("20", vehicle=None)]), dt.date(2025, 5, 1)),
        dated(expense("50", [(M, "50")], [line("50", vehicle=1)]), dt.date(2024, 5, 1)),
        dated(expense("9", [(A, "9")], [line("9", vehicle=None)], True), dt.date(2026, 1, 2)),
    ]
    assert garage_expense_years(depenses) == [2026, 2025]


def test_garage_expenses_of_all_years_are_the_garage_cost(scenario):
    """Invariant : la somme des dépenses du garage de toutes les années est le coût des
    lignes sans véhicule (`vehicle_costs`, clé None)."""
    depenses = [dated(d, dt.date(2025 + i % 2, 6, 1)) for i, d in enumerate(scenario[0])]
    total = sum(
        (garage_expenses_for_year(depenses, annee, PARTNERS).total for annee in (2025, 2026)),
        ZERO,
    )
    assert total == vehicle_costs(depenses).get(None, VehicleCost()).total


# --- Partage au plus grand reste --------------------------------------------------------


def test_split_rejects_amount_not_in_cents():
    with pytest.raises(ValueError):
        split_by_largest_remainder(D("1.005"), [D(1), D(1)])


def test_split_negative_amount():
    assert split_by_largest_remainder(D("-0.01"), [D(1), D(1)]) == [D("-0.01"), ZERO]


# --- Contrôles de cohérence -------------------------------------------------------------


def test_expense_problems_detects_inconsistencies():
    sans_ligne = expense("10", [(M, "10")], [])
    mauvais_paiements = expense("10", [(M, "9")], [line("10")])
    trop_rembourse = expense("10", [(M, "10")], [line("10", refunds=[RefundInput(M, D("11"))])])

    assert expense_problems(sans_ligne) == ["aucune ligne"]
    assert expense_problems(mauvais_paiements) == [
        f"les paiements (9,00{E}) ne font pas le montant payé (10,00{E})"
    ]
    assert expense_problems(trop_rembourse) == [
        f"remboursement (11,00{E}) plus grand que le coût de la ligne (10,00{E})"
    ]
    assert expense_problems(excel.EXPENSES[0]) == []


def test_sale_problems_detects_missing_money():
    assert sale_problems(sale("2600", [(M, "2000")])) == [
        f"encaissements + acomptes + reprise font 2{NBSP}000,00{E} "
        f"au lieu du prix (2{NBSP}600,00{E})"
    ]


@pytest.mark.parametrize(
    ("is_reserved", "active_deposits", "problemes"),
    [
        (True, 1, []),
        (False, 0, []),
        (True, 0, ["réservé avec 0 acompte(s) en cours au lieu d'un seul"]),
        (True, 2, ["réservé avec 2 acompte(s) en cours au lieu d'un seul"]),
        (False, 1, ["acompte en cours alors que le véhicule n'est pas réservé"]),
    ],
)
def test_reservation_problems(is_reserved, active_deposits, problemes):
    """Un véhicule réservé a exactement un acompte en cours, les autres aucun (D47)."""
    assert reservation_problems(is_reserved, active_deposits) == problemes


# --- Invariants (sur des scénarios mélangés) --------------------------------------------


def scenario_complet():
    """Un peu de tout : Excel, dépense à deux, outillage, achat personnel, retour,
    ventes (dont une annulée et une avec reprise), acomptes, règlements."""
    depenses = (
        *excel.EXPENSES,
        expense("81.37", [(M, "40.69"), (A, "40.68")], [line("81.37", vehicle=3)]),
        expense("10.00", [(A, "6.67"), (M, "3.33")], [line("1", vehicle=1), line("1"), line("1")]),
        expense("200", [(M, "200")], [line("200", vehicle=None)]),
        expense("49.90", [(A, "49.90")], [line("49.90", vehicle=None)], is_personal=True),
        expense(
            "48.20",
            [(A, "48.20")],
            [line("21.99", vehicle=2, refunds=[RefundInput(A, D("21.99"))]), line("26.21", 2)],
        ),
        expense("700", [(M, "350"), (A, "350")], [line("700", vehicle=4)]),
    )
    ventes = (
        sale("2000", [(A, "1300")], vehicle=2, trade_in="700"),
        sale("1200", [(M, "1200")], vehicle=3, cancelled=True),
        sale("1100", [(M, "1000")], vehicle=1),
    )
    acomptes = (
        DepositInput(A, D("100"), DEPOSIT_APPLIED, vehicle=1),
        DepositInput(M, D("150"), DEPOSIT_KEPT, vehicle=3),
        DepositInput(M, D("80"), DEPOSIT_REFUNDED, vehicle=3),
        DepositInput(A, D("50"), DEPOSIT_ACTIVE, vehicle=5),
    )
    reglements = (SettlementInput(A, M, D("205.44")), SettlementInput(M, A, D("205.44")))
    return depenses, ventes, acomptes, reglements


def scenario_acomptes_partages():
    """Acomptes reçus par les deux associés ou en plusieurs fois (D89) : un `DepositInput`
    par versement. Véhicule 1 vendu (acompte déduit, cas 21) ; véhicule 2 réservé (cas 22) ;
    véhicule 3 acompte conservé (cas 23) ; véhicule 4 acompte rendu (cas 24)."""
    depenses = tuple(
        expense("1000", [(M, "1000")], [line("1000", vehicle=v)]) for v in (1, 2, 3, 4)
    )
    ventes = (sale("2000", [(A, "1700")], vehicle=1),)
    acomptes = tuple(
        DepositInput(associe, D(montant), etat, vehicle=v, key=v)
        for v, etat in (
            (1, DEPOSIT_APPLIED),
            (2, DEPOSIT_ACTIVE),
            (3, DEPOSIT_KEPT),
            (4, DEPOSIT_REFUNDED),
        )
        for associe, montant in ((M, "200"), (A, "100"))
    )
    return depenses, ventes, acomptes, ()


SCENARIOS = {
    "excel": (excel.EXPENSES, (), (), ()),
    "complet": scenario_complet(),
    "acomptes_partages": scenario_acomptes_partages(),
}


@pytest.fixture(params=SCENARIOS, ids=str)
def scenario(request):
    return SCENARIOS[request.param]


def test_invariant_1_payments_total_and_allocated_agree(scenario):
    depenses, *_ = scenario
    for depense in depenses:
        assert sum(p.amount for p in depense.payments) == depense.total_paid
        assert sum(allocate_shipping(depense)) == depense.total_paid
        assert expense_problems(depense) == []


def test_invariant_2_partner_shares_add_up_to_each_line(scenario):
    depenses, *_ = scenario
    for depense in depenses:
        couts = expense_line_costs(depense)
        for cout in couts:
            assert sum(cout.shares.values()) == cout.allocated
        for paiement in depense.payments if couts else ():
            assert sum(c.shares.get(paiement.partner, ZERO) for c in couts) == paiement.amount


def test_invariant_3_costs_add_up_to_money_spent(scenario):
    depenses, *_ = scenario
    couts = vehicle_costs(depenses)
    depense_nette = sum(
        (
            d.total_paid - sum((r.amount for ligne in d.lines for r in ligne.refunds), ZERO)
            for d in depenses
            if not d.is_personal
        ),
        ZERO,
    )
    assert sum((c.total for c in couts.values()), ZERO) == depense_nette
    for cout in couts.values():
        assert sum(cout.by_partner.values(), ZERO) == cout.total


def test_invariant_4_settling_the_balance_gives_zero(scenario):
    depenses, ventes, acomptes, reglements = scenario
    donnees = {"expenses": depenses, "sales": ventes, "deposits": acomptes}
    du = solde(**donnees, settlements=reglements)
    assert not du.is_settled
    reglement = SettlementInput(du.debtor, du.creditor, du.amount)
    assert solde(**donnees, settlements=(*reglements, reglement)) == SETTLED


def test_invariant_5_balance_does_not_depend_on_order(scenario):
    depenses, ventes, acomptes, reglements = scenario
    attendu = solde(expenses=depenses, sales=ventes, deposits=acomptes, settlements=reglements)
    hasard = random.Random(1234)
    for _ in range(20):
        melange = [list(depenses), list(ventes), list(acomptes), list(reglements)]
        for liste in melange:
            hasard.shuffle(liste)
        # Inverser aussi l'ordre des payeurs d'une dépense change les arrondis par ligne,
        # jamais le solde.
        melange[0] = [
            ExpenseInput(d.total_paid, d.payments[::-1], d.lines, d.is_personal, d.key)
            for d in melange[0]
        ]
        depenses_m, ventes_m, acomptes_m, reglements_m = melange
        assert (
            solde(
                expenses=depenses_m, sales=ventes_m, deposits=acomptes_m, settlements=reglements_m
            )
            == attendu
        )


def test_invariant_6_no_float_anywhere(scenario):
    depenses, ventes, acomptes, reglements = scenario
    positions = partner_positions(
        PARTNERS, expenses=depenses, sales=ventes, deposits=acomptes, settlements=reglements
    )
    valeurs = [balance(positions).amount]
    for position in positions.values():
        valeurs += [position.paid_out, position.received, position.value]
    for cout in vehicle_costs(depenses).values():
        valeurs += [cout.total, *cout.by_partner.values()]
    for depense in depenses:
        for cout in expense_line_costs(depense):
            valeurs += [cout.allocated, cout.refunded, cout.net, *cout.shares.values()]
    assert valeurs
    assert all(type(valeur) is D for valeur in valeurs)
    assert all(valeur == valeur.quantize(D("0.01")) for valeur in valeurs)


# --- Contrôles des associés et des acomptes --------------------------------------------


def test_partner_problems_when_not_exactly_two_partners():
    assert partner_problems((M,)) == ["il faut exactement deux associés actifs, il y en a 1"]
    assert partner_problems(PARTNERS) == []


@pytest.mark.parametrize(
    "donnees",
    [
        {"expenses": [expense("10", [("Paul", "10")], [line("10")])]},
        {
            "expenses": [
                expense("10", [(M, "10")], [line("10", refunds=[RefundInput("Paul", D("1"))])])
            ]
        },
        {"sales": [sale("100", [("Paul", "100")])]},
        {"deposits": [DepositInput("Paul", D("50"), DEPOSIT_ACTIVE)]},
        {"settlements": [SettlementInput("Paul", M, D("5"))]},
    ],
    ids=["paiement", "remboursement", "encaissement", "acompte", "règlement"],
)
def test_partner_problems_names_an_inactive_partner_with_money(donnees):
    """Un associé inactif qui paie, reçoit ou règle quoi que ce soit est signalé, par son nom."""
    assert partner_problems(PARTNERS, **donnees, names={"Paul": "Paul D."}) == [
        "Paul D. n'est plus un associé actif mais a de l'argent dans les comptes"
    ]


def test_partner_problems_ignore_what_does_not_count():
    """Achat personnel, vente annulée, acompte rendu : aucun argent dans les comptes."""
    problemes = partner_problems(
        PARTNERS,
        expenses=[expense("10", [("Paul", "10")], [line("10")], is_personal=True)],
        sales=[sale("100", [("Paul", "100")], cancelled=True)],
        deposits=[DepositInput("Paul", D("50"), DEPOSIT_REFUNDED)],
    )
    assert problemes == []


def test_deposit_problems():
    vente = SaleInput(D("2600"), vehicle=1, key=10)
    applique = DepositInput(M, D("200"), DEPOSIT_APPLIED, vehicle=1, sale=10)

    assert deposit_problems(applique, vente) == []
    assert deposit_problems(applique, None) == ["acompte déduit d'une vente introuvable"]
    assert deposit_problems(applique, SaleInput(D("2600"), vehicle=1, cancelled=True)) == [
        "acompte déduit d'une vente annulée (à passer en « conservé » ou « rendu »)"
    ]
    assert deposit_problems(applique, SaleInput(D("2600"), vehicle=2)) == [
        "acompte déduit de la vente d'un autre véhicule"
    ]
    assert deposit_problems(DepositInput(M, D("200"), DEPOSIT_ACTIVE), None) == []


REPRISE = SaleInput(
    D("2000.00"), (Receipt(M, D("1299.99")),), trade_in_value=D("700.01"), trade_in_vehicle=2
)


def achat_reprise(payments, total="700.01", is_personal=False):
    return expense(total, payments, [line(total, vehicle=2)], is_personal=is_personal)


@pytest.mark.finance
def test_a_neutral_trade_in_has_no_problem():
    achat = achat_reprise([(M, "350.01"), (A, "350.00")])
    assert trade_in_problems(REPRISE, [achat], PARTNERS) == []


@pytest.mark.finance
def test_sales_without_trade_in_or_cancelled_are_not_checked():
    assert trade_in_problems(sale("2000", [(M, "2000")]), [], PARTNERS) == []
    annulee = SaleInput(D("2000"), trade_in_value=D("700"), trade_in_vehicle=2, cancelled=True)
    assert trade_in_problems(annulee, [], PARTNERS) == []


@pytest.mark.finance
def test_a_trade_in_value_without_vehicle_is_a_problem():
    assert trade_in_problems(sale("2000", [(M, "1300")], trade_in="700"), [], PARTNERS) == [
        "valeur de reprise sans véhicule repris"
    ]


@pytest.mark.finance
@pytest.mark.parametrize("nombre", [0, 2])
def test_a_trade_in_needs_exactly_one_purchase(nombre):
    achats = [achat_reprise([(M, "350.01"), (A, "350.00")])] * nombre
    assert trade_in_problems(REPRISE, achats, PARTNERS) == [
        f"le véhicule repris a {nombre} achat(s) au lieu d'un seul"
    ]


@pytest.mark.finance
def test_a_trade_in_purchase_of_another_amount_is_a_problem():
    achat = achat_reprise([(M, "350.00"), (A, "350.00")], total="700.00")
    assert trade_in_problems(REPRISE, [achat], PARTNERS) == [
        f"l'achat du véhicule repris (700,00{E}) ne fait pas la valeur de reprise (700,01{E})",
        "l'achat du véhicule repris n'est pas payé comme la reprise est partagée "
        f"(Mathis 350,01{E}, Axel 350,00{E})",
    ]


@pytest.mark.finance
@pytest.mark.parametrize(
    "payeurs", [[(M, "700.01")], [(M, "350.00"), (A, "350.01")], [(A, "350.01"), (M, "350.00")]]
)
def test_a_trade_in_purchase_paid_otherwise_is_a_problem(payeurs):
    """Le centime impair va au premier associé, comme pour la part « reçue » de la reprise."""
    probleme = trade_in_problems(REPRISE, [achat_reprise(payeurs)], PARTNERS, {M: "Mathis"})
    assert probleme == [
        "l'achat du véhicule repris n'est pas payé comme la reprise est partagée "
        f"(Mathis 350,01{E}, Axel 350,00{E})"
    ]


@pytest.mark.finance
def test_a_personal_trade_in_purchase_is_a_problem():
    achat = achat_reprise([(M, "350.01"), (A, "350.00")], is_personal=True)
    assert trade_in_problems(REPRISE, [achat], PARTNERS) == [
        "l'achat du véhicule repris est marqué comme achat personnel"
    ]


@pytest.mark.finance
@pytest.mark.parametrize("valeur", ["0.02", "0.03", "700.00", "700.01"])
def test_trade_in_is_possible_when_each_partner_pays_a_share(valeur):
    assert trade_in_problem(D(valeur), PARTNERS) is None


@pytest.mark.finance
def test_a_trade_in_of_one_cent_cannot_be_shared():
    probleme = trade_in_problem(D("0.01"), PARTNERS)
    assert "ne se partage pas" in probleme
    assert f"0,02{E}" in probleme


@pytest.mark.finance
@pytest.mark.parametrize("valeur", ["0.00", "-1.00"])
def test_a_trade_in_must_be_above_zero(valeur):
    assert trade_in_problem(D(valeur), PARTNERS) == (
        "La valeur de reprise doit être plus grande que 0 €."
    )


@pytest.mark.finance
@pytest.mark.parametrize("associes", [(), (M,), (M, A, "troisième")])
def test_a_trade_in_needs_exactly_two_partners(associes):
    assert "Préviens Mathis" in trade_in_problem(D("700"), associes)


def test_odd_trade_in_value_stays_neutral():
    """Reprise à 700,01 € : le centime impair va au premier associé des deux côtés
    (part de la reprise « reçue » et paiement de l'achat) : l'effet net reste nul."""
    vente = sale("2000.01", [(A, "1300")], vehicle=2, trade_in="700.01")
    achat = expense("700.01", [(M, "350.01"), (A, "350.00")], [line("700.01", vehicle=4)])
    sans_reprise = partner_positions(PARTNERS, sales=[sale("1300", [(A, "1300")], vehicle=2)])
    avec_reprise = partner_positions(PARTNERS, sales=[vente], expenses=[achat])
    assert {a: p.value for a, p in avec_reprise.items()} == {
        a: p.value for a, p in sans_reprise.items()
    }


@pytest.mark.parametrize(
    ("total", "paiements", "attendu"),
    [
        ("1500", [(M, "1500")], None),
        ("1500.01", [(M, "750.01"), (A, "750.00")], None),
        ("0", [(M, "0")], "Le montant doit être plus grand que 0 €."),
        ("-5", [(M, "-5")], "Le montant doit être plus grand que 0 €."),
        ("1500", [], "Indique qui a payé."),
        (
            "1500",
            [(M, "1000"), (M, "500")],
            "Chaque associé ne peut apparaître qu'une fois parmi les payeurs.",
        ),
        (
            "1500",
            [(M, "1500"), (A, "0")],
            "Chaque part payée doit être plus grande que 0 €. "
            "Si un seul associé a payé, choisis son prénom.",
        ),
        (
            "1500",
            [(M, "1000"), (A, "400")],
            f"Les parts payées font 1{NBSP}400,00{E} au lieu de 1{NBSP}500,00{E}. "
            "Corrige l'un des montants.",
        ),
    ],
    ids=["seul", "à deux", "zéro", "négatif", "personne", "doublon", "part nulle", "écart"],
)
def test_payment_problem(total, paiements, attendu):
    payments = [Payment(associe, D(montant)) for associe, montant in paiements]
    assert payment_problem(D(total), payments) == attendu


def test_combined_cost_of_a_purchase_line_in_a_shared_order():
    """L'achat (1 000 €) et une pièce (100 €) payés ensemble 1 111 €, moitié chacun :
    l'achat porte 1 010 € (frais de port au prorata), partagés 505 / 505."""
    achat = LineInput(D("1"), D("1000"), 1, key="achat")
    piece = LineInput(D("1"), D("100"), 1, key="piece")
    depense = ExpenseInput(
        D("1111"), (Payment(M, D("555.50")), Payment(A, D("555.50"))), (achat, piece)
    )
    couts = [c for c in expense_line_costs(depense) if c.line.key == "achat"]
    resultat = combined_cost(couts)
    assert resultat.total == D("1010.00")
    assert resultat.by_partner == {M: D("505.00"), A: D("505.00")}


def test_combined_cost_deducts_refunds_from_who_received_them():
    ligne = LineInput(D("1"), D("300"), 1, refunds=(RefundInput(M, D("50")),))
    resultat = combined_cost(
        expense_line_costs(ExpenseInput(D("300"), (Payment(M, D("300")),), (ligne,)))
    )
    assert resultat.total == D("250.00")
    assert resultat.by_partner == {M: D("250.00")}


def test_combined_cost_of_nothing_is_zero():
    assert combined_cost([]) == VehicleCost()


# --- Saisie d'une dépense : aperçu pendant la saisie (tâche 1.4) ----------------------


def test_lines_sum_rounds_each_line():
    """La somme des lignes additionne les totaux arrondis au centime de chaque ligne."""
    lignes = [LineInput(D("3"), D("6.15")), LineInput(D("1.5"), D("0.33"))]
    assert lines_sum(lignes) == D("18.95")
    assert lines_sum([]) == ZERO


def test_preview_with_shipping_spreads_it_over_the_lines():
    """86 € payés pour 71,90 € + 8,40 € de pièces : 5,70 € de frais de port répartis."""
    apercu = expense_preview(D("86.00"), [line("71.90"), line("8.40")])
    assert apercu.lines_sum == D("80.30")
    assert apercu.shipping == D("5.70")
    assert apercu.allocated == (D("77.00"), D("9.00"))
    assert sum(apercu.allocated) == D("86.00")
    assert not apercu.total_is_lines_sum


def test_preview_without_amount_paid_uses_the_lines_sum():
    """Montant payé laissé vide : il vaut la somme des lignes, sans frais de port."""
    apercu = expense_preview(None, [line("40.00"), line("2.50")])
    assert apercu.total_paid == D("42.50")
    assert apercu.shipping == ZERO
    assert apercu.allocated == (D("40.00"), D("2.50"))
    assert apercu.total_is_lines_sum


def test_preview_with_a_discount():
    apercu = expense_preview(D("90.00"), [line("60.00"), line("40.00")])
    assert apercu.shipping == D("-10.00")
    assert apercu.allocated == (D("54.00"), D("36.00"))


@pytest.mark.parametrize("total", [None, "0.00"])
def test_preview_allocates_nothing_while_amount_is_not_positive(total):
    """Rien à répartir tant que le montant payé n'est pas positif (lignes vides ou à 0 €)."""
    apercu = expense_preview(None if total is None else D(total), [line("0")])
    assert apercu.allocated == ()
    assert expense_preview(D("10.00"), []).allocated == ()


# --- Retours et remboursements --------------------------------------------------------


def test_refund_left_is_what_remains_never_negative():
    assert refund_left(D("21.99"), ZERO) == D("21.99")
    assert refund_left(D("21.99"), D("10.00")) == D("11.99")
    assert refund_left(D("21.99"), D("21.99")) == ZERO
    assert refund_left(D("10.00"), D("11.00")) == ZERO


@pytest.mark.parametrize(
    ("montant", "deja", "accepte"),
    [
        ("21.99", "0", True),  # remboursement complet (cas 14)
        ("10.00", "0", True),  # partiel
        ("11.99", "10.00", True),  # le reste, après un partiel
        ("0.00", "0", True),  # « à préciser »
        ("0.00", "21.99", True),  # « à préciser » même sur une ligne déjà remboursée
        ("22.00", "0", False),  # plus que la ligne
        ("12.00", "10.00", False),  # plus que le reste
        ("-1.00", "0", False),
    ],
)
def test_refund_problem(montant, deja, accepte):
    probleme = refund_problem(D(montant), D("21.99"), D(deja))
    assert (probleme is None) == accepte


def test_refund_problem_says_what_remains():
    assert refund_problem(D("12.00"), D("21.99"), D("10.00")) == (
        f"Il ne reste que 11,99{E} à rembourser sur cette ligne. Corrige le montant."
    )
    assert refund_problem(D("-1"), D("21.99"), ZERO) == "Le montant ne peut pas être négatif."


@pytest.mark.parametrize(
    ("rembourse", "complet"),
    [("21.99", True), ("10.00", False), ("0.00", False), ("22.00", True)],
)
def test_only_a_full_refund_returns_the_line(rembourse, complet):
    assert is_fully_refunded(D("21.99"), D(rembourse)) is complet


def test_partial_refund_lowers_the_cost_and_goes_to_who_received_it():
    """Ligne de 21,99 € payée par Axel, 10 € remboursés à Mathis : coût net 11,99 €,
    Mathis a reçu 10 € pour le garage."""
    depense = expense("21.99", [(A, "21.99")], [line("21.99", refunds=[RefundInput(M, D("10"))])])
    (cout,) = expense_line_costs(depense)
    assert cout.net == D("11.99")
    positions = partner_positions(PARTNERS, [depense])
    assert positions[M].received == D("10")
    assert positions[A].paid_out == D("21.99")


def test_refund_to_be_specified_changes_nothing():
    """Un remboursement de 0,00 € (« à préciser ») ne change ni le coût ni le solde."""
    sans = expense("21.99", [(A, "21.99")], [line("21.99")])
    avec = expense("21.99", [(A, "21.99")], [line("21.99", refunds=[RefundInput(A, ZERO)])])
    assert expense_line_costs(avec)[0].net == expense_line_costs(sans)[0].net == D("21.99")
    assert solde(expenses=[avec]) == solde(expenses=[sans])


@pytest.mark.parametrize(
    ("rembourse", "etait_retournee", "a_preciser", "retournee"),
    [
        ("21.99", False, False, True),  # entièrement remboursée
        ("10.00", False, False, False),  # partiel
        ("0.00", False, True, False),  # « à préciser » ne fait pas entrer en « Retourné »
        ("0.00", True, True, True),  # … ni sortir (reprise de l'Excel, D45)
        ("10.00", True, True, True),
        ("10.00", True, False, False),  # retour corrigé : plus entièrement remboursée
    ],
)
def test_is_returned(rembourse, etait_retournee, a_preciser, retournee):
    assert (
        is_returned(
            D("21.99"), D(rembourse), was_returned=etait_retournee, to_be_specified=a_preciser
        )
        is retournee
    )


def test_refund_room_does_not_count_the_refund_being_corrected():
    marge = refund_room(D("21.99"), D("31.99"), current=D("21.99"))
    assert (marge.allocated, marge.others, marge.left) == (D("21.99"), D("10.00"), D("11.99"))
    assert refund_room(D("21.99"), D("10.00")).left == D("11.99")


# --- Vente : reste à encaisser, aperçu, récapitulatif --------------------------------

ASSOCIES = (excel.M, excel.A)
# Cas 1 : Mathis a avancé 1 800 €, Axel 200 €.
COUT_CAS_1 = VehicleCost(D("2000.00"), {excel.M: D("1800.00"), excel.A: D("200.00")})


def acompte(status, amount="200.00", partner=excel.M):
    return DepositInput(partner, D(amount), status)


def test_left_to_receive_deducts_active_and_applied_deposits_and_the_trade_in():
    assert sale_left_to_receive(D("2600.00")) == D("2600.00")
    assert sale_left_to_receive(D("2600.00"), [acompte(DEPOSIT_ACTIVE)]) == D("2400.00")
    assert sale_left_to_receive(D("2600.00"), [acompte(DEPOSIT_APPLIED)]) == D("2400.00")
    assert sale_left_to_receive(D("2000.00"), (), D("700.00")) == D("1300.00")


@pytest.mark.parametrize("status", [DEPOSIT_REFUNDED, DEPOSIT_KEPT])
def test_left_to_receive_ignores_refunded_and_kept_deposits(status):
    """Un acompte rendu ou conservé d'une ancienne réservation n'est pas déduit du prix."""
    assert sale_left_to_receive(D("2600.00"), [acompte(status)]) == D("2600.00")


def test_left_to_receive_can_be_zero_or_negative():
    assert sale_left_to_receive(D("200.00"), [acompte(DEPOSIT_ACTIVE)]) == ZERO
    assert sale_left_to_receive(D("150.00"), [acompte(DEPOSIT_ACTIVE)]) == D("-50.00")


def test_sale_price_problem():
    assert sale_price_problem(D("200.00"), ZERO) is None
    assert "plus grand que 0" in sale_price_problem(D("0.00"), ZERO)
    assert "plus grand que 0" in sale_price_problem(D("-5.00"), D("-5.00"))
    probleme = sale_price_problem(D("150.00"), D("-50.00"))
    assert f"200,00{NBSP}€" in probleme
    assert "Corrige le prix" in probleme


def test_receipts_problem_nothing_left_to_receive():
    """L'acompte couvre le prix : aucun encaissement attendu, et aucun accepté."""
    assert receipts_problem(ZERO, []) is None
    assert "rien à encaisser" in receipts_problem(ZERO, [Payment(excel.M, D("10.00"))])


def test_receipts_problem_checks_the_amounts():
    assert receipts_problem(D("100.00"), [Payment(excel.M, D("100.00"))]) is None
    assert (
        receipts_problem(D("100.00"), [Payment(excel.M, D("60.00")), Payment(excel.A, D("40.00"))])
        is None
    )
    assert "qui a encaissé" in receipts_problem(D("100.00"), [])
    assert "une fois" in receipts_problem(
        D("100.00"), [Payment(excel.M, D("50.00")), Payment(excel.M, D("50.00"))]
    )
    assert "plus grande que 0" in receipts_problem(
        D("100.00"), [Payment(excel.M, D("100.00")), Payment(excel.A, ZERO)]
    )
    assert "Corrige l'un des montants" in receipts_problem(
        D("100.00"), [Payment(excel.M, D("60.00")), Payment(excel.A, D("30.00"))]
    )


def test_sale_preview_case_1():
    apercu = sale_preview(D("2600.00"), D("2000.00"), ASSOCIES)
    assert apercu.left_to_receive == D("2600.00")
    assert apercu.margin == D("600.00")
    assert apercu.margin_shares == {excel.M: D("300.00"), excel.A: D("300.00")}
    assert apercu.problem is None


def test_sale_preview_case_2_loss():
    apercu = sale_preview(D("1700.00"), D("2000.00"), ASSOCIES)
    assert apercu.margin == D("-300.00")
    assert apercu.margin_shares == {excel.M: D("-150.00"), excel.A: D("-150.00")}
    assert apercu.is_loss
    assert apercu.shown_margin == D("300.00")
    assert apercu.shown_shares == {excel.M: D("150.00"), excel.A: D("150.00")}


def test_margin_display_of_a_loss_drops_the_minus_sign():
    affichage = margin_display(D("-300.00"), {excel.M: D("-150.00"), excel.A: D("-150.00")})
    assert affichage.is_loss
    assert affichage.shown_margin == D("300.00")
    assert affichage.shown_shares == {excel.M: D("150.00"), excel.A: D("150.00")}


def test_margin_display_of_a_profit_keeps_the_amounts():
    affichage = margin_display(D("600.00"), {excel.M: D("300.00"), excel.A: D("300.00")})
    assert not affichage.is_loss
    assert affichage.shown_margin == D("600.00")
    assert affichage.shown_shares == {excel.M: D("300.00"), excel.A: D("300.00")}


def test_margin_display_of_a_zero_margin_is_not_a_loss():
    assert not margin_display(ZERO, {excel.M: ZERO, excel.A: ZERO}).is_loss


def test_margin_display_without_shares():
    affichage = margin_display(D("-300.00"), {})
    assert affichage.is_loss
    assert affichage.shown_shares == {}


def test_sale_preview_of_a_profit_keeps_the_amounts_as_they_are():
    apercu = sale_preview(D("2600.00"), D("2000.00"), ASSOCIES)
    assert not apercu.is_loss
    assert apercu.shown_margin == apercu.margin
    assert apercu.shown_shares == apercu.margin_shares


def test_sale_preview_at_cost_price_is_not_a_loss():
    assert not sale_preview(D("2000.00"), D("2000.00"), ASSOCIES).is_loss


def test_sale_preview_loss_with_an_odd_cent_keeps_every_cent():
    apercu = sale_preview(D("1699.99"), D("2000.00"), ASSOCIES)
    assert apercu.shown_margin == D("300.01")
    assert sorted(apercu.shown_shares.values()) == [D("150.00"), D("150.01")]
    assert sum(apercu.shown_shares.values(), ZERO) == apercu.shown_margin


def test_kept_deposits_total_counts_only_the_kept_deposits():
    assert kept_deposits_total([]) == ZERO
    assert kept_deposits_total(
        [
            acompte(DEPOSIT_KEPT),
            acompte(DEPOSIT_KEPT, "50.00"),
            acompte(DEPOSIT_ACTIVE),
            acompte(DEPOSIT_APPLIED),
            acompte(DEPOSIT_REFUNDED),
        ]
    ) == D("250.00")


def test_sale_preview_deducts_the_deposit_but_keeps_the_full_price_in_the_margin():
    apercu = sale_preview(D("2600.00"), D("2000.00"), ASSOCIES, [acompte(DEPOSIT_ACTIVE)])
    assert apercu.left_to_receive == D("2400.00")
    assert apercu.margin == D("600.00")


def test_sale_preview_counts_a_kept_deposit_in_the_margin():
    """D32 : l'acompte conservé d'une réservation tombée s'ajoute à la marge."""
    apercu = sale_preview(D("2600.00"), D("2000.00"), ASSOCIES, [acompte(DEPOSIT_KEPT)])
    assert apercu.left_to_receive == D("2600.00")
    assert apercu.margin == D("800.00")
    assert apercu.kept_deposits == D("200.00")


def test_sale_preview_kept_deposit_reduces_a_loss():
    apercu = sale_preview(D("1500.00"), D("2000.00"), ASSOCIES, [acompte(DEPOSIT_KEPT)])
    assert apercu.is_loss
    assert apercu.shown_margin == D("300.00")
    assert apercu.kept_deposits == D("200.00")


def test_sale_preview_reports_a_price_below_the_deposit():
    apercu = sale_preview(D("150.00"), D("2000.00"), ASSOCIES, [acompte(DEPOSIT_ACTIVE)])
    assert apercu.left_to_receive == D("-50.00")
    assert "Corrige le prix" in apercu.problem


def test_sale_recap_case_1():
    """Le tableau du cahier des charges : Mathis a encaissé, il doit 500 € à Axel."""
    vente = SaleInput(D("2600.00"), (Receipt(excel.M, D("2600.00")),))
    recap = sale_recap(COUT_CAS_1, vente, [], ASSOCIES)

    assert recap.margin == D("600.00")
    mathis, axel = recap.shares[excel.M], recap.shares[excel.A]
    assert (mathis.advanced, mathis.margin_share, mathis.total_share) == (
        D("1800.00"),
        D("300.00"),
        D("2100.00"),
    )
    assert (axel.advanced, axel.margin_share, axel.total_share) == (
        D("200.00"),
        D("300.00"),
        D("500.00"),
    )
    assert (mathis.received, axel.received) == (D("2600.00"), ZERO)
    assert recap.balance == Balance(excel.M, excel.A, D("500.00"))


def test_sale_recap_case_2_loss():
    vente = SaleInput(D("1700.00"), (Receipt(excel.M, D("1700.00")),))
    recap = sale_recap(COUT_CAS_1, vente, [], ASSOCIES)
    assert recap.margin == D("-300.00")
    assert recap.shares[excel.M].total_share == D("1650.00")
    assert recap.balance == Balance(excel.M, excel.A, D("50.00"))


def test_sale_recap_case_11_deposit_then_sale():
    """Cas 11 : Axel a tout payé, acompte de 200 € reçu par Mathis, 2 400 € par Axel."""
    cout = VehicleCost(D("2000.00"), {excel.A: D("2000.00")})
    vente = SaleInput(D("2600.00"), (Receipt(excel.A, D("2400.00")),))
    recap = sale_recap(cout, vente, [acompte(DEPOSIT_APPLIED)], ASSOCIES)

    assert recap.shares[excel.M].received == D("200.00")
    assert recap.shares[excel.A].received == D("2400.00")
    assert recap.shares[excel.M].advanced == ZERO
    assert recap.balance == Balance(excel.A, excel.M, D("100.00"))


def test_sale_recap_ignores_a_refunded_deposit_and_counts_a_kept_one():
    vente = SaleInput(D("2600.00"), (Receipt(excel.M, D("2600.00")),))
    rendu = sale_recap(COUT_CAS_1, vente, [acompte(DEPOSIT_REFUNDED)], ASSOCIES)
    assert rendu.shares[excel.M].received == D("2600.00")
    assert rendu.margin == D("600.00")

    conserve = sale_recap(COUT_CAS_1, vente, [acompte(DEPOSIT_KEPT, partner=excel.A)], ASSOCIES)
    assert conserve.shares[excel.A].received == D("200.00")
    assert conserve.margin == D("800.00")


def test_sale_recap_shares_the_trade_in():
    vente = SaleInput(D("2000.00"), (Receipt(excel.M, D("1300.00")),), trade_in_value=D("700.00"))
    recap = sale_recap(COUT_CAS_1, vente, [], ASSOCIES)
    assert recap.shares[excel.M].received == D("1650.00")
    assert recap.shares[excel.A].received == D("350.00")


def test_sale_recap_balance_reads_the_table_with_an_odd_trade_in():
    """Reprise de 700,01 € sur une vente de 2 000 € : le véhicule seul suit son tableau
    (Mathis a reçu 1 650,00 € pour une part de 1 800,00 €). Le garage, où l'achat du véhicule
    repris (350,01 € + 350,00 €) ajoute un demi-centime, garde sa règle d'arrondi (D33)."""
    vente = SaleInput(D("2000.00"), (Receipt(M, D("1299.99")),), trade_in_value=D("700.01"))
    cout = VehicleCost(D("2000.00"), {M: D("1800.00"), A: D("200.00")})
    recap = sale_recap(cout, vente, [], PARTNERS)

    assert recap.shares[M].received == D("1650.00")
    assert recap.shares[A].received == D("350.00")
    assert recap.balance == Balance(A, M, D("150.00"))
    garage = solde(
        expenses=[
            expense("1800.00", [(M, "1800.00")], [line("1800.00")]),
            expense("200.00", [(A, "200.00")], [line("200.00")]),
            expense("700.01", [(M, "350.01"), (A, "350.00")], [line("700.01", vehicle=2)]),
        ],
        sales=[vente],
    )
    assert garage == Balance(A, M, D("150.01"))


def test_sale_recap_balance_matches_the_table_with_an_odd_margin():
    """Fiche n°3 (relecture de la 1.7) : marge de 731,99 €, Axel a 365,99 € de marge et
    1 930,49 € de part totale sans rien avoir reçu. La phrase « pour ce véhicule seul » dit
    1 930,49 €, comme le tableau ; le solde du garage, lui, arrondit 1 930,495 € à
    1 930,50 € en faveur de celui qui a avancé (D33, inchangée)."""
    cout = VehicleCost(D("2668.01"), {M: D("1103.51"), A: D("1564.50")})
    vente = SaleInput(D("3000.00"), (Receipt(M, D("3000.00")),))
    acomptes = [
        acompte(DEPOSIT_KEPT, partner=M),
        acompte(DEPOSIT_REFUNDED, partner=M),
        acompte(DEPOSIT_KEPT, partner=M),
        acompte(DEPOSIT_REFUNDED, "99.00", partner=M),
    ]
    recap = sale_recap(cout, vente, acomptes, PARTNERS)

    assert recap.margin == D("731.99")
    assert recap.shares[A].total_share == D("1930.49")
    assert recap.shares[M].received - recap.shares[M].total_share == D("1930.49")
    assert recap.balance == Balance(M, A, D("1930.49"))
    positions = {
        M: Position(D("1103.51"), D("3400.00")),
        A: Position(D("1564.50"), ZERO),
    }
    assert balance(positions) == Balance(M, A, D("1930.50"))


@pytest.mark.parametrize(
    ("prix", "reprise"), [("2600.00", "0.00"), ("2600.01", "0.00"), ("2600.01", "700.01")]
)
def test_sale_recap_balance_settles_the_table_exactly(prix, reprise):
    """Invariant : ce que le débiteur a reçu en trop est ce qui manque au créancier."""
    vente = SaleInput(D(prix), (Receipt(excel.M, D(prix) - D(reprise)),), trade_in_value=D(reprise))
    recap = sale_recap(COUT_CAS_1, vente, [], ASSOCIES)
    du = recap.balance
    creancier, debiteur = recap.shares[du.creditor], recap.shares[du.debtor]
    assert creancier.total_share - creancier.received == du.amount
    assert debiteur.received - debiteur.total_share == du.amount


def test_recap_balance_is_settled_when_each_received_their_share():
    parts = {
        M: SalePartnerShare(D("100.00"), D("50.00"), D("150.00")),
        A: SalePartnerShare(D("100.00"), D("50.00"), D("150.00")),
    }
    assert recap_balance(parts).is_settled


def test_sale_recap_total_shares_match_what_came_in():
    """Invariant : les parts totales font tout ce que la vente a rapporté, au centime près."""
    vente = SaleInput(D("2600.01"), (Receipt(excel.M, D("2600.01")),))
    recap = sale_recap(COUT_CAS_1, vente, [], ASSOCIES)
    parts = recap.shares.values()
    assert sum((p.total_share for p in parts), ZERO) == D("2600.01")
    assert sum((p.received for p in parts), ZERO) == D("2600.01")


def test_sale_recap_margin_is_the_sum_of_what_the_partners_earn():
    vente = SaleInput(D("2600.00"), (Receipt(excel.M, D("2600.00")),))
    recap = sale_recap(COUT_CAS_1, vente, [], ASSOCIES)
    assert sum((p.margin_share for p in recap.shares.values()), ZERO) == recap.margin


def test_amounts_by_partner_adds_several_receipts_of_a_partner():
    totaux = amounts_by_partner(
        [
            Payment(excel.M, D("400.00")),
            Payment(excel.A, D("100.00")),
            Payment(excel.M, D("600.00")),
        ]
    )
    assert totaux == {excel.M: D("1000.00"), excel.A: D("100.00")}
    assert list(totaux) == [excel.M, excel.A]
    assert amounts_by_partner([]) == {}


def test_balance_sentence():
    noms = {excel.M: "Mathis", excel.A: "Axel"}
    assert (
        balance_sentence(Balance(excel.A, excel.M, D("205.44")), noms)
        == f"Axel doit 205,44{NBSP}€ à Mathis"
    )
    assert balance_sentence(Balance(None, None, ZERO), noms) == "comptes à l'équilibre"


def test_balance_label_is_the_screen_wording():
    """D67 : l'écran et l'export écrivent « À l'équilibre » ; `controle_comptes` garde son
    écriture (`balance_sentence`), pour que ses comptes rendus restent comparables."""
    noms = {excel.M: "Mathis", excel.A: "Axel"}
    assert balance_label(Balance(None, None, ZERO), noms) == "À l'équilibre"
    assert (
        balance_label(Balance(excel.A, excel.M, D("205.44")), noms)
        == f"Axel doit 205,44{NBSP}€ à Mathis"
    )


def test_sold_status_problems():
    assert sold_status_problems(True, 1) == []
    assert sold_status_problems(False, 0) == []
    assert sold_status_problems(True, 0) == ["vendu sans vente enregistrée"]
    assert sold_status_problems(False, 1) == [
        "vente en cours alors que le véhicule n'est pas vendu"
    ]

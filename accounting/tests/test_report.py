"""Tests des calculs du bilan (D59 à D62) : véhicules, année, associés, argent rangé.

Cas 18 à 20 de `.claude/skills/regles-financieres/exemples.md`.
"""

import dataclasses
import datetime as dt
from decimal import Decimal as D

import pytest

from accounting.services import (
    DEPOSIT_ACTIVE,
    DEPOSIT_KEPT,
    GROUP_ABANDONED,
    GROUP_GARAGE,
    GROUP_SOLD,
    GROUP_TRASH,
    VEHICLE_ABANDONED,
    ZERO,
    DepositInput,
    PartnerReport,
    SettlementInput,
    SignedAmount,
    VehicleState,
    abandoned_in_year,
    balance,
    buckets_problems,
    garage_expenses_for_year,
    invested_breakdown,
    money_buckets,
    partner_positions,
    partner_report,
    partner_report_problems,
    position_details,
    report_margins,
    report_years,
    sold_in_year,
    vehicle_report,
    year_report,
)

from .test_services import DEPENSES_CAS_1, PARTNERS, SCENARIOS, A, M, expense, line, owes, sale

pytestmark = pytest.mark.finance

NOMS = {M: M, A: A}
EN_REPARATION = VehicleState("IN_REPAIR")
VENDU = VehicleState("SOLD")


def abandonne(le=dt.date(2026, 5, 10)):
    return VehicleState(VEHICLE_ABANDONED, abandoned_on=le)


def bilan(expenses=(), sales=(), deposits=(), settlements=(), vehicles=None):
    """Tout le bilan d'un jeu de données : lignes, cases, associés, positions.

    `vehicles` : par défaut, chaque véhicule est « En réparation » (une vente le range
    quand même parmi les vendus)."""
    if vehicles is None:
        cles = {lg.vehicle for d in expenses for lg in d.lines} | {
            x.vehicle for x in (*sales, *deposits)
        }
        vehicles = {cle: EN_REPARATION for cle in cles if cle is not None}
    lignes = vehicle_report(expenses, sales, deposits, vehicles)
    cases = money_buckets(lignes, expenses, deposits)
    details = position_details(PARTNERS, expenses, sales, deposits, settlements)
    positions = {associe: detail.position for associe, detail in details.items()}
    return lignes, cases, partner_report(details, cases, PARTNERS), positions


def assert_d61(associes):
    """Pour chaque associé : ce qui lui revient = déjà revenu + encore dans le garage ± solde,
    et sa ligne « encore dans le garage » est sa moitié à un centime près."""
    for ligne in associes.values():
        assert ligne.due_to_partner == ligne.already_back + ligne.still_in_garage + ligne.balance
        assert abs(ligne.rounding_gap) <= D("0.01")
    assert partner_report_problems(associes, NOMS) == []


# --- Cas 18 : véhicule abandonné avec un acompte conservé -------------------------------


def test_case_18_abandoned_vehicle_with_a_kept_deposit():
    """Coût 1 000 € payé par Mathis ; Axel a reçu un acompte de 200 €, conservé ;
    le véhicule est abandonné. Perte 800 € (400 € chacun), Axel doit 600 € à Mathis."""
    depenses = [expense("1000", [(M, "1000")], [line("1000")])]
    acompte = DepositInput(A, D("200"), DEPOSIT_KEPT, vehicle=1)
    lignes, cases, associes, positions = bilan(
        depenses, deposits=[acompte], vehicles={1: abandonne()}
    )

    (vehicule,) = lignes
    assert vehicule.group == GROUP_ABANDONED
    assert vehicule.loss == D("800.00")
    assert vehicule.margin is None
    assert cases.profit == D("-800.00")
    assert cases.still_in_garage == ZERO
    assert balance(positions) == owes(A, M, "600.00")
    assert associes[M] == PartnerReport(
        put_in=D("1000.00"),
        profit_share=D("-400.00"),
        already_back=ZERO,
        still_in_garage=ZERO,
        balance=D("600.00"),
        rounding_gap=ZERO,
    )
    assert associes[A].due_to_partner == D("-400.00")
    assert associes[A].already_back == D("200.00")
    assert associes[A].balance == D("-600.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


def test_vehicle_back_from_abandoned_has_no_loss_anymore():
    """D59 : revenu au garage, le véhicule n'a plus de perte ; son coût est immobilisé et
    son acompte conservé reste « en attente » d'une vente ou d'un abandon."""
    depenses = [expense("1000", [(M, "1000")], [line("1000")])]
    acompte = DepositInput(A, D("200"), DEPOSIT_KEPT, vehicle=1)
    lignes, cases, associes, positions = bilan(depenses, deposits=[acompte])

    assert lignes[0].group == GROUP_GARAGE
    assert lignes[0].loss is None
    assert cases.profit == ZERO
    assert cases.pending_deposits == D("200")
    assert cases.still_in_garage == D("800.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


# --- Acompte en cours : il est déjà dans une poche, pas encore dans une marge -----------


def test_active_deposit_lowers_what_is_still_in_the_garage():
    """Coût 1 500 € payé par Mathis ; Axel a reçu un acompte de 300 € (véhicule réservé).
    Encore dans le garage : 1 500 − 300 = 1 200 €, 600 € chacun ; Axel doit 900 €."""
    depenses = [expense("1500", [(M, "1500")], [line("1500")])]
    acompte = DepositInput(A, D("300"), DEPOSIT_ACTIVE, vehicle=1)
    _, cases, associes, positions = bilan(
        depenses, deposits=[acompte], vehicles={1: VehicleState("RESERVED")}
    )

    assert cases.in_garage == D("1500.00")
    assert cases.pending_deposits == D("300")
    assert cases.still_in_garage == D("1200.00")
    assert cases.profit == ZERO
    assert balance(positions) == owes(A, M, "900.00")
    assert associes[M].due_to_partner == D("1500.00")
    assert associes[M].still_in_garage == D("600.00")
    assert associes[M].balance == D("900.00")
    assert associes[A].already_back == D("300")
    assert associes[A].still_in_garage == D("600.00")
    assert associes[A].balance == D("-900.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


# --- Cas 19 : une année avec une vente, un abandon et de l'outillage -------------------


def dated(depense, date):
    return dataclasses.replace(depense, date=date)


def test_case_19_year_with_a_sale_an_abandon_and_tooling():
    """2026 : vente du cas 1 (marge 600 €), abandon du cas 18 (perte 800 €), outillage
    200 €. Bénéfice 600 − 800 − 200 = −400 €, soit −200 € chacun."""
    en_2026 = dt.date(2026, 2, 1)
    depenses = [
        *(dated(d, en_2026) for d in DEPENSES_CAS_1),
        dated(expense("1000", [(M, "1000")], [line("1000", vehicle=2)]), en_2026),
        dated(expense("200", [(M, "200")], [line("200", vehicle=None)]), en_2026),
        dated(expense("49.90", [(A, "49.90")], [line("49.90", None)], is_personal=True), en_2026),
    ]
    vente = dataclasses.replace(sale("2600", [(M, "2600")]), date=dt.date(2026, 3, 1))
    annulee = dataclasses.replace(
        sale("9999", [(M, "9999")], cancelled=True), date=dt.date(2026, 2, 1)
    )
    acompte = DepositInput(A, D("200"), DEPOSIT_KEPT, vehicle=2)
    vehicules = {
        1: VehicleState("SOLD", purchased_on=dt.date(2026, 1, 10)),
        2: abandonne(dt.date(2026, 5, 10)),
    }
    lignes = vehicle_report(depenses, [vente, annulee], [acompte], vehicules)
    annee = year_report(2026, lignes, depenses, PARTNERS)

    assert annee.invested == D("3200.00")  # 2 000 + 1 000 + 200, sans l'achat personnel
    assert annee.sales_total == D("2600")  # la vente annulée ne compte pas
    assert annee.margins == D("600.00")
    assert annee.losses == D("800.00")
    assert annee.garage_expenses == D("200.00")
    assert annee.profit == D("-400.00")
    assert annee.shares == {M: D("-200.00"), A: D("-200.00")}
    assert annee.average_days == 50

    precedente = year_report(2025, lignes, depenses, PARTNERS)
    assert precedente.profit == ZERO
    assert precedente.invested == ZERO
    assert precedente.average_days is None


def test_profits_of_all_years_add_up_to_the_cumulated_profit():
    """Chaque marge, perte et dépense du garage est rangée dans une année et une seule."""
    depenses = [
        dated(expense("2000", [(M, "2000")], [line("2000", vehicle=1)]), dt.date(2025, 3, 1)),
        dated(expense("1000", [(A, "1000")], [line("1000", vehicle=2)]), dt.date(2025, 6, 1)),
        dated(expense("700", [(M, "700")], [line("700", vehicle=3)]), dt.date(2026, 1, 5)),
        dated(expense("120", [(A, "120")], [line("120", vehicle=None)]), dt.date(2025, 7, 1)),
        dated(expense("80.01", [(M, "80.01")], [line("80.01", vehicle=None)]), dt.date(2026, 2, 1)),
    ]
    ventes = [
        dataclasses.replace(sale("2600", [(M, "2600")], vehicle=1), date=dt.date(2026, 1, 20)),
        dataclasses.replace(sale("650", [(A, "650")], vehicle=3), date=dt.date(2026, 4, 2)),
    ]
    acomptes = [DepositInput(A, D("150"), DEPOSIT_KEPT, vehicle=2)]
    vehicules = {1: VENDU, 2: abandonne(dt.date(2026, 9, 9)), 3: VENDU}
    lignes = vehicle_report(depenses, ventes, acomptes, vehicules)
    cases = money_buckets(lignes, depenses, acomptes)

    par_annee = [year_report(annee, lignes, depenses, PARTNERS) for annee in (2025, 2026)]
    assert sum(annee.profit for annee in par_annee) == cases.profit
    assert sum(annee.invested for annee in par_annee) == D("3900.01")


def test_loss_counts_in_the_year_of_the_abandon():
    """D59 : un véhicule acheté en 2025 et abandonné en 2026 perd en 2026."""
    depenses = [dated(expense("500", [(M, "500")], [line("500")]), dt.date(2025, 9, 1))]
    lignes = vehicle_report(depenses, [], [], {1: abandonne(dt.date(2026, 1, 15))})

    assert year_report(2025, lignes, depenses, PARTNERS).losses == ZERO
    assert year_report(2025, lignes, depenses, PARTNERS).invested == D("500.00")
    assert year_report(2026, lignes, depenses, PARTNERS).losses == D("500.00")


def test_average_duration_is_rounded_to_the_day():
    """Deux ventes de 10 et 11 jours : 10,5 jours arrondis à 11 (ROUND_HALF_UP)."""
    depenses = [
        expense("100", [(M, "100")], [line("100", vehicle=1)]),
        expense("100", [(M, "100")], [line("100", vehicle=2)]),
    ]
    ventes = [
        dataclasses.replace(sale("150", [(M, "150")], vehicle=1), date=dt.date(2026, 4, 11)),
        dataclasses.replace(sale("150", [(M, "150")], vehicle=2), date=dt.date(2026, 4, 12)),
    ]
    debut = VehicleState("SOLD", purchased_on=dt.date(2026, 4, 1))
    lignes = vehicle_report(depenses, ventes, [], {1: debut, 2: debut})

    assert [lg.days for lg in lignes] == [10, 11]
    assert year_report(2026, lignes, depenses, PARTNERS).average_days == 11


# --- Cas 20 : bilan par associé avant et après le règlement ----------------------------


def test_case_20_partner_report_before_and_after_the_settlement():
    """Cas 1 et outillage de 200 € payé par Mathis : bénéfice 400 €, 200 € chacun.
    Mathis doit 400 € à Axel ; une fois réglé, « déjà revenu » est ce qui lui revient."""
    depenses = [*DEPENSES_CAS_1, expense("200", [(M, "200")], [line("200", vehicle=None)])]
    vente = sale("2600", [(M, "2600")])

    _, cases, avant, positions = bilan(depenses, [vente])
    assert cases.profit == D("400.00")
    assert balance(positions) == owes(M, A, "400.00")
    assert (avant[M].put_in, avant[M].due_to_partner) == (D("2000"), D("2200.00"))
    assert (avant[M].already_back, avant[M].balance) == (D("2600"), D("-400.00"))
    assert (avant[A].put_in, avant[A].due_to_partner) == (D("200"), D("400.00"))
    assert (avant[A].already_back, avant[A].balance) == (ZERO, D("400.00"))
    assert_d61(avant)

    reglement = SettlementInput(M, A, D("400.00"))
    _, cases_apres, apres, positions_apres = bilan(depenses, [vente], settlements=[reglement])
    assert cases_apres == cases  # un règlement ne change ni le bénéfice ni le garage
    assert apres[M].already_back == apres[M].due_to_partner == D("2200.00")
    assert apres[A].already_back == apres[A].due_to_partner == D("400.00")
    assert apres[M].balance == apres[A].balance == ZERO
    assert buckets_problems(positions_apres, cases_apres) == []
    assert_d61(apres)


def test_odd_cent_goes_on_the_still_in_the_garage_line():
    """Un centime impair : l'addition tombe juste, l'écart (≤ 0,01 €) est sur « encore
    dans le garage »."""
    depenses = [expense("100.01", [(M, "100.01")], [line("100.01")])]
    _, cases, associes, positions = bilan(depenses)

    assert cases.still_in_garage == D("100.01")
    assert sum(lg.still_in_garage for lg in associes.values()) == D("100.01")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


# --- Corbeille et contrôles ------------------------------------------------------------


def test_vehicle_in_the_trash_keeps_its_cost_with_the_money_still_in_the_garage():
    depenses = [expense("300", [(A, "300")], [line("300")])]
    lignes, cases, associes, positions = bilan(depenses, vehicles={})

    assert lignes[0].group == GROUP_TRASH
    assert cases.in_trash == D("300.00")
    assert cases.still_in_garage == D("300.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


def test_sold_vehicle_in_the_trash_stays_sold_and_keeps_its_duration():
    """Relecture de la 1.6 : une fiche vendue mise à la corbeille reste parmi les vendus,
    avec sa date d'achat, donc sa durée. Une fiche à la corbeille sans vente y reste."""
    depenses = [
        expense("100", [(M, "100")], [line("100", vehicle=1)]),
        expense("40", [(A, "40")], [line("40", vehicle=2)]),
    ]
    vente = dataclasses.replace(sale("150", [(M, "150")]), date=dt.date(2026, 3, 11))
    vehicules = {
        1: VehicleState("SOLD", purchased_on=dt.date(2026, 3, 1), in_trash=True),
        2: VehicleState("IN_REPAIR", in_trash=True),
    }
    vendu, corbeille = vehicle_report(depenses, [vente], [], vehicules)

    assert (vendu.group, vendu.margin, vendu.days) == (GROUP_SOLD, D("50.00"), 10)
    assert corbeille.group == GROUP_TRASH
    assert money_buckets([vendu, corbeille], depenses, []).in_trash == D("40.00")


def test_report_margins_reads_the_sold_rows_only():
    """D68 : la marge n'a qu'un chemin de calcul, le bilan par véhicule."""
    depenses = [
        expense("100", [(M, "100")], [line("100", vehicle=1)]),
        expense("70", [(M, "70")], [line("70", vehicle=2)]),
        expense("30", [(A, "30")], [line("30", vehicle=3)]),
    ]
    acompte = DepositInput(A, D("20"), DEPOSIT_KEPT, vehicle=1)
    lignes = vehicle_report(
        depenses,
        [sale("150", [(M, "150")]), sale("50", [(M, "50")], vehicle=2)],
        [acompte],
        {1: VENDU, 2: VENDU, 3: abandonne()},
    )

    assert report_margins(lignes) == {1: D("70.00"), 2: D("-20.00")}
    assert report_margins([]) == {}


def test_sale_wins_over_the_status():
    """D22 : une vente non annulée range le véhicule parmi les vendus, quel que soit son
    statut ; un véhicule sans argent n'apparaît pas."""
    depenses = [expense("100", [(M, "100")], [line("100")])]
    lignes = vehicle_report(
        depenses, [sale("150", [(M, "150")])], [], {1: abandonne(), 7: EN_REPARATION}
    )

    assert [(lg.vehicle, lg.group, lg.margin) for lg in lignes] == [(1, GROUP_SOLD, D("50.00"))]


def test_buckets_problems_reports_money_out_of_place():
    depenses = [expense("100", [(M, "100")], [line("100")])]
    _, cases, _, _ = bilan(depenses)
    fausses = partner_positions(PARTNERS, expenses=[expense("90", [(M, "90")], [line("90")])])

    assert buckets_problems(fausses, cases) != []


def test_partner_report_problems_reports_a_gap_above_one_cent():
    ligne = PartnerReport(
        put_in=D("10"),
        profit_share=ZERO,
        already_back=ZERO,
        still_in_garage=D("10"),
        balance=ZERO,
        rounding_gap=D("0.02"),
    )
    (probleme,) = partner_report_problems({M: ligne}, NOMS)
    assert probleme.startswith("Mathis : ce qui lui revient")


# --- Invariant : chaque euro est rangé, sur tous les scénarios -------------------------


@pytest.mark.parametrize("statut", [EN_REPARATION, abandonne(), None], ids=str)
@pytest.mark.parametrize("nom", SCENARIOS)
def test_invariant_7_every_euro_is_in_one_place(nom, statut):
    """Somme des positions = encore dans le garage − bénéfice, et l'addition D61 de chaque
    associé tombe juste, quel que soit le statut des véhicules non vendus (au garage,
    abandonnés ou à la corbeille), avant et après le règlement du solde."""
    depenses, ventes, acomptes, reglements = SCENARIOS[nom]
    cles = {lg.vehicle for d in depenses for lg in d.lines} | {
        x.vehicle for x in (*ventes, *acomptes)
    }
    vehicules = {cle: statut for cle in cles if cle is not None and statut is not None}
    for vente in ventes:
        if not vente.cancelled:
            vehicules[vente.vehicle] = VENDU

    _, cases, associes, positions = bilan(depenses, ventes, acomptes, reglements, vehicules)
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)

    du = balance(positions)
    regle = (*reglements, SettlementInput(du.debtor, du.creditor, du.amount))
    _, cases_apres, apres, positions_apres = bilan(depenses, ventes, acomptes, regle, vehicules)
    assert buckets_problems(positions_apres, cases_apres) == []
    assert_d61(apres)
    for associe in PARTNERS:
        assert apres[associe].balance == ZERO
        assert apres[associe].already_back == associes[associe].already_back + (
            associes[associe].balance
        )


# --- Ce dont l'écran du bilan se sert (sous-étape 4) -----------------------------------


def jeu_sur_deux_ans():
    """2025 : achat du n°1, outillage, achat personnel ; 2026 : ventes des n°1 et n°3,
    abandon du n°2 ; le n°4 est encore au garage."""
    depenses = [
        dated(expense("1000", [(M, "1000")], [line("1000", vehicle=1)]), dt.date(2025, 5, 1)),
        dated(expense("500", [(A, "500")], [line("500", vehicle=2)]), dt.date(2026, 1, 1)),
        dated(expense("300", [(M, "300")], [line("300", vehicle=3)]), dt.date(2026, 1, 1)),
        dated(expense("200", [(A, "200")], [line("200", vehicle=4)]), dt.date(2026, 1, 1)),
        dated(expense("40", [(M, "40")], [line("40", vehicle=None)]), dt.date(2025, 6, 1)),
        dated(
            expense("9", [(A, "9")], [line("9", vehicle=None)], is_personal=True),
            dt.date(2024, 1, 1),
        ),
    ]
    ventes = [
        dataclasses.replace(sale("400", [(M, "400")], vehicle=3), date=dt.date(2026, 6, 1)),
        dataclasses.replace(sale("1200", [(M, "1200")], vehicle=1), date=dt.date(2026, 2, 1)),
    ]
    vehicules = {1: VENDU, 2: abandonne(dt.date(2026, 3, 3)), 3: VENDU, 4: EN_REPARATION}
    return depenses, vehicle_report(depenses, ventes, [], vehicules)


def test_sold_and_abandoned_in_year_filter_and_sort():
    _, lignes = jeu_sur_deux_ans()

    assert [lg.vehicle for lg in sold_in_year(lignes, 2026)] == [1, 3]  # ordre des ventes
    assert sold_in_year(lignes, 2025) == []
    assert [lg.vehicle for lg in abandoned_in_year(lignes, 2026)] == [2]
    assert abandoned_in_year(lignes, 2025) == []


def test_report_years_leave_out_personal_purchases():
    depenses, lignes = jeu_sur_deux_ans()
    assert report_years(lignes, depenses) == [2026, 2025]


def test_vehicle_result_is_margin_or_negative_loss():
    _, lignes = jeu_sur_deux_ans()
    resultats = {lg.vehicle: lg.result for lg in lignes}

    assert resultats == {1: D("200.00"), 2: D("-500.00"), 3: D("100.00"), 4: None}


def test_year_report_without_partners_gives_the_same_profit_and_no_shares():
    depenses, lignes = jeu_sur_deux_ans()
    avec = year_report(2026, lignes, depenses, PARTNERS)
    sans = year_report(2026, lignes, depenses, ())

    assert sans.profit == avec.profit == D("-200.00")  # 200 + 100 − 500
    assert sans.shares == {}
    assert avec.abandons_result == D("-500.00")


def test_year_garage_expenses_match_the_garage_expenses_page():
    """Commande partagée entre le garage et un véhicule, frais de port compris : le bilan
    compte la même part du garage que la page « Dépenses du garage » (D57)."""
    commande = dated(
        expense("100", [(M, "100")], [line("60", vehicle=1), line("30", vehicle=None)]),
        dt.date(2026, 4, 4),
    )
    lignes = vehicle_report([commande], [], [], {1: EN_REPARATION})

    annee = year_report(2026, lignes, [commande], PARTNERS)
    assert annee.garage_expenses == garage_expenses_for_year([commande], 2026, PARTNERS).total
    assert annee.garage_expenses == D("33.33")


def test_settlements_net_and_vehicles_in_garage():
    depenses = [
        expense("100", [(M, "100")], [line("100", vehicle=1)]),
        expense("50", [(A, "50")], [line("50", vehicle=2)]),
    ]
    reglements = [SettlementInput(A, M, D("30")), SettlementInput(M, A, D("5"))]
    _, cases, associes, _ = bilan(depenses, settlements=reglements, vehicles={1: EN_REPARATION})

    assert associes[M].settlements_net == D("25")
    assert associes[A].settlements_net == D("-25")
    assert cases.vehicles_in_garage == D("150.00")  # 100 au garage + 50 à la corbeille


@pytest.mark.parametrize(
    ("montant", "negatif", "zero", "montre"),
    [("12.50", False, False, "12.50"), ("0", False, True, "0"), ("-7.25", True, False, "7.25")],
)
def test_signed_amount(montant, negatif, zero, montre):
    signe = SignedAmount(D(montant))
    assert (signe.is_negative, signe.is_zero, signe.shown) == (negatif, zero, D(montre))


def test_invested_breakdown_adds_up_to_the_invested_tile_every_year():
    """Pour chaque année : coût des véhicules du tableau + dépenses du garage + dépensé pour
    d'autres véhicules − dépensé une autre année pour ceux du tableau = « Investi »."""
    depenses, lignes = jeu_sur_deux_ans()
    # Un véhicule acheté en 2025 et abandonné en 2026 (comme le n°2 de la vraie base).
    depenses.append(
        dated(
            expense("3500.50", [(M, "2000"), (A, "1500.50")], [line("3500.50", 5)]),
            dt.date(2025, 9, 9),
        )
    )
    lignes = vehicle_report(
        depenses,
        [lg.sale for lg in lignes if lg.sale],
        [],
        {
            1: VENDU,
            2: abandonne(dt.date(2026, 3, 3)),
            3: VENDU,
            4: EN_REPARATION,
            5: abandonne(dt.date(2026, 10, 28)),
        },
    )
    for annee in (2024, 2025, 2026):
        detail = invested_breakdown(annee, lignes, depenses)
        du_tableau = sold_in_year(lignes, annee) + abandoned_in_year(lignes, annee)
        assert detail.listed_costs == sum((lg.cost.total for lg in du_tableau), ZERO)
        assert detail.total == year_report(annee, lignes, depenses, PARTNERS).invested

    en_2025 = invested_breakdown(2025, lignes, depenses)
    assert en_2025.listed_costs == ZERO
    assert en_2025.other_vehicles == D("4500.50")  # le n°1 (vendu en 2026) et le n°5
    assert en_2025.garage == D("40.00")
    en_2026 = invested_breakdown(2026, lignes, depenses)
    assert en_2026.other_years == D("4500.50")  # dépensé en 2025 pour les n°1 et n°5
    assert en_2026.other_vehicles == D("200.00")  # le n°4, encore au garage

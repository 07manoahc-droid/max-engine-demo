"""Acompte en plusieurs versements (D89) : reçu par les deux associés ou en plusieurs fois.

Cas 21 à 24 de `.claude/skills/regles-financieres/exemples.md`. Un acompte y est donné par un
`DepositInput` par versement, tous avec la clé de l'acompte : c'est ainsi que
`accounting.queries.load_accounts` le lit. Les formules ne changent pas.
"""

from decimal import Decimal as D

import pytest

from accounting.services import (
    DEPOSIT_ACTIVE,
    DEPOSIT_APPLIED,
    DEPOSIT_KEPT,
    DEPOSIT_REFUNDED,
    GROUP_ABANDONED,
    ZERO,
    DepositInput,
    Payment,
    balance,
    buckets_problems,
    deposit_receipts_problem,
    deposit_receipts_problems,
    deposit_total,
    kept_deposits_total,
    sale_left_to_receive,
    sale_problems,
    sale_recap,
    vehicle_costs,
)

from .test_report import abandonne, assert_d61, bilan
from .test_services import PARTNERS, SETTLED, A, M, expense, line, owes, sale

pytestmark = pytest.mark.finance

ACHAT = [expense("1000", [(M, "1000")], [line("1000")])]


def acompte(etat):
    """L'acompte de 300 € du véhicule 1 : 200 € reçus par Mathis, 100 € par Axel."""
    return [
        DepositInput(M, D("200"), etat, vehicle=1, key=7),
        DepositInput(A, D("100"), etat, vehicle=1, key=7),
    ]


# --- Cas 21 : acompte reçu par les deux, déduit de la vente --------------------------


def test_case_21_deposit_received_by_both_then_sale():
    """Achat 1 000 € payé par Mathis ; acompte 300 € (Mathis 200, Axel 100) ; vente
    2 000 €, dont 1 700 € encaissés par Axel. Marge 1 000 €, 500 € chacun ;
    Axel doit 1 300 € à Mathis."""
    acomptes = acompte(DEPOSIT_APPLIED)
    vente = sale("2000", [(A, "1700")])

    assert sale_left_to_receive(D("2000"), acomptes) == D("1700")
    assert sale_problems(vente, acomptes) == []
    lignes, cases, associes, positions = bilan(ACHAT, [vente], acomptes)
    (vehicule,) = lignes
    assert vehicule.margin == D("1000.00")
    assert positions[M].received == D("200")
    assert positions[A].received == D("1800")
    assert balance(positions) == owes(A, M, "1300.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


def test_case_21_sale_recap_shares_what_each_received():
    """Le tableau de la vente donne à chacun ce qu'il a vraiment reçu, versement par
    versement : le règlement du véhicule seul est le même que celui du garage."""
    recap = sale_recap(
        vehicle_costs(ACHAT)[1], sale("2000", [(A, "1700")]), acompte(DEPOSIT_APPLIED), PARTNERS
    )
    assert recap.margin == D("1000.00")
    assert recap.shares[M].received == D("200")
    assert recap.shares[A].received == D("1800")
    assert recap.shares[M].total_share == D("1500.00")
    assert recap.balance == owes(A, M, "1300.00")


# --- Cas 22 : le même acompte en cours ------------------------------------------------


def test_case_22_active_deposit_received_by_both():
    """Sans vente, l'acompte en cours est déjà dans la poche de chacun : Axel doit
    450 € à Mathis ; les 300 € sont « en attente » dans le bilan."""
    _, cases, associes, positions = bilan(ACHAT, deposits=acompte(DEPOSIT_ACTIVE))
    assert cases.pending_deposits == D("300")
    assert cases.still_in_garage == D("700.00")
    assert cases.profit == ZERO
    assert balance(positions) == owes(A, M, "450.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


# --- Cas 23 : le même acompte conservé, véhicule abandonné -----------------------------


def test_case_23_kept_deposit_on_an_abandoned_vehicle():
    """Perte = 1 000 − 300 = 700 € (350 € chacun) ; Axel doit 450 € à Mathis."""
    acomptes = acompte(DEPOSIT_KEPT)
    assert kept_deposits_total(acomptes) == D("300")
    lignes, cases, associes, positions = bilan(ACHAT, deposits=acomptes, vehicles={1: abandonne()})
    (vehicule,) = lignes
    assert vehicule.group == GROUP_ABANDONED
    assert vehicule.loss == D("700.00")
    assert cases.profit == D("-700.00")
    assert cases.pending_deposits == ZERO
    assert balance(positions) == owes(A, M, "450.00")
    assert buckets_problems(positions, cases) == []
    assert_d61(associes)


# --- Cas 24 : le même acompte rendu ---------------------------------------------------


def test_case_24_refunded_deposit_has_no_effect():
    """Rendu à l'acheteur : comme s'il n'y avait pas eu d'acompte. Axel doit 500 €."""
    _, cases, associes, positions = bilan(ACHAT, deposits=acompte(DEPOSIT_REFUNDED))
    _, cases_sans, _, positions_sans = bilan(ACHAT)
    assert positions == positions_sans
    assert cases == cases_sans
    assert balance(positions) == owes(A, M, "500.00")
    assert_d61(associes)


def test_one_receipt_gives_the_same_figures_as_the_old_deposit():
    """Non-régression : un acompte d'un seul versement compte comme l'acompte d'avant
    (ici, le cas 12 bis : 200 € reçus par Mathis, en cours)."""
    un = [DepositInput(M, D("200"), DEPOSIT_ACTIVE, vehicle=1, key=7)]
    assert balance(bilan(deposits=un)[3]) == owes(M, A, "100.00")
    rendu = [DepositInput(M, D("200"), DEPOSIT_REFUNDED, vehicle=1, key=7)]
    assert balance(bilan(deposits=rendu)[3]) == SETTLED


# --- Montant d'un acompte et saisie des versements -----------------------------------


def test_deposit_total_adds_the_receipts():
    assert deposit_total([D("200.00"), D("100.50")]) == D("300.50")
    assert deposit_total([]) == ZERO
    assert isinstance(deposit_total([D("1")]), D)


@pytest.mark.parametrize(
    ("total", "versements", "attendu"),
    [
        ("300", [(M, "300")], None),
        ("300", [(M, "200"), (A, "100")], None),
        ("0", [(M, "0")], "L'acompte doit être plus grand que 0 €."),
        ("300", [], "Indique qui a reçu l'argent."),
        ("300", [(M, "200"), (M, "100")], "qu'une fois"),
        ("300", [(M, "300"), (A, "0")], "Chaque part reçue doit être plus grande que 0 €."),
        ("300", [(M, "200"), (A, "50")], "au lieu de"),
    ],
)
def test_deposit_receipts_problem(total, versements, attendu):
    paiements = [Payment(associe, D(montant)) for associe, montant in versements]
    probleme = deposit_receipts_problem(D(total), paiements)
    if attendu is None:
        assert probleme is None
    else:
        assert attendu in probleme


def test_deposit_receipts_problems():
    assert deposit_receipts_problems(2, False) == []
    assert deposit_receipts_problems(0, False) == [
        "aucun versement : l'acompte ne compte nulle part"
    ]
    assert deposit_receipts_problems(1, True) == [
        "ancien montant encore rempli (non recopié en versement)"
    ]

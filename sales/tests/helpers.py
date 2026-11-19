"""Aides des tests pour les acomptes (D89), partagées par les tests des autres applications.

Depuis la 2.0.0, l'argent d'un acompte est dans ses versements (`DepositReceipt`). Ces aides
gardent la forme simple « un acompte de 200 € reçu par Mathis » des tests d'avant.
"""

import datetime as dt
from decimal import Decimal

from accounting.services import Payment
from sales import services
from sales.models import Deposit, DepositReceipt

LE_30 = dt.date(2026, 10, 30)


def create_deposit(
    *,
    vehicle,
    amount,
    received_by,
    status=Deposit.Status.ACTIVE,
    sale=None,
    date=LE_30,
    buyer=None,
) -> Deposit:
    """Un acompte d'un seul versement, écrit directement en base (sans les règles des
    services : pour fabriquer un cas incohérent ou un état d'arrivée)."""
    acompte = Deposit.objects.create(
        vehicle=vehicle, status=status, sale=sale, date=date, buyer=buyer
    )
    DepositReceipt.objects.create(
        deposit=acompte, partner=received_by, amount=Decimal(amount), date=date
    )
    return acompte


def record_deposit_for(vehicle, *, amount, received_by, date=LE_30, buyer=None, partner=None):
    """`record_deposit` avec un seul versement, reçu par `received_by`."""
    return services.record_deposit(
        vehicle,
        date=date,
        receipts=[Payment(received_by, Decimal(amount))],
        buyer=buyer,
        partner=partner,
    )


def deposit_amount(deposit: Deposit) -> Decimal:
    """Le montant de l'acompte, lu dans ses versements hors corbeille."""
    return services.deposit_detail(deposit).total

"""Tests des contraintes des ventes et des acomptes."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from sales.models import Deposit, Sale
from sales.tests.helpers import create_deposit


def test_only_one_active_sale_per_vehicle(make_vehicle):
    """Une deuxième vente non annulée est refusée (D21)."""
    vehicule = make_vehicle()
    Sale.objects.create(vehicle=vehicule, price=Decimal("2600.00"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Sale.objects.create(vehicle=vehicule, price=Decimal("2500.00"))


def test_vehicle_can_be_sold_again_after_cancellation(make_vehicle):
    """Vente annulée puis revente : les deux ventes restent visibles."""
    vehicule = make_vehicle()
    Sale.objects.create(vehicle=vehicule, price=Decimal("2600.00"), cancelled_at=timezone.now())
    Sale.objects.create(vehicle=vehicule, price=Decimal("2500.00"))
    assert vehicule.sales.count() == 2


def test_trade_in_value_cannot_exceed_price(make_vehicle):
    with pytest.raises(IntegrityError), transaction.atomic():
        Sale.objects.create(
            vehicle=make_vehicle(), price=Decimal("500.00"), trade_in_value=Decimal("700.00")
        )


def test_trade_in_vehicle_is_another_vehicle(make_vehicle):
    vehicule = make_vehicle()
    with pytest.raises(IntegrityError), transaction.atomic():
        Sale.objects.create(
            vehicle=vehicule,
            price=Decimal("2000.00"),
            trade_in_vehicle=vehicule,
            trade_in_value=Decimal("700.00"),
        )


def test_applied_deposit_needs_a_sale(make_vehicle, mathis):
    with pytest.raises(IntegrityError), transaction.atomic():
        create_deposit(
            vehicle=make_vehicle(),
            amount=Decimal("200.00"),
            received_by=mathis,
            status=Deposit.Status.APPLIED,
        )


def test_deposit_must_be_positive(make_vehicle, mathis):
    with pytest.raises(IntegrityError), transaction.atomic():
        create_deposit(vehicle=make_vehicle(), amount=Decimal("0"), received_by=mathis)

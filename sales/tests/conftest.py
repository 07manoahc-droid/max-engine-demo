"""Fixtures des tests des ventes."""

import datetime as dt
from decimal import Decimal as D

import pytest

from accounting.services import Payment
from vehicles import services as vehicle_services


@pytest.fixture
def purchased(make_vehicle, mathis):
    """Véhicule acheté 1 500 € par Mathis, au statut « Acheté »."""
    vehicule = make_vehicle(brand="GG", model="MC250", year=2010)
    vehicle_services.record_purchase(
        vehicule,
        price=D("1500"),
        date=dt.date(2026, 10, 1),
        payments=[Payment(mathis, D("1500"))],
        seller=None,
        partner=mathis,
    )
    vehicule.refresh_from_db()
    return vehicule

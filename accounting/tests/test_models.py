"""Tests du modèle Settlement : jamais modifié ni supprimé, annulé une seule fois."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounting.models import Settlement


@pytest.fixture
def settlement(axel, mathis):
    """Axel verse 205,44 € à Mathis."""
    return Settlement.objects.create(
        from_partner=axel, to_partner=mathis, amount=Decimal("205.44")
    )


def cancel(settlement, **champs):
    """Crée le règlement inverse de `settlement`."""
    champs = {
        "from_partner": settlement.to_partner,
        "to_partner": settlement.from_partner,
        "amount": settlement.amount,
        "cancels": settlement,
        **champs,
    }
    return Settlement.objects.create(**champs)


def test_settlement_cannot_be_modified(settlement):
    settlement.amount = Decimal("100.00")
    with pytest.raises(ValidationError):
        settlement.save()
    settlement.refresh_from_db()
    assert settlement.amount == Decimal("205.44")


def test_settlement_cannot_be_deleted(settlement):
    with pytest.raises(ValidationError):
        settlement.delete()
    assert Settlement.objects.filter(pk=settlement.pk).exists()


def test_settlement_cannot_be_bulk_modified_or_deleted(settlement):
    """`update()` et `delete()` groupés sont refusés eux aussi."""
    with pytest.raises(ValidationError):
        Settlement.objects.filter(pk=settlement.pk).update(amount=Decimal("1.00"))
    with pytest.raises(ValidationError):
        Settlement.objects.all().delete()
    settlement.refresh_from_db()
    assert settlement.amount == Decimal("205.44")


def test_settlement_is_cancelled_by_a_reverse_settlement(settlement):
    annulation = cancel(settlement)
    assert settlement.cancelled_by == annulation


def test_cancellation_cannot_itself_be_cancelled(settlement):
    """Une annulation ne s'annule pas : on saisit un nouveau règlement."""
    annulation = cancel(settlement)
    with pytest.raises(ValidationError):
        cancel(annulation)


def test_settlement_cannot_be_cancelled_twice(settlement):
    cancel(settlement)
    with pytest.raises(IntegrityError), transaction.atomic():
        cancel(settlement)


@pytest.mark.parametrize("ecart", ["sens", "montant"])
def test_cancellation_must_mirror_the_settlement(settlement, ecart):
    """L'annulation reprend le même montant, dans l'autre sens."""
    if ecart == "sens":
        champs = {"from_partner": settlement.from_partner, "to_partner": settlement.to_partner}
    else:
        champs = {"amount": Decimal("200.00")}
    with pytest.raises(ValidationError):
        cancel(settlement, **champs)


def test_settlement_between_two_different_partners(mathis):
    with pytest.raises(IntegrityError), transaction.atomic():
        Settlement.objects.create(from_partner=mathis, to_partner=mathis, amount=Decimal("1"))

"""Tests des contraintes des dépenses."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from expenses.models import Expense, ExpenseLine, ExpensePayment, PartTransfer, Refund


@pytest.fixture
def expense(db):
    return Expense.objects.create(total_paid=Decimal("86.00"))


def test_expense_total_must_be_positive(db):
    with pytest.raises(IntegrityError), transaction.atomic():
        Expense.objects.create(total_paid=Decimal("0.00"))


def test_one_payment_per_partner_and_expense(expense, mathis):
    ExpensePayment.objects.create(expense=expense, partner=mathis, amount=Decimal("10.00"))
    with pytest.raises(IntegrityError), transaction.atomic():
        ExpensePayment.objects.create(expense=expense, partner=mathis, amount=Decimal("5.00"))


def test_payment_must_be_positive(expense, mathis):
    with pytest.raises(IntegrityError), transaction.atomic():
        ExpensePayment.objects.create(expense=expense, partner=mathis, amount=Decimal("0.00"))


def test_line_without_expense_must_be_to_order(parts_category):
    """Seule une pièce « À commander » existe sans paiement."""
    ExpenseLine.objects.create(
        category=parts_category,
        label="Disque",
        unit_price=Decimal("71.90"),
        status=ExpenseLine.Status.TO_ORDER,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ExpenseLine.objects.create(
            category=parts_category,
            label="Filtre",
            unit_price=Decimal("8.40"),
            status=ExpenseLine.Status.RECEIVED,
        )


@pytest.mark.parametrize(
    ("quantite", "prix"),
    [(Decimal("0"), Decimal("1.00")), (Decimal("1"), Decimal("-0.01"))],
)
def test_line_quantity_positive_and_price_not_negative(expense, parts_category, quantite, prix):
    with pytest.raises(IntegrityError), transaction.atomic():
        ExpenseLine.objects.create(
            expense=expense,
            category=parts_category,
            label="Pièce",
            quantity=quantite,
            unit_price=prix,
        )


def test_refund_can_be_zero_but_not_negative(expense, parts_category, axel):
    """Un remboursement « à préciser » de 0,00 € est permis (import Excel)."""
    ligne = ExpenseLine.objects.create(
        expense=expense, category=parts_category, label="Joint", unit_price=Decimal("3.34")
    )
    Refund.objects.create(line=ligne, amount=Decimal("0.00"), refunded_to=axel)
    with pytest.raises(IntegrityError), transaction.atomic():
        Refund.objects.create(line=ligne, amount=Decimal("-1.00"), refunded_to=axel)


def test_part_transfer_needs_two_different_vehicles(expense, parts_category, make_vehicle):
    vehicule = make_vehicle()
    ligne = ExpenseLine.objects.create(
        expense=expense, vehicle=vehicule, category=parts_category, label="Roue", unit_price=1
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PartTransfer.objects.create(line=ligne, from_vehicle=vehicule, to_vehicle=vehicule)


def test_expense_has_history(expense):
    expense.total_paid = Decimal("80.00")
    expense.save()
    assert expense.history.count() == 2


def test_binned_payment_frees_the_partner_place(expense, mathis):
    """D40 : un paiement à la corbeille ne bloque pas un nouveau paiement du même associé."""
    ancien = ExpensePayment.objects.create(expense=expense, partner=mathis, amount=Decimal("10"))
    ancien.soft_delete()
    ExpensePayment.objects.create(expense=expense, partner=mathis, amount=Decimal("86.00"))
    assert list(expense.payments.values_list("amount", flat=True)) == [Decimal("86.00")]

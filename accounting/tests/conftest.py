"""Fixtures des tests des comptes."""

from decimal import Decimal as D

import pytest
from django.utils import timezone

from core.models import ExpenseCategory
from expenses.models import Expense, ExpenseLine, ExpensePayment


@pytest.fixture
def axel_owes(mathis, axel):
    """Outillage du garage de 410,88 € payé par Mathis : Axel lui doit 205,44 €."""
    depense = Expense.objects.create(total_paid=D("410.88"), date=timezone.localdate())
    ExpensePayment.objects.create(expense=depense, partner=mathis, amount=D("410.88"))
    ExpenseLine.objects.create(
        expense=depense,
        category=ExpenseCategory.objects.get(code="tooling"),
        label="Caisse à outils",
        unit_price=D("410.88"),
    )
    return depense

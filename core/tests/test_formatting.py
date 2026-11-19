"""Tests de la mise en forme des montants et des nombres."""

from decimal import Decimal

import pytest
from django.template import Context, Template

from core.formatting import NBSP, format_euros, format_integer


@pytest.mark.parametrize(
    ("montant", "attendu"),
    [
        ("1404.1", f"1{NBSP}404,10{NBSP}€"),
        ("0", f"0,00{NBSP}€"),
        ("205.44", f"205,44{NBSP}€"),
        ("-300", f"-300,00{NBSP}€"),
        ("1234567.891", f"1{NBSP}234{NBSP}567,89{NBSP}€"),
        ("0.005", f"0,01{NBSP}€"),
    ],
)
def test_format_euros(montant, attendu):
    assert format_euros(Decimal(montant)) == attendu


@pytest.mark.parametrize(("valeur", "attendu"), [(Decimal("205.44"), f"205,44{NBSP}€"), (None, "")])
def test_euros_template_filter(valeur, attendu):
    gabarit = Template("{% load ui_tags %}{{ montant|euros }}")
    assert gabarit.render(Context({"montant": valeur})) == attendu


@pytest.mark.parametrize(
    ("nombre", "attendu"),
    [
        (0, "0"),
        (999, "999"),
        (12500, f"12{NBSP}500"),
        (1234567, f"1{NBSP}234{NBSP}567"),
        (-4000, f"-4{NBSP}000"),
    ],
)
def test_format_integer(nombre, attendu):
    assert format_integer(nombre) == attendu


@pytest.mark.parametrize(("valeur", "attendu"), [(12500, f"12{NBSP}500"), (None, "")])
def test_nombre_template_filter(valeur, attendu):
    gabarit = Template("{% load ui_tags %}{{ valeur|nombre }}")
    assert gabarit.render(Context({"valeur": valeur})) == attendu

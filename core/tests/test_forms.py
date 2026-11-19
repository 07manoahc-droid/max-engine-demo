"""Tests des champs communs des formulaires : montants à la française, messages tutoyés."""

from decimal import Decimal as D

import pytest
from django import forms

from core.forms import HttpsURLField, MoneyField, StyledFormMixin


class ExampleForm(StyledFormMixin, forms.Form):
    montant = MoneyField()
    annee = forms.IntegerField(min_value=1900, required=False)
    lien = HttpsURLField(required=False)
    choix = forms.ChoiceField(choices=[("a", "A")], required=False)
    date = forms.DateField(required=False)


def errors_for(**donnees):
    form = ExampleForm({"montant": "10", **donnees})
    form.is_valid()
    return {cle: list(messages) for cle, messages in form.errors.items()}


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("1500", D("1500")),
        ("1500,50", D("1500.50")),
        ("1500.50", D("1500.50")),
        ("1 500,50", D("1500.50")),
        ("1\u00a0500,50", D("1500.50")),
        ("1\u202f500,50 €", D("1500.50")),
        ("0", D("0")),
    ],
)
def test_money_field_reads_french_amounts(saisie, attendu):
    form = ExampleForm({"montant": saisie})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["montant"] == attendu


@pytest.mark.parametrize(
    ("saisie", "message"),
    [
        ("beaucoup", "Indique un montant, par exemple 1 500,50."),
        ("-5", "Le montant ne peut pas être négatif."),
        ("12,345", "Indique au plus 2 chiffres après la virgule."),
        ("123456789012", "Ce montant est trop grand."),
    ],
)
def test_money_field_errors_are_friendly(saisie, message):
    assert errors_for(montant=saisie) == {"montant": [message]}


def test_other_fields_errors_are_friendly():
    assert errors_for(annee="deux mille", lien="pas un lien", choix="z", date="hier") == {
        "annee": ["Indique un nombre entier, sans espace ni virgule."],
        "lien": ["Indique une adresse internet complète, par exemple https://www.leboncoin.fr/…"],
        "choix": ["Choisis une valeur de la liste."],
        "date": ["Indique une date valide."],
    }
    assert errors_for(annee="1800") == {"annee": ["Indique un nombre d'au moins 1900."]}


def test_link_without_scheme_gets_https():
    form = ExampleForm({"montant": "1", "lien": "www.leboncoin.fr/ad/1"})
    assert form.is_valid()
    assert form.cleaned_data["lien"] == "https://www.leboncoin.fr/ad/1"


def test_friendly_errors_do_not_leak_between_forms():
    """Les messages sont posés sur les champs de chaque formulaire, pas sur la classe."""
    ExampleForm()
    assert forms.IntegerField().error_messages["invalid"] != (
        "Indique un nombre entier, sans espace ni virgule."
    )

"""Tests du modèle Partner et de sa migration de données."""

import importlib

import pytest
from django.apps import apps as django_apps

from core.models import Partner

migration = importlib.import_module("core.migrations.0002_associes_initiaux")


@pytest.mark.django_db
def test_initial_partners_exist_with_their_colors():
    """La migration crée Mathis (indigo) et Axel (turquoise), actifs, dans cet ordre."""
    partners = list(Partner.objects.values_list("name", "color", "is_active"))
    assert partners == [("Mathis", "indigo", True), ("Axel", "teal", True)]


@pytest.mark.django_db
def test_partner_str_is_the_name():
    assert str(Partner.objects.get(name="Axel")) == "Axel"


@pytest.mark.django_db
def test_data_migration_is_reversible_and_idempotent():
    """L'inverse retire les deux associés ; rejouer l'avant ne crée pas de doublon."""
    migration.remove_partners(django_apps, None)
    assert Partner.objects.count() == 0

    migration.create_partners(django_apps, None)
    migration.create_partners(django_apps, None)
    assert Partner.objects.count() == 2


@pytest.mark.django_db
def test_data_migration_keeps_an_existing_partner_untouched():
    """Un associé déjà présent (même renommé en couleur) n'est jamais écrasé."""
    Partner.objects.filter(name="Mathis").update(color="teal")
    migration.create_partners(django_apps, None)
    assert Partner.objects.get(name="Mathis").color == "teal"

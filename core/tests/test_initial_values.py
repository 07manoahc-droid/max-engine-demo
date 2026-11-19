"""Tests de la migration des valeurs initiales (listes, catégories, paramètres)."""

import importlib

import pytest
from django.apps import apps as django_apps

from core.models import ExpenseCategory, GarageSettings, PurchaseOrigin, VehicleType

migration = importlib.import_module("core.migrations.0006_valeurs_initiales")


@pytest.mark.django_db
def test_initial_lists_exist_in_order():
    assert list(VehicleType.objects.values_list("name", flat=True)) == ["Moto", "Scooter", "Quad"]
    assert list(PurchaseOrigin.objects.values_list("name", flat=True)) == [
        "Leboncoin",
        "Connaissance",
        "Autre",
    ]


@pytest.mark.django_db
def test_initial_expense_categories():
    """Six catégories ; seule « Achat véhicule » est une catégorie système."""
    categories = list(ExpenseCategory.objects.values_list("code", "name", "is_system"))
    assert categories == [
        ("vehicle_purchase", "Achat véhicule", True),
        ("parts", "Pièces", False),
        ("subcontracting", "Sous-traitance", False),
        ("tooling", "Outillage", False),
        ("consumable", "Consommable", False),
        ("other", "Autre", False),
    ]


@pytest.mark.django_db
def test_garage_settings_row_exists_with_defaults():
    parametres = GarageSettings.objects.get()
    assert (parametres.pk, parametres.garage_name, parametres.stale_sale_days) == (
        1,
        "MAx Engine",
        60,
    )


@pytest.mark.django_db
def test_forward_is_idempotent():
    migration.create_initial_values(django_apps, None)
    migration.create_initial_values(django_apps, None)
    assert VehicleType.objects.count() == 3
    assert PurchaseOrigin.objects.count() == 3
    assert ExpenseCategory.objects.count() == 6
    assert GarageSettings.objects.count() == 1


@pytest.mark.django_db
def test_replaying_keeps_the_partners_settings():
    """Défaire puis rejouer la migration ne change aucun réglage des associés :
    l'inverse ne fait rien, l'aller ne crée que ce qui manque."""
    VehicleType.objects.filter(name="Moto").update(name="Motos")
    VehicleType.objects.filter(name="Quad").update(is_active=False, order=9)
    PurchaseOrigin.objects.filter(name="Leboncoin").update(name="leboncoin.fr")
    ExpenseCategory.objects.filter(code="parts").update(name="Pièces détachées", order=7)
    ExpenseCategory.objects.filter(code="other").update(is_active=False)
    GarageSettings.objects.filter(pk=1).update(
        garage_name="Max Engine Garage", backup_dir="C:/OneDrive/Sauvegardes", stale_sale_days=45
    )
    avant = (
        list(VehicleType.objects.values_list("name", "order", "is_active")),
        list(PurchaseOrigin.objects.values_list("name", "order", "is_active")),
        list(ExpenseCategory.objects.values_list("code", "name", "order", "is_active")),
        list(GarageSettings.objects.values_list("garage_name", "backup_dir", "stale_sale_days")),
    )

    inverse = migration.Migration.operations[0].reverse_code
    inverse(django_apps, None)
    migration.create_initial_values(django_apps, None)

    apres = (
        list(VehicleType.objects.values_list("name", "order", "is_active")),
        list(PurchaseOrigin.objects.values_list("name", "order", "is_active")),
        list(ExpenseCategory.objects.values_list("code", "name", "order", "is_active")),
        list(GarageSettings.objects.values_list("garage_name", "backup_dir", "stale_sale_days")),
    )
    assert apres == avant


@pytest.mark.django_db
def test_missing_category_is_added_back():
    """Une catégorie absente (repérée par son code) est recréée, sans toucher aux autres."""
    ExpenseCategory.objects.filter(code="tooling").delete()
    migration.create_initial_values(django_apps, None)
    assert ExpenseCategory.objects.filter(code="tooling", name="Outillage").exists()
    assert ExpenseCategory.objects.count() == 6

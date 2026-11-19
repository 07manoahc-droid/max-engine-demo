"""Données initiales : types de véhicule, origines d'achat, catégories de dépense, paramètres.

Les associés sont créés par 0002. Rien n'est jamais écrasé ni dupliqué :
- les types et les origines ne sont créés que si leur liste est vide (une liste renommée
  par les associés, « Motos » au lieu de « Moto », ne reçoit pas de doublon) ;
- les catégories sont repérées par leur code stable, les paramètres par la ligne 1.

Le retour arrière ne fait rien, volontairement : supprimer ces valeurs effacerait les
réglages des associés (noms, ordre, catégories désactivées, dossier des sauvegardes), que
l'aller ne saurait pas rendre. Les tables elles-mêmes disparaissent avec 0003 et 0004.
"""

from django.db import migrations

VEHICLE_TYPES = ["Moto", "Scooter", "Quad"]
PURCHASE_ORIGINS = ["Leboncoin", "Connaissance", "Autre"]
# (code, nom, système)
EXPENSE_CATEGORIES = [
    ("vehicle_purchase", "Achat véhicule", True),
    ("parts", "Pièces", False),
    ("subcontracting", "Sous-traitance", False),
    ("tooling", "Outillage", False),
    ("consumable", "Consommable", False),
    ("other", "Autre", False),
]


def create_initial_values(apps, schema_editor):
    """Crée les valeurs manquantes, dans l'ordre d'affichage prévu."""
    VehicleType = apps.get_model("core", "VehicleType")
    PurchaseOrigin = apps.get_model("core", "PurchaseOrigin")
    ExpenseCategory = apps.get_model("core", "ExpenseCategory")
    GarageSettings = apps.get_model("core", "GarageSettings")

    if not VehicleType.objects.exists():
        for ordre, nom in enumerate(VEHICLE_TYPES, start=1):
            VehicleType.objects.create(name=nom, order=ordre)
    if not PurchaseOrigin.objects.exists():
        for ordre, nom in enumerate(PURCHASE_ORIGINS, start=1):
            PurchaseOrigin.objects.create(name=nom, order=ordre)
    for ordre, (code, nom, systeme) in enumerate(EXPENSE_CATEGORIES, start=1):
        ExpenseCategory.objects.get_or_create(
            code=code, defaults={"name": nom, "is_system": systeme, "order": ordre}
        )
    GarageSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_fichiers_joints_rattachements"),
    ]

    operations = [
        # Retour arrière sans effet : voir l'explication en tête du fichier.
        migrations.RunPython(create_initial_values, migrations.RunPython.noop),
    ]

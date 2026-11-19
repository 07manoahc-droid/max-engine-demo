"""Données initiales : les deux associés, Mathis (indigo) et Axel (turquoise)."""

from django.db import migrations

INITIAL_PARTNERS = [
    {"name": "Mathis", "color": "indigo"},
    {"name": "Axel", "color": "teal"},
]


def create_partners(apps, schema_editor):
    """Crée les deux associés s'ils n'existent pas déjà (sans rien écraser)."""
    Partner = apps.get_model("core", "Partner")
    for data in INITIAL_PARTNERS:
        Partner.objects.get_or_create(name=data["name"], defaults={"color": data["color"]})


def remove_partners(apps, schema_editor):
    """Inverse : retire les deux associés initiaux (l'inverse n'a lieu qu'avant toute donnée liée)."""
    Partner = apps.get_model("core", "Partner")
    Partner.objects.filter(name__in=[data["name"] for data in INITIAL_PARTNERS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_creation_associes"),
    ]

    operations = [
        migrations.RunPython(create_partners, remove_partners),
    ]

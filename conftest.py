"""Fixtures communes aux tests qui utilisent la base."""

import pytest

from core.models import ExpenseCategory, Partner, VehicleType
from vehicles.models import Vehicle


@pytest.fixture(autouse=True)
def media_in_temporary_folder(settings, tmp_path):
    """Les fichiers envoyés pendant les tests ne vont jamais dans le vrai `media/`."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def backups_in_temporary_folder(settings, tmp_path):
    """Les sauvegardes faites pendant les tests ne vont jamais dans le vrai dossier, et
    l'état de la dernière sauvegarde n'est jamais celui de la vraie base."""
    settings.BACKUP_DIR = ""
    settings.BACKUP_FALLBACK_DIR = tmp_path / "sauvegardes"
    settings.BACKUP_STATE_FILE = tmp_path / "derniere-sauvegarde.json"


@pytest.fixture
def mathis(db):
    return Partner.objects.get(name="Mathis")


@pytest.fixture
def axel(db):
    return Partner.objects.get(name="Axel")


@pytest.fixture
def as_mathis(client, mathis):
    """Client qui a déjà choisi Mathis au lancement."""
    client.post("/qui-es-tu/", {"partner": mathis.pk})
    return client


@pytest.fixture
def parts_category(db):
    return ExpenseCategory.objects.get_or_create(code="parts", defaults={"name": "Pièces"})[0]


@pytest.fixture
def make_vehicle(db):
    """Crée un véhicule (type Moto) ; les champs passés remplacent les valeurs par défaut."""
    moto = VehicleType.objects.get_or_create(name="Moto")[0]

    def fabriquer(**champs):
        champs = {"vehicle_type": moto, "brand": "Yamaha", "model": "YZ250", **champs}
        return Vehicle.objects.create(**champs)

    return fabriquer

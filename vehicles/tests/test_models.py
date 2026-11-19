"""Tests du modèle Vehicle : numéro, nom affiché, réouverture, historique."""

import pytest
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone
from simple_history.models import HistoricalRecords

from core.models import Partner, VehicleType
from vehicles.models import Vehicle


@pytest.fixture
def moto(db):
    return VehicleType.objects.get_or_create(name="Moto")[0]


def make_vehicle(vehicle_type, **champs):
    champs = {"brand": "Yamaha", "model": "YZ250", **champs}
    return Vehicle.objects.create(vehicle_type=vehicle_type, **champs)


def test_numbers_are_given_in_order_and_never_reused(moto):
    """Le numéro suit le plus grand existant, même si ce véhicule est à la corbeille."""
    premier = make_vehicle(moto)
    deuxieme = make_vehicle(moto)
    deuxieme.soft_delete()
    troisieme = make_vehicle(moto)

    assert (premier.number, deuxieme.number, troisieme.number) == (1, 2, 3)


def test_number_does_not_change_on_save(moto):
    vehicule = make_vehicle(moto)
    vehicule.brand = "Kawasaki"
    vehicule.save()
    vehicule.refresh_from_db()
    assert vehicule.number == 1


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"year": 2004, "vin": "js1rj18b"}, "n°1 · Yamaha YZ250 2004 · JS1RJ18B"),
        ({}, "n°1 · Yamaha YZ250"),
        ({"vin": "ABC"}, "n°1 · Yamaha YZ250 · ABC"),
    ],
)
def test_display_name_omits_empty_parts(moto, champs, attendu):
    """`n°… · marque modèle année · VIN`, VIN en majuscules, parties vides omises (D14)."""
    assert make_vehicle(moto, **champs).display_name == attendu


@pytest.mark.parametrize("motif", ["", "   "])
def test_reopening_requires_a_reason(moto, motif):
    """Un motif vide ou fait seulement d'espaces est refusé."""
    vehicule = make_vehicle(moto, status=Vehicle.Status.SOLD)
    vehicule.reopened_at = timezone.now()
    vehicule.reopen_reason = motif
    with pytest.raises(IntegrityError), transaction.atomic():
        vehicule.save()


def test_expenses_are_locked_only_when_sold_and_not_reopened(moto):
    vehicule = make_vehicle(moto, status=Vehicle.Status.SOLD)
    assert vehicule.expenses_locked

    vehicule.reopened_at = timezone.now()
    vehicule.reopen_reason = "Facture oubliée"
    vehicule.save()
    assert not vehicule.expenses_locked
    assert vehicule.status == Vehicle.Status.SOLD

    vehicule.status = Vehicle.Status.IN_REPAIR
    vehicule.reopened_at = None
    assert not vehicule.expenses_locked


def test_history_author_is_the_current_partner(moto):
    """L'historique note l'associé de la requête en cours comme auteur (D30)."""
    axel = Partner.objects.get(name="Axel")
    requete = RequestFactory().get("/")
    requete.partner = axel
    HistoricalRecords.context.request = requete
    try:
        vehicule = make_vehicle(moto)
    finally:
        del HistoricalRecords.context.request

    assert vehicule.history.get().history_user == axel


def test_history_author_is_empty_without_request(moto):
    vehicule = make_vehicle(moto)
    assert vehicule.history.get().history_user is None

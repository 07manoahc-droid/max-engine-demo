"""La commande `donnees_demo` : un garage inventé, cohérent, créé seulement sur une base vide."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounting import queries
from accounting.models import Settlement
from core.models import Attachment
from expenses.models import ExpenseLine, Refund
from sales.models import Deposit, Sale
from vehicles.models import Vehicle


def lancer():
    sortie = StringIO()
    call_command("donnees_demo", stdout=sortie)
    return sortie.getvalue()


@pytest.mark.django_db
def test_demo_covers_every_status_and_its_accounts_are_consistent():
    assert "Démo créée : 7 véhicules." in lancer()

    statuts = set(Vehicle.objects.values_list("status", flat=True))
    attendus = set(Vehicle.Status.values) - {Vehicle.Status.READY_FOR_SALE}
    assert attendus <= statuts
    assert Sale.objects.filter(trade_in_vehicle__isnull=False).count() == 1
    assert Deposit.objects.filter(status=Deposit.Status.ACTIVE).count() == 1
    assert Refund.objects.count() == 1
    assert Settlement.objects.count() == 1
    assert ExpenseLine.objects.filter(status=ExpenseLine.Status.TO_ORDER).count() == 2
    assert ExpenseLine.objects.filter(vehicle__isnull=True).count() == 2  # dépenses du garage
    assert Attachment.objects.filter(kind=Attachment.Kind.PHOTO).count() == 8

    assert queries.consistency_problems(queries.load_accounts()) == []


@pytest.mark.django_db
def test_demo_refuses_a_database_that_is_not_empty():
    lancer()
    with pytest.raises(CommandError, match="base vide"):
        lancer()


@pytest.mark.django_db
def test_demo_refuses_read_only_mode(settings):
    settings.READ_ONLY = True
    with pytest.raises(CommandError, match="lecture seule"):
        lancer()
    assert not Vehicle.all_objects.exists()

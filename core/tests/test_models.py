"""Tests des briques communes : suppression logique, paramètres, listes, fournisseurs."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import (
    Attachment,
    Contact,
    ExpenseCategory,
    GarageSettings,
    Supplier,
    attachment_upload_to,
)


@pytest.mark.django_db
def test_soft_delete_hides_then_restore_shows_again():
    """Un contact supprimé part à la corbeille : caché par `objects`, visible par `all_objects`."""
    contact = Contact.objects.create(name="Jean")
    contact.soft_delete()

    assert contact.is_deleted
    assert not Contact.objects.filter(pk=contact.pk).exists()
    assert Contact.all_objects.filter(pk=contact.pk).exists()

    contact.restore()
    assert Contact.objects.filter(pk=contact.pk).exists()


@pytest.mark.django_db
def test_supplier_name_is_unique_ignoring_case():
    """« LEBONCOIN » et « Leboncoin » sont le même fournisseur."""
    Supplier.objects.create(name="LEBONCOIN")
    with pytest.raises(IntegrityError), transaction.atomic():
        Supplier.objects.create(name="Leboncoin")


@pytest.mark.django_db
def test_garage_settings_has_a_single_row():
    """Les paramètres sont la ligne 1 ; la base refuse une deuxième ligne."""
    assert GarageSettings.load().pk == 1
    with pytest.raises(IntegrityError), transaction.atomic():
        GarageSettings.objects.create(garage_name="Autre nom")
    assert GarageSettings.objects.count() == 1


@pytest.mark.django_db
def test_system_category_cannot_be_deactivated():
    categorie = ExpenseCategory.objects.create(name="Test système", code="test_sys", is_system=True)
    categorie.is_active = False
    with pytest.raises(IntegrityError), transaction.atomic():
        categorie.save()


@pytest.mark.django_db
def test_system_category_code_cannot_change():
    categorie = ExpenseCategory.objects.create(name="Test système", code="test_sys", is_system=True)
    categorie.code = "autre_code"
    with pytest.raises(ValidationError):
        categorie.save()


@pytest.mark.django_db
def test_attachment_must_be_linked_to_something():
    with pytest.raises(IntegrityError), transaction.atomic():
        Attachment.objects.create(file="garage/ticket.jpg")


def test_attachment_files_are_stored_by_vehicle_number(make_vehicle):
    vehicule = make_vehicle()
    piece_jointe = Attachment(vehicle=vehicule)
    assert attachment_upload_to(piece_jointe, "avant.jpg") == "vehicules/1/avant.jpg"
    assert attachment_upload_to(Attachment(), "ticket.jpg") == "garage/ticket.jpg"


@pytest.mark.django_db
def test_system_category_can_be_renamed():
    """Le nom affiché d'une catégorie système reste modifiable, pas son code."""
    categorie = ExpenseCategory.objects.create(name="Test système", code="test_sys", is_system=True)
    categorie.name = "Nouveau nom"
    categorie.save()
    assert ExpenseCategory.objects.get(code="test_sys").name == "Nouveau nom"

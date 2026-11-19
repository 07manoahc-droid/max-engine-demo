"""Tests de l'administration de secours : rien ne s'y supprime, les règlements sont figés."""

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.urls import reverse

from accounting.models import Settlement

MODELES_DU_GARAGE = [
    modele
    for modele in admin.site._registry
    if modele._meta.app_label in {"core", "vehicles", "expenses", "sales", "accounting"}
]


@pytest.fixture
def requete_admin(db):
    requete = RequestFactory().get("/admin/")
    requete.user = User.objects.create_superuser("secours", password="x")
    return requete


@pytest.mark.parametrize("modele", MODELES_DU_GARAGE, ids=lambda m: m.__name__)
def test_nothing_can_be_deleted_from_admin(modele, requete_admin):
    """Ni bouton « Supprimer », ni action groupée, même pour un superutilisateur."""
    modele_admin = admin.site._registry[modele]
    assert not modele_admin.has_delete_permission(requete_admin)
    assert "delete_selected" not in modele_admin.get_actions(requete_admin)


def test_settlements_are_read_only_in_admin(requete_admin):
    settlement_admin = admin.site._registry[Settlement]
    assert not settlement_admin.has_add_permission(requete_admin)
    assert not settlement_admin.has_change_permission(requete_admin)
    assert settlement_admin.has_view_permission(requete_admin)


def test_history_page_shows_partner_as_author(client, make_vehicle, axel):
    """L'onglet « Historique » s'affiche avec un associé comme auteur (D30)."""
    vehicule = make_vehicle()
    vehicule.history.update(history_user=axel)
    client.force_login(User.objects.create_superuser("secours", password="x"))

    reponse = client.get(reverse("admin:vehicles_vehicle_history", args=[vehicule.pk]))

    assert reponse.status_code == 200
    assert "Axel" in reponse.content.decode()


@pytest.mark.parametrize("modele", MODELES_DU_GARAGE, ids=lambda m: m.__name__)
def test_admin_list_pages_open(db, client, modele):
    """Chaque liste de l'administration s'affiche (contrôle des données à la main)."""
    client.force_login(User.objects.create_superuser("secours", password="x"))
    adresse = reverse(f"admin:{modele._meta.app_label}_{modele._meta.model_name}_changelist")
    assert client.get(adresse).status_code == 200

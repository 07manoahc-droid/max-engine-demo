"""Tests de « Rouvrir la fiche » et « Verrouiller la fiche » (D22) : règles et écrans.

Le verrou des dépenses lui-même est testé dans `expenses/tests/test_lock.py`.
"""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError

from vehicles import services
from vehicles.models import StatusChange, Vehicle

Status = Vehicle.Status


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def vendu(make_vehicle):
    return make_vehicle(brand="GG", model="MC250", status=Status.SOLD)


# --- Règles ------------------------------------------------------------------------


def test_reopen_unlocks_without_changing_the_status(vendu):
    changements = StatusChange.objects.count()
    services.reopen_vehicle(vendu, "  Facture oubliée  ")

    vendu.refresh_from_db()
    assert not vendu.expenses_locked
    assert vendu.status == Status.SOLD
    assert vendu.reopen_reason == "Facture oubliée"
    assert StatusChange.objects.count() == changements


def test_lock_locks_again_and_keeps_the_trace_in_the_history(vendu):
    services.reopen_vehicle(vendu, "Facture oubliée")
    services.lock_vehicle(vendu)

    vendu.refresh_from_db()
    assert vendu.expenses_locked
    (verrouillee, rouverte, *_) = vendu.history.order_by("-history_id")
    assert verrouillee.reopened_at is None
    assert rouverte.reopened_at is not None
    assert rouverte.reopen_reason == "Facture oubliée"


@pytest.mark.parametrize("motif", ["", "   "])
def test_reopen_needs_a_reason(vendu, motif):
    with pytest.raises(ValidationError) as erreur:
        services.reopen_vehicle(vendu, motif)
    assert "reason" in erreur.value.message_dict
    vendu.refresh_from_db()
    assert vendu.expenses_locked


def test_a_second_reopen_is_refused_and_keeps_the_first_reason(vendu):
    perime = Vehicle.objects.get(pk=vendu.pk)
    services.reopen_vehicle(vendu, "Premier motif")
    with pytest.raises(ValidationError, match="Recharge la page"):
        services.reopen_vehicle(perime, "Second motif")
    vendu.refresh_from_db()
    assert vendu.reopen_reason == "Premier motif"


def test_only_a_sold_vehicle_is_reopened(make_vehicle):
    au_garage = make_vehicle(status=Status.IN_REPAIR)
    with pytest.raises(ValidationError):
        services.reopen_vehicle(au_garage, "Motif")
    au_garage.refresh_from_db()
    assert au_garage.reopened_at is None


def test_lock_is_refused_on_a_sheet_that_is_not_reopened(vendu):
    with pytest.raises(ValidationError, match="Recharge la page"):
        services.lock_vehicle(vendu)


# --- Écrans ------------------------------------------------------------------------


def test_reopen_page_explains_what_the_button_does(as_mathis, vendu):
    page = as_mathis.get(f"/vehicules/{vendu.number}/rouvrir/").content.decode()
    assert "Tu pourras corriger les dépenses. N'oublie pas de verrouiller la fiche" in page
    assert "Le véhicule reste vendu." in page


def test_reopen_page_saves_the_reason(as_mathis, vendu):
    reponse = as_mathis.post(f"/vehicules/{vendu.number}/rouvrir/", {"reason": "Facture oubliée"})
    assert reponse.status_code == 302
    assert reponse.url == vendu.get_absolute_url()
    vendu.refresh_from_db()
    assert vendu.reopen_reason == "Facture oubliée"
    assert "tu peux corriger les dépenses" in messages_of(reponse)[0]


def test_reopen_page_shows_the_error_without_a_reason(as_mathis, vendu):
    reponse = as_mathis.post(f"/vehicules/{vendu.number}/rouvrir/", {"reason": "   "})
    assert reponse.status_code == 200
    assert "Indique pourquoi tu rouvres la fiche." in reponse.content.decode()
    vendu.refresh_from_db()
    assert vendu.expenses_locked


def test_reopen_page_sends_back_a_sheet_that_is_not_locked(as_mathis, make_vehicle):
    au_garage = make_vehicle(status=Status.IN_REPAIR)
    reponse = as_mathis.get(f"/vehicules/{au_garage.number}/rouvrir/")
    assert reponse.status_code == 302
    assert "pas verrouillée" in messages_of(reponse)[0]


def test_lock_button_locks_the_sheet(as_mathis, vendu):
    services.reopen_vehicle(vendu, "Facture oubliée")
    reponse = as_mathis.post(f"/vehicules/{vendu.number}/verrouiller/")
    assert reponse.status_code == 302
    assert messages_of(reponse) == ["La fiche est verrouillée."]
    vendu.refresh_from_db()
    assert vendu.expenses_locked


def test_lock_button_of_a_stale_tab_gets_a_message(as_mathis, vendu):
    reponse = as_mathis.post(f"/vehicules/{vendu.number}/verrouiller/")
    assert reponse.status_code == 302
    assert "Recharge la page" in messages_of(reponse)[0]


def test_lock_needs_a_post(as_mathis, vendu):
    assert as_mathis.get(f"/vehicules/{vendu.number}/verrouiller/").status_code == 405


def test_no_lock_banner_on_a_vehicle_at_the_garage(as_mathis, make_vehicle):
    au_garage = make_vehicle(status=Status.IN_REPAIR)
    page = as_mathis.get(au_garage.get_absolute_url()).content.decode()
    assert "Dépenses verrouillées" not in page
    assert "Rouvrir la fiche" not in page

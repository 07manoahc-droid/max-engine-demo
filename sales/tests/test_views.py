"""Tests des écrans des ventes : acompte, vente, modification, annulation, fiche."""

import datetime as dt
import html
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError

from accounting.services import Payment
from core.formatting import NBSP
from core.models import Contact
from sales import services
from sales.models import Deposit, DepositReceipt, Sale, SaleReceipt
from sales.tests.helpers import create_deposit, deposit_amount, record_deposit_for
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

Status = Vehicle.Status
DepositStatus = Deposit.Status


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def deposit_data(partner, **champs) -> dict:
    """« Enregistrer un acompte » ou « Ajouter un versement » : `partner` est le choix de
    « Qui a reçu l'argent ? » (un associé, ou `moitie` / `montants`)."""
    payeur = partner if isinstance(partner, str) else partner.pk
    return {"amount": "200", "date": "2026-10-30", "payer": payeur, **champs}


def receipt_data(partner, **champs) -> dict:
    """« Corriger le versement »."""
    return {"amount": "200", "date": "2026-10-30", "received_by": partner.pk, **champs}


@pytest.fixture
def reserved(purchased, mathis):
    """Le véhicule acheté, réservé par un acompte de 200 € reçu par Mathis."""
    record_deposit_for(
        purchased,
        amount=D("200"),
        date="2026-10-30",
        received_by=mathis,
        buyer=None,
        partner=mathis,
    )
    purchased.refresh_from_db()
    return purchased


# --- Enregistrer un acompte -----------------------------------------------------


def test_deposit_form_proposes_the_current_partner(as_mathis, purchased, mathis):
    response = as_mathis.get(f"/vehicules/{purchased.number}/acompte/")
    assert response.status_code == 200
    assert response.context["form"].fields["payer"].initial == str(mathis.pk)
    assert "passera en « Réservé »" in response.content.decode()


def test_deposit_is_recorded(as_mathis, purchased, mathis):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/", deposit_data(mathis, amount="1 200,50 €")
    )

    assert response.status_code == 302
    assert response.url == purchased.get_absolute_url()
    acompte = Deposit.objects.get()
    versement = DepositReceipt.objects.get()
    assert (versement.deposit, versement.partner, versement.amount) == (
        acompte,
        mathis,
        D("1200.50"),
    )
    assert versement.date == acompte.date
    purchased.refresh_from_db()
    assert purchased.status == Status.RESERVED
    assert messages_of(response) == [f"Acompte enregistré : {purchased.display_name} est réservé."]


def test_deposit_received_by_both_partners(as_mathis, purchased, mathis, axel):
    """« Montants différents » : un versement par associé, à la date de l'acompte (D89)."""
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/",
        deposit_data(
            "montants", amount="300", **{f"amount_{mathis.pk}": "200", f"amount_{axel.pk}": "100"}
        ),
    )

    assert response.status_code == 302
    versements = DepositReceipt.objects.order_by("partner_id")
    assert [(v.partner, v.amount) for v in versements] == [
        (mathis, D("200.00")),
        (axel, D("100.00")),
    ]
    assert deposit_amount(Deposit.objects.get()) == D("300.00")


def test_deposit_split_in_half(as_mathis, purchased, mathis, axel):
    as_mathis.post(f"/vehicules/{purchased.number}/acompte/", deposit_data("moitie", amount="301"))
    versements = DepositReceipt.objects.order_by("partner_id")
    assert sorted(v.amount for v in versements) == [D("150.50"), D("150.50")]


def test_shares_that_do_not_make_the_deposit_are_refused(as_mathis, purchased, mathis, axel):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/",
        deposit_data(
            "montants", amount="300", **{f"amount_{mathis.pk}": "200", f"amount_{axel.pk}": "50"}
        ),
    )
    assert response.status_code == 200
    assert response.context["form"].errors["payer"] == [
        f"Les parts reçues font 250,00{NBSP}€ au lieu de 300,00{NBSP}€. Corrige l'un des montants."
    ]
    assert not Deposit.objects.exists()


def test_deposit_creates_a_buyer_on_the_spot(as_mathis, purchased, mathis):
    as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/",
        deposit_data(mathis, buyer="nouveau", **{"acheteur-name": "Julie Martin"}),
    )
    assert Deposit.objects.get().buyer == Contact.objects.get(name="Julie Martin")


def test_deposit_with_an_existing_buyer(as_mathis, purchased, mathis):
    client = Contact.objects.create(name="Paul Durand")
    as_mathis.post(f"/vehicules/{purchased.number}/acompte/", deposit_data(mathis, buyer=client.pk))
    assert Deposit.objects.get().buyer == client


def test_new_buyer_without_a_name_shows_the_error(as_mathis, purchased, mathis):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/", deposit_data(mathis, buyer="nouveau")
    )
    assert response.status_code == 200
    assert response.context["contact_form"].errors["name"]
    assert not Deposit.objects.exists()
    assert not Contact.objects.exists()


def test_zero_deposit_shows_the_error_under_the_amount(as_mathis, purchased, mathis):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/", deposit_data(mathis, amount="0")
    )
    assert response.status_code == 200
    assert response.context["form"].errors["amount"] == ["L'acompte doit être plus grand que 0 €."]
    assert not Deposit.objects.exists()


def test_deposit_needs_a_partner(as_mathis, purchased):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/acompte/", {"amount": "200", "date": "2026-10-30"}
    )
    assert response.context["form"].errors["payer"] == ["Choisis qui a reçu l'argent."]


def test_deposit_page_refused_on_a_reserved_vehicle(as_mathis, reserved):
    response = as_mathis.get(f"/vehicules/{reserved.number}/acompte/")
    assert response.status_code == 302
    assert messages_of(response) == ["Ce véhicule a déjà un acompte en cours."]


def test_active_deposit_on_a_vehicle_not_reserved_gives_a_message(as_mathis, purchased, mathis):
    """État forcé depuis l'administration : un message, pas une erreur 500."""
    create_deposit(vehicle=purchased, amount=D("100"), received_by=mathis)
    response = as_mathis.post(f"/vehicules/{purchased.number}/acompte/", deposit_data(mathis))
    assert response.status_code == 302
    assert messages_of(response) == ["Ce véhicule a déjà un acompte en cours."]
    assert Deposit.objects.count() == 1


def test_deposit_page_refused_before_the_purchase(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    response = as_mathis.get(f"/vehicules/{vehicule.number}/acompte/")
    assert response.status_code == 302
    assert messages_of(response) == ["Enregistre d'abord l'achat de ce véhicule."]


# --- Corriger un acompte --------------------------------------------------------


def test_correction_form_shows_the_deposit(as_mathis, reserved):
    acompte = Deposit.objects.get()
    response = as_mathis.get(f"/acomptes/{acompte.pk}/corriger/")
    assert response.status_code == 200
    assert response.context["form"].initial["date"] == acompte.date
    contenu = response.content.decode()
    assert "Corriger l'acompte" in contenu
    assert f"200,00{NBSP}€ reçus par Mathis" in contenu


def test_deposit_is_corrected(as_mathis, reserved, axel):
    """« Corriger l'acompte » change la date de la réservation ; l'argent n'y est plus."""
    acompte = Deposit.objects.get()
    response = as_mathis.post(
        f"/acomptes/{acompte.pk}/corriger/", {"date": "2026-10-28", "buyer": ""}
    )

    assert response.url == reserved.get_absolute_url()
    acompte.refresh_from_db()
    assert str(acompte.date) == "2026-10-28"
    assert deposit_amount(acompte) == D("200.00")
    assert messages_of(response) == ["L'acompte est corrigé."]


def test_correction_changes_the_buyer(as_mathis, reserved, mathis):
    client = Contact.objects.create(name="Paul Durand")
    acompte = Deposit.objects.get()
    as_mathis.post(f"/acomptes/{acompte.pk}/corriger/", deposit_data(mathis, buyer=client.pk))
    acompte.refresh_from_db()
    assert acompte.buyer == client


def test_correction_creates_a_new_buyer(as_mathis, reserved, mathis):
    acompte = Deposit.objects.get()
    as_mathis.post(
        f"/acomptes/{acompte.pk}/corriger/",
        deposit_data(mathis, buyer="nouveau", **{"acheteur-name": "Julie Martin"}),
    )
    acompte.refresh_from_db()
    assert acompte.buyer == Contact.objects.get(name="Julie Martin")


def test_correction_keeps_a_buyer_in_the_trash(as_mathis, reserved, mathis):
    """Un acheteur à la corbeille reste proposé, nommé « (à la corbeille) » (D81) : son lien
    n'est pas perdu en silence."""
    client = Contact.objects.create(name="Paul Durand")
    acompte = Deposit.objects.get()
    acompte.buyer = client
    acompte.save()
    client.soft_delete()

    page = as_mathis.get(f"/acomptes/{acompte.pk}/corriger/")
    choix = page.context["form"].fields["buyer"].choices
    assert (str(client.pk), "Paul Durand (à la corbeille)") in choix
    assert page.context["form"]["buyer"].value() == str(client.pk)

    as_mathis.post(f"/acomptes/{acompte.pk}/corriger/", deposit_data(mathis, buyer=client.pk))
    acompte.refresh_from_db()
    assert acompte.buyer_id == client.pk


def test_correction_can_remove_the_buyer(as_mathis, reserved, mathis):
    """« Pas de fiche acheteur » retire l'acheteur de l'acompte."""
    acompte = Deposit.objects.get()
    acompte.buyer = Contact.objects.create(name="Paul Durand")
    acompte.save()

    as_mathis.post(f"/acomptes/{acompte.pk}/corriger/", deposit_data(mathis, buyer=""))

    acompte.refresh_from_db()
    assert acompte.buyer is None


def test_history_names_who_made_each_change(as_mathis, purchased, mathis):
    """L'auteur de chaque étape de l'acompte et de ses versements, par les écrans, est dans
    leur historique."""
    as_mathis.post(f"/vehicules/{purchased.number}/acompte/", deposit_data(mathis))
    acompte = Deposit.objects.get()
    versement = DepositReceipt.objects.get()
    as_mathis.post(
        f"/acomptes/versements/{versement.pk}/corriger/", receipt_data(mathis, amount="350")
    )
    as_mathis.post(f"/acomptes/{acompte.pk}/annuler/", {"outcome": "KEPT"})

    assert [ligne.status for ligne in acompte.history.order_by("history_id")] == [
        DepositStatus.ACTIVE,
        DepositStatus.KEPT,
    ]
    assert [ligne.amount for ligne in versement.history.order_by("history_id")] == [
        D("200.00"),
        D("350.00"),
    ]
    auteurs = {ligne.history_user for ligne in acompte.history.all()}
    auteurs |= {ligne.history_user for ligne in versement.history.all()}
    assert auteurs == {mathis}


def test_trashed_contact_is_not_proposed_for_a_new_deposit(as_mathis, purchased):
    client = Contact.objects.create(name="Paul Durand")
    client.soft_delete()
    page = as_mathis.get(f"/vehicules/{purchased.number}/acompte/")
    assert (str(client.pk), "Paul Durand") not in page.context["form"].fields["buyer"].choices


def test_invalid_correction_changes_nothing(as_mathis, reserved, mathis):
    versement = DepositReceipt.objects.get()
    response = as_mathis.post(
        f"/acomptes/versements/{versement.pk}/corriger/", receipt_data(mathis, amount="abc")
    )
    assert response.status_code == 200
    assert response.context["form"].errors["amount"]
    versement.refresh_from_db()
    assert versement.amount == D("200.00")


# --- Versements -----------------------------------------------------------------


def test_deposit_block_lists_the_receipts(as_mathis, reserved, axel):
    acompte = Deposit.objects.get()
    services.add_deposit_receipts(
        acompte, date=dt.date(2026, 11, 2), receipts=[Payment(axel, D("100"))]
    )
    contenu = as_mathis.get(reserved.get_absolute_url()).content.decode()
    assert f"300,00{NBSP}€" in contenu
    assert "Le 30/10/2026" in contenu and "Le 02/11/2026" in contenu
    assert "Ajouter un versement" in contenu
    assert contenu.count("/acomptes/versements/") == 2


def test_receipt_is_added(as_mathis, reserved, axel):
    acompte = Deposit.objects.get()
    response = as_mathis.post(
        f"/acomptes/{acompte.pk}/versements/ajouter/",
        deposit_data(axel, amount="150", date="2026-11-02"),
    )
    assert response.url == reserved.get_absolute_url()
    assert deposit_amount(acompte) == D("350.00")
    assert messages_of(response) == [f"Versement ajouté : l'acompte fait maintenant 350,00{NBSP}€."]
    reserved.refresh_from_db()
    assert reserved.status == Status.RESERVED


def test_receipt_form_proposes_the_current_partner(as_mathis, reserved, mathis):
    acompte = Deposit.objects.get()
    page = as_mathis.get(f"/acomptes/{acompte.pk}/versements/ajouter/")
    assert page.status_code == 200
    assert page.context["form"].fields["payer"].initial == str(mathis.pk)
    assert f"fait aujourd'hui 200,00{NBSP}€" in page.content.decode()


def test_receipt_is_corrected(as_mathis, reserved, axel):
    versement = DepositReceipt.objects.get()
    response = as_mathis.post(
        f"/acomptes/versements/{versement.pk}/corriger/",
        receipt_data(axel, amount="250", date="2026-10-31"),
    )
    assert response.url == reserved.get_absolute_url()
    versement.refresh_from_db()
    assert (versement.partner, versement.amount, str(versement.date)) == (
        axel,
        D("250.00"),
        "2026-10-31",
    )
    assert messages_of(response) == ["Le versement est corrigé."]


def test_only_receipt_cannot_be_removed(as_mathis, reserved):
    versement = DepositReceipt.objects.get()
    page = as_mathis.get(f"/acomptes/versements/{versement.pk}/corriger/")
    assert "Retirer ce versement" not in page.content.decode()
    assert "C'est le seul versement de cet acompte" in page.content.decode()

    response = as_mathis.post(f"/acomptes/versements/{versement.pk}/retirer/")
    assert messages_of(response) == [
        "C'est le seul versement de cet acompte : pour tout enlever, annule l'acompte."
    ]
    versement.refresh_from_db()
    assert not versement.is_deleted


def test_receipt_is_removed(as_mathis, reserved, axel):
    acompte = Deposit.objects.get()
    (ajoute,) = services.add_deposit_receipts(
        acompte, date=reserved.purchased_on, receipts=[Payment(axel, D("100"))]
    )
    page = as_mathis.get(f"/acomptes/versements/{ajoute.pk}/corriger/")
    assert "Retirer ce versement" in page.content.decode()

    response = as_mathis.post(f"/acomptes/versements/{ajoute.pk}/retirer/")

    assert response.url == reserved.get_absolute_url()
    assert messages_of(response) == ["Le versement est retiré."]
    ajoute.refresh_from_db()
    assert ajoute.is_deleted
    assert deposit_amount(acompte) == D("200.00")


def test_receipt_already_removed_gives_a_message(as_mathis, reserved, axel, mathis):
    """Relecture : un versement retiré depuis un autre onglet ne donne pas une page
    introuvable, ni à la correction ni au retrait."""
    acompte = Deposit.objects.get()
    (ajoute,) = services.add_deposit_receipts(
        acompte, date=reserved.purchased_on, receipts=[Payment(axel, D("100"))]
    )
    services.remove_deposit_receipt(ajoute)
    message = ["Ce versement a déjà été retiré. Recharge la page."]
    for reponse in (
        as_mathis.get(f"/acomptes/versements/{ajoute.pk}/corriger/"),
        as_mathis.post(
            f"/acomptes/versements/{ajoute.pk}/corriger/", receipt_data(mathis, amount="90")
        ),
        as_mathis.post(f"/acomptes/versements/{ajoute.pk}/retirer/"),
    ):
        assert reponse.status_code == 302
        assert reponse.url == reserved.get_absolute_url()
        assert messages_of(reponse)[-1:] == message
    ajoute.refresh_from_db()
    assert ajoute.amount == D("100.00")


def test_zero_receipt_is_worded_as_a_receipt(as_mathis, reserved, mathis):
    acompte = Deposit.objects.get()
    page = as_mathis.post(
        f"/acomptes/{acompte.pk}/versements/ajouter/", deposit_data(mathis, amount="0")
    )
    assert page.status_code == 200
    assert "Le versement doit être plus grand que 0" in html.unescape(page.content.decode())
    assert DepositReceipt.objects.count() == 1


def test_receipts_of_a_closed_deposit_are_not_changed(as_mathis, reserved, mathis):
    acompte = Deposit.objects.get()
    versement = DepositReceipt.objects.get()
    services.cancel_deposit(acompte, DepositStatus.KEPT, mathis)
    message = ["Cet acompte n'est plus en cours : il ne se modifie plus."]
    for adresse in (
        f"/acomptes/{acompte.pk}/versements/ajouter/",
        f"/acomptes/versements/{versement.pk}/corriger/",
    ):
        response = as_mathis.get(adresse)
        assert response.status_code == 302
        # Les redirections ne sont pas suivies : les messages s'accumulent, le dernier compte.
        assert messages_of(response)[-1:] == message
    response = as_mathis.post(f"/acomptes/versements/{versement.pk}/retirer/")
    assert messages_of(response)[-1:] == message
    versement.refresh_from_db()
    assert not versement.is_deleted


def test_closed_deposit_shows_who_received_what(as_mathis, reserved, mathis, axel):
    acompte = Deposit.objects.get()
    services.add_deposit_receipts(
        acompte, date=reserved.purchased_on, receipts=[Payment(axel, D("100"))]
    )
    services.cancel_deposit(acompte, DepositStatus.KEPT, mathis)
    contenu = as_mathis.get(reserved.get_absolute_url()).content.decode()
    assert "Acomptes terminés" in contenu
    assert f"300,00{NBSP}€" in contenu
    assert f"Mathis · 200,00{NBSP}€" in contenu and f"Axel · 100,00{NBSP}€" in contenu
    assert "Ajouter un versement" not in contenu


def test_closed_deposit_cannot_be_opened_for_correction(as_mathis, reserved, mathis):
    acompte = Deposit.objects.get()
    services.cancel_deposit(acompte, DepositStatus.REFUNDED, mathis)
    response = as_mathis.get(f"/acomptes/{acompte.pk}/corriger/")
    assert response.status_code == 302
    assert messages_of(response) == ["Cet acompte n'est plus en cours : il ne se modifie plus."]


# --- Annuler un acompte ---------------------------------------------------------


def test_cancel_page_explains_the_choice(as_mathis, reserved):
    response = as_mathis.get(f"/acomptes/{Deposit.objects.get().pk}/annuler/")
    contenu = response.content.decode()
    assert response.status_code == 200
    assert response.context["return_status"] == Status.PURCHASED
    assert "Rendu à l&#x27;acheteur" in contenu
    assert "Conservé par le garage" in contenu
    assert "Le véhicule reviendra en" in contenu
    assert "Garder l'acompte" not in contenu


def test_deposit_is_cancelled(as_mathis, reserved):
    acompte = Deposit.objects.get()
    response = as_mathis.post(f"/acomptes/{acompte.pk}/annuler/", {"outcome": "REFUNDED"})

    assert response.url == reserved.get_absolute_url()
    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.REFUNDED
    reserved.refresh_from_db()
    assert reserved.status == Status.PURCHASED
    assert messages_of(response) == ["L'acompte est annulé et rendu."]


def test_kept_deposit_message(as_mathis, reserved):
    acompte = Deposit.objects.get()
    response = as_mathis.post(f"/acomptes/{acompte.pk}/annuler/", {"outcome": "KEPT"})
    assert messages_of(response) == ["L'acompte est annulé et conservé."]


def test_cancelling_needs_a_choice(as_mathis, reserved):
    acompte = Deposit.objects.get()
    response = as_mathis.post(f"/acomptes/{acompte.pk}/annuler/", {"outcome": ""})
    assert response.status_code == 200
    assert response.context["form"].errors["outcome"] == [
        "Indique si l'acompte a été rendu ou conservé."
    ]
    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.ACTIVE


def test_cancelling_refuses_an_unknown_choice(as_mathis, reserved):
    """« Déduit de la vente » n'est pas une issue d'annulation."""
    acompte = Deposit.objects.get()
    response = as_mathis.post(f"/acomptes/{acompte.pk}/annuler/", {"outcome": "APPLIED"})
    assert response.status_code == 200
    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.ACTIVE


def test_closed_deposit_cannot_be_cancelled_again(as_mathis, reserved, mathis):
    acompte = Deposit.objects.get()
    services.cancel_deposit(acompte, DepositStatus.KEPT, mathis)
    response = as_mathis.post(f"/acomptes/{acompte.pk}/annuler/", {"outcome": "REFUNDED"})
    assert response.status_code == 302
    assert messages_of(response) == ["Cet acompte n'est plus en cours."]
    acompte.refresh_from_db()
    assert acompte.status == DepositStatus.KEPT


# --- Fiche du véhicule ----------------------------------------------------------


def test_sheet_proposes_a_deposit_once_purchased(as_mathis, purchased):
    contenu = as_mathis.get(purchased.get_absolute_url()).content.decode()
    assert "Enregistrer un acompte" in contenu
    assert 'id="titre-acompte"' not in contenu


def test_sheet_does_not_propose_a_deposit_before_the_purchase(as_mathis, make_vehicle):
    contenu = as_mathis.get(make_vehicle().get_absolute_url()).content.decode()
    assert "Enregistrer un acompte" not in contenu


def test_sheet_of_a_reserved_vehicle_shows_the_deposit(as_mathis, reserved):
    contenu = as_mathis.get(reserved.get_absolute_url()).content.decode()
    assert "Enregistrer un acompte" not in contenu
    assert f"200,00{NBSP}€" in contenu
    assert "Corriger l'acompte" in contenu
    assert "Annuler l'acompte" in contenu


def test_sheet_keeps_a_cancelled_deposit_visible(as_mathis, reserved, mathis):
    services.cancel_deposit(Deposit.objects.get(), DepositStatus.KEPT, mathis)
    contenu = as_mathis.get(reserved.get_absolute_url()).content.decode()
    assert "Acomptes terminés" in contenu
    assert "Conservé" in contenu
    assert "Corriger l" not in contenu


def test_sheet_titles_the_finished_deposits_only_when_there_are_some(as_mathis, reserved):
    contenu = as_mathis.get(reserved.get_absolute_url()).content.decode()
    assert "Acomptes terminés" not in contenu


def test_read_only_hides_the_deposit_button(as_mathis, purchased, settings):
    settings.READ_ONLY = True
    contenu = as_mathis.get(purchased.get_absolute_url()).content.decode()
    assert "Enregistrer un acompte" not in contenu


def test_read_only_hides_the_deposit_actions(as_mathis, reserved, settings):
    settings.READ_ONLY = True
    contenu = as_mathis.get(reserved.get_absolute_url()).content.decode()
    assert "/corriger/" not in contenu
    assert "/annuler/" not in contenu


# --- Enregistrer une vente -------------------------------------------------------


def sale_data(partner, **champs) -> dict:
    return {"price": "2600", "date": "2026-10-30", "payer": partner.pk, **champs}


def record_sale(vehicule, partner, price="2600", amount="2600"):
    vente = services.record_sale(
        vehicule,
        price=D(price),
        date="2026-10-30",
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(partner, D(amount))],
        partner=partner,
    )
    vehicule.refresh_from_db()
    return vente


def test_sale_form_proposes_the_current_partner(as_mathis, purchased, mathis):
    response = as_mathis.get(f"/vehicules/{purchased.number}/vente/")
    assert response.status_code == 200
    assert "Qui a encaissé ?" in response.content.decode()
    assert response.context["form"]["payer"].value() == str(mathis.pk)


def test_sale_form_shows_the_deposit_and_its_buyer(as_mathis, purchased, mathis):
    client = Contact.objects.create(name="Paul")
    record_deposit_for(
        purchased,
        amount=D("200"),
        date="2026-10-30",
        received_by=mathis,
        buyer=client,
        partner=mathis,
    )
    response = as_mathis.get(f"/vehicules/{purchased.number}/vente/")
    assert f"200,00{NBSP}€" in response.content.decode()
    assert response.context["form"]["buyer"].value() == str(client.pk)


def test_sale_records_the_vehicle_as_sold(as_mathis, purchased, mathis):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/",
        sale_data(mathis, sold_where="Annonce en ligne", notes="Sans garantie"),
    )
    assert response.status_code == 302
    purchased.refresh_from_db()
    assert purchased.status == Status.SOLD
    vente = purchased.sales.get()
    assert (vente.price, vente.sold_where) == (D("2600.00"), "Annonce en ligne")
    assert vente.receipts.get().amount == D("2600.00")
    assert "est vendu" in messages_of(response)[0]


def test_sale_shared_half_half(as_mathis, purchased, mathis, axel):
    as_mathis.post(
        f"/vehicules/{purchased.number}/vente/", sale_data(mathis, payer="moitie", price="2600.01")
    )
    montants = {e.partner: e.amount for e in purchased.sales.get().receipts.all()}
    assert montants == {mathis: D("1300.01"), axel: D("1300.00")}


def test_sale_with_different_amounts(as_mathis, purchased, mathis, axel):
    as_mathis.post(
        f"/vehicules/{purchased.number}/vente/",
        sale_data(
            mathis,
            payer="montants",
            **{f"amount_{mathis.pk}": "2000", f"amount_{axel.pk}": "600"},
        ),
    )
    montants = {e.partner: e.amount for e in purchased.sales.get().receipts.all()}
    assert montants == {mathis: D("2000.00"), axel: D("600.00")}


def test_different_amounts_are_worded_as_receipts(as_mathis, purchased, mathis, axel):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/",
        sale_data(mathis, payer="montants", **{f"amount_{mathis.pk}": "2000"}),
    )
    contenu = html.unescape(response.content.decode())
    assert "Encaissé par Axel" in contenu
    assert "Indique combien Axel a encaissé." in contenu


def test_different_amounts_must_make_what_is_left(as_mathis, purchased, mathis, axel):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/",
        sale_data(
            mathis,
            payer="montants",
            **{f"amount_{mathis.pk}": "2000", f"amount_{axel.pk}": "100"},
        ),
    )
    assert response.status_code == 200
    assert "Corrige l'un des montants" in html.unescape(response.content.decode())
    assert not purchased.sales.exists()


def test_sale_of_a_reserved_vehicle_receives_only_what_is_left(as_mathis, reserved, axel):
    response = as_mathis.post(
        f"/vehicules/{reserved.number}/vente/", sale_data(axel, payer=axel.pk)
    )
    assert response.status_code == 302
    vente = reserved.sales.get()
    assert vente.receipts.get().amount == D("2400.00")
    assert reserved.deposits.get().status == DepositStatus.APPLIED


def test_sale_covered_by_the_deposit_needs_no_receiver(as_mathis, reserved):
    """Reste à encaisser de 0 € : on ne choisit personne, et la vente passe."""
    response = as_mathis.post(
        f"/vehicules/{reserved.number}/vente/", {"price": "200", "date": "2026-10-30"}
    )
    assert response.status_code == 302
    assert not reserved.sales.get().receipts.exists()


def test_sale_with_a_price_below_the_deposit_is_refused(as_mathis, reserved, mathis):
    response = as_mathis.post(
        f"/vehicules/{reserved.number}/vente/", sale_data(mathis, price="150")
    )
    assert response.status_code == 200
    assert "Corrige le prix" in html.unescape(response.content.decode())
    assert not reserved.sales.exists()


def test_sale_without_receiver_is_refused(as_mathis, purchased):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/", {"price": "2600", "date": "2026-10-30"}
    )
    assert response.status_code == 200
    assert "Choisis qui a encaissé" in html.unescape(response.content.decode())


def test_sale_with_a_new_buyer(as_mathis, purchased, mathis):
    as_mathis.post(
        f"/vehicules/{purchased.number}/vente/",
        sale_data(mathis, buyer="nouveau", **{"acheteur-name": "Paul"}),
    )
    assert purchased.sales.get().buyer.name == "Paul"


def test_sale_refused_on_a_vehicle_without_purchase(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    response = as_mathis.get(f"/vehicules/{vehicule.number}/vente/")
    assert response.status_code == 302
    assert "achat" in messages_of(response)[0]


def test_double_submit_records_one_sale(as_mathis, purchased, mathis):
    """Deuxième envoi : le véhicule est déjà vendu, un refus clair et aucune seconde vente."""
    url = f"/vehicules/{purchased.number}/vente/"
    as_mathis.post(url, sale_data(mathis))
    response = as_mathis.post(url, sale_data(mathis))
    assert response.status_code == 302
    assert purchased.sales.count() == 1
    assert "déjà vendu" in messages_of(response)[-1]


# --- Aperçu pendant la saisie ----------------------------------------------------


def test_preview_shows_what_is_left_the_margin_and_the_shares(as_mathis, reserved):
    response = as_mathis.post(f"/vehicules/{reserved.number}/vente/apercu/", {"price": "2600"})
    contenu = response.content.decode()
    assert f"2{NBSP}400,00{NBSP}€" in contenu  # 2 600 € − 200 € d'acompte
    assert f"1{NBSP}100,00{NBSP}€" in contenu  # 2 600 € − 1 500 € d'achat
    assert f"550,00{NBSP}€" in contenu
    assert "Pour Axel" in contenu


def test_preview_is_empty_without_a_valid_price(as_mathis, purchased):
    url = f"/vehicules/{purchased.number}/vente/apercu/"
    assert as_mathis.post(url, {"price": ""}).content == b""
    assert as_mathis.post(url, {"price": "abc"}).content == b""


def test_preview_reports_a_price_below_the_deposit(as_mathis, reserved):
    response = as_mathis.post(f"/vehicules/{reserved.number}/vente/apercu/", {"price": "150"})
    assert "Corrige le prix" in html.unescape(response.content.decode())


def test_preview_says_when_nothing_is_left(as_mathis, reserved):
    response = as_mathis.post(f"/vehicules/{reserved.number}/vente/apercu/", {"price": "200"})
    assert "rien à encaisser" in html.unescape(response.content.decode())


# --- Fiche du véhicule -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "bleu"),
    [
        (Status.PURCHASED, "Ajouter une dépense"),
        (Status.IN_REPAIR, "Ajouter une dépense"),
        (Status.READY_FOR_SALE, "Enregistrer la vente"),
        (Status.FOR_SALE, "Enregistrer la vente"),
    ],
)
def test_detail_gives_the_blue_button_to_the_expected_action(
    as_mathis, purchased, mathis, status, bleu
):
    if status != purchased.status:
        vehicle_services.change_status(purchased, status, mathis)
    contenu = as_mathis.get(f"/vehicules/{purchased.number}/").content.decode()
    assert "Enregistrer la vente" in contenu
    assert contenu.count('class="btn-primary"') == 1
    bouton_bleu = contenu.split('class="btn-primary"')[1].split("</a>")[0]
    assert bleu in bouton_bleu


def test_detail_of_a_reserved_vehicle_proposes_the_sale(as_mathis, reserved):
    contenu = as_mathis.get(f"/vehicules/{reserved.number}/").content.decode()
    assert "Enregistrer la vente" in contenu.split('class="btn-primary"')[1].split("</a>")[0]


def test_detail_without_purchase_does_not_propose_the_sale(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    assert (
        "Enregistrer la vente"
        not in as_mathis.get(f"/vehicules/{vehicule.number}/").content.decode()
    )


def test_detail_of_a_sold_vehicle_shows_the_sale(as_mathis, purchased, mathis, axel):
    """Exemple du cahier des charges : marge, gain de chacun, tableau, solde, démarches."""
    record_sale(purchased, mathis)
    contenu = html.unescape(as_mathis.get(f"/vehicules/{purchased.number}/").content.decode())

    assert "Solde actuel entre vous" in contenu
    assert f"Mathis doit 550,00{NBSP}€ à Axel" in contenu
    assert "Marge" in contenu
    assert "Part totale" in contenu
    assert "certificat de cession" in contenu
    assert "Modifier la vente" in contenu
    assert "Annuler la vente" in contenu
    assert 'class="btn-primary"' not in contenu
    assert "Enregistrer la vente" not in contenu


def test_detail_says_when_the_garage_balance_differs_from_the_vehicle(
    as_mathis, purchased, mathis, axel, make_vehicle
):
    """Le solde compte tout le garage : le montant du véhicule seul est précisé."""
    record_sale(purchased, mathis)
    autre = make_vehicle(brand="Yamaha", model="125")
    vehicle_services.record_purchase(
        autre,
        price=D("400"),
        date="2026-10-30",
        payments=[Payment(axel, D("400"))],
        seller=None,
        partner=axel,
    )
    contenu = html.unescape(as_mathis.get(f"/vehicules/{purchased.number}/").content.decode())
    assert f"Mathis doit 750,00{NBSP}€ à Axel" in contenu
    assert f"Pour ce véhicule seul : Mathis doit 550,00{NBSP}€ à Axel" in contenu


def test_detail_links_to_the_settlement_and_comes_back(as_mathis, purchased, mathis, axel):
    """D55 : « Enregistrer le règlement » à côté du solde, puis retour à la fiche."""
    record_sale(purchased, mathis)
    fiche = f"/vehicules/{purchased.number}/"
    lien = f"/comptes/reglements/ajouter/?vehicule={purchased.number}"
    assert f'href="{lien}"' in as_mathis.get(fiche).content.decode()

    reponse = as_mathis.post(
        lien,
        {
            "amount": "550",
            "date": "2026-11-03",
            "from_partner": mathis.pk,
            "to_partner": axel.pk,
            "seen_due": "550",
        },
    )
    assert reponse.url == fiche
    contenu = html.unescape(as_mathis.get(fiche).content.decode())
    assert "À l'équilibre" in contenu  # le bloc Vente (D67)
    assert "Vous êtes à l'équilibre." in contenu  # le message de confirmation
    assert lien not in contenu


def test_detail_hides_the_settlement_link_in_read_only(as_mathis, purchased, mathis, settings):
    record_sale(purchased, mathis)
    settings.READ_ONLY = True
    contenu = as_mathis.get(f"/vehicules/{purchased.number}/").content.decode()
    assert "/comptes/reglements/ajouter/" not in contenu


def test_detail_does_not_repeat_a_balance_equal_to_the_vehicle(as_mathis, purchased, mathis):
    record_sale(purchased, mathis)
    contenu = html.unescape(as_mathis.get(f"/vehicules/{purchased.number}/").content.decode())
    assert "Pour ce véhicule seul" not in contenu


def test_detail_shows_the_sale_without_shares_when_a_partner_left(
    as_mathis, purchased, mathis, axel
):
    record_sale(purchased, mathis)
    axel.is_active = False
    axel.save()
    contenu = html.unescape(as_mathis.get(f"/vehicules/{purchased.number}/").content.decode())
    assert "Préviens Mathis" in contenu
    assert "Solde actuel entre vous" not in contenu


def test_detail_hides_the_sale_actions_in_read_only(as_mathis, purchased, mathis, settings):
    record_sale(purchased, mathis)
    settings.READ_ONLY = True
    contenu = as_mathis.get(f"/vehicules/{purchased.number}/").content.decode()
    assert "Modifier la vente" not in contenu


def test_detail_keeps_cancelled_sales_visible(as_mathis, purchased, mathis):
    vente = record_sale(purchased, mathis)
    services.cancel_sale(vente, None, mathis)
    contenu = html.unescape(as_mathis.get(f"/vehicules/{purchased.number}/").content.decode())
    assert "Ventes annulées" in contenu
    assert "Modifier la vente" not in contenu
    assert "Enregistrer la vente" in contenu


# --- Modifier une vente ----------------------------------------------------------


def test_edit_form_shows_the_sale(as_mathis, purchased, mathis):
    vente = record_sale(purchased, mathis)
    response = as_mathis.get(f"/ventes/{vente.pk}/modifier/")
    form = response.context["form"]
    assert form["price"].value() == D("2600.00")
    assert form["payer"].value() == str(mathis.pk)


def test_edit_form_recognises_a_half_half_receipt(as_mathis, purchased, mathis, axel):
    vente = services.record_sale(
        purchased,
        price=D("2600"),
        date="2026-10-30",
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1300")), Payment(axel, D("1300"))],
        partner=mathis,
    )
    response = as_mathis.get(f"/ventes/{vente.pk}/modifier/")
    assert response.context["form"]["payer"].value() == "moitie"


def test_edit_updates_the_sale_and_trashes_the_removed_receipt(
    as_mathis, purchased, mathis, axel
):
    vente = services.record_sale(
        purchased,
        price=D("2600"),
        date="2026-10-30",
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1300")), Payment(axel, D("1300"))],
        partner=mathis,
    )
    response = as_mathis.post(
        f"/ventes/{vente.pk}/modifier/", sale_data(mathis, price="2500", notes="Remise")
    )
    assert response.status_code == 302
    assert messages_of(response) == ["La vente est modifiée."]
    vente.refresh_from_db()
    assert (vente.price, vente.notes) == (D("2500.00"), "Remise")
    assert vente.receipts.get().amount == D("2500.00")
    assert SaleReceipt.all_objects.filter(sale=vente, partner=axel).get().is_deleted


def test_edit_with_an_applied_deposit_keeps_the_deduction(as_mathis, reserved, mathis):
    vente = record_sale(reserved, mathis, amount="2400")
    response = as_mathis.post(f"/ventes/{vente.pk}/modifier/", sale_data(mathis, price="2500"))
    assert response.status_code == 302
    assert vente.receipts.get().amount == D("2300.00")
    assert reserved.deposits.get().status == DepositStatus.APPLIED


def test_edit_refused_on_a_cancelled_sale(as_mathis, purchased, mathis):
    vente = record_sale(purchased, mathis)
    services.cancel_sale(vente, None, mathis)
    response = as_mathis.post(f"/ventes/{vente.pk}/modifier/", sale_data(mathis, price="10"))
    assert response.status_code == 302
    assert "annulée" in messages_of(response)[0]
    vente.refresh_from_db()
    assert vente.price == D("2600.00")


def test_preview_during_an_edit_uses_the_applied_deposit(as_mathis, reserved, mathis):
    record_sale(reserved, mathis, amount="2400")
    response = as_mathis.post(f"/vehicules/{reserved.number}/vente/apercu/", {"price": "2500"})
    assert f"2{NBSP}300,00{NBSP}€" in response.content.decode()


# --- Annuler une vente -----------------------------------------------------------


def test_cancel_page_explains_what_will_happen(as_mathis, purchased, mathis):
    vente = record_sale(purchased, mathis)
    response = as_mathis.get(f"/ventes/{vente.pk}/annuler/")
    contenu = html.unescape(response.content.decode())
    assert response.status_code == 200
    assert "n'est pas une dépense" in contenu
    assert 'class="btn-danger"' in contenu
    assert "Qu'est devenu l'acompte ?" not in contenu


def test_cancel_without_deposit(as_mathis, purchased, mathis):
    vente = record_sale(purchased, mathis)
    response = as_mathis.post(f"/ventes/{vente.pk}/annuler/", {"csrf": "x"})
    assert response.status_code == 302
    vente.refresh_from_db()
    assert vente.is_cancelled
    purchased.refresh_from_db()
    assert purchased.status == Status.FOR_SALE
    assert "revient en vente" in messages_of(response)[0]


def test_cancel_with_a_deposit_asks_what_it_became(as_mathis, reserved, mathis):
    vente = record_sale(reserved, mathis, amount="2400")
    url = f"/ventes/{vente.pk}/annuler/"

    assert "Qu'est devenu l'acompte ?" in html.unescape(as_mathis.get(url).content.decode())

    response = as_mathis.post(url, {"csrf": "x"})
    assert response.status_code == 200
    vente.refresh_from_db()
    assert not vente.is_cancelled

    response = as_mathis.post(url, {"outcome": DepositStatus.KEPT})
    assert response.status_code == 302
    assert reserved.deposits.get().status == DepositStatus.KEPT


def test_cancel_twice_is_refused(as_mathis, purchased, mathis):
    vente = record_sale(purchased, mathis)
    url = f"/ventes/{vente.pk}/annuler/"
    as_mathis.post(url, {"csrf": "x"})
    response = as_mathis.post(url, {"csrf": "x"})
    assert response.status_code == 302
    assert "déjà annulée" in messages_of(response)[-1]


# --- Corrections après relecture -------------------------------------------------


def test_former_partner_who_received_stays_proposed_on_the_edit_form(
    as_mathis, purchased, mathis, axel
):
    """Corriger une note ne doit pas faire passer l'encaissement à un autre associé."""
    vente = record_sale(purchased, axel)
    axel.is_active = False
    axel.save()
    url = f"/ventes/{vente.pk}/modifier/"

    form = as_mathis.get(url).context["form"]
    assert form["payer"].value() == str(axel.pk)
    assert "Axel" in [libelle for _, libelle, _ in form.payer_options()]
    assert "Moitié-moitié" not in [libelle for _, libelle, _ in form.payer_options()]

    response = as_mathis.post(url, sale_data(axel, notes="Remise en main propre"))

    assert response.status_code == 302
    assert vente.receipts.get().partner == axel
    vente.refresh_from_db()
    assert vente.notes == "Remise en main propre"


def test_edit_refused_when_a_former_partner_shared_the_sale(as_mathis, purchased, mathis, axel):
    """Sans ce refus, sa part passerait à l'autre associé sans que personne le voie."""
    vente = services.record_sale(
        purchased,
        price=D("2600"),
        date="2026-10-30",
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1300")), Payment(axel, D("1300"))],
        partner=mathis,
    )
    axel.is_active = False
    axel.save()
    url = f"/ventes/{vente.pk}/modifier/"
    assert as_mathis.get(url).status_code == 200

    response = as_mathis.post(url, sale_data(mathis, price="2500"))

    assert response.status_code == 200
    assert "préviens Mathis" in html.unescape(response.content.decode())
    vente.refresh_from_db()
    assert vente.price == D("2600.00")
    assert {(e.partner, e.amount) for e in vente.receipts.all()} == {
        (mathis, D("1300.00")),
        (axel, D("1300.00")),
    }


def test_former_partner_with_two_receipts_is_proposed_once_with_an_avatar(
    as_mathis, purchased, mathis, axel
):
    vente = record_sale(purchased, axel, price="400", amount="400")
    SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("600"))
    Sale.objects.filter(pk=vente.pk).update(price=D("1000"))
    axel.is_active = False
    axel.save()

    form = as_mathis.get(f"/ventes/{vente.pk}/modifier/").context["form"]

    assert [associe for _, _, associe in form.payer_options()] == [mathis, axel]


def test_edit_form_recognises_a_shared_receipt_after_a_deposit(as_mathis, reserved, mathis, axel):
    """Le partage se compare à ce qui restait à encaisser, pas au prix de vente."""
    vente = services.record_sale(
        reserved,
        price=D("2600"),
        date="2026-10-30",
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1200")), Payment(axel, D("1200"))],
        partner=mathis,
    )
    form = as_mathis.get(f"/ventes/{vente.pk}/modifier/").context["form"]
    assert form["payer"].value() == "moitie"


def test_wrong_custom_receipts_are_explained_in_the_words_of_a_sale(
    as_mathis, purchased, mathis, axel
):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/",
        sale_data(
            mathis,
            payer="montants",
            **{f"amount_{mathis.pk}": "1000", f"amount_{axel.pk}": "1000"},
        ),
    )
    assert response.status_code == 200
    assert "Les parts encaissées font" in html.unescape(response.content.decode())
    assert not purchased.sales.exists()


def test_sale_form_asks_who_received_the_money(as_mathis, purchased):
    contenu = html.unescape(as_mathis.get(f"/vehicules/{purchased.number}/vente/").content.decode())
    assert "Qui a encaissé ?" in contenu
    assert "Qui a payé ?" not in contenu


def test_edit_form_adds_two_receipts_of_a_partner(as_mathis, purchased, mathis, axel):
    """Le montant prérempli est le total de l'associé, pas son dernier encaissement."""
    vente = record_sale(purchased, mathis, price="400", amount="400")
    SaleReceipt.objects.create(sale=vente, partner=mathis, amount=D("600"))
    SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("500"))
    Sale.objects.filter(pk=vente.pk).update(price=D("1500"))

    form = as_mathis.get(f"/ventes/{vente.pk}/modifier/").context["form"]

    assert form["payer"].value() == "montants"
    assert form[f"amount_{mathis.pk}"].value() == D("1000.00")
    assert form[f"amount_{axel.pk}"].value() == D("500.00")


def test_preview_during_an_edit_does_not_say_money_is_missing(as_mathis, purchased, mathis):
    record_sale(purchased, mathis)
    contenu = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/apercu/", {"price": "2600"}
    ).content.decode()
    assert "Total des encaissements" in contenu
    assert "Reste à encaisser" not in contenu


def test_preview_at_creation_says_what_is_left_to_receive(as_mathis, purchased):
    contenu = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/apercu/", {"price": "2600"}
    ).content.decode()
    assert "Reste à encaisser" in contenu
    assert "Total des encaissements" not in contenu


def test_preview_shows_a_loss_without_a_minus_sign(as_mathis, purchased):
    contenu = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/apercu/", {"price": "1200"}
    ).content.decode()
    assert f"Perte de 300,00{NBSP}€" in contenu
    assert "Perte pour Mathis" in contenu
    assert "Perte pour Axel" in contenu
    assert f"150,00{NBSP}€" in contenu
    assert "Marge" not in contenu
    assert f"-300,00{NBSP}€" not in contenu
    assert f"-150,00{NBSP}€" not in contenu


def test_preview_of_a_profit_keeps_its_wording(as_mathis, purchased):
    contenu = as_mathis.post(
        f"/vehicules/{purchased.number}/vente/apercu/", {"price": "2600"}
    ).content.decode()
    assert "Marge" in contenu
    assert "Pour Axel" in contenu
    assert "Perte" not in contenu
    assert "acomptes conservés" not in contenu
    assert "Calculé avec" not in contenu


@pytest.mark.parametrize(
    ("price", "margin"),
    [("2600", "Marge"), ("1000", f"Perte de 300,00{NBSP}€")],
)
def test_preview_mentions_the_kept_deposits(as_mathis, reserved, mathis, price, margin):
    """Acompte de 200 € conservé : il compte dans la marge, gain comme perte."""
    services.cancel_deposit(reserved.deposits.get(), DepositStatus.KEPT, mathis)
    contenu = html.unescape(
        as_mathis.post(
            f"/vehicules/{reserved.number}/vente/apercu/", {"price": price}
        ).content.decode()
    )
    assert margin in contenu
    assert f"Calculé avec 200,00{NBSP}€ d'acomptes conservés" in contenu


def test_detail_shows_a_loss(as_mathis, purchased, mathis):
    record_sale(purchased, mathis, price="1200", amount="1200")
    contenu = as_mathis.get(f"/vehicules/{purchased.number}/").content.decode()
    assert f"Perte de 300,00{NBSP}€" in contenu
    assert "Perte pour Mathis" in contenu
    assert "Perte pour Axel" in contenu
    assert f"150,00{NBSP}€" in contenu
    # Le tableau des comptes, plus bas, garde ses signes : seules les tuiles parlent de « perte ».
    assert f"-300,00{NBSP}€" not in contenu


def test_detail_shows_a_profit_with_its_usual_wording(as_mathis, purchased, mathis):
    record_sale(purchased, mathis, price="2600", amount="2600")
    contenu = as_mathis.get(f"/vehicules/{purchased.number}/").content.decode()
    assert f"1{NBSP}100,00{NBSP}€" in contenu
    assert "Pour Axel" in contenu
    assert "Perte" not in contenu


def test_sale_refused_by_the_service_stays_on_the_form(as_mathis, purchased, mathis, monkeypatch):
    """Le service refuse après les contrôles de la vue : le message reste sur l'écran."""

    def refus(*args, **kwargs):
        raise ValidationError("Ce véhicule a déjà changé de statut. Recharge la page.")

    monkeypatch.setattr(services, "record_sale", refus)
    response = as_mathis.post(f"/vehicules/{purchased.number}/vente/", sale_data(mathis))
    assert response.status_code == 200
    assert "Recharge la page" in response.content.decode()


def test_cancel_refused_by_the_service_goes_back_to_the_vehicle(
    as_mathis, purchased, mathis, monkeypatch
):
    vente = record_sale(purchased, mathis)

    def refus(*args, **kwargs):
        raise ValidationError("Cette vente est déjà annulée. Recharge la page.")

    monkeypatch.setattr(services, "cancel_sale", refus)
    response = as_mathis.post(f"/ventes/{vente.pk}/annuler/", {"csrf": "x"})
    assert response.status_code == 302
    assert "déjà annulée" in messages_of(response)[0]

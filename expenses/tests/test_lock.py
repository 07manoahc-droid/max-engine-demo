"""Tests du verrou des dépenses d'un véhicule vendu (D22, D50) : chaque action bloquée,
refus puis succès après réouverture, dépense sur plusieurs véhicules, revente, écrans."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from accounting import queries
from accounting.services import Payment
from core.formatting import format_euros as euros
from core.models import Attachment
from expenses import services
from expenses.models import Expense, ExpenseLine, Refund
from expenses.services import LineEntry
from sales import services as sale_services
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

from .test_views import form_data, line

Status = ExpenseLine.Status
DATE = dt.date(2026, 10, 25)


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def save(lines, payments, total, expense=None, **champs):
    valeurs = {
        "date": dt.date(2026, 10, 20),
        "supplier_name": "",
        "order_number": "",
        "total_paid": D(total),
        "is_personal": False,
        "notes": "",
    }
    return services.save_expense(expense, lines=lines, payments=payments, **valeurs | champs)


def buy(vehicle, partner, price="1500"):
    vehicle_services.record_purchase(
        vehicle,
        price=D(price),
        date=dt.date(2026, 10, 1),
        payments=[Payment(partner, D(price))],
        seller=None,
        partner=partner,
    )
    vehicle.refresh_from_db()
    return vehicle


def sell(vehicle, partner, price="2000"):
    vente = sale_services.record_sale(
        vehicle,
        price=D(price),
        date=dt.date(2026, 10, 28),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(partner, D(price))],
        partner=partner,
    )
    vehicle.refresh_from_db()
    return vente


def reopen(vehicle):
    vehicle_services.reopen_vehicle(vehicle, "Facture oubliée")
    vehicle.refresh_from_db()


@pytest.fixture
def moto(make_vehicle, mathis):
    return buy(make_vehicle(brand="GG", model="MC250"), mathis)


@pytest.fixture
def autre(make_vehicle, mathis):
    return buy(make_vehicle(brand="Honda", model="CR"), mathis)


@pytest.fixture
def disque(moto, parts_category, mathis):
    """Disque de 60 € sur la moto, payé par Mathis."""
    depense = save(
        [LineEntry("Disque", parts_category, D("60.00"), vehicle=moto, status=Status.ORDERED)],
        [Payment(mathis, D("60.00"))],
        "60.00",
    )
    return depense.lines.get()


@pytest.fixture
def vendue(moto, disque, mathis):
    """La moto, avec son disque, vendue : ses dépenses sont verrouillées."""
    sell(moto, mathis)
    return moto


def entry(line, price="60.00", **champs):
    valeurs = {
        "label": line.label,
        "category": line.category,
        "unit_price": D(price),
        "vehicle": line.vehicle,
        "status": line.status,
        "pk": line.pk,
    }
    return LineEntry(**valeurs | champs)


def facture(expense) -> Attachment:
    return Attachment.objects.create(
        file=SimpleUploadedFile("f.pdf", b"%PDF-1.4"),
        kind=Attachment.Kind.INVOICE,
        expense=expense,
        order=0,
    )


# --- Chaque action bloquée ----------------------------------------------------------


def test_new_expense_on_a_locked_vehicle_is_refused_until_reopened(vendue, parts_category, mathis):
    ligne = LineEntry("Câble", parts_category, D("10.00"), vehicle=vendue)
    with pytest.raises(ValidationError, match="verrouillées. Rouvre sa fiche"):
        save([ligne], [Payment(mathis, D("10.00"))], "10.00")
    assert not ExpenseLine.objects.filter(label="Câble").exists()

    reopen(vendue)
    save([ligne], [Payment(mathis, D("10.00"))], "10.00")
    assert ExpenseLine.objects.filter(label="Câble", vehicle=vendue).exists()


def test_editing_an_expense_is_refused_until_reopened(vendue, disque, mathis):
    depense = disque.expense
    nouveau = [entry(disque, "50.00")]
    paiement = [Payment(mathis, D("50.00"))]
    with pytest.raises(ValidationError, match="verrouillées"):
        save(nouveau, paiement, "50.00", expense=depense)
    disque.refresh_from_db()
    assert disque.unit_price == D("60.00")

    reopen(vendue)
    save(nouveau, paiement, "50.00", expense=depense)
    disque.refresh_from_db()
    assert disque.unit_price == D("50.00")


def test_moving_a_line_off_a_locked_vehicle_is_refused(vendue, disque, autre, mathis):
    with pytest.raises(ValidationError, match="verrouillées"):
        save(
            [entry(disque, vehicle=autre)],
            [Payment(mathis, D("60.00"))],
            "60.00",
            expense=disque.expense,
        )
    disque.refresh_from_db()
    assert disque.vehicle == vendue


def test_moving_a_line_onto_a_locked_vehicle_is_refused(vendue, autre, parts_category, mathis):
    depense = save(
        [LineEntry("Durite", parts_category, D("8.00"), vehicle=autre)],
        [Payment(mathis, D("8.00"))],
        "8.00",
    )
    ligne = depense.lines.get()
    with pytest.raises(ValidationError, match="verrouillées"):
        save(
            [entry(ligne, "8.00", vehicle=vendue)],
            [Payment(mathis, D("8.00"))],
            "8.00",
            expense=depense,
        )


def test_status_of_a_part_is_refused_until_reopened(vendue, disque):
    with pytest.raises(ValidationError, match="verrouillées"):
        services.change_part_status(disque, Status.RECEIVED)

    reopen(vendue)
    services.change_part_status(disque, Status.RECEIVED)
    disque.refresh_from_db()
    assert disque.status == Status.RECEIVED


def test_refund_is_refused_until_reopened(vendue, disque, mathis):
    args = {"date": DATE, "amount": D("60.00"), "refunded_to": mathis}
    with pytest.raises(ValidationError, match="verrouillées"):
        services.record_refund(disque, **args)
    assert not Refund.objects.exists()

    reopen(vendue)
    services.record_refund(disque, **args)
    assert Refund.objects.filter(line=disque).exists()


def test_removing_a_refund_is_refused_until_reopened(moto, disque, mathis):
    retour = services.record_refund(disque, date=DATE, amount=D("60.00"), refunded_to=mathis)
    sell(moto, mathis)

    with pytest.raises(ValidationError, match="verrouillées"):
        services.remove_refund(retour)
    assert Refund.objects.filter(pk=retour.pk).exists()

    reopen(moto)
    services.remove_refund(retour)
    assert not Refund.objects.filter(pk=retour.pk).exists()


def test_removing_an_invoice_is_refused_until_reopened(moto, disque, mathis, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    piece_jointe = facture(disque.expense)
    sell(moto, mathis)

    with pytest.raises(ValidationError, match="verrouillées"):
        services.remove_invoice(piece_jointe)
    assert Attachment.objects.filter(pk=piece_jointe.pk).exists()

    reopen(moto)
    services.remove_invoice(piece_jointe)
    assert not Attachment.objects.filter(pk=piece_jointe.pk).exists()


def test_a_stale_vehicle_does_not_get_through_the_lock(moto, parts_category, mathis):
    """Deux onglets : le véhicule a été lu avant la vente, le service relit la base."""
    perime = Vehicle.objects.get(pk=moto.pk)
    sell(moto, mathis)
    with pytest.raises(ValidationError, match="verrouillées"):
        save(
            [LineEntry("Câble", parts_category, D("10.00"), vehicle=perime)],
            [Payment(mathis, D("10.00"))],
            "10.00",
        )


# --- Dépense sur plusieurs véhicules -------------------------------------------------


@pytest.fixture
def commande(moto, autre, parts_category, mathis):
    """Une commande partagée entre la moto et un autre véhicule."""
    depense = save(
        [
            LineEntry("Bougie", parts_category, D("10.00"), vehicle=moto, status=Status.ORDERED),
            LineEntry("Filtre", parts_category, D("20.00"), vehicle=autre, status=Status.ORDERED),
        ],
        [Payment(mathis, D("30.00"))],
        "30.00",
    )
    return depense


def test_shared_expense_is_locked_by_any_of_its_vehicles(moto, autre, commande, mathis):
    sell(moto, mathis)
    assert services.locked_vehicles(commande) == [moto]
    filtre = commande.lines.get(vehicle=autre)

    # La ligne de l'autre véhicule, encore au garage, est verrouillée aussi.
    with pytest.raises(ValidationError, match="verrouillées"):
        services.change_part_status(filtre, Status.RECEIVED)
    with pytest.raises(ValidationError, match="verrouillées"):
        services.record_refund(filtre, date=DATE, amount=D("20.00"), refunded_to=mathis)

    reopen(moto)
    assert services.locked_vehicles(commande) == []
    services.change_part_status(filtre, Status.RECEIVED)


def test_shared_expense_stays_locked_while_one_vehicle_is_not_reopened(
    moto, autre, commande, mathis
):
    sell(moto, mathis)
    sell(autre, mathis)
    reopen(moto)
    assert services.locked_vehicles(commande) == [autre]

    (premiere, seconde) = sorted(
        services.line_rows(services.paid_lines().filter(expense=commande)),
        key=lambda rang: rang["line"].pk,
    )
    assert premiere["next_status"] is None and premiere["status_choices"] == []
    assert seconde["next_status"] is None and seconde["status_choices"] == []


def test_message_names_every_locked_vehicle(moto, autre, commande, mathis):
    sell(moto, mathis)
    sell(autre, mathis)
    refus = services.expense_lock_refusal(commande)
    assert moto.display_name in refus and autre.display_name in refus
    assert "Rouvre leurs fiches" in refus


def test_a_deleted_line_no_longer_locks_the_expense(moto, autre, commande, mathis):
    commande.lines.get(vehicle=moto).soft_delete()
    sell(moto, mathis)
    assert services.locked_vehicles(commande) == []


# --- Revente ------------------------------------------------------------------------


def test_cancelled_sale_unlocks_and_a_new_sale_locks_again(vendue, disque, mathis):
    vente = sale_services.active_sale(vendue)
    reopen(vendue)
    sale_services.cancel_sale(vente, None, mathis)
    vendue.refresh_from_db()
    assert vendue.reopened_at is None
    assert not vendue.expenses_locked
    services.change_part_status(disque, Status.RECEIVED)

    sell(vendue, mathis)
    assert vendue.expenses_locked
    with pytest.raises(ValidationError, match="verrouillées"):
        services.change_part_status(disque, Status.FITTED)


# --- Pièces à commander d'un véhicule vendu ou abandonné -------------------------------


def test_parts_leave_the_list_when_sold_and_come_back_when_the_sale_is_cancelled(moto, mathis):
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    garage = services.add_part_to_order(label="Graisse")
    assert set(services.parts_to_order()) == {piece, garage}

    vente = sell(moto, mathis)
    assert list(services.parts_to_order()) == [garage]  # les pièces du garage restent
    assert ExpenseLine.objects.filter(pk=piece.pk).exists()

    sale_services.cancel_sale(vente, None, mathis)
    assert set(services.parts_to_order()) == {piece, garage}


def test_parts_leave_the_list_when_abandoned(moto, mathis):
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)
    assert not services.parts_to_order().exists()
    vehicle_services.change_status(moto, Vehicle.Status.IN_REPAIR, mathis)
    assert list(services.parts_to_order()) == [piece]


def test_no_part_to_order_on_a_sold_vehicle_even_reopened(vendue):
    """Décision D50 : rouvrir sert à corriger ce qui est payé, pas à préparer des achats."""
    with pytest.raises(ValidationError, match="vendu"):
        services.add_part_to_order(label="Chaîne", vehicle=vendue)
    reopen(vendue)
    with pytest.raises(ValidationError, match="vendu"):
        services.add_part_to_order(label="Chaîne", vehicle=vendue)


def test_no_part_to_order_on_an_abandoned_vehicle(moto, mathis):
    vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)
    with pytest.raises(ValidationError, match="Change d'abord son statut"):
        services.add_part_to_order(label="Chaîne", vehicle=moto)


def test_adding_several_parts_is_all_or_nothing(vendue, autre):
    with pytest.raises(ValidationError):
        services.add_parts_to_order(
            [{"label": "Chaîne", "vehicle": autre}, {"label": "Pignon", "vehicle": vendue}]
        )
    assert not services.noted_parts().exists()


def test_update_and_removal_of_a_part_are_refused_on_a_locked_vehicle(moto, mathis):
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    sell(moto, mathis)
    champs = {
        "label": "Chaîne",
        "reference": "",
        "quantity": D("1"),
        "unit_price": D("0"),
        "vehicle": None,
    }
    with pytest.raises(ValidationError, match="verrouillées"):
        services.update_part_to_order(piece, **champs)
    with pytest.raises(ValidationError, match="verrouillées"):
        services.remove_part_to_order(piece)

    reopen(moto)
    services.remove_part_to_order(piece)
    assert not services.noted_parts().exists()


def test_a_part_cannot_be_moved_onto_a_sold_vehicle(vendue, autre):
    piece = services.add_part_to_order(label="Chaîne", vehicle=autre)
    with pytest.raises(ValidationError, match="vendu"):
        services.update_part_to_order(
            piece,
            label="Chaîne",
            reference="",
            quantity=D("1"),
            unit_price=D("0"),
            vehicle=vendue,
        )


def test_sidebar_does_not_count_the_parts_of_a_sold_vehicle(as_mathis, moto, mathis):
    services.add_part_to_order(label="Chaîne", vehicle=moto)
    sell(moto, mathis)
    page = as_mathis.get("/")
    assert not page.context["sidebar_parts_to_order"]


# --- Écrans ------------------------------------------------------------------------


def test_vehicle_sheet_when_locked(as_mathis, vendue, disque):
    page = as_mathis.get(vendue.get_absolute_url()).content.decode()
    assert "Dépenses verrouillées" in page
    assert "Rouvrir la fiche" in page
    assert "Tu pourras corriger les dépenses. N'oublie pas de verrouiller la fiche" in page
    assert "Verrouiller la fiche" not in page
    assert "Ajouter une dépense" not in page
    assert "titre-a-commander" not in page
    assert "Marquer reçue" not in page


def test_vehicle_sheet_when_reopened(as_mathis, vendue, disque):
    reopen(vendue)
    page = as_mathis.get(vendue.get_absolute_url()).content.decode()
    assert "Fiche rouverte le" in page
    assert "Facture oubliée" in page
    assert "Verrouiller la fiche" in page
    assert "Ajouter une dépense" in page
    assert "Marquer reçue" in page
    assert "titre-a-commander" not in page  # D50 : pas de nouvelle pièce, même rouverte
    assert page.count("btn-primary") == 1


def test_reopened_sheet_stays_in_sold_status_with_its_sale_actions(as_mathis, vendue):
    reopen(vendue)
    assert vendue.status == Vehicle.Status.SOLD
    assert not vendue.status_changes.filter(from_status=Vehicle.Status.SOLD).exists()
    page = as_mathis.get(vendue.get_absolute_url()).content.decode()
    assert "Modifier la vente" in page
    assert "Annuler la vente" in page


def test_read_only_sheet_shows_the_lock_without_any_action(as_mathis, vendue, settings):
    settings.READ_ONLY = True
    page = as_mathis.get(vendue.get_absolute_url()).content.decode()
    assert "Dépenses verrouillées" in page
    assert "Rouvrir la fiche" not in page


def test_expense_page_when_locked(as_mathis, vendue, disque, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    facture(disque.expense)
    page = as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    assert "Dépense verrouillée" in page
    assert vendue.get_absolute_url() in page
    assert "Modifier la dépense" not in page
    assert "Enregistrer un retour" not in page
    assert "Mettre f.pdf à la corbeille" not in page


def test_expense_page_when_reopened(as_mathis, vendue, disque):
    reopen(vendue)
    page = as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    assert "Dépense verrouillée" not in page
    assert "Modifier la dépense" in page
    assert "Enregistrer un retour" in page


def test_expense_page_of_a_locked_expense_keeps_its_refunds_visible(
    as_mathis, moto, disque, mathis
):
    services.record_refund(disque, date=DATE, amount=D("20.00"), refunded_to=mathis)
    sell(moto, mathis)
    page = as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    assert "remboursés à" in page
    assert "Corriger" not in page


def test_edit_form_of_a_locked_expense_sends_back_with_a_message(as_mathis, vendue, disque):
    url = f"/depenses/{disque.expense_id}/modifier/"
    for reponse in (as_mathis.get(url), as_mathis.post(url, {})):
        assert reponse.status_code == 302
        assert reponse.url == disque.expense.get_absolute_url()
    assert "Rouvre sa fiche" in messages_of(reponse)[0]


def test_new_expense_form_opens_on_a_reopened_vehicle(as_mathis, vendue):
    reopen(vendue)
    page = as_mathis.get(f"/depenses/ajouter/?vehicule={vendue.number}")
    assert page.status_code == 200
    (ligne,) = page.context["formset"].forms
    assert vendue in ligne.fields["vehicle"].queryset


def test_refund_form_of_a_locked_expense_sends_back_with_a_message(as_mathis, vendue, disque):
    reponse = as_mathis.get(f"/depenses/lignes/{disque.pk}/retour/")
    assert reponse.status_code == 302
    assert "Rouvre sa fiche" in messages_of(reponse)[0]


def test_stale_tab_gets_a_message_when_removing_an_invoice(
    as_mathis, moto, disque, mathis, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    piece_jointe = facture(disque.expense)
    sell(moto, mathis)
    reponse = as_mathis.post(f"/depenses/{disque.expense_id}/factures/{piece_jointe.pk}/corbeille/")
    assert reponse.status_code == 302
    assert "Rouvre sa fiche" in messages_of(reponse)[0]
    assert Attachment.objects.filter(pk=piece_jointe.pk).exists()


def test_stale_tab_gets_a_message_when_removing_a_refund(as_mathis, moto, disque, mathis):
    retour = services.record_refund(disque, date=DATE, amount=D("20.00"), refunded_to=mathis)
    sell(moto, mathis)
    reponse = as_mathis.post(f"/depenses/retours/{retour.pk}/corbeille/")
    assert reponse.status_code == 302
    assert "Rouvre sa fiche" in messages_of(reponse)[0]
    assert Refund.objects.filter(pk=retour.pk).exists()


def test_stale_tab_gets_a_message_on_a_part_of_a_sold_vehicle(as_mathis, moto, mathis):
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    sell(moto, mathis)

    reponse = as_mathis.post(f"/pieces-a-commander/{piece.pk}/corbeille/")
    assert reponse.status_code == 302
    assert "Rouvre sa fiche" in messages_of(reponse)[0]

    reponse = as_mathis.get(f"/pieces-a-commander/{piece.pk}/modifier/")
    assert reponse.status_code == 302
    assert any("vendu" in m for m in messages_of(reponse))
    assert services.noted_parts().filter(pk=piece.pk).exists()


def test_status_button_of_a_stale_tab_gets_a_message(as_mathis, vendue, disque):
    reponse = as_mathis.post(f"/depenses/lignes/{disque.pk}/statut/", {"status": "RECEIVED"})
    assert reponse.status_code == 302
    assert "Rouvre sa fiche" in messages_of(reponse)[0]
    disque.refresh_from_db()
    assert disque.status == Status.ORDERED


def test_part_form_refuses_a_sold_vehicle(as_mathis, vendue):
    url = f"/pieces-a-commander/ajouter/?vehicule={vendue.number}"
    reponse = as_mathis.get(url)
    assert reponse.status_code == 302
    assert reponse.url == vendue.get_absolute_url()
    assert "rouvre sa fiche et ajoute une dépense" in messages_of(reponse)[0]


def test_part_form_post_is_refused_too(as_mathis, vendue, parts_category):
    url = f"/pieces-a-commander/ajouter/?vehicule={vendue.number}"
    reponse = as_mathis.post(url, {"lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0"})
    assert reponse.status_code == 302
    assert not services.noted_parts().exists()


def test_refund_edit_of_a_locked_expense_sends_back_with_a_message(as_mathis, moto, disque, mathis):
    retour = services.record_refund(disque, date=DATE, amount=D("20.00"), refunded_to=mathis)
    sell(moto, mathis)
    reponse = as_mathis.get(f"/depenses/retours/{retour.pk}/modifier/")
    assert reponse.status_code == 302
    assert "Rouvre sa fiche" in messages_of(reponse)[0]


def test_status_button_with_htmx_reloads_the_sheet_to_show_the_message(as_mathis, vendue, disque):
    reponse = as_mathis.post(
        f"/depenses/lignes/{disque.pk}/statut/",
        {"status": "RECEIVED"},
        headers={"HX-Request": "true"},
    )
    assert reponse.headers["HX-Redirect"] == vendue.get_absolute_url()
    disque.refresh_from_db()
    assert disque.status == Status.ORDERED


def test_shared_expense_is_marked_locked_on_the_sheet_still_at_the_garage(
    as_mathis, moto, autre, commande, mathis
):
    sell(moto, mathis)
    assert "Verrouillée" in as_mathis.get(autre.get_absolute_url()).content.decode()
    assert "Verrouillée" not in as_mathis.get(moto.get_absolute_url()).content.decode()


def test_read_only_expense_page_does_not_ask_to_reopen(as_mathis, vendue, disque, settings):
    settings.READ_ONLY = True
    page = as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    assert "Dépense verrouillée" in page
    assert "rouvre la fiche" not in page


# --- Saisie sur une fiche rouverte ----------------------------------------------------


def test_new_expense_is_saved_on_a_reopened_vehicle(as_mathis, vendue, parts_category, mathis):
    reopen(vendue)
    donnees = form_data(
        [line(parts_category, label="Câble", price="10", vehicle=vendue)], payer=str(mathis.pk)
    )
    reponse = as_mathis.post(f"/depenses/ajouter/?vehicule={vendue.number}", donnees)
    assert reponse.status_code == 302
    assert ExpenseLine.objects.get(label="Câble").vehicle == vendue


def test_line_added_to_an_expense_stays_on_the_reopened_vehicle(
    as_mathis, vendue, disque, parts_category, mathis
):
    """Une ligne ajoutée à « Modifier la dépense » doit pouvoir rester sur le véhicule rouvert,
    sinon elle part sur le garage et le coût du véhicule devient faux sans alerte."""
    reopen(vendue)
    url = f"/depenses/{disque.expense_id}/modifier/"
    page = as_mathis.get(url)
    assert vendue in page.context["formset"].empty_form.fields["vehicle"].queryset

    existante = line(parts_category, price="60", vehicle=vendue, line_id=disque.pk)
    nouvelle = line(parts_category, label="Câble", price="10", vehicle=vendue)
    donnees = form_data([existante, nouvelle], payer=str(mathis.pk), initial_forms=1)
    reponse = as_mathis.post(url, donnees)
    assert reponse.status_code == 302
    assert ExpenseLine.objects.get(label="Câble").vehicle == vendue


def test_line_added_to_an_expense_stays_on_an_abandoned_vehicle(as_mathis, moto, disque, mathis):
    vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)
    page = as_mathis.get(f"/depenses/{disque.expense_id}/modifier/")
    assert moto in page.context["formset"].empty_form.fields["vehicle"].queryset


def test_preview_counts_the_lines_of_a_reopened_vehicle(as_mathis, vendue, parts_category, mathis):
    reopen(vendue)
    donnees = form_data(
        [line(parts_category, price="90", vehicle=vendue)], total="100", payer=str(mathis.pk)
    )
    page = as_mathis.post("/depenses/apercu/", donnees).content.decode()
    assert "90,00" in page and "10,00" in page


def test_preview_counts_the_lines_of_the_abandoned_vehicle_of_the_sheet(
    as_mathis, moto, parts_category, mathis
):
    """Sans le véhicule de la fiche, sa ligne serait ignorée : frais de port 100 €."""
    vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)
    page = as_mathis.get(f"/depenses/ajouter/?vehicule={moto.number}").content.decode()
    assert f"/depenses/apercu/?vehicule={moto.number}" in page

    donnees = form_data(
        [line(parts_category, price="90", vehicle=moto)], total="100", payer=str(mathis.pk)
    )
    reponse = as_mathis.post(f"/depenses/apercu/?vehicule={moto.number}", donnees)
    page = reponse.content.decode()
    assert "90,00" in page and "10,00" in page


def test_preview_of_an_expense_counts_a_line_added_on_the_reopened_vehicle(
    as_mathis, vendue, disque, parts_category, mathis
):
    reopen(vendue)
    existante = line(parts_category, price="60", vehicle=vendue, line_id=disque.pk)
    nouvelle = line(parts_category, label="Câble", price="30", vehicle=vendue)
    donnees = form_data([existante, nouvelle], total="100", payer=str(mathis.pk), initial_forms=1)
    reponse = as_mathis.post(f"/depenses/apercu/?depense={disque.expense_id}", donnees)
    assert "90,00" in reponse.content.decode()


def test_preview_equals_what_is_saved_with_several_vehicles(
    as_mathis, moto, autre, parts_category, mathis
):
    """Trois lignes sur deux véhicules et le garage, avec des frais de port à répartir : les
    montants de l'aperçu sont ceux enregistrés, au centime."""
    sell(moto, mathis)
    reopen(moto)
    lignes = [
        line(parts_category, label="Câble", price="10", vehicle=moto),
        line(parts_category, label="Pignon", price="20", vehicle=autre),
        line(parts_category, label="Graisse", price="33"),
    ]
    donnees = form_data(lignes, total="70", payer=str(mathis.pk))
    apercu = as_mathis.post("/depenses/apercu/", donnees).content.decode()

    assert as_mathis.post("/depenses/ajouter/", donnees).status_code == 302
    depense = ExpenseLine.objects.get(label="Câble").expense
    entrees = queries.load_expenses([depense.pk])
    couts = queries.line_costs(entrees)
    assert sum(cout.allocated for cout in couts.values()) == D("70.00")
    for ligne in depense.lines.all():
        assert euros(couts[ligne.pk].allocated) in apercu
    assert {ligne.vehicle for ligne in depense.lines.all()} == {moto, autre, None}


# --- Véhicules proposés sur une dépense -------------------------------------------------


def choices_of(page) -> set:
    return set(page.context["formset"].empty_form.fields["vehicle"].queryset)


def test_a_reopened_vehicle_is_offered_on_any_expense(as_mathis, vendue, autre, mathis):
    assert vendue not in choices_of(as_mathis.get("/depenses/ajouter/"))
    reopen(vendue)
    assert vendue in choices_of(as_mathis.get("/depenses/ajouter/"))

    vehicle_services.lock_vehicle(vendue)
    assert choices_of(as_mathis.get("/depenses/ajouter/")) == {autre}


def test_an_invoice_saved_on_the_garage_can_be_moved_to_a_reopened_vehicle(
    as_mathis, vendue, parts_category, mathis
):
    """Une facture saisie sur le garage par erreur, puis corrigée par une fiche rouverte."""
    depense = save(
        [LineEntry("Câble", parts_category, D("10.00"), status=Status.ORDERED)],
        [Payment(mathis, D("10.00"))],
        "10.00",
    )
    ligne = depense.lines.get()
    reopen(vendue)
    url = f"/depenses/{depense.pk}/modifier/"
    assert vendue in choices_of(as_mathis.get(url))

    donnees = form_data(
        [line(parts_category, label="Câble", price="10", vehicle=vendue, line_id=ligne.pk)],
        payer=str(mathis.pk),
        initial_forms=1,
    )
    assert as_mathis.post(url, donnees).status_code == 302
    ligne.refresh_from_db()
    assert ligne.vehicle == vendue


def test_a_line_sent_to_the_garage_can_come_back_on_the_reopened_vehicle(
    as_mathis, vendue, disque, parts_category, mathis
):
    reopen(vendue)
    url = f"/depenses/{disque.expense_id}/modifier/"
    for vehicule in (None, vendue):
        donnees = form_data(
            [line(parts_category, price="60", vehicle=vehicule, line_id=disque.pk)],
            payer=str(mathis.pk),
            initial_forms=1,
        )
        assert as_mathis.post(url, donnees).status_code == 302
        disque.refresh_from_db()
        assert disque.vehicle == vehicule


def test_expense_on_several_vehicles_offers_only_what_it_should(
    as_mathis, moto, autre, make_vehicle, parts_category, mathis
):
    """Rouvert et abandonné proposés, vendu sans réouverture absent, même forcé à l'envoi."""
    lignes = [
        LineEntry("Câble", parts_category, D("10.00"), vehicle=moto, status=Status.ORDERED),
        LineEntry("Pignon", parts_category, D("20.00"), vehicle=autre, status=Status.ORDERED),
    ]
    depense = save(lignes, [Payment(mathis, D("30.00"))], "30.00")
    ferme = buy(make_vehicle(brand="KTM", model="125"), mathis)
    sell(ferme, mathis)
    sell(moto, mathis)
    reopen(moto)
    vehicle_services.change_status(autre, Vehicle.Status.ABANDONED, mathis)

    url = f"/depenses/{depense.pk}/modifier/"
    assert choices_of(as_mathis.get(url)) == {moto, autre}

    cable, pignon = depense.lines.order_by("id")
    donnees = form_data(
        [
            line(parts_category, label="Câble", price="10", vehicle=moto, line_id=cable.pk),
            line(parts_category, label="Pignon", price="20", vehicle=ferme, line_id=pignon.pk),
        ],
        payer=str(mathis.pk),
        initial_forms=2,
    )
    reponse = as_mathis.post(url, donnees)
    assert reponse.status_code == 200
    assert "rouvre sa fiche, ou choisis" in reponse.content.decode()
    pignon.refresh_from_db()
    assert pignon.vehicle == autre


def test_a_part_to_order_never_offers_a_reopened_vehicle(as_mathis, vendue):
    reopen(vendue)
    page = as_mathis.get("/pieces-a-commander/ajouter/")
    assert vendue not in page.context["formset"].empty_form.fields["vehicle"].queryset


# --- Véhicule sorti de la liste pendant la saisie ---------------------------------------


@pytest.mark.parametrize(
    ("sortie", "attendu"),
    [
        ("vendu", "est vendu et ses dépenses sont verrouillées"),
        ("abandonne", "est abandonné"),
        ("corbeille", "est à la corbeille"),
    ],
)
def test_a_vehicle_that_left_the_list_is_refused_and_stays_selected(
    as_mathis, moto, parts_category, mathis, sortie, attendu
):
    """Le refus dit pourquoi, et un second clic ne met pas la ligne sur le garage."""
    assert moto in choices_of(as_mathis.get("/depenses/ajouter/"))
    if sortie == "vendu":
        sell(moto, mathis)
    elif sortie == "abandonne":
        vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)
    else:
        moto.soft_delete()

    donnees = form_data([line(parts_category, label="Câble", vehicle=moto)], payer=str(mathis.pk))
    for _ in range(2):
        reponse = as_mathis.post("/depenses/ajouter/", donnees)
        assert reponse.status_code == 200
        page = reponse.content.decode()
        assert attendu in page
        assert "est enregistré" in page
        champ = reponse.context["formset"].forms[0]["vehicle"]
        assert f'<option value="{moto.pk}" selected>' in str(champ)
    assert not ExpenseLine.objects.filter(label="Câble").exists()


def test_the_garage_stays_available_when_someone_chooses_it(
    as_mathis, moto, parts_category, mathis
):
    """Après le refus, choisir « Garage » reste possible : c'est alors un vrai choix."""
    sell(moto, mathis)
    donnees = form_data([line(parts_category, label="Câble")], payer=str(mathis.pk))
    assert as_mathis.post("/depenses/ajouter/", donnees).status_code == 302
    assert ExpenseLine.objects.get(label="Câble").vehicle is None


def test_reopening_the_sheet_lets_the_refused_line_through(as_mathis, moto, parts_category, mathis):
    sell(moto, mathis)
    donnees = form_data([line(parts_category, label="Câble", vehicle=moto)], payer=str(mathis.pk))
    assert as_mathis.post("/depenses/ajouter/", donnees).status_code == 200
    reopen(moto)
    assert as_mathis.post("/depenses/ajouter/", donnees).status_code == 302
    assert ExpenseLine.objects.get(label="Câble").vehicle == moto


def test_an_unknown_vehicle_is_refused_with_a_clear_message(as_mathis, parts_category, mathis):
    donnees = form_data(
        [line(parts_category, label="Câble", vehicle=None) | {"vehicle": "999999"}],
        payer=str(mathis.pk),
    )
    reponse = as_mathis.post("/depenses/ajouter/", donnees)
    assert reponse.status_code == 200
    assert "Ce véhicule n" in reponse.content.decode()
    assert not ExpenseLine.objects.filter(label="Câble").exists()


# --- Commande de pièces sorties de la liste ---------------------------------------------


def order_url(*parts) -> str:
    return "/depenses/ajouter/?" + "&".join(f"pieces={pk}" for pk in parts)


def order_data(piece, parts_category, mathis, price="10"):
    return form_data(
        [line(parts_category, label=piece.label, price=price, line_id=piece.pk)],
        payer=str(mathis.pk),
    )


def test_stale_order_of_a_part_of_a_sold_vehicle_says_why(as_mathis, moto, mathis):
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    sell(moto, mathis)
    reponse = as_mathis.get(order_url(piece.pk))
    assert reponse.status_code == 302
    assert reponse.url == "/pieces-a-commander/"
    (message,) = messages_of(reponse)
    assert "« Chaîne » n'est plus à commander" in message
    assert f"{moto.display_name} est vendu" in message
    assert "rouvre sa fiche et ajoute une dépense" in message
    assert "Rien n'est enregistré" in message


def test_sending_a_complete_order_of_a_stale_part_saves_nothing(
    as_mathis, moto, parts_category, mathis
):
    """L'envoi est contrôlé par la vue, pas seulement l'ouverture du formulaire : on revient à
    la liste, sans dépense et sans toucher à la pièce."""
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    sell(moto, mathis)
    avant = Expense.objects.count()
    reponse = as_mathis.post(order_url(piece.pk), order_data(piece, parts_category, mathis))
    assert reponse.status_code == 302
    assert reponse.url == "/pieces-a-commander/"
    assert "« Chaîne » n'est plus à commander" in messages_of(reponse)[0]
    assert Expense.objects.count() == avant
    piece.refresh_from_db()
    assert piece.expense_id is None
    assert piece.status == Status.TO_ORDER


def test_the_service_refuses_a_stale_part_on_its_own(moto, mathis):
    """Sans passer par la vue : véhicule vendu, le verrou répond ; fiche rouverte, le contrôle
    de la commande."""
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    sell(moto, mathis)
    ligne = entry(piece, "10.00", status=Status.ORDERED)
    with pytest.raises(ValidationError, match="verrouillées"):
        save([ligne], [Payment(mathis, D("10"))], "10.00")
    reopen(moto)
    with pytest.raises(ValidationError, match="« Chaîne » n'est plus à commander"):
        save([ligne], [Payment(mathis, D("10"))], "10.00")
    piece.refresh_from_db()
    assert piece.expense_id is None


def test_ordering_a_part_of_an_abandoned_vehicle_says_what_to_do(moto, mathis):
    piece = services.add_part_to_order(label="Chaîne", vehicle=moto)
    vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)
    with pytest.raises(ValidationError, match="Change d'abord son statut"):
        save([entry(piece, "10.00", status=Status.ORDERED)], [Payment(mathis, D("10"))], "10.00")


def retired(piece, moto, mathis):
    piece.soft_delete()


def ordered(piece, moto, mathis):
    save([entry(piece, "10.00", status=Status.ORDERED)], [Payment(mathis, D("10"))], "10.00")


def sold(piece, moto, mathis):
    sell(moto, mathis)


def sold_and_reopened(piece, moto, mathis):
    sell(moto, mathis)
    reopen(moto)


def abandoned(piece, moto, mathis):
    vehicle_services.change_status(moto, Vehicle.Status.ABANDONED, mathis)


@pytest.mark.parametrize(
    ("ecarter", "attendu"),
    [
        (retired, ["« Chaîne » est à la corbeille", "ajoute-la de nouveau"]),
        (ordered, ["« Chaîne » a déjà été commandée", "inutile de la commander"]),
        (sold, ["est vendu", "rouvre sa fiche et ajoute une dépense"]),
        (sold_and_reopened, ["est vendu", "Sa fiche est déjà rouverte", "ajoute une dépense"]),
        (abandoned, ["est abandonné", "Change d'abord son statut"]),
    ],
)
def test_a_mixed_order_is_stopped_with_one_message_per_cause(
    as_mathis, moto, autre, mathis, ecarter, attendu
):
    """Une pièce écartée parmi d'autres : le formulaire ne s'ouvre pas avec le reste."""
    ecartee = services.add_part_to_order(label="Chaîne", vehicle=moto)
    gardee = services.add_part_to_order(label="Pignon", vehicle=autre)
    ecarter(ecartee, moto, mathis)
    reponse = as_mathis.get(order_url(ecartee.pk, gardee.pk))
    assert reponse.status_code == 302
    assert reponse.url == "/pieces-a-commander/"
    (message,) = messages_of(reponse)
    for texte in attendu:
        assert texte in message
    assert "Rien n'est enregistré" in message
    assert "Pignon" not in message
    if ecarter is sold_and_reopened:
        assert "rouvre sa fiche" not in message


def test_order_refusal_names_every_part_set_aside(moto, autre, mathis):
    premiere = services.add_part_to_order(label="Chaîne", vehicle=moto)
    seconde = services.add_part_to_order(label="Pignon", vehicle=moto)
    seconde.soft_delete()
    sell(moto, mathis)
    refus = services.order_refusal([premiere.pk, seconde.pk, premiere.pk])
    assert refus.count("Chaîne") == 1
    assert "« Pignon » est à la corbeille" in refus
    assert refus.count("Rien n'est enregistré") == 1


@pytest.mark.parametrize("valeur", ["999999", "abc", "²", "9" * 26, "-1"])
def test_a_part_that_does_not_exist_stops_the_order(as_mathis, moto, valeur):
    gardee = services.add_part_to_order(label="Pignon", vehicle=moto)
    reponse = as_mathis.get(f"/depenses/ajouter/?pieces={gardee.pk}&pieces={valeur}")
    assert reponse.status_code == 302
    (message,) = messages_of(reponse)
    assert "Une des pièces cochées n'existe plus" in message
    assert "Rien n'est enregistré" in message


def test_a_valid_order_opens_with_every_ticked_part(as_mathis, moto, autre):
    premiere = services.add_part_to_order(label="Chaîne", vehicle=moto)
    seconde = services.add_part_to_order(label="Pignon", vehicle=autre)
    reponse = as_mathis.get(order_url(premiere.pk, seconde.pk))
    assert reponse.status_code == 200
    assert len(reponse.context["formset"].forms) == 2


def test_part_form_on_a_reopened_vehicle_does_not_ask_to_reopen_it(as_mathis, vendue):
    reopen(vendue)
    reponse = as_mathis.get(f"/pieces-a-commander/ajouter/?vehicule={vendue.number}")
    assert reponse.status_code == 302
    (message,) = messages_of(reponse)
    assert "Sa fiche est déjà rouverte" in message
    assert "rouvre sa fiche" not in message


# --- Adresses mal écrites ---------------------------------------------------------------


@pytest.mark.parametrize("valeur", ["²", "abc", "-1", "9" * 26, "9" * 5000])
def test_a_badly_written_address_is_ignored(as_mathis, parts_category, mathis, valeur):
    assert as_mathis.get(f"/depenses/ajouter/?vehicule={valeur}").status_code == 200
    assert as_mathis.get(f"/pieces-a-commander/ajouter/?vehicule={valeur}").status_code == 200
    donnees = form_data([line(parts_category, price="90")], total="100", payer=str(mathis.pk))
    for parametre in ("vehicule", "depense"):
        adresse = f"/depenses/apercu/?{parametre}={valeur}"
        assert as_mathis.post(adresse, donnees).status_code == 200


# --- Messages d'erreur ------------------------------------------------------------------


def test_an_error_message_stays_until_it_is_closed(as_mathis, vendue, disque):
    """Une erreur explique quoi faire : elle ne s'efface pas toute seule, contrairement à une
    confirmation."""
    page = as_mathis.get(f"/depenses/{disque.expense_id}/modifier/", follow=True).content.decode()
    assert "Fermer ce message" in page
    assert "setTimeout(() => open = false" not in page

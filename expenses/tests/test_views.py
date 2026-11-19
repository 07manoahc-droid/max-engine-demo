"""Tests des écrans des dépenses : ajout, modification, aperçu, fiche d'une dépense,
liste des dépenses sur la fiche véhicule."""

import datetime as dt
import html
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile

from accounting.services import Payment
from core.formatting import NBSP
from core.models import Attachment, ExpenseCategory, Supplier
from expenses import services
from expenses.models import Expense, ExpenseLine, ExpensePayment
from expenses.services import LineEntry
from vehicles import services as vehicle_services

E = f"{NBSP}€"
PDF = b"%PDF-1.7\n" + b"\x00" * 60
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 60


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def moto(make_vehicle, mathis):
    """Véhicule acheté 1 500 € par Mathis."""
    vehicule = make_vehicle(brand="GG", model="MC250", year=2010)
    vehicle_services.record_purchase(
        vehicule,
        price=D("1500"),
        date=dt.date(2026, 10, 1),
        payments=[Payment(mathis, D("1500"))],
        seller=None,
        partner=mathis,
    )
    vehicule.refresh_from_db()
    return vehicule


def form_data(lines, total="", payer="", initial_forms=0, **champs):
    """Données envoyées par le formulaire de dépense. `lines` : dictionnaires de champs."""
    donnees = {
        "date": "2026-10-12",
        "supplier_name": "",
        "order_number": "",
        "total_paid": total,
        "payer": payer,
        "notes": "",
        "lignes-TOTAL_FORMS": str(len(lines)),
        "lignes-INITIAL_FORMS": str(initial_forms),
        "lignes-MIN_NUM_FORMS": "0",
        "lignes-MAX_NUM_FORMS": "1000",
    }
    for i, ligne in enumerate(lines):
        for champ, valeur in ligne.items():
            donnees[f"lignes-{i}-{champ}"] = "" if valeur is None else str(valeur)
    donnees.update(champs)
    return donnees


def line(category, label="Disque", price="71,90", vehicle=None, quantity="1", **champs):
    return {
        "label": label,
        "reference": "",
        "quantity": quantity,
        "unit_price": price,
        "category": category.pk,
        "vehicle": vehicle.pk if vehicle else "",
        "status": "ORDERED",
        **champs,
    }


# --- Ajouter une dépense -------------------------------------------------------------


def test_add_from_a_vehicle_presets_the_vehicle_and_the_payer(as_mathis, moto, mathis):
    response = as_mathis.get(f"/depenses/ajouter/?vehicule={moto.number}")
    assert response.status_code == 200
    form, formset = response.context["form"], response.context["formset"]
    assert form["payer"].value() == str(mathis.pk)
    (ligne,) = formset.forms
    assert ligne["vehicle"].value() == moto.pk
    pieces = ExpenseCategory.objects.get(code="parts")
    assert ligne["category"].value() == pieces.pk
    # « Achat véhicule » n'est jamais proposé dans une dépense.
    codes = {c.code for c in ligne.fields["category"].queryset}
    assert ExpenseCategory.VEHICLE_PURCHASE not in codes
    assert moto.display_name in response.content.decode()


def test_add_from_home_is_a_garage_expense(as_mathis, moto):
    response = as_mathis.get("/depenses/ajouter/")
    assert response.status_code == 200
    (ligne,) = response.context["formset"].forms
    assert ligne["vehicle"].value() is None
    assert "Dépense du garage" in response.content.decode()


def test_add_from_garage_expenses_comes_back_to_the_list(as_mathis, moto, parts_category):
    """D57 : « Ajouter une dépense du garage » ramène à la liste, à l'année de la dépense."""
    page = as_mathis.get("/depenses/ajouter/?depuis=garage").content.decode()
    assert 'href="/comptes/depenses-du-garage/"' in page

    donnees = form_data([line(parts_category, price="12")], payer=response_payer(as_mathis))
    response = as_mathis.post("/depenses/ajouter/?depuis=garage", donnees)
    assert response.status_code == 302
    assert response.url == "/comptes/depenses-du-garage/?annee=2026"


def test_add_from_garage_expenses_a_vehicle_only_expense_opens_it(as_mathis, moto, parts_category):
    """Sans ligne pour le garage, la dépense n'apparaîtrait pas dans la liste : on l'ouvre."""
    donnees = form_data(
        [line(parts_category, price="12", vehicle=moto)], payer=response_payer(as_mathis)
    )
    response = as_mathis.post("/depenses/ajouter/?depuis=garage", donnees)
    assert response.url == Expense.objects.order_by("-id").first().get_absolute_url()


def test_home_and_vehicle_sheet_link_to_the_expense_form(as_mathis, moto):
    assert "/depenses/ajouter/" in as_mathis.get("/").content.decode()
    fiche = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert f"/depenses/ajouter/?vehicule={moto.number}" in fiche


def test_add_an_order_with_shipping_paid_by_both(as_mathis, moto, parts_category, mathis, axel):
    donnees = form_data(
        [
            line(parts_category, vehicle=moto),
            line(parts_category, "Filtre à huile", "8,40", vehicle=moto),
        ],
        total="86,00",
        payer="moitie",
        supplier_name="Moto Axxe",
        order_number="50017",
    )
    response = as_mathis.post(f"/depenses/ajouter/?vehicule={moto.number}", donnees)
    assert response.status_code == 302
    assert response.url == moto.get_absolute_url()
    assert "Dépense enregistrée." in messages_of(response)
    depense = Expense.objects.exclude(lines__category__code="vehicle_purchase").get()
    assert depense.total_paid == D("86.00")
    assert depense.supplier.name == "Moto Axxe"
    assert {(p.partner, p.amount) for p in depense.payments.all()} == {
        (mathis, D("43.00")),
        (axel, D("43.00")),
    }

    fiche = as_mathis.get(moto.get_absolute_url()).content.decode()
    # Coût réparti, frais de port compris : 71,90 → 77,00 € et 8,40 → 9,00 €.
    assert f"77,00{E}" in fiche
    assert f"9,00{E}" in fiche
    assert "Filtre à huile" in fiche


def test_empty_amount_paid_is_the_lines_sum(as_mathis, moto, parts_category):
    donnees = form_data(
        [line(parts_category, price="40", vehicle=moto, quantity="2")],
        payer=response_payer(as_mathis),
    )
    response = as_mathis.post("/depenses/ajouter/", donnees)
    assert response.status_code == 302
    assert Expense.objects.order_by("-id").first().total_paid == D("80.00")


def response_payer(client) -> str:
    """Le payeur prérempli (l'associé courant), tel que le formulaire le propose."""
    return client.get("/depenses/ajouter/").context["form"]["payer"].value()


def test_invalid_expense_shows_the_errors_and_saves_nothing(as_mathis, moto, parts_category):
    avant = Expense.objects.count()
    donnees = form_data(
        [line(parts_category, label="", vehicle=moto)],
        total="86,00",
        payer=response_payer(as_mathis),
    )
    response = as_mathis.post("/depenses/ajouter/", donnees)
    assert response.status_code == 200
    assert response.context["formset"].forms[0].errors["label"]
    assert Expense.objects.count() == avant


def test_no_line_is_refused(as_mathis, moto):
    donnees = form_data([], total="10", payer=response_payer(as_mathis))
    response = as_mathis.post("/depenses/ajouter/", donnees)
    assert response.status_code == 200
    assert "Ajoute au moins une ligne." in response.context["formset"].non_form_errors()


def test_payments_that_do_not_add_up_are_shown_under_the_amounts(
    as_mathis, moto, parts_category, mathis, axel
):
    donnees = form_data(
        [line(parts_category, price="74", vehicle=moto)],
        total="74",
        payer="montants",
        **{f"amount_{mathis.pk}": "40", f"amount_{axel.pk}": "30"},
    )
    response = as_mathis.post("/depenses/ajouter/", donnees)
    assert response.status_code == 200
    assert "Corrige l'un des montants" in response.context["form"].errors["payer"][0]


def test_personal_purchase_with_a_vehicle_is_refused_on_the_checkbox(
    as_mathis, moto, parts_category
):
    donnees = form_data(
        [line(parts_category, price="74", vehicle=moto)],
        total="74",
        payer=response_payer(as_mathis),
        is_personal="on",
    )
    response = as_mathis.post("/depenses/ajouter/", donnees)
    assert response.status_code == 200
    assert "achat personnel" in str(response.context["form"].errors["is_personal"])


def test_supplier_suggestions_are_offered(as_mathis, moto):
    Supplier.objects.create(name="Moto Axxe")
    page = as_mathis.get("/depenses/ajouter/").content.decode()
    assert 'list="fournisseurs"' in page
    assert '<option value="Moto Axxe">' in page


# --- Factures ------------------------------------------------------------------------


def test_invoice_is_attached(as_mathis, moto, parts_category):
    donnees = form_data(
        [line(parts_category, price="74", vehicle=moto)],
        total="74",
        payer=response_payer(as_mathis),
    )
    donnees["factures"] = SimpleUploadedFile("facture.pdf", PDF)
    response = as_mathis.post(f"/depenses/ajouter/?vehicule={moto.number}", donnees)
    assert response.status_code == 302
    facture = Attachment.objects.get(kind=Attachment.Kind.INVOICE)
    assert facture.vehicle == moto
    assert facture.expense.lines.get().label == "Disque"


def test_refused_invoice_saves_nothing_and_asks_to_choose_again(as_mathis, moto, parts_category):
    avant = Expense.objects.count()
    donnees = form_data(
        [line(parts_category, price="74", vehicle=moto)],
        total="74",
        payer=response_payer(as_mathis),
    )
    donnees["factures"] = SimpleUploadedFile("ticket.heic", HEIC)
    response = as_mathis.post("/depenses/ajouter/", donnees)
    assert response.status_code == 200
    assert [nom for nom, _ in response.context["refused_invoices"]] == ["ticket.heic"]
    assert "choisis de nouveau tes factures" in response.content.decode()
    assert Expense.objects.count() == avant


# --- Aperçu pendant la saisie ------------------------------------------------------


def test_preview_shows_shipping_and_allocated_costs(as_mathis, moto, parts_category):
    donnees = form_data(
        [line(parts_category, vehicle=moto), line(parts_category, "Filtre", "8,40", vehicle=moto)],
        total="86,00",
    )
    response = as_mathis.post("/depenses/apercu/", donnees, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    page = response.content.decode()
    assert f"5,70{E}" in page  # frais de port
    assert f"77,00{E}" in page
    assert "<html" not in page


def test_preview_ignores_incomplete_and_removed_lines(as_mathis, parts_category):
    donnees = form_data(
        [
            line(parts_category, price="40"),
            line(parts_category, label=""),
            line(parts_category, price="999", DELETE="on"),
        ]
    )
    page = as_mathis.post("/depenses/apercu/", donnees).content.decode()
    assert "somme des lignes" in page
    assert f"40,00{E}" in page
    assert "999" not in page


# --- Fiche d'une dépense et modification ---------------------------------------------


@pytest.fixture
def order(moto, parts_category, mathis, axel):
    return services.save_expense(
        None,
        date=dt.date(2026, 10, 12),
        supplier_name="Moto Axxe",
        order_number="50017",
        total_paid=D("86.00"),
        is_personal=False,
        notes="",
        lines=[
            LineEntry("Disque", parts_category, D("71.90"), vehicle=moto),
            LineEntry("Filtre", parts_category, D("8.40"), vehicle=moto),
        ],
        payments=[Payment(mathis, D("43.00")), Payment(axel, D("43.00"))],
        invoices=[SimpleUploadedFile("facture.pdf", PDF)],
    )


def test_expense_detail(as_mathis, order, moto):
    response = as_mathis.get(order.get_absolute_url())
    assert response.status_code == 200
    page = response.content.decode()
    assert "Dépense du 12/10/2026" in page
    assert "Moto Axxe" in page
    assert f"5,70{E}" in page
    assert f"Mathis · 43,00{E}" in page
    assert "facture.pdf" in page
    assert moto.get_absolute_url() in page


def test_edit_form_is_prefilled(as_mathis, order):
    response = as_mathis.get(f"/depenses/{order.pk}/modifier/")
    assert response.status_code == 200
    form, formset = response.context["form"], response.context["formset"]
    assert form["supplier_name"].value() == "Moto Axxe"
    assert form["payer"].value() == "moitie"
    assert [f["label"].value() for f in formset.forms] == ["Disque", "Filtre"]


def paid_expense(category, vehicle, payments):
    total = sum((p.amount for p in payments), D("0"))
    return services.save_expense(
        None,
        date=dt.date(2026, 10, 12),
        supplier_name="",
        order_number="",
        total_paid=total,
        is_personal=False,
        notes="",
        lines=[LineEntry("Disque", category, total, vehicle=vehicle)],
        payments=payments,
    )


@pytest.mark.parametrize(
    ("paid_by_mathis", "paid_by_axel", "choice"),
    [
        ("10.01", None, "mathis"),
        (None, "10.01", "axel"),
        ("5.01", "5.00", "moitie"),
        ("5.00", "5.01", "montants"),
        ("3.00", "7.00", "montants"),
    ],
)
def test_edit_form_recognises_who_paid(
    as_mathis, moto, parts_category, mathis, axel, paid_by_mathis, paid_by_axel, choice
):
    """Le centime impair va au premier associé : 5,01 / 5,00 est bien un partage égal."""
    paiements = [
        Payment(associe, D(montant))
        for associe, montant in ((mathis, paid_by_mathis), (axel, paid_by_axel))
        if montant
    ]
    depense = paid_expense(parts_category, moto, paiements)
    attendu = {"mathis": str(mathis.pk), "axel": str(axel.pk)}.get(choice, choice)

    form = as_mathis.get(f"/depenses/{depense.pk}/modifier/").context["form"]

    assert form["payer"].value() == attendu
    if choice == "montants":
        assert form[f"amount_{mathis.pk}"].value() == D(paid_by_mathis)
        assert form[f"amount_{axel.pk}"].value() == D(paid_by_axel)


def test_edit_form_opens_when_a_payer_left(as_mathis, moto, parts_category, axel):
    depense = paid_expense(parts_category, moto, [Payment(axel, D("10.00"))])
    axel.is_active = False
    axel.save()
    response = as_mathis.get(f"/depenses/{depense.pk}/modifier/")
    assert response.status_code == 200
    assert response.context["form"]["payer"].value() == str(axel.pk)


def test_expense_form_asks_who_paid(as_mathis):
    assert "Qui a payé ?" in html.unescape(as_mathis.get("/depenses/ajouter/").content.decode())


def test_edit_removes_a_line_and_a_payer(as_mathis, order, parts_category, moto, mathis, axel):
    disque, filtre = order.lines.order_by("id")
    donnees = form_data(
        [
            line(parts_category, vehicle=moto, line_id=disque.pk),
            line(parts_category, "Filtre", "8,40", vehicle=moto, line_id=filtre.pk, DELETE="on"),
        ],
        total="71,90",
        payer=str(mathis.pk),
        initial_forms=2,
    )
    response = as_mathis.post(f"/depenses/{order.pk}/modifier/", donnees)
    assert response.status_code == 302
    assert response.url == order.get_absolute_url()
    assert list(order.lines.values_list("label", flat=True)) == ["Disque"]
    assert ExpenseLine.all_objects.get(pk=filtre.pk).is_deleted
    assert [p.partner for p in order.payments.all()] == [mathis]
    assert ExpensePayment.all_objects.get(expense=order, partner=axel).is_deleted


def test_edit_purchase_expense_keeps_the_purchase_line_locked(as_mathis, moto):
    depense = vehicle_services.purchase_lines(moto).get().expense
    response = as_mathis.get(f"/depenses/{depense.pk}/modifier/")
    (ligne,) = response.context["formset"].forms
    assert ligne.fields["category"].disabled and ligne.fields["vehicle"].disabled
    assert "Retirer la ligne" not in response.content.decode().split('x-ref="modele"')[0]


def test_invoice_to_the_bin(as_mathis, order):
    facture = order.attachments.get()
    response = as_mathis.post(f"/depenses/{order.pk}/factures/{facture.pk}/corbeille/")
    assert response.status_code == 302
    assert "La facture est dans la corbeille." in messages_of(response)
    assert Attachment.all_objects.get(pk=facture.pk).is_deleted


def test_unknown_expense_gives_not_found(as_mathis, db):
    assert as_mathis.get("/depenses/999/").status_code == 404


# --- Fiche véhicule ---------------------------------------------------------------


def test_vehicle_sheet_lists_its_expenses_with_payers_and_invoice(as_mathis, order, moto):
    page = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert "Achat du véhicule" in page
    assert "Disque" in page
    assert "Commandé" in page
    assert "facture" in page
    assert order.get_absolute_url() in page


# --- Cas relevés à la relecture ------------------------------------------------------


def test_add_from_an_abandoned_vehicle_keeps_the_vehicle(as_mathis, moto, parts_category):
    """Un véhicule abandonné (donneur de pièces) n'est plus « au garage » : il doit quand
    même être proposé, et choisi d'office, quand on vient de sa fiche."""
    moto.status = moto.Status.ABANDONED
    moto.save()
    response = as_mathis.get(f"/depenses/ajouter/?vehicule={moto.number}")
    (ligne,) = response.context["formset"].forms
    assert moto in ligne.fields["vehicle"].queryset
    assert ligne["vehicle"].value() == moto.pk

    donnees = form_data(
        [line(parts_category, price="20", vehicle=moto)], payer=response_payer(as_mathis)
    )
    as_mathis.post(f"/depenses/ajouter/?vehicule={moto.number}", donnees)
    assert ExpenseLine.objects.filter(vehicle=moto, label="Disque").exists()


def test_no_new_expense_for_a_sold_vehicle(as_mathis, make_vehicle):
    vendu = make_vehicle(status="SOLD")
    response = as_mathis.get(f"/depenses/ajouter/?vehicule={vendu.number}")
    assert response.status_code == 302
    assert "ses dépenses sont verrouillées" in messages_of(response)[0]


def test_edit_purchase_expense_corrects_price_and_purchase_date(as_mathis, moto, mathis):
    """La ligne d'achat (champs désactivés) se corrige en prix ; corriger la date de la
    dépense corrige aussi la date d'achat du véhicule."""
    depense = vehicle_services.purchase_lines(moto).get().expense
    achat = depense.lines.get()
    page = as_mathis.get(f"/depenses/{depense.pk}/modifier/")
    # Sans frais de port, le montant payé reste vide : il suivra le prix corrigé.
    assert page.context["form"]["total_paid"].value() is None

    donnees = form_data(
        # Catégorie et véhicule envoyés faux : les champs désactivés gardent leur valeur.
        [{"line_id": achat.pk, "label": achat.label, "quantity": "1", "unit_price": "1450"}],
        payer=str(mathis.pk),
        initial_forms=1,
        date="2026-09-28",
    )
    response = as_mathis.post(f"/depenses/{depense.pk}/modifier/", donnees)
    assert response.status_code == 302
    depense.refresh_from_db()
    moto.refresh_from_db()
    assert depense.total_paid == D("1450.00")
    assert depense.lines.get().category.code == ExpenseCategory.VEHICLE_PURCHASE
    assert moto.purchased_on == dt.date(2026, 9, 28)


def test_edit_keeps_the_amount_paid_when_there_was_shipping(as_mathis, order):
    page = as_mathis.get(f"/depenses/{order.pk}/modifier/")
    assert page.context["form"]["total_paid"].value() == D("86.00")

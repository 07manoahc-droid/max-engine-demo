"""Tests des règles des dépenses : enregistrement, payeurs à la corbeille (D40), achat
personnel (D41), fournisseurs, factures."""

import datetime as dt
from decimal import Decimal as D
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from accounting import queries
from accounting import services as accounting
from accounting.services import Payment
from core.models import Attachment, ExpenseCategory, Supplier
from expenses import services
from expenses.models import Expense, ExpenseLine, ExpensePayment, Refund
from expenses.services import LineEntry
from vehicles import services as vehicle_services

Status = ExpenseLine.Status

PDF = b"%PDF-1.7\n" + b"\x00" * 60
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 60


@pytest.fixture
def tooling(db):
    return ExpenseCategory.objects.get(code="tooling")


@pytest.fixture
def moto(make_vehicle):
    return make_vehicle(brand="GG", model="MC250")


def save(expense=None, *, lines, payments, total="86.00", **champs):
    """Enregistre une dépense avec des valeurs par défaut raisonnables."""
    valeurs = {
        "date": dt.date(2026, 10, 12),
        "supplier_name": "",
        "order_number": "",
        "total_paid": D(total),
        "is_personal": False,
        "notes": "",
    }
    valeurs.update(champs)
    return services.save_expense(expense, lines=lines, payments=payments, **valeurs)


def piece(category, label="Disque", price="71.90", vehicle=None, **champs):
    return LineEntry(label=label, category=category, unit_price=D(price), vehicle=vehicle, **champs)


def run_check() -> str:
    sortie = StringIO()
    call_command("controle_comptes", stdout=sortie)
    return sortie.getvalue()


# --- Enregistrer une dépense ---------------------------------------------------------


def test_new_expense_with_shared_payers_and_shipping(parts_category, moto, mathis, axel):
    depense = save(
        lines=[
            piece(parts_category, vehicle=moto),
            piece(parts_category, "Filtre", "8.40", vehicle=moto),
        ],
        payments=[Payment(mathis, D("43.00")), Payment(axel, D("43.00"))],
        supplier_name="Moto Axxe",
        order_number="50017",
    )
    assert depense.supplier.name == "Moto Axxe"
    assert depense.lines.count() == 2
    assert {(p.partner, p.amount) for p in depense.payments.all()} == {
        (mathis, D("43.00")),
        (axel, D("43.00")),
    }
    # Une pièce payée est « Commandé » par défaut.
    assert set(depense.lines.values_list("status", flat=True)) == {Status.ORDERED}
    cout = queries.vehicle_cost(moto.pk)
    assert cout.total == D("86.00")


def test_only_parts_have_a_status(tooling, mathis):
    depense = save(
        lines=[piece(tooling, "Caisse à outils", "86.00", status=Status.RECEIVED)],
        payments=[Payment(mathis, D("86.00"))],
    )
    assert depense.lines.get().status == ""


def test_expense_needs_a_line_and_matching_payments(parts_category, mathis):
    with pytest.raises(ValidationError, match="au moins une ligne"):
        save(lines=[], payments=[Payment(mathis, D("86.00"))])
    with pytest.raises(ValidationError, match="Corrige l'un des montants"):
        save(lines=[piece(parts_category)], payments=[Payment(mathis, D("70.00"))])
    assert not Expense.objects.exists()


def test_vehicle_purchase_is_not_entered_as_a_line(mathis):
    achat = ExpenseCategory.objects.get(code=ExpenseCategory.VEHICLE_PURCHASE)
    with pytest.raises(ValidationError, match="Enregistrer l'achat"):
        save(lines=[piece(achat, price="86.00")], payments=[Payment(mathis, D("86.00"))])


# --- Modifier une dépense : lignes et payeurs ---------------------------------------


@pytest.fixture
def shared(parts_category, moto, mathis, axel):
    """Commande de 2 pièces (74 €), payée moitié chacun."""
    return save(
        lines=[
            piece(parts_category, vehicle=moto),
            piece(parts_category, "Filtre", "8.40", vehicle=moto),
        ],
        payments=[Payment(mathis, D("43.00")), Payment(axel, D("43.00"))],
    )


def test_edit_removes_a_payer_to_the_bin_and_can_bring_him_back(shared, mathis, axel):
    """D40 : un payeur retiré part à la corbeille ; remis ensuite, il a un nouveau paiement."""
    lignes = [
        piece(line.category, line.label, str(line.unit_price), vehicle=line.vehicle, pk=line.pk)
        for line in shared.lines.all()
    ]
    save(shared, lines=lignes, payments=[Payment(mathis, D("86.00"))])
    assert [(p.partner, p.amount) for p in shared.payments.all()] == [(mathis, D("86.00"))]
    retire = ExpensePayment.all_objects.get(expense=shared, partner=axel)
    assert retire.is_deleted

    save(shared, lines=lignes, payments=[Payment(mathis, D("43.00")), Payment(axel, D("43.00"))])
    assert shared.payments.count() == 2
    assert ExpensePayment.all_objects.filter(expense=shared, partner=axel).count() == 2


def test_binned_payments_are_ignored_everywhere(shared, moto, mathis, axel):
    """D40 : un paiement à la corbeille ne compte ni dans le solde, ni dans les coûts, ni
    pour `controle_comptes`."""
    lignes = [
        piece(line.category, line.label, str(line.unit_price), vehicle=line.vehicle, pk=line.pk)
        for line in shared.lines.all()
    ]
    save(shared, lines=lignes, payments=[Payment(mathis, D("86.00"))])

    donnees = queries.load_accounts()
    positions = queries.positions(donnees)
    assert positions[mathis.pk].paid_out == D("86.00")
    assert positions[axel.pk].paid_out == D("0.00")
    solde = accounting.balance(positions)
    assert (solde.debtor, solde.creditor, solde.amount) == (axel.pk, mathis.pk, D("43.00"))
    assert queries.vehicle_cost(moto.pk).by_partner == {mathis.pk: D("86.00")}

    sortie = run_check()
    assert "Les comptes sont cohérents." in sortie
    assert "1 paiements" in sortie
    assert "Axel doit 43,00 € à Mathis" in sortie


def test_edit_updates_lines_and_bins_the_removed_one(shared, parts_category, moto, mathis):
    disque, filtre = shared.lines.order_by("id")
    save(
        shared,
        lines=[
            piece(parts_category, "Disque arrière", "71.90", vehicle=moto, pk=disque.pk),
            piece(parts_category, "Chaîne", "11.01", vehicle=moto),
        ],
        payments=[Payment(mathis, D("86.00"))],
    )
    assert list(shared.lines.order_by("id").values_list("label", flat=True)) == [
        "Disque arrière",
        "Chaîne",
    ]
    assert ExpenseLine.all_objects.get(pk=filtre.pk).is_deleted


def test_refunded_line_cannot_be_removed_nor_cost_less_than_its_refund(
    shared, parts_category, moto, mathis
):
    disque, filtre = shared.lines.order_by("id")
    Refund.objects.create(line=filtre, amount=D("6.00"), refunded_to=mathis)
    paiements = [Payment(mathis, D("86.00"))]
    with pytest.raises(ValidationError, match="a un remboursement"):
        save(shared, lines=[piece(parts_category, vehicle=moto, pk=disque.pk)], payments=paiements)
    with pytest.raises(ValidationError, match="ne tombe pas juste"):
        save(
            shared,
            total="63.00",
            lines=[
                piece(parts_category, vehicle=moto, pk=disque.pk),
                piece(parts_category, "Filtre", "0.01", vehicle=moto, pk=filtre.pk),
            ],
            payments=[Payment(mathis, D("63.00"))],
        )
    shared.refresh_from_db()
    assert shared.total_paid == D("86.00")


def test_purchase_line_keeps_its_category_and_vehicle(moto, mathis, parts_category, make_vehicle):
    depense = vehicle_services.record_purchase(
        moto,
        price=D("1500"),
        date=dt.date(2026, 10, 1),
        payments=[Payment(mathis, D("1500"))],
        seller=None,
        partner=mathis,
    )
    achat = depense.lines.get()
    autre = make_vehicle()
    paiements = [Payment(mathis, D("1500"))]
    with pytest.raises(ValidationError, match="garde sa catégorie"):
        save(
            depense,
            total="1500",
            lines=[piece(achat.category, achat.label, "1500", vehicle=autre, pk=achat.pk)],
            payments=paiements,
        )
    with pytest.raises(ValidationError, match="ne peut pas être retirée"):
        save(depense, total="1500", lines=[piece(parts_category, price="1500")], payments=paiements)
    # Le prix d'achat, lui, se corrige.
    save(
        depense,
        total="1450",
        lines=[piece(achat.category, achat.label, "1450", vehicle=moto, pk=achat.pk)],
        payments=[Payment(mathis, D("1450"))],
    )
    assert queries.vehicle_cost(moto.pk).total == D("1450.00")


def test_a_line_of_another_expense_is_refused(shared, parts_category, mathis):
    autre = save(
        lines=[piece(parts_category, price="10")], total="10", payments=[Payment(mathis, D("10"))]
    )
    etrangere = autre.lines.get()
    with pytest.raises(ValidationError, match="déjà été commandée"):
        save(
            shared,
            lines=[piece(parts_category, price="74", pk=etrangere.pk)],
            payments=[Payment(mathis, D("86.00"))],
        )


# --- Achat personnel (D41) -----------------------------------------------------------


def test_personal_purchase_is_refused_with_a_vehicle(tooling, moto, mathis):
    with pytest.raises(ValidationError, match="achat personnel"):
        save(
            lines=[piece(tooling, "Clé", "86.00", vehicle=moto)],
            payments=[Payment(mathis, D("86.00"))],
            is_personal=True,
        )


def test_personal_purchase_without_vehicle_counts_nowhere(tooling, mathis, axel):
    save(
        lines=[piece(tooling, "Perceuse", "86.00")],
        payments=[Payment(mathis, D("86.00"))],
        is_personal=True,
    )
    positions = queries.positions(queries.load_accounts())
    assert positions[mathis.pk].paid_out == D("0.00")
    assert "Les comptes sont cohérents." in run_check()


# --- Fournisseurs ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("existant", "saisi"),
    [
        ("Leboncoin", "LEBONCOIN"),
        ("Bihr", "BIHR"),
        ("Électro Moto", "électro moto"),
        ("Électro Moto", "Electro Moto"),
        ("Electro Moto", "électro moto"),
        ("Pièces Œuvre", "pieces œuvre"),
        ("Moto Axxe", "  moto   axxe "),
    ],
)
def test_supplier_is_found_ignoring_case_spaces_and_accents(db, existant, saisi):
    """Comparaison côté application, sans majuscules ni accents : SQLite ne met pas « É »
    en minuscule."""
    fournisseur = Supplier.objects.create(name=existant)
    assert services.find_or_create_supplier(saisi) == fournisseur
    assert Supplier.objects.count() == 1


def test_bihr_and_BIHR_are_one_supplier_across_expenses(tooling, mathis):
    for nom in ("Bihr", "BIHR", "bihr "):
        save(
            lines=[piece(tooling, price="10.00")],
            payments=[Payment(mathis, D("10.00"))],
            total="10.00",
            supplier_name=nom,
        )
    (fournisseur,) = Supplier.objects.all()
    assert fournisseur.name == "Bihr"
    assert fournisseur.expenses.count() == 3


def test_new_supplier_is_created_once_and_empty_name_gives_none(db):
    cree = services.find_or_create_supplier("  Bihr  ")
    assert cree.name == "Bihr"
    assert services.find_or_create_supplier("bihr") == cree
    assert services.find_or_create_supplier("   ") is None


# --- Factures ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nom", "contenu", "accepte"),
    [
        ("facture.pdf", PDF, True),
        ("ticket.jpg", JPEG, True),
        ("facture.PDF", PDF, True),
        ("ticket.heic", HEIC, False),
        ("ticket.jpg", HEIC, False),
        ("facture.pdf", JPEG, False),
        ("facture.docx", PDF, False),
    ],
)
def test_invoice_formats(nom, contenu, accepte):
    assert (services.invoice_problem(nom, len(contenu), contenu[:16]) is None) is accepte


def test_heic_invoice_gets_the_jpeg_advice():
    assert "JPEG" in services.invoice_problem("ticket.heic", 10, HEIC[:16])


def test_too_big_invoice_is_refused():
    probleme = services.invoice_problem("facture.pdf", services.INVOICE_MAX_BYTES + 1, PDF[:16])
    assert "20 Mo" in probleme


def test_invoices_go_to_the_vehicle_folder_or_the_garage(shared, tooling, mathis):
    (facture,) = services.add_invoices(shared, [SimpleUploadedFile("facture.pdf", PDF)])
    assert facture.kind == Attachment.Kind.INVOICE
    assert facture.vehicle == services.single_vehicle(shared)
    assert facture.file.name.startswith(f"vehicules/{facture.vehicle.number}/")

    garage = save(lines=[piece(tooling, "Caisse", "86.00")], payments=[Payment(mathis, D("86.00"))])
    (ticket,) = services.add_invoices(garage, [SimpleUploadedFile("ticket.jpg", JPEG)])
    assert ticket.vehicle is None
    assert ticket.file.name.startswith("garage/")


def test_invoices_are_not_photos_of_the_gallery(shared):
    services.add_invoices(shared, [SimpleUploadedFile("facture.jpg", JPEG)])
    vehicule = services.single_vehicle(shared)
    assert all(not photos for _, _, photos in vehicle_services.photo_stages(vehicule))
    assert vehicle_services.cover_photos([vehicule]) == {}


def test_the_same_line_sent_twice_is_refused(shared, parts_category, moto, mathis):
    disque = shared.lines.order_by("id").first()
    with pytest.raises(ValidationError, match="envoyée deux fois"):
        save(
            shared,
            lines=[
                piece(parts_category, vehicle=moto, pk=disque.pk),
                piece(parts_category, "Autre", "11.01", vehicle=moto, pk=disque.pk),
            ],
            payments=[Payment(mathis, D("86.00"))],
        )

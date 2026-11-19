"""Corbeille et restauration (D74 à D77) : ce qu'on y met, ce qui en revient, et ce qui
reste bloqué. Restaurer une dépense change les soldes : les comptes sont comparés avant la
corbeille et après la restauration."""

import datetime as dt
import html
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from accounting import queries
from accounting.services import Payment
from core import trash
from core.models import Attachment, Contact, VehicleType
from expenses import services as expense_services
from expenses.models import Expense, ExpenseLine, PartTransfer, Refund
from expenses.services import LineEntry
from sales import services as sale_services
from sales.services import TradeIn
from sales.tests.helpers import record_deposit_for
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

DATE = dt.date(2026, 10, 20)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def comptes() -> str:
    """Le compte rendu de `controle_comptes` : il doit revenir à l'identique."""
    sortie = StringIO()
    call_command("controle_comptes", stdout=sortie)
    return sortie.getvalue()


def depense(lines, payments, total, **champs) -> Expense:
    valeurs = {
        "date": DATE,
        "supplier_name": "",
        "order_number": "",
        "total_paid": D(total),
        "is_personal": False,
        "notes": "",
    }
    return expense_services.save_expense(None, lines=lines, payments=payments, **valeurs | champs)


def acheter(vehicule, partner, prix="1500") -> Expense:
    achat = vehicle_services.record_purchase(
        vehicule,
        price=D(prix),
        date=DATE,
        payments=[Payment(partner, D(prix))],
        seller=None,
        partner=partner,
    )
    vehicule.refresh_from_db()
    return achat


def vendre(vehicule, partner, prix="2000", trade_in=None):
    return sale_services.record_sale(
        vehicule,
        price=D(prix),
        date=dt.date(2026, 10, 30),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(partner, D(prix) - (trade_in.value if trade_in else D("0")))],
        partner=partner,
        trade_in=trade_in,
    )


def relu(objet):
    return type(objet).all_objects.get(pk=objet.pk)


@pytest.fixture
def moto(make_vehicle):
    return make_vehicle(brand="GG", model="MC250", status="IN_REPAIR")


@pytest.fixture
def pieces(parts_category, moto, axel):
    """Une dépense de 60 € sur la GG, payée par Axel."""
    return depense(
        [LineEntry("Disque", parts_category, D("60.00"), vehicle=moto, status="RECEIVED")],
        [Payment(axel, D("60.00"))],
        "60.00",
    )


@pytest.fixture
def vente_avec_reprise(make_vehicle, mathis, axel):
    """GG achetée 1 500 €, vendue 2 000 € avec une 125 reprise pour 700 €."""
    vehicule = make_vehicle(brand="GG", model="MC250")
    acheter(vehicule, mathis)
    reprise = Vehicle(vehicle_type=VehicleType.objects.first(), brand="Yamaha", model="125")
    return vendre(vehicule, axel, trade_in=TradeIn(reprise, D("700")))


# ---------------------------------------------------------------------------
# Une dépense entière
# ---------------------------------------------------------------------------


def test_trashed_expense_leaves_the_accounts_and_comes_back_identical(pieces, axel):
    """D74 : la dépense sort des comptes avec ses lignes et ses paiements ; restaurée, les
    comptes reviennent exactement (`controle_comptes` identique)."""
    avant = comptes()
    expense_services.trash_expense(pieces)
    assert queries.positions(queries.load_accounts())[axel.pk].paid_out == D("0.00")
    assert comptes() != avant
    assert trash.restore("depense", pieces.pk).startswith("La dépense est sortie de la corbeille")
    assert comptes() == avant
    assert relu(pieces).lines.count() == 1


def test_expense_of_a_sold_vehicle_is_locked(pieces, moto):
    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    with pytest.raises(ValidationError, match="Rouvre sa fiche"):
        expense_services.trash_expense(pieces)
    assert not relu(pieces).is_deleted


def test_trade_in_purchase_does_not_go_to_the_trash_alone(vente_avec_reprise):
    """D51 : tant que la vente tient, l'achat repris ne part pas seul."""
    achat = vehicle_services.purchase_lines(vente_avec_reprise.trade_in_vehicle).get().expense
    with pytest.raises(ValidationError, match="Pour l'annuler, annule la vente"):
        expense_services.trash_expense(achat)


def test_trashing_twice_says_so(pieces):
    expense_services.trash_expense(pieces)
    with pytest.raises(ValidationError, match="déjà dans la corbeille"):
        expense_services.trash_expense(pieces)


def test_wrong_purchase_is_cancelled_by_the_trash_then_not_restored_twice(
    make_vehicle, mathis, axel
):
    """D35, D39 : l'achat saisi par erreur part à la corbeille, le véhicule revient en
    attente d'achat et reçoit le bon achat ; l'ancien ne revient plus."""
    vehicule = make_vehicle()
    faux = acheter(vehicule, mathis, "1500")
    expense_services.trash_expense(faux)
    assert not vehicle_services.has_purchase(vehicule)

    vehicle_services.change_status(vehicule, "WAITING_PURCHASE", mathis)
    assert "En attente d'achat" in trash.expense_restore_refusal(relu(faux))
    acheter(vehicule, axel, "1400")
    with pytest.raises(ValidationError, match="a déjà un autre achat"):
        trash.restore("depense", faux.pk)


def test_restored_expense_respects_the_lock(pieces, moto):
    expense_services.trash_expense(pieces)
    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    with pytest.raises(ValidationError, match="Rouvre sa fiche"):
        trash.restore("depense", pieces.pk)
    Vehicle.objects.filter(pk=moto.pk).update(
        reopened_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC), reopen_reason="Facture oubliée"
    )
    trash.restore("depense", pieces.pk)


def test_expense_waits_for_its_vehicle(pieces, moto):
    """La fiche à la corbeille revient d'abord, puis sa dépense."""
    expense_services.trash_expense(pieces)
    vehicle_services.trash_vehicle(moto)
    with pytest.raises(ValidationError, match="restaure d'abord le véhicule"):
        trash.restore("depense", pieces.pk)
    trash.restore("vehicule", moto.pk)
    trash.restore("depense", pieces.pk)


# ---------------------------------------------------------------------------
# Une fiche sans argent
# ---------------------------------------------------------------------------


def test_vehicle_without_money_goes_to_the_trash_and_back(moto):
    vehicle_services.trash_vehicle(moto)
    assert moto not in vehicle_services.garage_vehicles()
    assert trash.restore("vehicule", moto.pk) == "La fiche est sortie de la corbeille."
    assert moto in vehicle_services.garage_vehicles()


def test_vehicle_with_money_stays(pieces, moto):
    with pytest.raises(ValidationError, match="Mets d'abord ses dépenses à la corbeille"):
        vehicle_services.trash_vehicle(moto)
    expense_services.trash_expense(pieces)
    expense_services.add_part_to_order(label="Câble", vehicle=moto)
    assert "Retire d'abord les pièces à commander" in vehicle_services.vehicle_trash_refusal(moto)


def test_vehicle_with_a_deposit_stays(make_vehicle, mathis):
    vehicule = make_vehicle()
    acheter(vehicule, mathis)
    vehicule.status = "FOR_SALE"
    vehicule.save()
    record_deposit_for(
        vehicule, amount=D("200"), date=DATE, received_by=mathis, buyer=None, partner=mathis
    )
    assert "acompte" in vehicle_services.vehicle_trash_refusal(vehicule)


def test_part_transfer_blocks_the_trash_and_the_trade_in_cancellation(
    vente_avec_reprise, make_vehicle, parts_category, axel
):
    """D76 : un transfert de pièce porte de l'argent d'un véhicule à l'autre."""
    reprise = vente_avec_reprise.trade_in_vehicle
    autre = make_vehicle(brand="KTM")
    ligne = depense(
        [LineEntry("Selle", parts_category, D("40"), vehicle=autre, status="RECEIVED")],
        [Payment(axel, D("40"))],
        "40",
    ).lines.get()
    PartTransfer.objects.create(line=ligne, from_vehicle=reprise, to_vehicle=autre)
    assert "transfert de pièce" in sale_services.trade_in_cancel_refusal(vente_avec_reprise)
    vide = make_vehicle(brand="Honda")
    PartTransfer.objects.create(line=ligne, from_vehicle=autre, to_vehicle=vide)
    assert "transfert de pièce" in vehicle_services.vehicle_trash_refusal(vide)


# ---------------------------------------------------------------------------
# La reprise d'une vente annulée (D51)
# ---------------------------------------------------------------------------


def test_cancelled_trade_in_never_comes_back_alone(vente_avec_reprise, axel):
    reprise = vente_avec_reprise.trade_in_vehicle
    achat = vehicle_services.purchase_lines(reprise).get().expense
    sale_services.cancel_sale(vente_avec_reprise, None, axel)
    with pytest.raises(ValidationError, match="reste à la corbeille avec son achat"):
        trash.restore("vehicule", reprise.pk)
    with pytest.raises(ValidationError, match="rien ne revient sur lui. Si c'était une vraie"):
        trash.restore("depense", achat.pk)


# ---------------------------------------------------------------------------
# Pièces, remboursements, fichiers
# ---------------------------------------------------------------------------


def test_part_to_order_comes_back_only_to_a_vehicle_in_the_garage(moto, mathis):
    piece = expense_services.add_part_to_order(label="Câble", vehicle=moto)
    expense_services.remove_part_to_order(piece)
    vehicle_services.change_status(moto, "ABANDONED", mathis)
    with pytest.raises(ValidationError, match="est abandonné"):
        trash.restore("piece", piece.pk)
    vehicle_services.change_status(moto, "WAITING_PURCHASE", mathis)
    assert trash.restore("piece", piece.pk) == "La pièce est de nouveau à commander."
    assert piece in expense_services.parts_to_order()


def test_restored_refund_marks_the_part_returned_again(pieces, axel):
    ligne = pieces.lines.get()
    retour = expense_services.record_refund(ligne, date=DATE, amount=D("60"), refunded_to=axel)
    avant = comptes()
    expense_services.remove_refund(retour)
    assert relu(ligne).status == "RECEIVED"
    trash.restore("remboursement", retour.pk)
    assert relu(ligne).status == "RETURNED"
    assert comptes() == avant


def test_refund_does_not_come_back_beyond_the_line(pieces, axel):
    ligne = pieces.lines.get()
    retour = expense_services.record_refund(ligne, date=DATE, amount=D("40"), refunded_to=axel)
    expense_services.remove_refund(retour)
    expense_services.record_refund(ligne, date=DATE, amount=D("30"), refunded_to=axel)
    with pytest.raises(ValidationError, match="ne tient plus.*Il ne reste que 30,00"):
        trash.restore("remboursement", retour.pk)


def test_refund_waits_for_its_expense(pieces, axel):
    retour = expense_services.record_refund(
        pieces.lines.get(), date=DATE, amount=D("10"), refunded_to=axel
    )
    expense_services.remove_refund(retour)
    expense_services.trash_expense(pieces)
    assert "restaure-la d'abord" in trash.refund_restore_refusal(relu(retour))


def test_photo_comes_back_even_on_a_sold_vehicle(moto):
    """D50 : les photos restent permises sur un véhicule vendu."""
    (photo,), _ = vehicle_services.add_photos(
        moto, [SimpleUploadedFile("moto.jpg", JPEG, content_type="image/jpeg")], "BEFORE"
    )
    vehicle_services.remove_photo(photo)
    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    assert trash.restore("fichier", photo.pk) == "Le fichier est sorti de la corbeille."


def test_invoice_of_a_locked_expense_stays(pieces, moto):
    facture = Attachment.objects.create(
        expense=pieces, kind=Attachment.Kind.INVOICE, file="garage/facture.pdf"
    )
    expense_services.remove_invoice(facture)
    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    assert "Rouvre sa fiche" in trash.attachment_restore_refusal(relu(facture))


def test_restoring_something_already_back_says_so(moto):
    vehicle_services.trash_vehicle(moto)
    trash.restore("vehicule", moto.pk)
    with pytest.raises(ValidationError, match="n'est plus dans la corbeille"):
        trash.restore("vehicule", moto.pk)
    with pytest.raises(ValidationError, match="n'est plus dans la corbeille"):
        trash.restore("inconnu", moto.pk)


def test_paid_line_and_payment_are_never_listed(pieces, parts_category, axel):
    """Une ligne retirée d'une dépense payée, ou un paiement, ne reviennent jamais seuls."""
    ligne = pieces.lines.get()
    expense_services.save_expense(
        pieces,
        date=DATE,
        supplier_name="",
        order_number="",
        total_paid=D("60.00"),
        is_personal=False,
        notes="",
        lines=[LineEntry("Plaquettes", parts_category, D("60.00"), status="RECEIVED")],
        payments=[Payment(axel, D("60.00"))],
    )
    assert relu(ligne).is_deleted
    assert trash.trash_groups() == []
    with pytest.raises(ValidationError):
        trash.restore("piece", ligne.pk)


# ---------------------------------------------------------------------------
# Une ligne sur un véhicule à la corbeille (D77)
# ---------------------------------------------------------------------------


def test_services_refuse_a_line_on_a_trashed_vehicle(moto, parts_category, axel):
    moto.soft_delete()
    with pytest.raises(ValidationError, match="est à la corbeille : restaure-le ou choisis"):
        depense(
            [LineEntry("Câble", parts_category, D("10"), vehicle=moto, status="RECEIVED")],
            [Payment(axel, D("10"))],
            "10",
        )
    with pytest.raises(ValidationError, match="est à la corbeille : restaure-le ou choisis"):
        expense_services.add_part_to_order(label="Câble", vehicle=moto)


def test_expense_form_shows_the_trashed_vehicle_instead_of_garage(as_mathis, pieces, moto):
    """Au lieu de passer en silence sur « Garage », la ligne garde son véhicule, nommé
    « (à la corbeille) », et l'enregistrement est refusé."""
    moto.soft_delete()
    page = as_mathis.get(f"/depenses/{pieces.pk}/modifier/").content.decode()
    assert f'value="{moto.pk}" selected>n°{moto.number}' in page
    assert "(à la corbeille)" in page


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


def test_empty_trash_says_so(as_mathis):
    assert "La corbeille est vide." in as_mathis.get("/corbeille/").content.decode()


def test_sidebar_links_to_the_trash(as_mathis):
    assert 'href="/corbeille/"' in as_mathis.get("/").content.decode()


def test_expense_trashed_from_its_page_then_restored_from_the_trash(as_mathis, pieces, moto):
    page = as_mathis.get(f"/depenses/{pieces.pk}/").content.decode()
    assert f'action="/depenses/{pieces.pk}/corbeille/"' in page

    reponse = as_mathis.post(f"/depenses/{pieces.pk}/corbeille/")
    assert reponse.url == moto.get_absolute_url()
    assert "La dépense est dans la corbeille" in messages_of(reponse)[0]

    corbeille = as_mathis.get("/corbeille/").content.decode()
    assert "Dépenses" in corbeille
    assert "Dépense du 20/10/2026" in corbeille
    assert "par Mathis" in corbeille
    reponse = as_mathis.post(f"/corbeille/depense/{pieces.pk}/restaurer/")
    assert reponse.url == "/corbeille/"
    assert messages_of(reponse) == [
        "La dépense est sortie de la corbeille : elle compte de nouveau dans les comptes."
    ]
    assert not relu(pieces).is_deleted


def test_trash_shows_the_reason_instead_of_the_button(as_mathis, vente_avec_reprise, axel):
    reprise = vente_avec_reprise.trade_in_vehicle
    sale_services.cancel_sale(vente_avec_reprise, None, axel)
    page = as_mathis.get("/corbeille/").content.decode()
    assert "il reste à la corbeille avec son achat" in page
    assert f"/corbeille/vehicule/{reprise.pk}/restaurer/" not in page


def test_refused_restore_explains_why(as_mathis, pieces, moto):
    expense_services.trash_expense(pieces)
    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    reponse = as_mathis.post(f"/corbeille/depense/{pieces.pk}/restaurer/")
    assert "Rouvre sa fiche" in messages_of(reponse)[0]
    assert relu(pieces).is_deleted


def test_trade_in_purchase_page_has_no_trash_button(as_mathis, vente_avec_reprise):
    achat = vehicle_services.purchase_lines(vente_avec_reprise.trade_in_vehicle).get().expense
    page = as_mathis.get(f"/depenses/{achat.pk}/").content.decode()
    assert f"/depenses/{achat.pk}/corbeille/" not in page


def test_vehicle_page_offers_the_trash_only_without_money(as_mathis, moto, parts_category, axel):
    fiche = moto.get_absolute_url()
    assert f'action="/vehicules/{moto.number}/corbeille/"' in as_mathis.get(fiche).content.decode()

    reponse = as_mathis.post(f"/vehicules/{moto.number}/corbeille/")
    assert reponse.url == "/vehicules/"
    assert as_mathis.get(fiche).status_code == 404
    # Second clic (autre onglet) : un message, pas une page introuvable.
    reponse = as_mathis.post(f"/vehicules/{moto.number}/corbeille/")
    assert reponse.url == "/vehicules/"
    assert any("déjà dans la corbeille" in m for m in messages_of(reponse))
    trash.restore("vehicule", moto.pk)

    # Une pièce à commander : la fiche dit quoi faire avant.
    piece = expense_services.add_part_to_order(label="Câble", vehicle=moto)
    page = html.unescape(as_mathis.get(fiche).content.decode())
    assert "Pour mettre cette fiche à la corbeille : Retire d'abord les pièces" in page
    assert f'action="/vehicules/{moto.number}/corbeille/"' not in page
    expense_services.remove_part_to_order(piece)

    # Une dépense payée : la fiche ne parle plus de corbeille (relecture : ce serait une
    # invitation à effacer de vraies dépenses).
    depense(
        [LineEntry("Câble", parts_category, D("10"), vehicle=moto, status="RECEIVED")],
        [Payment(axel, D("10"))],
        "10",
    )
    page = as_mathis.get(fiche).content.decode()
    assert "Pour mettre cette fiche à la corbeille" not in page
    assert f'action="/vehicules/{moto.number}/corbeille/"' not in page


def test_trade_in_page_does_not_mention_the_trash(as_mathis, vente_avec_reprise):
    """Relecture : la fiche d'une reprise ne propose pas une impasse (son achat ne part pas
    à la corbeille seul, D51)."""
    reprise = vente_avec_reprise.trade_in_vehicle
    page = as_mathis.get(reprise.get_absolute_url()).content.decode()
    assert "Pour mettre cette fiche à la corbeille" not in page


def test_expense_trashed_twice_from_two_tabs_says_so(as_mathis, pieces, moto):
    as_mathis.post(f"/depenses/{pieces.pk}/corbeille/")
    reponse = as_mathis.post(f"/depenses/{pieces.pk}/corbeille/")
    assert reponse.url == moto.get_absolute_url()
    assert any("déjà dans la corbeille" in m for m in messages_of(reponse))


def test_sold_vehicle_page_does_not_mention_the_trash(as_mathis, make_vehicle, mathis):
    vehicule = make_vehicle()
    acheter(vehicule, mathis)
    vendre(vehicule, mathis)
    page = as_mathis.get(vehicule.get_absolute_url()).content.decode()
    assert "Pour mettre cette fiche à la corbeille" not in page


def test_trash_lists_every_group(as_mathis, pieces, moto, axel, make_vehicle):
    """Chaque groupe s'affiche avec son nombre ; un contact à la corbeille aussi."""
    retour = expense_services.record_refund(
        pieces.lines.get(), date=DATE, amount=D("5"), refunded_to=axel
    )
    expense_services.remove_refund(retour)
    piece = expense_services.add_part_to_order(label="Câble", vehicle=moto)
    expense_services.remove_part_to_order(piece)
    Contact.objects.create(name="Jean Vendeur", city="Lyon").soft_delete()
    vehicle_services.trash_vehicle(make_vehicle(brand="Honda"))

    page = as_mathis.get("/corbeille/").content.decode()
    for titre in ("Véhicules", "Pièces à commander", "Remboursements", "Contacts"):
        assert f"{titre} <span" in page
    assert "Remboursement de « Disque »" in page
    assert "Jean Vendeur" in page
    assert Refund.all_objects.get(pk=retour.pk).is_deleted
    assert ExpenseLine.all_objects.get(pk=piece.pk).is_deleted


def test_restored_purchase_gives_back_its_date(make_vehicle, mathis):
    """Relecture : P1 (01/01) à la corbeille, P2 (01/03) enregistré puis à la corbeille,
    P1 restauré : la fiche reprend le 01/01, sinon durée et bilan seraient faux."""
    vehicule = make_vehicle()
    premier = vehicle_services.record_purchase(
        vehicule,
        price=D("1000"),
        date=dt.date(2026, 1, 1),
        payments=[Payment(mathis, D("1000"))],
        seller=None,
        partner=mathis,
    )
    expense_services.trash_expense(premier)
    vehicle_services.change_status(vehicule, "WAITING_PURCHASE", mathis)
    second = vehicle_services.record_purchase(
        vehicule,
        price=D("900"),
        date=dt.date(2026, 3, 1),
        payments=[Payment(mathis, D("900"))],
        seller=None,
        partner=mathis,
    )
    expense_services.trash_expense(second)
    trash.restore("depense", premier.pk)
    assert relu(vehicule).purchased_on == dt.date(2026, 1, 1)


def test_expense_with_a_refund_comes_back_with_identical_accounts(pieces, axel):
    """Les remboursements entrent dans les positions : ils sortent et reviennent avec leur
    dépense."""
    expense_services.record_refund(
        pieces.lines.get(), date=DATE, amount=D("20"), refunded_to=axel
    )
    avant = comptes()
    expense_services.trash_expense(pieces)
    assert comptes() != avant
    trash.restore("depense", pieces.pk)
    assert comptes() == avant


def test_refund_of_a_removed_line_or_a_personal_expense_stays(pieces, axel, parts_category):
    ligne = pieces.lines.get()
    retour = expense_services.record_refund(ligne, date=DATE, amount=D("10"), refunded_to=axel)
    expense_services.remove_refund(retour)
    ExpenseLine.objects.filter(pk=ligne.pk).update(
        deleted_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
    )
    assert "a été retirée de sa dépense" in trash.refund_restore_refusal(relu(retour))

    ExpenseLine.all_objects.filter(pk=ligne.pk).update(deleted_at=None, vehicle=None)
    Expense.objects.filter(pk=pieces.pk).update(is_personal=True)
    assert "achat personnel" in trash.refund_restore_refusal(relu(retour))


def test_refund_invoice_photo_and_part_of_a_cancelled_trade_in_say_so(vente_avec_reprise, axel):
    """Relecture : la dépense d'une reprise annulée ne reviendra jamais ; le message ne dit
    pas « restaure-la d'abord ». « Préviens Mathis » est réservé aux dépenses (essai 3a)."""
    reprise = vente_avec_reprise.trade_in_vehicle
    achat = vehicle_services.purchase_lines(reprise).get().expense
    photo = Attachment.objects.create(vehicle=reprise, file="vehicules/2/moto.jpg")
    photo.soft_delete()
    piece = expense_services.add_part_to_order(label="Câble", vehicle=reprise)
    expense_services.remove_part_to_order(piece)
    facture = Attachment.objects.create(
        expense=achat, kind=Attachment.Kind.INVOICE, file="garage/facture.pdf"
    )
    facture.soft_delete()
    retour = Refund.objects.create(
        line=achat.lines.get(), date=DATE, amount=D("0"), refunded_to=axel
    )
    retour.soft_delete()
    sale_services.cancel_sale(vente_avec_reprise, None, axel)
    for refus in (
        trash.refund_restore_refusal(relu(retour)),
        trash.attachment_restore_refusal(relu(facture)),
        trash.attachment_restore_refusal(relu(photo)),
        trash.part_restore_refusal(relu(piece)),
    ):
        assert "rien ne revient sur lui" in refus
        assert "préviens Mathis" not in refus


def test_invoice_waits_for_its_expense_and_photo_for_its_vehicle(pieces, moto):
    facture = Attachment.objects.create(
        expense=pieces, kind=Attachment.Kind.INVOICE, file="garage/facture.pdf"
    )
    expense_services.remove_invoice(facture)
    expense_services.trash_expense(pieces)
    assert "restaure-la d'abord" in trash.attachment_restore_refusal(relu(facture))

    photo = Attachment.objects.create(vehicle=moto, file="vehicules/1/moto.jpg")
    photo.soft_delete()
    trash.restore("depense", pieces.pk)
    expense_services.trash_expense(pieces)
    vehicle_services.trash_vehicle(moto)
    assert "restaure d'abord le véhicule" in trash.attachment_restore_refusal(relu(photo))


def test_part_waits_for_its_vehicle(moto):
    piece = expense_services.add_part_to_order(label="Câble", vehicle=moto)
    expense_services.remove_part_to_order(piece)
    vehicle_services.trash_vehicle(moto)
    assert "restaure d'abord le véhicule" in trash.part_restore_refusal(relu(piece))


def test_photos_come_back_with_their_vehicle(moto):
    """D76 : les photos ne partent pas une à une ; elles reviennent avec la fiche."""
    photo = Attachment.objects.create(vehicle=moto, file="vehicules/1/moto.jpg")
    vehicle_services.trash_vehicle(moto)
    assert not relu(photo).is_deleted
    assert trash.trash_groups()[0].title == "Véhicules"
    assert len(trash.trash_groups()) == 1
    trash.restore("vehicule", moto.pk)
    assert list(relu(moto).attachments.all()) == [photo]

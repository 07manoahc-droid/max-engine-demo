"""Transfert de pièce entre véhicules (D90) : toute la ligne change de véhicule, ses payeurs
restent, le solde ne bouge pas. Chaque refus (verrou D50, reprise D51, corbeille D77,
véhicule abandonné, page restée ouverte), le succès après « Rouvrir la fiche », les écrans,
le journal et la consultation."""

import datetime as dt
import re
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.management import call_command

from accounting import queries
from accounting.services import Payment, balance
from core import journal
from core.models import ExpenseCategory, VehicleType
from expenses import services
from expenses.models import ExpenseLine, PartTransfer, Refund
from expenses.services import LineEntry
from sales import services as sale_services
from sales.services import TradeIn
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

Status = ExpenseLine.Status
DATE = dt.date(2026, 11, 10)
# Les lignes d'argent que compare le script de mise à jour.
LIGNES_ARGENT = re.compile(
    r"^\s*(Solde :|Bénéfice :|Encore dans le garage :|.+ : sorti .+, reçu .+, position )"
)


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def lignes_argent() -> list[str]:
    sortie = StringIO()
    call_command("controle_comptes", stdout=sortie)
    return [ligne for ligne in sortie.getvalue().splitlines() if LIGNES_ARGENT.match(ligne)]


def solde():
    return balance(queries.positions(queries.load_accounts()))


def depense(lines, payments, total):
    return services.save_expense(
        None,
        lines=lines,
        payments=payments,
        date=dt.date(2026, 10, 20),
        supplier_name="",
        order_number="",
        total_paid=D(total),
        is_personal=False,
        notes="",
    )


def acheter(vehicule, partner, prix="1500"):
    vehicle_services.record_purchase(
        vehicule,
        price=D(prix),
        date=dt.date(2026, 10, 1),
        payments=[Payment(partner, D(prix))],
        seller=None,
        partner=partner,
    )
    vehicule.refresh_from_db()
    return vehicule


def vendre(vehicule, partner, prix="2000", trade_in=None):
    vente = sale_services.record_sale(
        vehicule,
        price=D(prix),
        date=dt.date(2026, 10, 28),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(partner, D(prix) - (trade_in.value if trade_in else D("0")))],
        partner=partner,
        trade_in=trade_in,
    )
    vehicule.refresh_from_db()
    return vente


def transferer(ligne, vers, **champs):
    return services.transfer_line(ligne, to_vehicle=vers, date=DATE, **champs)


def cout(vehicule) -> D:
    return queries.vehicle_cost(vehicule.pk).total


@pytest.fixture
def gg(make_vehicle, mathis):
    return acheter(make_vehicle(brand="GG", model="MC250"), mathis)


@pytest.fixture
def honda(make_vehicle, axel):
    return acheter(make_vehicle(brand="Honda", model="CR"), axel, "1000")


@pytest.fixture
def disque(gg, parts_category, mathis):
    """Disque de 100 € sur la GG, payé par Mathis (cas 15)."""
    return depense(
        [LineEntry("Disque", parts_category, D("100.00"), vehicle=gg, status=Status.RECEIVED)],
        [Payment(mathis, D("100.00"))],
        "100.00",
    ).lines.get()


# ---------------------------------------------------------------------------
# Le transfert réussi : le coût suit la pièce, le solde ne bouge pas
# ---------------------------------------------------------------------------


@pytest.mark.finance
def test_transfer_moves_the_cost_and_keeps_the_balance(disque, gg, honda, mathis):
    """Cas 15 : coût de la GG −100 €, de la Honda +100 €, investissement de Mathis dans la
    Honda +100 € ; solde, bénéfice et argent encore dans le garage inchangés."""
    argent_avant, solde_avant = lignes_argent(), solde()
    avant = {v.pk: cout(v) for v in (gg, honda)}

    transfert = transferer(disque, honda, notes="Monté sur la Honda")

    assert cout(gg) == avant[gg.pk] - D("100.00")
    assert cout(honda) == avant[honda.pk] + D("100.00")
    assert queries.vehicle_cost(honda.pk).by_partner[mathis.pk] == D("100.00")
    assert solde() == solde_avant
    assert lignes_argent() == argent_avant
    disque.refresh_from_db()
    assert disque.vehicle == honda
    assert disque.status == Status.RECEIVED
    assert [(p.partner, p.amount) for p in disque.expense.payments.all()] == [(mathis, D("100.00"))]
    assert (transfert.from_vehicle, transfert.to_vehicle, transfert.date, transfert.notes) == (
        gg,
        honda,
        DATE,
        "Monté sur la Honda",
    )


@pytest.mark.finance
def test_refund_follows_the_line(disque, gg, honda, mathis):
    Refund.objects.create(line=disque, amount=D("30"), refunded_to=mathis, date=DATE)
    solde_avant = solde()
    transferer(disque, honda)
    assert cout(gg) == D("1500.00")
    assert cout(honda) == D("1070.00")
    assert solde() == solde_avant


@pytest.mark.finance
def test_line_shared_by_both_keeps_its_two_payers(gg, honda, parts_category, mathis, axel):
    """Toute la ligne part, avec la part de chacun : chaque associé garde son investissement."""
    ligne = depense(
        [LineEntry("Pneus", parts_category, D("90.00"), vehicle=gg, status=Status.RECEIVED)],
        [Payment(mathis, D("60.00")), Payment(axel, D("30.00"))],
        "90.00",
    ).lines.get()
    solde_avant = solde()
    transferer(ligne, honda)
    parts = queries.vehicle_cost(honda.pk).by_partner
    assert (parts[mathis.pk], parts[axel.pk]) == (D("60.00"), D("1030.00"))
    assert solde() == solde_avant


@pytest.mark.finance
def test_part_of_an_abandoned_donor_lowers_its_loss(disque, gg, honda):
    """Un véhicule abandonné donne ses pièces : sa perte baisse du coût de la pièce, qui
    passe sur le véhicule qui la reçoit. Le solde ne bouge pas."""
    vehicle_services.change_status(gg, Vehicle.Status.ABANDONED, None)
    solde_avant = solde()
    transferer(disque, honda)
    assert cout(gg) == D("1500.00")
    assert cout(honda) == D("1100.00")
    assert solde() == solde_avant


def test_a_line_can_move_again(disque, gg, honda, make_vehicle, axel):
    ktm = acheter(make_vehicle(brand="KTM", model="SX"), axel)
    transferer(disque, honda)
    transferer(disque, ktm)
    assert [(t.from_vehicle, t.to_vehicle) for t in disque.transfers.order_by("id")] == [
        (gg, honda),
        (honda, ktm),
    ]


# ---------------------------------------------------------------------------
# Verrou d'un véhicule vendu (D50) : refus, puis succès après « Rouvrir la fiche »
# ---------------------------------------------------------------------------


def test_line_of_a_sold_vehicle_moves_only_once_reopened(disque, gg, honda, mathis):
    vendre(gg, mathis)
    with pytest.raises(ValidationError, match="est vendu : ses dépenses sont verrouillées"):
        transferer(disque, honda)
    assert not PartTransfer.objects.exists()

    vehicle_services.reopen_vehicle(gg, "Disque monté sur la Honda")
    solde_avant = solde()
    transferer(disque, honda)
    disque.refresh_from_db()
    assert disque.vehicle == honda
    assert solde() == solde_avant


def test_sold_vehicle_receives_a_part_only_once_reopened(disque, gg, honda, axel):
    vendre(honda, axel)
    with pytest.raises(ValidationError, match="est vendu et ses dépenses sont verrouillées"):
        transferer(disque, honda)

    vehicle_services.reopen_vehicle(honda, "Pièce oubliée")
    honda.refresh_from_db()
    transferer(disque, honda)
    assert PartTransfer.objects.get().to_vehicle == honda


def test_expense_shared_with_a_sold_vehicle_is_locked(
    gg, honda, make_vehicle, parts_category, mathis
):
    """Une dépense sur plusieurs véhicules est verrouillée dès qu'un de ses véhicules l'est,
    même pour la ligne posée sur l'autre (D50)."""
    ktm = acheter(make_vehicle(brand="KTM", model="SX"), mathis)
    commande = depense(
        [
            LineEntry("Disque", parts_category, D("60"), vehicle=gg, status=Status.RECEIVED),
            LineEntry("Levier", parts_category, D("20"), vehicle=ktm, status=Status.RECEIVED),
        ],
        [Payment(mathis, D("80"))],
        "80",
    )
    vendre(ktm, mathis)
    ligne = commande.lines.get(label="Disque")
    with pytest.raises(ValidationError, match="Rouvre sa fiche"):
        transferer(ligne, honda)


# ---------------------------------------------------------------------------
# Reprise (D51), corbeille (D77) et autres refus
# ---------------------------------------------------------------------------


def test_purchase_line_never_moves(gg, honda):
    achat = vehicle_services.purchase_lines(gg).get()
    with pytest.raises(ValidationError, match="L'achat d'un véhicule reste sur ce véhicule"):
        transferer(achat, honda)


def test_trade_in_purchase_never_moves_and_a_transfer_blocks_the_cancellation(
    disque, gg, honda, axel
):
    """D51 : l'achat du véhicule repris garde sa ligne unique ; un transfert qui le touche
    bloque l'annulation de la vente, car il porte de l'argent d'un véhicule à l'autre (D76)."""
    reprise = Vehicle(vehicle_type=VehicleType.objects.first(), brand="Yamaha", model="125")
    vente = vendre(honda, axel, trade_in=TradeIn(reprise, D("700")))
    achat_repris = vehicle_services.purchase_lines(vente.trade_in_vehicle).get()
    with pytest.raises(ValidationError, match="L'achat d'un véhicule reste sur ce véhicule"):
        transferer(achat_repris, gg)

    assert sale_services.trade_in_cancel_refusal(vente) is None
    transferer(disque, vente.trade_in_vehicle)
    assert "a d'autres dépenses que son achat" in sale_services.trade_in_cancel_refusal(vente)
    # Repartie, la pièce laisse sa trace : l'annulation reste refusée.
    transferer(disque, gg)
    assert "transfert de pièce" in sale_services.trade_in_cancel_refusal(vente)


def test_vehicle_in_the_trash_receives_nothing(disque, make_vehicle):
    """D77 : une ligne ne se pose jamais sur un véhicule à la corbeille."""
    vide = make_vehicle(brand="Sherco", model="80")
    vehicle_services.trash_vehicle(vide)
    with pytest.raises(ValidationError, match="est à la corbeille : restaure-le"):
        transferer(disque, vide)
    assert not PartTransfer.objects.exists()


def test_expense_in_the_trash_is_not_transferred(disque, honda):
    services.trash_expense(disque.expense)
    with pytest.raises(ValidationError, match="Cette dépense est à la corbeille"):
        transferer(disque, honda)


def test_abandoned_vehicle_receives_nothing(disque, honda):
    vehicle_services.change_status(honda, Vehicle.Status.ABANDONED, None)
    with pytest.raises(ValidationError, match="est abandonné. Rien n'est enregistré"):
        transferer(disque, honda)


def test_garage_line_and_part_to_order_are_not_transferred(honda, mathis):
    outillage = ExpenseCategory.objects.get_or_create(code="tools", defaults={"name": "Outils"})[0]
    ligne = depense(
        [LineEntry("Clé", outillage, D("15"), vehicle=None)], [Payment(mathis, D("15"))], "15"
    ).lines.get()
    with pytest.raises(ValidationError, match="Cette ligne est sur le garage"):
        transferer(ligne, honda)
    piece = services.add_part_to_order(label="Chaîne", vehicle=honda)
    with pytest.raises(ValidationError, match="pas encore payée"):
        transferer(piece, honda)


def test_same_vehicle_and_page_left_open_are_refused(disque, gg, honda, make_vehicle, axel):
    with pytest.raises(ValidationError, match="déjà sur ce véhicule"):
        transferer(disque, gg)
    ktm = acheter(make_vehicle(brand="KTM", model="SX"), axel)
    transferer(disque, honda, from_vehicle_id=gg.pk)
    # Un second onglet ouvert quand la pièce était encore sur la GG.
    with pytest.raises(ValidationError, match="a changé de véhicule entre-temps"):
        transferer(disque, ktm, from_vehicle_id=gg.pk)
    assert PartTransfer.objects.count() == 1


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


def adresse(ligne):
    return f"/depenses/lignes/{ligne.pk}/transferer/"


def donnees(vers, depuis):
    return {"to_vehicle": vers.pk, "date": "2026-11-10", "notes": "", "from_vehicle": depuis.pk}


def test_expense_page_offers_the_transfer(as_mathis, disque, gg):
    page = as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    assert "Transférer sur un autre véhicule" in page
    assert adresse(disque) in page
    # L'achat ne se transfère pas : sa dépense ne le propose pas.
    achat = vehicle_services.purchase_lines(gg).get()
    assert adresse(achat) not in as_mathis.get(achat.expense.get_absolute_url()).content.decode()


def test_transfer_form_proposes_the_other_vehicles(as_mathis, disque, gg, honda):
    page = as_mathis.get(adresse(disque)).content.decode()
    assert "Transférer la ligne" in page
    assert f'value="{honda.pk}"' in page
    assert f'<option value="{gg.pk}"' not in page
    assert "le solde entre vous ne bouge pas" in page


def test_transfer_is_recorded_from_the_screen(as_mathis, disque, gg, honda):
    reponse = as_mathis.post(adresse(disque), donnees(honda, gg))
    assert reponse.status_code == 302
    assert reponse.url == disque.expense.get_absolute_url()
    assert messages_of(reponse) == [f"La ligne « Disque » est passée sur {honda.display_name}."]
    disque.refresh_from_db()
    assert disque.vehicle == honda

    page = as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    assert f"du {gg.display_name} vers le {honda.display_name}" in page
    fiche_tm = as_mathis.get(gg.get_absolute_url()).content.decode()
    assert "Pièces transférées" in fiche_tm
    assert "donnée au" in fiche_tm
    assert "reçue du" in as_mathis.get(honda.get_absolute_url()).content.decode()


def test_locked_line_page_gives_the_reason(as_mathis, disque, gg, mathis):
    vendre(gg, mathis)
    assert "Transférer sur un autre véhicule" not in (
        as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    )
    reponse = as_mathis.get(adresse(disque))
    assert reponse.status_code == 302
    assert messages_of(reponse) == [
        f"{gg.display_name} est vendu : ses dépenses sont verrouillées. Rouvre sa fiche pour "
        "les corriger."
    ]


def test_vehicle_sold_while_the_page_was_open_is_refused_under_the_field(
    as_mathis, disque, gg, honda, axel
):
    vendre(honda, axel)
    reponse = as_mathis.post(adresse(disque), donnees(honda, gg))
    assert reponse.status_code == 200
    assert "est vendu et ses dépenses sont verrouillées" in reponse.content.decode()
    disque.refresh_from_db()
    assert disque.vehicle == gg


def test_page_left_open_after_a_transfer_gives_a_message(
    as_mathis, disque, gg, honda, make_vehicle, axel
):
    ktm = acheter(make_vehicle(brand="KTM", model="SX"), axel)
    transferer(disque, honda)
    reponse = as_mathis.post(adresse(disque), donnees(ktm, gg))
    assert reponse.status_code == 302
    assert messages_of(reponse) == [services.TRANSFER_MOVED]


def test_vehicle_is_required(as_mathis, disque, gg, honda):
    reponse = as_mathis.post(adresse(disque), {"date": "2026-11-10", "from_vehicle": gg.pk})
    assert "Choisis le véhicule qui reçoit la pièce." in reponse.content.decode()


def test_read_only_refuses_the_transfer(as_mathis, disque, gg, honda, settings):
    settings.READ_ONLY = True
    assert "Transférer sur un autre véhicule" not in (
        as_mathis.get(disque.expense.get_absolute_url()).content.decode()
    )
    assert as_mathis.get(adresse(disque)).status_code == 403
    assert as_mathis.post(adresse(disque), donnees(honda, gg)).status_code == 403
    assert not PartTransfer.objects.exists()


def test_journal_shows_one_entry_for_the_transfer(as_mathis, disque, gg, honda):
    """Un clic, une entrée (D78) : le transfert et le changement de véhicule de la ligne."""
    as_mathis.post(adresse(disque), donnees(honda, gg))
    entrees = journal.journal_page(1).entries
    assert not [e for e in entrees if e.key[0] == "transfert"]
    textes = [ligne.text for ligne in entrees[0].lines]
    assert entrees[0].key == ("depense", disque.expense_id)
    assert any(t.startswith("a enregistré le transfert de « Disque »") for t in textes)
    assert any(t.startswith("a modifié la ligne « Disque »") for t in textes)
    assert entrees[0].link_label == "Voir la dépense"


def test_journal_still_reads_a_transfer_whose_line_was_removed(disque, gg, honda):
    transferer(disque, honda)
    disque.soft_delete()
    for page in (
        journal.journal_page(1, expense=disque.expense),
        journal.journal_page(1, vehicle=gg),
        journal.journal_page(1, vehicle=honda),
    ):
        textes = [ligne.text for e in page.entries for ligne in e.lines]
        assert any("le transfert de « Disque »" in t for t in textes)


# ---------------------------------------------------------------------------
# Relecture
# ---------------------------------------------------------------------------


def test_page_left_open_then_sent_to_where_the_line_now_is(as_mathis, disque, gg, honda):
    """Onglet A ouvert sur la GG ; l'onglet B transfère vers la Honda ; A envoie la Honda :
    « recharge la page », pas un faux « abandonné »."""
    transferer(disque, honda)
    reponse = as_mathis.post(adresse(disque), donnees(honda, gg))
    assert reponse.status_code == 302
    assert messages_of(reponse) == [services.TRANSFER_MOVED]
    assert PartTransfer.objects.count() == 1


def test_expense_put_in_the_trash_while_the_page_was_open(as_mathis, disque, gg, honda):
    services.trash_expense(disque.expense)
    reponse = as_mathis.post(adresse(disque), donnees(honda, gg))
    assert reponse.status_code == 302
    assert reponse.url == gg.get_absolute_url()
    assert messages_of(reponse) == ["Cette dépense est à la corbeille : restaure-la d'abord."]
    assert not PartTransfer.objects.exists()


def test_trashed_line_leaves_the_sheet_trace(as_mathis, disque, gg, honda):
    transferer(disque, honda)
    assert list(services.vehicle_transfers(gg))
    services.trash_expense(disque.expense)
    assert not list(services.vehicle_transfers(gg))
    assert "Pièces transférées" not in as_mathis.get(gg.get_absolute_url()).content.decode()


def test_line_leaves_a_vehicle_in_the_trash(disque, gg, honda):
    """D77 : sortir une ligne d'un véhicule à la corbeille reste possible."""
    Vehicle.all_objects.filter(pk=gg.pk).update(deleted_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC))
    transferer(disque, honda)
    disque.refresh_from_db()
    assert disque.vehicle == honda


def test_vehicle_waiting_for_its_purchase_or_reserved_receives_a_part(
    disque, honda, make_vehicle, mathis
):
    en_attente = make_vehicle(brand="Beta", model="RR")
    transferer(disque, en_attente)
    sale_services.record_deposit(
        honda,
        date=DATE,
        receipts=[Payment(mathis, D("100"))],
        buyer=None,
        partner=mathis,
    )
    honda.refresh_from_db()
    assert honda.status == Vehicle.Status.RESERVED
    transferer(disque, honda)
    assert PartTransfer.objects.count() == 2


def test_form_says_when_no_vehicle_can_receive_the_line(as_mathis, disque, gg):
    page = as_mathis.get(adresse(disque)).content.decode()
    assert "Aucun autre véhicule ne peut recevoir cette ligne" in page
    assert "Transférer la ligne" not in page

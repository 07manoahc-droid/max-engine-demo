"""Tests de la lecture de la base (`queries.py`) et de la commande `controle_comptes`."""

import dataclasses
import datetime as dt
from decimal import Decimal as D
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounting import queries, services
from accounting.management.commands.controle_comptes import Command
from accounting.models import Settlement
from core.formatting import format_euros
from core.models import ExpenseCategory
from expenses.models import Expense, ExpenseLine, ExpensePayment, Refund
from sales import services as sale_services
from sales.models import Deposit, DepositReceipt, Sale, SaleReceipt
from sales.tests.helpers import create_deposit
from vehicles.models import StatusChange, Vehicle

from . import excel_reference as excel


def run_command() -> str:
    sortie = StringIO()
    call_command("controle_comptes", stdout=sortie)
    return sortie.getvalue()


def mark_sold(*vehicules) -> None:
    """Les ventes de ces tests sont créées directement : le statut suit à la main."""
    Vehicle.objects.filter(pk__in=[v.pk for v in vehicules]).update(status=Vehicle.Status.SOLD)


def create_expense(data: services.ExpenseInput, partners, vehicles, category) -> Expense:
    """Enregistre en base une dépense décrite par les dataclasses des services."""
    depense = Expense.objects.create(
        total_paid=data.total_paid, order_number=data.key or "", is_personal=data.is_personal
    )
    for paiement in data.payments:
        ExpensePayment.objects.create(
            expense=depense, partner=partners[paiement.partner], amount=paiement.amount
        )
    for ligne in data.lines:
        ligne_en_base = ExpenseLine.objects.create(
            expense=depense,
            vehicle=vehicles.get(ligne.vehicle),
            category=category,
            label="Pièce",
            quantity=ligne.quantity,
            unit_price=ligne.unit_price,
        )
        for remboursement in ligne.refunds:
            Refund.objects.create(
                line=ligne_en_base,
                amount=remboursement.amount,
                refunded_to=partners[remboursement.partner],
            )
    return depense


@pytest.fixture
def partners(mathis, axel):
    return {excel.M: mathis, excel.A: axel}


@pytest.fixture
def excel_db(partners, make_vehicle):
    """Les 18 dépenses de l'Excel, enregistrées en base."""
    vehicules = {
        excel.YAMAHA: make_vehicle(brand="Yamaha", model="YZ250"),
        excel.GG: make_vehicle(brand="GasGas", model="MC250"),
        excel.SHERCO: make_vehicle(brand="Sherco", model="80"),
    }
    pieces = ExpenseCategory.objects.get(code="parts")
    for depense in excel.EXPENSES:
        create_expense(depense, partners, vehicules, pieces)
    return vehicules


def test_empty_database_is_consistent(db):
    sortie = run_command()
    assert "comptes à l'équilibre" in sortie
    assert "Aucune dépense." in sortie
    assert "Les comptes sont cohérents." in sortie


@pytest.mark.finance
def test_excel_data_read_from_database(excel_db, mathis, axel):
    """Cas 5 depuis la base : mêmes positions, même solde, mêmes coûts."""
    data = queries.load_accounts()
    positions = queries.positions(data)
    couts = services.vehicle_costs(data.expenses)

    assert positions[mathis.pk].paid_out == D("1564.15")
    assert positions[axel.pk].paid_out == D("1153.27")
    assert services.balance(positions) == services.Balance(axel.pk, mathis.pk, D("205.44"))
    for numero, (total, part_mathis, part_axel) in excel.EXPECTED_COSTS.items():
        cout = couts[excel_db[numero].pk]
        assert cout.total == total
        assert cout.by_partner == {mathis.pk: part_mathis, axel.pk: part_axel}
    assert queries.consistency_problems(data) == []


def test_command_prints_balance_and_costs(excel_db):
    sortie = run_command()

    assert f"Axel doit {format_euros(D('205.44'))} à Mathis" in sortie
    assert f"Mathis : sorti {format_euros(D('1564.15'))}" in sortie
    assert (
        f"n°2 · GasGas MC250 : {format_euros(D('1421.02'))} "
        f"(Mathis {format_euros(D('767.75'))}, Axel {format_euros(D('653.27'))})"
    ) in sortie
    assert (
        "3 véhicules, 18 dépenses, 25 lignes, 0 pièces à commander, 20 paiements, 1 remboursements"
        in sortie
    )
    assert "Les comptes sont cohérents." in sortie


@pytest.mark.finance
def test_trash_is_left_out_of_the_accounts(excel_db, partners, mathis):
    """Une dépense ou une ligne à la corbeille ne compte plus."""
    pieces = ExpenseCategory.objects.get(code="parts")
    en_trop = create_expense(
        services.ExpenseInput(
            D("50.00"),
            (services.Payment(excel.M, D("50.00")),),
            (services.LineInput(D("1"), D("50.00"), excel.YAMAHA),),
        ),
        partners,
        excel_db,
        pieces,
    )
    en_trop.soft_delete()

    data = queries.load_accounts()
    assert queries.positions(data)[mathis.pk].paid_out == D("1564.15")


@pytest.mark.finance
def test_cancelled_sale_and_kept_deposit_from_database(excel_db, mathis, axel):
    """Vente annulée : ignorée. Acompte conservé : encaissement de Mathis et marge (D32)."""
    gg = excel_db[excel.GG]
    annulee = Sale.objects.create(vehicle=gg, price=D("2000"), cancelled_at=timezone.now())
    SaleReceipt.objects.create(sale=annulee, partner=axel, amount=D("1800"))
    create_deposit(vehicle=gg, amount=D("200"), received_by=mathis, status=Deposit.Status.KEPT)

    data = queries.load_accounts()
    positions = queries.positions(data)
    assert positions[mathis.pk].received == D("200.00")
    assert positions[axel.pk].received == D("0.00")
    assert queries.vehicle_margins(queries.load_report(data)) == {}
    assert queries.consistency_problems(data) == []

    revente = Sale.objects.create(vehicle=gg, price=D("2500"))
    SaleReceipt.objects.create(sale=revente, partner=axel, amount=D("2500"))
    mark_sold(gg)
    data = queries.load_accounts()
    marges = queries.vehicle_margins(queries.load_report(data))
    assert marges == {gg.pk: D("2500") + D("200") - D("1421.02")}
    assert f"marge {format_euros(D('1278.98'))}" in run_command()


def test_settlement_settles_the_excel_balance(excel_db, mathis, axel):
    Settlement.objects.create(from_partner=axel, to_partner=mathis, amount=D("205.44"))
    assert "comptes à l'équilibre" in run_command()


def test_command_fails_on_inconsistent_expense(excel_db, mathis):
    """Paiements différents du montant payé : la commande signale et sort en erreur."""
    depense = Expense.objects.order_by("id").first()
    ExpensePayment.objects.filter(expense=depense, partner=mathis).update(amount=D("200.00"))

    sortie = StringIO()
    with pytest.raises(CommandError, match="1 incohérence"):
        call_command("controle_comptes", stdout=sortie)
    assert f"dépense n°{depense.pk} : les paiements" in sortie.getvalue()
    # Le bilan est faussé par la même erreur : ses contrôles ne la répètent pas.
    assert "argent mal rangé" not in sortie.getvalue()


def test_command_fails_on_incomplete_sale(excel_db):
    vente = Sale.objects.create(vehicle=excel_db[excel.SHERCO], price=D("900"))
    sortie = StringIO()
    with pytest.raises(CommandError):
        call_command("controle_comptes", stdout=sortie)
    assert f"vente n°{vente.pk} : encaissements" in sortie.getvalue()


def test_command_fails_without_two_active_partners(excel_db, axel):
    axel.is_active = False
    axel.save()
    sortie = StringIO()
    with pytest.raises(CommandError):
        call_command("controle_comptes", stdout=sortie)
    assert "il faut exactement deux associés actifs" in sortie.getvalue()


@pytest.mark.finance
def test_trashed_line_and_refund_of_an_active_expense_are_left_out(partners, make_vehicle):
    """Sur une dépense active, une ligne ou un remboursement à la corbeille ne compte plus."""
    vehicule = make_vehicle()
    pieces = ExpenseCategory.objects.get(code="parts")
    remboursement = services.RefundInput(excel.M, D("20.00"))
    depense = create_expense(
        services.ExpenseInput(
            D("30.00"),
            (services.Payment(excel.M, D("30.00")),),
            (
                services.LineInput(D("1"), D("10.00"), 1),
                services.LineInput(D("1"), D("20.00"), 1, refunds=(remboursement,)),
            ),
        ),
        partners,
        {1: vehicule},
        pieces,
    )
    Refund.objects.get(line__expense=depense).soft_delete()
    ExpenseLine.objects.filter(expense=depense).order_by("id").first().soft_delete()

    (lue,) = queries.load_accounts().expenses
    assert len(lue.lines) == 1
    assert lue.lines[0].refunds == ()
    # La ligne restante porte tout le montant payé.
    assert services.vehicle_costs([lue])[vehicule.pk].total == D("30.00")


@pytest.mark.finance
def test_applied_deposit_and_trade_in_from_database(excel_db, mathis, axel, make_vehicle):
    """Cas 11 et 13 lus en base : acompte déduit de la vente, reprise d'un véhicule."""
    gg, sherco = excel_db[excel.GG], excel_db[excel.SHERCO]
    moto_125 = make_vehicle(brand="Yamaha", model="125")
    vente = Sale.objects.create(
        vehicle=gg, price=D("2000"), trade_in_vehicle=moto_125, trade_in_value=D("700")
    )
    SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("1300"))
    vente_sherco = Sale.objects.create(vehicle=sherco, price=D("600"))
    SaleReceipt.objects.create(sale=vente_sherco, partner=axel, amount=D("400"))
    create_deposit(
        vehicle=sherco,
        amount=D("200"),
        received_by=mathis,
        status=Deposit.Status.APPLIED,
        sale=vente_sherco,
    )
    trade_in_purchase(moto_125, {mathis: "350", axel: "350"})
    mark_sold(gg, sherco)

    data = queries.load_accounts()
    assert queries.consistency_problems(data) == []
    positions = queries.positions(data)
    # Reçu : reprise 350 € chacun ; Axel 1 300 + 400 € ; Mathis l'acompte de 200 €.
    assert positions[mathis.pk].received == D("550.00")
    assert positions[axel.pk].received == D("2050.00")


def trade_in_purchase(vehicule, payments: dict, total=None) -> Expense:
    """L'achat d'un véhicule repris, enregistré directement : `payments` = {associé: montant}."""
    paye = sum((D(montant) for montant in payments.values()), D("0"))
    depense = Expense.objects.create(total_paid=D(total) if total else paye)
    for associe, montant in payments.items():
        ExpensePayment.objects.create(expense=depense, partner=associe, amount=D(montant))
    ExpenseLine.objects.create(
        expense=depense,
        vehicle=vehicule,
        category=ExpenseCategory.objects.get(code=ExpenseCategory.VEHICLE_PURCHASE),
        label="Achat",
        unit_price=depense.total_paid,
    )
    return depense


@pytest.fixture
def trade_in_sale(excel_db, axel, make_vehicle):
    """La GG vendue 2 000 € avec une 125 reprise pour 700,01 €, sans l'achat de la 125."""
    gg = excel_db[excel.GG]
    moto_125 = make_vehicle(brand="Yamaha", model="125")
    vente = Sale.objects.create(
        vehicle=gg, price=D("2000"), trade_in_vehicle=moto_125, trade_in_value=D("700.01")
    )
    SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("1299.99"))
    mark_sold(gg)
    return vente


@pytest.mark.finance
def test_neutral_trade_in_passes_the_check(trade_in_sale, mathis, axel):
    trade_in_purchase(trade_in_sale.trade_in_vehicle, {mathis: "350.01", axel: "350.00"})
    assert queries.consistency_problems(queries.load_accounts()) == []


@pytest.mark.finance
def test_trade_in_without_its_purchase_is_reported(trade_in_sale, mathis, axel):
    """Achat absent ou à la corbeille : la reprise n'est plus neutre, la commande le dit."""
    achat = trade_in_purchase(trade_in_sale.trade_in_vehicle, {mathis: "350.01", axel: "350"})
    achat.soft_delete()
    sortie = StringIO()
    with pytest.raises(CommandError, match="1 incohérence"):
        call_command("controle_comptes", stdout=sortie)
    assert (
        f"vente n°{trade_in_sale.pk} : le véhicule repris a 0 achat(s) au lieu d'un seul"
        in sortie.getvalue()
    )


@pytest.mark.finance
def test_trade_in_purchase_with_another_amount_or_split_is_reported(trade_in_sale, mathis, axel):
    trade_in_purchase(trade_in_sale.trade_in_vehicle, {mathis: "700.01"})
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"vente n°{trade_in_sale.pk} : l'achat du véhicule repris n'est pas payé comme la "
        f"reprise est partagée ({mathis.name} {format_euros(D('350.01'))}, "
        f"{axel.name} {format_euros(D('350.00'))})"
    ]
    ExpensePayment.objects.filter(partner=mathis).filter(amount=D("700.01")).update(
        amount=D("350.01")
    )
    ExpensePayment.objects.create(
        expense=Expense.objects.latest("id"), partner=axel, amount=D("349.99")
    )
    Expense.objects.filter(pk=Expense.objects.latest("id").pk).update(total_paid=D("700.00"))
    problemes = queries.consistency_problems(queries.load_accounts())
    assert any("ne fait pas la valeur de reprise" in texte for texte in problemes)


def test_applied_deposit_on_cancelled_sale_is_reported(excel_db, mathis):
    sherco = excel_db[excel.SHERCO]
    annulee = Sale.objects.create(vehicle=sherco, price=D("600"), cancelled_at=timezone.now())
    acompte = create_deposit(
        vehicle=sherco,
        amount=D("200"),
        received_by=mathis,
        status=Deposit.Status.APPLIED,
        sale=annulee,
    )
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"acompte n°{acompte.pk} : acompte déduit d'une vente annulée "
        "(à passer en « conservé » ou « rendu »)"
    ]


def test_inactive_partner_with_money_is_named(excel_db, axel):
    axel.is_active = False
    axel.save()
    problemes = queries.consistency_problems(queries.load_accounts())
    assert "Axel n'est plus un associé actif mais a de l'argent dans les comptes" in problemes


def test_sold_vehicle_without_a_sale_is_reported(excel_db):
    sherco = excel_db[excel.SHERCO]
    mark_sold(sherco)
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"{sherco.display_name} : vendu sans vente enregistrée"
    ]


def test_active_sale_on_a_vehicle_not_sold_is_reported(excel_db, axel):
    sherco = excel_db[excel.SHERCO]
    vente = Sale.objects.create(vehicle=sherco, price=D("600"))
    SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("600"))
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"{sherco.display_name} : vente en cours alors que le véhicule n'est pas vendu"
    ]


@pytest.mark.finance
def test_trashed_receipt_counts_nowhere(excel_db, mathis, axel):
    """Un encaissement retiré d'une vente part à la corbeille et ne compte plus (D49)."""
    sherco = excel_db[excel.SHERCO]
    vente = Sale.objects.create(vehicle=sherco, price=D("600"))
    retire = SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("600"))
    SaleReceipt.objects.create(sale=vente, partner=mathis, amount=D("600"))
    mark_sold(sherco)
    retire.soft_delete()

    data = queries.load_accounts()
    (lue,) = data.sales
    assert lue.receipts == (services.Receipt(mathis.pk, D("600.00")),)
    assert queries.positions(data)[axel.pk].received == D("0.00")
    assert queries.consistency_problems(data) == []


@pytest.mark.finance
def test_sale_accounts_give_the_vehicle_and_the_whole_garage(excel_db, mathis, axel):
    """Le récapitulatif ne parle que du véhicule, le solde de tout le garage."""
    sherco = excel_db[excel.SHERCO]
    vente = Sale.objects.create(vehicle=sherco, price=D("600"))
    SaleReceipt.objects.create(sale=vente, partner=mathis, amount=D("600"))
    mark_sold(sherco)

    comptes = queries.sale_accounts(sherco.pk, vente.pk)

    assert comptes.recap.margin == D("160.00")
    # Mathis a encaissé 600 € de plus : il passe de créancier (205,44 €) à débiteur.
    assert comptes.balance == services.Balance(mathis.pk, axel.pk, D("94.56"))
    assert comptes.partner_names[mathis.pk] == "Mathis"


def test_first_line_without_sales(excel_db):
    assert "0 vente active, 0 encaissement actif, 0 acompte en cours, 0 règlements" in run_command()


def test_first_line_does_not_make_a_cancelled_sale_look_active(excel_db, mathis, axel):
    """Une vente annulée et un acompte conservé : rien n'est encore vendu."""
    gg = excel_db[excel.GG]
    annulee = Sale.objects.create(vehicle=gg, price=D("2000"), cancelled_at=timezone.now())
    SaleReceipt.objects.create(sale=annulee, partner=axel, amount=D("1800"))
    create_deposit(vehicle=gg, amount=D("200"), received_by=mathis, status=Deposit.Status.KEPT)
    assert (
        "0 vente active (+ 1 annulée), 0 encaissement actif (+ 1 d'une vente annulée), "
        "0 acompte en cours (+ 1 terminé), 0 règlements"
    ) in run_command()


def test_first_line_counts_each_kind_in_the_plural(excel_db, mathis, axel):
    gg, sherco, yamaha = excel_db[excel.GG], excel_db[excel.SHERCO], excel_db[excel.YAMAHA]
    for vehicule in (gg, sherco):
        vente = Sale.objects.create(vehicle=vehicule, price=D("600"))
        SaleReceipt.objects.create(sale=vente, partner=axel, amount=D("300"))
        SaleReceipt.objects.create(sale=vente, partner=mathis, amount=D("300"))
    for _ in range(2):
        annulee = Sale.objects.create(vehicle=yamaha, price=D("600"), cancelled_at=timezone.now())
        SaleReceipt.objects.create(sale=annulee, partner=axel, amount=D("600"))
        create_deposit(
            vehicle=gg, amount=D("50"), received_by=mathis, status=Deposit.Status.REFUNDED
        )
    for vehicule in (sherco, yamaha):
        create_deposit(vehicle=vehicule, amount=D("50"), received_by=mathis)

    assert Command.volumes_des_ventes() == [
        "2 ventes actives (+ 2 annulées)",
        "4 encaissements actifs (+ 2 d'une vente annulée)",
        "2 acomptes en cours (+ 2 terminés)",
    ]


def test_first_line_does_not_count_a_trashed_receipt(excel_db, mathis):
    vente = Sale.objects.create(vehicle=excel_db[excel.GG], price=D("600"))
    retire = SaleReceipt.objects.create(sale=vente, partner=mathis, amount=D("600"))
    retire.soft_delete()
    assert Command.volumes_des_ventes()[1] == "0 encaissement actif"


def ligne_d61(nom, mis, revenu, garage, solde) -> str:
    """La ligne D61 d'un associé dans le compte rendu, sans bénéfice (données de l'Excel)."""
    return (
        f"{nom} : mis {format_euros(D(mis))} + part du bénéfice {format_euros(D('0'))}"
        f" = lui revient {format_euros(D(mis))} = déjà revenu {format_euros(D(revenu))}"
        f" + encore dans le garage {format_euros(D(garage))} {solde}"
    )


@pytest.mark.finance
def test_command_checks_d61_before_and_after_a_settlement(excel_db, mathis, axel):
    """Ce qui lui revient = déjà revenu + encore dans le garage ± solde, pour chacun,
    avant et après le règlement des 205,44 € dus par Axel."""
    avant = run_command()
    assert f"Bénéfice : {format_euros(D('0'))}" in avant
    assert f"Encore dans le garage : {format_euros(D('2717.42'))}" in avant
    a_recevoir = f"+ à recevoir {format_euros(D('205.44'))}"
    a_verser = f"− à verser {format_euros(D('205.44'))}"
    assert ligne_d61("Mathis", "1564.15", "0", "1358.71", a_recevoir) in avant
    assert ligne_d61("Axel", "1153.27", "0", "1358.71", a_verser) in avant
    assert "Les comptes sont cohérents." in avant

    Settlement.objects.create(from_partner=axel, to_partner=mathis, amount=D("205.44"))
    apres = run_command()
    equilibre = "(à l'équilibre)"
    assert ligne_d61("Mathis", "1564.15", "205.44", "1358.71", equilibre) in apres
    assert ligne_d61("Axel", "1153.27", "-205.44", "1358.71", equilibre) in apres
    assert "Les comptes sont cohérents." in apres

    rapport = queries.load_report()
    for ligne in rapport.partners.values():
        assert ligne.due_to_partner == ligne.already_back + ligne.still_in_garage + ligne.balance
        assert ligne.rounding_gap == D("0")


@pytest.mark.finance
def test_report_reads_the_last_abandon_date_and_pending_deposits(excel_db, axel):
    """D59 : la perte d'un abandon est datée de son dernier passage en « Abandonné ».
    L'acompte en cours d'un véhicule au garage est déjà dans une poche (D61)."""
    gg, sherco = excel_db[excel.GG], excel_db[excel.SHERCO]
    Vehicle.objects.filter(pk=gg.pk).update(status=Vehicle.Status.ABANDONED)
    for jour in (dt.date(2025, 6, 1), dt.date(2026, 2, 3)):
        StatusChange.objects.create(
            vehicle=gg,
            from_status=Vehicle.Status.IN_REPAIR,
            to_status=Vehicle.Status.ABANDONED,
            changed_at=timezone.make_aware(dt.datetime.combine(jour, dt.time(12))),
        )
    create_deposit(vehicle=sherco, amount=D("50"), received_by=axel)
    Vehicle.objects.filter(pk=sherco.pk).update(status=Vehicle.Status.RESERVED)

    rapport = queries.load_report()
    (perdu,) = [lg for lg in rapport.rows if lg.group == services.GROUP_ABANDONED]
    assert perdu.vehicle == gg.pk
    assert perdu.abandoned_on == dt.date(2026, 2, 3)
    assert perdu.loss == D("1421.02")
    assert rapport.buckets.pending_deposits == D("50.00")
    assert services.buckets_problems(queries.positions(rapport.data), rapport.buckets) == []
    assert "Les comptes sont cohérents." in run_command()


def test_command_reports_money_out_of_place(excel_db, monkeypatch):
    """Si le bilan oublie un montant, `controle_comptes` le signale et sort en erreur."""
    vrai = services.money_buckets
    monkeypatch.setattr(
        services,
        "money_buckets",
        lambda *args: dataclasses.replace(vrai(*args), in_garage=D("0")),
    )
    sortie = StringIO()
    with pytest.raises(CommandError):
        call_command("controle_comptes", stdout=sortie)
    assert "argent mal rangé" in sortie.getvalue()
    assert "ce qui lui revient" in sortie.getvalue()


def test_abandoned_vehicle_without_status_change_uses_its_creation_date(excel_db):
    """Véhicule repris de l'Excel déjà abandonné : pas de changement de statut enregistré."""
    gg = excel_db[excel.GG]
    Vehicle.objects.filter(pk=gg.pk).update(status=Vehicle.Status.ABANDONED)
    assert queries.vehicle_states()[gg.pk].abandoned_on == timezone.localdate(gg.created_at)


def test_report_has_no_partner_lines_without_two_active_partners(excel_db, axel):
    axel.is_active = False
    axel.save()
    assert queries.load_report().partners is None


def test_sale_accounts_are_none_when_a_partner_left(excel_db, mathis, axel):
    sherco = excel_db[excel.SHERCO]
    vente = Sale.objects.create(vehicle=sherco, price=D("600"))
    axel.is_active = False
    axel.save()
    assert queries.sale_accounts(sherco.pk, vente.pk) is None


# --- Acompte en plusieurs versements (D89) -----------------------------------------------


@pytest.fixture
def shared_deposit(excel_db, mathis, axel):
    """La GG réservée par un acompte de 300 € : 200 € reçus par Mathis, 100 € par Axel."""
    gg = excel_db[excel.GG]
    Vehicle.objects.filter(pk=gg.pk).update(status=Vehicle.Status.RESERVED)
    acompte = create_deposit(vehicle=gg, amount=D("200"), received_by=mathis)
    DepositReceipt.objects.create(deposit=acompte, partner=axel, amount=D("100"))
    return acompte


@pytest.mark.finance
def test_shared_deposit_counts_for_each_receiver(shared_deposit, mathis, axel):
    """Chaque versement entre dans ce qu'a reçu son associé ; l'acompte en cours est un seul
    acompte, pas deux (aucun faux « réservé avec 2 acomptes »)."""
    data = queries.load_accounts()
    avant = queries.positions(data)
    assert queries.consistency_problems(data) == []
    assert {(a.partner, a.amount, a.key) for a in data.deposits} == {
        (mathis.pk, D("200.00"), shared_deposit.pk),
        (axel.pk, D("100.00"), shared_deposit.pk),
    }
    # Le même argent reçu par Mathis seul : 100 € de plus chez lui, 100 € de moins chez Axel.
    sans = dataclasses.replace(data, deposits=())
    sans_positions = queries.positions(sans)
    assert avant[mathis.pk].received - sans_positions[mathis.pk].received == D("200.00")
    assert avant[axel.pk].received - sans_positions[axel.pk].received == D("100.00")
    assert "Les comptes sont cohérents." in run_command()


def test_first_line_counts_the_receipts_of_a_shared_deposit(shared_deposit):
    assert Command.volumes_des_ventes()[2] == "1 acompte en cours (2 versements)"


@pytest.mark.finance
def test_trashed_receipt_of_a_deposit_counts_nowhere(shared_deposit, axel):
    versement = shared_deposit.receipts.get(partner=axel)
    sale_services.remove_deposit_receipt(versement)
    data = queries.load_accounts()
    assert [(a.partner, a.amount) for a in data.deposits] == [
        (shared_deposit.receipts.get().partner_id, D("200.00"))
    ]
    assert queries.consistency_problems(data) == []
    assert Command.volumes_des_ventes()[2] == "1 acompte en cours"


def test_deposit_without_receipt_is_reported(excel_db):
    """Écrit à la main (administration) : un acompte sans versement ne compte nulle part."""
    gg = excel_db[excel.GG]
    acompte = Deposit.objects.create(vehicle=gg, status=Deposit.Status.REFUNDED)
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"acompte n°{acompte.pk} : aucun versement : l'acompte ne compte nulle part"
    ]


def test_active_deposit_without_receipt_is_said_once(excel_db):
    """Relecture : un acompte en cours sans versement sur un véhicule réservé est signalé
    une fois, sans faux « réservé avec 0 acompte(s) »."""
    gg = excel_db[excel.GG]
    Vehicle.objects.filter(pk=gg.pk).update(status=Vehicle.Status.RESERVED)
    acompte = Deposit.objects.create(vehicle=gg)
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"acompte n°{acompte.pk} : aucun versement : l'acompte ne compte nulle part"
    ]


def test_deposit_with_its_old_amount_still_filled_is_reported(excel_db, mathis):
    gg = excel_db[excel.GG]
    acompte = create_deposit(
        vehicle=gg, amount=D("50"), received_by=mathis, status=Deposit.Status.REFUNDED
    )
    Deposit.objects.filter(pk=acompte.pk).update(legacy_amount=D("50"), legacy_received_by=mathis)
    assert queries.consistency_problems(queries.load_accounts()) == [
        f"acompte n°{acompte.pk} : ancien montant encore rempli (non recopié en versement)"
    ]


def test_problem_of_an_applied_deposit_is_said_once(excel_db, mathis, axel):
    """Deux versements d'un acompte déduit d'une vente annulée : un seul message."""
    gg = excel_db[excel.GG]
    annulee = Sale.objects.create(vehicle=gg, price=D("2000"), cancelled_at=timezone.now())
    acompte = create_deposit(
        vehicle=gg, amount=D("50"), received_by=mathis, status=Deposit.Status.APPLIED, sale=annulee
    )
    DepositReceipt.objects.create(deposit=acompte, partner=axel, amount=D("10"))
    problemes = queries.consistency_problems(queries.load_accounts())
    assert [p for p in problemes if p.startswith(f"acompte n°{acompte.pk}")] == [
        f"acompte n°{acompte.pk} : acompte déduit d'une vente annulée "
        "(à passer en « conservé » ou « rendu »)"
    ]

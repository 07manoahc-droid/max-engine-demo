"""Migrations `sales.0004` et `sales.0005` (D89) : chaque acompte devient un acompte à un
versement, sans changer les comptes ; le retour arrière remet l'ancienne forme, ou refuse
quand elle ne saurait pas garder un acompte en plusieurs versements.

Les migrations sont jouées sur la base de test elle-même (en transaction réelle) ; elle est
toujours remise à la dernière migration à la fin, et les associés créés par migration sont
remis ensuite (`serialized_rollback`).
"""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounting import queries, services
from accounting.services import Payment
from core.models import Partner
from sales import services as sale_services
from sales.models import Deposit

AVANT = [("sales", "0003_encaissements_suppression_logique")]
APRES = [("sales", "0005_recopie_acomptes_en_versements")]

migrations_jouees = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def migrer(cible):
    """Joue les migrations jusqu'à `cible` et renvoie l'état des modèles à ce point."""
    executeur = MigrationExecutor(connection)
    executeur.migrate(cible)
    executeur.loader.build_graph()
    return executeur.loader.project_state(cible).apps


@pytest.fixture
def ancienne_forme():
    """La base mise avant les versements, avec deux acomptes « à l'ancienne » : 200 € reçus
    par Mathis (en cours) et 80 € reçus par Axel (conservé). Toujours remise à jour après."""
    apps = migrer(AVANT)
    try:
        Partner = apps.get_model("core", "Partner")
        VehicleType = apps.get_model("core", "VehicleType")
        Vehicle = apps.get_model("vehicles", "Vehicle")
        Deposit = apps.get_model("sales", "Deposit")
        mathis = Partner.objects.get(name="Mathis")
        axel = Partner.objects.get(name="Axel")
        moto = VehicleType.objects.get_or_create(name="Moto")[0]
        gg = Vehicle.objects.create(
            number=1, vehicle_type=moto, brand="GG", model="250", status="RESERVED"
        )
        sherco = Vehicle.objects.create(number=2, vehicle_type=moto, brand="Sherco", model="80")
        Deposit.objects.create(
            vehicle=gg, amount=D("200"), received_by=mathis, date=dt.date(2026, 10, 30)
        )
        Deposit.objects.create(
            vehicle=sherco,
            amount=D("80"),
            received_by=axel,
            date=dt.date(2026, 10, 2),
            status="KEPT",
        )
        yield {"mathis": mathis.pk, "axel": axel.pk}
    finally:
        migrer(executeur_feuilles())


def executeur_feuilles():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@migrations_jouees
@pytest.mark.finance
def test_each_old_deposit_becomes_one_receipt(ancienne_forme):
    apps = migrer(APRES)
    Deposit = apps.get_model("sales", "Deposit")
    DepositReceipt = apps.get_model("sales", "DepositReceipt")

    versements = {
        (v.deposit.vehicle.brand, v.partner_id, v.amount, v.date)
        for v in DepositReceipt.objects.select_related("deposit__vehicle")
    }
    assert versements == {
        ("GG", ancienne_forme["mathis"], D("200.00"), dt.date(2026, 10, 30)),
        ("Sherco", ancienne_forme["axel"], D("80.00"), dt.date(2026, 10, 2)),
    }
    assert not Deposit.objects.filter(legacy_amount__isnull=False).exists()
    assert not Deposit.objects.filter(legacy_received_by__isnull=False).exists()

    # Les comptes lus par le logiciel : Mathis a reçu 200 €, Axel 80 €, sans incohérence
    # d'acompte (contrôle de la sous-étape : mêmes chiffres qu'avant).
    migrer(executeur_feuilles())
    data = queries.load_accounts()
    positions = queries.positions(data)
    assert positions[ancienne_forme["mathis"]].received == D("200.00")
    assert positions[ancienne_forme["axel"]].received == D("80.00")
    assert services.kept_deposits_total(data.deposits) == D("80.00")
    assert not [p for p in queries.consistency_problems(data) if p.startswith("acompte")]


@migrations_jouees
def test_going_back_restores_the_old_form(ancienne_forme):
    migrer(APRES)
    apps = migrer(AVANT)
    Deposit = apps.get_model("sales", "Deposit")
    anciens = {
        (a.vehicle.brand, a.received_by_id, a.amount, a.date, a.status)
        for a in Deposit.objects.select_related("vehicle")
    }
    assert anciens == {
        ("GG", ancienne_forme["mathis"], D("200.00"), dt.date(2026, 10, 30), "ACTIVE"),
        ("Sherco", ancienne_forme["axel"], D("80.00"), dt.date(2026, 10, 2), "KEPT"),
    }


@migrations_jouees
def test_going_back_is_refused_with_two_receipts(ancienne_forme):
    apps = migrer(APRES)
    DepositReceipt = apps.get_model("sales", "DepositReceipt")
    premier = DepositReceipt.objects.order_by("id").first()
    DepositReceipt.objects.create(
        deposit_id=premier.deposit_id, partner_id=ancienne_forme["axel"], amount=D("10")
    )
    with pytest.raises(RuntimeError, match="versements, l'ancienne forme n'en garde qu'un"):
        migrer(AVANT)
    # Rien n'a bougé : les deux versements sont toujours là.
    assert DepositReceipt.objects.filter(deposit_id=premier.deposit_id).count() == 2


@migrations_jouees
def test_going_back_is_refused_with_a_receipt_in_the_trash(ancienne_forme):
    apps = migrer(APRES)
    DepositReceipt = apps.get_model("sales", "DepositReceipt")
    premier = DepositReceipt.objects.order_by("id").first()
    DepositReceipt.objects.create(
        deposit_id=premier.deposit_id,
        partner_id=ancienne_forme["axel"],
        amount=D("10"),
        deleted_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    )
    with pytest.raises(RuntimeError, match="à la corbeille"):
        migrer(AVANT)


def avec_le_vrai_code():
    """La base remise à la dernière migration, pour saisir avec les vrais services (qui
    écrivent l'historique, contrairement aux modèles des migrations)."""
    migrer(executeur_feuilles())
    return Deposit.objects.get(status=Deposit.Status.ACTIVE)


def assert_retour_refuse():
    with pytest.raises(RuntimeError, match="enregistrés ou corrigés depuis la mise à jour"):
        migrer(AVANT)
    # Rien n'a été défait : la base est toujours à la dernière migration.
    assert MigrationExecutor(connection).migration_plan(executeur_feuilles()) == []


@migrations_jouees
def test_going_back_is_refused_after_a_receipt_added_in_2_0(ancienne_forme):
    """Gardien : sans ce refus, le retour de `sales.0004` échouait à mi-chemin, puis un
    `migrate` recopiait les acomptes une seconde fois (argent compté deux fois)."""
    acompte = avec_le_vrai_code()
    sale_services.add_deposit_receipts(
        acompte,
        date=dt.date(2026, 11, 2),
        receipts=[Payment(Partner.objects.get(name="Axel"), D("50"))],
    )
    assert_retour_refuse()


@migrations_jouees
def test_going_back_is_refused_after_a_deposit_corrected_in_2_0(ancienne_forme):
    acompte = avec_le_vrai_code()
    sale_services.update_deposit(acompte, date=dt.date(2026, 10, 31), buyer=None)
    assert_retour_refuse()


@migrations_jouees
def test_copy_is_refused_when_receipts_already_exist(ancienne_forme):
    """Une base laissée à mi-chemin (versements présents, `sales.0005` non appliquée) : la
    recopie est refusée plutôt que de compter l'argent deux fois."""
    apps = migrer([("sales", "0004_versements_acompte")])
    Deposit_ = apps.get_model("sales", "Deposit")
    DepositReceipt = apps.get_model("sales", "DepositReceipt")
    acompte = Deposit_.objects.order_by("id").first()
    DepositReceipt.objects.create(
        deposit_id=acompte.pk, partner_id=ancienne_forme["mathis"], amount=D("200")
    )
    with pytest.raises(RuntimeError, match="compterait l'argent deux fois"):
        migrer(APRES)
    assert DepositReceipt.objects.count() == 1
    DepositReceipt.objects.all().delete()

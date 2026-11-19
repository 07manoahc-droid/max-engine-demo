"""Tests de l'export Excel complet (D65)."""

import datetime as dt
from decimal import Decimal as D
from io import BytesIO

import pytest
from django.db import models
from django.utils import timezone
from openpyxl import load_workbook

from accounting import export
from accounting.models import Settlement
from core.formatting import format_euros
from expenses.models import Expense, ExpenseLine
from sales.models import Deposit, DepositReceipt
from sales.tests.helpers import create_deposit
from vehicles.models import WorkLog

from .test_report_view import CETTE_ANNEE, abandonner, achat, vendre

FEUILLES = [
    "Bilan par véhicule",
    "Bilan par année",
    "Bilan par associé",
    "Solde",
    "Véhicules",
    "Dépenses",
    "Lignes de dépense",
    "Paiements",
    "Remboursements",
    "Transferts de pièce",
    "Ventes",
    "Encaissements",
    "Acomptes",
    "Versements d'acompte",
    "Règlements",
    "Changements de statut",
    "Interventions",
    "Choses à faire",
    "Annonces comparables",
    "Fichiers joints",
    "Contacts",
    "Fournisseurs",
    "Associés",
    "Types de véhicule",
    "Origines d'achat",
    "Catégories de dépense",
    "Paramètres",
]


def classeur():
    return load_workbook(BytesIO(export.build_workbook()))


def lignes(feuille) -> list[dict]:
    """Les lignes d'une feuille, chacune en dictionnaire « en-tête → cellule »."""
    entetes = [c.value for c in feuille[1]]
    return [dict(zip(entetes, rangee, strict=False)) for rangee in feuille.iter_rows(min_row=2)]


def montant(cellule) -> D:
    """Le montant d'une cellule, au centime (openpyxl relit les nombres en `float`)."""
    return D(str(cellule.value)).quantize(D("0.01"))


@pytest.fixture
def garage(make_vehicle, mathis, axel):
    """Une vente avec acompte conservé, un abandon, une dépense à la corbeille."""
    debut = dt.date(CETTE_ANNEE, 1, 10)
    yamaha = make_vehicle(brand="Yamaha", model="RM", purchased_on=debut)
    achat(yamaha, mathis, "2668.01", debut)
    create_deposit(vehicle=yamaha, amount=D("400"), received_by=axel, status=Deposit.Status.KEPT)
    vendre(yamaha, mathis, "3000", dt.date(CETTE_ANNEE, 3, 1))
    gg = make_vehicle(brand="GG", model="350")
    achat(gg, axel, "3500.50", dt.date(CETTE_ANNEE - 1, 9, 9))
    abandonner(gg, dt.date(CETTE_ANNEE, 2, 1))
    achat(make_vehicle(brand="Yamaha", model="YZ"), mathis, "99.99").soft_delete()
    return {"yamaha": yamaha, "gg": gg}


def test_workbook_has_the_report_then_one_sheet_per_table(garage):
    assert classeur().sheetnames == FEUILLES


@pytest.mark.finance
def test_report_sheets_give_the_amounts_to_the_cent(garage):
    cl = classeur()
    vehicules = {lg["Véhicule"].value: lg for lg in lignes(cl["Bilan par véhicule"])}
    yamaha = vehicules[garage["yamaha"].display_name]
    assert montant(yamaha["Coût de revient"]) == D("2668.01")
    assert montant(yamaha["Acomptes conservés"]) == D("400.00")
    assert montant(yamaha["Marge ou perte"]) == D("731.99")
    assert yamaha["Marge ou perte"].number_format == export.FORMAT_EUROS
    assert yamaha["Durée avant la vente (jours)"].value == 50
    gg = vehicules[garage["gg"].display_name]
    assert gg["Situation"].value == "Abandonné"
    assert montant(gg["Marge ou perte"]) == D("-3500.50")

    annees = {lg["Année"].value: lg for lg in lignes(cl["Bilan par année"])}
    assert montant(annees[CETTE_ANNEE]["Bénéfice"]) == D("-2768.51")  # 731,99 − 3 500,50
    assert montant(annees[CETTE_ANNEE - 1]["Investi"]) == D("3500.50")
    assert montant(annees[CETTE_ANNEE]["Pour Mathis"]) == D("-1384.26")

    associes = {lg["Depuis le début"].value: lg for lg in lignes(cl["Bilan par associé"])}
    assert montant(associes["Mis dans le garage"]["Mathis"]) == D("2668.01")
    assert montant(associes["Encaissé (ventes, acomptes, reprises)"]["Axel"]) == D("400.00")


@pytest.mark.finance
def test_balance_sheet_before_and_after_a_settlement(garage, mathis, axel):
    """Feuille « Solde » : avancé par chacun et phrase du solde, comme l'écran « Comptes »."""
    solde = {lg["Rubrique"].value: lg for lg in lignes(classeur()["Solde"])}
    # Mathis : 2 668,01 sortis, 3 000 reçus ; Axel : 3 500,50 sortis, 400 reçus.
    assert montant(solde["Avancé pour le garage (sorti − reçu)"]["Mathis"]) == D("-331.99")
    assert montant(solde["Avancé pour le garage (sorti − reçu)"]["Axel"]) == D("3100.50")
    phrase = f"Mathis doit {format_euros(D('1716.25'))} à Axel"
    assert phrase in classeur()["Solde"]["A11"].value

    Settlement.objects.create(from_partner=mathis, to_partner=axel, amount=D("1716.25"))
    solde = {lg["Rubrique"].value: lg for lg in lignes(classeur()["Solde"])}
    assert montant(solde["Règlements versés"]["Mathis"]) == D("1716.25")
    # D67 : « À l'équilibre », comme l'écran « Comptes » ; même nom de rubrique qu'au bilan.
    assert classeur()["Solde"]["A11"].value == "À l'équilibre"
    assert "Encaissé (ventes, acomptes, reprises)" in solde


def test_tables_keep_the_trash_with_its_date(garage):
    depenses = lignes(classeur()["Dépenses"])
    a_la_corbeille = [lg for lg in depenses if lg["Mis à la corbeille le"].value is not None]

    assert len(depenses) == 3
    assert len(a_la_corbeille) == 1
    assert a_la_corbeille[0]["Mis à la corbeille le"].number_format == export.FORMAT_DATE_HEURE
    assert montant(a_la_corbeille[0]["Montant payé"]) == D("99.99")
    assert isinstance(a_la_corbeille[0]["Date"].value, dt.datetime)
    assert a_la_corbeille[0]["Date"].number_format == export.FORMAT_DATE


def test_values_only_never_formulas(garage, mathis, axel):
    """Un texte qui commence par « = » reste un texte ; une quantité n'est pas en euros."""
    Expense.objects.filter(pk=Expense.objects.order_by("pk").first().pk).update(notes="=1+1")
    Settlement.objects.create(from_partner=axel, to_partner=mathis, amount=D("10"))
    cl = classeur()

    notes = [lg["Notes"] for lg in lignes(cl["Dépenses"]) if lg["Notes"].value]
    assert [(c.value, c.data_type) for c in notes] == [("=1+1", "s")]
    for feuille in cl.worksheets:
        for rangee in feuille.iter_rows():
            assert all(cellule.data_type != "f" for cellule in rangee)
    ligne = lignes(cl["Lignes de dépense"])[0]
    assert ligne["Quantité"].number_format == export.FORMAT_NOMBRE
    assert ligne["Prix unitaire"].number_format == export.FORMAT_EUROS
    assert lignes(cl["Règlements"])[0]["Versé par"].value == "Axel"


def test_only_money_fields_use_the_euro_format(garage):
    """Relecture de la 1.6 : les heures d'une intervention et les quantités sont des
    nombres simples ; le format euro est réservé aux champs d'argent."""
    WorkLog.objects.create(vehicle=garage["yamaha"], description="Vidange", hours=D("1.50"))
    cl = classeur()

    (intervention,) = lignes(cl["Interventions"])
    assert intervention["Heures"].number_format == export.FORMAT_NOMBRE
    assert intervention["Heures"].value == 1.5
    decimaux = [
        champ
        for _, modele in export.TABLES
        for champ in modele._meta.concrete_fields
        if isinstance(champ, models.DecimalField)
    ]
    montants = {f"{c.model.__name__}.{c.name}" for c in decimaux if export.est_montant(c)}
    assert {f"{c.model.__name__}.{c.name}" for c in decimaux} - montants == {
        "ExpenseLine.quantity",
        "WorkLog.hours",
    }
    assert "Expense.total_paid" in montants


def test_status_changes_are_exported(garage):
    changements = lignes(classeur()["Changements de statut"])
    assert [lg["Nouveau statut"].value for lg in changements] == ["Abandonné"]


def test_links_to_tables_use_their_number(garage):
    cl = classeur()
    depense = ExpenseLine.objects.order_by("pk").first().expense_id
    assert lignes(cl["Lignes de dépense"])[0]["Dépense (n°)"].value == depense
    assert lignes(cl["Lignes de dépense"])[0]["Véhicule"].value.startswith("n°")


def test_without_two_active_partners_the_partner_sheets_say_so(garage, axel):
    axel.is_active = False
    axel.save()
    cl = classeur()
    assert cl["Bilan par associé"]["A2"].value.endswith("préviens Mathis.")
    assert cl["Solde"]["A2"].value.endswith("préviens Mathis.")


def test_download_from_the_report_page(as_mathis, garage, settings):
    """Le seul bouton bleu du bilan ; permis en lecture seule, car il ne modifie rien.
    (Le mode lecture seule ne bloque encore aucune page, 1.7 : ce test le vérifiera alors.)"""
    page = as_mathis.get("/bilan/").content.decode()
    assert page.count('class="btn-primary"') == 1
    assert "Exporter en Excel" in page

    settings.READ_ONLY = True
    reponse = as_mathis.get("/bilan/export/")
    assert reponse.status_code == 200
    assert reponse["Content-Type"].startswith("application/vnd.openxmlformats")
    nom = f"MAx Engine - export du {timezone.localdate().isoformat()}.xlsx"
    assert nom in reponse["Content-Disposition"]
    assert "attachment" in reponse["Content-Disposition"]
    assert load_workbook(BytesIO(b"".join(reponse.streaming_content))).sheetnames == FEUILLES


@pytest.mark.finance
def test_deposit_receipts_have_their_sheet_and_the_deposit_its_total(garage, mathis):
    """D89 : un versement par ligne, corbeille comprise ; l'acompte donne le total de ses
    versements hors corbeille, et ses anciens champs vides."""
    acompte = Deposit.objects.get()
    retire = DepositReceipt.objects.create(deposit=acompte, partner=mathis, amount=D("25"))
    retire.soft_delete()
    DepositReceipt.objects.create(deposit=acompte, partner=mathis, amount=D("50.10"))

    feuilles = classeur()
    (ligne,) = lignes(feuilles["Acomptes"])
    assert montant(ligne["Total des versements"]) == D("450.10")
    assert ligne["Total des versements"].number_format == export.FORMAT_EUROS
    assert ligne["Montant (avant la 2.0)"].value is None
    versements = lignes(feuilles["Versements d'acompte"])
    assert sorted(montant(v["Montant"]) for v in versements) == [
        D("25.00"),
        D("50.10"),
        D("400.00"),
    ]
    assert {v["Acompte (n°)"].value for v in versements} == {acompte.pk}
    assert sum(v["Mis à la corbeille le"].value is not None for v in versements) == 1

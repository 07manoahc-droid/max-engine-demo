"""Export Excel complet (D65) : le bilan, puis une feuille par table, corbeille comprise.

Lecture seule, sans transaction : l'export marche aussi en mode lecture seule (D48).
Les montants viennent tels quels des services ou de la base (`Decimal`), au format euro ;
les dates sont de vraies dates. Le fichier ne contient que des valeurs, aucune formule :
un texte qui commence par « = » reste un texte.
"""

import datetime as dt
from decimal import Decimal
from io import BytesIO

from django.db import models
from django.utils import timezone
from django.utils.text import capfirst
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from core.models import (
    Attachment,
    Contact,
    ExpenseCategory,
    GarageSettings,
    Partner,
    PurchaseOrigin,
    Supplier,
    VehicleType,
)
from expenses.models import Expense, ExpenseLine, ExpensePayment, PartTransfer, Refund
from sales.models import Deposit, DepositReceipt, Sale, SaleReceipt
from vehicles.models import ComparableListing, StatusChange, Task, Vehicle, WorkLog

from . import queries, services
from .models import Settlement

# Format des montants : « 1 421,02 € » ; un montant négatif du bilan (perte) en rouge.
FORMAT_EUROS = '#,##0.00 "€";[Red]-#,##0.00 "€"'
FORMAT_DATE = "DD/MM/YYYY"
FORMAT_DATE_HEURE = "DD/MM/YYYY HH:MM"
# Une quantité ou des heures : un nombre simple (« 1 », « 1,5 »), jamais en euros.
FORMAT_NOMBRE = "General"
# Les champs d'argent ont tous cette taille (`MONEY` dans les modèles) ; les quantités et
# les heures en ont une autre.
TAILLE_MONTANT = (10, 2)
# Une cellule Excel ne peut pas contenir plus de caractères.
TEXTE_MAX = 32767

SITUATIONS = {
    services.GROUP_SOLD: "Vendu",
    services.GROUP_ABANDONED: "Abandonné",
    services.GROUP_GARAGE: "Au garage",
    services.GROUP_TRASH: "Fiche à la corbeille",
}

# Une feuille par table, toutes les données du logiciel (cahier des charges : « export
# complet ») : (titre de la feuille, modèle). Le gestionnaire de base lit tout, corbeille
# comprise. Les fichiers joints sont donnés par leur chemin dans `media/`.
TABLES = (
    ("Véhicules", Vehicle),
    ("Dépenses", Expense),
    ("Lignes de dépense", ExpenseLine),
    ("Paiements", ExpensePayment),
    ("Remboursements", Refund),
    ("Transferts de pièce", PartTransfer),
    ("Ventes", Sale),
    ("Encaissements", SaleReceipt),
    ("Acomptes", Deposit),
    ("Versements d'acompte", DepositReceipt),
    ("Règlements", Settlement),
    ("Changements de statut", StatusChange),
    ("Interventions", WorkLog),
    ("Choses à faire", Task),
    ("Annonces comparables", ComparableListing),
    ("Fichiers joints", Attachment),
    ("Contacts", Contact),
    ("Fournisseurs", Supplier),
    ("Associés", Partner),
    ("Types de véhicule", VehicleType),
    ("Origines d'achat", PurchaseOrigin),
    ("Catégories de dépense", ExpenseCategory),
    ("Paramètres", GarageSettings),
)

# Clés étrangères écrites par leur numéro (la feuille de la table liée le donne) ; les
# autres sont écrites par leur nom (véhicule « n°3 · … », associé, catégorie…).
PAR_NUMERO = (Expense, ExpenseLine, Sale, Settlement, Deposit)


def export_filename(today: dt.date) -> str:
    return f"MAx Engine - export du {today.isoformat()}.xlsx"


def build_workbook() -> bytes:
    """Le classeur complet, prêt à télécharger."""
    classeur = Workbook()
    classeur.remove(classeur.active)
    rapport = queries.load_report()
    associes = queries.active_partners() if rapport.partners is not None else []
    vehicules = Vehicle.all_objects.in_bulk([ligne.vehicle for ligne in rapport.rows])

    _feuille_vehicules(classeur, rapport, associes, vehicules)
    _feuille_annees(classeur, rapport, associes)
    _feuille_associes(classeur, rapport, associes)
    _feuille_solde(classeur, rapport, associes)
    for titre, modele in TABLES:
        _feuille_table(classeur, titre, modele)

    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


# ---------------------------------------------------------------------------
# Feuilles du bilan
# ---------------------------------------------------------------------------


def _feuille_vehicules(classeur, rapport, associes, vehicules) -> None:
    feuille = _nouvelle_feuille(
        classeur,
        "Bilan par véhicule",
        [
            "Véhicule",
            "Situation",
            *(f"Investi par {a.name}" for a in associes),
            "Coût de revient",
            "Prix de vente",
            "Date de vente",
            "Acomptes conservés",
            "Marge ou perte",
            "Durée avant la vente (jours)",
            "Abandonné le",
        ],
    )
    for ligne in sorted(rapport.rows, key=lambda r: vehicules[r.vehicle].number):
        _ecrire(
            feuille,
            [
                vehicules[ligne.vehicle].display_name,
                SITUATIONS[ligne.group],
                *(ligne.cost.by_partner.get(a.pk, services.ZERO) for a in associes),
                ligne.cost.total,
                ligne.sale.price if ligne.sale else None,
                ligne.sale.date if ligne.sale else None,
                ligne.kept_deposits,
                ligne.result,
                ligne.days,
                ligne.abandoned_on,
            ],
        )


def _feuille_annees(classeur, rapport, associes) -> None:
    feuille = _nouvelle_feuille(
        classeur,
        "Bilan par année",
        [
            "Année",
            "Investi",
            "Ventes",
            "Marges des ventes",
            "Pertes des abandons",
            "Dépenses du garage",
            "Bénéfice",
            *(f"Pour {a.name}" for a in associes),
            "Durée moyenne avant la vente (jours)",
        ],
    )
    depenses = rapport.data.expenses
    cles = [a.pk for a in associes]
    for annee in services.report_years(rapport.rows, depenses):
        bilan = services.year_report(annee, rapport.rows, depenses, cles)
        _ecrire(
            feuille,
            [
                annee,
                bilan.invested,
                bilan.sales_total,
                bilan.margins,
                bilan.losses,
                bilan.garage_expenses,
                bilan.profit,
                *(bilan.shares[cle] for cle in cles),
                bilan.average_days,
            ],
        )


def _feuille_associes(classeur, rapport, associes) -> None:
    """Le bilan de chacun depuis le début (D61), une colonne par associé."""
    feuille = _nouvelle_feuille(
        classeur, "Bilan par associé", ["Depuis le début", *(a.name for a in associes)]
    )
    if rapport.partners is None:
        _ecrire(feuille, ["Bilan par associé indisponible : préviens Mathis."])
        return
    bilans = [rapport.partners[a.pk] for a in associes]
    lignes = (
        ("Mis dans le garage", "put_in"),
        ("Part du bénéfice", "profit_share"),
        ("Ce qui lui revient", "due_to_partner"),
        ("Encaissé (ventes, acomptes, reprises)", "sales_received"),
        ("Règlements reçus", "settlements_received"),
        ("Règlements versés", "settlements_paid"),
        ("Déjà revenu", "already_back"),
        ("Encore dans le garage", "still_in_garage"),
        ("Solde à recevoir (négatif : à verser)", "balance"),
    )
    for libelle, attribut in lignes:
        _ecrire(feuille, [libelle, *(getattr(b, attribut) for b in bilans)])
    cases = rapport.buckets
    _ecrire(feuille, [])
    _ecrire(feuille, ["Bénéfice depuis le début (les deux associés)", cases.profit])
    _ecrire(feuille, ["Encore dans le garage (les deux associés)", cases.still_in_garage])


def _feuille_solde(classeur, rapport, associes) -> None:
    """Le solde entre associés et d'où il vient, comme l'écran « Comptes »."""
    feuille = _nouvelle_feuille(classeur, "Solde", ["Rubrique", *(a.name for a in associes)])
    if rapport.partners is None:
        _ecrire(feuille, ["Solde indisponible : préviens Mathis."])
        return
    data = rapport.data
    details = services.position_details(
        data.partners, data.expenses, data.sales, data.deposits, data.settlements
    )
    colonnes = [details[a.pk] for a in associes]
    lignes = (
        ("Dépenses payées pour le garage", lambda d: d.expenses_paid),
        ("Règlements versés", lambda d: d.settlements_paid),
        ("Encaissé (ventes, acomptes, reprises)", lambda d: d.sales_received),
        ("Remboursements reçus", lambda d: d.refunds_received),
        ("Règlements reçus", lambda d: d.settlements_received),
        ("Total sorti", lambda d: d.position.paid_out),
        ("Total reçu", lambda d: d.position.received),
        ("Avancé pour le garage (sorti − reçu)", lambda d: d.position.value),
    )
    for libelle, valeur in lignes:
        _ecrire(feuille, [libelle, *(valeur(d) for d in colonnes)])
    solde = services.balance({a.pk: d.position for a, d in zip(associes, colonnes, strict=True)})
    _ecrire(feuille, [])
    _ecrire(feuille, [services.balance_label(solde, data.partner_names)])


# ---------------------------------------------------------------------------
# Une feuille par table
# ---------------------------------------------------------------------------


def est_montant(champ) -> bool:
    """Un champ décimal porte de l'argent s'il a la taille des montants (`MONEY`)."""
    return (champ.max_digits, champ.decimal_places) == TAILLE_MONTANT


def _feuille_table(classeur, titre: str, modele) -> None:
    champs = list(modele._meta.concrete_fields)
    # Seuls les liens écrits par leur nom ont besoin de l'objet lié.
    liens = [c.name for c in champs if c.is_relation and c.related_model not in PAR_NUMERO]
    entetes = [_entete(c) for c in champs]
    # Le montant d'un acompte n'est pas stocké : il est lu dans ses versements (D89).
    totaux = queries.deposit_totals() if modele is Deposit else None
    if totaux is not None:
        entetes.append("Total des versements")
    feuille = _nouvelle_feuille(classeur, titre, entetes)
    # Seuls les montants sont en euros : une quantité ou des heures restent des nombres.
    formats = {
        numero: FORMAT_NOMBRE
        for numero, champ in enumerate(champs, start=1)
        if isinstance(champ, models.DecimalField) and not est_montant(champ)
    }
    for objet in modele._base_manager.select_related(*liens).order_by("pk"):
        valeurs = [_valeur(objet, champ) for champ in champs]
        if totaux is not None:
            valeurs.append(totaux.get(objet.pk, services.ZERO))
        _ecrire(feuille, valeurs, formats)


def _entete(champ) -> str:
    if champ.primary_key:
        return "N°"
    if champ.name == "deleted_at":
        return "Mis à la corbeille le"
    if champ.is_relation and champ.related_model in PAR_NUMERO:
        return f"{capfirst(champ.verbose_name)} (n°)"
    return str(capfirst(champ.verbose_name))


def _valeur(objet, champ):
    if champ.is_relation:
        if champ.related_model in PAR_NUMERO:
            return getattr(objet, champ.attname)
        lie = getattr(objet, champ.name)
        return None if lie is None else str(lie)
    if champ.choices:
        return getattr(objet, f"get_{champ.name}_display")() or None
    valeur = getattr(objet, champ.attname)
    if isinstance(champ, models.BooleanField):
        return "oui" if valeur else "non"
    if isinstance(champ, models.FileField):
        return valeur.name or None
    return valeur


# ---------------------------------------------------------------------------
# Écriture des cellules
# ---------------------------------------------------------------------------


def _nouvelle_feuille(classeur, titre: str, entetes: list[str]):
    feuille = classeur.create_sheet(titre)
    feuille.append(entetes)
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    feuille.freeze_panes = "A2"
    for numero, entete in enumerate(entetes, start=1):
        largeur = min(max(len(entete) + 2, 12), 45)
        feuille.column_dimensions[feuille.cell(1, numero).column_letter].width = largeur
    return feuille


def _ecrire(feuille, valeurs: list, formats: dict[int, str] | None = None) -> None:
    """Ajoute une ligne : montants au format euro, dates en vraies dates, textes jamais
    lus comme des formules. `formats` : format de certaines colonnes (numérotées dès 1)."""
    ligne = feuille.max_row + 1
    if not valeurs:
        feuille.cell(ligne, 1)  # une ligne vide, pour aérer la feuille
        return
    for colonne, valeur in enumerate(valeurs, start=1):
        cellule = feuille.cell(ligne, colonne)
        if isinstance(valeur, dt.datetime):
            # Excel ne connaît pas les fuseaux horaires : heure locale du garage.
            cellule.value = timezone.localtime(valeur).replace(tzinfo=None)
            cellule.number_format = FORMAT_DATE_HEURE
        elif isinstance(valeur, dt.date):
            cellule.value = valeur
            cellule.number_format = FORMAT_DATE
        elif isinstance(valeur, Decimal):
            cellule.value = valeur
            cellule.number_format = (formats or {}).get(colonne, FORMAT_EUROS)
        elif isinstance(valeur, str):
            cellule.value = ILLEGAL_CHARACTERS_RE.sub("", valeur)[:TEXTE_MAX]
            cellule.data_type = "s"  # « =… » reste un texte, jamais une formule
        elif valeur is None or isinstance(valeur, int):
            cellule.value = valeur
        else:
            cellule.value = str(valeur)[:TEXTE_MAX]
            cellule.data_type = "s"

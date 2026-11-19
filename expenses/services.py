"""Logique métier des dépenses : enregistrement d'un paiement (payeurs, lignes),
fournisseurs, factures jointes, verrou des véhicules vendus et transferts de pièce.

Les montants ne sont jamais calculés ici : la vérification des paiements et des
remboursements vient de `accounting/services.py`.
"""

import datetime as dt
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet

from accounting import queries
from accounting.services import (
    LineCost,
    LineInput,
    Payment,
    RefundRoom,
    expense_problems,
    is_returned,
    payment_problem,
    refund_problem,
    refund_room,
)
from core.models import Attachment, ExpenseCategory, Partner, Supplier
from sales.models import Sale
from vehicles.models import Vehicle
from vehicles.services import (
    HEIC_MESSAGE,
    NOT_IN_GARAGE,
    image_format,
    short_file_name,
    vehicles_open_to_expenses,
)

from .models import Expense, ExpenseLine, ExpensePayment, PartTransfer, Refund

Status = ExpenseLine.Status

PERSONAL_WITH_REFUND = (
    "Cette dépense a un remboursement : mets-le à la corbeille avant d'en faire un achat personnel."
)
PERSONAL_WITH_VEHICLE = (
    "Un achat personnel ne peut pas concerner un véhicule. Décoche « Achat personnel » "
    "ou mets les lignes sur « Garage »."
)

INVOICE_MAX_BYTES = 20 * 1024 * 1024
INVOICE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class LineEntry:
    """Une ligne saisie dans le formulaire de dépense. `pk` : ligne existante modifiée."""

    label: str
    category: ExpenseCategory
    unit_price: Decimal
    quantity: Decimal = Decimal("1")
    reference: str = ""
    vehicle: Vehicle | None = None
    status: str = ""
    pk: int | None = None

    def as_input(self) -> LineInput:
        """La ligne pour les calculs d'argent."""
        return LineInput(
            quantity=self.quantity,
            unit_price=self.unit_price,
            vehicle=self.vehicle.pk if self.vehicle else None,
        )


def line_status(category: ExpenseCategory, status: str) -> str:
    """Statut enregistré d'une ligne payée : seules les pièces en ont un, « Commandé » par
    défaut. Une pièce payée n'est jamais « À commander »."""
    if category.code != ExpenseCategory.PARTS:
        return ""
    if not status or status == Status.TO_ORDER:
        return Status.ORDERED
    return status


# ---------------------------------------------------------------------------
# Verrou des véhicules vendus (D22, D50)
# ---------------------------------------------------------------------------


def locked_among(vehicle_ids: Iterable[int | None]) -> list[Vehicle]:
    """Les véhicules verrouillés parmi ceux-ci, relus en base : un objet chargé avant une
    réouverture ou une vente serait périmé. La règle reste dans `Vehicle.expenses_locked`."""
    identifiants = {pk for pk in vehicle_ids if pk is not None}
    vehicules = Vehicle.all_objects.filter(pk__in=identifiants).order_by("number")
    return [vehicule for vehicule in vehicules if vehicule.expenses_locked]


def locked_vehicles(expense: Expense) -> list[Vehicle]:
    """Les véhicules de cette dépense qui la verrouillent. Une dépense sur plusieurs
    véhicules est verrouillée dès qu'un de ses véhicules l'est."""
    return locked_among(expense.lines.values_list("vehicle_id", flat=True))


def locked_expense_ids(expense_ids: Iterable[int]) -> set[int]:
    """Parmi ces dépenses, celles qui sont verrouillées, en une requête (listes de lignes)."""
    lignes = ExpenseLine.objects.filter(
        expense__in=set(expense_ids), vehicle__isnull=False
    ).select_related("vehicle")
    return {ligne.expense_id for ligne in lignes if ligne.vehicle.expenses_locked}


def lock_message(vehicles: Sequence[Vehicle]) -> str:
    """Explique un refus du verrou et dit quoi faire."""
    if len(vehicles) == 1:
        return (
            f"{vehicles[0].display_name} est vendu : ses dépenses sont verrouillées. "
            "Rouvre sa fiche pour les corriger."
        )
    noms = ", ".join(vehicule.display_name for vehicule in vehicles)
    return f"{noms} sont vendus : leurs dépenses sont verrouillées. Rouvre leurs fiches."


def expense_lock_refusal(expense: Expense) -> str | None:
    """Pourquoi cette dépense ne se modifie pas, ou None si elle se modifie."""
    verrouilles = locked_vehicles(expense)
    return lock_message(verrouilles) if verrouilles else None


def trashed_vehicle_message(vehicle: Vehicle) -> str:
    """Le refus d'une ligne posée sur un véhicule à la corbeille (D77)."""
    return (
        f"Le véhicule {vehicle.display_name} est à la corbeille : restaure-le ou choisis un "
        "autre véhicule. Rien n'est enregistré."
    )


def line_vehicle_refusal(vehicle: Vehicle) -> str:
    """Le refus quand le véhicule choisi pour une ligne n'est plus proposé : il a quitté la
    liste pendant que la page restait ouverte, ou il était déjà à la corbeille."""
    nom = vehicle.display_name
    if vehicle.deleted_at is not None:
        return trashed_vehicle_message(vehicle)
    if vehicle.status == Vehicle.Status.SOLD:
        return (
            f"{nom} est vendu et ses dépenses sont verrouillées. Rien n'est enregistré : "
            "rouvre sa fiche, ou choisis où mettre cette ligne."
        )
    return (
        f"{nom} est abandonné. Rien n'est enregistré : change d'abord son statut, ou choisis "
        "où mettre cette ligne."
    )


def trade_in_sale(expense: Expense) -> Sale | None:
    """La vente non annulée dont cette dépense est l'achat du véhicule repris, ou None."""
    achats = expense.lines.filter(category__code=ExpenseCategory.VEHICLE_PURCHASE)
    return (
        Sale.objects.filter(
            cancelled_at__isnull=True, trade_in_vehicle__in=achats.values("vehicle")
        )
        .select_related("vehicle")
        .first()
    )


def trade_in_purchase_message(sale: Sale) -> str:
    """Ce que dit « Modifier la dépense » sur l'achat d'un véhicule repris, en aide comme en
    refus : une seule phrase, pour ne pas l'afficher deux fois."""
    return (
        f"Cet achat vient de la reprise de la vente de {sale.vehicle.display_name} : son "
        "montant, ses payeurs et sa ligne ne changent pas. Pour les corriger, annule la "
        "vente. Tu peux modifier la date, le fournisseur, le n° de commande, la désignation, "
        "la référence, les notes et les factures."
    )


def _check_trade_in_purchase(
    expense: Expense | None,
    total_paid: Decimal,
    lines: Sequence[LineEntry],
    payments: Sequence[Payment],
) -> None:
    """Tant que la vente tient, l'achat du véhicule repris garde son montant, ses payeurs et
    sa ligne : sinon, la reprise ne serait plus neutre pour le solde (D51)."""
    vente = trade_in_sale(expense) if expense else None
    if vente is None:
        return
    avant = (
        expense.total_paid,
        {paiement.partner_id: paiement.amount for paiement in expense.payments.all()},
        [(ligne.pk, ligne.quantity, ligne.unit_price) for ligne in expense.lines.all()],
    )
    apres = (
        total_paid,
        {paiement.partner.pk: paiement.amount for paiement in payments},
        [(entree.pk, entree.quantity, entree.unit_price) for entree in lines],
    )
    if avant != apres:
        raise ValidationError(trade_in_purchase_message(vente))


def _check_vehicles_unlocked(vehicle_ids: Iterable[int | None]) -> None:
    verrouilles = locked_among(vehicle_ids)
    if verrouilles:
        raise ValidationError(lock_message(verrouilles))


def _check_expense_unlocked(expense: Expense) -> None:
    refus = expense_lock_refusal(expense)
    if refus:
        raise ValidationError(refus)


def _check_vehicles_not_in_trash(vehicle_ids: Iterable[int | None]) -> None:
    """Une ligne ne se pose jamais sur un véhicule à la corbeille (D77), même si le
    formulaire l'a laissé passer. La sortir de ce véhicule reste possible."""
    identifiants = {pk for pk in vehicle_ids if pk is not None}
    vehicule = (
        Vehicle.all_objects.filter(pk__in=identifiants, deleted_at__isnull=False)
        .order_by("number")
        .first()
    )
    if vehicule is not None:
        raise ValidationError(trashed_vehicle_message(vehicule))


# ---------------------------------------------------------------------------
# Fournisseurs
# ---------------------------------------------------------------------------


def clean_supplier_name(name: str) -> str:
    """Nom sans espaces en trop : «  Moto  Axxe » devient « Moto Axxe »."""
    return " ".join(name.split())


def supplier_key(name: str) -> str:
    """Clé de comparaison d'un nom de fournisseur : sans espaces en trop, sans majuscules
    ni accents. « Electro Moto », « Électro moto » et « électro  moto » ont la même clé."""
    decompose = unicodedata.normalize("NFKD", clean_supplier_name(name).casefold())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def find_supplier(name: str) -> Supplier | None:
    """Le fournisseur de ce nom, sans tenir compte des majuscules ni des accents, ou None.

    La comparaison se fait en Python : le `LOWER()` de SQLite ne met pas en minuscules les
    lettres accentuées, « Électro Moto » et « électro moto » y diffèrent.
    """
    cle = supplier_key(name)
    return next((f for f in Supplier.objects.all() if supplier_key(f.name) == cle), None)


def find_or_create_supplier(name: str) -> Supplier | None:
    """Le fournisseur de ce nom, créé s'il est nouveau ; None pour un nom vide."""
    nom = clean_supplier_name(name)
    if not nom:
        return None
    return find_supplier(nom) or Supplier.objects.create(name=nom)


# ---------------------------------------------------------------------------
# Factures
# ---------------------------------------------------------------------------


def invoice_problem(name: str, size: int, head: bytes) -> str | None:
    """Pourquoi un fichier n'est pas accepté comme facture, ou None.

    PDF, JPEG, PNG ou WebP, reconnus par leurs premiers octets (comme les photos, D37).
    """
    extension = Path(name).suffix.lower()
    format_image = image_format(head)
    if extension in (".heic", ".heif") or format_image == "heic":
        return HEIC_MESSAGE
    if extension == ".pdf":
        lisible = head.startswith(b"%PDF-")
    else:
        lisible = extension in INVOICE_IMAGE_EXTENSIONS and format_image is not None
    if not lisible:
        return "Ce fichier n'est pas lu : envoie la facture en PDF, JPEG, PNG ou WebP."
    if size > INVOICE_MAX_BYTES:
        return "Cette facture dépasse 20 Mo : envoie-la en taille réduite."
    return None


def refused_invoices(files: Iterable) -> list[tuple[str, str]]:
    """Les fichiers refusés comme facture : [(nom du fichier, raison)]."""
    refusees = []
    for fichier in files:
        debut = fichier.read(16)
        fichier.seek(0)
        probleme = invoice_problem(fichier.name, fichier.size, debut)
        if probleme:
            refusees.append((fichier.name, probleme))
    return refusees


def single_vehicle(expense: Expense) -> Vehicle | None:
    """Le véhicule de toutes les lignes, s'il n'y en a qu'un : ses factures vont dans son
    dossier. Sinon (plusieurs véhicules, dépense générale) : None, et `media/garage/`."""
    vehicules = {ligne.vehicle_id for ligne in expense.lines.all()}
    if len(vehicules) == 1 and None not in vehicules:
        return Vehicle.all_objects.get(pk=vehicules.pop())
    return None


def add_invoices(expense: Expense, files: Iterable) -> list[Attachment]:
    """Joint des factures, déjà vérifiées par `refused_invoices`, à une dépense."""
    fichiers = list(files)
    if not fichiers:
        return []
    vehicule = single_vehicle(expense)
    dernier = (
        Attachment.objects.filter(expense=expense, kind=Attachment.Kind.INVOICE)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    )
    ordre = 0 if dernier is None else dernier + 1
    ajoutees = []
    for fichier in fichiers:
        fichier.name = short_file_name(fichier.name)
        ajoutees.append(
            Attachment.objects.create(
                file=fichier,
                kind=Attachment.Kind.INVOICE,
                expense=expense,
                vehicle=vehicule,
                order=ordre,
            )
        )
        ordre += 1
    return ajoutees


@transaction.atomic
def remove_invoice(invoice: Attachment) -> None:
    """Met une facture à la corbeille (le fichier reste sur le disque)."""
    _check_expense_unlocked(invoice.expense)
    invoice.soft_delete()


# ---------------------------------------------------------------------------
# Dépenses
# ---------------------------------------------------------------------------


def paid_lines() -> QuerySet[ExpenseLine]:
    """Les lignes payées (dépense hors corbeille), la plus récente d'abord, avec
    `has_invoice` (la dépense a une facture)."""
    factures = Attachment.objects.filter(expense=OuterRef("expense"), kind=Attachment.Kind.INVOICE)
    return (
        ExpenseLine.objects.filter(expense__isnull=False, expense__deleted_at__isnull=True)
        .select_related("expense__supplier", "category")
        .annotate(has_invoice=Exists(factures))
        .order_by("-expense__date", "-expense_id", "id")
    )


def vehicle_paid_lines(vehicle: Vehicle) -> QuerySet[ExpenseLine]:
    """Les lignes payées d'un véhicule, la plus récente d'abord."""
    return paid_lines().filter(vehicle=vehicle)


def _check_lines(
    expense: Expense | None, lines: Sequence[LineEntry]
) -> tuple[dict[int, ExpenseLine], dict[int, ExpenseLine]]:
    """Vérifie les lignes saisies. Renvoie, par id, les lignes existantes de la dépense et
    les pièces « À commander » que ce paiement commande."""
    if not lines:
        raise ValidationError("Ajoute au moins une ligne.")
    existantes = {ligne.pk: ligne for ligne in expense.lines.all()} if expense else {}
    commandees: dict[int, ExpenseLine] = {}
    gardees = set()
    for entree in lines:
        achat = entree.category.code == ExpenseCategory.VEHICLE_PURCHASE
        if entree.pk is None:
            if achat:
                raise ValidationError(
                    "L'achat d'un véhicule s'enregistre avec « Enregistrer l'achat », sur sa fiche."
                )
            continue
        ligne = existantes.get(entree.pk)
        if ligne is None:
            refus = order_refusal([entree.pk])
            if refus:
                raise ValidationError(refus)
            ligne = parts_to_order().get(pk=entree.pk)
            commandees[ligne.pk] = ligne
        if ligne.pk in gardees:
            raise ValidationError("Une même ligne a été envoyée deux fois. Recharge la page.")
        gardees.add(ligne.pk)
        etait_achat = ligne.category.code == ExpenseCategory.VEHICLE_PURCHASE
        if (etait_achat or achat) and (
            ligne.category_id != entree.category.pk or ligne.vehicle_id != _pk(entree.vehicle)
        ):
            raise ValidationError(
                "La ligne d'achat du véhicule garde sa catégorie et son véhicule."
            )
        if ligne.status == Status.RETURNED and ligne.category_id != entree.category.pk:
            raise ValidationError(
                f"« {ligne.label} » est retournée : elle garde sa catégorie. "
                "Mets d'abord son remboursement à la corbeille."
            )
    for pk, ligne in existantes.items():
        if pk in gardees:
            continue
        if ligne.category.code == ExpenseCategory.VEHICLE_PURCHASE:
            raise ValidationError("La ligne d'achat du véhicule ne peut pas être retirée.")
        if ligne.refunds.exists():
            raise ValidationError(
                f"« {ligne.label} » a un remboursement : elle ne peut pas être retirée."
            )
    return existantes, commandees


def _pk(objet) -> int | None:
    return objet.pk if objet is not None else None


def _save_payments(expense: Expense, payments: Sequence[Payment]) -> None:
    """Paiements de la dépense : modifiés sur place, ajoutés, ou mis à la corbeille (D40)."""
    existants = {paiement.partner_id: paiement for paiement in expense.payments.all()}
    nouveaux = {paiement.partner.pk for paiement in payments}
    # D'abord la corbeille : un associé retiré libère sa place avant tout ajout.
    for associe, paiement in existants.items():
        if associe not in nouveaux:
            paiement.soft_delete()
    for paiement in payments:
        actuel = existants.get(paiement.partner.pk)
        if actuel is None:
            ExpensePayment.objects.create(
                expense=expense, partner=paiement.partner, amount=paiement.amount
            )
        elif actuel.amount != paiement.amount:
            actuel.amount = paiement.amount
            actuel.save()


def _save_lines(
    expense: Expense,
    lines: Sequence[LineEntry],
    existantes: dict[int, ExpenseLine],
    commandees: dict[int, ExpenseLine],
) -> None:
    """Lignes de la dépense : modifiées, ajoutées (ou pièces à commander rattachées au
    paiement), ou mises à la corbeille."""
    gardees = set()
    for entree in lines:
        if entree.pk is None:
            ligne = ExpenseLine()
        else:
            ligne = existantes.get(entree.pk) or commandees[entree.pk]
        ligne.expense = expense
        ligne.label = entree.label
        ligne.reference = entree.reference
        ligne.quantity = entree.quantity
        ligne.unit_price = entree.unit_price
        ligne.category = entree.category
        ligne.vehicle = entree.vehicle
        # Une pièce « Retourné » le reste ici : son statut suit le remboursement (D45).
        retournee = ligne.status == Status.RETURNED
        # Pas de statut reçu : page ouverte sur une ligne alors « Retourné », il ne change pas.
        ligne.status = line_status(entree.category, entree.status or ligne.status)
        if retournee and ligne.status:
            ligne.status = Status.RETURNED
        ligne.save()
        gardees.add(ligne.pk)
    for pk, ligne in existantes.items():
        if pk not in gardees:
            ligne.soft_delete()


def sync_purchase_date(expense: Expense) -> None:
    """La date de la dépense d'achat est la date d'achat du véhicule (`purchased_on`) :
    corriger l'une corrige l'autre."""
    for ligne in expense.lines.filter(category__code=ExpenseCategory.VEHICLE_PURCHASE):
        vehicule = ligne.vehicle
        if vehicule is not None and vehicule.purchased_on != expense.date:
            vehicule.purchased_on = expense.date
            vehicule.save(update_fields=["purchased_on", "updated_at"])


@transaction.atomic
def save_expense(
    expense: Expense | None,
    *,
    date: dt.date,
    supplier_name: str,
    order_number: str,
    total_paid: Decimal,
    is_personal: bool,
    notes: str,
    lines: Sequence[LineEntry],
    payments: Sequence[Payment],
    invoices: Iterable = (),
) -> Expense:
    """Crée (`expense` vide) ou modifie une dépense, tout ou rien.

    Enregistre le fournisseur (créé s'il est nouveau), les paiements (`Payment` dont la clé
    est le `Partner`), les lignes et les factures jointes, déjà vérifiées par
    `refused_invoices`. Une ligne retirée part à la corbeille. Une ligne qui reprend une
    pièce « À commander » la rattache à ce paiement : elle passe en « Commandé ».

    Refusée si un véhicule est verrouillé, avant comme après : on ne sort pas une ligne d'un
    véhicule vendu, et on ne lui en ajoute pas.
    """
    avant = expense.lines.values_list("vehicle_id", flat=True) if expense else ()
    _check_vehicles_unlocked([*avant, *(_pk(entree.vehicle) for entree in lines)])
    _check_vehicles_not_in_trash(_pk(entree.vehicle) for entree in lines)
    _check_trade_in_purchase(expense, total_paid, lines, payments)
    existantes, commandees = _check_lines(expense, lines)
    probleme = payment_problem(total_paid, payments)
    if probleme:
        raise ValidationError(probleme)
    anciens = {p.partner_id for p in expense.payments.all()} if expense else set()
    if any(not p.partner.is_active and p.partner.pk not in anciens for p in payments):
        raise ValidationError("Un des payeurs n'est plus un associé actif.")
    if is_personal and any(entree.vehicle for entree in lines):
        raise ValidationError(PERSONAL_WITH_VEHICLE)
    # Un achat personnel n'entre dans aucun calcul : un remboursement n'y aurait pas de sens.
    if is_personal and expense and Refund.objects.filter(line__expense=expense).exists():
        raise ValidationError(PERSONAL_WITH_REFUND)

    depense = expense or Expense()
    depense.date = date
    depense.supplier = find_or_create_supplier(supplier_name)
    depense.order_number = order_number
    depense.total_paid = total_paid
    depense.is_personal = is_personal
    depense.notes = notes
    depense.save()
    _save_payments(depense, payments)
    _save_lines(depense, lines, existantes, commandees)
    sync_purchase_date(depense)

    # Une ligne déjà remboursée ne peut pas coûter moins que son remboursement.
    (entree,) = queries.load_expenses([depense.pk])
    problemes = expense_problems(entree)
    if problemes:
        raise ValidationError(
            f"Cette dépense ne tombe pas juste : {' ; '.join(problemes)}. Corrige les montants."
        )
    # Un prix corrigé peut rendre une pièce entièrement remboursée, ou ne plus l'être.
    for ligne in (
        depense.lines.filter(refunds__deleted_at__isnull=True, refunds__isnull=False)
        .select_related("category")
        .distinct()
    ):
        sync_returned_status(ligne)
    add_invoices(depense, invoices)
    return depense


def expense_trash_refusal(expense: Expense) -> str | None:
    """Pourquoi cette dépense ne part pas à la corbeille, ou None (D74).

    Refusée si elle est verrouillée (D50), ou si c'est l'achat d'un véhicule repris par une
    vente en cours : la reprise ne serait plus neutre (D51).
    """
    refus = expense_lock_refusal(expense)
    if refus:
        return refus
    vente = trade_in_sale(expense)
    if vente is not None:
        return (
            f"Cet achat vient de la reprise de la vente de {vente.vehicle.display_name} : il ne "
            "part pas à la corbeille seul. Pour l'annuler, annule la vente."
        )
    return None


@transaction.atomic
def trash_expense(expense: Expense) -> Expense:
    """Met une dépense entière à la corbeille : ses lignes, paiements, remboursements et
    factures restent attachés et sortent des comptes avec elle. Pour l'achat d'un véhicule,
    c'est ainsi qu'on annule un achat saisi par erreur (D35, D39)."""
    depense = Expense.objects.filter(pk=expense.pk).first()
    if depense is None:
        raise ValidationError("Cette dépense est déjà dans la corbeille. Recharge la page.")
    refus = expense_trash_refusal(depense)
    if refus:
        raise ValidationError(refus)
    depense.soft_delete()
    return depense


# ---------------------------------------------------------------------------
# Pièces à commander et statut des pièces
# ---------------------------------------------------------------------------

# Étape suivante proposée sur la fiche véhicule : (statut, libellé du bouton).
NEXT_PART_STATUS = {
    Status.ORDERED: (Status.RECEIVED, "Marquer reçue"),
    Status.RECEIVED: (Status.FITTED, "Marquer montée"),
}
# Statuts qu'on choisit à la main pour une pièce payée.
MANUAL_PART_STATUSES = (Status.ORDERED, Status.RECEIVED, Status.FITTED)


def noted_parts() -> QuerySet[ExpenseLine]:
    """Toutes les pièces « À commander » hors corbeille, même celles d'un véhicule vendu ou
    abandonné. Pour la liste et le compteur, c'est `parts_to_order`."""
    return ExpenseLine.objects.filter(expense__isnull=True, status=Status.TO_ORDER).select_related(
        "vehicle", "category"
    )


def parts_to_order() -> QuerySet[ExpenseLine]:
    """Les pièces à commander, par véhicule. Celles d'un véhicule vendu ou abandonné restent
    en base, mais n'y sont pas : elles reviennent si la vente est annulée (D50)."""
    return (
        noted_parts().exclude(vehicle__status__in=NOT_IN_GARAGE).order_by("vehicle__number", "id")
    )


def parts_category() -> ExpenseCategory:
    return ExpenseCategory.objects.get(code=ExpenseCategory.PARTS)


def _out_of_garage(vehicle: Vehicle) -> tuple[str, str] | None:
    """Si ce véhicule, relu en base, ne prend plus de pièces à commander : ce qui lui arrive
    et quoi faire. Sinon None."""
    if vehicle.status == Vehicle.Status.SOLD:
        if vehicle.reopened_at is not None:
            return (
                "est vendu",
                "Sa fiche est déjà rouverte : si la pièce est déjà payée, ajoute une dépense.",
            )
        return "est vendu", "Si la pièce est déjà payée, rouvre sa fiche et ajoute une dépense."
    if vehicle.status == Vehicle.Status.ABANDONED:
        return "est abandonné", "Change d'abord son statut."
    return None


def part_to_order_refusal(vehicle: Vehicle | None) -> str | None:
    """Pourquoi on ne peut pas noter une pièce à commander sur ce véhicule, ou None.

    Lu en base, et même sur une fiche rouverte : rouvrir sert à corriger ce qui est payé,
    pas à préparer de nouveaux achats (D50).
    """
    if vehicle is None:
        return None
    vehicule = Vehicle.all_objects.get(pk=vehicle.pk)
    if vehicule.deleted_at is not None:
        return trashed_vehicle_message(vehicule)
    sortie = _out_of_garage(vehicule)
    if sortie is None:
        return None
    etat, suite = sortie
    return f"{vehicle.display_name} {etat} : on n'y note plus de pièce à commander. {suite}"


def _order_obstacle(part: ExpenseLine | None) -> str | None:
    """Ce qui empêche de commander cette pièce cochée, ou None : la pièce, la cause et quoi
    faire, à l'adresse de celui qui commande."""
    if part is None:
        return "Une des pièces cochées n'existe plus."
    if part.deleted_at is not None:
        return (
            f"« {part.label} » est à la corbeille. Si tu en as besoin, ajoute-la de nouveau "
            "à la liste."
        )
    if part.expense_id is not None or part.status != Status.TO_ORDER:
        return f"« {part.label} » a déjà été commandée : inutile de la commander de nouveau."
    sortie = _out_of_garage(part.vehicle) if part.vehicle is not None else None
    if sortie is None:
        return None
    etat, suite = sortie
    return f"« {part.label} » n'est plus à commander : {part.vehicle.display_name} {etat}. {suite}"


def order_refusal(part_ids: Iterable[int | None]) -> str | None:
    """Pourquoi cette commande s'arrête, ou None si toutes les pièces cochées sont à commander.

    Une seule pièce écartée arrête tout : commander les autres sans rien dire fausserait la
    facture, dont le montant couvre toutes les pièces cochées (l'écart passerait en frais de
    port). `None` dans la liste : un identifiant illisible dans l'adresse."""
    identifiants = list(dict.fromkeys(part_ids))
    pieces = ExpenseLine.all_objects.select_related("vehicle").in_bulk(
        [pk for pk in identifiants if pk is not None]
    )
    obstacles = [
        obstacle for pk in identifiants if (obstacle := _order_obstacle(pieces.get(pk))) is not None
    ]
    if not obstacles:
        return None
    return " ".join(
        [*obstacles, "Rien n'est enregistré : coche de nouveau les pièces de ta commande."]
    )


def _check_can_note_part(vehicle: Vehicle | None) -> None:
    refus = part_to_order_refusal(vehicle)
    if refus:
        raise ValidationError(refus)


@transaction.atomic
def add_part_to_order(
    *,
    label: str,
    reference: str = "",
    quantity: Decimal = Decimal("1"),
    unit_price: Decimal = Decimal("0"),
    vehicle: Vehicle | None = None,
) -> ExpenseLine:
    """Ajoute une pièce à commander : une ligne sans paiement, qui ne compte dans aucun
    calcul tant qu'elle n'est pas commandée. `unit_price` : prix estimé (0 si inconnu)."""
    _check_can_note_part(vehicle)
    return ExpenseLine.objects.create(
        label=label,
        reference=reference,
        quantity=quantity,
        unit_price=unit_price,
        vehicle=vehicle,
        category=parts_category(),
        status=Status.TO_ORDER,
    )


@transaction.atomic
def add_parts_to_order(parts: Iterable[dict]) -> list[ExpenseLine]:
    """Ajoute plusieurs pièces à commander d'un coup : toutes ou aucune.

    Chaque élément de `parts` porte les arguments nommés de `add_part_to_order`."""
    return [add_part_to_order(**piece) for piece in parts]


def _check_still_to_order(line: ExpenseLine) -> None:
    if line.expense_id is not None or line.status != Status.TO_ORDER:
        raise ValidationError("Cette pièce a déjà été commandée : modifie plutôt sa dépense.")


@transaction.atomic
def update_part_to_order(
    line: ExpenseLine,
    *,
    label: str,
    reference: str,
    quantity: Decimal,
    unit_price: Decimal,
    vehicle: Vehicle | None,
) -> ExpenseLine:
    """Modifie une pièce encore à commander."""
    _check_still_to_order(line)
    _check_vehicles_unlocked([line.vehicle_id])
    _check_can_note_part(vehicle)
    line.label = label
    line.reference = reference
    line.quantity = quantity
    line.unit_price = unit_price
    line.vehicle = vehicle
    line.save()
    return line


@transaction.atomic
def remove_part_to_order(line: ExpenseLine) -> None:
    """Met une pièce encore à commander à la corbeille (elle ne porte pas d'argent)."""
    _check_still_to_order(line)
    _check_vehicles_unlocked([line.vehicle_id])
    line.soft_delete()


@transaction.atomic
def change_part_status(line: ExpenseLine, status: str) -> ExpenseLine:
    """Change le statut d'une pièce payée : Commandé, Reçu ou Monté, dans n'importe quel
    ordre. « À commander » vient d'une pièce pas encore payée, « Retourné » d'un retour."""
    if line.expense_id is None:
        raise ValidationError(
            "Cette pièce n'est pas encore payée : enregistre d'abord sa commande."
        )
    _check_expense_unlocked(line.expense)
    if line.category.code != ExpenseCategory.PARTS:
        raise ValidationError("Seules les pièces ont un statut.")
    if line.status == Status.RETURNED:
        raise ValidationError("Cette pièce est retournée : son statut suit le remboursement.")
    if status not in MANUAL_PART_STATUSES:
        raise ValidationError("Choisis Commandé, Reçu ou Monté.")
    if line.status != status:
        line.status = status
        line.save(update_fields=["status", "updated_at"])
    return line


def line_rows(lines: Iterable[ExpenseLine], expenses=None) -> list[dict]:
    """Ce qu'affiche une liste de lignes payées : la ligne, son coût net (frais de port
    compris, retours déduits, calculé par `accounting`), ses payeurs et l'étape suivante
    de la pièce, sauf si la dépense est verrouillée. `expenses` : dépenses déjà chargées par
    `queries.load_expenses`."""
    lines = list(lines)
    couts = queries.line_costs(expenses)
    associes = {associe.pk: associe for associe in Partner.objects.all()}
    verrouillees = locked_expense_ids(ligne.expense_id for ligne in lines)
    lignes = []
    for ligne in lines:
        cout = couts.get(ligne.pk)
        payeurs = [associes[pk] for pk in sorted(cout.shares) if cout.shares[pk]] if cout else []
        ouverte = ligne.expense_id is not None and ligne.expense_id not in verrouillees
        suivant = NEXT_PART_STATUS.get(ligne.status) if ouverte else None
        # Le badge du statut ouvre un menu pour corriger (revenir de « Monté » à « Reçu »).
        modifiable = ouverte and ligne.status in MANUAL_PART_STATUSES
        lignes.append(
            {
                "line": ligne,
                "cost": cout.net if cout else None,
                "payers": payeurs,
                "locked": ligne.expense_id in verrouillees,
                "next_status": suivant[0] if suivant else None,
                "next_label": suivant[1] if suivant else None,
                "status_choices": (
                    [(s.value, s.label) for s in MANUAL_PART_STATUSES] if modifiable else []
                ),
            }
        )
    return lignes


# ---------------------------------------------------------------------------
# Retours et remboursements
# ---------------------------------------------------------------------------

# Statut d'une pièce qui n'est plus entièrement remboursée (retour annulé ou corrigé) : on
# ne garde pas le statut d'avant le retour ; une pièce retournée avait été reçue.
STATUS_AFTER_CANCELLED_RETURN = Status.RECEIVED


def line_cost(line: ExpenseLine) -> LineCost:
    """Le coût d'une ligne payée (total réparti, déjà remboursé), calculé par `accounting`.

    Refuse une pièce pas encore payée et une ligne d'achat personnel (hors calculs)."""
    if line.expense_id is None:
        raise ValidationError("Cette pièce n'est pas encore payée : rien à rembourser.")
    cout = queries.line_costs(queries.load_expenses([line.expense_id])).get(line.pk)
    if cout is None:
        if line.expense.is_deleted:
            raise ValidationError("Cette dépense est dans la corbeille.")
        raise ValidationError(
            "Un achat personnel n'entre dans aucun calcul : il n'y a pas de retour à enregistrer."
        )
    return cout


def line_refund_room(line: ExpenseLine, refund: Refund | None = None) -> RefundRoom:
    """Coût de la ligne, autres remboursements et reste à rembourser, pour le formulaire
    d'un retour (`refund` : celui qu'on corrige)."""
    cout = line_cost(line)
    return refund_room(cout.allocated, cout.refunded, refund.amount if refund else Decimal("0"))


def sync_returned_status(line: ExpenseLine) -> None:
    """Une pièce entièrement remboursée est « Retourné » ; si elle ne l'est plus, elle
    redevient « Reçu », sauf si elle garde un remboursement « à préciser » (D45). Les autres
    catégories n'ont pas de statut."""
    if line.category.code != ExpenseCategory.PARTS:
        return
    cout = line_cost(line)
    retournee = is_returned(
        cout.allocated,
        cout.refunded,
        was_returned=line.status == Status.RETURNED,
        to_be_specified=line.refunds.filter(amount=0).exists(),
    )
    if retournee:
        statut = Status.RETURNED
    elif line.status == Status.RETURNED:
        statut = STATUS_AFTER_CANCELLED_RETURN
    else:
        return
    if line.status != statut:
        line.status = statut
        line.save(update_fields=["status", "updated_at"])


@transaction.atomic
def record_refund(
    line: ExpenseLine,
    *,
    date: dt.date,
    amount: Decimal,
    refunded_to: Partner,
    notes: str = "",
    refund: Refund | None = None,
) -> Refund:
    """Enregistre un retour remboursé sur une ligne payée (ou corrige `refund`).

    Le montant ne dépasse pas ce qui reste à rembourser ; 0,00 € est accepté, « à
    préciser ». La pièce passe en « Retourné » seulement si elle est entièrement remboursée.
    """
    ligne = ExpenseLine.objects.select_for_update().select_related("category").get(pk=line.pk)
    if refund is not None and refund.line_id != ligne.pk:
        raise ValidationError("Ce remboursement concerne une autre ligne.")
    # Un ancien associé reste possible pour un remboursement qu'il avait déjà reçu.
    meme_associe = refund is not None and refund.refunded_to_id == refunded_to.pk
    if not refunded_to.is_active and not meme_associe:
        raise ValidationError(
            {"refunded_to": f"{refunded_to.name} n'est plus associé : choisis un autre nom."}
        )
    # D'abord le reste à rembourser : il refuse une pièce pas encore payée, sans dépense.
    marge = line_refund_room(ligne, refund)
    _check_expense_unlocked(ligne.expense)
    probleme = refund_problem(amount, marge.allocated, marge.others)
    if probleme:
        raise ValidationError({"amount": probleme})

    remboursement = refund if refund is not None else Refund(line=ligne)
    remboursement.date = date
    remboursement.amount = amount
    remboursement.refunded_to = refunded_to
    remboursement.notes = notes
    remboursement.save()
    sync_returned_status(ligne)
    return remboursement


@transaction.atomic
def remove_refund(refund: Refund) -> None:
    """Met un remboursement à la corbeille ; la pièce redevient « Reçu » si elle était
    « Retourné »."""
    ligne = ExpenseLine.objects.select_related("category", "expense").get(pk=refund.line_id)
    _check_expense_unlocked(ligne.expense)
    refund.soft_delete()
    sync_returned_status(ligne)


def default_refunded_to(expense: Expense) -> Partner | None:
    """L'associé proposé dans « Remboursé à » : le payeur, s'il est seul. Avec deux
    payeurs, rien n'est choisi d'avance (« Choisis qui a reçu l'argent. »)."""
    payeurs = list(expense.payments.select_related("partner"))
    return payeurs[0].partner if len(payeurs) == 1 else None


# ---------------------------------------------------------------------------
# Transferts de pièce (D90)
# ---------------------------------------------------------------------------

TRANSFER_MOVED = "Cette ligne a changé de véhicule entre-temps. Recharge la page."


def transfer_refusal(line: ExpenseLine) -> str | None:
    """Pourquoi cette ligne ne se transfère pas sur un autre véhicule, ou None. Relue en
    base : la page a pu rester ouverte pendant une vente ou une mise à la corbeille."""
    ligne = (
        ExpenseLine.all_objects.select_related("category")
        .filter(pk=line.pk)
        .only("vehicle_id", "expense_id", "deleted_at", "category__code")
        .first()
    )
    if ligne is None or ligne.expense_id is None:
        return "Cette pièce n'est pas encore payée : elle se change dans « À commander »."
    depense = Expense.all_objects.get(pk=ligne.expense_id)
    if ligne.deleted_at is not None or depense.deleted_at is not None:
        return "Cette dépense est à la corbeille : restaure-la d'abord."
    if depense.is_personal:
        return "Un achat personnel n'entre dans aucun calcul : il ne se transfère pas."
    if ligne.vehicle_id is None:
        return (
            "Cette ligne est sur le garage, pas sur un véhicule. Pour la mettre sur un "
            "véhicule, modifie la dépense."
        )
    if ligne.category.code == ExpenseCategory.VEHICLE_PURCHASE:
        return "L'achat d'un véhicule reste sur ce véhicule : il ne se transfère pas."
    # Une dépense sur plusieurs véhicules est verrouillée dès qu'un de ses véhicules l'est
    # (D50), y compris quand on part de la fiche rouverte de l'autre.
    return expense_lock_refusal(depense)


def transfer_targets(line: ExpenseLine) -> QuerySet[Vehicle]:
    """Les véhicules où cette ligne peut aller : ceux qu'une dépense propose (garage et
    vendus à la fiche rouverte, D50), sauf celui où elle est déjà."""
    return vehicles_open_to_expenses().exclude(pk=line.vehicle_id)


@transaction.atomic
def transfer_line(
    line: ExpenseLine,
    *,
    to_vehicle: Vehicle,
    date: dt.date,
    notes: str = "",
    from_vehicle_id: int | None = None,
) -> PartTransfer:
    """Transfère toute la ligne sur un autre véhicule et en garde la trace (D90).

    Ses payeurs, ses remboursements et son statut ne changent pas : le coût suit la pièce,
    le solde entre associés ne bouge pas (cas 15). `from_vehicle_id` : le véhicule affiché
    quand la page s'est ouverte ; si la ligne en a changé depuis, rien n'est enregistré.
    """
    ligne = ExpenseLine.all_objects.get(pk=line.pk)
    if from_vehicle_id is not None and ligne.vehicle_id != from_vehicle_id:
        raise ValidationError(TRANSFER_MOVED)
    refus = transfer_refusal(ligne)
    if refus:
        raise ValidationError(refus)
    cible = Vehicle.all_objects.get(pk=to_vehicle.pk)
    if cible.pk == ligne.vehicle_id:
        raise ValidationError("La ligne est déjà sur ce véhicule : choisis-en un autre.")
    # Corbeille (D77), verrou d'un véhicule vendu (D50), véhicule abandonné : le même refus
    # que dans « Modifier la dépense ».
    if not transfer_targets(ligne).filter(pk=cible.pk).exists():
        raise ValidationError(line_vehicle_refusal(cible))
    transfert = PartTransfer.objects.create(
        line=ligne, from_vehicle_id=ligne.vehicle_id, to_vehicle=cible, date=date, notes=notes
    )
    ligne.vehicle = cible
    ligne.save(update_fields=["vehicle", "updated_at"])
    return transfert


def vehicle_transfers(vehicle: Vehicle) -> QuerySet[PartTransfer]:
    """Les transferts reçus ou donnés par ce véhicule, le plus récent d'abord, sans ceux
    d'une ligne ou d'une dépense à la corbeille (la pièce n'est plus dans les comptes).
    `trade_in_cancel_refusal` lit, lui, tous les transferts."""
    return (
        PartTransfer.objects.filter(Q(from_vehicle=vehicle) | Q(to_vehicle=vehicle))
        .filter(line__deleted_at__isnull=True, line__expense__deleted_at__isnull=True)
        .select_related("line", "from_vehicle", "to_vehicle")
        .order_by("-date", "-id")
    )

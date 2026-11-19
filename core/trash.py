"""Corbeille : ce qu'elle contient et la restauration (D74 à D77).

Rien n'est jamais supprimé (règle 6) : la corbeille liste ce qui a été mis de côté, et
« Restaurer » le remet en place. Restaurer une dépense ou un remboursement change les
soldes : chaque restauration est contrôlée ici, dans une transaction qui relit la base, et
le refus est montré à l'écran avant même le clic (D75).

Ce qui n'est pas listé ne se restaure jamais seul : un paiement, un encaissement (D40, D49)
ou une ligne retirée d'une dépense payée (cela changerait ses frais de port). Ce qui est
attaché à une fiche à la corbeille (photos, interventions, tâches) reste caché avec elle.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction

from accounting.services import refund_problem
from core.formatting import format_euros
from core.models import Attachment, Contact, ExpenseCategory
from expenses import services as expense_services
from expenses.models import Expense, ExpenseLine, Refund
from sales.models import Sale
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

ALREADY_RESTORED = "Cet élément n'est plus dans la corbeille. Recharge la page."


@dataclass(frozen=True)
class TrashItem:
    """Un élément de la corbeille, prêt à afficher."""

    kind: str  # clé de l'adresse de restauration (voir `KINDS`)
    pk: int
    title: str
    detail: str
    deleted_at: dt.datetime
    deleted_by: str  # nom de l'associé, vide s'il n'est pas connu (pas d'historique)
    refusal: str | None  # pourquoi « Restaurer » est refusé, et quoi faire


@dataclass(frozen=True)
class TrashGroup:
    title: str
    items: list[TrashItem]


# ---------------------------------------------------------------------------
# Règles de restauration (D75)
# ---------------------------------------------------------------------------


def _cancelled_trade_in_sale(vehicle_id: int) -> Sale | None:
    """La vente annulée dont ce véhicule était la reprise, ou None."""
    return (
        Sale.objects.filter(trade_in_vehicle_id=vehicle_id, cancelled_at__isnull=False)
        .select_related("vehicle")
        .first()
    )


def _vehicle_obstacle(vehicle: Vehicle | None, *, for_expense: bool = False) -> str | None:
    """Pourquoi rien ne revient sur ce véhicule : reprise d'une vente annulée (D51), ou
    fiche à la corbeille. None sinon.

    `for_expense` : pour une dépense seulement, le message ajoute de prévenir Mathis (une
    vraie dépense de la reprise ne revient plus, limite notée dans D75) ; pour une photo,
    une pièce ou un remboursement, la phrase tomberait à côté."""
    if vehicle is None:
        return None
    vente = _cancelled_trade_in_sale(vehicle.pk)
    if vente is not None:
        message = (
            f"{vehicle.display_name} était la reprise de la vente annulée de "
            f"{vente.vehicle.display_name} : rien ne revient sur lui."
        )
        if for_expense:
            message += " Si c'était une vraie dépense, préviens Mathis."
        return message
    if vehicle.deleted_at is not None:
        return f"{vehicle.display_name} est à la corbeille : restaure d'abord le véhicule."
    return None


def vehicle_restore_refusal(vehicle: Vehicle) -> str | None:
    """La fiche reprise d'une vente annulée ne revient jamais seule (D51) : la vente reste
    annulée, elle n'aurait plus de contrepartie."""
    vente = _cancelled_trade_in_sale(vehicle.pk)
    if vente is not None:
        return (
            f"Ce véhicule était la reprise de la vente annulée de {vente.vehicle.display_name} : "
            "il reste à la corbeille avec son achat."
        )
    return None


def expense_restore_refusal(expense: Expense) -> str | None:
    """Une dépense revient si ses véhicules sont là et ouverts, et si l'achat qu'elle porte
    n'a pas été remplacé (D39, D50, D51)."""
    lignes = list(ExpenseLine.objects.filter(expense=expense).select_related("vehicle", "category"))
    vehicules = {ligne.vehicle.pk: ligne.vehicle for ligne in lignes if ligne.vehicle}
    for vehicule in sorted(vehicules.values(), key=lambda v: v.number):
        obstacle = _vehicle_obstacle(vehicule, for_expense=True)
        if obstacle:
            return obstacle
    refus = expense_services.expense_lock_refusal(expense)
    if refus:
        return refus
    for ligne in lignes:
        if ligne.category.code != ExpenseCategory.VEHICLE_PURCHASE or ligne.vehicle is None:
            continue
        nom = ligne.vehicle.display_name
        if vehicle_services.has_purchase(ligne.vehicle):
            return (
                f"{nom} a déjà un autre achat : celui-ci ne revient pas. Si c'est l'autre qui est "
                "faux, mets-le d'abord à la corbeille."
            )
        if ligne.vehicle.status == Vehicle.Status.WAITING_PURCHASE:
            return (
                f"{nom} est revenu en « En attente d'achat » : enregistre de nouveau son achat "
                "sur sa fiche."
            )
    return None


def part_restore_refusal(line: ExpenseLine) -> str | None:
    """Une pièce à commander ne revient pas sur un véhicule vendu, abandonné ou à la
    corbeille (D50)."""
    return _vehicle_obstacle(line.vehicle) or expense_services.part_to_order_refusal(line.vehicle)


def refund_restore_refusal(refund: Refund) -> str | None:
    """Un remboursement revient sur une ligne encore payée, ouverte, et qui peut encore être
    remboursée de ce montant."""
    ligne = ExpenseLine.all_objects.select_related("expense", "vehicle", "category").get(
        pk=refund.line_id
    )
    nom = f"« {ligne.label} »"
    if ligne.deleted_at is not None:
        return f"{nom} a été retirée de sa dépense : ce remboursement ne peut plus revenir."
    # Le véhicule d'abord : la dépense d'une reprise annulée ne reviendra jamais (D51).
    obstacle = _vehicle_obstacle(ligne.vehicle)
    if obstacle:
        return obstacle
    if ligne.expense.deleted_at is not None:
        return f"La dépense de {nom} est à la corbeille : restaure-la d'abord."
    refus = expense_services.expense_lock_refusal(ligne.expense)
    if refus:
        return refus
    if ligne.expense.is_personal:
        return f"{nom} est devenue un achat personnel : un remboursement n'y a plus de sens."
    try:
        marge = expense_services.line_refund_room(ligne)
    except ValidationError as erreur:
        return " ".join(erreur.messages)
    probleme = refund_problem(refund.amount, marge.allocated, marge.others)
    if probleme:
        return f"Ce remboursement ne tient plus sur {nom}. {probleme}"
    return None


def attachment_restore_refusal(attachment: Attachment) -> str | None:
    """Une facture revient sur une dépense présente et ouverte ; une photo, sur une fiche
    présente (les photos restent permises sur un véhicule vendu, D50)."""
    if attachment.expense_id is not None:
        depense = Expense.all_objects.get(pk=attachment.expense_id)
        # Le véhicule d'abord : la dépense d'une reprise annulée ne reviendra jamais (D51).
        for ligne in ExpenseLine.objects.filter(expense=depense).select_related("vehicle"):
            obstacle = _vehicle_obstacle(ligne.vehicle)
            if obstacle:
                return obstacle
        if depense.deleted_at is not None:
            return "La dépense de cette facture est à la corbeille : restaure-la d'abord."
        refus = expense_services.expense_lock_refusal(depense)
        if refus:
            return refus
    if attachment.vehicle_id is not None:
        obstacle = _vehicle_obstacle(Vehicle.all_objects.get(pk=attachment.vehicle_id))
        if obstacle:
            return obstacle
    return None


# ---------------------------------------------------------------------------
# Ce que contient la corbeille
# ---------------------------------------------------------------------------


def _deleted_by(objet) -> str:
    """L'associé qui a mis cet élément à la corbeille, lu dans son historique (D30) ; vide
    pour un élément sans historique (photo, facture, contact) ou mis là par une commande."""
    historique = getattr(type(objet), "history", None)
    if historique is None:
        return ""
    trace = (
        objet.history.filter(deleted_at=objet.deleted_at)
        .select_related("history_user")
        .order_by("history_date")
        .first()
    )
    return trace.history_user.name if trace is not None and trace.history_user else ""


def _place(vehicle: Vehicle | None) -> str:
    return vehicle.display_name if vehicle is not None else "Garage"


def _item(kind: str, objet, title: str, detail: str, refusal: str | None) -> TrashItem:
    return TrashItem(kind, objet.pk, title, detail, objet.deleted_at, _deleted_by(objet), refusal)


def _trashed(model: type[models.Model]) -> models.QuerySet:
    return model.all_objects.filter(deleted_at__isnull=False).order_by("-deleted_at", "-id")


def _vehicles() -> list[TrashItem]:
    return [
        _item("vehicule", v, v.display_name, v.get_status_display(), vehicle_restore_refusal(v))
        for v in _trashed(Vehicle)
    ]


def _expenses() -> list[TrashItem]:
    elements = []
    for depense in _trashed(Expense).select_related("supplier"):
        lignes = list(ExpenseLine.objects.filter(expense=depense).select_related("vehicle"))
        titre = f"Dépense du {depense.date:%d/%m/%Y}"
        if depense.supplier:
            titre += f" · {depense.supplier.name}"
        quoi = ", ".join(dict.fromkeys(ligne.label for ligne in lignes))
        ou = ", ".join(dict.fromkeys(_place(ligne.vehicle) for ligne in lignes))
        detail = " · ".join(p for p in (format_euros(depense.total_paid), quoi, ou) if p)
        elements.append(_item("depense", depense, titre, detail, expense_restore_refusal(depense)))
    return elements


def _parts() -> list[TrashItem]:
    return [
        _item("piece", p, f"« {p.label} »", _place(p.vehicle), part_restore_refusal(p))
        for p in _trashed(ExpenseLine).filter(expense__isnull=True).select_related("vehicle")
    ]


def _refunds() -> list[TrashItem]:
    elements = []
    for remboursement in _trashed(Refund).select_related("line__expense", "refunded_to"):
        ligne = remboursement.line
        montant = format_euros(remboursement.amount) if remboursement.amount else "à préciser"
        detail = (
            f"{montant} pour {remboursement.refunded_to.name} · dépense du "
            f"{ligne.expense.date:%d/%m/%Y}"
            if ligne.expense
            else montant
        )
        elements.append(
            _item(
                "remboursement",
                remboursement,
                f"Remboursement de « {ligne.label} »",
                detail,
                refund_restore_refusal(remboursement),
            )
        )
    return elements


def _attachments() -> list[TrashItem]:
    elements = []
    for fichier in _trashed(Attachment).select_related("vehicle", "expense"):
        if fichier.expense is not None:
            detail = f"Facture de la dépense du {fichier.expense.date:%d/%m/%Y}"
        elif fichier.vehicle is not None:
            detail = f"{fichier.get_kind_display()} de {fichier.vehicle.display_name}"
        else:
            detail = fichier.get_kind_display()
        elements.append(
            _item(
                "fichier", fichier, fichier.file_name, detail, attachment_restore_refusal(fichier)
            )
        )
    return elements


def _contacts() -> list[TrashItem]:
    return [
        _item("contact", c, c.name, " · ".join(p for p in (c.phone, c.city) if p), None)
        for c in _trashed(Contact)
    ]


GROUPS: list[tuple[str, Callable[[], list[TrashItem]]]] = [
    ("Véhicules", _vehicles),
    ("Dépenses", _expenses),
    ("Pièces à commander", _parts),
    ("Remboursements", _refunds),
    ("Photos et factures", _attachments),
    ("Contacts", _contacts),
]


def trash_groups() -> list[TrashGroup]:
    """Les groupes non vides de la corbeille, le plus récent d'abord dans chacun."""
    groupes = [TrashGroup(titre, lister()) for titre, lister in GROUPS]
    return [groupe for groupe in groupes if groupe.items]


# ---------------------------------------------------------------------------
# Restaurer
# ---------------------------------------------------------------------------

# Clé de l'adresse → (modèle, filtre de ce qui est listé, règle, message de confirmation).
KINDS: dict[str, tuple[type[models.Model], dict, Callable, str]] = {
    "vehicule": (Vehicle, {}, vehicle_restore_refusal, "La fiche est sortie de la corbeille."),
    "depense": (
        Expense,
        {},
        expense_restore_refusal,
        "La dépense est sortie de la corbeille : elle compte de nouveau dans les comptes.",
    ),
    "piece": (
        ExpenseLine,
        {"expense__isnull": True},
        part_restore_refusal,
        "La pièce est de nouveau à commander.",
    ),
    "remboursement": (
        Refund,
        {},
        refund_restore_refusal,
        "Le remboursement est sorti de la corbeille.",
    ),
    "fichier": (
        Attachment,
        {},
        attachment_restore_refusal,
        "Le fichier est sorti de la corbeille.",
    ),
    "contact": (Contact, {}, lambda contact: None, "Le contact est sorti de la corbeille."),
}


@transaction.atomic
def restore(kind: str, pk: int) -> str:
    """Restaure un élément de la corbeille et renvoie le message de confirmation.

    La règle est relue dans la transaction : la page a pu rester ouverte pendant qu'un
    véhicule était vendu ou un autre achat enregistré. Refus : `ValidationError`.
    """
    if kind not in KINDS:
        raise ValidationError(ALREADY_RESTORED)
    modele, filtre, regle, message = KINDS[kind]
    objet = modele.all_objects.filter(pk=pk, deleted_at__isnull=False, **filtre).first()
    if objet is None:
        raise ValidationError(ALREADY_RESTORED)
    refus = regle(objet)
    if refus:
        raise ValidationError(refus)
    objet.restore()
    if kind == "depense":
        # Un achat restauré redonne sa date d'achat au véhicule (D42).
        expense_services.sync_purchase_date(objet)
    if kind == "remboursement":
        # La pièce peut redevenir « Retourné » (D45).
        expense_services.sync_returned_status(
            ExpenseLine.objects.select_related("category").get(pk=objet.line_id)
        )
    return message

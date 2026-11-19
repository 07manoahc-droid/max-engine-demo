"""Logique métier des véhicules : statuts, création, achat et photos.

Les montants ne sont jamais calculés ici : la vérification des paiements vient de
`accounting/services.py`.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from accounting.services import Payment, payment_problem
from core import thumbnails
from core.models import Attachment, Contact, ExpenseCategory, Partner
from expenses.models import Expense, ExpenseLine, ExpensePayment, PartTransfer

from .models import StatusChange, Vehicle

Status = Vehicle.Status

# Un véhicule « au garage » est en cours : ni vendu, ni abandonné.
NOT_IN_GARAGE = (Status.SOLD, Status.ABANDONED)

# Statuts qu'on choisit à la main une fois l'achat enregistré (D35).
STATUSES_AFTER_PURCHASE = (
    Status.PURCHASED,
    Status.IN_REPAIR,
    Status.READY_FOR_SALE,
    Status.FOR_SALE,
    Status.ABANDONED,
)

PURCHASE_LABEL = "Achat du véhicule"


def garage_vehicles() -> QuerySet[Vehicle]:
    """Les véhicules au garage, par numéro."""
    return Vehicle.objects.exclude(status__in=NOT_IN_GARAGE).order_by("number")


def vehicles_open_to_expenses() -> QuerySet[Vehicle]:
    """Les véhicules proposés sur une dépense : ceux du garage, plus les vendus à la fiche
    rouverte. Rouvrir sert à corriger ce qui est payé : on doit pouvoir y mettre une dépense
    ou y ramener une ligne partie sur le garage par erreur (D50)."""
    rouverts = Vehicle.objects.filter(status=Status.SOLD, reopened_at__isnull=False)
    return (garage_vehicles() | rouverts).order_by("number")


def purchase_lines(vehicle: Vehicle) -> QuerySet[ExpenseLine]:
    """Les lignes « Achat véhicule » payées du véhicule, hors corbeille (une en général)."""
    return ExpenseLine.objects.filter(
        vehicle=vehicle,
        category__code=ExpenseCategory.VEHICLE_PURCHASE,
        expense__isnull=False,
        expense__deleted_at__isnull=True,
    )


def has_purchase(vehicle: Vehicle) -> bool:
    """Le véhicule a une ligne « Achat véhicule » payée, hors corbeille."""
    return purchase_lines(vehicle).exists()


def allowed_statuses(current: str, purchased: bool) -> list[str]:
    """Statuts proposés par le menu « Changer le statut » (D35, D39), sans le statut actuel.

    - Réservé et Vendu : aucun ; on en sort en annulant l'acompte ou la vente (1.5).
    - Achat enregistré : Acheté → En vente dans n'importe quel ordre, ou Abandonné ;
      jamais le retour en attente d'achat.
    - Sans achat : Abandonné, ou le retour en attente d'achat (depuis Abandonné, ou
      depuis un statut d'après l'achat quand la dépense d'achat est à la corbeille).
      Le passage en Acheté se fait par le formulaire d'achat.
    """
    if current in (Status.RESERVED, Status.SOLD):
        return []
    if purchased:
        choix = STATUSES_AFTER_PURCHASE
    else:
        choix = (Status.WAITING_PURCHASE, Status.ABANDONED)
    return [statut for statut in choix if statut != current]


def _record_status_change(vehicle: Vehicle, from_status: str, partner: Partner | None) -> None:
    StatusChange.objects.create(
        vehicle=vehicle, from_status=from_status, to_status=vehicle.status, changed_by=partner
    )


@transaction.atomic
def create_vehicle(vehicle: Vehicle, partner: Partner | None) -> Vehicle:
    """Enregistre un nouveau véhicule « En attente d'achat » (le numéro est attribué à
    l'enregistrement) et le premier changement de statut de sa frise."""
    vehicle.status = Status.WAITING_PURCHASE
    vehicle.save()
    _record_status_change(vehicle, "", partner)
    return vehicle


@transaction.atomic
def change_status(vehicle: Vehicle, new_status: str, partner: Partner | None) -> Vehicle:
    """Change le statut si `allowed_statuses` le permet, et garde la trace du changement."""
    if new_status not in allowed_statuses(vehicle.status, has_purchase(vehicle)):
        raise ValidationError(_refusal_message(vehicle.status, new_status))
    return apply_status(vehicle, new_status, partner)


def apply_status(vehicle: Vehicle, new_status: str, partner: Partner | None) -> Vehicle:
    """Applique un statut et garde la trace du changement, sans vérifier s'il est permis :
    c'est à l'appelant de le faire (menu « Changer le statut », acompte, vente).

    Le passage en « En vente » renseigne la date de mise en vente si elle est vide.
    """
    ancien = vehicle.status
    vehicle.status = new_status
    if new_status == Status.FOR_SALE and vehicle.listed_on is None:
        vehicle.listed_on = timezone.localdate()
    vehicle.save()
    _record_status_change(vehicle, ancien, partner)
    return vehicle


def _refusal_message(current: str, new_status: str) -> str:
    """Explique un changement de statut refusé, et quoi faire à la place."""
    if new_status == current:
        return "Le véhicule a déjà ce statut."
    if new_status == Status.PURCHASED and current == Status.WAITING_PURCHASE:
        return "Pour passer en « Acheté », enregistre l'achat avec son prix."
    if current == Status.SOLD:
        return "Ce véhicule est vendu : pour changer son statut, il faut annuler la vente."
    if current == Status.RESERVED:
        return "Ce véhicule est réservé : pour changer son statut, il faut annuler l'acompte."
    if new_status in (Status.RESERVED, Status.SOLD):
        return "Ce statut s'obtient en enregistrant un acompte ou une vente."
    if new_status == Status.WAITING_PURCHASE:
        return "L'achat est déjà enregistré : le véhicule ne peut plus revenir en attente d'achat."
    return "Ce changement de statut n'est pas possible."


@transaction.atomic
def record_purchase(
    vehicle: Vehicle,
    *,
    price: Decimal,
    date: dt.date,
    payments: Sequence[Payment],
    seller: Contact | None,
    partner: Partner | None,
) -> Expense:
    """Enregistre l'achat d'un véhicule en attente d'achat, tout ou rien.

    Crée la dépense (montant payé = prix), sa ligne « Achat véhicule » et un paiement par
    payeur ; enregistre le vendeur (créé s'il est nouveau), la date d'achat, et le passage
    en « Acheté ». `payments` : un `Payment` par associé, dont la clé est le `Partner`.
    """
    if vehicle.status != Status.WAITING_PURCHASE:
        raise ValidationError("Seul un véhicule en attente d'achat peut être acheté.")
    probleme = payment_problem(price, payments)
    if probleme:
        raise ValidationError(probleme)
    if any(not paiement.partner.is_active for paiement in payments):
        raise ValidationError("Un des payeurs n'est plus un associé actif.")

    # Le passage en « Acheté » se fait d'abord en base, sous condition : de deux envois
    # simultanés (double-clic), un seul trouve encore le véhicule en attente d'achat.
    reserve = Vehicle.objects.filter(pk=vehicle.pk, status=Status.WAITING_PURCHASE).update(
        status=Status.PURCHASED
    )
    if not reserve:
        raise ValidationError("L'achat de ce véhicule est déjà enregistré.")
    # Une ligne d'achat déjà là (saisie à la main, restaurée de la corbeille) : pas de second.
    if has_purchase(vehicle):
        raise ValidationError(
            "Ce véhicule a déjà une dépense « Achat véhicule ». Passe-le en « Acheté » avec "
            "« Changer le statut »."
        )

    if seller is not None and seller.pk is None:
        seller.save()
    categorie = ExpenseCategory.objects.get(code=ExpenseCategory.VEHICLE_PURCHASE)
    depense = Expense.objects.create(date=date, total_paid=price)
    ExpenseLine.objects.create(
        expense=depense,
        vehicle=vehicle,
        category=categorie,
        label=PURCHASE_LABEL,
        quantity=Decimal("1"),
        unit_price=price,
    )
    for paiement in payments:
        ExpensePayment.objects.create(
            expense=depense, partner=paiement.partner, amount=paiement.amount
        )

    vehicle.status = Status.PURCHASED
    vehicle.purchased_on = date
    if seller is not None:
        vehicle.seller = seller
    vehicle.save()
    _record_status_change(vehicle, Status.WAITING_PURCHASE, partner)
    return depense


# ---------------------------------------------------------------------------
# Verrou d'un véhicule vendu (D22)
# ---------------------------------------------------------------------------


@transaction.atomic
def reopen_vehicle(vehicle: Vehicle, reason: str) -> Vehicle:
    """« Rouvrir la fiche » : déverrouille les dépenses, motif obligatoire. Le véhicule reste
    vendu, sans changement de statut ; la trace passe par l'historique de `Vehicle`."""
    motif = reason.strip()
    if not motif:
        raise ValidationError({"reason": "Indique pourquoi tu rouvres la fiche."})
    # Sous condition : de deux envois simultanés, un seul rouvre la fiche.
    rouverte = Vehicle.objects.filter(
        pk=vehicle.pk, status=Status.SOLD, reopened_at__isnull=True
    ).update(reopened_at=timezone.now(), reopen_reason=motif)
    if not rouverte:
        raise ValidationError("Cette fiche n'est plus verrouillée. Recharge la page.")
    # Relu avant d'enregistrer, pour créer la ligne d'historique que `update()` ne crée pas.
    vehicle.refresh_from_db()
    vehicle.save()
    return vehicle


@transaction.atomic
def lock_vehicle(vehicle: Vehicle) -> Vehicle:
    """« Verrouiller la fiche » : les dépenses ne se modifient plus. Le motif de la
    réouverture reste dans l'historique de `Vehicle`."""
    verrouillee = Vehicle.objects.filter(
        pk=vehicle.pk, status=Status.SOLD, reopened_at__isnull=False
    ).update(reopened_at=None)
    if not verrouillee:
        raise ValidationError("Cette fiche n'est plus rouverte. Recharge la page.")
    vehicle.refresh_from_db()
    vehicle.save()
    return vehicle


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

Stage = Attachment.Stage

# Ordre de la galerie : le parcours du véhicule.
PHOTO_STAGES = (Stage.LISTING, Stage.BEFORE, Stage.DURING, Stage.AFTER)
# Photo de la carte : la première de l'étape la plus avancée.
COVER_PRIORITY = {Stage.AFTER: 0, Stage.DURING: 1, Stage.BEFORE: 2, Stage.LISTING: 3}

PHOTO_MAX_BYTES = 20 * 1024 * 1024
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
FILE_NAME_MAX = 100  # le chemin complet (vehicules/<n>/<nom>) tient dans 255 caractères
HEIC_MESSAGE = (
    "Ce format n'est pas lu. Envoie la photo en JPEG (réglage « Le plus compatible » de l'iPhone)."
)
_HEIC_BRANDS = (
    b"heic",
    b"heix",
    b"heim",
    b"heis",
    b"hevc",
    b"hevx",
    b"hevm",
    b"hevs",
    b"heif",
    b"mif1",
    b"msf1",
)


def image_format(head: bytes) -> str | None:
    """Format réel d'une image d'après ses premiers octets : jpeg, png, webp, heic ou None."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "heic"
    return None


def photo_problem(name: str, size: int, head: bytes) -> str | None:
    """Pourquoi un fichier envoyé n'est pas accepté comme photo, ou None.

    Le contenu compte, pas seulement le nom : une photo HEIC renommée en .jpg est
    refusée avec le même conseil.
    """
    extension = Path(name).suffix.lower()
    format_reel = image_format(head)
    if extension in (".heic", ".heif") or format_reel == "heic":
        return HEIC_MESSAGE
    if extension not in PHOTO_EXTENSIONS or format_reel is None:
        return "Ce n'est pas une photo lisible : envoie un fichier JPEG, PNG ou WebP."
    if size > PHOTO_MAX_BYTES:
        return "Cette photo dépasse 20 Mo : envoie-la en taille réduite."
    return None


def short_file_name(name: str) -> str:
    """Nom de fichier raccourci, extension gardée."""
    chemin = Path(name)
    return chemin.stem[: FILE_NAME_MAX - len(chemin.suffix)] + chemin.suffix.lower()


def add_photos(vehicle: Vehicle, files: Iterable, stage: str):
    """Ajoute des photos à une étape de la galerie, à la suite des photos existantes.

    Les fichiers refusés n'empêchent pas d'ajouter les autres. Renvoie
    (photos ajoutées, [(nom du fichier refusé, raison)]).
    """
    if stage not in PHOTO_STAGES:
        raise ValidationError("Choisis l'étape des photos : annonce, avant, pendant ou après.")
    fichiers = list(files)
    if not fichiers:
        raise ValidationError("Choisis au moins une photo.")
    dernier = (
        Attachment.objects.filter(vehicle=vehicle, kind=Attachment.Kind.PHOTO, stage=stage)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    )
    ordre = 0 if dernier is None else dernier + 1
    ajoutees, refusees = [], []
    for fichier in fichiers:
        debut = fichier.read(16)
        fichier.seek(0)
        probleme = photo_problem(fichier.name, fichier.size, debut)
        if probleme:
            refusees.append((fichier.name, probleme))
            continue
        fichier.name = short_file_name(fichier.name)
        ajoutees.append(
            Attachment.objects.create(
                file=fichier,
                kind=Attachment.Kind.PHOTO,
                stage=stage,
                vehicle=vehicle,
                order=ordre,
            )
        )
        ordre += 1
    thumbnails.prepare(ajoutees)
    return ajoutees, refusees


def photo_stages(vehicle: Vehicle) -> list[tuple[str, str, list[Attachment]]]:
    """La galerie par étape, dans l'ordre du parcours : (étape, libellé, photos)."""
    photos = Attachment.objects.filter(vehicle=vehicle, kind=Attachment.Kind.PHOTO).order_by(
        "order", "id"
    )
    par_etape: dict[str, list[Attachment]] = {etape: [] for etape in PHOTO_STAGES}
    for photo in photos:
        if photo.stage in par_etape:
            par_etape[photo.stage].append(photo)
    return [(etape, etape.label, par_etape[etape]) for etape in PHOTO_STAGES]


def cover_photos(vehicles: Iterable[Vehicle]) -> dict[int, Attachment]:
    """Photo de couverture de chaque véhicule qui en a une : la première photo de l'étape
    la plus avancée (Après, sinon Pendant, Avant, Annonce)."""
    photos = Attachment.objects.filter(
        vehicle__in=list(vehicles), kind=Attachment.Kind.PHOTO, stage__in=PHOTO_STAGES
    )
    couvertures: dict[int, Attachment] = {}
    for photo in sorted(photos, key=lambda p: (COVER_PRIORITY[p.stage], p.order, p.id)):
        couvertures.setdefault(photo.vehicle_id, photo)
    return couvertures


def remove_photo(photo: Attachment) -> None:
    """Met une photo à la corbeille (suppression logique : le fichier reste sur le disque)."""
    photo.soft_delete()


# ---------------------------------------------------------------------------
# Corbeille d'une fiche (D74)
# ---------------------------------------------------------------------------


def vehicle_trash_refusal(vehicle: Vehicle) -> str | None:
    """Pourquoi cette fiche ne part pas à la corbeille, ou None (D74).

    Seule une fiche sans argent y part, par exemple créée par erreur : ni dépense payée, ni
    pièce à commander, ni acompte, ni vente (même terminés ou annulés), ni transfert de
    pièce. Ses photos, interventions, tâches et annonces partent avec elle, cachées (D76).
    """
    nom = vehicle.display_name
    if vehicle.sales.exists():
        return f"{nom} a une vente, même annulée : sa fiche ne part pas à la corbeille."
    if vehicle.deposits.exists():
        return f"{nom} a eu un acompte, qui compte dans l'argent : sa fiche reste."
    lignes = ExpenseLine.objects.filter(vehicle=vehicle)
    if lignes.filter(expense__isnull=False, expense__deleted_at__isnull=True).exists():
        return (
            f"{nom} a des dépenses. Mets d'abord ses dépenses à la corbeille (ou déplace leurs "
            "lignes dans « Modifier la dépense »), puis recommence."
        )
    if lignes.filter(expense__isnull=True).exists():
        return f"Retire d'abord les pièces à commander de {nom}, puis recommence."
    if PartTransfer.objects.filter(Q(from_vehicle=vehicle) | Q(to_vehicle=vehicle)).exists():
        return f"{nom} a un transfert de pièce : préviens Mathis."
    return None


@transaction.atomic
def trash_vehicle(vehicle: Vehicle) -> Vehicle:
    """Met une fiche sans argent à la corbeille. Relue en base : la page a pu rester ouverte
    pendant qu'on y ajoutait une dépense."""
    vehicule = Vehicle.objects.filter(pk=vehicle.pk).first()
    if vehicule is None:
        raise ValidationError("Cette fiche est déjà dans la corbeille. Recharge la page.")
    refus = vehicle_trash_refusal(vehicule)
    if refus:
        raise ValidationError(refus)
    vehicule.soft_delete()
    return vehicule

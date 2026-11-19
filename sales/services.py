"""Logique métier des ventes : acomptes, statut « Réservé », vente, reprise et annulation.

Les montants ne sont jamais calculés ici : les calculs d'argent sont dans
`accounting/services.py`.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone

from accounting import queries
from accounting.services import (
    ZERO,
    DepositInput,
    Payment,
    amounts_by_partner,
    deposit_receipts_problem,
    deposit_total,
    receipts_problem,
    sale_left_to_receive,
    sale_price_problem,
    split_equally,
    trade_in_problem,
)
from core.models import Contact, Partner
from expenses.models import ExpenseLine, PartTransfer, Refund
from expenses.services import locked_among, noted_parts, vehicle_paid_lines
from vehicles.models import Vehicle
from vehicles.services import (
    apply_status,
    create_vehicle,
    has_purchase,
    purchase_lines,
    record_purchase,
)

from .models import Deposit, DepositReceipt, Sale, SaleReceipt

Status = Vehicle.Status
DepositStatus = Deposit.Status

# Statuts depuis lesquels on peut prendre un acompte (D47).
BEFORE_RESERVATION = (
    Status.PURCHASED,
    Status.IN_REPAIR,
    Status.READY_FOR_SALE,
    Status.FOR_SALE,
)
# Statuts depuis lesquels on peut vendre.
BEFORE_SALE = (*BEFORE_RESERVATION, Status.RESERVED)
# Statuts où la vente est l'action attendue.
READY_TO_SELL = (Status.READY_FOR_SALE, Status.FOR_SALE, Status.RESERVED)
# Ce que devient un acompte annulé.
DEPOSIT_OUTCOMES = (DepositStatus.REFUNDED, DepositStatus.KEPT)


@dataclass(frozen=True)
class TradeIn:
    """Reprise saisie avec une vente : le véhicule repris, pas encore enregistré, et sa valeur."""

    vehicle: Vehicle
    value: Decimal


@dataclass(frozen=True)
class DepositDetail:
    """Un acompte et ses versements hors corbeille, prêts à afficher (D89)."""

    deposit: Deposit
    receipts: tuple[DepositReceipt, ...]
    total: Decimal

    @property
    def receivers(self) -> list[Partner]:
        """Ceux qui ont reçu une partie de l'acompte, dans l'ordre des versements."""
        return list(dict.fromkeys(versement.partner for versement in self.receipts))

    @property
    def receivers_label(self) -> str:
        """« Mathis », « Mathis et Axel »."""
        return " et ".join(associe.name for associe in self.receivers)

    @property
    def by_receiver(self) -> list[tuple[Partner, Decimal]]:
        """Ce que chacun a reçu en tout sur cet acompte, dans l'ordre des versements."""
        return list(amounts_by_partner(Payment(v.partner, v.amount) for v in self.receipts).items())


RECEIPTS_IN_ORDER = DepositReceipt.objects.select_related("partner").order_by("date", "id")


def deposit_detail(deposit: Deposit) -> DepositDetail:
    """L'acompte et ses versements hors corbeille ; lus d'avance s'ils ont été préchargés
    (`Prefetch("receipts", RECEIPTS_IN_ORDER)`), sinon lus ici."""
    if "receipts" in getattr(deposit, "_prefetched_objects_cache", {}):
        versements = tuple(deposit.receipts.all())
    else:
        versements = tuple(RECEIPTS_IN_ORDER.filter(deposit=deposit))
    return DepositDetail(deposit, versements, deposit_total(v.amount for v in versements))


def active_deposit(vehicle: Vehicle) -> Deposit | None:
    """L'acompte en cours du véhicule, ou None (il n'y en a qu'un, D47)."""
    return vehicle.deposits.filter(status=DepositStatus.ACTIVE).select_related("buyer").first()


def active_deposit_detail(vehicle: Vehicle) -> DepositDetail | None:
    acompte = active_deposit(vehicle)
    return deposit_detail(acompte) if acompte is not None else None


def past_deposits(vehicle: Vehicle) -> list[DepositDetail]:
    """Les acomptes qui ne sont plus en cours, le plus récent d'abord, avec leurs versements."""
    return [
        deposit_detail(acompte)
        for acompte in vehicle.deposits.exclude(status=DepositStatus.ACTIVE)
        .order_by("-date", "-id")
        .prefetch_related(Prefetch("receipts", queryset=RECEIPTS_IN_ORDER))
    ]


def deposit_refusal(vehicle: Vehicle) -> str | None:
    """Pourquoi ce véhicule ne peut pas recevoir d'acompte, ou None s'il le peut (D47)."""
    if (
        vehicle.status == Status.RESERVED
        or vehicle.deposits.filter(status=DepositStatus.ACTIVE).exists()
    ):
        return "Ce véhicule a déjà un acompte en cours."
    if vehicle.status == Status.SOLD:
        return "Ce véhicule est vendu : il ne peut plus recevoir d'acompte."
    if vehicle.status == Status.ABANDONED:
        return "Ce véhicule est abandonné. Change d'abord son statut."
    if vehicle.status not in BEFORE_RESERVATION or not has_purchase(vehicle):
        return "Enregistre d'abord l'achat de ce véhicule."
    return None


def status_before_reservation(vehicle: Vehicle) -> str:
    """Le statut d'avant la réservation, lu dans l'historique des statuts (D47).

    « En vente » s'il n'est pas retrouvé (véhicule passé en « Réservé » hors de l'écran).
    """
    passage = (
        vehicle.status_changes.filter(to_status=Status.RESERVED)
        .order_by("-changed_at", "-id")
        .first()
    )
    if passage is not None and passage.from_status in BEFORE_RESERVATION:
        return passage.from_status
    return Status.FOR_SALE


def _check_receipts(
    receipts: Sequence[Payment], former: Sequence[int] = (), what: str = "L'acompte"
) -> None:
    """Refuse des versements vides, nuls ou en double, ou un associé parti. Un ancien
    associé reste possible sur un versement qu'il avait déjà reçu (`former`)."""
    total = deposit_total(v.amount for v in receipts)
    probleme = deposit_receipts_problem(total, receipts, what)
    if probleme:
        raise ValidationError(probleme)
    for versement in receipts:
        if not versement.partner.is_active and versement.partner.pk not in former:
            raise ValidationError(
                f"{versement.partner.name} n'est plus associé : choisis un autre nom."
            )


def _create_receipts(deposit: Deposit, date: dt.date, receipts: Sequence[Payment]) -> None:
    for versement in receipts:
        DepositReceipt.objects.create(
            deposit=deposit, partner=versement.partner, amount=versement.amount, date=date
        )


def _active_deposit_again(deposit: Deposit) -> None:
    """Relit l'acompte en base : un onglet resté ouvert ne doit pas toucher un acompte
    annulé ou déduit d'une vente entre-temps."""
    deposit.refresh_from_db()
    if deposit.status != DepositStatus.ACTIVE:
        raise ValidationError("Cet acompte n'est plus en cours : il ne se modifie plus.")


@transaction.atomic
def record_deposit(
    vehicle: Vehicle,
    *,
    date: dt.date,
    receipts: Sequence[Payment],
    buyer: Contact | None,
    partner: Partner | None,
) -> Deposit:
    """Enregistre un acompte : le véhicule passe en « Réservé » (D19), tout ou rien.

    `receipts` : un `Payment` par associé qui a reçu une partie de l'argent ; chacun devient
    un versement à la date de l'acompte (D89). L'acheteur est créé s'il est nouveau.
    """
    refus = deposit_refusal(vehicle)
    if refus:
        raise ValidationError(refus)
    _check_receipts(receipts)

    # `vehicle` a pu être lu avant un changement de statut (deux onglets) : le passage en
    # « Réservé » se fait donc en base, sous condition que le statut n'ait pas bougé.
    reserve = Vehicle.objects.filter(pk=vehicle.pk, status=vehicle.status).update(
        status=Status.RESERVED
    )
    if not reserve:
        raise ValidationError("Ce véhicule a déjà changé de statut. Recharge la page.")

    if buyer is not None and buyer.pk is None:
        buyer.save()
    acompte = Deposit.objects.create(vehicle=vehicle, date=date, buyer=buyer)
    _create_receipts(acompte, date, receipts)
    apply_status(vehicle, Status.RESERVED, partner)
    return acompte


@transaction.atomic
def update_deposit(deposit: Deposit, *, date: dt.date, buyer: Contact | None) -> Deposit:
    """Corrige la date de réservation et l'acheteur d'un acompte en cours. Les versements
    se corrigent un par un (`update_deposit_receipt`). L'ancienne valeur reste dans
    l'historique."""
    _active_deposit_again(deposit)
    if buyer is not None and buyer.pk is None:
        buyer.save()
    deposit.date = date
    deposit.buyer = buyer
    deposit.save()
    return deposit


@transaction.atomic
def add_deposit_receipts(
    deposit: Deposit, *, date: dt.date, receipts: Sequence[Payment]
) -> list[DepositReceipt]:
    """« Ajouter un versement » : l'acheteur complète un acompte en cours (D89). Un
    `Payment` par associé qui a reçu une partie de ce complément."""
    _active_deposit_again(deposit)
    _check_receipts(receipts, what="Le versement")
    avant = set(deposit.receipts.values_list("pk", flat=True))
    _create_receipts(deposit, date, receipts)
    return list(deposit.receipts.exclude(pk__in=avant).order_by("id"))


def _receipt_again(receipt: DepositReceipt) -> None:
    """Relit le versement et son acompte en base (onglet resté ouvert, double-clic)."""
    receipt.refresh_from_db()
    if receipt.is_deleted:
        raise ValidationError("Ce versement a déjà été retiré. Recharge la page.")
    _active_deposit_again(receipt.deposit)


@transaction.atomic
def update_deposit_receipt(
    receipt: DepositReceipt, *, amount: Decimal, date: dt.date, received_by: Partner
) -> DepositReceipt:
    """Corrige un versement d'un acompte en cours : montant, date, qui l'a reçu.
    L'ancienne valeur reste dans son historique."""
    _receipt_again(receipt)
    _check_receipts(
        [Payment(received_by, amount)], former=[receipt.partner_id], what="Le versement"
    )
    receipt.amount = amount
    receipt.date = date
    receipt.partner = received_by
    receipt.save()
    return receipt


@transaction.atomic
def remove_deposit_receipt(receipt: DepositReceipt) -> None:
    """Retire un versement d'un acompte en cours : il part à la corbeille et ne compte plus
    nulle part. Le dernier versement ne se retire pas : on annule alors l'acompte."""
    _receipt_again(receipt)
    if not receipt.deposit.receipts.exclude(pk=receipt.pk).exists():
        raise ValidationError(
            "C'est le seul versement de cet acompte : pour tout enlever, annule l'acompte."
        )
    receipt.soft_delete()


@transaction.atomic
def cancel_deposit(deposit: Deposit, outcome: str, partner: Partner | None) -> Deposit:
    """Annule un acompte en cours : le véhicule revient au statut d'avant la réservation.

    `outcome` vaut pour tous ses versements : `REFUNDED` (rendu à l'acheteur, aucun effet
    sur les comptes) ou `KEPT` (conservé : chaque versement reste encaissé par celui qui
    l'a reçu et compte dans la marge, D32).
    """
    if outcome not in DEPOSIT_OUTCOMES:
        raise ValidationError({"outcome": "Indique si l'acompte a été rendu ou conservé."})
    # Sous condition, comme à l'enregistrement : un double-clic n'annule qu'une fois.
    annule = Deposit.objects.filter(pk=deposit.pk, status=DepositStatus.ACTIVE).update(
        status=outcome
    )
    if not annule:
        raise ValidationError("Cet acompte n'est plus en cours. Recharge la page.")
    # Relu avant d'enregistrer : un objet périmé n'écrase pas une date corrigée entre-temps.
    # L'enregistrement crée la ligne d'historique, que `update()` ne crée pas.
    deposit.refresh_from_db()
    deposit.save()

    vehicule = Vehicle.all_objects.get(pk=deposit.vehicle_id)
    if vehicule.status == Status.RESERVED:
        apply_status(vehicule, status_before_reservation(vehicule), partner)
    return deposit


# ---------------------------------------------------------------------------
# Vente
# ---------------------------------------------------------------------------


def active_sale(vehicle: Vehicle) -> Sale | None:
    """La vente non annulée du véhicule, ou None (il n'y en a qu'une, D21)."""
    return (
        vehicle.sales.filter(cancelled_at__isnull=True)
        .select_related("buyer", "trade_in_vehicle")
        .first()
    )


def sold_sales() -> QuerySet[Sale]:
    """Les ventes non annulées des véhicules vendus (hors corbeille), la plus récente
    d'abord. Même règle que `active_sale`, pour tous les véhicules à la fois."""
    return (
        Sale.objects.filter(
            cancelled_at__isnull=True,
            vehicle__status=Status.SOLD,
            vehicle__deleted_at__isnull=True,
        )
        .select_related("vehicle", "vehicle__vehicle_type")
        .order_by("-date", "-id")
    )


def cancelled_sales(vehicle: Vehicle) -> QuerySet[Sale]:
    """Les ventes annulées du véhicule, la plus récente d'abord. Elles restent visibles."""
    return (
        vehicle.sales.filter(cancelled_at__isnull=False)
        .select_related("trade_in_vehicle")
        .order_by("-cancelled_at")
    )


def applied_deposit(sale: Sale) -> Deposit | None:
    """L'acompte déduit de cette vente, ou None."""
    return sale.deposits.filter(status=DepositStatus.APPLIED).first()


def applied_deposit_detail(sale: Sale) -> DepositDetail | None:
    acompte = applied_deposit(sale)
    return deposit_detail(acompte) if acompte is not None else None


def deposit_inputs(vehicle: Vehicle) -> list[DepositInput]:
    """Les versements des acomptes du véhicule, pour les calculs d'une vente."""
    return queries.load_deposits(deposit_ids=vehicle.deposits.values("pk"))


def sale_refusal(vehicle: Vehicle) -> str | None:
    """Pourquoi ce véhicule ne peut pas être vendu, ou None s'il le peut."""
    if vehicle.status == Status.SOLD or vehicle.sales.filter(cancelled_at__isnull=True).exists():
        return "Ce véhicule est déjà vendu."
    if vehicle.status == Status.ABANDONED:
        return "Ce véhicule est abandonné. Change d'abord son statut."
    if vehicle.status not in BEFORE_SALE or not has_purchase(vehicle):
        return "Enregistre d'abord l'achat de ce véhicule."
    return None


def _check_sale(
    price: Decimal,
    deposits: Sequence[DepositInput],
    trade_in_value: Decimal,
    receipts: Sequence[Payment],
    former_receivers: Sequence[int] = (),
) -> None:
    """Refuse un prix trop bas, des encaissements qui ne font pas le reste à encaisser ou
    un associé parti. Un ancien associé reste possible s'il avait déjà encaissé cette vente
    (`former_receivers`)."""
    reste = sale_left_to_receive(price, deposits, trade_in_value)
    probleme = sale_price_problem(price, reste) or receipts_problem(reste, receipts)
    if probleme:
        raise ValidationError(probleme)
    if any(
        not recu.partner.is_active and recu.partner.pk not in former_receivers for recu in receipts
    ):
        raise ValidationError("Un des associés qui ont encaissé n'est plus actif.")


def _record_trade_in(
    trade_in: TradeIn, date: dt.date, buyer: Contact | None, partner: Partner | None
) -> Vehicle:
    """Fait entrer le véhicule repris au garage : acheté à l'acheteur pour la valeur de reprise,
    payé pour moitié par chaque associé. Le partage suit l'ordre de `active_partners`, comme
    dans les positions : la reprise reste neutre au centime près, même sur une valeur impaire."""
    associes = queries.active_partners()
    probleme = trade_in_problem(trade_in.value, associes)
    if probleme:
        raise ValidationError(probleme)
    reprise = create_vehicle(trade_in.vehicle, partner)
    record_purchase(
        reprise,
        price=trade_in.value,
        date=date,
        payments=[
            Payment(associe, part)
            for associe, part in split_equally(trade_in.value, associes).items()
        ],
        seller=buyer,
        partner=partner,
    )
    return reprise


@transaction.atomic
def record_sale(
    vehicle: Vehicle,
    *,
    price: Decimal,
    date: dt.date,
    buyer: Contact | None,
    sold_where: str,
    notes: str,
    receipts: Sequence[Payment],
    partner: Partner | None,
    trade_in: TradeIn | None = None,
) -> Sale:
    """Enregistre la vente d'un véhicule, tout ou rien : le client (créé s'il est nouveau),
    le véhicule repris et son achat, un encaissement par associé, l'acompte en cours déduit
    du prix, le passage en « Vendu ».

    `receipts` : un `Payment` par associé, dont la clé est le `Partner` ; vide si l'acompte
    et la reprise couvrent le prix.
    """
    refus = sale_refusal(vehicle)
    if refus:
        raise ValidationError(refus)
    # Sous condition, comme pour l'acompte : de deux envois simultanés, un seul passe.
    vendu = Vehicle.objects.filter(pk=vehicle.pk, status=vehicle.status).update(status=Status.SOLD)
    if not vendu:
        raise ValidationError("Ce véhicule a déjà changé de statut. Recharge la page.")
    valeur_reprise = trade_in.value if trade_in else ZERO
    _check_sale(price, deposit_inputs(vehicle), valeur_reprise, receipts)

    if buyer is not None and buyer.pk is None:
        buyer.save()
    vente = Sale.objects.create(
        vehicle=vehicle,
        price=price,
        date=date,
        buyer=buyer,
        sold_where=sold_where,
        notes=notes,
        trade_in_vehicle=_record_trade_in(trade_in, date, buyer, partner) if trade_in else None,
        trade_in_value=valeur_reprise,
    )
    for recu in receipts:
        SaleReceipt.objects.create(sale=vente, partner=recu.partner, amount=recu.amount, date=date)
    acompte = active_deposit(vehicle)
    if acompte is not None:
        acompte.status = DepositStatus.APPLIED
        acompte.sale = vente
        acompte.save()
    apply_status(vehicle, Status.SOLD, partner)
    return vente


def _save_receipts(sale: Sale, receipts: Sequence[Payment]) -> None:
    """Encaissements de la vente : modifiés sur place, ajoutés, ou mis à la corbeille (D49).

    Ils suivent la date de la vente. Si un associé en avait plusieurs, un seul est gardé :
    le formulaire donne un montant par associé.
    """
    restants = list(sale.receipts.order_by("id"))
    for recu in receipts:
        actuel = next((e for e in restants if e.partner_id == recu.partner.pk), None)
        if actuel is None:
            SaleReceipt.objects.create(
                sale=sale, partner=recu.partner, amount=recu.amount, date=sale.date
            )
            continue
        restants.remove(actuel)
        if actuel.amount != recu.amount or actuel.date != sale.date:
            actuel.amount = recu.amount
            actuel.date = sale.date
            actuel.save()
    for encaissement in restants:
        encaissement.soft_delete()


@transaction.atomic
def update_sale(
    sale: Sale,
    *,
    price: Decimal,
    date: dt.date,
    buyer: Contact | None,
    sold_where: str,
    notes: str,
    receipts: Sequence[Payment],
) -> Sale:
    """Modifie une vente non annulée : prix, date, acheteur, notes, encaissements (D49).

    Les anciennes valeurs restent dans les historiques. La reprise ne change jamais ici :
    pour la corriger, on annule la vente. L'achat du véhicule repris ne suit pas non plus :
    sa date se corrige dans la dépense, son vendeur dans « Modifier la fiche » (D51).
    """
    # Relue en base : un objet périmé ne doit pas modifier une vente annulée entre-temps.
    sale.refresh_from_db()
    if sale.is_cancelled:
        raise ValidationError("Cette vente est annulée : elle ne se modifie plus.")
    anciens = list(sale.receipts.values_list("partner_id", flat=True))
    _check_sale(price, deposit_inputs(sale.vehicle), sale.trade_in_value, receipts, anciens)
    if buyer is not None and buyer.pk is None:
        buyer.save()
    sale.price = price
    sale.date = date
    sale.buyer = buyer
    sale.sold_where = sold_where
    sale.notes = notes
    sale.save()
    _save_receipts(sale, receipts)
    return sale


def trade_in_cancel_refusal(sale: Sale) -> str | None:
    """Pourquoi la reprise de cette vente en empêche l'annulation, ou None (D51).

    Annuler met à la corbeille le véhicule repris et son achat : il ne doit rien porter
    d'autre. Le message dit quoi faire quand un écran le permet, sinon de prévenir Mathis.
    """
    reprise = sale.trade_in_vehicle
    if reprise is None:
        return None
    nom = reprise.display_name
    # Même terminés ou annulés : un acompte conservé, par exemple, compte dans l'argent.
    if reprise.deposits.exists() or reprise.sales.exists():
        return (
            f"{nom} a déjà eu un acompte ou une vente : annuler cette vente fausserait les "
            "comptes. Préviens Mathis."
        )
    achats = list(purchase_lines(reprise).select_related("expense"))
    if len(achats) != 1:
        return f"L'achat de {nom} n'est plus celui de la reprise. Préviens Mathis."
    (achat,) = achats
    if achat.expense.lines.count() != 1:
        return f"L'achat de {nom} a une autre ligne. Préviens Mathis."
    autres = vehicle_paid_lines(reprise).exclude(pk=achat.pk)
    if autres.exists():
        return _other_expenses_refusal(nom, autres)
    if Refund.objects.filter(line=achat).exists():
        return (
            f"L'achat de {nom} a un remboursement. Mets-le d'abord à la corbeille, puis annule "
            "la vente."
        )
    # Un transfert de pièce porte de l'argent d'un véhicule à l'autre (D76).
    if PartTransfer.objects.filter(Q(from_vehicle=reprise) | Q(to_vehicle=reprise)).exists():
        return (
            f"{nom} a un transfert de pièce : annuler la vente fausserait les comptes. "
            "Préviens Mathis."
        )
    if noted_parts().filter(vehicle=reprise).exists():
        if reprise.status == Status.ABANDONED:
            return (
                f"{nom} a des pièces à commander. Change d'abord son statut, puis retire-les de "
                "sa fiche."
            )
        return f"Retire d'abord les pièces à commander de {nom}, puis annule la vente."
    return None


def _other_expenses_refusal(nom: str, lignes: QuerySet[ExpenseLine]) -> str:
    """Le véhicule repris a d'autres dépenses que son achat : on déplace leurs lignes ailleurs
    avec « Modifier la dépense ». Une dépense partagée avec un véhicule vendu se rouvre d'abord."""
    deplacer = (
        f"{nom} a d'autres dépenses que son achat. Déplace d'abord leurs lignes sur un autre "
        "véhicule ou sur le garage, dans « Modifier la dépense », puis annule la vente."
    )
    verrouilles = locked_among(
        ExpenseLine.objects.filter(expense__in=lignes.values("expense")).values_list(
            "vehicle_id", flat=True
        )
    )
    if not verrouilles:
        return deplacer
    if len(verrouilles) == 1:
        return f"{deplacer} Rouvre d'abord la fiche de {verrouilles[0].display_name}, vendu."
    noms = ", ".join(vehicule.display_name for vehicule in verrouilles)
    return f"{deplacer} Rouvre d'abord les fiches de {noms}, vendus."


def _trash_trade_in(reprise: Vehicle) -> None:
    """Met à la corbeille le véhicule repris et son achat : ils ne comptent plus nulle part."""
    purchase_lines(reprise).get().expense.soft_delete()
    reprise.soft_delete()


@transaction.atomic
def cancel_sale(sale: Sale, deposit_outcome: str | None, partner: Partner | None) -> Sale:
    """Annule une vente : ses encaissements ne comptent plus, et l'argent rendu à l'acheteur
    n'est pas une dépense (D34). Le véhicule revient en « En vente », fiche verrouillée.
    Le véhicule repris et son achat partent à la corbeille (D51).

    `deposit_outcome` : ce que devient l'acompte déduit de cette vente, `REFUNDED` ou `KEPT`.
    """
    # Sous condition : un double-clic n'annule qu'une fois.
    annulee = Sale.objects.filter(pk=sale.pk, cancelled_at__isnull=True).update(
        cancelled_at=timezone.now()
    )
    if not annulee:
        raise ValidationError("Cette vente est déjà annulée. Recharge la page.")
    # Relue avant d'enregistrer, pour créer la ligne d'historique que `update()` ne crée pas.
    sale.refresh_from_db()
    # Refus relu ici : le véhicule repris a pu recevoir une dépense depuis l'ouverture de l'écran.
    refus = trade_in_cancel_refusal(sale)
    if refus:
        raise ValidationError(refus)
    sale.save()
    if sale.trade_in_vehicle is not None:
        _trash_trade_in(sale.trade_in_vehicle)

    acompte = applied_deposit(sale)
    if acompte is not None:
        if deposit_outcome not in DEPOSIT_OUTCOMES:
            raise ValidationError({"outcome": "Indique si l'acompte a été rendu ou conservé."})
        acompte.status = deposit_outcome
        acompte.save()

    vehicule = Vehicle.all_objects.get(pk=sale.vehicle_id)
    # Une fiche rouverte puis revendue ne doit pas rester déverrouillée (D22).
    vehicule.reopened_at = None
    if vehicule.status == Status.SOLD:
        apply_status(vehicule, Status.FOR_SALE, partner)
    else:
        vehicule.save()
    return sale

"""Modèles des ventes : vente, encaissements, acomptes. La reprise est portée par Sale."""

from decimal import Decimal

from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.history import partner_history
from core.models import SoftDeleteModel, TimeStampedModel

MONEY = {"max_digits": 10, "decimal_places": 2}


class Sale(TimeStampedModel):
    """La vente d'un véhicule. Une vente annulée reste visible (`cancelled_at`) ;
    un véhicule n'a qu'une seule vente non annulée (D21).

    Règle vérifiée par les services : encaissements + acomptes appliqués + reprise = prix.
    """

    vehicle = models.ForeignKey(
        "vehicles.Vehicle", on_delete=models.PROTECT, related_name="sales", verbose_name="véhicule"
    )
    date = models.DateField("date", default=timezone.localdate)
    price = models.DecimalField("prix de vente", **MONEY, help_text="Reprise comprise.")
    buyer = models.ForeignKey(
        "core.Contact",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchases",
        verbose_name="acheteur",
    )
    sold_where = models.CharField("où et comment", max_length=200, blank=True)
    trade_in_vehicle = models.OneToOneField(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="trade_in_sale",
        verbose_name="véhicule repris",
    )
    trade_in_value = models.DecimalField("valeur de reprise", **MONEY, default=Decimal("0.00"))
    notes = models.TextField("notes", blank=True)
    cancelled_at = models.DateTimeField("annulée le", null=True, blank=True)

    history = partner_history()

    class Meta:
        verbose_name = "vente"
        verbose_name_plural = "ventes"
        ordering = ["-date", "-id"]
        constraints = [
            # Si Sale reçoit un jour un deleted_at, l'ajouter à cette condition (D21).
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(cancelled_at__isnull=True),
                name="sale_one_active_per_vehicle",
                violation_error_message="Ce véhicule a déjà une vente en cours.",
            ),
            models.CheckConstraint(condition=Q(price__gt=0), name="sale_price_positive"),
            models.CheckConstraint(
                condition=Q(trade_in_value__gte=0) & Q(trade_in_value__lte=F("price")),
                name="sale_trade_in_value_within_price",
            ),
            models.CheckConstraint(
                condition=~Q(trade_in_vehicle=F("vehicle")),
                name="sale_trade_in_is_another_vehicle",
            ),
        ]

    def __str__(self) -> str:
        return f"Vente du {self.date:%d/%m/%Y} ({self.price} €)"

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None


class SaleReceipt(SoftDeleteModel):
    """Argent reçu par un associé pour une vente. Retiré en modifiant la vente, il part à la
    corbeille et ne compte plus nulle part (D49)."""

    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, related_name="receipts", verbose_name="vente"
    )
    partner = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        related_name="sale_receipts",
        verbose_name="reçu par",
    )
    amount = models.DecimalField("montant", **MONEY)
    date = models.DateField("date", default=timezone.localdate)

    history = partner_history()

    class Meta:
        verbose_name = "encaissement"
        verbose_name_plural = "encaissements"
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="sale_receipt_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.partner} : {self.amount} €"


class Deposit(TimeStampedModel):
    """Acompte versé par un acheteur (D19). L'argent est dans ses versements
    (`DepositReceipt`, D89) : un ou plusieurs, chacun reçu par un associé.

    `legacy_amount` et `legacy_received_by` portaient l'acompte avant la 2.0.0. Ils sont
    vides depuis la migration `sales.0005`, qui les a recopiés en un versement ; ils restent
    pour que l'historique garde les anciennes valeurs (D47). Rien ne les lit pour compter.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "En cours"
        APPLIED = "APPLIED", "Déduit de la vente"
        REFUNDED = "REFUNDED", "Rendu"
        KEPT = "KEPT", "Conservé"

    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        related_name="deposits",
        verbose_name="véhicule",
    )
    buyer = models.ForeignKey(
        "core.Contact",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deposits",
        verbose_name="acheteur",
    )
    legacy_amount = models.DecimalField("montant (avant la 2.0)", **MONEY, null=True, blank=True)
    legacy_received_by = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legacy_deposits",
        verbose_name="reçu par (avant la 2.0)",
    )
    date = models.DateField("date de la réservation", default=timezone.localdate)
    status = models.CharField("état", max_length=20, choices=Status.choices, default=Status.ACTIVE)
    sale = models.ForeignKey(
        Sale,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deposits",
        verbose_name="vente",
    )

    history = partner_history()

    class Meta:
        verbose_name = "acompte"
        verbose_name_plural = "acomptes"
        ordering = ["date", "id"]
        constraints = [
            # Les anciens champs sont vides, ou remplis ensemble avec un montant positif
            # (le retour arrière de `sales.0005` les remplit de nouveau).
            models.CheckConstraint(
                condition=Q(legacy_amount__isnull=True, legacy_received_by__isnull=True)
                | Q(legacy_amount__gt=0, legacy_received_by__isnull=False),
                name="deposit_legacy_fields_together",
            ),
            models.CheckConstraint(
                condition=~Q(status="APPLIED") | Q(sale__isnull=False),
                name="deposit_applied_has_sale",
            ),
            # Un seul acompte en cours par véhicule (D47).
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(status="ACTIVE"),
                name="deposit_one_active_per_vehicle",
                violation_error_message="Ce véhicule a déjà un acompte en cours.",
            ),
        ]

    def __str__(self) -> str:
        return f"Acompte du {self.date:%d/%m/%Y} ({self.get_status_display()})"


class DepositReceipt(SoftDeleteModel):
    """Un versement d'acompte : de l'argent reçu par un associé, à une date (D89).

    Il suit l'état de son acompte (en cours, déduit, rendu, conservé). Retiré en corrigeant
    l'acompte, il part à la corbeille et ne compte plus nulle part, comme un encaissement.
    """

    deposit = models.ForeignKey(
        Deposit, on_delete=models.PROTECT, related_name="receipts", verbose_name="acompte"
    )
    partner = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        related_name="deposit_receipts",
        verbose_name="reçu par",
    )
    amount = models.DecimalField("montant", **MONEY)
    date = models.DateField("date", default=timezone.localdate)

    history = partner_history()

    class Meta:
        verbose_name = "versement d'acompte"
        verbose_name_plural = "versements d'acompte"
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="deposit_receipt_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.partner} : {self.amount} €"

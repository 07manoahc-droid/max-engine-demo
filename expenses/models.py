"""Modèles des dépenses : un paiement (Expense), ses payeurs, ses lignes, retours et transferts.

Aucun total n'est stocké : les montants répartis, coûts et soldes sont calculés par
`accounting/services.py`.
"""

from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from core.history import partner_history
from core.models import SoftDeleteModel, TimeStampedModel

MONEY = {"max_digits": 10, "decimal_places": 2}
QUANTITY = {"max_digits": 8, "decimal_places": 2}


class Expense(SoftDeleteModel):
    """Une dépense = un paiement réellement fait, frais de port compris (D10).

    Règles vérifiées par les services (pas par la base) : la somme des paiements égale
    `total_paid`, et la dépense a au moins une ligne.
    """

    date = models.DateField("date", default=timezone.localdate)
    supplier = models.ForeignKey(
        "core.Supplier",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name="fournisseur",
    )
    order_number = models.CharField("n° de commande", max_length=60, blank=True)
    total_paid = models.DecimalField("montant payé", **MONEY, help_text="Frais de port compris.")
    is_personal = models.BooleanField(
        "achat personnel",
        default=False,
        help_text="Gardé pour mémoire, n'entre dans aucun calcul.",
    )
    notes = models.TextField("notes", blank=True)

    history = partner_history()

    class Meta:
        verbose_name = "dépense"
        verbose_name_plural = "dépenses"
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_paid__gt=0), name="expense_total_paid_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"Dépense du {self.date:%d/%m/%Y} ({self.total_paid} €)"

    def get_absolute_url(self) -> str:
        return reverse("expenses:detail", args=[self.pk])


class ExpensePayment(SoftDeleteModel):
    """Part d'une dépense payée par un associé.

    Un payeur retiré d'une dépense modifiée part à la corbeille (D40) : il n'est plus compté
    nulle part, mais reste restaurable.
    """

    expense = models.ForeignKey(
        Expense, on_delete=models.PROTECT, related_name="payments", verbose_name="dépense"
    )
    partner = models.ForeignKey(
        "core.Partner", on_delete=models.PROTECT, related_name="payments", verbose_name="associé"
    )
    amount = models.DecimalField("montant", **MONEY)

    history = partner_history()

    class Meta:
        verbose_name = "paiement d'un associé"
        verbose_name_plural = "paiements des associés"
        ordering = ["partner_id"]
        constraints = [
            # Les paiements à la corbeille ne comptent pas : un associé retiré puis remis
            # comme payeur reçoit un nouveau paiement.
            models.UniqueConstraint(
                fields=["expense", "partner"],
                condition=Q(deleted_at__isnull=True),
                name="expense_payment_one_per_partner_not_deleted",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_payment_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.partner} : {self.amount} €"


class ExpenseLine(SoftDeleteModel):
    """Une ligne de dépense : une pièce, une prestation ou l'achat d'un véhicule.

    Sans véhicule : dépense générale du garage. Sans dépense : pièce « À commander »,
    qui n'entre dans aucun calcul.
    """

    class Status(models.TextChoices):
        TO_ORDER = "TO_ORDER", "À commander"
        ORDERED = "ORDERED", "Commandé"
        RECEIVED = "RECEIVED", "Reçu"
        FITTED = "FITTED", "Monté"
        RETURNED = "RETURNED", "Retourné/Remboursé"

    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lines",
        verbose_name="dépense",
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_lines",
        verbose_name="véhicule",
        help_text="Vide : dépense générale du garage.",
    )
    category = models.ForeignKey(
        "core.ExpenseCategory", on_delete=models.PROTECT, verbose_name="catégorie"
    )
    label = models.CharField("désignation", max_length=200)
    reference = models.CharField("référence", max_length=80, blank=True)
    quantity = models.DecimalField("quantité", **QUANTITY, default=1)
    unit_price = models.DecimalField("prix unitaire", **MONEY)
    status = models.CharField(
        "statut de la pièce", max_length=20, choices=Status.choices, blank=True
    )
    show_to_buyer = models.BooleanField("à montrer à l'acheteur", default=False)

    history = partner_history()

    class Meta:
        verbose_name = "ligne de dépense"
        verbose_name_plural = "lignes de dépense"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="expense_line_quantity_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="expense_line_unit_price_not_negative"
            ),
            # Seule une pièce « À commander » peut exister sans paiement.
            models.CheckConstraint(
                condition=Q(expense__isnull=False) | Q(status="TO_ORDER"),
                name="expense_line_without_expense_is_to_order",
            ),
        ]

    def __str__(self) -> str:
        return self.label


class Refund(SoftDeleteModel):
    """Remboursement d'une ligne retournée. Le plafond (coût de la ligne) est vérifié par
    les services."""

    line = models.ForeignKey(
        ExpenseLine, on_delete=models.PROTECT, related_name="refunds", verbose_name="ligne"
    )
    date = models.DateField("date", default=timezone.localdate)
    amount = models.DecimalField("montant remboursé", **MONEY)
    refunded_to = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name="remboursé à",
    )
    notes = models.TextField("notes", blank=True)

    history = partner_history()

    class Meta:
        verbose_name = "remboursement"
        verbose_name_plural = "remboursements"
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="refund_amount_not_negative"),
        ]

    def __str__(self) -> str:
        return f"Remboursement de {self.amount} €"


class PartTransfer(TimeStampedModel):
    """Trace du transfert d'une pièce d'un véhicule à un autre (le payeur ne change pas)."""

    line = models.ForeignKey(
        ExpenseLine, on_delete=models.PROTECT, related_name="transfers", verbose_name="ligne"
    )
    from_vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        related_name="parts_given",
        verbose_name="depuis le véhicule",
    )
    to_vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        related_name="parts_received",
        verbose_name="vers le véhicule",
    )
    date = models.DateField("date", default=timezone.localdate)
    notes = models.TextField("notes", blank=True)

    history = partner_history()

    class Meta:
        verbose_name = "transfert de pièce"
        verbose_name_plural = "transferts de pièce"
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(from_vehicle=F("to_vehicle")),
                name="part_transfer_between_two_vehicles",
            ),
        ]

    def __str__(self) -> str:
        return f"Transfert de « {self.line} »"

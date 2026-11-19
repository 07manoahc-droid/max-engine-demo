"""Modèle des règlements entre associés."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.history import partner_history
from core.models import TimeStampedModel

MONEY = {"max_digits": 10, "decimal_places": 2}


MESSAGE_MODIFICATION = "Un règlement ne se modifie jamais : annule-le par un règlement inverse."
MESSAGE_SUPPRESSION = "Un règlement ne se supprime jamais : annule-le par un règlement inverse."


class SettlementQuerySet(models.QuerySet):
    """Refuse aussi les modifications et suppressions groupées (`update()`, `delete()`)."""

    def update(self, **kwargs):
        raise ValidationError(MESSAGE_MODIFICATION)

    def delete(self):
        raise ValidationError(MESSAGE_SUPPRESSION)


class Settlement(TimeStampedModel):
    """Argent versé d'un associé à l'autre pour équilibrer les comptes.

    Jamais modifié ni supprimé (règle 7) : une erreur s'annule par un règlement inverse
    (`cancels`). Un règlement ne s'annule qu'une fois, et une annulation ne s'annule pas.
    """

    date = models.DateField("date", default=timezone.localdate)
    from_partner = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        related_name="settlements_paid",
        verbose_name="versé par",
    )
    to_partner = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        related_name="settlements_received",
        verbose_name="versé à",
    )
    amount = models.DecimalField("montant", **MONEY)
    notes = models.TextField("notes", blank=True)
    cancels = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_by",
        verbose_name="annule le règlement",
    )

    objects = SettlementQuerySet.as_manager()

    history = partner_history()

    class Meta:
        verbose_name = "règlement"
        verbose_name_plural = "règlements"
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="settlement_positive"),
            models.CheckConstraint(
                condition=~Q(from_partner=F("to_partner")),
                name="settlement_between_two_partners",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.from_partner} → {self.to_partner} : {self.amount} €"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(MESSAGE_MODIFICATION)
        if self.cancels_id is not None:
            self._check_cancellation()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(MESSAGE_SUPPRESSION)

    def _check_cancellation(self) -> None:
        """Une annulation est l'exact inverse d'un règlement qui n'est pas lui-même
        une annulation."""
        annule = self.cancels
        if annule.cancels_id is not None:
            raise ValidationError("Une annulation ne peut pas être annulée à son tour.")
        if (
            self.from_partner_id != annule.to_partner_id
            or self.to_partner_id != annule.from_partner_id
            or self.amount != annule.amount
        ):
            raise ValidationError("Une annulation reprend le même montant, dans l'autre sens.")

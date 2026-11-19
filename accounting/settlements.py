"""Enregistrement et annulation des règlements entre associés (D53, D54).

Un règlement n'est jamais modifié ni supprimé (règle 7) : une erreur s'annule par un
règlement inverse. Les montants ne sont pas calculés ici : le solde et les refus viennent
de `accounting/services.py`. `accounting/services.py` reste ainsi en fonctions pures (D56).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Partner

from . import queries, services
from .models import Settlement

ACCOUNTS_UNAVAILABLE = (
    "Les comptes ne peuvent pas être calculés pour l'instant, donc pas réglés. Préviens Mathis."
)
FUTURE_DATE = (
    "Un règlement ne peut pas être daté après aujourd'hui : on l'enregistre une fois l'argent "
    "versé. Indique le jour du versement, aujourd'hui ou avant."
)


def date_problem(date: dt.date) -> str | None:
    """Pourquoi cette date de règlement n'est pas possible, ou None (D53).

    Une date passée est permise : un règlement fait la veille se saisit le lendemain.
    """
    if date > timezone.localdate():
        return FUTURE_DATE
    return None


@dataclass(frozen=True)
class RecordedSettlement:
    """Le règlement enregistré et le solde qu'il laisse, calculé sur les comptes relus dans
    la transaction : l'écran de confirmation n'a pas à relire toute la base."""

    settlement: Settlement
    balance_after: services.Balance
    partner_names: dict[int, str]


@transaction.atomic
def record_settlement(
    *,
    from_partner: Partner,
    to_partner: Partner,
    amount: Decimal,
    date: dt.date,
    notes: str = "",
    seen_due: Decimal | None = None,
) -> RecordedSettlement:
    """Enregistre un règlement de `from_partner` à `to_partner`.

    `seen_due` : le montant dû affiché à l'ouverture du formulaire. Le solde est relu dans
    la transaction (D48) : si un autre règlement est passé entre-temps (second onglet,
    même pour un règlement partiel), le montant dû a changé et l'envoi est refusé.
    """
    probleme_date = date_problem(date)
    if probleme_date:
        raise ValidationError({"date": probleme_date})
    comptes = queries.partner_accounts()
    if comptes is None:
        raise ValidationError(ACCOUNTS_UNAVAILABLE)
    reglement = services.SettlementInput(from_partner.pk, to_partner.pk, amount)
    probleme = services.settlement_problem(
        reglement, comptes.balance, comptes.partner_names, seen_due
    )
    if probleme:
        raise ValidationError(probleme)
    enregistre = Settlement.objects.create(
        from_partner=from_partner,
        to_partner=to_partner,
        amount=amount,
        date=date,
        notes=notes.strip(),
    )
    return RecordedSettlement(
        settlement=enregistre,
        balance_after=services.balance_after(comptes.positions, reglement),
        partner_names=comptes.partner_names,
    )


def cancel_refusal(settlement: Settlement) -> str | None:
    """Pourquoi ce règlement ne peut pas être annulé, ou None s'il le peut (D54)."""
    if settlement.cancels_id is not None:
        return (
            "Ce règlement est déjà une annulation : il ne s'annule pas. "
            "S'il le faut, enregistre un nouveau règlement."
        )
    if Settlement.objects.filter(cancels=settlement).exists():
        return "Ce règlement est déjà annulé."
    if not (settlement.from_partner.is_active and settlement.to_partner.is_active):
        return "Ce règlement concerne un ancien associé. Préviens Mathis."
    return None


@transaction.atomic
def cancel_settlement(settlement: Settlement, *, reason: str) -> Settlement:
    """Annule un règlement par un règlement inverse, daté du jour, avec le motif en notes.

    Les deux règlements restent visibles. Le règlement est relu dans la transaction : un
    double-clic n'annule qu'une fois.
    """
    motif = reason.strip()
    if not motif:
        raise ValidationError({"reason": "Indique pourquoi tu annules ce règlement."})
    reglement = Settlement.objects.select_related("from_partner", "to_partner").get(
        pk=settlement.pk
    )
    refus = cancel_refusal(reglement)
    if refus:
        raise ValidationError(refus)
    return Settlement.objects.create(
        from_partner=reglement.to_partner,
        to_partner=reglement.from_partner,
        amount=reglement.amount,
        date=timezone.localdate(),
        notes=motif,
        cancels=reglement,
    )

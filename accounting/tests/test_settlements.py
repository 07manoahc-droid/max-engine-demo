"""Tests de l'enregistrement et de l'annulation des règlements (D53, D54), en base."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounting import queries, services, settlements
from accounting.models import Settlement
from core.models import Partner

pytestmark = pytest.mark.finance


def solde() -> services.Balance:
    return queries.partner_accounts().balance


def regler(payeur, receveur, montant) -> Settlement:
    return settlements.record_settlement(
        from_partner=payeur,
        to_partner=receveur,
        amount=D(montant),
        date=timezone.localdate(),
        notes="  virement  ",
    ).settlement


def test_partial_settlement_lowers_what_is_owed(axel_owes, mathis, axel):
    reglement = regler(axel, mathis, "100.00")

    assert reglement.notes == "virement"
    assert solde() == services.Balance(axel.pk, mathis.pk, D("105.44"))


def test_settling_what_is_owed_settles(axel_owes, mathis, axel):
    regler(axel, mathis, "205.44")
    assert solde().is_settled


def test_settlement_above_what_is_owed_is_refused(axel_owes, mathis, axel):
    with pytest.raises(ValidationError, match="ne peut pas dépasser"):
        regler(axel, mathis, "205.45")
    assert not Settlement.objects.exists()


def test_settlement_in_the_wrong_direction_is_refused(axel_owes, mathis, axel):
    with pytest.raises(ValidationError, match="Le solde a changé"):
        regler(mathis, axel, "10")


def test_second_identical_settlement_is_refused(axel_owes, mathis, axel):
    """Un second envoi (deux onglets, double-clic) voit le solde déjà réglé."""
    regler(axel, mathis, "205.44")
    with pytest.raises(ValidationError, match="équilibre"):
        regler(axel, mathis, "205.44")
    assert Settlement.objects.count() == 1


def test_second_partial_settlement_from_another_tab_is_refused(axel_owes, mathis, axel):
    """Les deux onglets ont vu 205,44 € dus : le second règlement de 100 € est refusé."""
    vu = D("205.44")
    settlements.record_settlement(
        from_partner=axel,
        to_partner=mathis,
        amount=D("100"),
        date=timezone.localdate(),
        seen_due=vu,
    )
    with pytest.raises(ValidationError, match="Le solde a changé depuis l'ouverture"):
        settlements.record_settlement(
            from_partner=axel,
            to_partner=mathis,
            amount=D("100"),
            date=timezone.localdate(),
            seen_due=vu,
        )
    assert Settlement.objects.count() == 1


def test_settlement_refused_without_two_active_partners(axel_owes, mathis, axel):
    Partner.objects.filter(pk=axel.pk).update(is_active=False)
    with pytest.raises(ValidationError, match="Préviens Mathis"):
        regler(axel, mathis, "10")


def test_cancellation_restores_the_balance(axel_owes, mathis, axel):
    reglement = regler(axel, mathis, "205.44")

    annulation = settlements.cancel_settlement(reglement, reason="  saisi en double  ")

    assert annulation.cancels == reglement
    assert (annulation.from_partner, annulation.to_partner) == (mathis, axel)
    assert annulation.amount == D("205.44")
    assert annulation.date == timezone.localdate()
    assert annulation.notes == "saisi en double"
    assert solde() == services.Balance(axel.pk, mathis.pk, D("205.44"))
    assert Settlement.objects.count() == 2


def test_cancellation_needs_a_reason(axel_owes, mathis, axel):
    reglement = regler(axel, mathis, "100")
    with pytest.raises(ValidationError) as erreur:
        settlements.cancel_settlement(reglement, reason="   ")
    assert erreur.value.message_dict == {"reason": ["Indique pourquoi tu annules ce règlement."]}


def test_settlement_is_cancelled_only_once(axel_owes, mathis, axel):
    reglement = regler(axel, mathis, "100")
    settlements.cancel_settlement(reglement, reason="erreur")

    with pytest.raises(ValidationError, match="déjà annulé"):
        settlements.cancel_settlement(reglement, reason="encore")
    assert Settlement.objects.count() == 2


def test_a_cancellation_cannot_be_cancelled(axel_owes, mathis, axel):
    annulation = settlements.cancel_settlement(regler(axel, mathis, "100"), reason="erreur")

    assert settlements.cancel_refusal(annulation).startswith("Ce règlement est déjà une annulation")
    with pytest.raises(ValidationError, match="déjà une annulation"):
        settlements.cancel_settlement(annulation, reason="erreur")


def test_cancellation_is_not_limited_by_what_is_owed(axel_owes, mathis, axel):
    """Après un règlement complet, on est à l'équilibre : l'annulation reste possible."""
    reglement = regler(axel, mathis, "205.44")
    settlements.cancel_settlement(reglement, reason="virement refusé")
    assert solde() == services.Balance(axel.pk, mathis.pk, D("205.44"))


def test_settlement_dated_in_the_future_is_refused(axel_owes, mathis, axel):
    demain = timezone.localdate() + dt.timedelta(days=1)
    with pytest.raises(ValidationError) as erreur:
        settlements.record_settlement(
            from_partner=axel, to_partner=mathis, amount=D("100"), date=demain
        )
    assert erreur.value.message_dict == {"date": [settlements.FUTURE_DATE]}
    assert not Settlement.objects.exists()


def test_settlement_dated_in_the_past_is_accepted(axel_owes, mathis, axel):
    hier = timezone.localdate() - dt.timedelta(days=1)
    enregistre = settlements.record_settlement(
        from_partner=axel, to_partner=mathis, amount=D("100"), date=hier
    )
    assert enregistre.settlement.date == hier


def test_recorded_settlement_gives_the_balance_it_leaves(axel_owes, mathis, axel):
    """Le solde laissé par le règlement est celui des comptes relus dans la transaction :
    l'écran de confirmation n'a pas à relire la base, et il dit la même chose qu'elle."""
    enregistre = settlements.record_settlement(
        from_partner=axel, to_partner=mathis, amount=D("100"), date=timezone.localdate()
    )
    assert enregistre.balance_after == solde()
    assert enregistre.partner_names[axel.pk] == "Axel"

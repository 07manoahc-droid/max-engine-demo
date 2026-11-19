"""Logique métier commune : l'associé courant (« qui suis-je »)."""

from django.http import HttpRequest

from .models import Partner

SESSION_KEY = "partner_id"


def get_current_partner(request: HttpRequest) -> Partner | None:
    """Retourne l'associé choisi au lancement, ou None s'il n'a pas encore choisi."""
    partner_id = request.session.get(SESSION_KEY)
    if partner_id is None:
        return None
    return Partner.objects.filter(pk=partner_id, is_active=True).first()


def set_current_partner(request: HttpRequest, partner: Partner) -> None:
    """Mémorise l'associé choisi dans la session (conservée un an, voir D28)."""
    request.session[SESSION_KEY] = partner.pk

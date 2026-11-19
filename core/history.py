"""Historique des modifications (django-simple-history) avec l'associé comme auteur (D30).

Le logiciel n'a pas de comptes utilisateurs : l'auteur d'une modification est l'associé
choisi au lancement (`request.partner`, posé par `CurrentPartnerMiddleware`). Le middleware
`HistoryRequestMiddleware` de django-simple-history rend la requête visible ici.
"""

from simple_history.models import HistoricalRecords


def current_partner_for_history(request=None, **kwargs):
    """Associé de la requête en cours, ou None (commande, migration, test sans requête).

    django-simple-history ne transmet la requête que si un utilisateur Django est connecté :
    on la relit donc directement dans son contexte.
    """
    request = getattr(HistoricalRecords.context, "request", None)
    return getattr(request, "partner", None)


def partner_history(**kwargs) -> HistoricalRecords:
    """Historique dont l'auteur (`history_user`) est un associé."""
    return HistoricalRecords(
        user_model="core.Partner", get_user=current_partner_for_history, **kwargs
    )

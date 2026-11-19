"""Variables disponibles dans tous les gabarits."""

from django.conf import settings

from core.backup import current_sidebar_status, read_only_banner
from expenses.services import parts_to_order
from vehicles.services import garage_vehicles

# Le nom viendra des paramètres du garage (GarageSettings) à partir de la tâche 1.2.
DEFAULT_GARAGE_NAME = "MAx Engine"


def garage(request):
    return {
        "garage_name": DEFAULT_GARAGE_NAME,
        "current_partner": getattr(request, "partner", None),
        # Barre latérale : lus seulement si le gabarit les affiche (requête paresseuse).
        "garage_vehicles": garage_vehicles(),
        # Compteur « À commander » : `.count` n'est appelé que si le gabarit l'affiche.
        "sidebar_parts_to_order": parts_to_order(),
        "read_only": settings.READ_ONLY,
        "read_only_banner": (
            read_only_banner(settings.READ_ONLY_SOURCE) if settings.READ_ONLY else ""
        ),
        # « Dernière sauvegarde » (D73) : une fonction, lue seulement si le gabarit l'affiche.
        "last_backup": current_sidebar_status,
    }

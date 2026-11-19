"""Couleurs et libellés de l'interface : seule source de vérité (skill `charte-graphique`).

Les classes sont écrites en toutes lettres pour que Tailwind les repère dans ce fichier ;
les couleurs elles-mêmes (clair et sombre) sont définies dans `styles/input.css`.
Ne jamais dupliquer cette table ailleurs.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    """Apparence d'un statut ou d'un associé."""

    label: str
    badge: str  # classes du badge : fond et texte
    dot: str  # classe de la pastille (couleur du texte du statut)


# Clés = valeurs de `Vehicle.Status` (CLAUDE.md). Un test de la tâche 1.2 vérifiera l'accord.
STATUS_STYLES: dict[str, Style] = {
    "WAITING_PURCHASE": Style(
        "En attente d'achat", "bg-status-gray-bg text-status-gray-fg", "bg-status-gray-fg"
    ),
    "PURCHASED": Style("Acheté", "bg-status-sky-bg text-status-sky-fg", "bg-status-sky-fg"),
    "IN_REPAIR": Style(
        "En réparation", "bg-status-amber-bg text-status-amber-fg", "bg-status-amber-fg"
    ),
    "READY_FOR_SALE": Style(
        "Prêt à vendre", "bg-status-violet-bg text-status-violet-fg", "bg-status-violet-fg"
    ),
    "FOR_SALE": Style("En vente", "bg-status-green-bg text-status-green-fg", "bg-status-green-fg"),
    "RESERVED": Style("Réservé", "bg-status-pink-bg text-status-pink-fg", "bg-status-pink-fg"),
    "SOLD": Style("Vendu", "bg-status-slate-bg text-status-slate-fg", "bg-status-slate-fg"),
    "ABANDONED": Style("Abandonné", "bg-status-red-bg text-status-red-fg", "bg-status-red-fg"),
}

# Apparence neutre quand une clé est inconnue (ex. couleur saisie à la main dans l'admin) :
# l'écran reste affiché au lieu de planter.
NEUTRAL_STYLE = STATUS_STYLES["WAITING_PURCHASE"]

# Clés = `Partner.color`.
PARTNER_STYLES: dict[str, Style] = {
    "indigo": Style(
        "Indigo", "bg-partner-indigo-bg text-partner-indigo-fg", "bg-partner-indigo-fg"
    ),
    "teal": Style("Turquoise", "bg-partner-teal-bg text-partner-teal-fg", "bg-partner-teal-fg"),
}

# Statuts d'une pièce (clés = `ExpenseLine.Status`) : tout en gris, sauf « À commander » en
# ambre, la seule qui demande une action. Le libellé est toujours écrit dans le badge.
PART_STATUS_STYLES: dict[str, Style] = {
    "TO_ORDER": Style(
        "À commander", "bg-status-amber-bg text-status-amber-fg", "bg-status-amber-fg"
    ),
    "ORDERED": Style("Commandé", "bg-status-gray-bg text-status-gray-fg", "bg-status-gray-fg"),
    "RECEIVED": Style("Reçu", "bg-status-gray-bg text-status-gray-fg", "bg-status-gray-fg"),
    "FITTED": Style("Monté", "bg-status-gray-bg text-status-gray-fg", "bg-status-gray-fg"),
    "RETURNED": Style(
        "Retourné/Remboursé", "bg-status-gray-bg text-status-gray-fg", "bg-status-gray-fg"
    ),
}

# Alerte qui n'est pas un statut (« Dernière sauvegarde » ancienne ou échouée, D73) : la même
# ambre que « À commander », qui demande aussi une action.
WARNING_BADGE = "bg-status-amber-bg text-status-amber-fg"
WARNING_TEXT = "text-status-amber-fg"

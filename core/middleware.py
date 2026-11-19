"""Middleware : consultation en lecture seule (D79), et choix de l'associé avant d'utiliser
le logiciel."""

from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve, reverse
from django_htmx.http import HttpResponseClientRedirect

from .services import get_current_partner

# Les seules pages ouvertes en consultation : elles ne font que lire. Toute autre page
# (formulaire, aperçu, administration, « Qui es-tu ? ») est refusée, même une page ajoutée
# plus tard : la liste est à compléter à la main, jamais l'inverse.
READ_ONLY_PAGES = {
    "core:home",
    "core:health",
    "core:trash",
    "core:journal",
    "core:read_only",
    "core:contacts",
    "core:contact_detail",
    "core:photo_thumbnail",
    "vehicles:list",
    "vehicles:sold",
    "vehicles:abandoned",
    "vehicles:detail",
    "expenses:detail",
    "expenses:parts_to_order",
    "accounting:accounts",
    "accounting:garage_expenses",
    "accounting:report",
    "accounting:report_export",  # D65 : l'export ne fait que lire
}
READ_METHODS = {"GET", "HEAD"}


class ReadOnlyMiddleware:
    """En consultation (`READ_ONLY=1`), refuse tout envoi (HTMX compris) et toute page qui
    n'est pas dans `READ_ONLY_PAGES`, avec une page qui explique pourquoi. La base est de
    toute façon ouverte en lecture seule par SQLite : ceci évite d'aller jusqu'à l'erreur."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.file_prefixes = tuple(
            "/" + prefixe.lstrip("/") for prefixe in (settings.STATIC_URL, settings.MEDIA_URL)
        )

    def __call__(self, request):
        if not settings.READ_ONLY or request.path.startswith(self.file_prefixes):
            return self.get_response(request)
        try:
            page = resolve(request.path_info).view_name
        except Resolver404:
            return self.get_response(request)  # page introuvable, en français (D69)
        if request.method in READ_METHODS and page in READ_ONLY_PAGES:
            return self.get_response(request)
        if request.htmx:
            return HttpResponseClientRedirect(reverse("core:read_only"))
        return render(request, "core/read_only.html", status=403)


class CurrentPartnerMiddleware:
    """Pose `request.partner` et renvoie vers « Qui es-tu ? » tant qu'aucun associé n'est choisi."""

    def __init__(self, get_response):
        self.get_response = get_response
        # Fichiers statiques et joints (D85), et « /sante/ » du lanceur : ni session ni
        # requête en base, et pas de « Qui es-tu ? » pour une image.
        self.free_prefixes = (
            "/" + settings.STATIC_URL.lstrip("/"),
            "/" + settings.MEDIA_URL.lstrip("/"),
            "/sante/",
        )
        self.admin_prefix = "/admin/"

    def __call__(self, request):
        path = request.path
        if path.startswith(self.free_prefixes):
            request.partner = None
            return self.get_response(request)

        if settings.READ_ONLY:
            # Consultation : personne ne modifie, il n'y a pas d'associé à choisir (D79).
            request.partner = None
            return self.get_response(request)

        request.partner = get_current_partner(request)
        if request.partner is None and not self._is_exempt(path):
            adresse = reverse("core:choose_partner")
            if request.htmx:
                return HttpResponseClientRedirect(adresse)
            if request.method == "GET":
                adresse += "?" + urlencode({"next": request.get_full_path()})
            return redirect(adresse)
        return self.get_response(request)

    def _is_exempt(self, path: str) -> bool:
        """La page de choix elle-même et l'administration de secours."""
        return path == reverse("core:choose_partner") or path.startswith(self.admin_prefix)

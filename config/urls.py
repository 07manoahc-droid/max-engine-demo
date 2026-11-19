"""Adresses de MAx Engine."""

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.views import serve as serve_static
from django.urls import include, path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", include("vehicles.urls")),
    path("", include("expenses.urls")),
    path("", include("sales.urls")),
    path("", include("accounting.urls")),
]

# Page introuvable en français ; réponse courte pour un fichier statique ou joint (D69).
handler404 = "core.views.page_not_found"
# Trop de photos d'un coup : un message clair, pas une erreur 400 brute (D92).
handler400 = "core.views.bad_request"


def serve_media(request, path):
    """Un fichier joint (photo, facture), lu dans `MEDIA_ROOT` au moment de la demande :
    `MEDIA_PATH` peut le changer (répétition, consultation)."""
    return serve(request, path, document_root=settings.MEDIA_ROOT)


# Fichiers statiques et joints servis par Django lui-même, même sans DEBUG (D85) : un seul
# poste, sur 127.0.0.1, sans dépendance de plus. Les fichiers statiques sont lus là où ils
# sont (`static/`, et ceux des bibliothèques), sans `collectstatic` à la mise à jour.
# `serve` refuse tout chemin qui sortirait de son dossier.
# `STATIC_URL` vaut « /static/ » une fois lu (Django ajoute la barre) : on la retire.
urlpatterns += [
    path(f"{settings.STATIC_URL.lstrip('/')}<path:path>", serve_static, {"insecure": True}),
    path(f"{settings.MEDIA_URL.lstrip('/')}<path:path>", serve_media),
]

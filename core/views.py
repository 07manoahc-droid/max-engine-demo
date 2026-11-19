"""Vues communes : accueil, choix de l'associé, corbeille, journal, contacts, miniatures
des photos et pages d'erreur."""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import SuspiciousOperation, TooManyFilesSent, ValidationError
from django.http import Http404, HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views import defaults
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers
from django_htmx.http import HttpResponseClientRedirect

from accounting import queries
from expenses.models import Expense
from sales.services import sold_sales
from vehicles.models import Vehicle
from vehicles.services import garage_vehicles

from . import contacts as contact_services
from . import journal as journal_module
from . import thumbnails, trash
from .formatting import format_euros, parse_id
from .forms import ContactEditForm
from .models import Attachment, Contact, Partner
from .services import set_current_partner
from .version import VERSION


@require_GET
def health(request):
    """« MAx Engine 1.0.0 » : le lanceur et « Mettre à jour » savent ainsi que c'est bien
    MAx Engine qui répond sur le port, et non un autre logiciel (D84, D86). Ne lit pas la
    base."""
    return HttpResponse(f"MAx Engine {VERSION}", content_type="text/plain; charset=utf-8")


@require_GET
def home(request):
    """Accueil : bandeau d'indicateurs et trois gros boutons."""
    annee = timezone.localdate().year
    apercu = queries.garage_overview(annee)
    return render(
        request,
        "core/home.html",
        {
            # Même chiffre que le bilan, fiches à la corbeille comprises (D66).
            "overview": apercu,
            "trash_note": _trash_note(apercu.in_trash),
            # Le vrai bénéfice de l'année : marges − pertes − dépenses du garage (D63).
            "profit_label": f"Bénéfice {annee}",
            "garage_count": garage_vehicles().count(),
            "sold_count": sold_sales().count(),
        },
    )


def page_not_found(request, exception):
    """Page introuvable (D69). Un fichier statique ou joint introuvable reçoit une réponse
    courte : la page complète lirait la base (barre latérale) pour une simple image."""
    fichiers = ("/" + settings.STATIC_URL.lstrip("/"), "/" + settings.MEDIA_URL.lstrip("/"))
    if request.path.startswith(fichiers):
        return HttpResponseNotFound("Fichier introuvable.", content_type="text/plain")
    return defaults.page_not_found(request, exception)


logger = logging.getLogger(__name__)

# Au-delà, Django refuse l'envoi entier (`DATA_UPLOAD_MAX_NUMBER_FILES`, D92). « Fichiers » :
# le refus vaut aussi pour les factures d'une dépense.
TOO_MANY_FILES = (
    "Tu as envoyé plus de {nombre} fichiers d'un coup : rien n'est enregistré. Envoie-les "
    "en plusieurs fois, {nombre} au plus à chaque envoi."
)


def bad_request(request, exception):
    """Requête refusée (400). Trop de photos d'un coup : un message clair et retour à la
    page d'où l'on vient, au lieu d'une erreur brute (D92). Le refus arrive avant la vue,
    dès la lecture de l'envoi."""
    if not isinstance(exception, TooManyFilesSent):
        return defaults.bad_request(request, exception)
    messages.error(request, TOO_MANY_FILES.format(nombre=settings.DATA_UPLOAD_MAX_NUMBER_FILES))
    origine = request.META.get("HTTP_REFERER", "")
    if not url_has_allowed_host_and_scheme(origine, allowed_hosts={request.get_host()}):
        origine = reverse("core:home")
    if request.headers.get("HX-Request"):
        return HttpResponseClientRedirect(origine)
    return redirect(origine)


@require_GET
def photo_thumbnail(request, pk: int):
    """La miniature d'une photo (D92), faite si elle manque. Une photo que Pillow ne sait
    pas réduire est montrée telle quelle ; une photo dont le fichier manque est introuvable."""
    photo = get_object_or_404(Attachment.all_objects, pk=pk, kind=Attachment.Kind.PHOTO)
    try:
        donnees = thumbnails.thumbnail_bytes(photo)
    except FileNotFoundError:
        return HttpResponseNotFound("Fichier introuvable.", content_type="text/plain")
    except SuspiciousOperation:
        raise  # chemin hors de MEDIA_ROOT : refus de Django (400)
    except Exception:
        # La miniature est facultative : quoi qu'il arrive à Pillow, la photo d'origine.
        logger.warning("Pas de miniature pour la photo n°%s.", photo.pk, exc_info=True)
        return redirect(photo.file.url)
    reponse = HttpResponse(donnees, content_type="image/jpeg")
    # Une photo ne change jamais (D71) : sa miniature non plus. L'adresse porte le nom du
    # fichier (`thumbnail_url`) : après une restauration, un même numéro de photo désignant
    # une autre photo n'est pas confondu dans le cache du navigateur.
    reponse["Cache-Control"] = "private, max-age=604800"
    return reponse


def _trash_note(in_trash) -> str:
    """« dont 200,00 € sur des fiches à la corbeille », ou rien (D66)."""
    if not in_trash:
        return ""
    return f"dont {format_euros(in_trash)} sur des fiches à la corbeille"


def _safe_next(request) -> str:
    """Adresse où revenir après le choix, ou l'accueil si elle n'est pas sûre."""
    adresse = request.POST.get("next") or request.GET.get("next") or ""
    if adresse and url_has_allowed_host_and_scheme(adresse, allowed_hosts={request.get_host()}):
        return adresse
    return reverse("core:home")


@require_http_methods(["GET", "POST"])
def choose_partner(request):
    """« Qui es-tu ? » : deux gros boutons, un par associé."""
    partners = Partner.objects.filter(is_active=True)
    error = ""
    if request.method == "POST":
        choix = request.POST.get("partner", "")
        partner = partners.filter(pk=choix).first() if choix.isdigit() else None
        if partner is not None:
            set_current_partner(request, partner)
            return redirect(_safe_next(request))
        error = "Choisis ton prénom pour continuer."
    if not partners:
        error = "Aucun associé n'est actif. Demande à Mathis de corriger ça."
    return render(
        request,
        "core/choose_partner.html",
        {"partners": partners, "next": _safe_next(request), "error": error},
    )


@require_GET
def trash_list(request):
    """La corbeille : ce qui a été mis de côté, par groupe, avec « Restaurer » ou la raison
    pour laquelle ce n'est pas possible (D74, D75)."""
    return render(request, "core/trash.html", {"groups": trash.trash_groups()})


@require_POST
def trash_restore(request, kind: str, pk: int):
    """« Restaurer » : remet l'élément à sa place, ou dit pourquoi ce n'est pas possible."""
    try:
        message = trash.restore(kind, pk)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, message)
    return redirect("core:trash")


def _journal_filter(request, nom: str, manager, champ: str):
    """L'objet demandé dans l'adresse (« ?vehicule=2 »), corbeille comprise ; None si le
    filtre est vide. Un numéro qui n'existe pas donne la page introuvable."""
    texte = request.GET.get(nom, "")
    if not texte:
        return None
    numero = parse_id(texte)
    if numero is None:
        raise Http404
    return get_object_or_404(manager, **{champ: numero})


@require_GET
def journal(request):
    """Le journal des modifications (D78), filtré par véhicule ou par dépense."""
    vehicule = _journal_filter(request, "vehicule", Vehicle.all_objects, "number")
    depense = _journal_filter(request, "depense", Expense.all_objects, "pk")
    if depense is not None:
        vehicule = None
    page = journal_module.journal_page(
        parse_id(request.GET.get("page", "")) or 1, vehicle=vehicule, expense=depense
    )

    def adresse(numero: int) -> str:
        parametres = {"page": numero}
        if vehicule is not None:
            parametres["vehicule"] = vehicule.number
        if depense is not None:
            parametres["depense"] = depense.pk
        return f"{reverse('core:journal')}?{urlencode(parametres)}"

    return render(
        request,
        "core/journal.html",
        {
            "journal": page,
            "vehicles": Vehicle.all_objects.order_by("number"),
            "vehicle": vehicule,
            "expense": depense,
            "previous_url": adresse(page.page - 1) if page.has_previous else "",
            "next_url": adresse(page.page + 1) if page.has_next else "",
        },
    )


@require_GET
def read_only_refused(request):
    """Ce qu'on voit en consultation après un clic qui voulait modifier (D79). Hors
    consultation, la page n'a pas de sens : retour à l'accueil."""
    if not settings.READ_ONLY:
        return redirect("core:home")
    return render(request, "core/read_only.html", status=403)


# ---------------------------------------------------------------------------
# Contacts (D81)
# ---------------------------------------------------------------------------


@require_GET
@vary_on_headers("HX-Request")
def contact_list(request):
    """Les contacts, avec leurs rôles ; la recherche filtre pendant la frappe (HTMX) et ne
    renvoie alors que la liste. Une restauration de l'historique reçoit la page complète."""
    recherche = " ".join(request.GET.get("q", "").split())
    contacts = contact_services.search_contacts(recherche)
    roles = contact_services.contact_roles(contacts)
    gabarit = "core/contact_list.html"
    fragment = bool(request.htmx) and not request.htmx.history_restore_request
    return render(
        request,
        f"{gabarit}#resultats" if fragment else gabarit,
        {"rows": [(c, roles[c.pk]) for c in contacts], "query": recherche},
    )


@require_GET
def contact_detail(request, pk: int):
    contact = get_object_or_404(Contact.objects, pk=pk)
    return render(
        request,
        "core/contact_detail.html",
        {"contact": contact, "roles": contact_services.contact_roles([contact])[contact.pk]},
    )


def _warn_same_name(request, contact: Contact) -> None:
    """Un autre contact porte déjà ce nom : le dire, pour éviter un doublon (D81)."""
    autres = contact_services.same_name_contacts(contact.name, exclude_pk=contact.pk)
    if autres:
        ou = ", ".join(c.city for c in autres if c.city)
        messages.warning(
            request,
            f"Un autre contact s'appelle déjà {contact.name}{f' ({ou})' if ou else ''} : "
            "vérifie que ce n'est pas la même personne.",
        )


@require_http_methods(["GET", "POST"])
def contact_create(request):
    form = ContactEditForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        contact = form.save()
        messages.success(request, f"Le contact {contact.name} est ajouté.")
        _warn_same_name(request, contact)
        return redirect("core:contact_detail", pk=contact.pk)
    return render(request, "core/contact_form.html", {"form": form, "contact": None})


@require_http_methods(["GET", "POST"])
def contact_edit(request, pk: int):
    contact = get_object_or_404(Contact.objects, pk=pk)
    form = ContactEditForm(request.POST or None, instance=contact)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Le contact est enregistré.")
        _warn_same_name(request, contact)
        return redirect("core:contact_detail", pk=contact.pk)
    return render(request, "core/contact_form.html", {"form": form, "contact": contact})


@require_POST
def contact_trash(request, pk: int):
    """Met le contact à la corbeille (D74, D81). Lu corbeille comprise : un second clic
    reçoit « déjà dans la corbeille » plutôt qu'une page introuvable."""
    contact = get_object_or_404(Contact.all_objects, pk=pk)
    try:
        contact_services.trash_contact(contact)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(
            request,
            f"{contact.name} est dans la corbeille, toujours lié à ses véhicules. Tu peux le "
            "restaurer depuis « Corbeille ».",
        )
    return redirect("core:contacts")

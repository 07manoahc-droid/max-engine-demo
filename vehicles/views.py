"""Vues des véhicules : liste au garage, ajout, fiche, modification, statut, achat, photos.

Les vues restent minces : les règles sont dans `vehicles/services.py`, l'argent dans
`accounting/`.
"""

from collections import Counter

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers

from accounting import queries
from accounting.services import ZERO, VehicleCost, balance_label, margin_display
from core.forms import new_contact_form
from core.journal import vehicle_reopenings
from core.models import Attachment, Partner
from core.ui import STATUS_STYLES
from expenses import services as expense_services
from sales import services as sale_services
from sales.models import Sale

from . import services
from .forms import PurchaseForm, ReopenForm, VehicleCreateForm, VehicleForm
from .models import Vehicle


def _get_vehicle(number: int) -> Vehicle:
    """Le véhicule de ce numéro (hors corbeille), ou une page « introuvable »."""
    return get_object_or_404(
        Vehicle.objects.select_related("vehicle_type", "origin", "seller"), number=number
    )


@require_GET
@vary_on_headers("HX-Request")
def vehicle_list(request):
    """« Véhicules au garage » : une carte par véhicule, filtrable par statut.

    Le filtre passe par HTMX : seule la grille (partial `grille`) est renvoyée. Quand htmx
    redemande une page de l'historique (bouton « Retour »), il lui faut la page complète.
    """
    vehicules = list(services.garage_vehicles().select_related("vehicle_type"))
    comptes = Counter(v.status for v in vehicules)
    filtre = request.GET.get("statut", "")
    if filtre not in comptes:
        filtre = ""
    apercu = queries.garage_overview(timezone.localdate().year)
    associes = queries.active_partners()
    couvertures = services.cover_photos(vehicules)
    gabarit = "vehicles/vehicle_list.html"
    return render(
        request,
        f"{gabarit}#grille" if _wants_fragment(request) else gabarit,
        {
            "cards": [
                _card(v, apercu.costs, associes, couvertures.get(v.pk))
                for v in vehicules
                if not filtre or v.status == filtre
            ],
            # Les statuts présents au garage, dans l'ordre du parcours, avec leur nombre.
            "status_counts": [(s, comptes[s]) for s in Vehicle.Status if comptes[s]],
            "total_count": len(vehicules),
            "current_status": filtre,
        },
    )


@require_GET
def sold_list(request):
    """« Véhicules vendus » : une carte par véhicule vendu, la vente la plus récente d'abord."""
    ventes = list(sale_services.sold_sales())
    vehicules = [vente.vehicle for vente in ventes]
    marges = queries.sold_margins([v.pk for v in vehicules])
    # Toute vente non annulée a une marge ; la garde évite une page d'erreur sinon.
    ventes = [vente for vente in ventes if vente.vehicle_id in marges]
    associes = queries.active_partners()
    couvertures = services.cover_photos(vehicules)
    return render(
        request,
        "vehicles/sold_list.html",
        {
            "cards": [
                _sold_card(vente, marges.get(vente.vehicle_id), associes, couvertures)
                for vente in ventes
            ]
        },
    )


@require_GET
def abandoned_list(request):
    """« Véhicules abandonnés » : une carte par véhicule, avec sa perte (D59), en lecture
    seule. L'abandon le plus récent d'abord."""
    liste = queries.abandoned_vehicles()
    couvertures = services.cover_photos([carte.vehicle for carte in liste.cards])
    associes = queries.active_partners()
    return render(
        request,
        "vehicles/abandoned_list.html",
        {
            "cards": [
                {
                    "card": carte,
                    "cover": couvertures.get(carte.vehicle.pk),
                    "shares": [
                        (a, carte.loss.result.shown_shares[a.pk])
                        for a in associes
                        if a.pk in carte.loss.result.shown_shares
                    ],
                }
                for carte in liste.cards
            ],
            "total": liste.total,
        },
    )


def _sold_card(vente, marge, associes, couvertures) -> dict:
    """Ce qu'affiche la carte d'un véhicule vendu : photo, vente, marge et gain de chacun.
    Sans gain si l'argent n'est pas partagé entre deux associés actifs."""
    return {
        "vehicle": vente.vehicle,
        "cover": couvertures.get(vente.vehicle_id),
        "sale": vente,
        "margin": marge,
        "shares": [(a, marge.shown_shares[a.pk]) for a in associes if a.pk in marge.shown_shares],
    }


def _wants_fragment(request) -> bool:
    """Requête HTMX ordinaire (pas une restauration de l'historique)."""
    return bool(request.htmx) and not request.htmx.history_restore_request


def _card(vehicule: Vehicle, couts, associes, couverture: Attachment | None) -> dict:
    """Ce qu'affiche la carte d'un véhicule : photo, investissement total et par associé."""
    cout = couts.get(vehicule.pk, VehicleCost())
    return {
        "vehicle": vehicule,
        "cover": couverture,
        "invested": cout.total,
        "shares": [(a, cout.by_partner.get(a.pk, ZERO)) for a in associes],
    }


@require_http_methods(["GET", "POST"])
def vehicle_create(request):
    """« Ajouter un véhicule » : formulaire court, le véhicule arrive en attente d'achat."""
    form = VehicleCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        vehicule = services.create_vehicle(form.save(commit=False), request.partner)
        messages.success(request, f"{vehicule.display_name} est ajouté au garage.")
        return redirect(vehicule)
    return render(
        request,
        "vehicles/vehicle_form.html",
        {"form": form, "title": "Ajouter un véhicule", "submit_label": "Ajouter le véhicule"},
    )


@require_GET
def vehicle_detail(request, number: int):
    """La fiche d'un véhicule : identité, investissement, achat, historique des statuts."""
    vehicule = _get_vehicle(number)
    depenses = queries.load_expenses()
    cout = queries.vehicle_cost(vehicule.pk, depenses)
    peut_vendre = sale_services.sale_refusal(vehicule) is None
    return render(
        request,
        "vehicles/vehicle_detail.html",
        {
            "vehicle": vehicule,
            "investments": [
                (associe, cout.by_partner.get(associe.pk, ZERO))
                for associe in queries.active_partners()
            ],
            "invested_total": cout.total,
            "purchase": _purchase_summary(vehicule, depenses),
            "expense_rows": expense_services.line_rows(
                expense_services.vehicle_paid_lines(vehicule), depenses
            ),
            "transfers": expense_services.vehicle_transfers(vehicule),
            "vehicle_parts_to_order": expense_services.parts_to_order().filter(vehicle=vehicule),
            # Vendu ou abandonné : on n'y note plus de pièce, le bloc n'a plus de raison d'être.
            "takes_parts_to_order": vehicule.status not in services.NOT_IN_GARAGE,
            "photo_stages": services.photo_stages(vehicule),
            "allowed_statuses": services.allowed_statuses(
                vehicule.status, services.has_purchase(vehicule)
            ),
            "can_take_deposit": sale_services.deposit_refusal(vehicule) is None,
            "active_deposit": sale_services.active_deposit_detail(vehicule),
            "past_deposits": sale_services.past_deposits(vehicule),
            "can_sell": peut_vendre,
            # La vente devient l'action attendue, donc le bouton bleu de la fiche.
            "sale_is_next": peut_vendre and vehicule.status in sale_services.READY_TO_SELL,
            "sale_block": _sale_block(vehicule),
            "cancelled_sales": sale_services.cancelled_sales(vehicule),
            "status_changes": vehicule.status_changes.select_related("changed_by").order_by(
                "-changed_at", "-id"
            ),
            "reopenings": vehicle_reopenings(vehicule),
            "trash_offer": _trash_offer(vehicule),
        },
    )


def _trash_offer(vehicule: Vehicle) -> dict | None:
    """Le bas de la fiche : « Mettre la fiche à la corbeille », ou ce qu'il faut faire avant
    (D74). Rien pour une fiche qui a de l'argent (dépense payée, vente, acompte) ou qui est
    une reprise : la proposer inviterait à effacer de vraies dépenses."""
    a_de_l_argent = (
        vehicule.sales.exists()
        or vehicule.deposits.exists()
        or expense_services.vehicle_paid_lines(vehicule).exists()
        or Sale.objects.filter(trade_in_vehicle=vehicule).exists()
    )
    if a_de_l_argent:
        return None
    return {"refusal": services.vehicle_trash_refusal(vehicule)}


@require_POST
def vehicle_trash(request, number: int):
    """Met une fiche sans argent à la corbeille (D74). Lue corbeille comprise : un second
    clic reçoit « déjà dans la corbeille » plutôt qu'une page introuvable."""
    vehicule = get_object_or_404(Vehicle.all_objects, number=number)
    try:
        services.trash_vehicle(vehicule)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect(vehicule if not vehicule.is_deleted else "vehicles:list")
    messages.success(
        request,
        f"La fiche {vehicule.display_name} est dans la corbeille. Tu peux la restaurer depuis "
        "« Corbeille ».",
    )
    return redirect("vehicles:list")


def _sale_block(vehicule: Vehicle) -> dict | None:
    """Ce qu'affiche le bloc « Vente » de la fiche, ou None sans vente non annulée.

    `accounts` est vide si l'argent n'est pas partagé entre deux associés actifs : le bloc
    affiche la vente, sans les parts.
    """
    vente = sale_services.active_sale(vehicule)
    if vente is None:
        return None
    bloc = {
        "sale": vente,
        "receipts": vente.receipts.select_related("partner"),
        "deposit": sale_services.applied_deposit_detail(vente),
    }
    comptes = queries.sale_accounts(vehicule.pk, vente.pk)
    if comptes is None:
        return bloc
    associes = queries.active_partners()
    parts = [comptes.recap.shares[associe.pk] for associe in associes]
    affichage = margin_display(
        comptes.recap.margin,
        {associe.pk: part.margin_share for associe, part in zip(associes, parts, strict=True)},
    )
    bloc["accounts"] = {
        "margin": affichage,
        "partners": associes,
        "gains": [(associe, affichage.shown_shares[associe.pk]) for associe in associes],
        "rows": [
            ("Avancé sur le véhicule", [part.advanced for part in parts]),
            ("Moitié de la marge", [part.margin_share for part in parts]),
            ("Part totale", [part.total_share for part in parts]),
            ("Déjà encaissé", [part.received for part in parts]),
        ],
        "balance": balance_label(comptes.balance, comptes.partner_names),
        "vehicle_balance": balance_label(comptes.recap.balance, comptes.partner_names),
        "differs": comptes.balance != comptes.recap.balance,
        "settled": comptes.balance.is_settled,
    }
    return bloc


def _purchase_summary(vehicule: Vehicle, depenses) -> dict | None:
    """Prix payé et part de chaque payeur, ou None tant que l'achat n'est pas enregistré."""
    lignes = list(services.purchase_lines(vehicule).values_list("pk", flat=True))
    if not lignes:
        return None
    achat = queries.lines_cost(lignes, depenses)
    payeurs = Partner.objects.filter(pk__in=achat.by_partner).order_by("id")
    return {
        "price": achat.total,
        "payers": [(p, achat.by_partner[p.pk]) for p in payeurs if achat.by_partner[p.pk]],
    }


@require_http_methods(["GET", "POST"])
def vehicle_edit(request, number: int):
    """« Modifier la fiche » : tous les champs sauf le statut et la date d'achat."""
    vehicule = _get_vehicle(number)
    form = VehicleForm(request.POST or None, instance=vehicule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La fiche est enregistrée.")
        return redirect(vehicule)
    return render(
        request,
        "vehicles/vehicle_form.html",
        {
            "form": form,
            "vehicle": vehicule,
            "title": f"Modifier {vehicule.display_name}",
            "submit_label": "Enregistrer",
        },
    )


@require_POST
def vehicle_status(request, number: int):
    """« Changer le statut » : applique le statut choisi s'il est permis (D35)."""
    vehicule = _get_vehicle(number)
    nouveau = request.POST.get("status", "")
    try:
        services.change_status(vehicule, nouveau, request.partner)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, f"Nouveau statut : {STATUS_STYLES[nouveau].label}.")
    return redirect(vehicule)


@require_http_methods(["GET", "POST"])
def vehicle_reopen(request, number: int):
    """« Rouvrir la fiche » : motif obligatoire, le véhicule reste vendu (D22)."""
    vehicule = _get_vehicle(number)
    if not vehicule.expenses_locked:
        messages.error(request, "Cette fiche n'est pas verrouillée.")
        return redirect(vehicule)
    form = ReopenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reopen_vehicle(vehicule, form.cleaned_data["reason"])
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            messages.success(request, "La fiche est rouverte : tu peux corriger les dépenses.")
            return redirect(vehicule)
    return render(request, "vehicles/vehicle_reopen.html", {"vehicle": vehicule, "form": form})


@require_POST
def vehicle_lock(request, number: int):
    """« Verrouiller la fiche » : les dépenses ne se modifient plus (D22)."""
    vehicule = _get_vehicle(number)
    try:
        services.lock_vehicle(vehicule)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, "La fiche est verrouillée.")
    return redirect(vehicule)


@require_http_methods(["GET", "POST"])
def vehicle_purchase(request, number: int):
    """« Enregistrer l'achat » : prix, date, payeur(s) et vendeur, créé sur place si besoin.
    Le véhicule passe en « Acheté »."""
    vehicule = _get_vehicle(number)
    if vehicule.status != Vehicle.Status.WAITING_PURCHASE:
        messages.error(request, "L'achat de ce véhicule est déjà enregistré.")
        return redirect(vehicule)

    donnees = request.POST or None
    form = PurchaseForm(
        donnees,
        vehicle=vehicule,
        partners=queries.active_partners(),
        current_partner=request.partner,
    )
    contact_form, nouveau = new_contact_form(donnees, "seller", "vendeur")

    # Les deux formulaires sont validés ensemble : toutes les erreurs s'affichent d'un coup.
    achat_valide = donnees is not None and form.is_valid()
    vendeur_valide = not nouveau or contact_form.is_valid()
    if achat_valide and vendeur_valide:
        vendeur = contact_form.save(commit=False) if nouveau else form.contact
        try:
            services.record_purchase(
                vehicule,
                price=form.cleaned_data["price"],
                date=form.cleaned_data["date"],
                payments=form.payments,
                seller=vendeur,
                partner=request.partner,
            )
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            messages.success(request, f"Achat enregistré : {vehicule.display_name} est acheté.")
            return redirect(vehicule)

    return render(
        request,
        "vehicles/purchase_form.html",
        {"vehicle": vehicule, "form": form, "contact_form": contact_form},
    )


@require_POST
def vehicle_photos_add(request, number: int):
    """Ajoute les photos envoyées (glisser-déposer ou « Choisir des photos ») à une étape."""
    vehicule = _get_vehicle(number)
    try:
        ajoutees, refusees = services.add_photos(
            vehicule, request.FILES.getlist("photos"), request.POST.get("stage", "")
        )
    except ValidationError as erreur:
        return _photos_response(request, vehicule, [("error", " ".join(erreur.messages))])
    notes = []
    if ajoutees:
        etape = Attachment.Stage(ajoutees[0].stage).label
        quantite = "1 photo ajoutée" if len(ajoutees) == 1 else f"{len(ajoutees)} photos ajoutées"
        notes.append(("success", f"{quantite} dans « {etape} »."))
    notes += [("error", f"{nom} n'a pas été ajoutée. {raison}") for nom, raison in refusees]
    return _photos_response(request, vehicule, notes)


@require_POST
def photo_delete(request, number: int, photo_id: int):
    """Met une photo à la corbeille (elle pourra être restaurée)."""
    vehicule = _get_vehicle(number)
    photo = get_object_or_404(Attachment, pk=photo_id, vehicle=vehicule, kind=Attachment.Kind.PHOTO)
    services.remove_photo(photo)
    return _photos_response(request, vehicule, [("success", "La photo est dans la corbeille.")])


def _photos_response(request, vehicule: Vehicle, notes: list[tuple[str, str]]):
    """Avec HTMX : la galerie seule, avec les messages. Sans : retour à la fiche."""
    if _wants_fragment(request):
        return render(
            request,
            "vehicles/vehicle_detail.html#photos",
            {
                "vehicle": vehicule,
                "photo_stages": services.photo_stages(vehicule),
                "photo_notes": notes,
            },
        )
    for niveau, texte in notes:
        (messages.error if niveau == "error" else messages.success)(request, texte)
    return redirect(vehicule)

"""Vues des dépenses : ajout, modification, fiche d'une dépense, aperçu pendant la saisie,
pièces à commander et statut des pièces, retours et remboursements, transferts de pièce.

Les vues restent minces : les règles sont dans `expenses/services.py`, l'argent dans
`accounting/`.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_htmx.http import HttpResponseClientRedirect

from accounting import queries
from accounting.services import expense_preview, refund_left, shipping_cost
from core.formatting import parse_id
from core.forms import MoneyField
from core.models import Attachment, ExpenseCategory, Partner
from vehicles.models import Vehicle

from . import services
from .forms import (
    ExpenseForm,
    PartToOrderForm,
    RefundForm,
    TransferForm,
    line_formset,
    parts_to_order_formset,
)
from .models import Expense, ExpenseLine, PartTransfer, Refund


def _get_expense(pk: int) -> Expense:
    return get_object_or_404(Expense.objects.select_related("supplier"), pk=pk)


@require_http_methods(["GET", "POST"])
def expense_create(request):
    """« Ajouter une dépense » : depuis la fiche d'un véhicule (`?vehicule=<numéro>`, ses
    lignes y sont rattachées d'office) ou depuis l'accueil (dépense du garage).

    `?pieces=<id>&pieces=<id>` : « Enregistrer la commande » des pièces à commander
    choisies ; elles forment les lignes de départ et passeront en « Commandé ».

    `?depuis=garage` : depuis « Dépenses du garage » (D57), où l'on revient ensuite.
    """
    numero = parse_id(request.GET.get("vehicule", ""))
    ids = [parse_id(pk) for pk in request.GET.getlist("pieces")]
    # Onglet resté ouvert, double clic, retour arrière : une pièce cochée peut être sortie de
    # la liste entre-temps. Rien n'est commandé : le formulaire ne s'ouvre pas sans elle.
    refus = services.order_refusal(ids)
    if refus:
        messages.error(request, refus)
        return redirect("expenses:parts_to_order")
    pieces = list(services.parts_to_order().filter(pk__in=ids)) if ids else []
    vehicule = None
    if numero is not None:
        vehicule = get_object_or_404(Vehicle, number=numero)
        # Une fiche rouverte accepte de nouvelles dépenses, même si le véhicule reste vendu.
        if vehicule.expenses_locked:
            messages.error(request, services.lock_message([vehicule]))
            return redirect(vehicule)
    depuis_garage = vehicule is None and not pieces and request.GET.get("depuis") == "garage"
    return _expense_form(request, None, vehicule, pieces, from_garage=depuis_garage)


@require_http_methods(["GET", "POST"])
def expense_edit(request, pk: int):
    """« Modifier la dépense » : mêmes champs que l'ajout, lignes et payeurs compris."""
    depense = _get_expense(pk)
    refus = services.expense_lock_refusal(depense)
    if refus:
        messages.error(request, refus)
        return redirect(depense)
    return _expense_form(request, depense, None)


def _expense_form(
    request,
    depense: Expense | None,
    vehicule: Vehicle | None,
    pieces=(),
    from_garage: bool = False,
):
    donnees = request.POST or None
    # Achat d'une reprise : montant, payeurs et prix de la ligne sont grisés (D51).
    vente_reprise = services.trade_in_sale(depense) if depense is not None else None
    form = ExpenseForm(
        donnees,
        partners=queries.active_partners(),
        current_partner=request.partner,
        expense=depense,
        trade_in_purchase=vente_reprise is not None,
    )
    formset = line_formset(
        donnees,
        expense=depense,
        default_vehicle=vehicule,
        parts=pieces,
        trade_in_purchase=vente_reprise is not None,
    )
    factures = request.FILES.getlist("factures")
    refusees = services.refused_invoices(factures) if donnees is not None else []

    if donnees is not None and form.is_valid() and formset.is_valid():
        lignes = formset.entries()
        form.complete(lignes)
        if form.is_valid() and not refusees:
            try:
                depense = services.save_expense(
                    depense,
                    date=form.cleaned_data["date"],
                    supplier_name=form.cleaned_data["supplier_name"],
                    order_number=form.cleaned_data["order_number"],
                    total_paid=form.total,
                    is_personal=form.cleaned_data["is_personal"],
                    notes=form.cleaned_data["notes"],
                    lines=lignes,
                    payments=form.payments,
                    invoices=factures,
                )
            except ValidationError as erreur:
                form.add_error(None, erreur)
                # Tout a été annulé en base : l'en-tête affiche la dépense telle qu'elle est.
                if depense is not None:
                    depense.refresh_from_db()
            else:
                messages.success(request, "Dépense enregistrée.")
                if vehicule is not None:
                    return redirect(vehicule)
                if pieces:
                    return redirect("expenses:parts_to_order")
                # Sans ligne pour le garage, elle n'apparaîtrait pas dans la liste.
                if from_garage and depense.lines.filter(vehicle__isnull=True).exists():
                    return redirect(_garage_list_url(depense))
                return redirect(depense)

    if depense is not None:
        retour = depense.get_absolute_url()
    elif vehicule is not None:
        retour = vehicule.get_absolute_url()
    elif pieces:
        retour = reverse("expenses:parts_to_order")
    elif from_garage:
        retour = reverse("accounting:garage_expenses")
    else:
        retour = reverse("core:home")
    # Achat d'une reprise : la phrase s'affiche une fois, en aide ou en erreur après un refus.
    aide_reprise = services.trade_in_purchase_message(vente_reprise) if vente_reprise else None
    if aide_reprise in form.non_field_errors():
        aide_reprise = None
    return render(
        request,
        "expenses/expense_form.html",
        {
            "form": form,
            "formset": formset,
            "expense": depense,
            "vehicle": vehicule,
            "ordering_parts": bool(pieces),
            "back_url": retour,
            "invoices": depense.attachments.filter(kind=Attachment.Kind.INVOICE).order_by(
                "order", "id"
            )
            if depense is not None
            else [],
            "refused_invoices": refusees,
            "trade_in_notice": aide_reprise,
            "trade_in_locked": vente_reprise is not None,
            # Le navigateur ne garde pas les fichiers choisis quand la page revient.
            "invoices_to_choose_again": bool(factures),
        },
    )


def _garage_list_url(depense: Expense) -> str:
    """« Dépenses du garage » à l'année de la dépense, pour la retrouver dans la liste."""
    return f"{reverse('accounting:garage_expenses')}?annee={depense.date.year}"


@require_POST
def expense_preview_view(request):
    """Aperçu renvoyé pendant la saisie (HTMX) : somme des lignes, frais de port et coût
    réparti de chaque ligne. Les lignes incomplètes sont ignorées.

    `?depense=<id>` : dépense en cours de modification (sa ligne d'achat y est reconnue).
    `?vehicule=<numéro>` : fiche d'où l'on vient. Abandonné, ce véhicule n'est pas proposé à
    toutes les dépenses : sans lui, ses lignes seraient ignorées et l'aperçu serait faux."""
    numero = parse_id(request.GET.get("depense", ""))
    depense = Expense.objects.filter(pk=numero).first() if numero is not None else None
    numero = parse_id(request.GET.get("vehicule", ""))
    vehicule = Vehicle.objects.filter(number=numero).first() if numero is not None else None
    # Achat d'une reprise : le navigateur n'envoie pas les champs grisés (montant, quantité,
    # prix). Sans ce verrou, la ligne paraîtrait vide et l'aperçu afficherait 0,00 €.
    verrou = depense is not None and services.trade_in_sale(depense) is not None
    formset = line_formset(
        request.POST, expense=depense, default_vehicle=vehicule, trade_in_purchase=verrou
    )
    lignes = [
        form.entry()
        for form in formset.forms
        if form.is_valid() and form.cleaned_data and not form.cleaned_data.get("DELETE")
    ]
    try:
        total = MoneyField(required=False).clean(request.POST.get("total_paid", ""))
    except ValidationError:
        total = None
    if verrou:
        total = depense.total_paid
    apercu = expense_preview(total, [entree.as_input() for entree in lignes])
    return render(
        request,
        "expenses/expense_form.html#apercu",
        {
            "preview": apercu,
            "preview_lines": list(zip(lignes, apercu.allocated, strict=False)),
        },
    )


@require_GET
def expense_detail(request, pk: int):
    """La fiche d'une dépense : paiement, payeurs, lignes avec leur coût et leurs
    retours, factures."""
    depense = _get_expense(pk)
    verrouilles = services.locked_vehicles(depense)
    refus_corbeille = services.expense_trash_refusal(depense) if not verrouilles else None
    entrees = queries.load_expenses([depense.pk])
    couts = queries.line_costs(entrees)
    lignes = (
        depense.lines.select_related("category", "vehicle")
        .prefetch_related(
            Prefetch(
                "refunds",
                queryset=Refund.objects.select_related("refunded_to").order_by("date", "id"),
            ),
            Prefetch(
                "transfers",
                queryset=PartTransfer.objects.select_related("from_vehicle", "to_vehicle").order_by(
                    "date", "id"
                ),
            ),
        )
        .order_by("id")
    )
    rangs = []
    for ligne in lignes:
        cout = couts.get(ligne.pk)
        # Rien à rembourser : achat personnel (pas de coût) ou ligne déjà remboursée. Dépense
        # verrouillée : pas de retour à proposer.
        reste = refund_left(cout.allocated, cout.refunded) if cout and not verrouilles else None
        rangs.append(
            {
                "line": ligne,
                "cost": cout,
                "refunds": list(ligne.refunds.all()),
                "left": reste,
                "transfers": list(ligne.transfers.all()),
                # Même règle que `services.transfer_refusal`, sans relire chaque ligne (D90).
                "can_transfer": (
                    not verrouilles
                    and not depense.is_personal
                    and ligne.vehicle_id is not None
                    and ligne.category.code != ExpenseCategory.VEHICLE_PURCHASE
                ),
            }
        )
    return render(
        request,
        "expenses/expense_detail.html",
        {
            "expense": depense,
            "vehicle": services.single_vehicle(depense),
            "locked_vehicles": verrouilles,
            "trash_refusal": refus_corbeille,
            "payments": depense.payments.select_related("partner").order_by("partner_id"),
            "shipping": shipping_cost(entrees[0]),
            "rows": rangs,
            "invoices": depense.attachments.filter(kind=Attachment.Kind.INVOICE).order_by(
                "order", "id"
            ),
        },
    )


@require_POST
def expense_trash(request, pk: int):
    """Met une dépense entière à la corbeille (D74) : retour à la fiche de son véhicule, ou
    à l'accueil pour une dépense du garage. Lue corbeille comprise : un second clic reçoit
    « déjà dans la corbeille » plutôt qu'une page introuvable."""
    depense = get_object_or_404(Expense.all_objects, pk=pk)
    vehicule = services.single_vehicle(depense)
    try:
        services.trash_expense(depense)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect(depense if not depense.is_deleted else vehicule or "core:home")
    messages.success(
        request, "La dépense est dans la corbeille. Tu peux la restaurer depuis « Corbeille »."
    )
    return redirect(vehicule or "core:home")


@require_POST
def invoice_delete(request, pk: int, invoice_id: int):
    """Met une facture à la corbeille (elle pourra être restaurée)."""
    depense = _get_expense(pk)
    facture = get_object_or_404(
        Attachment, pk=invoice_id, expense=depense, kind=Attachment.Kind.INVOICE
    )
    try:
        services.remove_invoice(facture)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, "La facture est dans la corbeille.")
    return redirect(depense)


# ---------------------------------------------------------------------------
# Pièces à commander et statut des pièces
# ---------------------------------------------------------------------------


@require_GET
def parts_to_order(request):
    """« Pièces à commander » : les pièces pas encore payées, par véhicule, sauf celles d'un
    véhicule vendu ou abandonné (D50). On coche celles d'une même commande, puis « Enregistrer
    la commande » ouvre la dépense."""
    groupes: dict[int | None, list[ExpenseLine]] = {}
    for piece in services.parts_to_order():
        groupes.setdefault(piece.vehicle_id, []).append(piece)
    # Les pièces du garage (sans véhicule) viennent en dernier.
    ordre = sorted(groupes, key=lambda pk: pk is None)
    return render(
        request,
        "expenses/parts_to_order.html",
        {
            "groups": [(groupes[pk][0].vehicle, groupes[pk]) for pk in ordre],
        },
    )


def _get_part_to_order(pk: int) -> ExpenseLine:
    """Même une pièce sortie de la liste : un onglet resté ouvert reçoit alors un message
    du verrou, pas une page introuvable."""
    return get_object_or_404(services.noted_parts(), pk=pk)


def _part_back_url(request, vehicule: Vehicle | None) -> str:
    """Retour vers la fiche du véhicule si l'on en vient, sinon vers la liste."""
    if request.GET.get("depuis") == "fiche" and vehicule is not None:
        return vehicule.get_absolute_url()
    return reverse("expenses:parts_to_order")


@require_http_methods(["GET", "POST"])
def part_to_order_create(request):
    """« Ajouter une pièce à commander » : une ou plusieurs lignes, enregistrées toutes
    ensemble ou pas du tout (`?vehicule=<numéro>` : depuis sa fiche, préremplit le véhicule)."""
    numero = parse_id(request.GET.get("vehicule", ""))
    vehicule = get_object_or_404(Vehicle, number=numero) if numero is not None else None
    refus = services.part_to_order_refusal(vehicule)
    if refus:
        messages.error(request, refus)
        return redirect(vehicule)
    formset = parts_to_order_formset(request.POST or None, default_vehicle=vehicule)
    if request.method == "POST" and formset.is_valid():
        try:
            pieces = services.add_parts_to_order(formset.parts())
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
            return redirect(_part_back_url(request, vehicule))
        if len(pieces) == 1:
            messages.success(request, f"« {pieces[0].label} » est à commander.")
        else:
            messages.success(request, f"{len(pieces)} pièces sont à commander.")
        return redirect(_part_back_url(request, vehicule))
    return render(
        request,
        "expenses/part_to_order_form.html",
        {
            "formset": formset,
            "title": "Ajouter des pièces à commander",
            "back_url": _part_back_url(request, vehicule),
        },
    )


@require_http_methods(["GET", "POST"])
def part_to_order_edit(request, pk: int):
    """Modifier une pièce encore à commander."""
    piece = _get_part_to_order(pk)
    refus = services.part_to_order_refusal(piece.vehicle)
    if refus:
        messages.error(request, refus)
        return redirect(_part_back_url(request, piece.vehicle))
    form = PartToOrderForm(request.POST or None, line=piece)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_part_to_order(piece, **form.part_fields())
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
        else:
            messages.success(request, "La pièce est enregistrée.")
        return redirect(_part_back_url(request, piece.vehicle))
    return render(
        request,
        "expenses/part_to_order_form.html",
        {
            "form": form,
            "title": "Modifier la pièce à commander",
            "back_url": _part_back_url(request, piece.vehicle),
        },
    )


@require_POST
def part_to_order_delete(request, pk: int):
    """Met une pièce encore à commander à la corbeille (elle pourra être restaurée)."""
    piece = _get_part_to_order(pk)
    try:
        services.remove_part_to_order(piece)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, f"« {piece.label} » est dans la corbeille.")
    return redirect(_part_back_url(request, piece.vehicle))


@require_POST
def line_status_change(request, pk: int):
    """« Marquer reçue », « Marquer montée » : change le statut d'une pièce payée.

    Avec HTMX, renvoie la ligne mise à jour ; sinon, retour à la fiche du véhicule."""
    ligne = get_object_or_404(services.paid_lines().select_related("vehicle"), pk=pk)
    retour = ligne.vehicle.get_absolute_url() if ligne.vehicle else ligne.expense.get_absolute_url()
    try:
        services.change_part_status(ligne, request.POST.get("status", ""))
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        # Avec HTMX, la page entière est rechargée pour afficher le message.
        if request.htmx:
            return HttpResponseClientRedirect(retour)
    else:
        if request.htmx:
            ligne = services.paid_lines().get(pk=pk)
            (rang,) = services.line_rows([ligne], queries.load_expenses([ligne.expense_id]))
            return render(request, "components/ligne_depense.html", {"row": rang})
        messages.success(request, f"« {ligne.label} » : {ligne.get_status_display()}.")
    return redirect(retour)


# ---------------------------------------------------------------------------
# Retours et remboursements
# ---------------------------------------------------------------------------


def _refund_form(request, ligne: ExpenseLine, remboursement: Refund | None):
    """Formulaire commun à « Enregistrer un retour » et à sa correction."""
    refus = services.expense_lock_refusal(ligne.expense)
    if refus:
        messages.error(request, refus)
        return redirect(ligne.expense)
    try:
        marge = services.line_refund_room(ligne, remboursement)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect(ligne.expense)
    form = RefundForm(
        request.POST or None,
        partners=queries.active_partners(),
        refund=remboursement,
        amount_left=marge.left,
        default_partner=services.default_refunded_to(ligne.expense),
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.record_refund(
                ligne,
                date=form.cleaned_data["date"],
                amount=form.cleaned_data["amount"],
                refunded_to=form.partner(),
                notes=form.cleaned_data["notes"],
                refund=remboursement,
            )
        except ValidationError as erreur:
            # Montant ou associé : sous le champ concerné ; le reste en haut du formulaire.
            form.add_error(None, erreur)
        else:
            messages.success(request, f"Le retour de « {ligne.label} » est enregistré.")
            return redirect(ligne.expense)
    return render(
        request,
        "expenses/refund_form.html",
        {
            "form": form,
            "line": ligne,
            "room": marge,
            "refund": remboursement,
            "is_part": ligne.category.code == ExpenseCategory.PARTS,
        },
    )


@require_http_methods(["GET", "POST"])
def refund_create(request, pk: int):
    """« Enregistrer un retour » sur une ligne payée."""
    ligne = get_object_or_404(services.paid_lines().select_related("expense"), pk=pk)
    return _refund_form(request, ligne, None)


def _get_refund(pk: int) -> Refund:
    return get_object_or_404(
        Refund.objects.select_related("line__expense", "line__category", "refunded_to"),
        pk=pk,
        line__deleted_at__isnull=True,
        line__expense__deleted_at__isnull=True,
    )


@require_http_methods(["GET", "POST"])
def refund_edit(request, pk: int):
    """Corriger un remboursement (préciser un montant « à préciser », par exemple)."""
    remboursement = _get_refund(pk)
    return _refund_form(request, remboursement.line, remboursement)


@require_POST
def refund_delete(request, pk: int):
    """Met un remboursement à la corbeille (il pourra être restauré)."""
    remboursement = _get_refund(pk)
    try:
        services.remove_refund(remboursement)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        messages.success(request, "Le remboursement est dans la corbeille.")
    return redirect(remboursement.line.expense)


@require_http_methods(["GET", "POST"])
def line_transfer(request, pk: int):
    """« Transférer sur un autre véhicule » : toute la ligne change de véhicule, ses payeurs
    restent (D90). Retour à la dépense, qui montre la ligne sur son nouveau véhicule."""
    # Corbeille comprise : une page restée ouverte pendant une mise à la corbeille reçoit le
    # refus, pas une page introuvable.
    ligne = get_object_or_404(
        ExpenseLine.all_objects.filter(expense__isnull=False).select_related(
            "expense", "vehicle", "category"
        ),
        pk=pk,
    )
    refus = services.transfer_refusal(ligne)
    if refus:
        messages.error(request, refus)
        return _back_from_transfer(ligne)
    form = TransferForm(request.POST or None, line=ligne)
    if request.method == "POST" and form.is_valid():
        cible = form.cleaned_data["to_vehicle"]
        try:
            services.transfer_line(
                ligne,
                to_vehicle=cible,
                date=form.cleaned_data["date"],
                notes=form.cleaned_data["notes"],
                from_vehicle_id=form.cleaned_data["from_vehicle"],
            )
        except ValidationError as erreur:
            if services.TRANSFER_MOVED in erreur.messages:
                messages.error(request, services.TRANSFER_MOVED)
                return redirect(ligne.expense)
            form.add_error(None, erreur)
        else:
            messages.success(
                request, f"La ligne « {ligne.label} » est passée sur {cible.display_name}."
            )
            return redirect(ligne.expense)
    cout = services.line_cost(ligne)
    payeurs = Partner.objects.in_bulk(cout.shares)
    return render(
        request,
        "expenses/transfer_form.html",
        {
            "form": form,
            "line": ligne,
            "cost": cout,
            "payers": [payeurs[pk] for pk in sorted(cout.shares) if cout.shares[pk]],
            # Un envoi garde son formulaire, pour montrer l'erreur sous le champ.
            "has_targets": form.is_bound or form.proposed_vehicles.exists(),
        },
    )


def _back_from_transfer(ligne: ExpenseLine):
    """La dépense, ou la fiche du véhicule si la dépense est à la corbeille."""
    if not ligne.expense.is_deleted:
        return redirect(ligne.expense)
    if ligne.vehicle is not None and not ligne.vehicle.is_deleted:
        return redirect(ligne.vehicle)
    return redirect("core:home")

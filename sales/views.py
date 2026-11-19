"""Vues des ventes : acompte, vente, modification et annulation.

Les vues restent minces : les règles sont dans `sales/services.py`, l'argent dans
`accounting/`.
"""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounting import queries
from accounting.services import ZERO, sale_preview
from core.formatting import format_euros
from core.forms import MoneyField, new_contact_form
from vehicles.forms import trade_in_vehicle_form
from vehicles.models import Vehicle

from . import services
from .forms import (
    DepositCancelForm,
    DepositEditForm,
    DepositForm,
    DepositReceiptForm,
    DepositReceiptsForm,
    SaleForm,
)
from .models import Deposit, DepositReceipt, Sale


def _get_deposit(pk: int) -> Deposit:
    return get_object_or_404(Deposit.objects.select_related("vehicle", "buyer"), pk=pk)


def _active_or_redirect(request, acompte: Deposit):
    """Un acompte qui n'est plus en cours ne se modifie plus : retour à la fiche."""
    if acompte.status != Deposit.Status.ACTIVE:
        messages.error(request, "Cet acompte n'est plus en cours : il ne se modifie plus.")
        return redirect(acompte.vehicle)
    return None


@require_http_methods(["GET", "POST"])
def deposit_create(request, number: int):
    """« Enregistrer un acompte » : le véhicule passe en « Réservé »."""
    vehicule = get_object_or_404(Vehicle, number=number)
    refus = services.deposit_refusal(vehicule)
    if refus:
        messages.error(request, refus)
        return redirect(vehicule)
    donnees = request.POST or None
    form = DepositForm(donnees, partners=queries.active_partners(), current_partner=request.partner)
    # Les deux formulaires sont validés ensemble : toutes les erreurs s'affichent d'un coup.
    contact_form, nouveau = new_contact_form(donnees, "buyer", "acheteur")
    acompte_valide = donnees is not None and form.is_valid()
    acheteur_valide = not nouveau or contact_form.is_valid()
    if acompte_valide and acheteur_valide:
        acheteur = contact_form.save(commit=False) if nouveau else form.contact
        try:
            services.record_deposit(
                vehicule, **form.deposit_fields(), buyer=acheteur, partner=request.partner
            )
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            messages.success(request, f"Acompte enregistré : {vehicule.display_name} est réservé.")
            return redirect(vehicule)
    return render(
        request,
        "sales/deposit_form.html",
        {"vehicle": vehicule, "form": form, "contact_form": contact_form},
    )


@require_http_methods(["GET", "POST"])
def deposit_edit(request, pk: int):
    """« Corriger l'acompte » : la date de la réservation et l'acheteur, tant qu'il est en
    cours. Chaque versement se corrige depuis la fiche (D89)."""
    acompte = _get_deposit(pk)
    retour = _active_or_redirect(request, acompte)
    if retour:
        return retour
    donnees = request.POST or None
    form = DepositEditForm(donnees, deposit=acompte)
    contact_form, nouveau = new_contact_form(donnees, "buyer", "acheteur")
    acompte_valide = donnees is not None and form.is_valid()
    acheteur_valide = not nouveau or contact_form.is_valid()
    if acompte_valide and acheteur_valide:
        acheteur = contact_form.save(commit=False) if nouveau else form.contact
        try:
            services.update_deposit(acompte, date=form.cleaned_data["date"], buyer=acheteur)
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            messages.success(request, "L'acompte est corrigé.")
            return redirect(acompte.vehicle)
    return render(
        request,
        "sales/deposit_edit.html",
        {
            "vehicle": acompte.vehicle,
            "deposit": services.deposit_detail(acompte),
            "form": form,
            "contact_form": contact_form,
        },
    )


@require_http_methods(["GET", "POST"])
def deposit_receipts_add(request, pk: int):
    """« Ajouter un versement » : l'acheteur complète l'acompte en cours (D89)."""
    acompte = _get_deposit(pk)
    retour = _active_or_redirect(request, acompte)
    if retour:
        return retour
    form = DepositReceiptsForm(
        request.POST or None, partners=queries.active_partners(), current_partner=request.partner
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.add_deposit_receipts(acompte, **form.receipts_fields())
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            total = services.deposit_detail(acompte).total
            messages.success(
                request, f"Versement ajouté : l'acompte fait maintenant {format_euros(total)}."
            )
            return redirect(acompte.vehicle)
    return render(
        request,
        "sales/deposit_receipts_form.html",
        {"vehicle": acompte.vehicle, "deposit": services.deposit_detail(acompte), "form": form},
    )


def _get_receipt(pk: int) -> DepositReceipt:
    # Corbeille comprise : un versement retiré depuis un autre onglet donne un message, pas
    # une page introuvable.
    return get_object_or_404(
        DepositReceipt.all_objects.select_related("deposit", "deposit__vehicle", "partner"), pk=pk
    )


@require_http_methods(["GET", "POST"])
def deposit_receipt_edit(request, pk: int):
    """« Corriger le versement » : montant, date, qui l'a reçu. L'écran propose aussi de le
    retirer s'il n'est pas le seul."""
    versement = _get_receipt(pk)
    acompte = versement.deposit
    if versement.is_deleted:
        messages.error(request, "Ce versement a déjà été retiré. Recharge la page.")
        return redirect(acompte.vehicle)
    retour = _active_or_redirect(request, acompte)
    if retour:
        return retour
    form = DepositReceiptForm(
        request.POST or None, partners=queries.active_partners(), receipt=versement
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.update_deposit_receipt(versement, **form.receipt_fields())
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            messages.success(request, "Le versement est corrigé.")
            return redirect(acompte.vehicle)
    detail = services.deposit_detail(acompte)
    return render(
        request,
        "sales/deposit_receipt_form.html",
        {
            "vehicle": acompte.vehicle,
            "deposit": detail,
            "receipt": versement,
            "form": form,
            "can_remove": len(detail.receipts) > 1,
        },
    )


@require_POST
def deposit_receipt_remove(request, pk: int):
    """« Retirer ce versement » : il part à la corbeille et ne compte plus nulle part."""
    versement = _get_receipt(pk)
    try:
        services.remove_deposit_receipt(versement)
    except ValidationError as erreur:
        messages.error(request, erreur.messages[0])
    else:
        messages.success(request, "Le versement est retiré.")
    return redirect(versement.deposit.vehicle)


@require_http_methods(["GET", "POST"])
def deposit_cancel(request, pk: int):
    """« Annuler l'acompte » : on indique s'il a été rendu ou conservé, et le véhicule
    revient au statut d'avant la réservation."""
    acompte = _get_deposit(pk)
    if acompte.status != Deposit.Status.ACTIVE:
        messages.error(request, "Cet acompte n'est plus en cours.")
        return redirect(acompte.vehicle)
    form = DepositCancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.cancel_deposit(acompte, form.cleaned_data["outcome"], request.partner)
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            devenir = "rendu" if acompte.status == Deposit.Status.REFUNDED else "conservé"
            messages.success(request, f"L'acompte est annulé et {devenir}.")
            return redirect(acompte.vehicle)
    return render(
        request,
        "sales/deposit_cancel.html",
        {
            "vehicle": acompte.vehicle,
            "deposit": services.deposit_detail(acompte),
            "form": form,
            "return_status": services.status_before_reservation(acompte.vehicle),
        },
    )


def _get_sale(pk: int) -> Sale:
    return get_object_or_404(
        Sale.objects.select_related("vehicle", "buyer", "trade_in_vehicle"), pk=pk
    )


@require_http_methods(["GET", "POST"])
def sale_create(request, number: int):
    """« Enregistrer la vente » : l'acompte en cours est déduit, le véhicule passe en « Vendu »."""
    vehicule = get_object_or_404(Vehicle, number=number)
    refus = services.sale_refusal(vehicule)
    if refus:
        messages.error(request, refus)
        return redirect(vehicule)
    return _sale_form(request, vehicule, None)


@require_http_methods(["GET", "POST"])
def sale_edit(request, pk: int):
    """« Modifier la vente » : tant qu'elle n'est pas annulée (D49)."""
    vente = _get_sale(pk)
    if vente.is_cancelled:
        messages.error(request, "Cette vente est annulée : elle ne se modifie plus.")
        return redirect(vente.vehicle)
    return _sale_form(request, vente.vehicle, vente)


def _sale_form(request, vehicule: Vehicle, vente: Sale | None):
    """Formulaire commun à l'enregistrement et à la modification d'une vente."""
    donnees = request.POST or None
    if vente is not None:
        acompte = services.applied_deposit_detail(vente)
        acheteur_initial = vente.buyer_id
    else:
        acompte = services.active_deposit_detail(vehicule)
        acheteur_initial = acompte.deposit.buyer_id if acompte else None
    form = SaleForm(
        donnees,
        vehicle=vehicule,
        partners=queries.active_partners(),
        deposits=services.deposit_inputs(vehicule),
        current_partner=request.partner,
        sale=vente,
        buyer_pk=acheteur_initial,
    )
    # Les formulaires sont validés ensemble : toutes les erreurs s'affichent d'un coup.
    contact_form, nouveau = new_contact_form(donnees, "buyer", "acheteur")
    # Une reprise se saisit à l'enregistrement de la vente, jamais à sa modification.
    reprise_form, reprise = trade_in_vehicle_form(donnees if vente is None else None)
    vente_valide = donnees is not None and form.is_valid()
    acheteur_valide = not nouveau or contact_form.is_valid()
    reprise_valide = not reprise or reprise_form.is_valid()
    if vente_valide and acheteur_valide and reprise_valide:
        acheteur = contact_form.save(commit=False) if nouveau else form.contact
        try:
            if vente is None:
                enregistree = services.record_sale(
                    vehicule,
                    **form.sale_fields(),
                    buyer=acheteur,
                    partner=request.partner,
                    trade_in=(
                        services.TradeIn(reprise_form.save(commit=False), form.trade_in_value)
                        if reprise
                        else None
                    ),
                )
            else:
                services.update_sale(vente, **form.sale_fields(), buyer=acheteur)
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            if vente is not None:
                messages.success(request, "La vente est modifiée.")
            elif enregistree.trade_in_vehicle is None:
                messages.success(request, f"Vente enregistrée : {vehicule.display_name} est vendu.")
            else:
                messages.success(
                    request,
                    f"Vente enregistrée : {vehicule.display_name} est vendu, et "
                    f"{enregistree.trade_in_vehicle.display_name} arrive au garage.",
                )
            return redirect(vehicule)
    return render(
        request,
        "sales/sale_form.html",
        {
            "vehicle": vehicule,
            "sale": vente,
            "deposit": acompte,
            "form": form,
            "contact_form": contact_form,
            "trade_in_form": reprise_form,
        },
    )


def _typed_trade_in(donnees) -> Decimal:
    """La valeur de reprise en cours de saisie : zéro si la case est décochée ou si ce n'est
    pas encore un montant."""
    if not donnees.get("has_trade_in"):
        return ZERO
    try:
        return MoneyField(required=False).clean(donnees.get("trade_in_value", "")) or ZERO
    except ValidationError:
        return ZERO


@require_POST
def sale_preview_view(request, number: int):
    """Aperçu renvoyé pendant la saisie du prix (HTMX) : reste à encaisser, marge, part de
    chacun. Vide tant que le prix n'est pas un montant valable."""
    vehicule = get_object_or_404(Vehicle, number=number)
    try:
        prix = MoneyField(required=False).clean(request.POST.get("price", ""))
    except ValidationError:
        prix = None
    if prix is None:
        # Vraiment vide : la moindre espace ferait afficher une boîte grise sans contenu.
        return HttpResponse("")
    associes = queries.active_partners()
    vente = services.active_sale(vehicule)
    apercu = sale_preview(
        prix,
        queries.vehicle_cost(vehicule.pk).total,
        [associe.pk for associe in associes],
        services.deposit_inputs(vehicule),
        vente.trade_in_value if vente else _typed_trade_in(request.POST),
    )
    return render(
        request,
        "sales/sale_form.html#apercu",
        {
            "preview": apercu,
            "preview_shares": [(a, apercu.shown_shares[a.pk]) for a in associes],
            # En modification, l'argent est déjà encaissé : il ne « reste » rien à recevoir.
            "editing": vente is not None,
        },
    )


@require_http_methods(["GET", "POST"])
def sale_cancel(request, pk: int):
    """« Annuler la vente » : écran de confirmation, qui demande ce que devient l'acompte
    s'il y en avait un. Le véhicule revient en « En vente », et le véhicule repris part à la
    corbeille. Une annulation que la reprise empêche est refusée dès l'ouverture de l'écran."""
    vente = _get_sale(pk)
    if vente.is_cancelled:
        messages.error(request, "Cette vente est déjà annulée.")
        return redirect(vente.vehicle)
    refus = services.trade_in_cancel_refusal(vente)
    if refus:
        messages.error(request, refus)
        return redirect(vente.vehicle)
    acompte = services.applied_deposit_detail(vente)
    form = DepositCancelForm(request.POST or None) if acompte else None
    if request.method == "POST" and (form is None or form.is_valid()):
        try:
            services.cancel_sale(
                vente, form.cleaned_data["outcome"] if form else None, request.partner
            )
        except ValidationError as erreur:
            if form is None:
                messages.error(request, erreur.messages[0])
                return redirect(vente.vehicle)
            form.add_error(None, erreur)
        else:
            message = f"La vente est annulée : {vente.vehicle.display_name} revient en vente."
            if vente.trade_in_vehicle is not None:
                message += f" {vente.trade_in_vehicle.display_name} part à la corbeille."
            messages.success(request, message)
            return redirect(vente.vehicle)
    return render(
        request,
        "sales/sale_cancel.html",
        {
            "vehicle": vente.vehicle,
            "sale": vente,
            "receipts": vente.receipts.select_related("partner"),
            "deposit": acompte,
            "form": form,
        },
    )

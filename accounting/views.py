"""Vues des comptes : solde entre associés, enregistrement et annulation des règlements.

Les vues restent minces : le solde et les refus sont calculés dans `accounting/services.py`,
les écritures sont dans `accounting/settlements.py`.
"""

from io import BytesIO

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.formatting import format_euros, parse_id
from core.forms import MoneyField
from core.models import Partner
from vehicles.models import Vehicle

from . import export, queries, services, settlements
from .forms import SettlementCancelForm, SettlementForm
from .models import Settlement


def _balance_sentence(comptes: queries.PartnerAccounts) -> str:
    """« Axel doit 205,44 € à Mathis » ou « À l'équilibre » (D67)."""
    return services.balance_label(comptes.balance, comptes.partner_names)


def _after_sentence(solde: services.Balance | None, noms: dict[int, str]) -> str:
    """Ce qu'il reste après un règlement ou une annulation, pour le message de confirmation :
    une phrase, « Vous êtes à l'équilibre. » ou « Axel doit 17,14 € à Mathis. »."""
    if solde is None:
        return ""
    if solde.is_settled:
        return " Vous êtes à l'équilibre."
    return f" {services.balance_sentence(solde, noms)}."


def _after_comptes(comptes: queries.PartnerAccounts | None) -> str:
    """Comme `_after_sentence`, à partir des comptes relus (annulation d'un règlement)."""
    if comptes is None:
        return ""
    return _after_sentence(comptes.balance, comptes.partner_names)


@require_GET
def accounts(request):
    """« Comptes » : le solde entre associés, d'où il vient, et l'historique des règlements."""
    comptes = queries.partner_accounts()
    associes = queries.active_partners()
    contexte = {"accounts": comptes, "settlements": _settlement_rows()}
    if comptes is not None:
        details = [comptes.details[associe.pk] for associe in associes]
        contexte.update(
            balance_sentence=_balance_sentence(comptes),
            partners=associes,
            sorti_rows=[
                ("Dépenses payées pour le garage", [d.expenses_paid for d in details]),
                ("Règlements versés", [d.settlements_paid for d in details]),
            ],
            recu_rows=[
                ("Encaissé (ventes, acomptes, reprises)", [d.sales_received for d in details]),
                ("Remboursements reçus", [d.refunds_received for d in details]),
                ("Règlements reçus", [d.settlements_received for d in details]),
            ],
            totals=[
                ("Total sorti", [d.position.paid_out for d in details]),
                ("Total reçu", [d.position.received for d in details]),
                ("Avancé pour le garage (sorti − reçu)", [d.position.value for d in details]),
            ],
        )
    return render(request, "accounting/accounts.html", contexte)


def _chosen_year(request) -> int:
    """L'année demandée (`?annee=2025`), sinon l'année en cours."""
    annee = parse_id(request.GET.get("annee", ""))
    if annee is not None and 2000 <= annee <= 2100:
        return annee
    return timezone.localdate().year


@require_GET
def garage_expenses(request):
    """« Dépenses du garage » : les dépenses sans véhicule d'une année, leur total et la part
    de chacun ; les achats personnels à part, pour mémoire (D57, D58)."""
    annee = _chosen_year(request)
    page = queries.garage_expenses(annee, timezone.localdate().year)
    associes = queries.active_partners()
    contexte = {"year": annee, "years": page.years, "report": page.report}
    if page.report is not None:
        contexte.update(
            rows=[_garage_row(page.expenses[d.expense.key], d) for d in page.report.items],
            personal_rows=[
                {"expense": page.expenses[d.key], "labels": _labels(page.expenses[d.key])}
                for d in page.report.personal
            ],
            shares=[(associe, page.report.shares[associe.pk]) for associe in associes],
        )
    return render(request, "accounting/garage_expenses.html", contexte)


@require_GET
def report(request):
    """« Bilan » : l'année choisie (tuiles, véhicules vendus et abandonnés, dépenses du
    garage), ce qui est encore au garage aujourd'hui, et le bilan de chaque associé depuis
    le début (D59 à D63). Tous les montants viennent de `accounting.services`."""
    annee = _chosen_year(request)
    page = queries.report_page(annee, timezone.localdate().year)
    rapport, bilan_annee = page.report, page.year
    associes = queries.active_partners()
    if rapport.partners is None:
        associes = []

    def ligne(row) -> dict:
        return _report_row(row, page.vehicles[row.vehicle], associes)

    cases = rapport.buckets
    return render(
        request,
        "accounting/report.html",
        {
            "year": annee,
            "years": page.years,
            "year_report": bilan_annee,
            "profit": services.SignedAmount(bilan_annee.profit),
            "margins": services.SignedAmount(bilan_annee.margins),
            "invested": services.invested_breakdown(annee, rapport.rows, rapport.data.expenses),
            "abandons": services.SignedAmount(bilan_annee.abandons_result),
            "shares": [
                (associe, services.SignedAmount(bilan_annee.shares[associe.pk]))
                for associe in associes
            ],
            "sold_rows": [ligne(r) for r in services.sold_in_year(rapport.rows, annee)],
            "abandoned_rows": [ligne(r) for r in services.abandoned_in_year(rapport.rows, annee)],
            "garage_rows": sorted(
                (
                    ligne(r)
                    for r in rapport.rows
                    if r.group in (services.GROUP_GARAGE, services.GROUP_TRASH)
                ),
                key=lambda r: r["vehicle"].number,
            ),
            "buckets": cases,
            "still_in_garage": services.SignedAmount(cases.still_in_garage),
            "partners": associes,
            "partner_columns": [
                _partner_column(associe, rapport.partners[associe.pk]) for associe in associes
            ],
        },
    )


@require_GET
def report_export(request):
    """« Exporter en Excel » : le bilan et toutes les tables, corbeille comprise (D65).
    Lecture seule : permis aussi en mode lecture seule."""
    return FileResponse(
        BytesIO(export.build_workbook()),
        as_attachment=True,
        filename=export.export_filename(timezone.localdate()),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _report_row(row, vehicule, associes) -> dict:
    """Une ligne du tableau par véhicule : sa fiche, l'investissement de chacun, son
    résultat sans signe moins (« Perte de … »)."""
    return {
        "vehicle": vehicule,
        "invested": [row.cost.by_partner.get(associe.pk, services.ZERO) for associe in associes],
        "cost": row.cost.total,
        "sale_price": row.sale.price if row.sale else None,
        "result": None if row.result is None else services.SignedAmount(row.result),
        "days": row.days,
        "abandoned_on": row.abandoned_on,
        "kept_deposits": row.kept_deposits,
    }


def _partner_column(associe, bilan) -> dict:
    """La colonne d'un associé dans le bilan par associé (D61) : chaque montant qui peut
    être négatif passe par `SignedAmount`, le gabarit choisit ses mots."""
    return {
        "partner": associe,
        "put_in": bilan.put_in,
        "profit_share": services.SignedAmount(bilan.profit_share),
        "due": services.SignedAmount(bilan.due_to_partner),
        "sales_received": bilan.sales_received,
        "settlements": services.SignedAmount(bilan.settlements_net),
        "already_back": services.SignedAmount(bilan.already_back),
        "still_in_garage": services.SignedAmount(bilan.still_in_garage),
        "balance": services.SignedAmount(bilan.balance),
    }


def _labels(depense, line_ids=None) -> str:
    """Les désignations des lignes (seulement `line_ids` si donné), séparées par des virgules."""
    return ", ".join(
        ligne.label for ligne in depense.lines.all() if line_ids is None or ligne.pk in line_ids
    )


def _garage_row(depense, item) -> dict:
    """Une ligne de la liste : la dépense, ses lignes du garage et leur coût net."""
    lignes = set(item.garage_lines)
    return {
        "expense": depense,
        "labels": _labels(depense, lignes),
        "categories": sorted(
            {ligne.category.name for ligne in depense.lines.all() if ligne.pk in lignes}
        ),
        "payers": [paiement.partner for paiement in depense.payments.all()],
        "cost": item.cost,
        "shared": item.shared_with_vehicle,
    }


def _settlement_rows() -> list[dict]:
    """Les règlements, le plus récent d'abord, avec leur annulation éventuelle."""
    reglements = Settlement.objects.select_related(
        "from_partner", "to_partner", "cancels", "cancelled_by"
    ).order_by("-date", "-id")
    lignes = []
    for reglement in reglements:
        # Absent sans annulation : l'accès direct lèverait une exception.
        annule_par = getattr(reglement, "cancelled_by", None)
        lignes.append(
            {
                "settlement": reglement,
                "cancelled_by": annule_par,
                # Mêmes règles que `settlements.cancel_refusal`, avec ce qui est déjà chargé
                # (une requête par ligne sinon). Le service les revérifie à l'annulation.
                "can_cancel": reglement.cancels_id is None
                and annule_par is None
                and reglement.from_partner.is_active
                and reglement.to_partner.is_active,
            }
        )
    return lignes


def _vehicle_to_return_to(request) -> Vehicle | None:
    """La fiche d'où l'on vient (`?vehicule=<numéro>`, lien du bloc Vente, D55), ou None."""
    numero = parse_id(request.GET.get("vehicule", ""))
    if numero is None:
        return None
    return Vehicle.objects.filter(number=numero).first()


@require_http_methods(["GET", "POST"])
def settlement_create(request):
    """« Enregistrer le règlement » : celui qui doit verse tout ou partie à l'autre (D53)."""
    retour = _vehicle_to_return_to(request)
    comptes = queries.partner_accounts()
    if comptes is None:
        messages.error(request, settlements.ACCOUNTS_UNAVAILABLE)
        return redirect(retour or "accounting:accounts")
    if comptes.balance.is_settled and request.method == "GET":
        messages.info(request, "Vous êtes à l'équilibre : il n'y a rien à régler.")
        return redirect(retour or "accounting:accounts")

    form = SettlementForm(request.POST or None, due=comptes.balance)
    if request.method == "POST" and form.is_valid():
        donnees = form.cleaned_data
        associes = Partner.objects.in_bulk([donnees["from_partner"], donnees["to_partner"]])
        payeur = associes.get(donnees["from_partner"])
        receveur = associes.get(donnees["to_partner"])
        if payeur is None or receveur is None:
            form.add_error(None, "Le solde a changé. Recharge la page.")
        else:
            try:
                enregistre = settlements.record_settlement(
                    from_partner=payeur,
                    to_partner=receveur,
                    amount=donnees["amount"],
                    date=donnees["date"],
                    notes=donnees["notes"],
                    seen_due=donnees["seen_due"],
                )
            except ValidationError as erreur:
                form.add_error(None, erreur)
            else:
                messages.success(
                    request,
                    f"Règlement enregistré : {payeur.name} a versé "
                    f"{format_euros(donnees['amount'])} à {receveur.name}."
                    + _after_sentence(enregistre.balance_after, enregistre.partner_names),
                )
                return redirect(retour or "accounting:accounts")

    noms = comptes.partner_names
    return render(
        request,
        "accounting/settlement_form.html",
        {
            "form": form,
            "balance_sentence": _balance_sentence(comptes),
            "debtor": Partner.objects.filter(pk=comptes.balance.debtor).first(),
            "creditor": Partner.objects.filter(pk=comptes.balance.creditor).first(),
            "names": noms,
            "return_vehicle": retour,
        },
    )


@require_POST
def settlement_preview(request):
    """Aperçu renvoyé pendant la saisie du montant (HTMX) : le solde une fois le règlement
    fait. Vide tant que le montant n'en est pas un."""
    try:
        montant = MoneyField(required=False).clean(request.POST.get("amount", ""))
    except ValidationError:
        montant = None
    comptes = queries.partner_accounts()
    if montant is None or comptes is None or comptes.balance.is_settled:
        # Vraiment vide : la moindre espace ferait afficher une boîte grise sans contenu.
        return HttpResponse("")
    du = comptes.balance
    reglement = services.SettlementInput(du.debtor, du.creditor, montant)
    probleme = services.settlement_problem(reglement, du, comptes.partner_names)
    apres = services.balance_after(comptes.positions, reglement)
    if apres.is_settled:
        phrase = "Après ce règlement, vous serez à l'équilibre."
    else:
        noms = comptes.partner_names
        phrase = (
            f"Après ce règlement, {noms[apres.debtor]} devra encore "
            f"{format_euros(apres.amount)} à {noms[apres.creditor]}."
        )
    return render(
        request,
        "accounting/settlement_form.html#apercu",
        {"problem": probleme, "after_sentence": phrase},
    )


@require_http_methods(["GET", "POST"])
def settlement_cancel(request, pk: int):
    """« Annuler le règlement » : un règlement inverse est enregistré, avec un motif (D54)."""
    reglement = get_object_or_404(
        Settlement.objects.select_related("from_partner", "to_partner"), pk=pk
    )
    refus = settlements.cancel_refusal(reglement)
    if refus:
        messages.error(request, refus)
        return redirect("accounting:accounts")
    form = SettlementCancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            settlements.cancel_settlement(reglement, reason=form.cleaned_data["reason"])
        except ValidationError as erreur:
            form.add_error(None, erreur)
        else:
            messages.success(
                request,
                "Le règlement est annulé : un règlement inverse est enregistré."
                + _after_comptes(queries.partner_accounts()),
            )
            return redirect("accounting:accounts")
    return render(
        request, "accounting/settlement_cancel.html", {"settlement": reglement, "form": form}
    )

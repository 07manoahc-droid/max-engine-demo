"""Formulaires des ventes : acompte, vente, annulation d'un acompte ou d'une vente."""

from collections.abc import Sequence

from django import forms
from django.utils import timezone

from accounting.services import (
    ZERO,
    DepositInput,
    Payment,
    amounts_by_partner,
    deposit_receipts_problem,
    receipts_problem,
    sale_left_to_receive,
    sale_price_problem,
    trade_in_problem,
)
from core.formatting import format_euros
from core.forms import (
    ContactChoiceMixin,
    DateInput,
    MoneyField,
    PartnerChoiceMixin,
    StyledFormMixin,
)
from expenses.forms import PayersFormMixin
from vehicles.models import Vehicle

from .models import Deposit, DepositReceipt, Sale


class DepositReceiptsForm(PayersFormMixin, StyledFormMixin, forms.Form):
    """« Ajouter un versement » : montant, date, qui l'a reçu (un associé, « Moitié-moitié »
    ou « Montants différents », comme « Qui a payé ? » d'une dépense, D89).

    Après validation, `payments` contient un `Payment` par associé qui a reçu de l'argent :
    chacun deviendra un versement.
    """

    amount_label = "Reçu par {name}"
    amount_missing = "Indique combien {name} a reçu."

    @staticmethod
    def payments_check(total, paiements):
        return deposit_receipts_problem(total, paiements, "Le versement")

    amount = MoneyField(label="Montant reçu")
    date = forms.DateField(label="Date du versement", widget=DateInput())
    payer = forms.ChoiceField(
        label="Qui a reçu l'argent ?",
        widget=forms.RadioSelect,
        error_messages={"required": "Choisis qui a reçu l'argent."},
    )

    def __init__(self, *args, partners, current_partner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate
        self.setup_payers(partners, current_partner)

    def clean(self):
        donnees = super().clean()
        montant = donnees.get("amount")
        if montant is not None:
            self.clean_payments(montant, "amount")
        return donnees

    def receipts_fields(self) -> dict:
        """Les valeurs pour `add_deposit_receipts`."""
        return {"date": self.cleaned_data["date"], "receipts": self.payments}


class DepositForm(ContactChoiceMixin, DepositReceiptsForm):
    """« Enregistrer un acompte » : montant, date, qui l'a reçu, acheteur.

    Après validation, `contact` contient l'acheteur existant choisi (ou None) et `payments`
    un `Payment` par associé qui a reçu une partie de l'acompte.
    """

    buyer = forms.ChoiceField(label="Acheteur", required=False)
    payments_check = staticmethod(deposit_receipts_problem)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Montant de l'acompte"
        self.fields["date"].label = "Date de l'acompte"
        self.setup_contact(
            "buyer", none_label="Pas de fiche acheteur", new_label="Nouvel acheteur…"
        )

    def clean(self):
        donnees = super().clean()
        self.resolve_contact(donnees)
        return donnees

    def deposit_fields(self) -> dict:
        """Les valeurs pour `record_deposit`, sauf l'acheteur."""
        return self.receipts_fields()


class DepositEditForm(ContactChoiceMixin, StyledFormMixin, forms.Form):
    """« Corriger l'acompte » : la date de la réservation et l'acheteur. Les versements se
    corrigent un par un (`DepositReceiptForm`)."""

    date = forms.DateField(label="Date de la réservation", widget=DateInput())
    buyer = forms.ChoiceField(label="Acheteur", required=False)

    def __init__(self, *args, deposit: Deposit, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_contact(
            "buyer",
            none_label="Pas de fiche acheteur",
            new_label="Nouvel acheteur…",
            initial_pk=deposit.buyer_id,
        )
        self.initial["date"] = deposit.date
        self.style_fields()

    def clean(self):
        donnees = super().clean()
        self.resolve_contact(donnees)
        return donnees


class DepositReceiptForm(PartnerChoiceMixin, StyledFormMixin, forms.Form):
    """« Corriger le versement » : montant, date, qui l'a reçu. Un ancien associé reste
    proposé s'il avait reçu ce versement."""

    amount = MoneyField(label="Montant reçu")
    date = forms.DateField(label="Date du versement", widget=DateInput())
    received_by = forms.ChoiceField(
        label="Qui a reçu l'argent ?",
        widget=forms.RadioSelect,
        error_messages={"required": "Choisis qui a reçu l'argent."},
    )

    def __init__(self, *args, partners, receipt: DepositReceipt, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_partners("received_by", partners, keep=receipt.partner)
        self.initial.update(
            amount=receipt.amount, date=receipt.date, received_by=str(receipt.partner_id)
        )
        self.style_fields()

    def clean_amount(self):
        montant = self.cleaned_data["amount"]
        if montant is not None and montant <= 0:
            raise forms.ValidationError("Le versement doit être plus grand que 0 €.")
        return montant

    def receipt_fields(self) -> dict:
        """Les valeurs pour `update_deposit_receipt`."""
        return {
            "amount": self.cleaned_data["amount"],
            "date": self.cleaned_data["date"],
            "received_by": self.partner(),
        }


class SaleForm(ContactChoiceMixin, PayersFormMixin, StyledFormMixin, forms.Form):
    """« Enregistrer la vente » ou la modifier : prix, date, qui a encaissé, acheteur, lieu.

    Le reste à encaisser dépend de `deposits` et de la reprise ; s'il vaut 0 €, personne
    n'a à encaisser. Après validation, `payments` contient un `Payment` par associé (vide
    dans ce cas), `contact` l'acheteur existant choisi (ou None) et `trade_in_value` la
    valeur de reprise. La reprise se saisit à l'enregistrement, jamais à la modification.
    """

    amount_label = "Encaissé par {name}"
    amount_missing = "Indique combien {name} a encaissé."
    payments_check = staticmethod(receipts_problem)

    price = MoneyField(label="Prix de vente")
    date = forms.DateField(label="Date de la vente", widget=DateInput())
    has_trade_in = forms.BooleanField(
        label="L'acheteur paie en partie avec son véhicule",
        required=False,
        help_text="Son véhicule arrive au garage en « Acheté », payé pour moitié par chacun.",
    )
    # Facultatif pour le formulaire : `clean` l'exige si la case est cochée.
    trade_in_value = MoneyField(
        label="Valeur de reprise",
        required=False,
        help_text="Elle est comprise dans le prix de vente.",
    )
    # Facultatif pour le formulaire : `clean` l'exige s'il reste de l'argent à encaisser.
    payer = forms.ChoiceField(label="Qui a encaissé ?", widget=forms.RadioSelect, required=False)
    buyer = forms.ChoiceField(label="Acheteur", required=False)
    sold_where = forms.CharField(
        label="Où et comment",
        max_length=200,
        required=False,
        help_text="Par exemple : annonce en ligne, remise en main propre.",
    )
    notes = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(
        self,
        *args,
        vehicle: Vehicle,
        partners,
        deposits: Sequence[DepositInput],
        current_partner=None,
        sale: Sale | None = None,
        buyer_pk: int | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.deposits = deposits
        self.active_partner_ids = [associe.pk for associe in partners]
        self.trade_in_value = sale.trade_in_value if sale is not None else ZERO
        if sale is not None:
            del self.fields["has_trade_in"], self.fields["trade_in_value"]
        self.fields["date"].initial = timezone.localdate
        encaissements = []
        if sale is not None:
            encaissements = [
                Payment(e.partner, e.amount) for e in sale.receipts.select_related("partner")
            ]
        # Un ancien associé qui a encaissé cette vente reste proposé.
        self.setup_payers(partners, current_partner, keep=[e.partner for e in encaissements])
        # Avec un autre associé, le formulaire ne sait pas le garder : voir `clean`.
        self.former_partner_shared = (
            bool(self.former_partners) and len(amounts_by_partner(encaissements)) > 1
        )
        self.setup_contact(
            "buyer",
            none_label="Pas de fiche acheteur",
            new_label="Nouvel acheteur…",
            initial_pk=buyer_pk,
        )
        if sale is None:
            if vehicle.sale_price_target is not None:
                self.fields[
                    "price"
                ].help_text = f"Prix affiché : {format_euros(vehicle.sale_price_target)}."
            return
        self.initial.update(
            price=sale.price, date=sale.date, sold_where=sale.sold_where, notes=sale.notes
        )
        if encaissements:
            reste = sale_left_to_receive(sale.price, deposits, sale.trade_in_value)
            self.set_initial_payers(encaissements, reste)

    def clean(self):
        donnees = super().clean()
        self.resolve_contact(donnees)
        if self.former_partner_shared:
            self.add_error(
                None,
                "Un ancien associé a encaissé une partie de cette vente : "
                "préviens Mathis avant de la modifier.",
            )
            return donnees
        self._clean_trade_in(donnees)
        prix = donnees.get("price")
        if prix is None:
            return donnees
        if "trade_in_value" in self.errors:
            # Valeur de reprise illisible : le reste à encaisser n'est pas connu. Un payeur
            # manquant est signalé quand même, pour montrer toutes les erreurs d'un coup.
            sans_reprise = sale_left_to_receive(prix, self.deposits)
            if sans_reprise > 0 and not donnees.get("payer") and "payer" not in self.errors:
                self.add_error("payer", "Choisis qui a encaissé l'argent.")
            return donnees
        reste = sale_left_to_receive(prix, self.deposits, self.trade_in_value)
        probleme = sale_price_problem(prix, reste)
        if probleme:
            self.add_error("price", probleme)
        elif reste > 0 and donnees.get("payer"):
            self.clean_payments(reste, "price")
        elif reste > 0 and "payer" not in self.errors:
            self.add_error("payer", "Choisis qui a encaissé l'argent.")
        return donnees

    def _clean_trade_in(self, donnees: dict) -> None:
        if not donnees.get("has_trade_in"):
            # Case décochée : le champ est masqué, son contenu ne doit pas bloquer la vente.
            self.errors.pop("trade_in_value", None)
            return
        if "trade_in_value" in self.errors:
            return
        valeur = donnees.get("trade_in_value")
        if valeur is None:
            self.add_error("trade_in_value", "Indique la valeur de reprise.")
            return
        probleme = trade_in_problem(valeur, self.active_partner_ids)
        if probleme:
            self.add_error("trade_in_value", probleme)
            return
        self.trade_in_value = valeur

    def sale_fields(self) -> dict:
        """Les valeurs pour `record_sale` / `update_sale`, sauf l'acheteur."""
        return {
            "price": self.cleaned_data["price"],
            "date": self.cleaned_data["date"],
            "sold_where": self.cleaned_data["sold_where"],
            "notes": self.cleaned_data["notes"],
            "receipts": self.payments,
        }


class DepositCancelForm(StyledFormMixin, forms.Form):
    """« Annuler l'acompte » : rendu ou conservé, sans choix d'avance (voir les explications
    de l'écran : le choix change les comptes). Sert aussi à l'annulation d'une vente, pour
    l'acompte qui en avait été déduit."""

    outcome = forms.ChoiceField(
        label="Qu'est devenu l'argent ?",
        choices=[
            (Deposit.Status.REFUNDED.value, "Rendu à l'acheteur"),
            (Deposit.Status.KEPT.value, "Conservé par le garage"),
        ],
        widget=forms.RadioSelect,
        error_messages={"required": "Indique si l'acompte a été rendu ou conservé."},
    )

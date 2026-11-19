"""Formulaires des comptes : enregistrer et annuler un règlement entre associés."""

from django import forms
from django.utils import timezone

from core.forms import DateInput, MoneyField, StyledFormMixin

from .settlements import date_problem


class SettlementForm(StyledFormMixin, forms.Form):
    """« Enregistrer le règlement » : montant, date, note.

    Le sens n'est pas un choix (D53) : c'est celui du solde à l'ouverture de la page. Le
    sens et le montant dû voyagent dans des champs cachés, pour que le service refuse le
    règlement si le solde a changé entre-temps (autre onglet, même pour un règlement
    partiel).
    """

    amount = MoneyField(
        label="Montant versé",
        help_text="Tout ce qui est dû, ou une partie.",
        error_messages={"required": "Indique le montant versé."},
    )
    date = forms.DateField(label="Date du règlement", widget=DateInput())
    notes = forms.CharField(
        label="Note",
        required=False,
        help_text="Par exemple : virement, espèces.",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    from_partner = forms.IntegerField(widget=forms.HiddenInput)
    to_partner = forms.IntegerField(widget=forms.HiddenInput)
    seen_due = MoneyField(widget=forms.HiddenInput)

    def __init__(self, *args, due, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate
        # Le calendrier du navigateur grise les jours à venir ; `clean_date` les refuse.
        self.fields["date"].widget.attrs["max"] = timezone.localdate().isoformat()
        self.initial.update(
            amount=due.amount,
            from_partner=due.debtor,
            to_partner=due.creditor,
            seen_due=due.amount,
        )

    def clean_date(self):
        date = self.cleaned_data["date"]
        probleme = date_problem(date)
        if probleme:
            raise forms.ValidationError(probleme)
        return date

    def clean(self):
        donnees = super().clean()
        # Les champs cachés n'affichent pas leurs erreurs : on le dit en haut du formulaire.
        if any(nom in self.errors for nom in ("from_partner", "to_partner", "seen_due")):
            self.add_error(None, "Cette page n'est plus à jour. Recharge-la.")
        return donnees


class SettlementCancelForm(StyledFormMixin, forms.Form):
    """« Annuler le règlement » : le motif est obligatoire (D54)."""

    reason = forms.CharField(
        label="Pourquoi annuler ce règlement ?",
        help_text="Par exemple : montant saisi en double, virement pas encore fait.",
        widget=forms.Textarea(attrs={"rows": 3}),
        error_messages={"required": "Indique pourquoi tu annules ce règlement."},
    )

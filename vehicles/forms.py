"""Formulaires des véhicules : ajout (court), modification de la fiche, achat."""

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.formatting import format_euros
from core.forms import (
    ContactChoiceMixin,
    DateInput,
    HttpsURLField,
    MoneyField,
    StyledFormMixin,
    contacts_or_current,
)
from core.models import PurchaseOrigin, VehicleType
from expenses.forms import PayersFormMixin

from .models import Vehicle

YEAR_MIN = 1900


def _active_or_current(queryset, current_pk):
    """Les valeurs actives d'une liste, plus la valeur actuelle même si elle a été désactivée."""
    condition = Q(is_active=True)
    if current_pk is not None:
        condition |= Q(pk=current_pk)
    return queryset.filter(condition).order_by("order", "name")


class VehicleForm(StyledFormMixin, forms.ModelForm):
    """Fiche complète d'un véhicule. Le statut et la date d'achat ne s'y changent pas :
    ils passent par « Changer le statut » et « Enregistrer l'achat »."""

    # Rubriques affichées par le gabarit, dans l'ordre.
    groups = (
        (
            "Le véhicule",
            (
                "vehicle_type",
                "brand",
                "model",
                "year",
                "displacement_cc",
                "color",
                "mileage_km",
                "vin",
                "registration",
            ),
        ),
        ("Achat", ("origin", "seller", "listing_url", "asking_price", "location")),
        ("Vente", ("sale_price_target", "listed_on")),
        ("Notes", ("notes",)),
    )

    class Meta:
        model = Vehicle
        fields = [
            "vehicle_type",
            "brand",
            "model",
            "year",
            "displacement_cc",
            "color",
            "mileage_km",
            "vin",
            "registration",
            "origin",
            "seller",
            "listing_url",
            "asking_price",
            "location",
            "sale_price_target",
            "listed_on",
            "notes",
        ]
        field_classes = {
            "asking_price": MoneyField,
            "sale_price_target": MoneyField,
            "listing_url": HttpsURLField,
        }
        widgets = {
            "year": forms.NumberInput(attrs={"inputmode": "numeric"}),
            "listing_url": forms.URLInput(attrs={"placeholder": "https://"}),
            "listed_on": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "listing_url": "Colle ici le lien de l'annonce.",
            "asking_price": "Le prix demandé par le vendeur dans l'annonce.",
            "vin": "Les 17 caractères du numéro de série, s'il est connu.",
            "sale_price_target": "Le prix affiché dans ton annonce de vente.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type_actuel = self.instance.vehicle_type_id
        self.fields["vehicle_type"].queryset = _active_or_current(
            VehicleType.objects.all(), type_actuel
        )
        self.fields["vehicle_type"].empty_label = None
        if "origin" in self.fields:
            self.fields["origin"].queryset = _active_or_current(
                PurchaseOrigin.objects.all(), self.instance.origin_id
            )
            self.fields["origin"].empty_label = "Non renseignée"
        if "seller" in self.fields:
            self.fields["seller"].queryset = contacts_or_current(self.instance.seller_id)
            self.fields["seller"].empty_label = "Aucun"

    def clean_year(self):
        annee = self.cleaned_data.get("year")
        maximum = timezone.localdate().year + 1
        if annee is not None and not YEAR_MIN <= annee <= maximum:
            raise forms.ValidationError(f"Indique une année entre {YEAR_MIN} et {maximum}.")
        return annee

    def grouped_fields(self):
        """Les rubriques du gabarit, chacune avec ses champs présents dans ce formulaire."""
        rubriques = []
        for titre, noms in self.groups:
            champs = [self[nom] for nom in noms if nom in self.fields]
            if champs:
                rubriques.append((titre, champs))
        return rubriques


class VehicleCreateForm(VehicleForm):
    """« Ajouter un véhicule » : le strict nécessaire, le reste se complète plus tard."""

    groups = (
        (
            "",
            (
                "vehicle_type",
                "brand",
                "model",
                "year",
                "origin",
                "listing_url",
                "asking_price",
                "location",
            ),
        ),
    )

    class Meta(VehicleForm.Meta):
        fields = [
            "vehicle_type",
            "brand",
            "model",
            "year",
            "origin",
            "listing_url",
            "asking_price",
            "location",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Le premier type de la liste (Moto) est proposé d'office.
        premier = self.fields["vehicle_type"].queryset.first()
        if premier is not None and not self.is_bound:
            self.initial.setdefault("vehicle_type", premier.pk)


class TradeInVehicleForm(VehicleCreateForm):
    """Le véhicule repris, saisi dans la vente : le reste se complète ensuite sur sa fiche."""

    class Meta(VehicleCreateForm.Meta):
        fields = ["vehicle_type", "brand", "model", "year"]


def trade_in_vehicle_form(data) -> tuple[TradeInVehicleForm, bool]:
    """Le formulaire du véhicule repris, et s'il doit être lu : ses champs ne comptent que si
    la case de la reprise est cochée.

    Case décochée, le formulaire n'est pas lu (aucune erreur), mais ce qui a été tapé reste
    affiché : après un refus de la vente, on le retrouve en recochant la case.
    """
    repris = data is not None and bool(data.get("has_trade_in"))
    if repris:
        return TradeInVehicleForm(data, prefix="reprise"), True
    vide = TradeInVehicleForm(prefix="reprise")
    if data is None:
        return vide, False
    noms = {nom: vide.add_prefix(nom) for nom in vide.fields}
    tape = {nom: data[cle] for nom, cle in noms.items() if data.get(cle)}
    return TradeInVehicleForm(initial=tape, prefix="reprise"), False


class ReopenForm(StyledFormMixin, forms.Form):
    """« Rouvrir la fiche » d'un véhicule vendu : le motif est obligatoire (D22)."""

    reason = forms.CharField(
        label="Pourquoi rouvres-tu la fiche ?",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Par exemple : facture oubliée, erreur de prix.",
        error_messages={"required": "Indique pourquoi tu rouvres la fiche."},
    )


class PurchaseForm(ContactChoiceMixin, PayersFormMixin, StyledFormMixin, forms.Form):
    """« Enregistrer l'achat » : prix, date, qui a payé, vendeur.

    Après validation, `payments` contient un `Payment` par payeur (clé : le `Partner`) et
    `contact` le vendeur existant choisi (ou None).
    """

    price = MoneyField(label="Prix payé")
    date = forms.DateField(label="Date de l'achat", widget=DateInput())
    payer = forms.ChoiceField(label="Qui a payé ?", widget=forms.RadioSelect)
    seller = forms.ChoiceField(label="Vendeur", required=False)

    def __init__(self, *args, vehicle: Vehicle, partners, current_partner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate
        if vehicle.asking_price is not None:
            self.fields[
                "price"
            ].help_text = f"Prix demandé dans l'annonce : {format_euros(vehicle.asking_price)}."
        self.setup_payers(partners, current_partner)
        self.setup_contact(
            "seller",
            none_label="Pas de fiche vendeur",
            new_label="Nouveau vendeur…",
            initial_pk=vehicle.seller_id,
        )

    def clean(self):
        donnees = super().clean()
        self.resolve_contact(donnees)
        prix = donnees.get("price")
        if prix is not None:
            self.clean_payments(prix, "price")
        return donnees

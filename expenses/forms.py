"""Formulaires des dépenses : le paiement (avec le bloc « Qui a payé ? », partagé avec
l'achat d'un véhicule), ses lignes, les retours et les transferts de pièce."""

from collections.abc import Sequence
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from accounting.services import (
    LineInput,
    Payment,
    amounts_by_partner,
    lines_sum,
    payment_problem,
    split_equally,
)
from core.formatting import parse_id
from core.forms import DateInput, MoneyField, PartnerChoiceMixin, StyledFormMixin
from core.models import ExpenseCategory, Partner, Supplier
from vehicles.models import Vehicle
from vehicles.services import garage_vehicles, vehicles_open_to_expenses

from .models import Expense, ExpenseLine, Refund
from .services import PERSONAL_WITH_VEHICLE, LineEntry, line_vehicle_refusal, transfer_targets

# Champ texte sans les suggestions du navigateur : Edge proposait d'anciennes saisies, et
# pouvait remplir la référence avec une désignation.
NO_AUTOFILL = forms.TextInput(attrs={"autocomplete": "off"})

# Choix spéciaux de « Qui a payé ? » (les autres valeurs sont des identifiants d'associé).
PAID_HALF = "moitie"
PAID_CUSTOM = "montants"


class PayersFormMixin:
    """Bloc « Qui a payé ? » : un associé, « Moitié-moitié » ou « Montants différents »
    (un champ « Payé par … » par associé).

    Le formulaire déclare un champ `payer` (choix en boutons radio), appelle
    `setup_payers` dans son `__init__`, puis `clean_payments` une fois le total connu.
    Ensuite, `payments` contient un `Payment` par payeur (clé : le `Partner`).

    La vente réutilise ce bloc pour « Qui a encaissé ? » : elle remplace les trois attributs
    ci-dessous, qui portent le vocabulaire et la vérification.
    """

    amount_label = "Payé par {name}"
    amount_missing = "Indique combien {name} a payé."
    payments_check = staticmethod(payment_problem)

    def setup_payers(self, partners, current_partner=None, keep: Sequence[Partner] = ()) -> None:
        """`keep` : anciens associés déjà enregistrés. Ils restent proposés un par un, mais
        n'entrent ni dans « Moitié-moitié » ni dans « Montants différents »."""
        self.partners: list[Partner] = list(partners)
        self.payments: list[Payment] = []
        self.former_partners = [a for a in dict.fromkeys(keep) if a not in self.partners]
        choix = [
            (str(associe.pk), associe.name) for associe in [*self.partners, *self.former_partners]
        ]
        if len(self.partners) > 1:
            choix += [(PAID_HALF, "Moitié-moitié"), (PAID_CUSTOM, "Montants différents")]
        self.fields["payer"].choices = choix
        if current_partner in self.partners:
            self.fields["payer"].initial = str(current_partner.pk)
        # Facultatifs pour le formulaire, mais exigés avec « Montants différents ».
        for associe in self.partners:
            self.fields[self.amount_field(associe)] = MoneyField(
                label=self.amount_label.format(name=associe.name), required=False
            )
        self.style_fields()

    def set_initial_payers(self, payments: Sequence[Payment], total: Decimal) -> None:
        """Préremplit le choix d'après les paiements d'une dépense déjà enregistrée."""
        par_associe = amounts_by_partner(payments)
        if len(par_associe) == 1:
            (associe,) = par_associe
            self.initial["payer"] = str(associe.pk)
        elif len(self.partners) > 1 and par_associe == split_equally(total, self.partners):
            self.initial["payer"] = PAID_HALF
        else:
            self.initial["payer"] = PAID_CUSTOM
            for associe, montant in par_associe.items():
                self.initial[self.amount_field(associe)] = montant

    @staticmethod
    def amount_field(partner: Partner) -> str:
        return f"amount_{partner.pk}"

    def amount_fields(self):
        """Les champs « Payé par … » (affichés pour « Montants différents »)."""
        return [self[self.amount_field(associe)] for associe in self.partners]

    def payer_options(self):
        """Les choix de payeur pour le gabarit : (valeur, libellé, associé ou None)."""
        associes = {str(a.pk): a for a in [*self.partners, *self.former_partners]}
        return [
            (valeur, libelle, associes.get(valeur))
            for valeur, libelle in self.fields["payer"].choices
        ]

    def clean_payments(self, total: Decimal, total_field: str) -> None:
        """Calcule `payments` pour une dépense de `total`, ou ajoute l'erreur sous le bon
        champ (`total_field` : celui du montant)."""
        payeur = self.cleaned_data.get("payer")
        if not payeur:
            return
        if payeur == PAID_HALF:
            paiements = [Payment(a, m) for a, m in split_equally(total, self.partners).items()]
        elif payeur == PAID_CUSTOM:
            paiements = []
            for associe in self.partners:
                montant = self.cleaned_data.get(self.amount_field(associe))
                if montant is None:
                    self.add_error(
                        self.amount_field(associe), self.amount_missing.format(name=associe.name)
                    )
                else:
                    paiements.append(Payment(associe, montant))
            if len(paiements) != len(self.partners):
                return
        else:
            associes = [*self.partners, *self.former_partners]
            associe = next(a for a in associes if str(a.pk) == payeur)
            paiements = [Payment(associe, total)]

        probleme = self.payments_check(total, paiements)
        if probleme:
            self.add_error(self._field_for(total, payeur, paiements, total_field), probleme)
        else:
            self.payments = paiements

    def _field_for(self, total, payeur: str, paiements: list[Payment], total_field: str) -> str:
        """Le champ sous lequel afficher un problème de paiement."""
        if total <= 0:
            return total_field
        if payeur == PAID_CUSTOM:
            nuls = [p.partner for p in paiements if p.amount <= 0]
            if len(nuls) == 1:
                return self.amount_field(nuls[0])
        return "payer"


class ExpenseForm(PayersFormMixin, StyledFormMixin, forms.Form):
    """Le paiement d'une dépense : date, fournisseur, montant payé, payeurs, achat personnel.

    Les lignes sont dans `ExpenseLineFormSet`. Une fois les deux valides, `complete(lignes)`
    fixe `total` (la somme des lignes si le montant payé est laissé vide) et `payments`.

    `trade_in_purchase` : la dépense est l'achat d'un véhicule repris par une vente en
    cours (D51). Le montant et les payeurs sont alors grisés : désactivés, ils gardent leur
    valeur enregistrée, quoi que le navigateur envoie.
    """

    date = forms.DateField(label="Date du paiement", widget=DateInput())
    supplier_name = forms.CharField(
        label="Fournisseur",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"list": "fournisseurs", "autocomplete": "off"}),
        help_text="Choisis-le dans la liste ou tape un nouveau nom.",
    )
    order_number = forms.CharField(
        label="N° de commande", max_length=60, required=False, widget=NO_AUTOFILL
    )
    total_paid = MoneyField(
        label="Montant payé",
        required=False,
        help_text="Frais de port compris. Laisse vide s'il n'y en a pas : ce sera la somme "
        "des lignes.",
    )
    payer = forms.ChoiceField(label="Qui a payé ?", widget=forms.RadioSelect)
    is_personal = forms.BooleanField(
        label="Achat personnel",
        required=False,
        help_text="Gardé pour mémoire, n'entre dans aucun calcul. Seulement pour une dépense "
        "sans véhicule.",
    )
    notes = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(
        self,
        *args,
        partners,
        current_partner=None,
        expense: Expense | None = None,
        trade_in_purchase: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.total: Decimal | None = None
        self.fields["date"].initial = timezone.localdate
        self.setup_payers(partners, current_partner)
        if trade_in_purchase:
            for nom in ["total_paid", "payer", *(self.amount_field(a) for a in self.partners)]:
                self.fields[nom].disabled = True
        if expense is not None:
            # Sans frais de port, le montant reste vide : il suivra le prix des lignes si on
            # le corrige (D42), au lieu de laisser un écart fantôme.
            somme = lines_sum(
                LineInput(ligne.quantity, ligne.unit_price) for ligne in expense.lines.all()
            )
            self.initial.update(
                date=expense.date,
                supplier_name=expense.supplier.name if expense.supplier else "",
                order_number=expense.order_number,
                total_paid=None if expense.total_paid == somme else expense.total_paid,
                is_personal=expense.is_personal,
                notes=expense.notes,
            )
            paiements = [
                Payment(p.partner, p.amount) for p in expense.payments.select_related("partner")
            ]
            self.set_initial_payers(paiements, expense.total_paid)

    def suppliers(self) -> list[str]:
        """Les fournisseurs connus, proposés pendant la saisie."""
        return list(Supplier.objects.values_list("name", flat=True))

    def complete(self, lines: Sequence[LineEntry]) -> None:
        """Une fois les lignes valides : montant payé, payeurs et achat personnel."""
        total = self.cleaned_data.get("total_paid")
        if total is None:
            total = lines_sum([entree.as_input() for entree in lines])
        self.total = total
        self.clean_payments(total, "total_paid")
        if self.cleaned_data.get("is_personal") and any(entree.vehicle for entree in lines):
            self.add_error("is_personal", PERSONAL_WITH_VEHICLE)


# « À commander » vient d'une pièce pas encore payée, « Retourné » d'un retour : on ne les
# choisit pas dans une dépense.
_MANUAL_EXCLUDED = (ExpenseLine.Status.TO_ORDER, ExpenseLine.Status.RETURNED)


QUANTITY_ERRORS = {
    "invalid": "Indique une quantité, par exemple 2 ou 1,5.",
    "max_decimal_places": "Indique au plus 2 chiffres après la virgule.",
}


def _positive_quantity(quantite: Decimal) -> Decimal:
    if quantite <= 0:
        raise forms.ValidationError("Indique une quantité plus grande que 0.")
    return quantite


class VehicleChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: Vehicle) -> str:
        if obj.deleted_at is not None:
            return f"{obj.display_name} (à la corbeille)"
        return obj.display_name


class ExpenseLineForm(StyledFormMixin, forms.Form):
    """Une ligne : désignation, référence, quantité, prix, catégorie, véhicule, statut.

    « Achat véhicule » n'est pas proposé (l'achat a son propre formulaire). La ligne
    d'achat d'une dépense déjà enregistrée garde sa catégorie et son véhicule.
    """

    line_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    label = forms.CharField(label="Désignation", max_length=200, widget=NO_AUTOFILL)
    reference = forms.CharField(
        label="Référence", max_length=80, required=False, widget=NO_AUTOFILL
    )
    quantity = forms.DecimalField(
        label="Quantité",
        max_digits=8,
        decimal_places=2,
        initial=Decimal("1"),
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
    unit_price = MoneyField(label="Prix à l'unité")
    category = forms.ModelChoiceField(
        label="Catégorie", queryset=ExpenseCategory.objects.none(), empty_label=None
    )
    vehicle = VehicleChoiceField(
        label="Véhicule",
        queryset=Vehicle.objects.none(),
        required=False,
        empty_label="Garage (aucun véhicule)",
    )
    status = forms.ChoiceField(label="Statut de la pièce", required=False)

    def __init__(
        self,
        *args,
        default_vehicle: Vehicle | None = None,
        expense_vehicles: Sequence[int] = (),
        trade_in_purchase: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].error_messages.update(QUANTITY_ERRORS)
        self.fields["vehicle"].error_messages["invalid_choice"] = (
            "Ce véhicule n'existe plus. Rien n'est enregistré : choisis où mettre cette ligne."
        )

        categorie = self.initial.get("category")
        statut = self.initial.get("status")
        categories = ExpenseCategory.objects.filter(is_active=True).exclude(
            code=ExpenseCategory.VEHICLE_PURCHASE
        )
        if categorie is not None:
            categories = ExpenseCategory.objects.filter(
                Q(pk__in=categories.values("pk")) | Q(pk=categorie)
            )
        self.fields["category"].queryset = categories.order_by("order", "name")
        # Le statut n'est affiché que pour les pièces (gabarit : variable Alpine `categorie`).
        self.fields["category"].widget.attrs["x-model"] = "categorie"
        pieces = ExpenseCategory.objects.filter(code=ExpenseCategory.PARTS).first()
        if categorie is None and pieces is not None:
            self.fields["category"].initial = pieces.pk
        self.parts_category_id = pieces.pk if pieces else None

        # Les véhicules au garage ou rouverts, plus ceux de la dépense et celui de la fiche d'où
        # l'on vient (un véhicule abandonné, gardé pour ses pièces, n'est pas proposé à toutes
        # les dépenses). Sans eux, une ligne ajoutée à une dépense partirait sur le garage : le
        # coût du véhicule serait faux, sans aucune alerte.
        vehicules = Q(pk__in=vehicles_open_to_expenses().values("pk")) | Q(pk__in=expense_vehicles)
        if default_vehicle is not None:
            vehicules |= Q(pk=default_vehicle.pk)
        self.proposed_vehicles = Vehicle.objects.filter(vehicules)
        # Le véhicule envoyé reste à l'écran même s'il a quitté la liste (`clean_vehicle` le
        # refuse) : sans lui, le navigateur retomberait sur « Garage », et un second clic y
        # mettrait la ligne sans que personne l'ait choisi.
        envoye = parse_id(self.data.get(self.add_prefix("vehicle"), ""))
        # Une ligne déjà posée sur un véhicule à la corbeille l'affiche « (à la corbeille) » au
        # lieu de passer en silence sur « Garage » ; l'enregistrement le refuse (D77).
        self.fields["vehicle"].queryset = (
            self.proposed_vehicles
            | Vehicle.all_objects.filter(pk=envoye)
            | Vehicle.all_objects.filter(pk__in=expense_vehicles)
        ).order_by("number")
        if default_vehicle is not None:
            self.fields["vehicle"].initial = default_vehicle.pk

        choix = [(s.value, s.label) for s in ExpenseLine.Status if s not in _MANUAL_EXCLUDED]
        if statut and statut not in dict(choix):
            choix.append((statut, ExpenseLine.Status(statut).label))
        self.fields["status"].choices = choix
        self.fields["status"].initial = ExpenseLine.Status.ORDERED
        # « Retourné » suit le remboursement (D45) : affiché, jamais modifié ici. La catégorie
        # est verrouillée aussi : quitter « Pièces » effacerait le statut (D46).
        self.status_read_only = statut == ExpenseLine.Status.RETURNED
        if self.status_read_only:
            self.fields["status"].disabled = True
            self.fields["category"].disabled = True

        # La ligne d'achat d'un véhicule : ni sa catégorie ni son véhicule ne changent ici.
        self.is_purchase = (
            categorie is not None
            and ExpenseCategory.objects.filter(
                pk=categorie, code=ExpenseCategory.VEHICLE_PURCHASE
            ).exists()
        )
        if self.is_purchase:
            self.fields["category"].disabled = True
            self.fields["vehicle"].disabled = True
        # L'achat d'un véhicule repris garde aussi son prix (D51) : la reprise reste neutre.
        if self.is_purchase and trade_in_purchase:
            self.fields["quantity"].disabled = True
            self.fields["unit_price"].disabled = True

    def clean_quantity(self):
        return _positive_quantity(self.cleaned_data["quantity"])

    def clean_vehicle(self):
        vehicule = self.cleaned_data["vehicle"]
        if vehicule is None or self.proposed_vehicles.filter(pk=vehicule.pk).exists():
            return vehicule
        raise forms.ValidationError(line_vehicle_refusal(vehicule))

    def entry(self) -> LineEntry:
        donnees = self.cleaned_data
        return LineEntry(
            label=donnees["label"],
            reference=donnees["reference"],
            quantity=donnees["quantity"],
            unit_price=donnees["unit_price"],
            category=donnees["category"],
            vehicle=donnees["vehicle"],
            status=donnees["status"],
            pk=donnees["line_id"],
        )


class BaseExpenseLineFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if not any(self.errors) and not self.entries():
            raise forms.ValidationError("Ajoute au moins une ligne.")

    def entries(self) -> list[LineEntry]:
        """Les lignes remplies et gardées (ni vides, ni retirées)."""
        return [
            form.entry()
            for form in self.forms
            if form.cleaned_data and not self._should_delete_form(form)
        ]


ExpenseLineFormSet = forms.formset_factory(
    ExpenseLineForm, formset=BaseExpenseLineFormSet, extra=0, can_delete=True
)


def _initial_line(ligne: ExpenseLine, **valeurs) -> dict:
    return {
        "line_id": ligne.pk,
        "label": ligne.label,
        "reference": ligne.reference,
        "quantity": ligne.quantity,
        "unit_price": ligne.unit_price,
        "category": ligne.category_id,
        "vehicle": ligne.vehicle_id,
        "status": ligne.status,
        **valeurs,
    }


def line_formset(
    data,
    *,
    expense: Expense | None,
    default_vehicle: Vehicle | None = None,
    parts: Sequence[ExpenseLine] = (),
    trade_in_purchase: bool = False,
):
    """Les lignes du formulaire : celles de la dépense, les pièces à commander choisies
    (`parts`, qui passeront en « Commandé »), ou une ligne vide à remplir.
    `trade_in_purchase` : voir `ExpenseForm`."""
    if expense is not None:
        initiales = [_initial_line(ligne) for ligne in expense.lines.order_by("id")]
    else:
        initiales = [_initial_line(ligne, status=ExpenseLine.Status.ORDERED) for ligne in parts]
    vehicules = {ligne["vehicle"] for ligne in initiales if ligne["vehicle"] is not None}
    formset = ExpenseLineFormSet(
        data,
        prefix="lignes",
        initial=initiales,
        form_kwargs={
            "default_vehicle": default_vehicle,
            "expense_vehicles": sorted(vehicules),
            "trade_in_purchase": trade_in_purchase,
        },
    )
    # Une nouvelle dépense s'ouvre sur une ligne vide, ignorée si on n'y touche pas.
    formset.extra = 0 if initiales else 1
    return formset


class PartToOrderForm(StyledFormMixin, forms.Form):
    """Une pièce à commander : désignation, référence, quantité, prix estimé, véhicule."""

    label = forms.CharField(label="Désignation", max_length=200, widget=NO_AUTOFILL)
    reference = forms.CharField(
        label="Référence", max_length=80, required=False, widget=NO_AUTOFILL
    )
    quantity = forms.DecimalField(
        label="Quantité",
        max_digits=8,
        decimal_places=2,
        initial=Decimal("1"),
        localize=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
    unit_price = MoneyField(
        label="Prix estimé à l'unité",
        required=False,
        help_text="Si tu le connais déjà. Le vrai prix se saisit à la commande.",
    )
    vehicle = VehicleChoiceField(
        label="Véhicule",
        queryset=Vehicle.objects.none(),
        required=False,
        empty_label="Garage (aucun véhicule)",
    )

    def __init__(self, *args, line: ExpenseLine | None = None, default_vehicle=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].error_messages.update(QUANTITY_ERRORS)
        vehicules = Q(pk__in=garage_vehicles().values("pk"))
        for vehicule in (default_vehicle, line.vehicle if line else None):
            if vehicule is not None:
                vehicules |= Q(pk=vehicule.pk)
        self.fields["vehicle"].queryset = Vehicle.objects.filter(vehicules).order_by("number")
        if default_vehicle is not None:
            self.fields["vehicle"].initial = default_vehicle.pk
        if line is not None:
            self.initial.update(
                label=line.label,
                reference=line.reference,
                quantity=line.quantity,
                unit_price=line.unit_price or None,
                vehicle=line.vehicle_id,
            )

    def clean_quantity(self):
        return _positive_quantity(self.cleaned_data["quantity"])

    def has_changed(self) -> bool:
        """Dans le formulaire à plusieurs lignes, une ligne sans désignation, référence ni
        prix est vide : elle est ignorée, même si elle a repris le véhicule de la précédente."""
        return any(
            str(self.data.get(self.add_prefix(nom), "")).strip()
            for nom in ("label", "reference", "unit_price")
        )

    def part_fields(self) -> dict:
        """Les valeurs pour `add_part_to_order` / `update_part_to_order`."""
        donnees = self.cleaned_data
        return {
            "label": donnees["label"],
            "reference": donnees["reference"],
            "quantity": donnees["quantity"],
            "unit_price": donnees["unit_price"] or Decimal("0"),
            "vehicle": donnees["vehicle"],
        }


class BasePartsToOrderFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if not any(self.errors) and not self.parts():
            raise forms.ValidationError("Indique au moins une pièce.")

    def parts(self) -> list[dict]:
        """Les pièces remplies et gardées (ni vides, ni retirées)."""
        return [
            form.part_fields()
            for form in self.forms
            if form.cleaned_data and not self._should_delete_form(form)
        ]


PartsToOrderFormSet = forms.formset_factory(
    PartToOrderForm, formset=BasePartsToOrderFormSet, extra=1, can_delete=True
)


def parts_to_order_formset(data, *, default_vehicle: Vehicle | None = None):
    """« Ajouter des pièces à commander » : une ligne vide, d'autres ajoutées à la demande."""
    return PartsToOrderFormSet(
        data, prefix="lignes", form_kwargs={"default_vehicle": default_vehicle}
    )


class RefundForm(PartnerChoiceMixin, StyledFormMixin, forms.Form):
    """« Enregistrer un retour » : date, montant remboursé, à qui, notes.

    Le montant est prérempli avec ce qui reste à rembourser ; « Remboursé à » avec le
    payeur s'il est seul, sinon rien n'est choisi d'avance.
    """

    date = forms.DateField(label="Date du remboursement", widget=DateInput())
    amount = MoneyField(
        label="Montant remboursé",
        help_text="Mets 0 si tu ne le connais pas encore : il sera marqué « à préciser ».",
    )
    refunded_to = forms.ChoiceField(
        label="Remboursé à",
        widget=forms.RadioSelect,
        help_text="Choisis qui a reçu l'argent.",
        error_messages={"required": "Choisis qui a reçu l'argent."},
    )
    notes = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(
        self,
        *args,
        partners,
        refund: Refund | None = None,
        amount_left: Decimal = Decimal("0"),
        default_partner: Partner | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # L'associé d'un remboursement déjà enregistré reste proposé, même s'il est parti.
        self.setup_partners("refunded_to", partners, keep=refund.refunded_to if refund else None)
        self.fields["date"].initial = timezone.localdate
        if refund is not None:
            self.initial.update(
                date=refund.date,
                # « Préciser le montant » d'un remboursement à 0,00 € : le reste est proposé.
                amount=refund.amount or amount_left,
                refunded_to=str(refund.refunded_to_id),
                notes=refund.notes,
            )
        else:
            self.initial["amount"] = amount_left
            if default_partner in self.partners:
                self.initial["refunded_to"] = str(default_partner.pk)
        # L'aide ne sert que si personne n'est choisi d'avance (deux payeurs).
        if self.initial.get("refunded_to"):
            self.fields["refunded_to"].help_text = ""


class TransferForm(StyledFormMixin, forms.Form):
    """« Transférer sur un autre véhicule » (D90) : le véhicule qui reçoit toute la ligne,
    la date et une note. Le véhicule de départ voyage caché : si la ligne en a changé
    pendant que la page restait ouverte, rien n'est enregistré."""

    to_vehicle = VehicleChoiceField(
        queryset=Vehicle.objects.none(),
        label="Vers quel véhicule ?",
        empty_label="Choisis un véhicule",
        error_messages={
            "required": "Choisis le véhicule qui reçoit la pièce.",
            "invalid_choice": "Ce véhicule n'existe plus. Recharge la page.",
        },
    )
    date = forms.DateField(label="Date du transfert", widget=DateInput())
    notes = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    from_vehicle = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, line: ExpenseLine, **kwargs):
        super().__init__(*args, **kwargs)
        self.line = line
        self.proposed_vehicles = transfer_targets(line)
        # Le véhicule envoyé reste reconnu s'il a quitté la liste pendant que la page
        # restait ouverte : `clean_to_vehicle` dit alors pourquoi (D50).
        envoye = parse_id(self.data.get(self.add_prefix("to_vehicle"), ""))
        self.fields["to_vehicle"].queryset = (
            self.proposed_vehicles | Vehicle.all_objects.filter(pk=envoye)
        ).order_by("number")
        self.fields["date"].initial = timezone.localdate
        self.fields["from_vehicle"].initial = line.vehicle_id

    def clean_to_vehicle(self):
        vehicule = self.cleaned_data["to_vehicle"]
        # Le véhicule où la ligne est arrivée depuis un autre onglet : `transfer_line` dit
        # que la page est restée ouverte, plutôt qu'un faux « abandonné ».
        if vehicule.pk == self.line.vehicle_id:
            return vehicule
        if self.proposed_vehicles.filter(pk=vehicule.pk).exists():
            return vehicule
        raise forms.ValidationError(line_vehicle_refusal(vehicule))

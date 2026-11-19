"""Champs et mise en forme communs à tous les formulaires.

Les classes `field-input` (champ) sont définies dans `styles/input.css`.
"""

from decimal import Decimal

from django import forms
from django.db.models import Q

from .models import Contact

# Espaces qu'on peut taper ou coller dans un montant : « 1 500 », « 1 500 » (insécable).
_SPACES = (" ", " ", " ")


class MoneyField(forms.DecimalField):
    """Montant en euros saisi à la française : « 1500,50 », « 1 500,50 », « 1500.50 € ».

    Toujours au centime et jamais négatif ; le montant est rendu en `Decimal` exact.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 10)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        kwargs.setdefault("localize", True)
        # Pas de suggestions du navigateur sur un montant (anciennes saisies).
        kwargs.setdefault(
            "widget", forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"})
        )
        super().__init__(**kwargs)

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace("€", "")
            for espace in _SPACES:
                value = value.replace(espace, "")
        return super().to_python(value)


class HttpsURLField(forms.URLField):
    """Lien d'annonce ou de profil : « leboncoin.fr/… » devient « https://leboncoin.fr/… »."""

    def __init__(self, **kwargs):
        kwargs.setdefault("assume_scheme", "https")
        super().__init__(**kwargs)


class DateInput(forms.DateInput):
    """Sélecteur de date du navigateur (affiché au format 27/10/2026 par Edge)."""

    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


# Messages d'erreur tutoyés, à la place de ceux de Django (« Saisissez un nombre. »).
# Les clés sont les codes d'erreur : Django prend le message du champ qui porte le code de
# l'erreur, y compris pour les validateurs (max_digits, min_value…).
FRIENDLY_ERRORS = {
    forms.DecimalField: {
        "invalid": "Indique un montant, par exemple 1 500,50.",
        "max_decimal_places": "Indique au plus 2 chiffres après la virgule.",
        "max_digits": "Ce montant est trop grand.",
        "max_whole_digits": "Ce montant est trop grand.",
        "min_value": "Le montant ne peut pas être négatif.",
    },
    forms.IntegerField: {
        "invalid": "Indique un nombre entier, sans espace ni virgule.",
        "min_value": "Indique un nombre d'au moins %(limit_value)s.",
        "max_value": "Indique un nombre d'au plus %(limit_value)s.",
    },
    forms.URLField: {
        "invalid": "Indique une adresse internet complète, par exemple https://www.leboncoin.fr/…",
    },
    forms.DateField: {"invalid": "Indique une date valide."},
    forms.ChoiceField: {"invalid_choice": "Choisis une valeur de la liste."},
    forms.ModelChoiceField: {"invalid_choice": "Choisis une valeur de la liste."},
    forms.CharField: {
        "max_length": "Ce texte est trop long : %(limit_value)d caractères au plus.",
    },
}


class StyledFormMixin:
    """Pose la classe de champ de la charte sur chaque widget du formulaire, et tutoie
    les messages d'erreur.

    Un formulaire qui ajoute des champs après `__init__` rappelle `style_fields()`.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def style_fields(self) -> None:
        for champ in self.fields.values():
            classes = champ.widget.attrs.get("class", "").split()
            if "field-input" not in classes:
                champ.widget.attrs["class"] = " ".join([*classes, "field-input"])
            # Du plus général au plus précis : DecimalField hérite d'IntegerField, son
            # message « montant » doit l'emporter sur celui des nombres entiers.
            for classe in reversed(type(champ).__mro__):
                champ.error_messages.update(FRIENDLY_ERRORS.get(classe, {}))


class ContactForm(StyledFormMixin, forms.ModelForm):
    """Nouvelle fiche de vendeur ou de client, remplie sans quitter l'achat ou la vente.
    Seulement les coordonnées utiles : ce sont des données personnelles."""

    class Meta:
        model = Contact
        fields = ["name", "phone", "city", "leboncoin_profile"]
        field_classes = {"leboncoin_profile": HttpsURLField}
        widgets = {
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
            "leboncoin_profile": forms.URLInput(attrs={"placeholder": "https://"}),
        }
        help_texts = {"leboncoin_profile": "Le lien de son profil, s'il en a un."}


class ContactEditForm(ContactForm):
    """La fiche complète d'un contact, depuis la liste des contacts (D81)."""

    class Meta(ContactForm.Meta):
        fields = ["name", "phone", "email", "city", "leboncoin_profile", "notes"]
        widgets = {
            **ContactForm.Meta.widgets,
            "email": forms.EmailInput(attrs={"autocomplete": "off"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            **ContactForm.Meta.help_texts,
            "notes": "Ce qu'il faut savoir : disponibilités, ce qu'il cherche…",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].error_messages["required"] = "Indique son nom."
        self.fields["email"].error_messages["invalid"] = (
            "Indique une adresse e-mail valide, par exemple nom@exemple.fr."
        )


# Choix spécial d'un contact (les autres valeurs sont des identifiants de contact).
NEW_CONTACT = "nouveau"


def contacts_or_current(current_pk: int | None):
    """Les contacts hors corbeille, par nom, plus le contact déjà enregistré même s'il est à
    la corbeille : sinon, il disparaîtrait de la liste et son lien se perdrait en silence."""
    condition = Q(deleted_at__isnull=True)
    if current_pk is not None:
        condition |= Q(pk=current_pk)
    return Contact.all_objects.filter(condition).order_by("name")


def contact_choice_label(contact: Contact) -> str:
    """« Jean · Lyon », et « (à la corbeille) » pour le contact déjà enregistré qu'on a mis
    à la corbeille depuis (D81) : il reste choisi, mais on voit pourquoi."""
    nom = f"{contact.name} · {contact.city}" if contact.city else contact.name
    return f"{nom} (à la corbeille)" if contact.deleted_at else nom


class ContactChoiceMixin:
    """Bloc « vendeur » ou « acheteur » : aucun, une fiche existante, ou une nouvelle fiche
    remplie à côté (`ContactForm`).

    Le formulaire déclare un champ de choix, appelle `setup_contact` dans son `__init__`,
    puis `resolve_contact` dans son `clean()`. Ensuite, `contact` contient la fiche existante
    choisie, ou None. Le gabarit suit le choix avec la variable Alpine `contact`.
    `initial_pk` : le contact déjà enregistré, gardé dans la liste même s'il est à la corbeille.
    """

    def setup_contact(
        self, field: str, *, none_label: str, new_label: str, initial_pk: int | None = None
    ) -> None:
        self.contact_field = field
        self.contact: Contact | None = None
        contacts = contacts_or_current(initial_pk)
        self.fields[field].choices = [("", none_label), (NEW_CONTACT, new_label)] + [
            (str(c.pk), contact_choice_label(c)) for c in contacts
        ]
        if initial_pk is not None:
            self.fields[field].initial = str(initial_pk)
        self.fields[field].widget.attrs["x-model"] = "contact"

    def resolve_contact(self, cleaned_data: dict) -> None:
        choix = cleaned_data.get(self.contact_field, "")
        if choix and choix != NEW_CONTACT:
            self.contact = Contact.all_objects.get(pk=choix)


def new_contact_form(data, field: str, prefix: str) -> tuple[ContactForm, bool]:
    """La fiche du nouveau contact, et si elle doit être lue : ses champs ne comptent que
    si « Nouveau… » est choisi dans `field`."""
    nouveau = data is not None and data.get(field) == NEW_CONTACT
    return ContactForm(data if nouveau else None, prefix=prefix), nouveau


class PartnerChoiceMixin:
    """Choix d'un seul associé, en gros boutons (composant `choix_associe.html`).

    Le formulaire déclare un champ de choix et appelle `setup_partners` dans son `__init__`.
    `keep` : un associé à garder dans la liste même s'il est parti (valeur déjà enregistrée).
    """

    def setup_partners(self, field: str, partners, keep=None) -> None:
        self.partner_field = field
        self.partners = list(partners)
        if keep is not None and keep not in self.partners:
            self.partners.append(keep)
        self.fields[field].choices = [(str(a.pk), a.name) for a in self.partners]

    def partner_options(self):
        """Les choix pour le gabarit : (valeur, libellé, associé)."""
        return [(str(a.pk), a.name, a) for a in self.partners]

    def partner(self):
        choix = self.cleaned_data[self.partner_field]
        return next(a for a in self.partners if str(a.pk) == choix)

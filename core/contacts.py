"""Contacts (D81) : recherche, rôles auprès des véhicules, mise à la corbeille.

Un contact est un vendeur ou un client (parfois les deux). Ses coordonnées sont des données
personnelles : elles restent dans la base, jamais dans Git. Mettre un contact à la corbeille
ne le détache pas de ses véhicules : la fiche d'un véhicule garde son vendeur, une vente
son acheteur, et la restauration le remet dans les listes.
"""

import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Contact
from sales.models import Deposit, Sale
from vehicles.models import Vehicle

ALREADY_IN_TRASH = "Ce contact est déjà dans la corbeille. Recharge la page."
# Ce qu'on tape souvent dans un numéro : « 06 12 34 », « 06.12.34 », « 06-12-34 ».
PHONE_SEPARATORS = (" ", ".", "-", " ")


@dataclass(frozen=True)
class ContactRole:
    """Ce qu'un contact a fait pour le garage : « Vendeur du n°2 », « Acheteur du n°1 »."""

    text: str  # le badge de la liste
    vehicle: Vehicle
    note: str = ""  # « vente annulée », « rendu »…

    @property
    def vehicle_url(self) -> str:
        """Le lien vers la fiche, sauf si elle est à la corbeille."""
        return "" if self.vehicle.deleted_at else self.vehicle.get_absolute_url()


def text_key(text: str) -> str:
    """Clé de comparaison, comme pour les fournisseurs (D43) : sans espaces en trop, sans
    majuscules ni accents. « Éric », « eric » et « ERIC » ont la même clé."""
    decompose = unicodedata.normalize("NFKD", " ".join(text.split()).casefold())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _phone_digits(text: str) -> str:
    for separateur in PHONE_SEPARATORS:
        text = text.replace(separateur, "")
    return text


def search_contacts(query: str = "") -> list[Contact]:
    """Les contacts hors corbeille, par nom ; filtrés sur le nom, la ville ou le téléphone.

    La comparaison se fait en Python : le `LIKE` de SQLite ne traite la casse que pour les
    lettres sans accent (« eric » n'y trouve pas « Éric »). Il n'y a que quelques centaines
    de contacts au plus. Le téléphone se cherche aussi sans espaces ni points : « 0612 »
    trouve « 06 12 34 56 78 »."""
    contacts = sorted(Contact.objects.all(), key=lambda c: (text_key(c.name), c.pk))
    cle = text_key(query)
    if not cle:
        return contacts
    chiffres = _phone_digits(cle)
    numero = len(chiffres) >= 2 and chiffres.isdecimal()

    def correspond(contact: Contact) -> bool:
        if cle in text_key(contact.name) or cle in text_key(contact.city):
            return True
        if cle in text_key(contact.phone):
            return True
        return numero and chiffres in _phone_digits(contact.phone)

    return [c for c in contacts if correspond(c)]


def same_name_contacts(name: str, exclude_pk: int | None = None) -> list[Contact]:
    """Les autres contacts hors corbeille qui portent ce nom (sans casse ni accents) : à
    signaler, pour éviter un doublon tant qu'il n'y a pas de fusion (D81)."""
    cle = text_key(name)
    return [c for c in Contact.objects.exclude(pk=exclude_pk) if text_key(c.name) == cle]


def contact_roles(contacts) -> dict[int, list[ContactRole]]:
    """Pour chaque contact, ses rôles, du plus petit numéro de véhicule au plus grand.

    Trois requêtes en tout, quel que soit le nombre de contacts. Les véhicules à la
    corbeille comptent (la reprise d'une vente annulée garde son vendeur)."""
    ids = [c.pk for c in contacts]
    roles: dict[int, list[tuple]] = {pk: [] for pk in ids}
    for vehicule in Vehicle.all_objects.filter(seller_id__in=ids):
        roles[vehicule.seller_id].append(
            (vehicule.number, 0, ContactRole(f"Vendeur du n°{vehicule.number}", vehicule))
        )
    for vente in Sale.objects.filter(buyer_id__in=ids).select_related("vehicle"):
        vehicule = vente.vehicle
        note = "vente annulée" if vente.is_cancelled else ""
        roles[vente.buyer_id].append(
            (vehicule.number, 1, ContactRole(f"Acheteur du n°{vehicule.number}", vehicule, note))
        )
    acomptes = Deposit.objects.filter(buyer_id__in=ids).select_related("vehicle", "sale")
    for acompte in acomptes:
        # Un acompte déduit de la vente du même acheteur : « Acheteur du n°… » suffit.
        if acompte.status == Deposit.Status.APPLIED and acompte.sale.buyer_id == acompte.buyer_id:
            continue
        vehicule = acompte.vehicle
        note = "" if acompte.status == Deposit.Status.ACTIVE else acompte.get_status_display()
        roles[acompte.buyer_id].append(
            (
                vehicule.number,
                2,
                ContactRole(f"Acompte sur le n°{vehicule.number}", vehicule, note.lower()),
            )
        )
    return {
        pk: [role for *_cle, role in sorted(liste, key=lambda r: r[:2])]
        for pk, liste in roles.items()
    }


@transaction.atomic
def trash_contact(contact: Contact) -> None:
    """Met le contact à la corbeille, relu dans la transaction : un second clic (autre
    onglet) reçoit « déjà dans la corbeille ». Ses liens avec les véhicules restent."""
    actuel = Contact.objects.filter(pk=contact.pk).first()
    if actuel is None:
        raise ValidationError(ALREADY_IN_TRASH)
    actuel.soft_delete()
    contact.deleted_at = actuel.deleted_at

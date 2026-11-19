"""Mise en forme des montants et des nombres à la française, pour l'écran et les comptes rendus,
et lecture d'un identifiant écrit dans une adresse."""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

NBSP = " "  # espace insécable : « 1 421,02 € » ne se coupe jamais en fin de ligne


ID_MAX_DIGITS = 18  # tient toujours dans un entier SQLite


def parse_id(text: str) -> int | None:
    """Le nombre écrit dans une adresse ou un formulaire, ou None s'il n'est pas utilisable.

    `isdigit()` ne suffit pas : « ² » passe ce test mais pas `int()`, et un nombre trop long
    fait planter SQLite."""
    return int(text) if text.isdecimal() and len(text) <= ID_MAX_DIGITS else None


def format_euros(amount: Decimal) -> str:
    """`Decimal("1404.1")` → `1 421,02 €` (espaces insécables, virgule décimale)."""
    arrondi = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    signe = "-" if arrondi < 0 else ""
    entiers, centimes = f"{abs(arrondi):.2f}".split(".")
    return f"{signe}{_group_thousands(entiers)},{centimes}{NBSP}€"


def format_integer(number: int) -> str:
    """`12500` → `12 500` (espace insécable entre les milliers)."""
    signe = "-" if number < 0 else ""
    return f"{signe}{_group_thousands(str(abs(number)))}"


def _group_thousands(digits: str) -> str:
    """`"1234567"` → `"1 234 567"`."""
    groupes = []
    while digits:
        groupes.insert(0, digits[-3:])
        digits = digits[:-3]
    return NBSP.join(groupes)


def format_moment(moment: dt.datetime, now: dt.datetime) -> str:
    """« aujourd'hui à 18:02 », « hier à 18:02 » ou « le 04/11/2026 à 18:02 » (heure de Paris)."""
    moment = timezone.localtime(moment)
    jours = (timezone.localtime(now).date() - moment.date()).days
    if jours == 0:
        return f"aujourd'hui à {moment:%H:%M}"
    if jours == 1:
        return f"hier à {moment:%H:%M}"
    return f"le {moment:%d/%m/%Y} à {moment:%H:%M}"

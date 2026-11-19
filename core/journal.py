"""Journal des modifications (D78) : qui a changé quoi, et quand, en phrases.

Le journal lit l'historique (`django-simple-history`) des onze tables qui en ont un :
l'argent (dépenses, lignes, paiements, remboursements, transferts, ventes, encaissements,
acomptes et leurs versements, règlements) et les fiches des véhicules (statut, réouverture,
corbeille).
Les contacts, photos, interventions et tâches n'ont pas d'historique : ils n'y sont pas.

Chaque ligne d'historique devient une phrase (« a modifié la dépense du 04/11/2026 ») avec
ce qui a changé (« Montant payé : 86,00 € → 87,00 € »). Les phrases écrites ensemble par le
même associé sur le même sujet (une dépense, ses lignes et ses paiements) sont regroupées.
Un enregistrement qui ne change rien de visible (une ligne réenregistrée telle quelle quand
on modifie sa dépense) n'est pas affiché.

Ce module ne fait que lire : il n'écrit jamais dans la base.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import capfirst

from accounting.models import Settlement
from core.formatting import format_euros, format_integer, format_moment
from core.models import Partner
from expenses.models import Expense, ExpenseLine, ExpensePayment, PartTransfer, Refund
from sales.models import Deposit, DepositReceipt, Sale, SaleReceipt
from vehicles.models import Vehicle

PER_PAGE = 50
# Deux enregistrements à moins de 2 secondes d'écart, du même associé et sur le même sujet,
# viennent du même clic (« Enregistrer » crée la dépense, ses lignes et ses paiements).
SAME_CLICK = dt.timedelta(seconds=2)
NO_AUTHOR = "Sans associé (commande)"
TEXT_MAX = 80

# Champs techniques jamais montrés comme un changement.
HIDDEN_FIELDS = {"id", "number", "created_at", "updated_at", "deleted_at"}
# Champs qui donnent le verbe de la phrase plutôt qu'une ligne « avant → après ».
VERB_FIELDS = {"reopened_at", "cancelled_at"}
# Champs vidés par la migration `sales.0005` (leur valeur est passée dans un versement, D89) :
# leurs anciennes valeurs restent montrées, mais pas leur passage à vide.
EMPTIED_FIELDS = {"legacy_amount", "legacy_received_by"}
# Les seuls décimaux qui ne sont pas des montants.
NOT_MONEY = {"quantity"}
# Les nombres entiers écrits sans espace des milliers (« 2019 », pas « 2 019 »).
NOT_GROUPED = {"year"}


@dataclass(frozen=True)
class JournalLine:
    """Une phrase du journal : « a modifié la dépense du 04/11/2026 (75,00 €) », et ce qui a
    changé (« Montant payé : 86,00 € → 87,00 € »)."""

    text: str
    changes: list[str]


@dataclass
class JournalEntry:
    """Ce qu'un associé a fait d'un seul clic sur un même sujet."""

    author: Partner | None
    at: dt.datetime
    when: str
    lines: list[JournalLine]
    link_url: str
    link_label: str
    key: tuple = field(repr=False)

    @property
    def author_name(self) -> str:
        return self.author.name if self.author else NO_AUTHOR


@dataclass(frozen=True)
class JournalPage:
    entries: list[JournalEntry]
    page: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True)
class Reopening:
    """Une réouverture de la fiche d'un véhicule vendu (D22, D50), lue dans son historique."""

    reopened_at: dt.datetime
    reason: str
    by: str
    locked_at: dt.datetime | None  # None : la fiche est encore rouverte
    ended_by_cancelled_sale: bool = False  # fin due à l'annulation de la vente, pas au verrou


# ---------------------------------------------------------------------------
# Libellés des objets liés (lus une seule fois par page, corbeille comprise)
# ---------------------------------------------------------------------------


class _Labels:
    """Les objets cités par une page du journal, lus en une requête par table.

    Le gestionnaire de base (`_base_manager`) voit aussi ce qui est à la corbeille : le
    journal parle encore d'une dépense ou d'une fiche mise de côté."""

    def __init__(self):
        self._cache: dict[type[models.Model], dict[int, models.Model]] = defaultdict(dict)

    def preload(self, model: type[models.Model], ids) -> None:
        manquants = {pk for pk in ids if pk is not None} - self._cache[model].keys()
        if manquants:
            self._cache[model].update(model._base_manager.in_bulk(manquants))

    def get(self, model: type[models.Model], pk: int | None):
        if pk is None:
            return None
        self.preload(model, [pk])
        return self._cache[model].get(pk)


def _vehicle_short(vehicle: Vehicle | None) -> str:
    """« n°2 GG 125 » : assez pour reconnaître le véhicule dans une phrase."""
    if vehicle is None:
        return "un véhicule disparu"
    nom = " ".join(partie for partie in (vehicle.brand, vehicle.model) if partie)
    return f"n°{vehicle.number} {nom}".strip()


def _object_label(obj) -> str:
    """Comment un objet lié s'écrit dans « avant → après »."""
    if isinstance(obj, Vehicle):
        return obj.display_name
    if isinstance(obj, Partner):
        return obj.name
    if isinstance(obj, Expense):
        return f"dépense du {obj.date:%d/%m/%Y} ({format_euros(obj.total_paid)})"
    if isinstance(obj, Sale):
        return f"vente du {obj.date:%d/%m/%Y} ({format_euros(obj.price)})"
    if isinstance(obj, ExpenseLine):
        return f"« {obj.label} »"
    return str(obj)


def _of(name: str) -> str:
    """« de Mathis », « d'Axel » : l'élision devant une voyelle."""
    return f"d'{name}" if name[:1].lower() in "aeéèêiîoôuy" else f"de {name}"


def _format_number(value: Decimal) -> str:
    """`Decimal("2.50")` → `2,5` ; `Decimal("1.00")` → `1`."""
    texte = f"{value.normalize():f}"
    return texte.replace(".", ",")


def _display(champ: models.Field, value, labels: _Labels) -> str:
    """Une valeur d'un champ, écrite comme à l'écran."""
    if value is None or value == "":
        return "—"
    if champ.choices:
        return str(dict(champ.flatchoices).get(value, value))
    if champ.is_relation:
        obj = labels.get(champ.related_model, value)
        return _object_label(obj) if obj is not None else "—"
    if isinstance(champ, models.BooleanField):
        return "oui" if value else "non"
    if isinstance(champ, models.DecimalField):
        return _format_number(value) if champ.name in NOT_MONEY else format_euros(value)
    if isinstance(champ, models.DateTimeField):
        return f"{timezone.localtime(value):%d/%m/%Y à %H:%M}"
    if isinstance(champ, models.DateField):
        return f"{value:%d/%m/%Y}"
    if isinstance(champ, models.IntegerField):
        return str(value) if champ.name in NOT_GROUPED else format_integer(value)
    texte = " ".join(str(value).split())
    return texte if len(texte) <= TEXT_MAX else texte[: TEXT_MAX - 1] + "…"


# ---------------------------------------------------------------------------
# Les tables du journal : comment nommer chaque ligne et à quel sujet la rattacher
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Source:
    model: type[models.Model]
    created: str  # verbe d'une création : « a ajouté », « a créé »…
    noun: Callable  # (ligne d'historique, _Labels) → « la dépense du 04/11/2026 (75,00 €) »
    subject: Callable  # (ligne, _Labels) → (genre, pk) : le sujet qui regroupe les phrases


def _noun_vehicle(row, labels):
    return f"la fiche {row.instance.display_name}"


def _noun_expense(row, labels):
    return f"la dépense du {row.date:%d/%m/%Y} ({format_euros(row.total_paid)})"


def _noun_line(row, labels):
    if row.expense_id is None:
        nom = f"la pièce à commander « {row.label} »"
    else:
        nom = f"la ligne « {row.label} »"
    if row.vehicle_id is not None:
        nom += f" ({_vehicle_short(labels.get(Vehicle, row.vehicle_id))})"
    return nom


def _noun_payment(row, labels):
    associe = labels.get(Partner, row.partner_id)
    return f"le paiement {_of(_object_label(associe))} ({format_euros(row.amount)})"


def _noun_refund(row, labels):
    ligne = labels.get(ExpenseLine, row.line_id)
    nom = f"le remboursement de {format_euros(row.amount)}"
    return f"{nom} sur « {ligne.label} »" if ligne else nom


def _noun_transfer(row, labels):
    ligne = labels.get(ExpenseLine, row.line_id)
    depuis = _vehicle_short(labels.get(Vehicle, row.from_vehicle_id))
    vers = _vehicle_short(labels.get(Vehicle, row.to_vehicle_id))
    nom = f"« {ligne.label} »" if ligne else "une pièce"
    return f"le transfert de {nom} du {depuis} vers le {vers}"


def _noun_sale(row, labels):
    vehicule = _vehicle_short(labels.get(Vehicle, row.vehicle_id))
    return f"la vente du {vehicule} ({format_euros(row.price)})"


def _noun_receipt(row, labels):
    associe = labels.get(Partner, row.partner_id)
    return f"l'encaissement {_of(_object_label(associe))} ({format_euros(row.amount)})"


def _noun_deposit(row, labels):
    vehicule = _vehicle_short(labels.get(Vehicle, row.vehicle_id))
    # Avant la 2.0.0, le montant était sur l'acompte ; depuis, il est dans ses versements.
    if row.legacy_amount is not None:
        return f"l'acompte de {format_euros(row.legacy_amount)} sur le {vehicule}"
    return f"l'acompte sur le {vehicule}"


def _noun_deposit_receipt(row, labels):
    associe = labels.get(Partner, row.partner_id)
    return f"le versement {_of(_object_label(associe))} ({format_euros(row.amount)})"


def _noun_settlement(row, labels):
    montant = format_euros(row.amount)
    if row.cancels_id is not None:
        return f"l'annulation d'un règlement de {montant}"
    de = _object_label(labels.get(Partner, row.from_partner_id))
    a = _object_label(labels.get(Partner, row.to_partner_id))
    return f"le règlement de {montant} versé par {de} à {a}"


def _subject_line(row, labels):
    return ("depense", row.expense_id) if row.expense_id else ("piece", row.id)


def _subject_transfer(row, labels):
    """Avec sa dépense, comme un remboursement : le transfert et le changement de véhicule
    de la ligne viennent du même clic (D90)."""
    ligne = labels.get(ExpenseLine, row.line_id)
    if ligne is not None and ligne.expense_id is not None:
        return ("depense", ligne.expense_id)
    return ("transfert", row.id)


def _subject_refund(row, labels):
    ligne = labels.get(ExpenseLine, row.line_id)
    if ligne is not None and ligne.expense_id is not None:
        return ("depense", ligne.expense_id)
    return ("remboursement", row.id)


SOURCES = [
    _Source(Vehicle, "a créé", _noun_vehicle, lambda row, labels: ("vehicule", row.id)),
    _Source(Expense, "a ajouté", _noun_expense, lambda row, labels: ("depense", row.id)),
    _Source(ExpenseLine, "a ajouté", _noun_line, _subject_line),
    _Source(
        ExpensePayment,
        "a ajouté",
        _noun_payment,
        lambda row, labels: ("depense", row.expense_id),
    ),
    _Source(Refund, "a enregistré", _noun_refund, _subject_refund),
    _Source(PartTransfer, "a enregistré", _noun_transfer, _subject_transfer),
    _Source(Sale, "a enregistré", _noun_sale, lambda row, labels: ("vente", row.id)),
    _Source(SaleReceipt, "a ajouté", _noun_receipt, lambda row, labels: ("vente", row.sale_id)),
    _Source(Deposit, "a enregistré", _noun_deposit, lambda row, labels: ("acompte", row.id)),
    _Source(
        DepositReceipt,
        "a ajouté",
        _noun_deposit_receipt,
        lambda row, labels: ("acompte", row.deposit_id),
    ),
    _Source(
        Settlement, "a enregistré", _noun_settlement, lambda row, labels: ("reglement", row.id)
    ),
]


# ---------------------------------------------------------------------------
# Filtres : un véhicule, ou une dépense
# ---------------------------------------------------------------------------


def _filtered(model, vehicle: Vehicle | None, expense: Expense | None):
    """L'historique d'une table, limité au véhicule ou à la dépense demandés ; None si la
    table n'en parle jamais (les règlements ne concernent aucun véhicule)."""
    historique = model.history.model.objects.all()
    if vehicle is None and expense is None:
        return historique
    lignes = ExpenseLine.history.model.objects
    if expense is not None:
        lignes_ids = lignes.filter(expense_id=expense.pk).values("id")
        filtres = {
            Expense: Q(id=expense.pk),
            ExpenseLine: Q(id__in=lignes_ids),
            ExpensePayment: Q(expense_id=expense.pk),
            Refund: Q(line_id__in=lignes_ids),
            PartTransfer: Q(line_id__in=lignes_ids),
        }
    else:
        # Lu dans l'historique : une ligne passée par ce véhicule, puis déplacée, en fait partie.
        lignes_ids = lignes.filter(vehicle_id=vehicle.pk).values("id")
        depenses_ids = lignes.filter(vehicle_id=vehicle.pk).values("expense_id")
        ventes = Sale.history.model.objects
        ventes_ids = ventes.filter(
            Q(vehicle_id=vehicle.pk) | Q(trade_in_vehicle_id=vehicle.pk)
        ).values("id")
        # Les encaissements : seulement ceux de la vente de ce véhicule, pas de celle dont il
        # est la reprise (l'argent reçu pour l'autre véhicule).
        ventes_propres_ids = ventes.filter(vehicle_id=vehicle.pk).values("id")
        filtres = {
            Vehicle: Q(id=vehicle.pk),
            Expense: Q(id__in=depenses_ids),
            ExpenseLine: Q(id__in=lignes_ids),
            ExpensePayment: Q(expense_id__in=depenses_ids),
            Refund: Q(line_id__in=lignes_ids),
            PartTransfer: Q(from_vehicle_id=vehicle.pk) | Q(to_vehicle_id=vehicle.pk),
            Sale: Q(id__in=ventes_ids),
            SaleReceipt: Q(sale_id__in=ventes_propres_ids),
            Deposit: Q(vehicle_id=vehicle.pk),
            DepositReceipt: Q(
                deposit_id__in=Deposit.history.model.objects.filter(vehicle_id=vehicle.pk).values(
                    "id"
                )
            ),
        }
    if model not in filtres:
        return None
    return historique.filter(filtres[model])


# ---------------------------------------------------------------------------
# Une ligne d'historique → une phrase
# ---------------------------------------------------------------------------


def _shown_fields(model) -> list[models.Field]:
    return [
        champ
        for champ in model._meta.concrete_fields
        if champ.name not in HIDDEN_FIELDS and champ.name not in VERB_FIELDS
    ]


def _changed_verb(row, previous) -> str:
    """Le verbe d'une modification : mise à la corbeille, restauration, réouverture…"""

    def passe(attribut):
        avant = getattr(previous, attribut, None)
        apres = getattr(row, attribut, None)
        return avant, apres

    avant, apres = passe("deleted_at")
    if avant is None and apres is not None:
        return "a mis à la corbeille"
    if avant is not None and apres is None:
        return "a restauré"
    avant, apres = passe("cancelled_at")
    if avant is None and apres is not None:
        return "a annulé"
    avant, apres = passe("reopened_at")
    if avant is None and apres is not None:
        return "a rouvert"
    if avant is not None and apres is None:
        # Une vente annulée sur une fiche rouverte vide aussi `reopened_at` : ce n'est pas
        # « Verrouiller la fiche », le changement de statut le dit déjà.
        statut_change = getattr(previous, "status", None) != getattr(row, "status", None)
        return "a modifié" if statut_change else "a verrouillé"
    if apres is not None and avant != apres:
        return "a rouvert"
    return "a modifié"


def _sentence(source: _Source, row, previous, labels: _Labels) -> JournalLine | None:
    """La phrase d'une ligne d'historique, ou None si rien de visible n'a changé."""
    nom = source.noun(row, labels)
    if row.history_type == "+":
        return JournalLine(f"{source.created} {nom}", [])
    if row.history_type == "-" or previous is None:
        # Sans ligne précédente (un versement recopié par la migration `sales.0005` n'en a
        # pas), une mise à la corbeille se lit encore dans `deleted_at`.
        if row.history_type == "-":
            verbe = "a supprimé"
        elif getattr(row, "deleted_at", None) is not None:
            verbe = "a mis à la corbeille"
        else:
            verbe = "a modifié"
        return JournalLine(f"{verbe} {nom}", [])
    changements = []
    for champ in _shown_fields(source.model):
        avant = getattr(previous, champ.attname)
        apres = getattr(row, champ.attname)
        if champ.name in EMPTIED_FIELDS and apres is None:
            continue
        if avant != apres:
            changements.append(
                f"{capfirst(champ.verbose_name)} : {_display(champ, avant, labels)}"
                f" → {_display(champ, apres, labels)}"
            )
    verbe = _changed_verb(row, previous)
    if verbe == "a modifié" and not changements:
        return None
    return JournalLine(f"{verbe} {nom}", changements)


def _link(key: tuple, labels: _Labels) -> tuple[str, str]:
    """Le lien de l'entrée vers ce dont elle parle, s'il est encore hors corbeille."""
    genre, pk = key
    if genre == "depense":
        depense = labels.get(Expense, pk)
        if depense is not None and depense.deleted_at is None:
            return depense.get_absolute_url(), "Voir la dépense"
        return "", ""
    vehicule = None
    if genre == "vehicule":
        vehicule = labels.get(Vehicle, pk)
    elif genre == "vente":
        vente = labels.get(Sale, pk)
        vehicule = labels.get(Vehicle, vente.vehicle_id) if vente else None
    elif genre == "acompte":
        acompte = labels.get(Deposit, pk)
        vehicule = labels.get(Vehicle, acompte.vehicle_id) if acompte else None
    elif genre == "piece":
        ligne = labels.get(ExpenseLine, pk)
        vehicule = labels.get(Vehicle, ligne.vehicle_id) if ligne else None
    if vehicule is not None and vehicule.deleted_at is None:
        return vehicule.get_absolute_url(), "Voir la fiche"
    return "", ""


def _previous_rows(model, rows) -> dict[int, object]:
    """Pour chaque ligne d'historique modifiée, celle qui la précède (même objet)."""
    modifiees = [row for row in rows if row.history_type == "~"]
    if not modifiees:
        return {}
    ids = {row.id for row in modifiees}
    precedent: dict[int, object] = {}
    dernier: dict[int, object] = {}
    for row in model.history.model.objects.filter(id__in=ids).order_by(
        "history_date", "history_id"
    ):
        precedent[row.history_id] = dernier.get(row.id)
        dernier[row.id] = row
    return {row.history_id: precedent.get(row.history_id) for row in modifiees}


def _preload_relations(source: _Source, rows, labels: _Labels) -> None:
    """Lit d'un coup les objets cités par les lignes (véhicules, associés, lignes…)."""
    for champ in source.model._meta.concrete_fields:
        if champ.is_relation:
            labels.preload(champ.related_model, {getattr(row, champ.attname) for row in rows})


def journal_page(
    page: int = 1, vehicle: Vehicle | None = None, expense: Expense | None = None
) -> JournalPage:
    """Une page du journal, les modifications les plus récentes d'abord.

    La page compte 50 enregistrements de l'historique, toutes tables confondues ; une fois
    regroupés par clic, et sans ceux qui ne changent rien, ils donnent moins d'entrées."""
    page = max(page, 1)
    fin = page * PER_PAGE
    total = 0
    toutes: list[tuple[dt.datetime, int, int, object]] = []
    for rang, source in enumerate(SOURCES):
        historique = _filtered(source.model, vehicle, expense)
        if historique is None:
            continue
        total += historique.count()
        lignes = historique.select_related("history_user").order_by("-history_date", "-history_id")[
            :fin
        ]
        toutes.extend((row.history_date, rang, row.history_id, row) for row in lignes)
    toutes.sort(key=lambda t: t[:3], reverse=True)
    choisies = toutes[fin - PER_PAGE : fin]

    labels = _Labels()
    par_source: dict[int, list] = defaultdict(list)
    for _date, rang, _hid, row in choisies:
        par_source[rang].append(row)
    precedents: dict[tuple[int, int], object] = {}
    for rang, rows in par_source.items():
        source = SOURCES[rang]
        anciens = _previous_rows(source.model, rows)
        _preload_relations(source, rows + [r for r in anciens.values() if r is not None], labels)
        precedents.update({(rang, hid): ancien for hid, ancien in anciens.items()})

    maintenant = timezone.now()
    entrees: list[JournalEntry] = []
    for _date, rang, hid, row in choisies:
        source = SOURCES[rang]
        phrase = _sentence(source, row, precedents.get((rang, hid)), labels)
        if phrase is None:
            continue
        sujet = source.subject(row, labels)
        derniere = entrees[-1] if entrees else None
        if (
            derniere is not None
            and derniere.key == sujet
            and derniere.author == row.history_user
            and derniere.at - row.history_date <= SAME_CLICK
        ):
            # Plus ancien que les phrases déjà là : il passe devant (ordre de saisie).
            derniere.lines.insert(0, phrase)
            derniere.at = row.history_date
            continue
        url, libelle = _link(sujet, labels)
        entrees.append(
            JournalEntry(
                author=row.history_user,
                at=row.history_date,
                when=format_moment(row.history_date, maintenant),
                lines=[phrase],
                link_url=url,
                link_label=libelle,
                key=sujet,
            )
        )
    return JournalPage(entrees, page, has_previous=page > 1, has_next=total > fin)


# ---------------------------------------------------------------------------
# Réouvertures d'une fiche (bloc de la fiche véhicule)
# ---------------------------------------------------------------------------


def vehicle_reopenings(vehicle: Vehicle) -> list[Reopening]:
    """Chaque réouverture de la fiche, la plus récente d'abord, avec son motif, son auteur
    et la date où la fiche a été verrouillée de nouveau."""
    reouvertures: list[dict] = []
    for row in vehicle.history.select_related("history_user").order_by(
        "history_date", "history_id"
    ):
        en_cours = reouvertures[-1] if reouvertures else None
        ouverte = en_cours is not None and en_cours["locked_at"] is None
        if row.reopened_at is not None and (not ouverte or en_cours["at"] != row.reopened_at):
            reouvertures.append(
                {
                    "at": row.reopened_at,
                    "reason": row.reopen_reason,
                    "by": row.history_user.name if row.history_user else NO_AUTHOR,
                    "locked_at": None,
                    "cancelled": False,
                }
            )
        elif row.reopened_at is None and ouverte:
            en_cours["locked_at"] = row.history_date
            # Le statut quitte « Vendu » en même temps : c'est la vente annulée.
            en_cours["cancelled"] = row.status != Vehicle.Status.SOLD
    return [
        Reopening(r["at"], r["reason"], r["by"], r["locked_at"], r["cancelled"])
        for r in reversed(reouvertures)
    ]

"""Calculs d'argent du garage : fonctions pures, testées (marqueur `finance`).

Règles : skill `regles-financieres`. Ces fonctions ne lisent pas la base : elles reçoivent
des `Decimal` et des dataclasses simples, préparées par `accounting/queries.py`.
Un associé est désigné par une clé quelconque (son id en base, son nom dans les tests).

Principe : tout ce qui est dépensé ou encaissé pour le garage appartient pour moitié à
chaque associé. Rien n'est stocké en base : tout est recalculé.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from core.formatting import format_euros

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
TRADE_IN_MINIMUM = Decimal("0.02")  # un centime pour chacun des deux associés

# États d'un acompte (mêmes valeurs que `sales.models.Deposit.Status`).
DEPOSIT_ACTIVE = "ACTIVE"
DEPOSIT_APPLIED = "APPLIED"
DEPOSIT_REFUNDED = "REFUNDED"
DEPOSIT_KEPT = "KEPT"
# Un acompte rendu n'a aucun effet : l'argent est revenu à l'acheteur.
DEPOSIT_STATUSES_RECEIVED = frozenset({DEPOSIT_ACTIVE, DEPOSIT_APPLIED, DEPOSIT_KEPT})
# Un acompte en cours sera déduit du prix quand la vente sera enregistrée.
DEPOSIT_STATUSES_DEDUCTED = frozenset({DEPOSIT_ACTIVE, DEPOSIT_APPLIED})

PartnerKey = Hashable
VehicleKey = Hashable


# ---------------------------------------------------------------------------
# Données d'entrée
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Payment:
    """Part d'une dépense payée par un associé."""

    partner: PartnerKey
    amount: Decimal


@dataclass(frozen=True)
class RefundInput:
    """Remboursement reçu par un associé sur une ligne retournée."""

    partner: PartnerKey
    amount: Decimal


@dataclass(frozen=True)
class LineInput:
    """Ligne d'une dépense. `vehicle` vide : dépense générale du garage."""

    quantity: Decimal
    unit_price: Decimal
    vehicle: VehicleKey | None = None
    refunds: tuple[RefundInput, ...] = ()
    key: Hashable = None


@dataclass(frozen=True)
class ExpenseInput:
    """Une dépense : un paiement réel (frais de port compris), ses payeurs et ses lignes.

    L'ordre des paiements compte pour l'arrondi des parts (D31) : le dernier payeur
    reçoit le reste de chaque ligne. `date` sert à ranger la dépense dans son année (D57).
    """

    total_paid: Decimal
    payments: tuple[Payment, ...]
    lines: tuple[LineInput, ...]
    is_personal: bool = False
    key: Hashable = None
    date: dt.date | None = None


@dataclass(frozen=True)
class Receipt:
    """Argent d'une vente reçu par un associé."""

    partner: PartnerKey
    amount: Decimal


@dataclass(frozen=True)
class SaleInput:
    """Une vente. Prix = encaissements + acomptes appliqués + valeur de reprise."""

    price: Decimal
    receipts: tuple[Receipt, ...] = ()
    trade_in_value: Decimal = ZERO
    vehicle: VehicleKey | None = None
    cancelled: bool = False
    key: Hashable = None
    date: dt.date | None = None
    trade_in_vehicle: VehicleKey | None = None


@dataclass(frozen=True)
class DepositInput:
    """Un versement d'acompte reçu par un associé, avec l'état de son acompte (`ACTIVE`,
    `APPLIED`, ...). Un acompte reçu en plusieurs fois ou par les deux associés donne un
    `DepositInput` par versement, tous avec la même clé (`key`, celle de l'acompte, D89).
    `sale` : clé de la vente dont l'acompte est déduit (`APPLIED`)."""

    partner: PartnerKey
    amount: Decimal
    status: str
    vehicle: VehicleKey | None = None
    sale: Hashable = None
    key: Hashable = None


@dataclass(frozen=True)
class SettlementInput:
    """Argent versé d'un associé à l'autre (une annulation est un règlement inverse)."""

    from_partner: PartnerKey
    to_partner: PartnerKey
    amount: Decimal


# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineCost:
    """Coût d'une ligne : total réparti (frais de port compris), remboursements, coût net,
    et part de chaque associé dans le total réparti."""

    line: LineInput
    allocated: Decimal
    refunded: Decimal
    shares: Mapping[PartnerKey, Decimal]
    refunds_by_partner: Mapping[PartnerKey, Decimal]

    @property
    def vehicle(self) -> VehicleKey | None:
        return self.line.vehicle

    @property
    def net(self) -> Decimal:
        return self.allocated - self.refunded


@dataclass(frozen=True)
class VehicleCost:
    """Coût de revient d'un véhicule et investissement de chaque associé."""

    total: Decimal = ZERO
    by_partner: Mapping[PartnerKey, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    """Ce qu'un associé a sorti de sa poche pour le garage et ce qu'il a reçu."""

    paid_out: Decimal = ZERO
    received: Decimal = ZERO

    @property
    def value(self) -> Decimal:
        """Position = sorti − reçu."""
        return self.paid_out - self.received


@dataclass(frozen=True)
class Balance:
    """Solde entre associés : `debtor` doit `amount` à `creditor`."""

    debtor: PartnerKey | None
    creditor: PartnerKey | None
    amount: Decimal

    @property
    def is_settled(self) -> bool:
        return self.amount == ZERO


# ---------------------------------------------------------------------------
# Arrondi
# ---------------------------------------------------------------------------


def to_cents(amount: Decimal) -> Decimal:
    """Arrondi au centime, demi-centime vers le haut (`ROUND_HALF_UP`)."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def split_by_largest_remainder(total: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """Partage `total` (au centime) au prorata de `weights`, la somme tombant juste.

    Méthode du plus grand reste : chaque part est tronquée au centime, puis les centimes
    manquants vont, un par un, aux parts dont la partie tronquée était la plus grande ;
    à égalité, dans l'ordre. Si tous les poids sont nuls, le partage est égal.
    Un total négatif (une perte) est partagé comme son opposé, puis changé de signe.
    """
    if not weights:
        raise ValueError("Rien entre quoi partager.")
    if any(poids < 0 for poids in weights):
        raise ValueError("Les poids d'un partage ne peuvent pas être négatifs.")
    if total != to_cents(total):
        raise ValueError(f"Le montant à partager n'est pas au centime : {total}.")
    if total < 0:
        return [-part for part in split_by_largest_remainder(-total, weights)]

    somme = sum(weights, ZERO)
    if somme == 0:
        weights = [Decimal(1)] * len(weights)
        somme = Decimal(len(weights))

    brutes = [total * poids / somme for poids in weights]
    parts = [brute.quantize(CENT, rounding=ROUND_FLOOR) for brute in brutes]
    centimes_manquants = int((total - sum(parts, ZERO)) / CENT)
    ordre = sorted(range(len(parts)), key=lambda i: (-(brutes[i] - parts[i]), i))
    for i in ordre[:centimes_manquants]:
        parts[i] += CENT
    return parts


def split_equally(amount: Decimal, partners: Sequence[PartnerKey]) -> dict[PartnerKey, Decimal]:
    """Partage à parts égales entre associés ; le centime impair va au premier."""
    parts = split_by_largest_remainder(amount, [Decimal(1)] * len(partners))
    return dict(zip(partners, parts, strict=True))


# ---------------------------------------------------------------------------
# Dépenses : frais de port, parts, coûts
# ---------------------------------------------------------------------------


def line_total(line: LineInput) -> Decimal:
    """Total d'une ligne = quantité × prix unitaire, au centime."""
    return to_cents(line.quantity * line.unit_price)


def lines_sum(lines: Iterable[LineInput]) -> Decimal:
    """Somme des totaux des lignes (sans les frais de port)."""
    return sum((line_total(ligne) for ligne in lines), ZERO)


def shipping_cost(expense: ExpenseInput) -> Decimal:
    """Frais de port = montant payé − somme des lignes (négatif en cas de remise)."""
    return expense.total_paid - lines_sum(expense.lines)


def allocate_shipping(expense: ExpenseInput) -> list[Decimal]:
    """Total réparti de chaque ligne : son total + sa part des frais de port, au prorata
    du prix des lignes. La somme égale exactement le montant payé."""
    if not expense.lines:
        return []
    return split_by_largest_remainder(
        expense.total_paid, [line_total(ligne) for ligne in expense.lines]
    )


def partner_shares(
    expense: ExpenseInput, allocated: Sequence[Decimal]
) -> list[dict[PartnerKey, Decimal]]:
    """Part de chaque associé dans chaque ligne, proportionnelle à son paiement (D31).

    Le paiement de chaque associé, sauf le dernier, est réparti sur les lignes par la
    méthode du plus grand reste ; le dernier payeur reçoit le reste de chaque ligne.
    Ainsi les parts d'une ligne somment au total réparti, et les parts d'un associé
    somment à son paiement.
    """
    parts: list[dict[PartnerKey, Decimal]] = [{} for _ in allocated]
    if not expense.payments or not allocated:
        return parts
    *premiers, dernier = expense.payments
    for paiement in premiers:
        for i, montant in enumerate(split_by_largest_remainder(paiement.amount, allocated)):
            parts[i][paiement.partner] = parts[i].get(paiement.partner, ZERO) + montant
    for i, total_ligne in enumerate(allocated):
        deja = sum(parts[i].values(), ZERO)
        parts[i][dernier.partner] = parts[i].get(dernier.partner, ZERO) + total_ligne - deja
    return parts


def expense_line_costs(expense: ExpenseInput) -> list[LineCost]:
    """Coût de chaque ligne d'une dépense. Un achat personnel ne coûte rien au garage."""
    if expense.is_personal:
        return []
    repartis = allocate_shipping(expense)
    parts = partner_shares(expense, repartis)
    couts = []
    for ligne, reparti, parts_ligne in zip(expense.lines, repartis, parts, strict=True):
        rembourse_par_associe: dict[PartnerKey, Decimal] = defaultdict(lambda: ZERO)
        for remboursement in ligne.refunds:
            rembourse_par_associe[remboursement.partner] += remboursement.amount
        couts.append(
            LineCost(
                line=ligne,
                allocated=reparti,
                refunded=sum(rembourse_par_associe.values(), ZERO),
                shares=parts_ligne,
                refunds_by_partner=dict(rembourse_par_associe),
            )
        )
    return couts


def vehicle_costs(expenses: Iterable[ExpenseInput]) -> dict[VehicleKey | None, VehicleCost]:
    """Coût de revient de chaque véhicule (clé `None` : dépenses générales du garage).

    Investissement d'un associé = ses parts sur les lignes − remboursements qu'il a reçus.
    """
    totaux: dict[VehicleKey | None, Decimal] = defaultdict(lambda: ZERO)
    par_associe: dict[VehicleKey | None, dict[PartnerKey, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    for depense in expenses:
        for cout in expense_line_costs(depense):
            totaux[cout.vehicle] += cout.net
            for associe, part in cout.shares.items():
                par_associe[cout.vehicle][associe] += part
            for associe, rembourse in cout.refunds_by_partner.items():
                par_associe[cout.vehicle][associe] -= rembourse
    return {
        vehicule: VehicleCost(total=total, by_partner=dict(par_associe[vehicule]))
        for vehicule, total in totaux.items()
    }


def combined_cost(costs: Iterable[LineCost]) -> VehicleCost:
    """Coût net de quelques lignes (par exemple l'achat d'un véhicule) et part nette de
    chaque associé : ses parts moins les remboursements qu'il a reçus."""
    total = ZERO
    par_associe: dict[PartnerKey, Decimal] = defaultdict(lambda: ZERO)
    for cout in costs:
        total += cout.net
        for associe, part in cout.shares.items():
            par_associe[associe] += part
        for associe, rembourse in cout.refunds_by_partner.items():
            par_associe[associe] -= rembourse
    return VehicleCost(total=total, by_partner=dict(par_associe))


@dataclass(frozen=True)
class ExpensePreview:
    """Ce que le formulaire de dépense affiche pendant la saisie."""

    total_paid: Decimal
    lines_sum: Decimal
    shipping: Decimal
    allocated: tuple[Decimal, ...]
    total_is_lines_sum: bool


def expense_preview(total_paid: Decimal | None, lines: Sequence[LineInput]) -> ExpensePreview:
    """Aperçu d'une dépense en cours de saisie : somme des lignes, frais de port et total
    réparti de chaque ligne.

    Un montant payé laissé vide vaut la somme des lignes (pas de frais de port). Tant que
    le montant payé n'est pas positif, rien n'est réparti.
    """
    somme = lines_sum(lines)
    vide = total_paid is None
    total = somme if vide else total_paid
    repartis: tuple[Decimal, ...] = ()
    if lines and total > 0:
        repartis = tuple(allocate_shipping(ExpenseInput(total, (), tuple(lines))))
    return ExpensePreview(
        total_paid=total,
        lines_sum=somme,
        shipping=total - somme,
        allocated=repartis,
        total_is_lines_sum=vide,
    )


# ---------------------------------------------------------------------------
# Dépenses du garage (lignes sans véhicule)
# ---------------------------------------------------------------------------


def is_garage_expense(expense: ExpenseInput) -> bool:
    """Une dépense du garage a au moins une ligne sans véhicule (D57). Un achat personnel
    en est une aussi (D41), mais il ne coûte rien au garage."""
    return any(ligne.vehicle is None for ligne in expense.lines)


@dataclass(frozen=True)
class GarageExpense:
    """Une dépense du garage : seul le coût net de ses lignes sans véhicule compte (D57).

    `cost` : total réparti (frais de port compris) − remboursements de ces lignes ;
    `garage_lines` : leurs clés ; `shared_with_vehicle` : la dépense a aussi des lignes sur
    un véhicule, qui ne sont pas comptées ici.
    """

    expense: ExpenseInput
    cost: Decimal
    garage_lines: tuple[Hashable, ...]
    shared_with_vehicle: bool


@dataclass(frozen=True)
class GarageExpenses:
    """Les dépenses du garage d'une année, leur total et la part de chacun (moitié).

    `personal` : les achats personnels de l'année, montrés pour mémoire, hors du total
    (D09, D58).
    """

    year: int
    items: tuple[GarageExpense, ...]
    personal: tuple[ExpenseInput, ...]
    total: Decimal
    shares: Mapping[PartnerKey, Decimal]


def garage_expenses_for_year(
    expenses: Iterable[ExpenseInput], year: int, partners: Sequence[PartnerKey]
) -> GarageExpenses:
    """Les dépenses du garage datées de l'année `year`, dans l'ordre reçu.

    Une dépense est rangée dans l'année de sa date, remboursements compris (D57). La somme
    des coûts est la même que la clé `None` de `vehicle_costs` pour ces dépenses.
    """
    depenses: list[GarageExpense] = []
    personnelles: list[ExpenseInput] = []
    for depense in expenses:
        if depense.date is None or depense.date.year != year or not is_garage_expense(depense):
            continue
        if depense.is_personal:
            personnelles.append(depense)
            continue
        couts = [c for c in expense_line_costs(depense) if c.vehicle is None]
        depenses.append(
            GarageExpense(
                expense=depense,
                cost=sum((c.net for c in couts), ZERO),
                garage_lines=tuple(c.line.key for c in couts),
                shared_with_vehicle=len(couts) < len(depense.lines),
            )
        )
    total = sum((d.cost for d in depenses), ZERO)
    return GarageExpenses(
        year=year,
        items=tuple(depenses),
        personal=tuple(personnelles),
        total=total,
        shares=split_equally(total, partners),
    )


def garage_expense_years(expenses: Iterable[ExpenseInput]) -> list[int]:
    """Les années qui ont une dépense du garage (achats personnels compris), la plus
    récente d'abord : ce sont les choix du sélecteur d'année."""
    return sorted(
        {d.date.year for d in expenses if d.date is not None and is_garage_expense(d)},
        reverse=True,
    )


def amounts_by_partner(payments: Iterable[Payment]) -> dict[PartnerKey, Decimal]:
    """Le total de chaque associé, dans l'ordre où il apparaît."""
    totaux: dict[PartnerKey, Decimal] = defaultdict(lambda: ZERO)
    for paiement in payments:
        totaux[paiement.partner] += paiement.amount
    return dict(totaux)


def payment_problem(total: Decimal, payments: Sequence[Payment]) -> str | None:
    """Pourquoi des paiements ne conviennent pas à une dépense de `total`, ou None.

    Le message s'affiche tel quel dans le formulaire.
    """
    if total <= 0:
        return "Le montant doit être plus grand que 0 €."
    if not payments:
        return "Indique qui a payé."
    associes = [paiement.partner for paiement in payments]
    if len(set(associes)) != len(associes):
        return "Chaque associé ne peut apparaître qu'une fois parmi les payeurs."
    if any(paiement.amount <= 0 for paiement in payments):
        return (
            "Chaque part payée doit être plus grande que 0 €. "
            "Si un seul associé a payé, choisis son prénom."
        )
    paye = sum((paiement.amount for paiement in payments), ZERO)
    if paye != total:
        return (
            f"Les parts payées font {format_euros(paye)} au lieu de {format_euros(total)}. "
            "Corrige l'un des montants."
        )
    return None


# ---------------------------------------------------------------------------
# Retours et remboursements
# ---------------------------------------------------------------------------


def refund_left(allocated: Decimal, refunded: Decimal) -> Decimal:
    """Ce qui reste à rembourser sur une ligne : son total réparti moins les
    remboursements déjà enregistrés (jamais négatif)."""
    return max(allocated - refunded, ZERO)


def refund_problem(amount: Decimal, allocated: Decimal, other_refunds: Decimal) -> str | None:
    """Pourquoi un remboursement de `amount` ne convient pas à une ligne de total réparti
    `allocated` déjà remboursée de `other_refunds` (sans compter ce remboursement), ou None.

    0,00 € est accepté : le montant est « à préciser » (retour pas encore remboursé, ou
    reprise de l'Excel). Le message s'affiche tel quel dans le formulaire.
    """
    if amount < 0:
        return "Le montant ne peut pas être négatif."
    reste = refund_left(allocated, other_refunds)
    if amount > reste:
        return (
            f"Il ne reste que {format_euros(reste)} à rembourser sur cette ligne. "
            "Corrige le montant."
        )
    return None


def is_fully_refunded(allocated: Decimal, refunded: Decimal) -> bool:
    """La ligne est entièrement remboursée : elle passe en « Retourné ». Un remboursement
    partiel ou « à préciser » (0,00 €) ne suffit pas."""
    return refunded > 0 and refunded >= allocated


def is_returned(
    allocated: Decimal, refunded: Decimal, *, was_returned: bool, to_be_specified: bool
) -> bool:
    """La pièce est-elle « Retourné » (D45) ?

    - entièrement remboursée : oui ;
    - déjà « Retourné » avec un remboursement « à préciser » (0,00 €) : elle le reste
      (ligne de l'Excel marquée « Retourné/Remboursé » dont le montant n'est pas connu) ;
    - sinon : non. Un remboursement « à préciser » ne fait donc jamais passer une pièce en
      « Retourné », mais ne l'en fait pas sortir non plus.
    """
    if is_fully_refunded(allocated, refunded):
        return True
    return was_returned and to_be_specified


@dataclass(frozen=True)
class RefundRoom:
    """Ce qu'un remboursement peut encore couvrir sur une ligne."""

    allocated: Decimal  # total réparti de la ligne, frais de port compris
    others: Decimal  # remboursements déjà enregistrés, sans celui qu'on corrige
    left: Decimal  # ce qui reste à rembourser


def refund_room(allocated: Decimal, refunded: Decimal, current: Decimal = ZERO) -> RefundRoom:
    """La marge d'un remboursement sur une ligne remboursée de `refunded` en tout ;
    `current` : le montant du remboursement qu'on corrige (il ne compte pas contre lui-même)."""
    autres = refunded - current
    return RefundRoom(allocated=allocated, others=autres, left=refund_left(allocated, autres))


# ---------------------------------------------------------------------------
# Ventes et marges
# ---------------------------------------------------------------------------


def active_sale(sales: Iterable[SaleInput]) -> SaleInput | None:
    """La vente non annulée d'un véhicule, ou None (D21 : il n'y en a qu'une)."""
    actives = [vente for vente in sales if not vente.cancelled]
    if len(actives) > 1:
        raise ValueError("Un véhicule ne peut avoir qu'une seule vente non annulée.")
    return actives[0] if actives else None


def vehicle_margin(
    cost_price: Decimal, sales: Iterable[SaleInput], deposits: Iterable[DepositInput] = ()
) -> Decimal | None:
    """Marge = prix de la vente non annulée + acomptes conservés − coût de revient (D32).

    None tant que le véhicule n'a pas de vente non annulée (ni le statut ni la
    réouverture de la fiche n'entrent en compte, D22).
    """
    vente = active_sale(sales)
    if vente is None:
        return None
    return vente.price + kept_deposits_total(deposits) - cost_price


def kept_deposits_total(deposits: Iterable[DepositInput]) -> Decimal:
    """Les acomptes conservés par le garage : ils comptent dans la marge (D32)."""
    return sum((a.amount for a in deposits if a.status == DEPOSIT_KEPT), ZERO)


def deposit_total(amounts: Iterable[Decimal]) -> Decimal:
    """Le montant d'un acompte : la somme de ses versements hors corbeille (D89).
    Il n'est jamais stocké."""
    return sum(amounts, ZERO)


def deposit_receipts_problem(
    total: Decimal, receipts: Sequence[Payment], what: str = "L'acompte"
) -> str | None:
    """Pourquoi ces versements ne conviennent pas à un acompte (ou un complément) de
    `total`, ou None. Même règle que les payeurs d'une dépense, avec les mots de l'acompte.
    `what` : « L'acompte », ou « Le versement » pour un complément.

    Le message s'affiche tel quel dans le formulaire.
    """
    if total <= 0:
        return f"{what} doit être plus grand que 0 €."
    if not receipts:
        return "Indique qui a reçu l'argent."
    associes = [versement.partner for versement in receipts]
    if len(set(associes)) != len(associes):
        return "Chaque associé ne peut apparaître qu'une fois parmi ceux qui ont reçu l'argent."
    if any(versement.amount <= 0 for versement in receipts):
        return (
            "Chaque part reçue doit être plus grande que 0 €. "
            "Si un seul associé a reçu l'argent, choisis son prénom."
        )
    recu = sum((versement.amount for versement in receipts), ZERO)
    if recu != total:
        return (
            f"Les parts reçues font {format_euros(recu)} au lieu de {format_euros(total)}. "
            "Corrige l'un des montants."
        )
    return None


def margin_per_partner(
    margin: Decimal, partners: Sequence[PartnerKey]
) -> dict[PartnerKey, Decimal]:
    """Chaque associé gagne (ou perd) la moitié de la marge."""
    return split_equally(margin, partners)


def sale_left_to_receive(
    price: Decimal, deposits: Iterable[DepositInput] = (), trade_in_value: Decimal = ZERO
) -> Decimal:
    """Ce qui reste à encaisser : prix − acomptes en cours ou déjà déduits − reprise.

    0 € quand l'acompte couvre le prix ; négatif quand le prix est trop bas (voir
    `sale_price_problem`). Un acompte rendu ou conservé n'est pas déduit du prix.
    """
    deduits = sum((a.amount for a in deposits if a.status in DEPOSIT_STATUSES_DEDUCTED), ZERO)
    return price - deduits - trade_in_value


def sale_price_problem(price: Decimal, left_to_receive: Decimal) -> str | None:
    """Pourquoi ce prix de vente n'est pas possible, ou None.

    Le message s'affiche tel quel dans le formulaire.
    """
    if price <= 0:
        return "Le prix de vente doit être plus grand que 0 €."
    if left_to_receive < 0:
        return (
            f"Ce qui est déjà reçu (acompte ou reprise) fait "
            f"{format_euros(price - left_to_receive)}, plus que ce prix. Corrige le prix."
        )
    return None


def trade_in_problem(value: Decimal, partners: Sequence[PartnerKey]) -> str | None:
    """Pourquoi cette reprise n'est pas possible, ou None.

    Le véhicule repris est acheté pour moitié par chaque associé (D19) : il faut deux
    associés, et chacun doit en payer une part, ce qu'une valeur de 0,01 € ne permet pas.
    Le message s'affiche tel quel dans le formulaire.
    """
    if len(partners) != 2:
        return (
            "Une reprise se partage entre deux associés, et il n'y en a pas deux d'actifs. "
            "Préviens Mathis."
        )
    if value <= 0:
        return "La valeur de reprise doit être plus grande que 0 €."
    if min(split_equally(value, partners).values()) <= 0:
        return (
            f"Une reprise de {format_euros(value)} ne se partage pas entre vous deux. "
            f"Indique au moins {format_euros(TRADE_IN_MINIMUM)}."
        )
    return None


def receipts_problem(left_to_receive: Decimal, receipts: Sequence[Payment]) -> str | None:
    """Pourquoi ces encaissements ne conviennent pas à ce qui reste à encaisser, ou None.

    Si rien ne reste à encaisser, aucun encaissement n'est attendu. Le message s'affiche
    tel quel dans le formulaire.
    """
    if left_to_receive == 0:
        if receipts:
            return "Il ne reste rien à encaisser : ce qui est déjà reçu couvre le prix."
        return None
    if not receipts:
        return "Indique qui a encaissé l'argent."
    associes = [encaissement.partner for encaissement in receipts]
    if len(set(associes)) != len(associes):
        return "Chaque associé ne peut apparaître qu'une fois parmi les encaissements."
    if any(encaissement.amount <= 0 for encaissement in receipts):
        return (
            "Chaque part encaissée doit être plus grande que 0 €. "
            "Si un seul associé a encaissé, choisis son prénom."
        )
    encaisse = sum((encaissement.amount for encaissement in receipts), ZERO)
    if encaisse != left_to_receive:
        return (
            f"Les parts encaissées font {format_euros(encaisse)} au lieu de "
            f"{format_euros(left_to_receive)}. Corrige l'un des montants."
        )
    return None


@dataclass(frozen=True)
class MarginDisplay:
    """Une marge telle que l'écran l'affiche : sans signe moins.

    `shown_margin` et `shown_shares` sont sans signe : l'écran dit « Perte de 300,00 € »
    d'après `is_loss`, pas « -300,00 € ».
    """

    is_loss: bool
    shown_margin: Decimal
    shown_shares: Mapping[PartnerKey, Decimal]


def margin_display(margin: Decimal, shares: Mapping[PartnerKey, Decimal]) -> MarginDisplay:
    """La marge et la part de chaque associé, prêtes à afficher (perte sans signe).

    Une marge de 0,00 € n'est pas une perte.
    """
    return MarginDisplay(
        is_loss=margin < 0,
        shown_margin=abs(margin),
        shown_shares={associe: abs(part) for associe, part in shares.items()},
    )


@dataclass(frozen=True)
class SalePreview:
    """Ce que le formulaire de vente affiche pendant la saisie du prix.

    `display` donne la marge sans signe ; `is_loss`, `shown_margin` et `shown_shares` en
    sont des raccourcis pour le gabarit.
    """

    left_to_receive: Decimal
    margin: Decimal
    margin_shares: Mapping[PartnerKey, Decimal]
    kept_deposits: Decimal
    display: MarginDisplay
    problem: str | None

    @property
    def is_loss(self) -> bool:
        return self.display.is_loss

    @property
    def shown_margin(self) -> Decimal:
        return self.display.shown_margin

    @property
    def shown_shares(self) -> Mapping[PartnerKey, Decimal]:
        return self.display.shown_shares


def sale_preview(
    price: Decimal,
    cost_price: Decimal,
    partners: Sequence[PartnerKey],
    deposits: Iterable[DepositInput] = (),
    trade_in_value: Decimal = ZERO,
) -> SalePreview:
    """Aperçu d'une vente en cours de saisie : reste à encaisser, marge, part de chacun."""
    acomptes = tuple(deposits)
    reste = sale_left_to_receive(price, acomptes, trade_in_value)
    marge = vehicle_margin(cost_price, [SaleInput(price)], acomptes)
    parts = margin_per_partner(marge, partners)
    return SalePreview(
        left_to_receive=reste,
        margin=marge,
        margin_shares=parts,
        kept_deposits=kept_deposits_total(acomptes),
        display=margin_display(marge, parts),
        problem=sale_price_problem(price, reste),
    )


# ---------------------------------------------------------------------------
# Positions et solde entre associés
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionDetail:
    """D'où vient la position d'un associé, pour l'écran des comptes.

    `sales_received` : encaissements des ventes, acomptes et part de la valeur de reprise.
    """

    expenses_paid: Decimal = ZERO
    refunds_received: Decimal = ZERO
    sales_received: Decimal = ZERO
    settlements_paid: Decimal = ZERO
    settlements_received: Decimal = ZERO

    @property
    def position(self) -> Position:
        return Position(
            paid_out=self.expenses_paid + self.settlements_paid,
            received=self.refunds_received + self.sales_received + self.settlements_received,
        )


def position_details(
    partners: Sequence[PartnerKey],
    expenses: Iterable[ExpenseInput] = (),
    sales: Iterable[SaleInput] = (),
    deposits: Iterable[DepositInput] = (),
    settlements: Iterable[SettlementInput] = (),
) -> dict[PartnerKey, PositionDetail]:
    """Le détail de la position de chaque associé, rubrique par rubrique.

    - sorti : paiements des dépenses non personnelles + règlements versés ;
    - reçu : encaissements des ventes non annulées, acomptes en cours, appliqués ou
      conservés, part de la valeur de reprise (moitié chacun), remboursements, règlements
      reçus.

    Un règlement compte en entier, sans partage : c'est de l'argent passé d'un associé à
    l'autre, pas de l'argent du garage. Il ne change donc pas la somme des positions.
    """
    rubriques = (
        "expenses_paid",
        "refunds_received",
        "sales_received",
        "settlements_paid",
        "settlements_received",
    )
    comptes: dict[str, dict[PartnerKey, Decimal]] = {
        rubrique: dict.fromkeys(partners, ZERO) for rubrique in rubriques
    }

    def ajouter(rubrique: str, associe: PartnerKey, montant: Decimal):
        if associe not in comptes[rubrique]:
            raise ValueError(f"Associé inconnu : {associe!r}.")
        comptes[rubrique][associe] += montant

    for depense in expenses:
        if depense.is_personal:
            continue
        for paiement in depense.payments:
            ajouter("expenses_paid", paiement.partner, paiement.amount)
        for ligne in depense.lines:
            for remboursement in ligne.refunds:
                ajouter("refunds_received", remboursement.partner, remboursement.amount)

    for vente in sales:
        if vente.cancelled:
            continue
        for encaissement in vente.receipts:
            ajouter("sales_received", encaissement.partner, encaissement.amount)
        for associe, part in split_equally(vente.trade_in_value, partners).items():
            ajouter("sales_received", associe, part)

    for acompte in deposits:
        if acompte.status in DEPOSIT_STATUSES_RECEIVED:
            ajouter("sales_received", acompte.partner, acompte.amount)

    for reglement in settlements:
        ajouter("settlements_paid", reglement.from_partner, reglement.amount)
        ajouter("settlements_received", reglement.to_partner, reglement.amount)

    return {
        associe: PositionDetail(**{rubrique: comptes[rubrique][associe] for rubrique in rubriques})
        for associe in partners
    }


def total_paid_out(positions: Mapping[PartnerKey, Position]) -> Decimal:
    """Ce que les associés ont sorti de leur poche en tout (dépenses et règlements)."""
    return sum((position.paid_out for position in positions.values()), ZERO)


def partner_positions(
    partners: Sequence[PartnerKey],
    expenses: Iterable[ExpenseInput] = (),
    sales: Iterable[SaleInput] = (),
    deposits: Iterable[DepositInput] = (),
    settlements: Iterable[SettlementInput] = (),
) -> dict[PartnerKey, Position]:
    """Position de chaque associé : ce qu'il a sorti − ce qu'il a reçu pour le garage
    (détail dans `position_details`)."""
    details = position_details(partners, expenses, sales, deposits, settlements)
    return {associe: detail.position for associe, detail in details.items()}


def balance(positions: Mapping[PartnerKey, Position]) -> Balance:
    """Qui doit combien à qui, entre les deux associés.

    Montant dû par le second au premier = (position du premier − position du second) / 2,
    arrondi au centime (`ROUND_HALF_UP` sur la valeur absolue : le demi-centime va à celui
    qui a avancé).

    Un écart d'un centime entre les positions est l'équilibre (D33) : sinon, régler ce
    centime ferait passer la dette dans l'autre sens, sans fin. Avec cette règle, après le
    règlement du montant dû, il reste au plus un centime d'écart : le solde est à zéro.
    """
    if len(positions) != 2:
        raise ValueError("Le solde se calcule entre exactement deux associés.")
    (premier, position_1), (second, position_2) = positions.items()
    ecart = position_1.value - position_2.value
    if abs(ecart) <= CENT:
        return Balance(debtor=None, creditor=None, amount=ZERO)
    montant = to_cents(abs(ecart) / 2)
    if ecart > 0:
        return Balance(debtor=second, creditor=premier, amount=montant)
    return Balance(debtor=premier, creditor=second, amount=montant)


def balance_sentence(solde: Balance, names: Mapping[PartnerKey, str]) -> str:
    """Le solde en toutes lettres : « Axel doit 205,44 € à Mathis »."""
    if solde.is_settled:
        return "comptes à l'équilibre"
    return f"{names[solde.debtor]} doit {format_euros(solde.amount)} à {names[solde.creditor]}"


SETTLED_LABEL = "À l'équilibre"


def balance_label(solde: Balance, names: Mapping[PartnerKey, str]) -> str:
    """Le solde tel que l'écran et l'export l'affichent (D67) : « À l'équilibre » ou
    « Axel doit 205,44 € à Mathis ». `balance_sentence` garde l'écriture de
    `controle_comptes`, pour que ses comptes rendus restent comparables."""
    return SETTLED_LABEL if solde.is_settled else balance_sentence(solde, names)


# ---------------------------------------------------------------------------
# Règlements entre associés
# ---------------------------------------------------------------------------


def balance_after(positions: Mapping[PartnerKey, Position], settlement: SettlementInput) -> Balance:
    """Le solde une fois ce règlement fait : le payeur a « sorti » le montant, l'autre
    l'a « reçu » (sans partage, voir `position_details`)."""
    apres = dict(positions)
    payeur = apres[settlement.from_partner]
    receveur = apres[settlement.to_partner]
    apres[settlement.from_partner] = Position(payeur.paid_out + settlement.amount, payeur.received)
    apres[settlement.to_partner] = Position(
        receveur.paid_out, receveur.received + settlement.amount
    )
    return balance(apres)


def settlement_problem(
    settlement: SettlementInput,
    due: Balance,
    names: Mapping[PartnerKey, str],
    seen_due: Decimal | None = None,
) -> str | None:
    """Pourquoi ce règlement n'est pas possible avec le solde `due`, ou None (D53).

    Un règlement va de celui qui doit vers l'autre, et ne dépasse pas ce qui est dû : un
    règlement partiel est permis. `seen_due` : le montant dû affiché à l'ouverture de la
    page ; s'il a changé depuis (un autre règlement, même partiel, envoyé d'un autre
    onglet), le règlement est refusé. Le message s'affiche tel quel à l'écran.
    """
    if due.is_settled:
        return "Vous êtes à l'équilibre : il n'y a rien à régler."
    if (settlement.from_partner, settlement.to_partner) != (due.debtor, due.creditor):
        return (
            f"Le solde a changé : c'est {names[due.debtor]} qui doit de l'argent à "
            f"{names[due.creditor]}. Recharge la page."
        )
    if seen_due is not None and seen_due != due.amount:
        return (
            f"Le solde a changé depuis l'ouverture de la page : {names[due.debtor]} doit "
            f"maintenant {format_euros(due.amount)} à {names[due.creditor]}. Recharge la page."
        )
    if settlement.amount <= 0:
        return "Le règlement doit être plus grand que 0 €."
    if settlement.amount > due.amount:
        return (
            f"{names[due.debtor]} doit {format_euros(due.amount)} à {names[due.creditor]} : "
            f"un règlement ne peut pas dépasser ce montant. Corrige le montant."
        )
    return None


@dataclass(frozen=True)
class SalePartnerShare:
    """Ce qu'un associé a avancé et reçu pour un véhicule vendu, et ce qui lui revient."""

    advanced: Decimal
    margin_share: Decimal
    received: Decimal

    @property
    def total_share(self) -> Decimal:
        """Part totale : ce qu'il a avancé + la moitié de la marge."""
        return self.advanced + self.margin_share


@dataclass(frozen=True)
class SaleRecap:
    """Le tableau d'une vente (exemple 1 du cahier des charges) et le règlement qu'elle
    demande à elle seule, sans tenir compte du reste du garage (`recap_balance`)."""

    margin: Decimal
    shares: Mapping[PartnerKey, SalePartnerShare]
    balance: Balance


def sale_recap(
    cost: VehicleCost,
    sale: SaleInput,
    deposits: Iterable[DepositInput],
    partners: Sequence[PartnerKey],
) -> SaleRecap:
    """Récapitulatif d'une vente non annulée. `deposits` : les acomptes de ce véhicule.

    Reçu par un associé : ses encaissements, ses acomptes déduits ou conservés, sa moitié
    de la reprise. Le règlement du véhicule seul se lit dans ce tableau (`recap_balance`).
    """
    acomptes = tuple(deposits)
    marge = vehicle_margin(cost.total, [sale], acomptes)
    moities = margin_per_partner(marge, partners)
    encaisse = dict.fromkeys(partners, ZERO)
    for encaissement in sale.receipts:
        encaisse[encaissement.partner] += encaissement.amount
    for acompte in acomptes:
        if acompte.status in DEPOSIT_STATUSES_RECEIVED:
            encaisse[acompte.partner] += acompte.amount
    reprise = split_equally(sale.trade_in_value, partners)
    parts = {
        associe: SalePartnerShare(
            cost.by_partner.get(associe, ZERO),
            moities[associe],
            encaisse[associe] + reprise[associe],
        )
        for associe in partners
    }
    return SaleRecap(margin=marge, shares=parts, balance=recap_balance(parts))


def recap_balance(shares: Mapping[PartnerKey, SalePartnerShare]) -> Balance:
    """Le règlement que demande une vente à elle seule, lu dans son tableau (D88).

    Celui qui a reçu moins que sa part totale attend la différence de l'autre. Les parts
    totales font ce que la vente a rapporté (reprise comprise), donc ce qui manque à l'un
    est exactement ce que l'autre a reçu en trop : le montant tombe juste au centime, sans
    arrondi. Il peut différer d'un centime du solde du garage (`balance`), qui arrondit le
    demi-centime en faveur de celui qui a avancé (D33) quand la marge est impaire.
    """
    if len(shares) != 2:
        raise ValueError("Le solde se calcule entre exactement deux associés.")
    (premier, part_1), (second, _) = shares.items()
    reste = part_1.total_share - part_1.received  # ce qui revient encore au premier
    if reste > ZERO:
        return Balance(debtor=second, creditor=premier, amount=reste)
    if reste < ZERO:
        return Balance(debtor=premier, creditor=second, amount=-reste)
    return Balance(debtor=None, creditor=None, amount=ZERO)


# ---------------------------------------------------------------------------
# Bilan (D59 à D62)
# ---------------------------------------------------------------------------

# Statut « Abandonné » (même valeur que `vehicles.models.Vehicle.Status.ABANDONED`).
VEHICLE_ABANDONED = "ABANDONED"

# Où le bilan range un véhicule.
GROUP_SOLD = "sold"  # vente non annulée : il a une marge
GROUP_ABANDONED = "abandoned"  # abandonné, sans vente : il a une perte
GROUP_GARAGE = "garage"  # au garage : son coût est de l'argent immobilisé
# Fiche à la corbeille dont les dépenses ne le sont pas : son coût reste compté (comme
# dans `controle_comptes`), avec l'argent encore dans le garage.
GROUP_TRASH = "trash"


@dataclass(frozen=True)
class VehicleState:
    """Ce que le bilan sait d'un véhicule : son statut, sa date d'achat, s'il est abandonné
    la date de son dernier passage en « Abandonné » (D59), et si sa fiche est à la
    corbeille. Une fiche à la corbeille garde sa date d'achat : vendue, sa durée reste
    connue."""

    status: str
    purchased_on: dt.date | None = None
    abandoned_on: dt.date | None = None
    in_trash: bool = False


@dataclass(frozen=True)
class VehicleReportRow:
    """Une ligne du bilan par véhicule.

    `margin` pour un véhicule vendu (D32), `loss` pour un véhicule abandonné (D59) ;
    `days` : durée de l'achat à la vente.
    """

    vehicle: VehicleKey
    group: str
    cost: VehicleCost
    kept_deposits: Decimal
    sale: SaleInput | None = None
    margin: Decimal | None = None
    loss: Decimal | None = None
    days: int | None = None
    abandoned_on: dt.date | None = None

    @property
    def result(self) -> Decimal | None:
        """Ce que le véhicule a rapporté : sa marge s'il est vendu, sa perte (en négatif)
        s'il est abandonné, None s'il est encore au garage."""
        if self.margin is not None:
            return self.margin
        if self.loss is not None:
            return -self.loss
        return None


def vehicle_report(
    expenses: Iterable[ExpenseInput],
    sales: Iterable[SaleInput],
    deposits: Iterable[DepositInput],
    vehicles: Mapping[VehicleKey, VehicleState],
) -> list[VehicleReportRow]:
    """Le bilan de chaque véhicule qui a de l'argent (coût, vente ou acompte reçu), par clé.

    Un véhicule est vendu s'il a une vente non annulée, quel que soit son statut ni l'état de
    sa fiche (D22) ; sinon à la corbeille si sa fiche l'est (ou s'il est absent de
    `vehicles`) ; sinon abandonné si son statut l'est ; sinon au garage. Perte d'un
    abandonné = coût de revient − acomptes conservés (D59).
    """
    couts = vehicle_costs(expenses)
    ventes: dict[VehicleKey, list[SaleInput]] = defaultdict(list)
    for vente in sales:
        ventes[vente.vehicle].append(vente)
    acomptes: dict[VehicleKey, list[DepositInput]] = defaultdict(list)
    for acompte in deposits:
        acomptes[acompte.vehicle].append(acompte)

    cles = {cle for cle in couts if cle is not None}
    cles |= {cle for cle, liste in ventes.items() if active_sale(liste) is not None}
    cles |= {
        cle
        for cle, liste in acomptes.items()
        if any(a.status in DEPOSIT_STATUSES_RECEIVED for a in liste)
    }
    cles.discard(None)

    lignes = []
    for cle in sorted(cles):
        cout = couts.get(cle, VehicleCost())
        conserves = kept_deposits_total(acomptes[cle])
        etat = vehicles.get(cle)
        vente = active_sale(ventes[cle])
        if vente is not None:
            achat = etat.purchased_on if etat is not None else None
            lignes.append(
                VehicleReportRow(
                    vehicle=cle,
                    group=GROUP_SOLD,
                    cost=cout,
                    kept_deposits=conserves,
                    sale=vente,
                    margin=vehicle_margin(cout.total, [vente], acomptes[cle]),
                    days=(vente.date - achat).days if achat and vente.date else None,
                )
            )
        elif etat is None or etat.in_trash:
            lignes.append(
                VehicleReportRow(vehicle=cle, group=GROUP_TRASH, cost=cout, kept_deposits=conserves)
            )
        elif etat.status == VEHICLE_ABANDONED:
            lignes.append(
                VehicleReportRow(
                    vehicle=cle,
                    group=GROUP_ABANDONED,
                    cost=cout,
                    kept_deposits=conserves,
                    loss=cout.total - conserves,
                    abandoned_on=etat.abandoned_on,
                )
            )
        else:
            lignes.append(
                VehicleReportRow(
                    vehicle=cle, group=GROUP_GARAGE, cost=cout, kept_deposits=conserves
                )
            )
    return lignes


def report_margins(rows: Iterable[VehicleReportRow]) -> dict[VehicleKey, Decimal]:
    """Marge de chaque véhicule vendu, lue dans le bilan par véhicule : c'est le seul chemin
    de calcul de la marge (D68), pour les cartes « Vendus » comme pour `controle_comptes`."""
    return {ligne.vehicle: ligne.margin for ligne in rows if ligne.margin is not None}


@dataclass(frozen=True)
class MoneyBuckets:
    """Où est rangé chaque euro du garage, depuis le début (contrôle « chaque euro est
    rangé »).

    `pending_deposits` : acomptes reçus qui ne sont encore dans aucune marge ni perte
    (en cours, ou conservés sur un véhicule ni vendu ni abandonné).
    """

    margins: Decimal
    losses: Decimal
    garage_expenses: Decimal
    in_garage: Decimal
    in_trash: Decimal
    pending_deposits: Decimal

    @property
    def profit(self) -> Decimal:
        """Bénéfice cumulé = marges des ventes − pertes des abandons − dépenses du garage."""
        return self.margins - self.losses - self.garage_expenses

    @property
    def vehicles_in_garage(self) -> Decimal:
        """Coût des véhicules au garage, fiches à la corbeille comprises."""
        return self.in_garage + self.in_trash

    @property
    def still_in_garage(self) -> Decimal:
        """Argent encore dans le garage : véhicules au garage + fiches à la corbeille
        − acomptes en attente (D61)."""
        return self.in_garage + self.in_trash - self.pending_deposits


def money_buckets(
    rows: Sequence[VehicleReportRow],
    expenses: Iterable[ExpenseInput],
    deposits: Iterable[DepositInput],
) -> MoneyBuckets:
    """Range l'argent du garage dans les cases du bilan."""

    def somme(groupe: str, valeur) -> Decimal:
        return sum((valeur(ligne) for ligne in rows if ligne.group == groupe), ZERO)

    termines = {ligne.vehicle for ligne in rows if ligne.group in (GROUP_SOLD, GROUP_ABANDONED)}
    en_attente = sum(
        (
            a.amount
            for a in deposits
            if a.status == DEPOSIT_ACTIVE
            or (a.status == DEPOSIT_KEPT and a.vehicle not in termines)
        ),
        ZERO,
    )
    return MoneyBuckets(
        margins=somme(GROUP_SOLD, lambda ligne: ligne.margin),
        losses=somme(GROUP_ABANDONED, lambda ligne: ligne.loss),
        garage_expenses=vehicle_costs(expenses).get(None, VehicleCost()).total,
        in_garage=somme(GROUP_GARAGE, lambda ligne: ligne.cost.total),
        in_trash=somme(GROUP_TRASH, lambda ligne: ligne.cost.total),
        pending_deposits=en_attente,
    )


def buckets_problems(positions: Mapping[PartnerKey, Position], buckets: MoneyBuckets) -> list[str]:
    """Chaque euro est rangé : la somme des positions vaut l'argent encore dans le garage
    moins le bénéfice cumulé. Sinon, un montant manque au bilan ou y est compté deux fois."""
    total = sum((p.value for p in positions.values()), ZERO)
    attendu = buckets.still_in_garage - buckets.profit
    if total == attendu:
        return []
    return [
        f"argent mal rangé : les positions font {format_euros(total)}, le bilan "
        f"{format_euros(attendu)} (encore dans le garage − bénéfice)"
    ]


@dataclass(frozen=True)
class PartnerReport:
    """Le bilan d'un associé (D61), qui se lit comme une addition :
    mis dans le garage + part du bénéfice = ce qui lui revient
    = déjà revenu + encore dans le garage + solde (à recevoir positif, à verser négatif).

    `still_in_garage` porte l'écart d'arrondi (`rounding_gap`, au plus un centime, D33),
    pour que l'addition tombe juste à l'écran.
    """

    put_in: Decimal
    profit_share: Decimal
    already_back: Decimal
    still_in_garage: Decimal
    balance: Decimal
    rounding_gap: Decimal
    # Le détail de « déjà revenu », pour l'écran (D61) : encaissé + reçus − versés.
    sales_received: Decimal = ZERO
    settlements_received: Decimal = ZERO
    settlements_paid: Decimal = ZERO

    @property
    def due_to_partner(self) -> Decimal:
        """Ce qui lui revient = mis dans le garage + part du bénéfice."""
        return self.put_in + self.profit_share

    @property
    def settlements_net(self) -> Decimal:
        """Règlements reçus − versés : positif s'il a surtout reçu, négatif s'il a surtout versé."""
        return self.settlements_received - self.settlements_paid


def partner_report(
    details: Mapping[PartnerKey, PositionDetail],
    buckets: MoneyBuckets,
    partners: Sequence[PartnerKey],
) -> dict[PartnerKey, PartnerReport]:
    """Le bilan de chaque associé (D61).

    - mis dans le garage : dépenses payées − remboursements reçus ;
    - déjà revenu : ventes, acomptes et reprises encaissés + règlements reçus − versés ;
    - part du bénéfice : moitié du bénéfice cumulé ;
    - encore dans le garage : moitié de l'argent encore dans le garage ;
    - solde : ce que `balance` dit qu'il doit recevoir (+) ou verser (−).
    """
    solde = balance({associe: details[associe].position for associe in partners})
    parts_benefice = split_equally(buckets.profit, partners)
    parts_garage = split_equally(buckets.still_in_garage, partners)
    bilans = {}
    for associe in partners:
        detail = details[associe]
        mis = detail.expenses_paid - detail.refunds_received
        revenu = detail.sales_received + detail.settlements_received - detail.settlements_paid
        # Solde signé : positif s'il doit recevoir, négatif s'il doit verser.
        if associe == solde.creditor:
            a_recevoir = solde.amount
        elif associe == solde.debtor:
            a_recevoir = -solde.amount
        else:
            a_recevoir = ZERO
        encore_au_garage = mis + parts_benefice[associe] - revenu - a_recevoir
        bilans[associe] = PartnerReport(
            put_in=mis,
            profit_share=parts_benefice[associe],
            already_back=revenu,
            still_in_garage=encore_au_garage,
            balance=a_recevoir,
            rounding_gap=encore_au_garage - parts_garage[associe],
            sales_received=detail.sales_received,
            settlements_received=detail.settlements_received,
            settlements_paid=detail.settlements_paid,
        )
    return bilans


def partner_report_problems(
    reports: Mapping[PartnerKey, PartnerReport], names: Mapping[PartnerKey, str]
) -> list[str]:
    """Pour chaque associé : ce qui lui revient = déjà revenu + encore dans le garage
    (sa moitié) ± solde, à un centime près (arrondis D33 et partage du bénéfice).

    Ce n'est pas un second contrôle indépendant : si `buckets_problems` ne trouve rien,
    celui-ci non plus. Il montre l'erreur associé par associé, comme on la lit à l'écran.
    """
    problemes = []
    for associe, bilan in reports.items():
        if abs(bilan.rounding_gap) <= CENT:
            continue
        attendu = bilan.already_back + (bilan.still_in_garage - bilan.rounding_gap) + bilan.balance
        revient = format_euros(bilan.due_to_partner)
        problemes.append(
            f"{names.get(associe, associe)} : ce qui lui revient ({revient}) ne fait pas "
            f"déjà revenu + encore dans le garage ± solde ({format_euros(attendu)})"
        )
    return problemes


@dataclass(frozen=True)
class YearReport:
    """Le bilan d'une année (D60) : chaque montant est rangé dans l'année de sa date
    (dépense, vente, dernier passage en « Abandonné »)."""

    year: int
    invested: Decimal
    sales_total: Decimal
    margins: Decimal
    losses: Decimal
    garage_expenses: Decimal
    average_days: int | None
    shares: Mapping[PartnerKey, Decimal]

    @property
    def profit(self) -> Decimal:
        """Bénéfice de l'année = marges des ventes − pertes des abandons − dépenses du garage."""
        return self.margins - self.losses - self.garage_expenses

    @property
    def abandons_result(self) -> Decimal:
        """Ce que les abandons de l'année ont rapporté : l'opposé des pertes (négatif = perte,
        positif si des acomptes conservés dépassent le coût)."""
        return -self.losses


def year_report(
    year: int,
    rows: Sequence[VehicleReportRow],
    expenses: Sequence[ExpenseInput],
    partners: Sequence[PartnerKey],
) -> YearReport:
    """Le bilan de l'année `year`.

    Total investi : coût net des dépenses non personnelles datées de l'année (véhicules et
    garage). Total des ventes : prix des ventes non annulées de l'année, reprise comprise.
    Durée moyenne : de l'achat à la vente des véhicules vendus dans l'année, arrondie au jour.
    `partners` vide : la part de chacun n'est pas calculée (associés à corriger).
    """
    couts_de_l_annee = [
        cout
        for depense in expenses
        if depense.date is not None and depense.date.year == year
        for cout in expense_line_costs(depense)
    ]
    investi = sum((cout.net for cout in couts_de_l_annee), ZERO)
    garage = sum((cout.net for cout in couts_de_l_annee if cout.vehicle is None), ZERO)
    vendus = sold_in_year(rows, year)
    abandonnes = abandoned_in_year(rows, year)
    durees = [ligne.days for ligne in vendus if ligne.days is not None]
    moyenne = None
    if durees:
        moyenne = int((Decimal(sum(durees)) / len(durees)).quantize(Decimal(1), ROUND_HALF_UP))
    marges = sum((ligne.margin for ligne in vendus), ZERO)
    pertes = sum((ligne.loss for ligne in abandonnes), ZERO)
    return YearReport(
        year=year,
        invested=investi,
        sales_total=sum((ligne.sale.price for ligne in vendus), ZERO),
        margins=marges,
        losses=pertes,
        garage_expenses=garage,
        average_days=moyenne,
        shares=split_equally(marges - pertes - garage, partners) if partners else {},
    )


def sold_in_year(rows: Iterable[VehicleReportRow], year: int) -> list[VehicleReportRow]:
    """Les véhicules vendus dans l'année, dans l'ordre des ventes."""
    vendus = [
        ligne
        for ligne in rows
        if ligne.group == GROUP_SOLD and ligne.sale.date and ligne.sale.date.year == year
    ]
    return sorted(vendus, key=lambda ligne: ligne.sale.date)


def abandoned_in_year(rows: Iterable[VehicleReportRow], year: int) -> list[VehicleReportRow]:
    """Les véhicules abandonnés dans l'année (D59), dans l'ordre des abandons."""
    abandonnes = [
        ligne
        for ligne in rows
        if ligne.group == GROUP_ABANDONED
        and ligne.abandoned_on is not None
        and ligne.abandoned_on.year == year
    ]
    return sorted(abandonnes, key=lambda ligne: ligne.abandoned_on)


@dataclass(frozen=True)
class AbandonedDisplay:
    """Ce que la carte d'un véhicule abandonné affiche : son coût de revient, ses acomptes
    conservés, et sa perte (D59) avec la part de chaque associé, sans signe (`MarginDisplay`
    : `is_loss` faux et un montant non nul quand l'acompte conservé dépasse le coût)."""

    cost: Decimal
    kept_deposits: Decimal
    result: MarginDisplay


def abandoned_display(
    row: VehicleReportRow | None, partners: Sequence[PartnerKey]
) -> AbandonedDisplay:
    """La perte d'un véhicule abandonné, lue dans sa ligne du bilan (D59 : coût de revient −
    acomptes conservés), et la moitié de chacun. Sans ligne (aucun argent sur le véhicule) :
    aucune perte. Sans deux associés actifs (`partners` vide) : pas de parts."""
    if row is None:
        return AbandonedDisplay(ZERO, ZERO, margin_display(ZERO, {}))
    resultat = row.result
    parts = margin_per_partner(resultat, partners) if partners else {}
    return AbandonedDisplay(row.cost.total, row.kept_deposits, margin_display(resultat, parts))


@dataclass(frozen=True)
class InvestedBreakdown:
    """D'où vient « Investi en <année> », vu depuis le tableau des véhicules de l'année.

    Le tableau montre le coût de revient complet des véhicules vendus ou abandonnés dans
    l'année, alors que « Investi » compte l'argent dépensé dans l'année. Pour passer de
    l'un à l'autre :
    - `listed_costs` : coût de revient des véhicules du tableau, toutes années confondues ;
    - `garage` : dépenses du garage de l'année ;
    - `other_vehicles` : dépensé dans l'année pour des véhicules qui ne sont pas dans le
      tableau (encore au garage, ou vendus ou abandonnés une autre année) ;
    - `other_years` : dépensé une autre année pour les véhicules du tableau, à retirer.
    """

    listed_costs: Decimal
    garage: Decimal
    other_vehicles: Decimal
    other_years: Decimal

    @property
    def total(self) -> Decimal:
        """Égal à `YearReport.invested` de la même année."""
        return self.listed_costs + self.garage + self.other_vehicles - self.other_years


def invested_breakdown(
    year: int, rows: Iterable[VehicleReportRow], expenses: Iterable[ExpenseInput]
) -> InvestedBreakdown:
    """Le détail de « Investi en <année> » ligne par ligne (voir `InvestedBreakdown`)."""
    du_tableau = {ligne.vehicle for ligne in sold_in_year(rows, year)}
    du_tableau |= {ligne.vehicle for ligne in abandoned_in_year(rows, year)}
    couts_du_tableau = garage = autres_vehicules = autres_annees = ZERO
    for depense in expenses:
        de_l_annee = depense.date is not None and depense.date.year == year
        for cout in expense_line_costs(depense):
            if cout.vehicle in du_tableau:
                couts_du_tableau += cout.net
                if not de_l_annee:
                    autres_annees += cout.net
            elif de_l_annee and cout.vehicle is None:
                garage += cout.net
            elif de_l_annee:
                autres_vehicules += cout.net
    return InvestedBreakdown(
        listed_costs=couts_du_tableau,
        garage=garage,
        other_vehicles=autres_vehicules,
        other_years=autres_annees,
    )


def report_years(rows: Iterable[VehicleReportRow], expenses: Iterable[ExpenseInput]) -> list[int]:
    """Les années qui ont une dépense, une vente ou un abandon, la plus récente d'abord :
    ce sont les choix du sélecteur d'année du bilan."""
    annees = {d.date.year for d in expenses if d.date is not None and not d.is_personal}
    for ligne in rows:
        if ligne.group == GROUP_SOLD and ligne.sale.date:
            annees.add(ligne.sale.date.year)
        if ligne.group == GROUP_ABANDONED and ligne.abandoned_on:
            annees.add(ligne.abandoned_on.year)
    return sorted(annees, reverse=True)


@dataclass(frozen=True)
class SignedAmount:
    """Un montant que l'écran affiche sans signe moins (D61) : le gabarit choisit ses mots
    d'après `is_negative` (« Perte de 300,00 € », « versé 205,44 € ») et montre `shown`."""

    amount: Decimal

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def shown(self) -> Decimal:
        return abs(self.amount)


# ---------------------------------------------------------------------------
# Contrôles de cohérence (utilisés par `controle_comptes`)
# ---------------------------------------------------------------------------


def expense_problems(expense: ExpenseInput) -> list[str]:
    """Incohérences d'une dépense : pas de ligne, paiements différents du montant payé,
    remboursement supérieur au coût d'une ligne."""
    problemes = []
    if not expense.lines:
        problemes.append("aucune ligne")
    paye = sum((paiement.amount for paiement in expense.payments), ZERO)
    if paye != expense.total_paid:
        problemes.append(
            f"les paiements ({format_euros(paye)}) ne font pas le montant payé "
            f"({format_euros(expense.total_paid)})"
        )
    if expense.lines and not expense.is_personal:
        for cout in expense_line_costs(expense):
            if cout.refunded > cout.allocated:
                problemes.append(
                    f"remboursement ({format_euros(cout.refunded)}) plus grand que le "
                    f"coût de la ligne ({format_euros(cout.allocated)})"
                )
    return problemes


def sale_problems(sale: SaleInput, applied_deposits: Iterable[DepositInput] = ()) -> list[str]:
    """Incohérence d'une vente non annulée : encaissements + acomptes appliqués + reprise
    différents du prix."""
    if sale.cancelled:
        return []
    encaisse = sum((e.amount for e in sale.receipts), ZERO)
    acomptes = sum((a.amount for a in applied_deposits if a.status == DEPOSIT_APPLIED), ZERO)
    total = encaisse + acomptes + sale.trade_in_value
    if total != sale.price:
        return [
            f"encaissements + acomptes + reprise font {format_euros(total)} "
            f"au lieu du prix ({format_euros(sale.price)})"
        ]
    return []


def trade_in_problems(
    sale: SaleInput,
    purchases: Sequence[ExpenseInput],
    partners: Sequence[PartnerKey],
    names: Mapping[PartnerKey, str] | None = None,
) -> list[str]:
    """Incohérences d'une reprise : elle n'est neutre pour le solde que si le véhicule repris
    a un seul achat, du montant de la reprise, payé moitié chacun comme la reprise est
    « reçue » (D51). `purchases` : les dépenses d'achat du véhicule repris, hors corbeille ;
    `names` : le nom des associés, pour le message."""
    if sale.cancelled:
        return []
    if sale.trade_in_vehicle is None:
        if sale.trade_in_value:
            return ["valeur de reprise sans véhicule repris"]
        return []
    if len(purchases) != 1:
        return [f"le véhicule repris a {len(purchases)} achat(s) au lieu d'un seul"]
    (achat,) = purchases
    problemes = []
    if achat.is_personal:
        problemes.append("l'achat du véhicule repris est marqué comme achat personnel")
    if achat.total_paid != sale.trade_in_value:
        problemes.append(
            f"l'achat du véhicule repris ({format_euros(achat.total_paid)}) ne fait pas "
            f"la valeur de reprise ({format_euros(sale.trade_in_value)})"
        )
    attendu = {a: m for a, m in split_equally(sale.trade_in_value, partners).items() if m}
    if amounts_by_partner(achat.payments) != attendu:
        noms = names or {}
        parts = ", ".join(f"{noms.get(a, a)} {format_euros(m)}" for a, m in attendu.items())
        problemes.append(
            f"l'achat du véhicule repris n'est pas payé comme la reprise est partagée ({parts})"
        )
    return problemes


def reservation_problems(is_reserved: bool, active_deposits: int) -> list[str]:
    """Incohérence entre le statut d'un véhicule et ses acomptes en cours : un véhicule
    réservé en a exactement un, un véhicule qui ne l'est pas n'en a aucun (D47)."""
    if is_reserved and active_deposits != 1:
        return [f"réservé avec {active_deposits} acompte(s) en cours au lieu d'un seul"]
    if not is_reserved and active_deposits:
        return ["acompte en cours alors que le véhicule n'est pas réservé"]
    return []


def sold_status_problems(is_sold: bool, active_sales: int) -> list[str]:
    """Incohérence entre le statut d'un véhicule et ses ventes : un véhicule vendu a une
    vente non annulée, un véhicule qui ne l'est pas n'en a aucune."""
    if is_sold and not active_sales:
        return ["vendu sans vente enregistrée"]
    if not is_sold and active_sales:
        return ["vente en cours alors que le véhicule n'est pas vendu"]
    return []


def deposit_receipts_problems(active_receipts: int, legacy_filled: bool) -> list[str]:
    """Incohérence d'un acompte avec ses versements (D89) : aucun versement hors corbeille
    (il ne compterait nulle part), ou l'ancien montant encore rempli (il devait être recopié
    en versement par la migration `sales.0005`, puis vidé)."""
    problemes = []
    if not active_receipts:
        problemes.append("aucun versement : l'acompte ne compte nulle part")
    if legacy_filled:
        problemes.append("ancien montant encore rempli (non recopié en versement)")
    return problemes


def deposit_problems(deposit: DepositInput, sale: SaleInput | None) -> list[str]:
    """Incohérence d'un acompte déduit d'une vente (`APPLIED`) : vente introuvable,
    annulée, ou vente d'un autre véhicule. Il compte pourtant dans ce qu'a reçu l'associé."""
    if deposit.status != DEPOSIT_APPLIED:
        return []
    if sale is None:
        return ["acompte déduit d'une vente introuvable"]
    if sale.cancelled:
        return ["acompte déduit d'une vente annulée (à passer en « conservé » ou « rendu »)"]
    if sale.vehicle != deposit.vehicle:
        return ["acompte déduit de la vente d'un autre véhicule"]
    return []


def partner_problems(
    partners: Sequence[PartnerKey],
    expenses: Iterable[ExpenseInput] = (),
    sales: Iterable[SaleInput] = (),
    deposits: Iterable[DepositInput] = (),
    settlements: Iterable[SettlementInput] = (),
    names: Mapping[PartnerKey, str] | None = None,
) -> list[str]:
    """Le partage suppose exactement deux associés actifs, et que tout l'argent passe
    par eux : signale un nombre d'associés différent de deux et chaque associé inconnu
    (inactif) qui paie, reçoit ou règle quelque chose."""
    names = names or {}
    problemes = []
    if len(partners) != 2:
        problemes.append(f"il faut exactement deux associés actifs, il y en a {len(partners)}")

    utilises: set[PartnerKey] = set()
    for depense in expenses:
        if depense.is_personal:
            continue
        utilises.update(paiement.partner for paiement in depense.payments)
        utilises.update(r.partner for ligne in depense.lines for r in ligne.refunds)
    for vente in sales:
        if not vente.cancelled:
            utilises.update(encaissement.partner for encaissement in vente.receipts)
    utilises.update(a.partner for a in deposits if a.status in DEPOSIT_STATUSES_RECEIVED)
    for reglement in settlements:
        utilises.update((reglement.from_partner, reglement.to_partner))

    for associe in sorted(utilises - set(partners), key=str):
        nom = names.get(associe, associe)
        problemes.append(f"{nom} n'est plus un associé actif mais a de l'argent dans les comptes")
    return problemes

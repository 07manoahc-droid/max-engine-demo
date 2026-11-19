"""Lecture de la base pour les calculs d'argent.

Ce module traduit les données de la base en dataclasses simples, puis appelle les
fonctions pures de `accounting/services.py`. Il ne calcule rien lui-même.
Les éléments à la corbeille sont exclus (gestionnaires `objects`) ; les ventes
annulées et les achats personnels sont transmis tels quels, les services les écartent.
"""

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from core.models import ExpenseCategory, Partner
from expenses.models import Expense, ExpenseLine, ExpensePayment, Refund
from sales.models import Deposit, DepositReceipt, Sale
from vehicles.models import StatusChange, Vehicle

from . import services
from .models import Settlement


@dataclass(frozen=True)
class AccountsData:
    """Toutes les données d'argent du garage, prêtes pour les services."""

    partners: tuple[int, ...]
    expenses: tuple[services.ExpenseInput, ...]
    sales: tuple[services.SaleInput, ...]
    deposits: tuple[services.DepositInput, ...]
    settlements: tuple[services.SettlementInput, ...]
    # Nom de chaque associé, actif ou non, pour les messages.
    partner_names: dict[int, str]
    # Statut de chaque véhicule hors corbeille, pour les contrôles.
    vehicle_statuses: dict[int, str]
    # Dépenses d'achat de chaque véhicule repris, pour vérifier que la reprise est neutre.
    trade_in_purchases: dict[int, tuple[int, ...]]
    # Pour chaque acompte (D89) : ses versements hors corbeille, si son ancien montant est
    # encore rempli, son véhicule et son état.
    deposit_checks: dict[int, "DepositCheck"] = field(default_factory=dict)


@dataclass(frozen=True)
class DepositCheck:
    """Ce que `controle_comptes` vérifie d'un acompte, lu sur la table des acomptes : un
    acompte sans versement n'a aucun `DepositInput`, mais il existe et peut être en cours."""

    receipts: int
    legacy_filled: bool
    vehicle: int
    status: str


def active_partners() -> list[Partner]:
    return list(Partner.objects.filter(is_active=True).order_by("id"))


def load_expenses(expense_ids=None) -> list[services.ExpenseInput]:
    """Dépenses non supprimées, avec leurs payeurs (dans l'ordre des associés) et leurs
    lignes et remboursements non supprimés. Les paiements à la corbeille sont écartés par
    le gestionnaire par défaut (D40). `expense_ids` : seulement ces dépenses."""
    depenses = Expense.objects.order_by("date", "id")
    if expense_ids is not None:
        depenses = depenses.filter(pk__in=expense_ids)
    depenses = depenses.prefetch_related(
        "payments",
        Prefetch(
            "lines",
            queryset=ExpenseLine.objects.order_by("id").prefetch_related(
                Prefetch("refunds", queryset=Refund.objects.order_by("id"))
            ),
        ),
    )
    return [
        services.ExpenseInput(
            total_paid=depense.total_paid,
            payments=tuple(
                services.Payment(paiement.partner_id, paiement.amount)
                for paiement in sorted(depense.payments.all(), key=lambda p: p.partner_id)
            ),
            lines=tuple(
                services.LineInput(
                    quantity=ligne.quantity,
                    unit_price=ligne.unit_price,
                    vehicle=ligne.vehicle_id,
                    refunds=tuple(
                        services.RefundInput(r.refunded_to_id, r.amount)
                        for r in ligne.refunds.all()
                    ),
                    key=ligne.pk,
                )
                for ligne in depense.lines.all()
            ),
            is_personal=depense.is_personal,
            key=depense.pk,
            date=depense.date,
        )
        for depense in depenses
    ]


def load_accounts() -> AccountsData:
    """Lit en une fois tout ce qui sert aux calculs d'argent."""
    ventes = list(Sale.objects.order_by("date", "id").prefetch_related("receipts"))
    return AccountsData(
        partners=tuple(p.pk for p in active_partners()),
        expenses=tuple(load_expenses()),
        sales=tuple(
            services.SaleInput(
                price=vente.price,
                receipts=tuple(
                    services.Receipt(e.partner_id, e.amount) for e in vente.receipts.all()
                ),
                trade_in_value=vente.trade_in_value,
                vehicle=vente.vehicle_id,
                cancelled=vente.is_cancelled,
                key=vente.pk,
                date=vente.date,
                trade_in_vehicle=vente.trade_in_vehicle_id,
            )
            for vente in ventes
        ),
        deposits=tuple(load_deposits()),
        settlements=tuple(
            services.SettlementInput(r.from_partner_id, r.to_partner_id, r.amount)
            for r in Settlement.objects.order_by("date", "id")
        ),
        partner_names={p.pk: p.name for p in Partner.objects.all()},
        vehicle_statuses=dict(Vehicle.objects.values_list("pk", "status")),
        trade_in_purchases=trade_in_purchases(
            vente.trade_in_vehicle_id for vente in ventes if vente.trade_in_vehicle_id
        ),
        deposit_checks=deposit_checks(),
    )


def load_deposits(deposit_ids=None) -> list[services.DepositInput]:
    """Un `DepositInput` par versement d'acompte hors corbeille, avec l'état, le véhicule et
    la vente de son acompte (D89). `deposit_ids` : seulement ces acomptes."""
    versements = DepositReceipt.objects.select_related("deposit").order_by(
        "deposit__date", "deposit_id", "date", "id"
    )
    if deposit_ids is not None:
        versements = versements.filter(deposit_id__in=deposit_ids)
    return [
        services.DepositInput(
            v.partner_id,
            v.amount,
            v.deposit.status,
            vehicle=v.deposit.vehicle_id,
            sale=v.deposit.sale_id,
            key=v.deposit_id,
        )
        for v in versements
    ]


def deposit_totals() -> dict[int, Decimal]:
    """Le montant de chaque acompte : la somme de ses versements hors corbeille (D89)."""
    montants: dict[int, list[Decimal]] = defaultdict(list)
    for acompte, montant in DepositReceipt.objects.values_list("deposit_id", "amount"):
        montants[acompte].append(montant)
    return {acompte: services.deposit_total(liste) for acompte, liste in montants.items()}


def deposit_checks() -> dict[int, DepositCheck]:
    """Pour chaque acompte : son nombre de versements hors corbeille, si son ancien montant
    est encore rempli (il doit être vide depuis `sales.0005`), son véhicule et son état."""
    lignes = Deposit.objects.annotate(
        versements=Count("receipts", filter=Q(receipts__deleted_at__isnull=True))
    ).values_list("pk", "versements", "legacy_amount", "legacy_received_by", "vehicle", "status")
    return {
        pk: DepositCheck(nombre, montant is not None or associe is not None, vehicule, etat)
        for pk, nombre, montant, associe, vehicule, etat in lignes
    }


def trade_in_purchases(vehicle_ids) -> dict[int, tuple[int, ...]]:
    """Pour chaque véhicule repris, ses dépenses d'achat hors corbeille (une en principe)."""
    achats: dict[int, list[int]] = {pk: [] for pk in vehicle_ids}
    lignes = ExpenseLine.objects.filter(
        vehicle__in=achats,
        category__code=ExpenseCategory.VEHICLE_PURCHASE,
        expense__isnull=False,
        expense__deleted_at__isnull=True,
    ).order_by("expense_id")
    for vehicule, depense in lignes.values_list("vehicle_id", "expense_id"):
        if depense not in achats[vehicule]:
            achats[vehicule].append(depense)
    return {vehicule: tuple(depenses) for vehicule, depenses in achats.items()}


def positions(data: AccountsData) -> dict[int, services.Position]:
    return services.partner_positions(
        data.partners,
        expenses=data.expenses,
        sales=data.sales,
        deposits=data.deposits,
        settlements=data.settlements,
    )


def vehicle_margins(report: "GarageReport") -> dict[int, Decimal]:
    """Marge de chaque véhicule qui a une vente non annulée, lue dans le bilan (D68)."""
    return services.report_margins(report.rows)


def sold_margins(vehicle_ids) -> dict[int, services.MarginDisplay]:
    """Marge et gain de chaque associé pour ces véhicules vendus, prêts à afficher.

    Les véhicules sans vente non annulée n'y sont pas. Si l'argent n'est pas partagé entre
    deux associés actifs (`controle_comptes` dit pourquoi), la marge est donnée sans les
    parts : un partage n'aurait pas de sens.
    """
    rapport = load_report()
    marges = vehicle_margins(rapport)
    associes = rapport.data.partners if rapport.partners is not None else ()
    return {
        pk: services.margin_display(
            marges[pk], services.margin_per_partner(marges[pk], associes) if associes else {}
        )
        for pk in vehicle_ids
        if pk in marges
    }


@dataclass(frozen=True)
class AbandonedCard:
    """Un véhicule abandonné, sa date d'abandon (D59) et sa perte prête à afficher."""

    vehicle: Vehicle
    abandoned_on: dt.date | None
    loss: services.AbandonedDisplay


@dataclass(frozen=True)
class AbandonedList:
    """La liste « Véhicules abandonnés », le plus récent d'abord, et la perte de tous les
    abandons, celle que le bilan compte (`MoneyBuckets.losses`)."""

    cards: list[AbandonedCard]
    total: services.MarginDisplay


def abandoned_vehicles() -> AbandonedList:
    """Les véhicules abandonnés (hors corbeille) et leur perte, lue dans le bilan : les mêmes
    chiffres que « Bilan » et `controle_comptes`. Un véhicule abandonné sans argent y figure,
    sans perte."""
    rapport = load_report()
    lignes = {ligne.vehicle: ligne for ligne in rapport.rows}
    associes = rapport.data.partners if rapport.partners is not None else ()
    etats = None  # lus seulement pour un abandonné sans argent (absent du bilan)
    cartes = []
    for vehicule in Vehicle.objects.filter(status=Vehicle.Status.ABANDONED):
        ligne = lignes.get(vehicule.pk)
        # Une vente non annulée l'emporte sur le statut (D22) : ce véhicule est un vendu.
        if ligne is not None and ligne.group != services.GROUP_ABANDONED:
            continue
        if ligne is not None:
            abandon = ligne.abandoned_on
        else:
            etats = vehicle_states() if etats is None else etats
            abandon = etats[vehicule.pk].abandoned_on
        cartes.append(AbandonedCard(vehicule, abandon, services.abandoned_display(ligne, associes)))
    cartes.sort(key=lambda c: (c.abandoned_on or dt.date.min, c.vehicle.number), reverse=True)
    return AbandonedList(cartes, services.margin_display(-rapport.buckets.losses, {}))


def vehicle_cost(vehicle_id: int, expenses=None) -> services.VehicleCost:
    """Coût de revient d'un véhicule et investissement de chaque associé.

    Toutes les dépenses sont lues (ou reprises de `expenses`, déjà chargées) : une
    commande partagée entre plusieurs véhicules répartit ses frais de port sur toutes
    ses lignes.
    """
    expenses = load_expenses() if expenses is None else expenses
    return services.vehicle_costs(expenses).get(vehicle_id, services.VehicleCost())


def lines_cost(line_ids, expenses=None) -> services.VehicleCost:
    """Coût net de quelques lignes de dépense (ex. l'achat d'un véhicule) et part nette
    de chaque associé. `expenses` : dépenses déjà chargées par `load_expenses`."""
    expenses = load_expenses() if expenses is None else expenses
    cles = set(line_ids)
    couts = [
        cout
        for depense in expenses
        for cout in services.expense_line_costs(depense)
        if cout.line.key in cles
    ]
    return services.combined_cost(couts)


def line_costs(expenses=None) -> dict[int, services.LineCost]:
    """Coût de chaque ligne payée, par identifiant de ligne. Les lignes d'un achat
    personnel n'y sont pas : elles ne coûtent rien au garage."""
    expenses = load_expenses() if expenses is None else expenses
    return {
        cout.line.key: cout for depense in expenses for cout in services.expense_line_costs(depense)
    }


@dataclass(frozen=True)
class GarageOverview:
    """Ce que l'accueil et les cartes affichent : coût et investissement de chaque
    véhicule, coût des véhicules au garage, bénéfice de l'année (D60, D63).

    `garage_cost` est le chiffre du bilan, fiches à la corbeille comprises (D66) ;
    `in_trash` dit combien en vient de fiches à la corbeille.
    """

    costs: dict[int, services.VehicleCost]
    garage_cost: Decimal
    in_trash: Decimal
    year: int
    profit: services.SignedAmount


def garage_overview(year: int) -> GarageOverview:
    """Lit le bilan une seule fois : les coûts des cartes viennent de ses lignes."""
    rapport = load_report()
    annee = services.year_report(year, rapport.rows, rapport.data.expenses, ())
    return GarageOverview(
        costs={ligne.vehicle: ligne.cost for ligne in rapport.rows},
        garage_cost=rapport.buckets.vehicles_in_garage,
        in_trash=rapport.buckets.in_trash,
        year=year,
        profit=services.SignedAmount(annee.profit),
    )


def consistency_problems(data: AccountsData) -> list[str]:
    """Toutes les incohérences des comptes, en phrases (vide si tout va bien).
    Les règles sont dans `services` ; ici, on ne fait que les appliquer à chaque élément."""
    problemes = partner_problems(data)
    for depense in data.expenses:
        problemes += [
            f"dépense n°{depense.key} : {texte}" for texte in services.expense_problems(depense)
        ]
    ventes = {vente.key: vente for vente in data.sales}
    acomptes_par_vente: dict[int, list[services.DepositInput]] = defaultdict(list)
    # Un acompte a un `DepositInput` par versement : chacun de ses problèmes n'est dit qu'une fois.
    un_par_acompte: dict[int, services.DepositInput] = {}
    for versement in data.deposits:
        if versement.sale is not None:
            acomptes_par_vente[versement.sale].append(versement)
        un_par_acompte.setdefault(versement.key, versement)
    for cle, acompte in un_par_acompte.items():
        problemes += [
            f"acompte n°{cle} : {texte}"
            for texte in services.deposit_problems(acompte, ventes.get(acompte.sale))
        ]
    for cle, controle in sorted(data.deposit_checks.items()):
        problemes += [
            f"acompte n°{cle} : {texte}"
            for texte in services.deposit_receipts_problems(
                controle.receipts, controle.legacy_filled
            )
        ]
    depenses = {depense.key: depense for depense in data.expenses}
    for vente in data.sales:
        achats = [
            depenses[cle]
            for cle in data.trade_in_purchases.get(vente.trade_in_vehicle, ())
            if cle in depenses
        ]
        problemes += [
            f"vente n°{vente.key} : {texte}"
            for texte in services.sale_problems(vente, acomptes_par_vente[vente.key])
            + services.trade_in_problems(vente, achats, data.partners, data.partner_names)
        ]
    # Compté sur la table des acomptes, pas sur les versements : deux versements font un
    # acompte, et un acompte sans versement en reste un.
    acomptes_en_cours = Counter(
        c.vehicle for c in data.deposit_checks.values() if c.status == services.DEPOSIT_ACTIVE
    )
    ventes_en_cours = Counter(vente.vehicle for vente in data.sales if not vente.cancelled)
    noms = vehicle_names(data.vehicle_statuses)
    for vehicule, statut in data.vehicle_statuses.items():
        reserve = statut == Vehicle.Status.RESERVED
        vendu = statut == Vehicle.Status.SOLD
        problemes += [
            f"{noms[vehicule]} : {texte}"
            for texte in services.reservation_problems(reserve, acomptes_en_cours[vehicule])
            + services.sold_status_problems(vendu, ventes_en_cours[vehicule])
        ]
    return problemes


def partner_problems(data: AccountsData) -> list[str]:
    """Ce qui empêche de partager les comptes entre deux associés actifs."""
    return services.partner_problems(
        data.partners,
        expenses=data.expenses,
        sales=data.sales,
        deposits=data.deposits,
        settlements=data.settlements,
        names=data.partner_names,
    )


@dataclass(frozen=True)
class PartnerAccounts:
    """Le solde entre associés et d'où il vient, pour l'écran des comptes."""

    details: dict[int, services.PositionDetail]
    positions: dict[int, services.Position]
    balance: services.Balance
    partner_names: dict[int, str]


def partner_accounts() -> PartnerAccounts | None:
    """Positions, détail et solde de tout le garage.

    None si l'argent n'est pas partagé entre deux associés actifs (`controle_comptes` dit
    pourquoi) : un solde n'aurait alors pas de sens.
    """
    data = load_accounts()
    if partner_problems(data):
        return None
    details = services.position_details(
        data.partners,
        expenses=data.expenses,
        sales=data.sales,
        deposits=data.deposits,
        settlements=data.settlements,
    )
    positions_ = {associe: detail.position for associe, detail in details.items()}
    return PartnerAccounts(
        details=details,
        positions=positions_,
        balance=services.balance(positions_),
        partner_names=data.partner_names,
    )


@dataclass(frozen=True)
class GarageExpensesPage:
    """Ce qu'affiche « Dépenses du garage » pour une année.

    `report` vaut None si l'argent n'est pas partagé entre deux associés actifs : la part
    de chacun n'aurait pas de sens. `expenses` : les dépenses en base, par identifiant,
    avec leur fournisseur, leurs payeurs et leurs lignes.
    """

    report: services.GarageExpenses | None
    years: list[int]
    expenses: dict[int, Expense]


def garage_expenses(year: int, this_year: int) -> GarageExpensesPage:
    """Les dépenses du garage de l'année `year`, la plus récente d'abord (D57, D58).

    `years` : les années qui ont une dépense du garage, plus l'année en cours `this_year`
    (toujours proposée, même sans dépense) et l'année demandée.
    """
    data = load_accounts()
    recentes = sorted(data.expenses, key=lambda d: (d.date, d.key), reverse=True)
    rapport = None
    if not partner_problems(data):
        rapport = services.garage_expenses_for_year(recentes, year, data.partners)
    cles = []
    if rapport is not None:
        cles = [d.expense.key for d in rapport.items] + [d.key for d in rapport.personal]
    depenses = (
        Expense.objects.filter(pk__in=cles)
        .select_related("supplier")
        .prefetch_related(
            # Dans l'ordre des associés, comme `load_expenses` : les badges ne bougent pas.
            Prefetch(
                "payments",
                queryset=ExpensePayment.objects.select_related("partner").order_by("partner_id"),
            ),
            Prefetch(
                "lines", queryset=ExpenseLine.objects.select_related("category").order_by("id")
            ),
        )
    )
    return GarageExpensesPage(
        report=rapport,
        years=sorted(
            {*services.garage_expense_years(data.expenses), this_year, year}, reverse=True
        ),
        expenses=depenses.in_bulk(),
    )


def vehicle_states() -> dict[int, services.VehicleState]:
    """Statut, date d'achat et date d'abandon de chaque véhicule, fiches à la corbeille
    comprises (D59) : une fiche vendue mise à la corbeille garde sa date d'achat.

    La date d'abandon est celle du dernier passage en « Abandonné » ; à défaut (véhicule
    repris de l'Excel sans changement de statut), la date de création de la fiche.
    """
    abandons: dict[int, dt.date] = {}
    changements = StatusChange.objects.filter(to_status=Vehicle.Status.ABANDONED).order_by(
        "changed_at", "id"
    )
    for vehicule, quand in changements.values_list("vehicle_id", "changed_at"):
        abandons[vehicule] = timezone.localdate(quand)
    etats = {}
    for pk, statut, achat, cree, supprime in Vehicle.all_objects.values_list(
        "pk", "status", "purchased_on", "created_at", "deleted_at"
    ):
        abandon = None
        if statut == Vehicle.Status.ABANDONED:
            abandon = abandons.get(pk, timezone.localdate(cree))
        etats[pk] = services.VehicleState(
            statut, purchased_on=achat, abandoned_on=abandon, in_trash=supprime is not None
        )
    return etats


@dataclass(frozen=True)
class GarageReport:
    """Le bilan de tout le garage (D59 à D62), pour `controle_comptes` et l'écran du bilan.

    `partners` vaut None si l'argent n'est pas partagé entre deux associés actifs : le
    bilan par associé n'aurait pas de sens.
    """

    data: AccountsData
    rows: list[services.VehicleReportRow]
    buckets: services.MoneyBuckets
    partners: dict[int, services.PartnerReport] | None


def load_report(data: AccountsData | None = None) -> GarageReport:
    """Range l'argent du garage dans les cases du bilan. `data` : les comptes déjà lus
    (`load_accounts`), pour que le bilan parle des mêmes chiffres que les positions."""
    data = load_accounts() if data is None else data
    lignes = services.vehicle_report(data.expenses, data.sales, data.deposits, vehicle_states())
    cases = services.money_buckets(lignes, data.expenses, data.deposits)
    associes = None
    if not partner_problems(data):
        details = services.position_details(
            data.partners,
            expenses=data.expenses,
            sales=data.sales,
            deposits=data.deposits,
            settlements=data.settlements,
        )
        associes = services.partner_report(details, cases, data.partners)
    return GarageReport(data=data, rows=lignes, buckets=cases, partners=associes)


@dataclass(frozen=True)
class ReportPage:
    """Ce qu'affiche le bilan pour une année (D59 à D62).

    `years` : les choix du sélecteur ; `vehicles` : les fiches des véhicules du bilan, par
    identifiant, corbeille comprise (une fiche à la corbeille est nommée, sans lien).
    """

    report: GarageReport
    year: services.YearReport
    years: list[int]
    vehicles: dict[int, Vehicle]


def report_page(year: int, this_year: int) -> ReportPage:
    """Le bilan de l'année `year`, et celui de chaque associé depuis le début.

    `years` : les années qui ont une dépense, une vente ou un abandon, plus l'année en
    cours `this_year` (toujours proposée) et l'année demandée.
    """
    rapport = load_report()
    data = rapport.data
    associes = data.partners if rapport.partners is not None else ()
    return ReportPage(
        report=rapport,
        year=services.year_report(year, rapport.rows, data.expenses, associes),
        years=sorted(
            {*services.report_years(rapport.rows, data.expenses), this_year, year}, reverse=True
        ),
        vehicles=Vehicle.all_objects.in_bulk([ligne.vehicle for ligne in rapport.rows]),
    )


@dataclass(frozen=True)
class SaleAccounts:
    """Les comptes d'une vente : récapitulatif du véhicule et solde de tout le garage."""

    recap: services.SaleRecap
    balance: services.Balance
    partner_names: dict[int, str]


def sale_accounts(vehicle_id: int, sale_id: int) -> SaleAccounts | None:
    """Les comptes d'une vente non annulée pour la fiche du véhicule.

    None si l'argent n'est pas partagé entre deux associés actifs (`controle_comptes` dit
    pourquoi) : le partage n'aurait alors pas de sens.
    """
    data = load_accounts()
    if partner_problems(data):
        return None
    vente = next(v for v in data.sales if v.key == sale_id)
    cout = services.vehicle_costs(data.expenses).get(vehicle_id, services.VehicleCost())
    acomptes = [a for a in data.deposits if a.vehicle == vehicle_id]
    return SaleAccounts(
        recap=services.sale_recap(cout, vente, acomptes, data.partners),
        balance=services.balance(positions(data)),
        partner_names=data.partner_names,
    )


def refund_warnings() -> list[str]:
    """Les retours à 0,00 € « à préciser » (D45), en phrases : un montant à compléter, pas
    une incohérence. Le coût et le solde n'en dépendent pas."""
    retours = (
        Refund.objects.filter(
            amount=0,
            line__deleted_at=None,
            line__expense__isnull=False,
            line__expense__deleted_at=None,
        )
        .select_related("line__expense", "refunded_to")
        .order_by("line__expense__date", "id")
    )
    return [
        f"retour à 0,00 € à préciser : « {r.line.label} », dépense n°{r.line.expense_id} du "
        f"{r.line.expense.date:%d/%m/%Y}, pour {r.refunded_to.name}"
        for r in retours
    ]


def vehicle_names(ids) -> dict[int, str]:
    """Nom affiché des véhicules (corbeille comprise : leurs dépenses restent comptées)."""
    return {v.pk: v.display_name for v in Vehicle.all_objects.filter(pk__in=ids)}

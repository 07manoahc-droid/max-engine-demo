"""Commande `controle_comptes` : affiche les comptes du garage et vérifie leur cohérence.

À lancer avant et après toute migration qui touche à l'argent : les deux comptes rendus
doivent être identiques. Code de sortie non nul si une incohérence est trouvée ; un
avertissement (retour à 0,00 € « à préciser ») ne change pas le code de sortie.
"""

from django.core.management.base import BaseCommand, CommandError

from accounting import queries, services
from core.formatting import format_euros
from expenses.models import Expense, ExpenseLine, ExpensePayment, Refund
from sales.models import Deposit, DepositReceipt, Sale, SaleReceipt
from vehicles.models import Vehicle

from ...models import Settlement


def decompte(nombre: int, singulier: str, pluriel: str) -> str:
    """« 0 vente », « 1 vente », « 2 ventes »."""
    return f"{nombre} {pluriel if nombre > 1 else singulier}"


def avec_detail(volume: str, nombre: int, singulier: str, pluriel: str) -> str:
    """Ajoute au volume « (+ N annulées) » : les éléments annulés ou terminés, s'il y en a."""
    return f"{volume} (+ {decompte(nombre, singulier, pluriel)})" if nombre else volume


class Command(BaseCommand):
    help = "Affiche les positions, le solde et les coûts de revient, puis vérifie les comptes."

    def handle(self, *args, **options):
        # Sous Windows, une sortie redirigée (fichier, Git Bash) serait en cp1252 :
        # les accents et le signe € seraient illisibles. On impose l'UTF-8.
        flux = getattr(self.stdout, "_out", None)
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8")

        data = queries.load_accounts()
        noms = data.partner_names
        problemes = queries.consistency_problems(data)

        self.titre("Contrôle des comptes")
        self.afficher_volumes()

        try:
            positions = queries.positions(data)
        except ValueError as erreur:
            problemes.append(str(erreur))
            positions = None

        if positions is not None:
            self.titre("Associés")
            for associe, position in positions.items():
                self.stdout.write(
                    f"  {noms[associe]} : sorti {format_euros(position.paid_out)}, "
                    f"reçu {format_euros(position.received)}, "
                    f"position {format_euros(position.value)}"
                )
            if len(positions) == 2:
                solde = services.balance(positions)
                self.stdout.write(f"  Solde : {services.balance_sentence(solde, noms)}")

        # Le bilan est lu une seule fois : les marges et la section « Bilan » en viennent.
        bilan = queries.load_report(data)
        couts = services.vehicle_costs(data.expenses)
        marges = queries.vehicle_margins(bilan)
        self.titre("Coût de revient des véhicules")
        noms_vehicules = queries.vehicle_names(cle for cle in couts if cle is not None)
        for vehicule in sorted(cle for cle in couts if cle is not None):
            ligne = f"  {noms_vehicules[vehicule]} : {self.phrase_cout(couts[vehicule], noms)}"
            if vehicule in marges:
                ligne += f", marge {format_euros(marges[vehicule])}"
            self.stdout.write(ligne)
        if None in couts:
            self.stdout.write(
                f"  Dépenses générales du garage : {self.phrase_cout(couts[None], noms)}"
            )
        if not couts:
            self.stdout.write("  Aucune dépense.")

        if positions is not None:
            problemes_du_bilan = self.afficher_bilan(bilan, positions, noms)
            # Une autre incohérence (paiements ≠ montant payé...) fausse aussi le bilan :
            # ses contrôles ne répéteraient que la même erreur.
            if not problemes:
                problemes += problemes_du_bilan

        self.titre("Contrôles")
        for avertissement in queries.refund_warnings():
            self.stdout.write(self.style.WARNING(f"  Attention : {avertissement}"))
        if problemes:
            for probleme in problemes:
                self.stdout.write(self.style.ERROR(f"  - {probleme}"))
            raise CommandError(f"{len(problemes)} incohérence(s) dans les comptes.")
        self.stdout.write(self.style.SUCCESS("  Les comptes sont cohérents."))

    def afficher_bilan(self, bilan, positions, noms: dict[int, str]) -> list[str]:
        """Le bilan depuis le début (D59 à D61) ; renvoie ses incohérences.

        Deux contrôles : chaque euro est rangé (somme des positions = encore dans le garage
        − bénéfice), et, pour chaque associé, ce qui lui revient = déjà revenu + encore dans
        le garage ± solde, à un centime près.
        """
        cases = bilan.buckets
        self.titre("Bilan depuis le début")
        self.stdout.write(
            f"  Bénéfice : {format_euros(cases.profit)} = marges {format_euros(cases.margins)}"
            f" − pertes {format_euros(cases.losses)}"
            f" − dépenses du garage {format_euros(cases.garage_expenses)}"
        )
        detail = f"véhicules au garage {format_euros(cases.in_garage)}"
        if cases.in_trash:
            detail += f" + fiches à la corbeille {format_euros(cases.in_trash)}"
        detail += f" − acomptes en attente {format_euros(cases.pending_deposits)}"
        self.stdout.write(
            f"  Encore dans le garage : {format_euros(cases.still_in_garage)} ({detail})"
        )
        problemes = services.buckets_problems(positions, cases)
        if bilan.partners is None:
            return problemes
        for associe, ligne in bilan.partners.items():
            if ligne.balance > 0:
                solde = f"+ à recevoir {format_euros(ligne.balance)}"
            elif ligne.balance < 0:
                solde = f"− à verser {format_euros(-ligne.balance)}"
            else:
                solde = "(à l'équilibre)"
            self.stdout.write(
                f"  {noms[associe]} : mis {format_euros(ligne.put_in)}"
                f" + part du bénéfice {format_euros(ligne.profit_share)}"
                f" = lui revient {format_euros(ligne.due_to_partner)}"
                f" = déjà revenu {format_euros(ligne.already_back)}"
                f" + encore dans le garage {format_euros(ligne.still_in_garage)} {solde}"
            )
        return problemes + services.partner_report_problems(bilan.partners, noms)

    def titre(self, texte: str) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(texte))

    def afficher_volumes(self) -> None:
        """Nombre d'éléments de chaque table. La corbeille n'est pas comptée, ni ce qui
        dépend d'un élément à la corbeille (lignes et paiements d'une dépense supprimée...).

        Ventes, encaissements et acomptes séparent ce qui est actif de ce qui est annulé ou
        terminé, pour qu'on ne croie pas à une vente restante.
        """
        volumes = [
            f"{nombre} {nom}"
            for nom, nombre in (
                ("véhicules", Vehicle.objects.count()),
                ("dépenses", Expense.objects.count()),
                (
                    "lignes",
                    ExpenseLine.objects.filter(
                        expense__isnull=False, expense__deleted_at=None
                    ).count(),
                ),
                ("pièces à commander", ExpenseLine.objects.filter(expense=None).count()),
                ("paiements", ExpensePayment.objects.filter(expense__deleted_at=None).count()),
                (
                    "remboursements",
                    Refund.objects.filter(
                        line__deleted_at=None, line__expense__deleted_at=None
                    ).count(),
                ),
            )
        ]
        volumes += self.volumes_des_ventes()
        volumes.append(f"{Settlement.objects.count()} règlements")
        self.stdout.write("  " + ", ".join(volumes))

    @staticmethod
    def volumes_des_ventes() -> list[str]:
        """« 3 ventes actives (+ 1 annulée) », « 4 encaissements actifs (+ 1 d'une vente annulée) »,
        « 1 acompte en cours (2 versements) (+ 3 terminés) ». La parenthèse disparaît si elle vaut
        zéro, celle des versements s'il n'y en a pas plus que d'acomptes."""
        ventes = Sale.objects.filter(cancelled_at__isnull=True).count()
        encaissements = SaleReceipt.objects.filter(sale__cancelled_at__isnull=True).count()
        acomptes = Deposit.objects.filter(status=Deposit.Status.ACTIVE).count()
        versements = DepositReceipt.objects.filter(deposit__status=Deposit.Status.ACTIVE).count()
        en_cours = decompte(acomptes, "acompte en cours", "acomptes en cours")
        # Un acompte peut avoir plusieurs versements (D89) : dit seulement s'il y en a plus.
        if versements > acomptes:
            en_cours += f" ({versements} versements)"
        return [
            avec_detail(
                decompte(ventes, "vente active", "ventes actives"),
                Sale.objects.count() - ventes,
                "annulée",
                "annulées",
            ),
            avec_detail(
                decompte(encaissements, "encaissement actif", "encaissements actifs"),
                SaleReceipt.objects.count() - encaissements,
                "d'une vente annulée",
                "d'une vente annulée",
            ),
            avec_detail(
                en_cours,
                Deposit.objects.count() - acomptes,
                "terminé",
                "terminés",
            ),
        ]

    @staticmethod
    def phrase_cout(cout: services.VehicleCost, noms: dict[int, str]) -> str:
        parts = ", ".join(
            f"{noms[associe]} {format_euros(montant)}"
            for associe, montant in sorted(cout.by_partner.items())
        )
        return f"{format_euros(cout.total)} ({parts})"

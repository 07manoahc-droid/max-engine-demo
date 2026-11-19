"""Commande `sauvegarder` : copie la base et les fichiers joints dans le dossier des
sauvegardes (OneDrive d'Axel, D70 à D73).

    uv run python manage.py sauvegarder [--raison fermeture|quotidienne|mise-a-jour|manuelle]
                                        [--nom "texte"]

La commande lit la base et n'y écrit jamais. Code de sortie non nul si la sauvegarde échoue.
La restauration n'a pas d'écran : la marche à suivre est dans README.md.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import backup


def nombre(n: int, mot: str) -> str:
    """« 0 véhicule », « 1 véhicule », « 2 véhicules »."""
    return f"{n} {mot}{'s' if n > 1 else ''}"


def corbeille(n: int) -> str:
    """« (+ 2 à la corbeille) », comme `controle_comptes`, qui ne les compte pas."""
    return f" (+ {n} à la corbeille)" if n else ""


class Command(BaseCommand):
    help = "Sauvegarde la base et les fichiers joints dans le dossier des sauvegardes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--raison",
            choices=list(backup.REASONS),
            default="manuelle",
            help="Pourquoi on sauvegarde (fixe la rotation). Par défaut : manuelle.",
        )
        parser.add_argument(
            "--nom",
            default="",
            help="Un nom pour la sauvegarde, par exemple « avant import ».",
        )

    def handle(self, *args, raison, nom, **options):
        # Sous Windows, une sortie redirigée serait en cp1252 : on impose l'UTF-8.
        for sortie in (self.stdout, self.stderr):
            flux = getattr(sortie, "_out", None)
            if hasattr(flux, "reconfigure"):
                flux.reconfigure(encoding="utf-8")

        if nom and raison != "manuelle":
            # Une sauvegarde nommée doit être gardée : seule la « manuelle » échappe à la
            # rotation (D72).
            raise CommandError("Un nom (--nom) ne se donne qu'à une sauvegarde manuelle.")
        maintenant = timezone.now()
        if raison == "quotidienne" and not backup.daily_backup_needed(
            backup.read_state(), maintenant
        ):
            self.stdout.write("Une sauvegarde a déjà été faite il y a moins de 24 heures.")
            return

        try:
            resultat = backup.make_backup(raison, nom, now=maintenant)
        except backup.BackupError as erreur:
            raise CommandError(f"Sauvegarde impossible. {erreur}") from erreur

        if not resultat.folder.configured:
            self.stderr.write(
                "Attention : aucun dossier de sauvegarde n'est réglé (variable BACKUP_DIR ou "
                "paramètres du garage). La sauvegarde reste sur cet ordinateur, sans copie "
                "dans OneDrive."
            )
        controle = resultat.check
        self.stdout.write(f"Sauvegarde faite : {resultat.file.name}")
        self.stdout.write(f"  Dossier : {resultat.file.parent}")
        self.stdout.write(f"  Taille : {backup.format_size(resultat.size)}")
        self.stdout.write(
            f"  Base vérifiée : {nombre(controle.vehicles, 'véhicule')}"
            f"{corbeille(controle.vehicles_in_trash)}, {nombre(controle.expenses, 'dépense')}"
            f"{corbeille(controle.expenses_in_trash)}"
        )
        fichiers = resultat.media
        self.stdout.write(
            f"  Fichiers joints : {fichiers.copied} nouveau{'x' if fichiers.copied > 1 else ''}"
            f" copié{'s' if fichiers.copied > 1 else ''} sur {fichiers.total}, dans "
            f"« {backup.MIRROR} »"
        )
        if not resultat.state_saved:
            self.stderr.write(
                "Attention : la sauvegarde est faite, mais « Dernière sauvegarde » n'a pas pu "
                "être mis à jour dans la barre latérale."
            )
        if resultat.rotated:
            n = len(resultat.rotated)
            pluriel = "s" if n > 1 else ""
            self.stdout.write(
                f"  Rotation : {n} ancienne{pluriel} sauvegarde{pluriel} effacée{pluriel}"
            )

"""Commande `dossier_sauvegardes` : affiche ou règle le dossier des sauvegardes (D70).

    uv run python manage.py dossier_sauvegardes
    uv run python manage.py dossier_sauvegardes "C:\\…\\OneDrive\\MAx Engine - sauvegardes"

Sert à l'installation. Le dossier doit déjà exister : on ne règle
pas un dossier OneDrive mal tapé, où aucune sauvegarde n'arriverait.
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core import backup
from core.models import GarageSettings


class Command(BaseCommand):
    help = "Affiche ou règle le dossier des sauvegardes (OneDrive)."

    def add_arguments(self, parser):
        parser.add_argument("dossier", nargs="?", help="Chemin complet du dossier.")

    def handle(self, *args, dossier=None, **options):
        flux = getattr(self.stdout, "_out", None)
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8")
        if dossier:
            chemin = Path(os.path.expandvars(dossier.strip()))
            if not chemin.is_absolute():
                raise CommandError(f"« {dossier} » n'est pas un chemin complet.")
            if not chemin.is_dir():
                raise CommandError(
                    f"Le dossier « {chemin} » est introuvable : crée-le d'abord dans OneDrive."
                )
            parametres, _ = GarageSettings.objects.get_or_create(pk=1)
            parametres.backup_dir = str(chemin)
            parametres.save()
            self.stdout.write(f"Dossier des sauvegardes réglé : {chemin}")
            return
        actuel = backup.backup_folder()
        if actuel.configured:
            self.stdout.write(f"Dossier des sauvegardes : {actuel.path}")
        else:
            self.stdout.write(
                f"Aucun dossier réglé : les sauvegardes vont dans {actuel.path}, sans copie "
                "dans OneDrive."
            )

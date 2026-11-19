#!/usr/bin/env python
"""Utilitaire de commandes Django de MAx Engine."""

import os
import sys


def main():
    """Lance une commande d'administration."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Développement : DEBUG actif par défaut. Le lanceur Waitress n'utilise pas ce fichier
    # et reste donc en production (décision D26).
    os.environ.setdefault("DEBUG", "1")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Impossible d'importer Django. Vérifie qu'il est installé (uv sync) "
            "et lance les commandes avec `uv run`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

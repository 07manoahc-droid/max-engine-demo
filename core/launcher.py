"""Lanceur Windows de MAx Engine (D84) : ce que fait le double-clic sur « MAx Engine ».

1. Si MAx Engine est déjà ouvert, il rouvre seulement une fenêtre, puis s'arrête.
2. Sinon, il vérifie la base (présente, migrations faites), démarre le serveur Waitress sur
   127.0.0.1:8000 et ouvre Microsoft Edge en mode application, avec son propre profil
   (`donnees/edge-profil`).
3. En parallèle, une sauvegarde « quotidienne » est faite s'il n'y en a pas eu depuis
   24 heures (D73).
4. Quand la dernière fenêtre est fermée, il fait la sauvegarde « fermeture », puis arrête le
   serveur. Si une fenêtre a été rouverte pendant la sauvegarde, il attend sa fermeture et
   sauvegarde de nouveau.

Le fichier `lanceur.py` est lancé par `pythonw`, sans terminal. DEBUG reste faux, car
`manage.py` n'est pas utilisé (D26). Une erreur s'affiche dans une boîte de message
Windows, en français ; le détail est écrit dans `donnees/lanceur.log`.

Consultation en lecture seule (D80) : `--consultation --port 8001`, avec
READ_ONLY=1. Ni sauvegarde ni écriture, et un profil Edge à part, dans le dossier
temporaire.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8000
HEALTH_PATH = "/sante/"
SIGNATURE = "MAx Engine"
LOG_FILE = BASE_DIR / "donnees" / "lanceur.log"
LOG_MAX_SIZE = 1_000_000  # au-delà, le journal repart de zéro au lancement
PROFILE = BASE_DIR / "donnees" / "edge-profil"
CONSULTATION_PROFILE = Path(tempfile.gettempdir()) / "MAx Engine - consultation" / "edge-profil"
# Chromium (donc Edge) garde ce fichier ouvert dans le dossier du profil tant que le
# navigateur tourne, et l'efface en s'arrêtant.
EDGE_LOCK = "lockfile"
# Posé par « Mettre à jour MAx Engine » avant de toucher à la base, retiré si tout tombe
# juste (D86). S'il reste, MAx Engine ne s'ouvre pas avant que Mathis ait vérifié.
UPDATE_MARKER = BASE_DIR / "donnees" / "mise-a-jour-a-verifier"
EDGE_RELATIVE = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
EDGE_REGISTRY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
SERVER_START_TIMEOUT = 20.0  # secondes
POLL_INTERVAL = 1.0

ERROR_HELP = "Si ça recommence, préviens Mathis (le détail est dans donnees\\lanceur.log)."
logger = logging.getLogger("lanceur")


class LauncherError(Exception):
    """Un problème expliqué en français, affiché tel quel dans la boîte de message."""


# ---------------------------------------------------------------------------
# Microsoft Edge
# ---------------------------------------------------------------------------


def edge_candidates(environ: dict | None = None) -> list[Path]:
    """Les emplacements habituels de `msedge.exe`, dans l'ordre où on les essaie."""
    env = os.environ if environ is None else environ
    racines = [env.get(nom) for nom in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    return [Path(racine) / EDGE_RELATIVE for racine in racines if racine]


def _edge_from_registry() -> Path | None:
    try:
        import winreg
    except ImportError:  # hors de Windows (tests)
        return None
    for ruche in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(ruche, EDGE_REGISTRY) as cle:
                return Path(winreg.QueryValue(cle, None))
        except OSError:
            continue
    return None


def find_edge(candidates: Iterable[Path] | None = None) -> Path | None:
    """Le chemin de `msedge.exe` : emplacements habituels, puis registre, puis PATH."""
    for chemin in edge_candidates() if candidates is None else candidates:
        if chemin.is_file():
            return chemin
    if candidates is not None:
        return None
    registre = _edge_from_registry()
    if registre and registre.is_file():
        return registre
    trouve = shutil.which("msedge")
    return Path(trouve) if trouve else None


def edge_command(edge: Path, url: str, profile: Path) -> list[str]:
    """Edge en mode application (une fenêtre sans barre d'adresse) avec son propre profil :
    c'est ce qui permet de savoir quand la fenêtre se ferme."""
    return [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def edge_running(profile: Path) -> bool:
    """Edge tourne-t-il encore avec ce profil ? Tant qu'il tourne, Edge garde le fichier
    `lockfile` ouvert en écriture ; un fichier resté là après un arrêt brutal s'ouvre."""
    verrou = profile / EDGE_LOCK
    if not verrou.exists():
        return False
    try:
        with open(verrou, "a"):
            return False
    except OSError:
        return True


def wait_for_edge(
    process, profile: Path, poll: float = POLL_INTERVAL, sleep=time.sleep, grace: float = 10.0
) -> None:
    """Attend la fermeture de la dernière fenêtre. Le processus lancé peut s'arrêter tout de
    suite s'il a confié la fenêtre à un Edge déjà ouvert avec ce profil : on attend alors que
    le profil soit libéré.

    Edge se relance lui-même : son verrou peut tarder à apparaître. On lui laisse `grace`
    secondes pour le poser, puis la fermeture n'est lue qu'après deux lectures de suite sans
    verrou, pour ne pas arrêter le serveur pendant qu'une fenêtre s'ouvre."""
    if process is not None:
        process.wait()
    attendu = 0.0
    while not edge_running(profile) and attendu < grace:
        sleep(poll)
        attendu += poll
    lectures_libres = 0
    while lectures_libres < 2:
        lectures_libres = 0 if edge_running(profile) else lectures_libres + 1
        if lectures_libres < 2:
            sleep(poll)


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------

RUNNING = "max-engine"  # MAx Engine répond déjà sur le port
OTHER = "autre"  # un autre logiciel occupe le port
FREE = "libre"


def server_state(port: int, timeout: float = 2.0) -> str:
    """Qui répond sur le port ? Lu sur `/sante/`, qui renvoie « MAx Engine … »."""
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}{HEALTH_PATH}", timeout=timeout
        ) as reponse:
            texte = reponse.read(200).decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        return OTHER
    except (urllib.error.URLError, OSError):
        return FREE
    return RUNNING if texte.startswith(SIGNATURE) else OTHER


def wait_for_server(port: int, timeout: float = SERVER_START_TIMEOUT, sleep=time.sleep) -> None:
    fin = time.monotonic() + timeout
    while server_state(port) != RUNNING:
        if time.monotonic() > fin:
            raise LauncherError(f"Le serveur de MAx Engine n'a pas démarré. {ERROR_HELP}")
        sleep(0.2)


def start_server(port: int):
    """Démarre Waitress dans un fil à part ; renvoie le serveur, déjà à l'écoute."""
    from django.core.wsgi import get_wsgi_application
    from waitress import create_server

    serveur = create_server(get_wsgi_application(), host=HOST, port=port, threads=6)
    threading.Thread(target=serveur.run, name="waitress", daemon=True).start()
    return serveur


# ---------------------------------------------------------------------------
# Base et sauvegardes
# ---------------------------------------------------------------------------


def check_database() -> None:
    """La base existe et ses migrations sont faites. Sans ce contrôle, Django créerait une
    base vide à la place d'une base disparue, ou chaque page finirait en erreur."""
    from django.conf import settings
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    nom = str(settings.DATABASES["default"]["NAME"])
    if not nom.startswith("file:") and not Path(nom).is_file():
        raise LauncherError(
            f"La base de MAx Engine est introuvable ({nom}). Ne saisis rien : préviens Mathis."
        )
    executeur = MigrationExecutor(connection)
    if executeur.migration_plan(executeur.loader.graph.leaf_nodes()):
        raise LauncherError(
            "La base n'est pas à jour. Lance « Mettre à jour MAx Engine » sur le bureau, puis "
            f"rouvre MAx Engine. {ERROR_HELP}"
        )


def _daily_backup() -> None:
    from django.db import connections

    try:
        backup("quotidienne")
    finally:
        connections.close_all()  # la connexion de ce fil seulement


def backup(reason: str) -> str | None:
    """Fait une sauvegarde ; renvoie le message d'erreur, ou rien si elle a réussi.
    L'échec est aussi noté pour la barre latérale (« La dernière sauvegarde a échoué »)."""
    from django.utils import timezone

    from core import backup as sauvegarde

    try:
        if reason == "quotidienne" and not sauvegarde.daily_backup_needed(
            sauvegarde.read_state(), timezone.now()
        ):
            return None
        resultat = sauvegarde.make_backup(reason)
        logger.info("Sauvegarde « %s » faite : %s", reason, resultat.file)
        return None
    except sauvegarde.BackupError as erreur:
        logger.error("Sauvegarde « %s » impossible : %s", reason, erreur)
        return str(erreur)


# ---------------------------------------------------------------------------
# Messages et journal
# ---------------------------------------------------------------------------

MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000


def show_message(text: str, warning: bool = False) -> None:
    """Boîte de message Windows ; ailleurs (tests, Linux), le texte sur la sortie d'erreur."""
    if warning:
        logger.warning(text)
    else:
        logger.error(text)
    try:
        import ctypes

        icone = MB_ICONWARNING if warning else MB_ICONERROR
        ctypes.windll.user32.MessageBoxW(
            None, text, SIGNATURE, icone | MB_TOPMOST | MB_SETFOREGROUND
        )
    except (ImportError, AttributeError, OSError):
        print(text, file=sys.stderr)


def setup_log(fichier: Path = LOG_FILE) -> None:
    """Journal dans `donnees/lanceur.log`. Avec `pythonw`, il n'y a ni sortie ni erreur
    standard : elles sont envoyées dans le journal, pour ne rien perdre."""
    fichier.parent.mkdir(parents=True, exist_ok=True)
    if fichier.exists() and fichier.stat().st_size > LOG_MAX_SIZE:
        fichier.unlink()
    flux = open(fichier, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (ouvert jusqu'à la fin)
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = flux
    logging.basicConfig(
        stream=flux,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
        force=True,
    )


# ---------------------------------------------------------------------------
# Lancement
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ouvre MAx Engine.")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--consultation",
        action="store_true",
        help="consultation d'une sauvegarde (READ_ONLY=1) : ni sauvegarde ni écriture",
    )
    return parser.parse_args(argv)


def open_window(edge: Path, port: int, profile: Path) -> subprocess.Popen:
    profile.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(edge_command(edge, f"http://{HOST}:{port}/", profile))


def run(args: argparse.Namespace) -> None:
    edge = find_edge()
    if edge is None:
        raise LauncherError(
            "Microsoft Edge est introuvable sur cet ordinateur : MAx Engine s'ouvre dans Edge. "
            "Préviens Mathis."
        )
    profil = CONSULTATION_PROFILE if args.consultation else PROFILE

    etat = server_state(args.port)
    if etat == RUNNING:  # second double-clic : une fenêtre de plus, rien d'autre
        open_window(edge, args.port, profil)
        return
    if etat == OTHER:
        raise LauncherError(
            f"Un autre logiciel utilise déjà le port {args.port} de cet ordinateur : MAx "
            f"Engine ne peut pas s'ouvrir. {ERROR_HELP}"
        )

    # Production : DEBUG reste faux (D26), quoi que dise l'environnement.
    os.environ.pop("DEBUG", None)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if args.consultation and os.environ.get("READ_ONLY") != "1":
        raise LauncherError("La consultation demande READ_ONLY=1 : relance avec cette variable.")
    if not args.consultation and UPDATE_MARKER.exists():
        raise LauncherError(
            "La dernière mise à jour s'est mal terminée : n'utilise pas MAx Engine avant que "
            "Mathis ait vérifié les comptes. Préviens Mathis."
        )
    import django

    django.setup()
    check_database()
    try:
        start_server(args.port)
    except OSError:
        # Deux double-clics très rapprochés : l'autre lancement a pris le port avant nous.
        if server_state(args.port) == RUNNING:
            open_window(edge, args.port, profil)
            return
        raise
    wait_for_server(args.port)
    logger.info("MAx Engine ouvert sur le port %s", args.port)

    fenetre = open_window(edge, args.port, profil)
    quotidienne = None
    if not args.consultation:
        quotidienne = threading.Thread(target=_daily_backup, name="quotidienne")
        quotidienne.start()

    while True:
        wait_for_edge(fenetre, profil)
        logger.info("Fenêtre fermée")
        if args.consultation:
            break
        quotidienne.join()
        erreur = backup("fermeture")
        if erreur:
            show_message(
                f"La sauvegarde de fermeture n'a pas pu se faire.\n\n{erreur}\n\nTes saisies "
                "sont bien enregistrées sur cet ordinateur.",
                warning=True,
            )
        # Un double-clic pendant la sauvegarde a rouvert une fenêtre sur ce serveur (il
        # répondait encore) : on la laisse vivre, et on sauvegardera à sa fermeture.
        if not edge_running(profil):
            break
        logger.info("Fenêtre rouverte pendant la sauvegarde de fermeture")
        fenetre = None
    # Le serveur s'arrête avec le programme, juste après : `serveur.close()` couperait
    # une réponse encore en cours d'envoi.


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        setup_log()
    except OSError:
        pass  # sans journal, le lanceur marche quand même
    try:
        run(args)
    except LauncherError as erreur:
        show_message(str(erreur))
        return 1
    except Exception:
        logger.exception("Erreur inattendue du lanceur")
        show_message(f"MAx Engine n'a pas pu s'ouvrir. {ERROR_HELP}")
        return 1
    return 0

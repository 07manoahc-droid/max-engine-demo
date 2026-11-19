"""Lanceur Windows (D84) : Edge, verrou du profil, serveur, contrôles et sauvegardes."""

import datetime as dt
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from django.utils import timezone

from core import backup as sauvegarde
from core import launcher

# ---------------------------------------------------------------------------
# Microsoft Edge
# ---------------------------------------------------------------------------


def test_edge_candidates_follow_windows_folders():
    chemins = launcher.edge_candidates(
        {"ProgramFiles(x86)": r"C:\PF86", "ProgramFiles": r"C:\PF", "LOCALAPPDATA": ""}
    )
    assert chemins == [
        Path(r"C:\PF86") / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(r"C:\PF") / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]


def test_find_edge_takes_the_first_existing_file(tmp_path):
    absent, present = tmp_path / "a" / "msedge.exe", tmp_path / "b" / "msedge.exe"
    present.parent.mkdir()
    present.write_text("")
    assert launcher.find_edge([absent, present]) == present
    assert launcher.find_edge([absent]) is None


def test_edge_opens_as_an_application_with_its_own_profile(tmp_path):
    commande = launcher.edge_command(Path("msedge.exe"), "http://127.0.0.1:8000/", tmp_path)
    assert commande[0] == "msedge.exe"
    assert "--app=http://127.0.0.1:8000/" in commande
    assert f"--user-data-dir={tmp_path}" in commande


def test_profile_without_lock_is_free(tmp_path):
    assert launcher.edge_running(tmp_path) is False
    (tmp_path / "lockfile").write_text("")  # resté après un arrêt brutal : il s'ouvre
    assert launcher.edge_running(tmp_path) is False


@pytest.mark.skipif(os.name != "nt", reason="verrou de fichier propre à Windows")
def test_profile_locked_like_edge_does(tmp_path):
    """Edge ouvre `lockfile` en écriture, sans partage, effacé à la fermeture : on fait de
    même avec l'API Windows."""
    import ctypes

    noyau = ctypes.windll.kernel32
    noyau.CreateFileW.restype = ctypes.c_void_p
    poignee = noyau.CreateFileW(
        str(tmp_path / "lockfile"),
        0x40000000,  # GENERIC_WRITE
        0x1,  # FILE_SHARE_READ
        None,
        2,  # CREATE_ALWAYS
        0x80 | 0x04000000,  # FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE
        None,
    )
    assert poignee not in (None, ctypes.c_void_p(-1).value)
    try:
        assert launcher.edge_running(tmp_path) is True
    finally:
        noyau.CloseHandle(ctypes.c_void_p(poignee))
    assert launcher.edge_running(tmp_path) is False


def test_wait_for_edge_waits_for_the_profile(monkeypatch, tmp_path):
    """Le processus lancé peut s'arrêter tout de suite (il confie la fenêtre au navigateur) :
    on attend que le profil soit libéré."""

    class Processus:
        attendu = False

        def wait(self):
            self.attendu = True

    # Verrou pas encore posé (Edge se relance), puis posé, un faux départ, puis fermé.
    etats = iter([False, True, True, False, True, False, False])
    lectures = []

    def verrou(profil):
        lectures.append(1)
        return next(etats)

    monkeypatch.setattr(launcher, "edge_running", verrou)
    processus = Processus()
    launcher.wait_for_edge(processus, tmp_path, sleep=lambda s: None)
    assert processus.attendu
    assert len(lectures) == 7  # deux lectures de suite sans verrou pour conclure


def test_wait_for_edge_gives_up_on_a_lock_that_never_comes(monkeypatch, tmp_path):
    """Edge ne s'est pas ouvert : après le délai de grâce, on conclut à la fermeture."""
    monkeypatch.setattr(launcher, "edge_running", lambda profil: False)
    pauses = []
    launcher.wait_for_edge(None, tmp_path, poll=1.0, sleep=pauses.append, grace=3.0)
    assert len(pauses) == 4  # 3 s de grâce, puis une pause entre les deux lectures


# ---------------------------------------------------------------------------
# Qui répond sur le port ?
# ---------------------------------------------------------------------------


def serveur_qui_repond(texte: str, statut: int = 200):
    class Reponse(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(statut)
            self.end_headers()
            self.wfile.write(texte.encode())

        def log_message(self, *args):
            pass

    serveur = HTTPServer(("127.0.0.1", 0), Reponse)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur


@pytest.mark.parametrize(
    ("texte", "statut", "attendu"),
    [
        ("MAx Engine 0.1.0", 200, launcher.RUNNING),
        ("Un autre logiciel", 200, launcher.OTHER),
        ("Introuvable", 404, launcher.OTHER),
    ],
)
def test_server_state(texte, statut, attendu):
    serveur = serveur_qui_repond(texte, statut)
    try:
        assert launcher.server_state(serveur.server_address[1]) == attendu
    finally:
        serveur.shutdown()
        serveur.server_close()


def test_free_port():
    serveur = serveur_qui_repond("")
    port = serveur.server_address[1]
    serveur.shutdown()
    serveur.server_close()
    assert launcher.server_state(port, timeout=0.5) == launcher.FREE


# ---------------------------------------------------------------------------
# Lancement
# ---------------------------------------------------------------------------


@pytest.fixture
def fenetres(monkeypatch, tmp_path):
    """Edge factice : les fenêtres ouvertes sont notées, rien ne s'affiche."""
    ouvertes = []
    monkeypatch.setattr(launcher, "find_edge", lambda: tmp_path / "msedge.exe")
    monkeypatch.setattr(launcher, "PROFILE", tmp_path / "profil")
    monkeypatch.setattr(launcher, "UPDATE_MARKER", tmp_path / "aucun-repere")
    monkeypatch.setattr(
        launcher, "open_window", lambda edge, port, profil: ouvertes.append((port, profil))
    )
    return ouvertes


def test_second_launch_only_opens_a_window(monkeypatch, fenetres, tmp_path):
    monkeypatch.setattr(launcher, "server_state", lambda port: launcher.RUNNING)
    monkeypatch.setattr(launcher, "start_server", lambda port: pytest.fail("second serveur"))

    launcher.run(launcher.parse_args([]))
    assert fenetres == [(8000, tmp_path / "profil")]


def test_port_taken_by_another_program(monkeypatch, fenetres):
    monkeypatch.setattr(launcher, "server_state", lambda port: launcher.OTHER)
    with pytest.raises(launcher.LauncherError, match="Un autre logiciel utilise déjà le port"):
        launcher.run(launcher.parse_args([]))
    assert fenetres == []


def test_missing_edge_is_explained(monkeypatch):
    monkeypatch.setattr(launcher, "find_edge", lambda: None)
    with pytest.raises(launcher.LauncherError, match="Microsoft Edge est introuvable"):
        launcher.run(launcher.parse_args([]))


def test_consultation_needs_read_only(monkeypatch, fenetres):
    monkeypatch.setattr(launcher, "server_state", lambda port: launcher.FREE)
    monkeypatch.delenv("READ_ONLY", raising=False)
    with pytest.raises(launcher.LauncherError, match="READ_ONLY=1"):
        launcher.run(launcher.parse_args(["--consultation", "--port", "8001"]))


def test_failed_update_keeps_max_engine_closed(monkeypatch, fenetres, tmp_path):
    """D86 : le repère d'une mise à jour mal terminée empêche d'ouvrir MAx Engine."""
    repere = tmp_path / "mise-a-jour-a-verifier"
    repere.write_text("")
    monkeypatch.setattr(launcher, "UPDATE_MARKER", repere)
    monkeypatch.setattr(launcher, "server_state", lambda port: launcher.FREE)
    monkeypatch.setattr(launcher, "start_server", lambda port: pytest.fail("serveur démarré"))
    with pytest.raises(launcher.LauncherError, match="mise à jour s'est mal terminée"):
        launcher.run(launcher.parse_args([]))
    assert fenetres == []


def test_window_reopened_during_closing_backup(monkeypatch, fenetres, tmp_path):
    """Un double-clic pendant la sauvegarde de fermeture rouvre une fenêtre sur ce serveur :
    le lanceur attend sa fermeture et sauvegarde de nouveau, au lieu de s'arrêter dessous."""
    monkeypatch.setenv("DEBUG", "0")  # le lanceur le retire ; remis après le test
    monkeypatch.setattr(launcher, "server_state", lambda port: launcher.FREE)
    monkeypatch.setattr(launcher, "check_database", lambda: None)
    monkeypatch.setattr(launcher, "start_server", lambda port: None)
    monkeypatch.setattr(launcher, "wait_for_server", lambda port: None)
    monkeypatch.setattr(launcher, "_daily_backup", lambda: None)
    attentes, sauvegardes = [], []
    monkeypatch.setattr(launcher, "wait_for_edge", lambda f, p: attentes.append(f))
    monkeypatch.setattr(launcher, "backup", lambda raison: sauvegardes.append(raison))
    rouverte = iter([True, False])  # fenêtre rouverte après la 1re sauvegarde seulement
    monkeypatch.setattr(launcher, "edge_running", lambda profil: next(rouverte))

    launcher.run(launcher.parse_args([]))
    assert sauvegardes == ["fermeture", "fermeture"]
    assert len(attentes) == 2 and attentes[1] is None
    assert fenetres == [(8000, tmp_path / "profil")]


def test_main_shows_a_french_message(monkeypatch):
    messages = []
    monkeypatch.setattr(launcher, "setup_log", lambda: None)
    monkeypatch.setattr(
        launcher, "show_message", lambda texte, warning=False: messages.append(texte)
    )

    def panne(args):
        raise launcher.LauncherError("La base n'est pas à jour.")

    monkeypatch.setattr(launcher, "run", panne)
    assert launcher.main([]) == 1
    assert messages == ["La base n'est pas à jour."]

    def bogue(args):
        raise ZeroDivisionError

    monkeypatch.setattr(launcher, "run", bogue)
    assert launcher.main([]) == 1
    assert messages[-1].startswith("MAx Engine n'a pas pu s'ouvrir. Si ça recommence")


# ---------------------------------------------------------------------------
# Base et sauvegardes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_database_ready():
    launcher.check_database()  # base des tests : migrations faites


def test_missing_database_is_refused(monkeypatch, settings, tmp_path):
    # Le nom est changé le temps du test seulement, sans ouvrir de connexion.
    monkeypatch.setitem(settings.DATABASES["default"], "NAME", str(tmp_path / "absente.sqlite3"))
    with pytest.raises(launcher.LauncherError, match="La base de MAx Engine est introuvable"):
        launcher.check_database()
    assert not (tmp_path / "absente.sqlite3").exists()


@pytest.mark.django_db
def test_pending_migrations_ask_for_an_update(monkeypatch):
    from django.db.migrations.executor import MigrationExecutor

    monkeypatch.setattr(MigrationExecutor, "migration_plan", lambda self, cibles: ["0099"])
    with pytest.raises(launcher.LauncherError, match="Mettre à jour MAx Engine"):
        launcher.check_database()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_closing_backup_is_written(settings):
    assert launcher.backup("fermeture") is None
    (fichier,) = Path(settings.BACKUP_FALLBACK_DIR).glob("MAx Engine *.zip")
    assert "(fermeture)" in fichier.name


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_daily_backup_is_skipped_within_24_hours(settings):
    sauvegarde.record_success(timezone.now() - dt.timedelta(hours=3), "récente.zip")
    assert launcher.backup("quotidienne") is None
    assert not list(Path(settings.BACKUP_FALLBACK_DIR).glob("*.zip"))


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_failed_backup_returns_its_message(settings, tmp_path):
    settings.BACKUP_DIR = str(tmp_path / "OneDrive absent")
    message = launcher.backup("fermeture")
    assert "est introuvable" in message
    assert sauvegarde.read_state().failed


def test_parse_args_defaults():
    args = launcher.parse_args([])
    assert (args.port, args.consultation) == (8000, False)

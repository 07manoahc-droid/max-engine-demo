"""Commande `sauvegarder` et indicateur « Dernière sauvegarde » (D70 à D73)."""

import datetime as dt
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from django.conf import settings as reglages
from django.core.management import CommandError, call_command
from django.utils import timezone

from core import backup
from core.models import GarageSettings
from core.ui import WARNING_BADGE

PARIS = timezone.get_fixed_timezone(60)
MOMENT = dt.datetime(2026, 11, 4, 18, 2, tzinfo=PARIS)

# Les sauvegardes lisent la base par une connexion à part : les données du test doivent
# être enregistrées (transaction=True), et les associés créés par migration remis ensuite.
base_enregistree = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def nom_auto(jour: int, heure: int = 18, raison: str = "fermeture") -> str:
    return backup.backup_name(dt.datetime(2026, 10, jour, heure, 0), raison)


# ---------------------------------------------------------------------------
# Noms et rotation
# ---------------------------------------------------------------------------


def test_backup_name_follows_d71():
    assert backup.backup_name(MOMENT, "fermeture") == "MAx Engine 2026-11-04 18-02 (fermeture).zip"
    assert (
        backup.backup_name(MOMENT, "manuelle", "essais avant import")
        == "MAx Engine 2026-11-04 18-02 (manuelle - essais avant import).zip"
    )
    assert backup.backup_name(MOMENT, "fermeture", rank=2).startswith(
        "MAx Engine 2026-11-04 18-02 2 ("
    )


def test_backup_name_removes_characters_windows_refuses():
    nom = backup.backup_name(MOMENT, "manuelle", 'avant "import" : 1/2 ?')
    assert nom == "MAx Engine 2026-11-04 18-02 (manuelle - avant import 1 2).zip"


def test_backup_family_reads_the_name_and_ignores_other_files():
    assert backup.backup_family(nom_auto(1)) == backup.AUTOMATIC
    assert backup.backup_family(nom_auto(1, raison="quotidienne")) == backup.AUTOMATIC
    assert backup.backup_family(nom_auto(1, raison="mise-a-jour")) == backup.UPDATE
    nom_manuel = backup.backup_name(MOMENT, "manuelle", "avant import")
    assert backup.backup_family(nom_manuel) == backup.MANUAL
    for autre in ("notes.txt", nom_auto(1) + ".tmp", "MAx Engine copie.zip"):
        assert backup.backup_family(autre) is None


def test_rotation_keeps_30_automatic_and_10_update_backups():
    """D72 : la 31e automatique fait partir la plus ancienne ; les « avant mise à jour » ont
    leur propre compte ; les manuelles et les fichiers inconnus restent."""
    automatiques = [nom_auto(1 + i // 2, 8 + i % 2 * 10) for i in range(31)]
    mises_a_jour = [nom_auto(i + 1, raison="mise-a-jour") for i in range(11)]
    manuelles = [backup.backup_name(dt.datetime(2026, 1, 1, 0, 0), "manuelle", "très vieille")]
    a_effacer = backup.files_to_rotate([*automatiques, *mises_a_jour, *manuelles, "notes.txt"])
    assert a_effacer == sorted([automatiques[0], mises_a_jour[0]])


def test_rotation_accepts_names_read_once():
    """Relecture : la liste lue dans le dossier n'est parcourue qu'une fois (générateur)."""
    noms = [nom_auto(i + 1, raison="mise-a-jour") for i in range(11)]
    assert backup.files_to_rotate(n for n in noms) == [noms[0]]


def test_rotation_mixes_closing_and_daily_backups():
    noms = [nom_auto(i + 1, raison="quotidienne") for i in range(15)]
    noms += [nom_auto(i + 1, 20) for i in range(16)]
    assert backup.files_to_rotate(noms) == [nom_auto(1, raison="quotidienne")]


def test_format_size():
    assert backup.format_size(500) == "1 ko"
    assert backup.format_size(350 * 1024) == "350 ko"
    assert backup.format_size(1258291) == "1,2 Mo"


# ---------------------------------------------------------------------------
# Indicateur de la barre latérale
# ---------------------------------------------------------------------------


def etat(succes=None, echec=None) -> backup.BackupState:
    return backup.BackupState(last_success=succes, last_failure=echec)


def test_sidebar_status_says_when():
    maintenant = MOMENT + dt.timedelta(hours=2)
    assert backup.sidebar_status(etat(MOMENT), maintenant) == backup.SidebarStatus(
        "Dernière sauvegarde : aujourd'hui à 18:02", False
    )
    hier = MOMENT - dt.timedelta(days=1)
    assert backup.sidebar_status(etat(hier), maintenant).text == (
        "Dernière sauvegarde : hier à 18:02"
    )


def test_sidebar_status_turns_amber_after_two_days():
    ancienne = MOMENT - dt.timedelta(days=2, minutes=1)
    statut = backup.sidebar_status(etat(ancienne), MOMENT)
    assert statut == backup.SidebarStatus("Dernière sauvegarde : le 02/11/2026 à 18:01", True)
    assert not backup.sidebar_status(etat(MOMENT - dt.timedelta(days=2)), MOMENT).warning


def test_sidebar_status_after_a_failure_or_without_backup():
    echec = backup.sidebar_status(etat(MOMENT, MOMENT + dt.timedelta(hours=1)), MOMENT)
    assert echec == backup.SidebarStatus("La dernière sauvegarde a échoué : préviens Mathis.", True)
    # Une réussite après l'échec efface l'alerte.
    reprise = etat(MOMENT + dt.timedelta(hours=2), MOMENT + dt.timedelta(hours=1))
    assert not backup.sidebar_status(reprise, MOMENT + dt.timedelta(hours=3)).warning
    assert backup.sidebar_status(etat(), MOMENT) == backup.SidebarStatus(
        "Aucune sauvegarde pour l'instant.", True
    )


def test_daily_backup_waits_24_hours():
    assert backup.daily_backup_needed(etat(), MOMENT)
    assert not backup.daily_backup_needed(etat(MOMENT), MOMENT + dt.timedelta(hours=23))
    assert backup.daily_backup_needed(etat(MOMENT), MOMENT + dt.timedelta(hours=24))


def test_state_file_survives_a_success_then_a_failure(settings):
    backup.record_success(MOMENT, "MAx Engine 2026-11-04 18-02 (fermeture).zip")
    backup.record_failure(MOMENT + dt.timedelta(hours=1), "OneDrive est fermé.")
    lu = backup.read_state()
    assert lu.last_success == MOMENT
    assert lu.last_file == "MAx Engine 2026-11-04 18-02 (fermeture).zip"
    assert lu.failed and lu.failure_message == "OneDrive est fermé."


@pytest.mark.parametrize("contenu", ["pas du json", '{"last_success": 5}', "[1, 2]"])
def test_unreadable_state_file_means_no_backup(settings, contenu):
    """Un fichier d'état abîmé ne fait jamais planter les pages (barre latérale)."""
    Path(settings.BACKUP_STATE_FILE).write_text(contenu, encoding="utf-8")
    assert backup.read_state() == backup.BackupState()


def test_sidebar_shows_the_last_backup(as_mathis):
    page = as_mathis.get("/").content.decode()
    assert "Aucune sauvegarde pour l&#x27;instant." in page
    backup.record_success(timezone.now(), "MAx Engine.zip")
    page = as_mathis.get("/").content.decode()
    assert "Dernière sauvegarde : aujourd&#x27;hui à" in page
    assert 'id="alerte-sauvegarde"' not in page


def test_sidebar_warns_in_amber_even_when_collapsed(as_mathis):
    """Sans sauvegarde : la ligne est en ambre (style de `core/ui.py`), et le menu replié
    garde une icône d'alerte avec le texte en infobulle."""
    page = as_mathis.get("/").content.decode()
    assert WARNING_BADGE in page
    assert 'id="alerte-sauvegarde" title="Aucune sauvegarde pour l&#x27;instant."' in page


def test_sidebar_hides_the_backup_in_read_only_mode(as_mathis, settings):
    settings.READ_ONLY = True
    assert "Aucune sauvegarde" not in as_mathis.get("/").content.decode()


# ---------------------------------------------------------------------------
# Dossier des sauvegardes
# ---------------------------------------------------------------------------


def test_folder_falls_back_to_the_project(db, settings):
    dossier = backup.backup_folder()
    assert dossier == backup.BackupFolder(Path(settings.BACKUP_FALLBACK_DIR), configured=False)


def test_folder_comes_from_the_settings_then_from_backup_dir(db, settings, tmp_path):
    GarageSettings.objects.filter(pk=1).update(backup_dir=str(tmp_path / "OneDrive"))
    assert backup.backup_folder() == backup.BackupFolder(tmp_path / "OneDrive", configured=True)
    settings.BACKUP_DIR = str(tmp_path / "variable")
    assert backup.backup_folder().path == tmp_path / "variable"


def test_missing_configured_folder_is_refused_in_french(db, settings, tmp_path):
    """Un dossier OneDrive réglé mais absent n'est pas créé à côté : message clair, échec
    noté pour la barre latérale, aucun fichier écrit."""
    settings.BACKUP_DIR = str(tmp_path / "OneDrive absent")
    with pytest.raises(CommandError, match="est introuvable. Vérifie que OneDrive est ouvert"):
        call_command("sauvegarder", raison="fermeture")
    assert not (tmp_path / "OneDrive absent").exists()
    assert backup.read_state().failed


def test_relative_configured_folder_is_refused(db, settings):
    """Un chemin incomplet (par exemple « %OneDrive% » non compris) est refusé."""
    settings.BACKUP_DIR = "%DOSSIER_INCONNU%\\MAx Engine - sauvegardes"
    with pytest.raises(CommandError, match="n'est pas un chemin complet"):
        call_command("sauvegarder", raison="fermeture")


def test_name_is_only_for_a_manual_backup(db):
    """Une sauvegarde « fermeture » nommée serait effacée par la rotation : refusée."""
    with pytest.raises(CommandError, match="ne se donne qu'à une sauvegarde manuelle"):
        call_command("sauvegarder", raison="fermeture", nom="avant import")


def test_missing_database_is_refused_without_creating_it(tmp_path, monkeypatch):
    """Relecture : sans base (milieu d'une restauration), rien n'est créé à sa place."""
    from django.db import connection

    absente = tmp_path / "donnees" / "garage.sqlite3"
    monkeypatch.setitem(connection.settings_dict, "NAME", str(absente))
    with pytest.raises(backup.BackupError, match="est introuvable : rien n'a été sauvegardé"):
        backup.database_uri()
    assert not absente.exists()


# ---------------------------------------------------------------------------
# La sauvegarde complète
# ---------------------------------------------------------------------------


def sauvegardes(dossier: Path) -> list[Path]:
    return sorted(p for p in dossier.iterdir() if p.is_file())


def lancer(*args, **options) -> str:
    from io import StringIO

    sortie = StringIO()
    call_command("sauvegarder", *args, stdout=sortie, stderr=sortie, **options)
    return sortie.getvalue()


@base_enregistree
def test_backup_copies_a_checked_database(make_vehicle, settings, tmp_path):
    make_vehicle()
    make_vehicle(brand="GG")
    sortie = lancer(raison="manuelle", nom="essais avant import")

    dossier = Path(settings.BACKUP_FALLBACK_DIR)
    (fichier,) = sauvegardes(dossier)
    assert fichier.name.endswith(" (manuelle - essais avant import).zip")
    assert "Base vérifiée : 2 véhicules, 0 dépense" in sortie
    assert "Attention : aucun dossier de sauvegarde n'est réglé" in sortie
    with zipfile.ZipFile(fichier) as archive:
        assert archive.namelist() == [backup.DATABASE_IN_ZIP]
        archive.extract(backup.DATABASE_IN_ZIP, tmp_path / "restauree")
    copie = sqlite3.connect(tmp_path / "restauree" / backup.DATABASE_IN_ZIP)
    try:
        assert copie.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert copie.execute("SELECT count(*) FROM vehicles_vehicle").fetchone() == (2,)
    finally:
        copie.close()
    assert backup.read_state().last_file == fichier.name


@base_enregistree
def test_restored_copy_passes_controle_comptes(make_vehicle, settings, tmp_path):
    """La restauration à blanc (sous-étape 7) en petit : la copie s'ouvre comme une vraie
    base et `controle_comptes` y passe."""
    make_vehicle()
    lancer(raison="fermeture")
    (fichier,) = sauvegardes(Path(settings.BACKUP_FALLBACK_DIR))
    with zipfile.ZipFile(fichier) as archive:
        archive.extract(backup.DATABASE_IN_ZIP, tmp_path)
    environnement = {**os.environ, "DATABASE_PATH": str(tmp_path / backup.DATABASE_IN_ZIP)}
    resultat = subprocess.run(
        [sys.executable, "manage.py", "controle_comptes"],
        cwd=reglages.BASE_DIR,
        env=environnement,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert "Contrôle des comptes" in resultat.stdout
    assert "1 véhicule" in resultat.stdout


@base_enregistree
def test_backup_goes_to_the_configured_folder_and_rotates(settings, tmp_path):
    dossier = tmp_path / "OneDrive" / "MAx Engine - sauvegardes"
    dossier.mkdir(parents=True)
    GarageSettings.objects.filter(pk=1).update(backup_dir=str(dossier))
    anciennes = [nom_auto(1 + i // 2, 8 + i % 2 * 10) for i in range(30)]
    manuelle = backup.backup_name(dt.datetime(2026, 1, 1, 0, 0), "manuelle", "à garder")
    for nom in [*anciennes, manuelle]:
        (dossier / nom).write_bytes(b"ancienne")

    sortie = lancer(raison="fermeture")
    noms = [p.name for p in sauvegardes(dossier)]
    assert anciennes[0] not in noms
    assert manuelle in noms
    assert len([n for n in noms if backup.backup_family(n) == backup.AUTOMATIC]) == 30
    assert "Rotation : 1 ancienne sauvegarde effacée" in sortie
    assert "Attention" not in sortie


@base_enregistree
def test_two_backups_in_the_same_minute_keep_both(settings):
    lancer(raison="manuelle")
    lancer(raison="manuelle")
    assert len(sauvegardes(Path(settings.BACKUP_FALLBACK_DIR))) == 2


@base_enregistree
def test_media_mirror_copies_only_new_files(settings):
    """D71 : les fichiers joints sont recopiés une fois ; un fichier déjà présent dans le
    miroir n'est jamais réécrit, et le miroir n'efface rien."""
    media = Path(settings.MEDIA_ROOT)
    (media / "vehicules" / "1").mkdir(parents=True)
    (media / "vehicules" / "1" / "photo.jpg").write_bytes(b"photo")
    sortie = lancer(raison="fermeture")
    assert "Fichiers joints : 1 nouveau copié sur 1" in sortie
    miroir = Path(settings.BACKUP_FALLBACK_DIR) / backup.MIRROR
    assert (miroir / "vehicules" / "1" / "photo.jpg").read_bytes() == b"photo"

    (media / "vehicules" / "1" / "photo.jpg").write_bytes(b"autre")
    (media / "facture.pdf").write_bytes(b"facture")
    sortie = lancer(raison="fermeture")
    assert "Fichiers joints : 1 nouveau copié sur 2" in sortie
    assert (miroir / "vehicules" / "1" / "photo.jpg").read_bytes() == b"photo"
    (media / "facture.pdf").unlink()
    lancer(raison="fermeture")
    assert (miroir / "facture.pdf").exists()


@base_enregistree
def test_daily_backup_is_skipped_within_24_hours(settings):
    lancer(raison="quotidienne")
    assert "moins de 24 heures" in lancer(raison="quotidienne")
    assert len(sauvegardes(Path(settings.BACKUP_FALLBACK_DIR))) == 1


@base_enregistree
def test_damaged_backup_leaves_no_temporary_file(settings, monkeypatch):
    """Une erreur après l'écriture du zip provisoire : rien ne reste dans le dossier (ni
    sauvegarde, ni `.tmp`), l'échec est noté."""
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: backup.DATABASE_IN_ZIP)
    with pytest.raises(CommandError, match="Le fichier de sauvegarde est abîmé"):
        lancer(raison="fermeture")
    assert sauvegardes(Path(settings.BACKUP_FALLBACK_DIR)) == []
    assert backup.read_state().failed


def test_damaged_database_copy_is_refused(tmp_path):
    """Le contrôle d'intégrité refuse une copie abîmée."""
    source = tmp_path / "source.sqlite3"
    source.write_bytes(b"ceci n'est pas une base SQLite" * 100)
    with pytest.raises(backup.BackupError, match="préviens Mathis"):
        backup.copy_database(source.as_uri() + "?mode=ro", tmp_path / "copie.sqlite3")


def test_busy_database_gives_up_after_the_timeout(tmp_path, monkeypatch):
    """Une base bloquée par une écriture : erreur claire au lieu d'une attente sans fin."""
    monkeypatch.setattr(backup, "BUSY_TIMEOUT", 0.2)
    source = tmp_path / "source.sqlite3"
    ecriture = sqlite3.connect(source, isolation_level=None)
    try:
        ecriture.execute("CREATE TABLE t (x)")
        ecriture.execute("BEGIN EXCLUSIVE")
        with pytest.raises(backup.BackupError, match="Ferme MAx Engine et recommence"):
            backup.copy_database(source.as_uri() + "?mode=ro", tmp_path / "copie.sqlite3")
    finally:
        ecriture.execute("ROLLBACK")
        ecriture.close()


def test_backup_never_writes_the_source(tmp_path):
    """La base est ouverte en lecture seule : la sauvegarde ne peut pas la modifier."""
    source = tmp_path / "source.sqlite3"
    base = sqlite3.connect(source)
    base.execute("CREATE TABLE vehicles_vehicle (id, deleted_at)")
    base.execute("CREATE TABLE expenses_expense (id, deleted_at)")
    base.commit()
    base.close()
    avant = source.read_bytes()
    controle = backup.copy_database(source.as_uri() + "?mode=ro", tmp_path / "copie.sqlite3")
    assert controle == backup.CopyCheck(vehicles=0, expenses=0)
    assert source.read_bytes() == avant


@base_enregistree
def test_rotation_of_update_backups_through_the_command(settings):
    """D72, relecture : la 11e sauvegarde « mise-a-jour » fait partir la plus ancienne."""
    dossier = Path(settings.BACKUP_FALLBACK_DIR)
    dossier.mkdir(parents=True)
    anciennes = [nom_auto(i + 1, raison="mise-a-jour") for i in range(10)]
    for nom in anciennes:
        (dossier / nom).write_bytes(b"ancienne")
    sortie = lancer(raison="mise-a-jour")
    noms = [p.name for p in sauvegardes(dossier)]
    assert anciennes[0] not in noms
    assert len([n for n in noms if backup.backup_family(n) == backup.UPDATE]) == 10
    assert "Rotation : 1 ancienne sauvegarde effacée" in sortie


@base_enregistree
def test_trash_is_counted_apart_like_controle_comptes(make_vehicle):
    """Le gardien des données : « 8 véhicules » contre « 6 » dans `controle_comptes` ferait
    croire à une perte. La corbeille est comptée à part."""
    make_vehicle()
    make_vehicle(brand="GG").soft_delete()
    assert "Base vérifiée : 1 véhicule (+ 1 à la corbeille), 0 dépense" in lancer()


@base_enregistree
def test_unexpected_error_is_reported_in_french(settings, monkeypatch):
    """Une erreur imprévue donne un message, jamais une trace Python, et elle est notée."""

    def panne(*args, **kwargs):
        raise RuntimeError("disque débranché")

    monkeypatch.setattr(backup, "copy_database", panne)
    with pytest.raises(CommandError, match="Erreur inattendue \\(disque débranché\\)"):
        lancer(raison="fermeture")
    assert backup.read_state().failed
    assert sauvegardes(Path(settings.BACKUP_FALLBACK_DIR)) == []


@base_enregistree
def test_backup_done_even_if_the_state_cannot_be_written(settings, monkeypatch):
    def refus(*args, **kwargs):
        raise PermissionError("fichier verrouillé")

    monkeypatch.setattr(backup, "record_success", refus)
    sortie = lancer(raison="fermeture")
    assert "Sauvegarde faite" in sortie
    assert "« Dernière sauvegarde » n'a pas pu être mis à jour" in sortie
    assert len(sauvegardes(Path(settings.BACKUP_FALLBACK_DIR))) == 1


def test_copy_works_with_spaces_accents_and_parentheses(tmp_path, monkeypatch):
    """Comme le dossier du projet : « Mes projets\\Logiciel (garage) »."""
    from django.db import connection

    dossier = tmp_path / "Mes projets" / "Logiciel (garage) é"
    dossier.mkdir(parents=True)
    source = dossier / "garage.sqlite3"
    base = sqlite3.connect(source)
    base.execute("CREATE TABLE vehicles_vehicle (id, deleted_at)")
    base.execute("CREATE TABLE expenses_expense (id, deleted_at)")
    base.execute("INSERT INTO vehicles_vehicle VALUES (1, NULL)")
    base.commit()
    base.close()
    monkeypatch.setitem(connection.settings_dict, "NAME", str(source))
    controle = backup.copy_database(backup.database_uri(), tmp_path / "copie.sqlite3")
    assert controle == backup.CopyCheck(vehicles=1, expenses=0)


@base_enregistree
def test_new_backup_survives_a_clock_that_went_back(settings):
    """Relecture : 30 sauvegardes datées « dans le futur » (horloge du PC qui a reculé) ;
    la nouvelle serait triée la plus ancienne, mais elle n'est jamais effacée."""
    dossier = Path(settings.BACKUP_FALLBACK_DIR)
    dossier.mkdir(parents=True)
    for i in range(30):
        (dossier / backup.backup_name(dt.datetime(2099, 1, 1 + i, 8, 0), "fermeture")).write_bytes(
            b"future"
        )
    lancer(raison="fermeture")
    assert backup.read_state().last_file in [p.name for p in sauvegardes(dossier)]

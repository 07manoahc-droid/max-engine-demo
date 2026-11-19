"""Sauvegarde de la base et des fichiers joints (D70 à D73).

Une sauvegarde est un fichier `MAx Engine AAAA-MM-JJ HH-MM (raison).zip` qui contient la
copie de la base (`garage.sqlite3`). Les fichiers joints (photos, factures) sont recopiés à
part, une seule fois, dans le dossier miroir `fichiers/` : on n'y ajoute que les nouveaux.

Sûreté :
- la base est lue par une connexion à part, en lecture seule, avec l'API de sauvegarde de
  SQLite : la copie est cohérente même si le logiciel est ouvert, et la base n'est jamais
  modifiée (D04) ;
- la copie est contrôlée (`PRAGMA integrity_check`, nombre de véhicules et de dépenses)
  avant d'être déclarée réussie ;
- tout s'écrit d'abord sous un nom provisoire en `.tmp`, renommé à la fin : OneDrive ne
  voit jamais une sauvegarde à moitié écrite, et ne synchronise pas les `.tmp`.

Le résultat de la dernière sauvegarde est noté dans un petit fichier d'état
(`settings.BACKUP_STATE_FILE`), lu par la barre latérale.
"""

import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from core.formatting import format_moment

# Raisons d'une sauvegarde (option `--raison`) et la famille qui fixe sa rotation (D72).
AUTOMATIC = "automatique"
UPDATE = "mise-a-jour"
MANUAL = "manuelle"
REASONS = {
    "fermeture": AUTOMATIC,
    "quotidienne": AUTOMATIC,
    "mise-a-jour": UPDATE,
    "manuelle": MANUAL,
}
# Combien on en garde par famille ; les manuelles ne sont jamais effacées.
KEEP = {AUTOMATIC: 30, UPDATE: 10}

PREFIX = "MAx Engine"
DATABASE_IN_ZIP = "garage.sqlite3"
MIRROR = "fichiers"
TEMP_SUFFIX = ".tmp"
# La sauvegarde « quotidienne » est sautée s'il y en a eu une réussie depuis moins que ça.
DAILY_INTERVAL = dt.timedelta(hours=24)
# Sans sauvegarde réussie depuis plus longtemps, la barre latérale passe en ambre.
STALE_AFTER = dt.timedelta(days=2)
# Délai d'attente si la base est occupée par une écriture (en secondes).
BUSY_TIMEOUT = 10

NAME_PATTERN = re.compile(
    rf"^{PREFIX} (?P<moment>\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}-\d{{2}})(?: \d+)? "
    rf"\((?P<reason>{'|'.join(REASONS)})(?: - .*)?\)\.zip$"
)
# Caractères interdits dans un nom de fichier Windows.
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class BackupError(Exception):
    """Une sauvegarde impossible, avec un message en français pour l'associé."""


# ---------------------------------------------------------------------------
# Noms et rotation (fonctions pures)
# ---------------------------------------------------------------------------


def clean_label(label: str) -> str:
    """Le nom donné à une sauvegarde manuelle, sans caractère interdit sous Windows."""
    return " ".join(FORBIDDEN.sub(" ", label).split())[:60].rstrip(" .")


def backup_name(moment: dt.datetime, reason: str, label: str = "", rank: int = 1) -> str:
    """« MAx Engine 2026-11-04 18-02 (fermeture).zip », ou « … (manuelle - essais).zip ».

    `rank` distingue deux sauvegardes de la même raison dans la même minute (« 18-02 2 »).
    """
    horodatage = f"{moment:%Y-%m-%d %H-%M}" + (f" {rank}" if rank > 1 else "")
    nom = clean_label(label)
    raison = f"{reason} - {nom}" if nom else reason
    return f"{PREFIX} {horodatage} ({raison}).zip"


def backup_family(name: str) -> str | None:
    """La famille d'un fichier de sauvegarde (automatique, mise-a-jour, manuelle), ou None
    si le fichier n'est pas une sauvegarde : il n'est alors jamais effacé."""
    trouve = NAME_PATTERN.match(name)
    return REASONS[trouve["reason"]] if trouve else None


def backup_moment(name: str) -> dt.datetime | None:
    """L'heure (de Paris) écrite dans le nom d'une sauvegarde, ou None si ce n'en est pas une."""
    trouve = NAME_PATTERN.match(Path(name).name)
    if not trouve:
        return None
    moment = dt.datetime.strptime(trouve["moment"], "%Y-%m-%d %H-%M")
    return timezone.make_aware(moment)


def read_only_banner(source: str) -> str:
    """Le bandeau de la consultation (D79), daté par le nom de la sauvegarde ouverte."""
    moment = backup_moment(source) if source else None
    if moment is None:
        return "Consultation en lecture seule : rien ne peut être modifié ici."
    return (
        f"Consultation de la sauvegarde du {moment:%d/%m/%Y} à {moment:%H:%M} : "
        "rien ne peut être modifié ici."
    )


def files_to_rotate(names: Iterable[str]) -> list[str]:
    """Les sauvegardes à effacer : au-delà des 30 automatiques et des 10 « avant mise à
    jour » les plus récentes (D72). Les noms commencent par la date : l'ordre alphabétique
    est l'ordre chronologique."""
    names = list(names)  # parcourue une fois par famille : un générateur serait vidé
    a_effacer: list[str] = []
    for famille, garder in KEEP.items():
        noms = sorted(n for n in names if backup_family(n) == famille)
        a_effacer += noms[: max(len(noms) - garder, 0)]
    return sorted(a_effacer)


def format_size(size: int) -> str:
    """« 350 ko », « 1,2 Mo »."""
    if size < 1024 * 1024:
        return f"{max(round(size / 1024), 1)} ko"
    return f"{size / (1024 * 1024):.1f} Mo".replace(".", ",")


# ---------------------------------------------------------------------------
# Dossier des sauvegardes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupFolder:
    """Le dossier des sauvegardes et d'où vient ce choix."""

    path: Path
    configured: bool  # False : repli sur `sauvegardes/` du projet, avec un avertissement


def backup_folder() -> BackupFolder:
    """La variable `BACKUP_DIR` passe avant les paramètres du garage ; sans l'un ni l'autre,
    les sauvegardes vont dans `sauvegardes/` du projet (D70). Lecture seule de la base."""
    from core.models import GarageSettings

    chemin = settings.BACKUP_DIR or (
        GarageSettings.objects.filter(pk=1).values_list("backup_dir", flat=True).first() or ""
    )
    if chemin.strip():
        return BackupFolder(Path(os.path.expandvars(chemin.strip())), configured=True)
    return BackupFolder(Path(settings.BACKUP_FALLBACK_DIR), configured=False)


def ready_folder(dossier: BackupFolder) -> Path:
    """Le dossier, vérifié. Un dossier réglé doit déjà exister (OneDrive peut être fermé ou
    pas encore synchronisé : on ne crée pas un dossier à côté) ; le dossier de repli est
    créé au besoin."""
    if not dossier.configured:
        dossier.path.mkdir(parents=True, exist_ok=True)
    if not dossier.path.is_absolute():  # par exemple « %OneDrive% » non compris
        raise BackupError(
            f"Le dossier des sauvegardes « {dossier.path} » n'est pas un chemin complet "
            "(comme C:\\Users\\Axel\\OneDrive\\MAx Engine - sauvegardes) : préviens Mathis."
        )
    if not dossier.path.is_dir():
        raise BackupError(
            f"Le dossier des sauvegardes « {dossier.path} » est introuvable. Vérifie que "
            "OneDrive est ouvert et synchronisé, puis recommence ; sinon, préviens Mathis."
        )
    return dossier.path


# ---------------------------------------------------------------------------
# Copie de la base
# ---------------------------------------------------------------------------


def database_uri() -> str:
    """L'adresse de la base pour une connexion à part, en lecture seule.

    Une base absente est refusée avant toute requête : Django créerait sinon un fichier vide
    à sa place (par exemple au milieu d'une restauration).
    """
    nom = str(connection.settings_dict["NAME"])
    if nom.startswith("file:"):  # base en mémoire des tests, déjà une adresse
        return nom
    if not Path(nom).is_file():
        raise BackupError(f"La base « {nom} » est introuvable : rien n'a été sauvegardé.")
    return Path(nom).resolve().as_uri() + "?mode=ro"


@dataclass(frozen=True)
class CopyCheck:
    """Ce que contient la copie, lu après le contrôle d'intégrité. Les nombres sont ceux
    de `controle_comptes` (hors corbeille) ; la corbeille est comptée à part."""

    vehicles: int
    expenses: int
    vehicles_in_trash: int = 0
    expenses_in_trash: int = 0


def copy_database(source_uri: str, cible: Path) -> CopyCheck:
    """Copie la base dans `cible` avec l'API de sauvegarde de SQLite, puis la contrôle.

    La source est ouverte en lecture seule. Une lecture est d'abord réservée, avec un délai
    d'attente : si la base reste occupée, l'erreur arrive après `BUSY_TIMEOUT` secondes au
    lieu d'une attente sans fin.
    """
    from expenses.models import Expense
    from vehicles.models import Vehicle

    try:
        source = sqlite3.connect(source_uri, uri=True, timeout=BUSY_TIMEOUT, isolation_level=None)
    except sqlite3.Error as erreur:
        raise BackupError(f"La base ne s'ouvre pas ({erreur}) : préviens Mathis.") from erreur
    try:
        copie = sqlite3.connect(cible)
        try:
            source.execute("BEGIN")
            source.execute("SELECT count(*) FROM sqlite_master").fetchone()
            source.backup(copie)
            source.execute("COMMIT")
            verdict = copie.execute("PRAGMA integrity_check").fetchone()[0]
            if verdict != "ok":
                raise BackupError(f"La copie de la base est abîmée ({verdict}) : préviens Mathis.")
            vehicules, depenses = Vehicle._meta.db_table, Expense._meta.db_table
            return CopyCheck(
                vehicles=_count(copie, vehicules, "deleted_at IS NULL"),
                expenses=_count(copie, depenses, "deleted_at IS NULL"),
                vehicles_in_trash=_count(copie, vehicules, "deleted_at IS NOT NULL"),
                expenses_in_trash=_count(copie, depenses, "deleted_at IS NOT NULL"),
            )
        finally:
            copie.close()
    except sqlite3.Error as erreur:
        raise BackupError(
            f"La base n'a pas pu être copiée ({erreur}). Ferme MAx Engine et recommence ; "
            "sinon, préviens Mathis."
        ) from erreur
    finally:
        source.close()


def _count(base: sqlite3.Connection, table: str, condition: str) -> int:
    return base.execute(f'SELECT count(*) FROM "{table}" WHERE {condition}').fetchone()[0]


# ---------------------------------------------------------------------------
# Fichiers joints : le miroir
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MirrorResult:
    copied: int
    total: int


def mirror_media(source: Path, miroir: Path) -> MirrorResult:
    """Recopie dans `miroir` les fichiers joints qui n'y sont pas encore (D71).

    Un fichier joint ne change jamais (une nouvelle photo a un nouveau nom) et n'est jamais
    effacé (corbeille logique) : un fichier déjà présent n'est ni recopié ni modifié, et
    le miroir n'efface rien. Les miniatures (`miniatures/`, D92) ne sont pas recopiées :
    ce ne sont que des copies réduites, refaites quand elles manquent.
    """
    if not source.is_dir():
        return MirrorResult(0, 0)
    miniatures = source / settings.THUMBNAILS_FOLDER
    copies = total = 0
    for fichier in sorted(source.rglob("*")):
        if not fichier.is_file() or fichier.name.endswith(TEMP_SUFFIX):
            continue
        if fichier.is_relative_to(miniatures):
            continue
        total += 1
        cible = miroir / fichier.relative_to(source)
        if cible.exists():
            continue
        cible.parent.mkdir(parents=True, exist_ok=True)
        provisoire = cible.with_name(cible.name + TEMP_SUFFIX)
        try:
            shutil.copy2(fichier, provisoire)
            provisoire.replace(cible)
        finally:
            provisoire.unlink(missing_ok=True)
        copies += 1
    return MirrorResult(copies, total)


# ---------------------------------------------------------------------------
# La sauvegarde complète
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupResult:
    folder: BackupFolder
    file: Path
    size: int
    check: CopyCheck
    media: MirrorResult
    rotated: list[str]
    state_saved: bool = True  # False : la sauvegarde est faite, mais pas notée


def make_backup(reason: str, label: str = "", now: dt.datetime | None = None) -> BackupResult:
    """Fait une sauvegarde complète et note son résultat (réussite ou échec).

    Ordre : la base d'abord (le plus précieux), puis les fichiers joints, puis la rotation.
    En cas d'erreur : `BackupError`, aucun fichier provisoire ne reste, l'échec est noté.
    """
    if reason not in REASONS:
        raise BackupError(f"Raison inconnue : « {reason} ».")
    moment = timezone.localtime(now)
    try:
        source = database_uri()  # avant toute requête : voir `database_uri`
        dossier = backup_folder()
        resultat = _backup_into(dossier, ready_folder(dossier), source, reason, label, moment)
    except BackupError as erreur:
        record_failure(moment, str(erreur))
        raise
    except OSError as erreur:
        message = (
            f"La sauvegarde n'a pas pu être écrite ({erreur.strerror or erreur}). Vérifie "
            "qu'il reste de la place et que OneDrive est ouvert ; sinon, préviens Mathis."
        )
        record_failure(moment, message)
        raise BackupError(message) from erreur
    except Exception as erreur:  # base illisible, zip abîmé… : jamais une trace Python
        message = f"Erreur inattendue ({erreur}) : préviens Mathis."
        record_failure(moment, message)
        raise BackupError(message) from erreur
    try:
        record_success(moment, resultat.file.name)
    except OSError:
        return dataclasses.replace(resultat, state_saved=False)
    return resultat


def _backup_into(
    dossier: BackupFolder,
    chemin: Path,
    source: str,
    reason: str,
    label: str,
    moment: dt.datetime,
) -> BackupResult:
    rang = 1
    while (chemin / backup_name(moment, reason, label, rang)).exists():
        rang += 1
    final = chemin / backup_name(moment, reason, label, rang)
    provisoire = final.with_name(final.name + TEMP_SUFFIX)
    try:
        # La copie de la base se fait sur ce PC (dossier temporaire), pas dans OneDrive.
        with tempfile.TemporaryDirectory(prefix="max-engine-") as temporaire:
            copie = Path(temporaire) / DATABASE_IN_ZIP
            controle = copy_database(source, copie)
            with zipfile.ZipFile(provisoire, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(copie, DATABASE_IN_ZIP)
        with zipfile.ZipFile(provisoire) as archive:
            if archive.testzip() is not None:
                raise BackupError("Le fichier de sauvegarde est abîmé : préviens Mathis.")
        # `rename` refuse d'écraser un fichier existant sous Windows (deux sauvegardes
        # lancées en même temps) : l'erreur est signalée, rien n'est perdu.
        provisoire.rename(final)
    finally:
        provisoire.unlink(missing_ok=True)

    fichiers = mirror_media(Path(settings.MEDIA_ROOT), chemin / MIRROR)
    # Celle qu'on vient d'écrire compte, mais n'est jamais effacée, même si l'horloge du PC
    # a reculé (elle serait alors triée parmi les plus anciennes).
    noms = [p.name for p in chemin.iterdir() if p.is_file()]
    effaces = [nom for nom in files_to_rotate(noms) if nom != final.name]
    for nom in effaces:
        (chemin / nom).unlink(missing_ok=True)
    return BackupResult(dossier, final, final.stat().st_size, controle, fichiers, effaces)


# ---------------------------------------------------------------------------
# État de la dernière sauvegarde (barre latérale)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupState:
    last_success: dt.datetime | None = None
    last_file: str = ""
    last_failure: dt.datetime | None = None
    failure_message: str = ""

    @property
    def failed(self) -> bool:
        """La dernière tentative a échoué (aucune réussite depuis)."""
        if self.last_failure is None:
            return False
        return self.last_success is None or self.last_failure > self.last_success


def read_state() -> BackupState:
    """L'état noté par la dernière sauvegarde ; vide si le fichier manque ou est illisible."""
    try:
        brut = json.loads(Path(settings.BACKUP_STATE_FILE).read_text(encoding="utf-8"))
        return BackupState(
            last_success=_moment(brut.get("last_success")),
            last_file=brut.get("last_file", ""),
            last_failure=_moment(brut.get("last_failure")),
            failure_message=brut.get("failure_message", ""),
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return BackupState()


def _moment(texte: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(texte) if texte else None


def _write_state(etat: BackupState) -> None:
    fichier = Path(settings.BACKUP_STATE_FILE)
    fichier.parent.mkdir(parents=True, exist_ok=True)
    contenu = {
        "last_success": etat.last_success.isoformat() if etat.last_success else None,
        "last_file": etat.last_file,
        "last_failure": etat.last_failure.isoformat() if etat.last_failure else None,
        "failure_message": etat.failure_message,
    }
    provisoire = fichier.with_name(fichier.name + TEMP_SUFFIX)
    provisoire.write_text(json.dumps(contenu, ensure_ascii=False, indent=2), encoding="utf-8")
    provisoire.replace(fichier)


def record_success(moment: dt.datetime, nom: str) -> None:
    ancien = read_state()
    _write_state(BackupState(moment, nom, ancien.last_failure, ancien.failure_message))


def record_failure(moment: dt.datetime, message: str) -> None:
    ancien = read_state()
    try:
        _write_state(BackupState(ancien.last_success, ancien.last_file, moment, message))
    except OSError:
        pass  # l'échec de la sauvegarde est déjà signalé par la commande


def daily_backup_needed(etat: BackupState, now: dt.datetime) -> bool:
    """La sauvegarde « quotidienne » ne se fait que sans réussite depuis 24 heures (D73)."""
    return etat.last_success is None or now - etat.last_success >= DAILY_INTERVAL


@dataclass(frozen=True)
class SidebarStatus:
    text: str
    warning: bool  # en ambre : sauvegarde ancienne, absente ou échouée


def sidebar_status(etat: BackupState, now: dt.datetime) -> SidebarStatus:
    """La ligne « Dernière sauvegarde » du bas de la barre latérale."""
    if etat.failed:
        return SidebarStatus("La dernière sauvegarde a échoué : préviens Mathis.", True)
    if etat.last_success is None:
        return SidebarStatus("Aucune sauvegarde pour l'instant.", True)
    quand = format_moment(etat.last_success, now)
    return SidebarStatus(f"Dernière sauvegarde : {quand}", now - etat.last_success > STALE_AFTER)


def current_sidebar_status() -> SidebarStatus:
    return sidebar_status(read_state(), timezone.now())

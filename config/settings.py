"""Réglages Django de MAx Engine.

Quelques réglages se changent par variables d'environnement (valeurs par défaut sûres) :
DATABASE_PATH, MEDIA_PATH, READ_ONLY (et READ_ONLY_SOURCE), BACKUP_DIR, DEBUG, SECRET_KEY.
"""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    """Lit une variable d'environnement booléenne (1, true, oui)."""
    valeur = os.environ.get(name)
    if valeur is None:
        return default
    return valeur.strip().lower() in {"1", "true", "oui", "yes"}


def load_secret_key(fichier: Path | None = None) -> str:
    """Clé secrète : variable SECRET_KEY, sinon fichier local créé au premier lancement.

    Le fichier vit dans `donnees/` (hors Git). La clé doit rester stable : elle signe les
    sessions, qui durent un an. Un fichier vide (arrêt pendant l'écriture) est régénéré ;
    l'écriture passe par un fichier temporaire renommé, donc jamais à moitié faite.
    """
    depuis_env = os.environ.get("SECRET_KEY")
    if depuis_env:
        return depuis_env
    fichier = fichier or BASE_DIR / "donnees" / "secret.key"
    if fichier.exists():
        cle = fichier.read_text(encoding="utf-8").strip()
        if cle:
            return cle
    fichier.parent.mkdir(exist_ok=True)
    cle = secrets.token_urlsafe(50)
    temporaire = fichier.with_name(fichier.name + ".tmp")
    temporaire.write_text(cle, encoding="utf-8")
    temporaire.replace(fichier)
    return cle


SECRET_KEY = load_secret_key()

# Sûr par défaut : seul manage.py active DEBUG pour le développement (décision D26).
DEBUG = env_bool("DEBUG", False)

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Consultation d'une sauvegarde (D79) : base ouverte en lecture seule par SQLite, sessions
# en cookie, tout envoi refusé, actions masquées, pas de choix d'associé.
READ_ONLY = env_bool("READ_ONLY", False)
# Le nom du fichier de sauvegarde consulté (« MAx Engine 2026-11-04 18-02 (fermeture).zip »),
# posé par le script de consultation (D80) : le bandeau en tire la date.
READ_ONLY_SOURCE = os.environ.get("READ_ONLY_SOURCE", "")

# Dossier des sauvegardes (OneDrive, D70) : la variable passe avant les paramètres du garage
# (`GarageSettings.backup_dir`) ; sans l'un ni l'autre, repli sur `sauvegardes/` du projet.
BACKUP_DIR = os.environ.get("BACKUP_DIR", "")
BACKUP_FALLBACK_DIR = BASE_DIR / "sauvegardes"


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Bibliothèques
    "django_htmx",
    "django_tailwind_cli",
    "simple_history",
    "template_partials",
    # Applications du garage
    "core",
    "vehicles",
    "expenses",
    "sales",
    "accounting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Consultation (D79) : refuse tout envoi et toute page d'action, avant le choix d'associé.
    "core.middleware.ReadOnlyMiddleware",
    "core.middleware.CurrentPartnerMiddleware",
    # Rend la requête (donc l'associé courant) visible par l'historique (D30).
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.garage",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Base de données : un seul fichier SQLite, sur le disque local, jamais dans OneDrive (D04).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH") or BASE_DIR / "donnees" / "garage.sqlite3",
        # Une transaction réserve l'écriture dès son début : deux envois simultanés s'attendent
        # au lieu de finir en « database is locked » (D48).
        "OPTIONS": {"transaction_mode": "IMMEDIATE"},
    }
}
# Résultat de la dernière sauvegarde (barre latérale), rangé à côté de la base.
BACKUP_STATE_FILE = Path(DATABASES["default"]["NAME"]).parent / "derniere-sauvegarde.json"


def read_only_database(reglages: dict) -> dict:
    """La base en lecture seule (D79) : SQLite lui-même refuse toute écriture (adresse
    `file:…?mode=ro`, que Django ouvre en mode URI). Sans `IMMEDIATE` : ce mode réserve
    l'écriture au début de chaque transaction, ce qu'un fichier en lecture seule refuse."""
    adresse = Path(reglages["NAME"]).resolve().as_uri() + "?mode=ro"
    options = {cle: v for cle, v in reglages["OPTIONS"].items() if cle != "transaction_mode"}
    return {**reglages, "NAME": adresse, "OPTIONS": options}


if READ_ONLY:
    DATABASES["default"] = read_only_database(DATABASES["default"])
    # Les sessions s'écriraient sinon dans la base.
    SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Langue et heure
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# L'associé choisi au lancement reste en mémoire un an (décision D28).
SESSION_COOKIE_AGE = 365 * 24 * 3600


# Fichiers statiques (servis depuis static/, aucun CDN) et fichiers joints
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
# MEDIA_PATH : les fichiers joints d'une autre base (répétition de l'import, consultation
# d'une sauvegarde), pour ne jamais mêler ses photos à celles de `media/`.
MEDIA_ROOT = Path(os.environ.get("MEDIA_PATH") or BASE_DIR / "media")
# Photos envoyées d'un coup, au plus (valeur de Django, écrite ici parce que l'écran la
# cite : au-delà, un message clair, D92).
DATA_UPLOAD_MAX_NUMBER_FILES = 100
# Dossier des miniatures des photos dans MEDIA_ROOT (D92) : jamais sauvegardé. Ici plutôt
# que dans `core.thumbnails`, pour que la sauvegarde n'ait pas besoin de Pillow.
THUMBNAILS_FOLDER = "miniatures"

# Tailwind : source dans styles/, CSS construit dans static/css (commité, décision D25).
TAILWIND_CLI_SRC_CSS = "styles/input.css"
TAILWIND_CLI_DIST_CSS = "css/tailwind.css"
TAILWIND_CLI_VERSION = "4.3.3"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

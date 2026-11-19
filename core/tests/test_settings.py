"""Tests des réglages : clé secrète et variables d'environnement."""

import pytest

from config import settings as project_settings


@pytest.fixture(autouse=True)
def no_secret_key_in_environment(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)


def test_secret_key_from_environment_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "cle-de-test")
    assert project_settings.load_secret_key(tmp_path / "secret.key") == "cle-de-test"


def test_secret_key_is_created_once_then_reused(tmp_path):
    fichier = tmp_path / "donnees" / "secret.key"
    premiere = project_settings.load_secret_key(fichier)
    assert len(premiere) >= 50
    assert fichier.read_text(encoding="utf-8") == premiere
    assert project_settings.load_secret_key(fichier) == premiere


def test_empty_secret_key_file_is_regenerated(tmp_path):
    """Un fichier vide (arrêt pendant l'écriture) ne doit pas empêcher le démarrage."""
    fichier = tmp_path / "secret.key"
    fichier.write_text("", encoding="utf-8")
    cle = project_settings.load_secret_key(fichier)
    assert cle
    assert fichier.read_text(encoding="utf-8") == cle


def test_no_temporary_file_is_left_behind(tmp_path):
    project_settings.load_secret_key(tmp_path / "secret.key")
    assert [f.name for f in tmp_path.iterdir()] == ["secret.key"]


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [("1", True), ("true", True), ("OUI", True), ("0", False), ("non", False), ("", False)],
)
def test_env_bool(monkeypatch, valeur, attendu):
    monkeypatch.setenv("MAX_TEST_FLAG", valeur)
    assert project_settings.env_bool("MAX_TEST_FLAG") is attendu


def test_env_bool_uses_the_safe_default_when_unset(monkeypatch):
    monkeypatch.delenv("MAX_TEST_FLAG", raising=False)
    assert project_settings.env_bool("MAX_TEST_FLAG") is False
    assert project_settings.env_bool("MAX_TEST_FLAG", True) is True

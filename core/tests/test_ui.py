"""Tests de la table des couleurs (core/ui.py) et des filtres de gabarit."""

from pathlib import Path

from django.conf import settings

from core.templatetags import ui_tags
from core.ui import NEUTRAL_STYLE, PART_STATUS_STYLES, PARTNER_STYLES, STATUS_STYLES
from expenses.models import ExpenseLine

EXPECTED_STATUSES = [
    "WAITING_PURCHASE",
    "PURCHASED",
    "IN_REPAIR",
    "READY_FOR_SALE",
    "FOR_SALE",
    "RESERVED",
    "SOLD",
    "ABANDONED",
]
BUILT_CSS = Path(settings.BASE_DIR) / "static" / "css" / "tailwind.css"


def test_all_eight_statuses_have_a_french_label():
    assert list(STATUS_STYLES) == EXPECTED_STATUSES
    assert STATUS_STYLES["IN_REPAIR"].label == "En réparation"
    assert STATUS_STYLES["WAITING_PURCHASE"].label == "En attente d'achat"


def test_both_partners_have_a_style():
    assert set(PARTNER_STYLES) == {"indigo", "teal"}


def test_blue_is_never_used_for_a_status():
    """Le bleu est réservé aux actions (charte) : aucun statut n'utilise la couleur accent."""
    for style in STATUS_STYLES.values():
        assert "accent" not in style.badge + style.dot


def test_every_class_of_ui_exists_in_the_built_css():
    """Le CSS commité contient toutes les classes de couleur (sinon : relancer tailwind build)."""
    css = BUILT_CSS.read_text(encoding="utf-8")
    styles = [*STATUS_STYLES.values(), *PARTNER_STYLES.values(), *PART_STATUS_STYLES.values()]
    for style in styles:
        for name in (style.badge + " " + style.dot).split():
            assert f".{name}" in css, f"{name} manque dans static/css/tailwind.css"


def test_template_filters_read_the_ui_table():
    assert ui_tags.status_label("SOLD") == "Vendu"
    assert ui_tags.status_badge("FOR_SALE") == STATUS_STYLES["FOR_SALE"].badge
    assert ui_tags.status_dot("RESERVED") == STATUS_STYLES["RESERVED"].dot
    assert ui_tags.partner_badge("teal") == PARTNER_STYLES["teal"].badge


def test_unknown_keys_give_the_neutral_look_instead_of_an_error():
    """Une couleur ou un statut inconnu ne doit jamais faire planter une page."""
    assert ui_tags.partner_badge("fuchsia") == NEUTRAL_STYLE.badge
    assert ui_tags.status_badge("INCONNU") == NEUTRAL_STYLE.badge
    assert ui_tags.status_dot("INCONNU") == NEUTRAL_STYLE.dot
    assert ui_tags.status_label("INCONNU") == "INCONNU"


def test_part_statuses_match_the_model_and_only_to_order_stands_out():
    """Statuts de pièce : mêmes clés et libellés que le modèle ; tout en gris sauf
    « À commander » (ambre), et jamais le bleu des actions."""
    assert {k: s.label for k, s in PART_STATUS_STYLES.items()} == dict(ExpenseLine.Status.choices)
    voyants = {k for k, s in PART_STATUS_STYLES.items() if s.badge != NEUTRAL_STYLE.badge}
    assert voyants == {"TO_ORDER"}
    assert all("accent" not in s.badge for s in PART_STATUS_STYLES.values())
    assert ui_tags.part_status_label("FITTED") == "Monté"
    assert ui_tags.part_status_label("") == ""

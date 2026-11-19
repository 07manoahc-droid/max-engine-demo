"""Tests de la page « Dépenses du garage » (D52, D57, D58)."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.utils import timezone

from core.formatting import format_euros
from core.models import ExpenseCategory
from expenses.models import Expense, ExpenseLine, ExpensePayment, Refund

URL = "/comptes/depenses-du-garage/"


@pytest.fixture
def make_expense(db):
    """Crée une dépense payée par un associé : `lines` = ((désignation, prix, véhicule), ...)."""
    outillage = ExpenseCategory.objects.get(code="tooling")

    def fabriquer(partner, total, lines, date=dt.date(2026, 3, 3), is_personal=False):
        depense = Expense.objects.create(total_paid=D(total), date=date, is_personal=is_personal)
        ExpensePayment.objects.create(expense=depense, partner=partner, amount=D(total))
        for libelle, prix, vehicule in lines:
            ExpenseLine.objects.create(
                expense=depense,
                vehicle=vehicule,
                category=outillage,
                label=libelle,
                unit_price=D(prix),
            )
        return depense

    return fabriquer


def page(client, annee=2026) -> str:
    return client.get(f"{URL}?annee={annee}").content.decode()


def test_lists_garage_expenses_with_total_and_shares(as_mathis, make_expense, mathis):
    make_expense(mathis, "200", [("Caisse à outils", "200", None)])
    contenu = page(as_mathis)

    assert "Caisse à outils" in contenu
    assert "Dépenses du garage en 2026" in contenu
    assert contenu.count(format_euros(D("200"))) == 2  # tuile et ligne
    assert contenu.count(format_euros(D("100"))) == 2  # part de chacun
    assert "Pour Mathis" in contenu
    assert "Pour Axel" in contenu


def test_mixed_order_shows_only_its_garage_part(as_mathis, make_expense, mathis, make_vehicle):
    """Commande de 100 € : 60 € pour une moto, 30 € pour le garage, 10 € de port."""
    moto = make_vehicle()
    make_expense(mathis, "100", [("Plaquettes", "60", moto), ("Graisse", "30", None)])
    contenu = page(as_mathis)

    assert "Graisse" in contenu
    assert "Plaquettes" not in contenu
    assert format_euros(D("33.33")) in contenu
    assert "Commande partagée avec un véhicule" in contenu


def test_refund_lowers_the_amount(as_mathis, make_expense, mathis):
    depense = make_expense(mathis, "80", [("Perceuse", "80", None)])
    Refund.objects.create(line=depense.lines.get(), amount=D("30"), refunded_to=mathis)

    assert format_euros(D("50")) in page(as_mathis)


def test_vehicle_expense_is_not_listed(as_mathis, make_expense, mathis, make_vehicle):
    make_expense(mathis, "50", [("Pneu", "50", make_vehicle())])
    contenu = page(as_mathis)

    assert "Pneu" not in contenu
    assert "Aucune dépense du garage en 2026. Ajoute la première." in contenu


def test_personal_purchase_is_apart_and_not_counted(as_mathis, make_expense, axel):
    make_expense(axel, "49.90", [("Gants", "49.90", None)], is_personal=True)
    contenu = page(as_mathis)

    assert "Achats personnels (hors comptes)" in contenu
    assert "Gants" in contenu
    assert "Dépenses du garage en 2026" in contenu
    assert format_euros(D("0")) in contenu


def test_years_are_offered_and_filter_the_list(as_mathis, make_expense, mathis):
    make_expense(mathis, "20", [("Clé", "20", None)], date=dt.date(2025, 6, 1))
    contenu_2026 = page(as_mathis, 2026)
    contenu_2025 = page(as_mathis, 2025)

    assert 'href="?annee=2025"' in contenu_2026
    assert "Clé" not in contenu_2026
    assert "Clé" in contenu_2025


def test_add_button_and_tabs(as_mathis):
    contenu = as_mathis.get(URL).content.decode()

    assert 'href="/depenses/ajouter/?depuis=garage"' in contenu
    assert "Ajouter une dépense du garage" in contenu
    assert contenu.count('class="btn-primary"') == 1
    assert 'href="/comptes/"' in contenu
    # L'onglet de la page affichée est marqué, dans le menu des comptes.
    assert "Pages des comptes" in contenu


def test_read_only_hides_the_add_button(as_mathis, settings):
    settings.READ_ONLY = True
    contenu = as_mathis.get(URL).content.decode()
    assert "Ajouter une dépense du garage" not in contenu
    assert "Ajoute la première" not in contenu


def test_accounts_page_links_to_garage_expenses(as_mathis):
    assert f'href="{URL}"' in as_mathis.get("/comptes/").content.decode()


def test_unreadable_year_falls_back_to_this_year(as_mathis):
    reponse = as_mathis.get(f"{URL}?annee=abc")
    assert reponse.status_code == 200
    assert reponse.context["year"] == timezone.localdate().year


@pytest.mark.parametrize("annee", ["²", "99999999999999999999", "1850"])
def test_odd_year_falls_back_to_this_year(as_mathis, annee):
    """« ² » passe `isdigit()` mais pas `int()` : la page ne doit pas planter."""
    reponse = as_mathis.get(f"{URL}?annee={annee}")
    assert reponse.status_code == 200
    assert reponse.context["year"] == timezone.localdate().year


def onglet_courant(contenu: str) -> str:
    """L'adresse de l'onglet marqué `aria-current` dans le menu des comptes."""
    onglets = contenu.split('aria-label="Pages des comptes"')[1].split("</nav>")[0]
    lien = onglets.split('aria-current="page"')[0].rsplit("<a ", 1)[1]
    return lien.split('href="')[1].split('"')[0]


def test_each_tab_is_marked_on_its_own_page(as_mathis):
    assert onglet_courant(as_mathis.get(URL).content.decode()) == URL
    assert onglet_courant(as_mathis.get("/comptes/").content.decode()) == "/comptes/"


def test_page_without_two_active_partners_asks_for_mathis(as_mathis, make_expense, mathis, axel):
    make_expense(mathis, "200", [("Caisse à outils", "200", None)])
    axel.is_active = False
    axel.save()
    reponse = as_mathis.get(URL)

    assert reponse.status_code == 200
    assert "Préviens Mathis" in reponse.content.decode()

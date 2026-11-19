"""Tests de l'écran du bilan (D59 à D63) et de la tuile « Bénéfice » de l'accueil."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.utils import timezone

from accounting.models import Settlement
from accounting.services import SignedAmount
from core.formatting import format_euros
from core.models import ExpenseCategory
from core.templatetags.ui_tags import resultat
from expenses.models import Expense, ExpenseLine, ExpensePayment
from sales.models import Deposit, Sale, SaleReceipt
from sales.tests.helpers import create_deposit
from vehicles.models import StatusChange, Vehicle

URL = "/bilan/"
CETTE_ANNEE = timezone.localdate().year


def euros(montant: str) -> str:
    return format_euros(D(montant))


def page(client, annee=CETTE_ANNEE) -> str:
    return client.get(f"{URL}?annee={annee}").content.decode()


def achat(vehicule, partner, montant, date=None):
    """L'achat d'un véhicule, payé par un associé."""
    depense = Expense.objects.create(total_paid=D(montant), date=date or timezone.localdate())
    ExpensePayment.objects.create(expense=depense, partner=partner, amount=D(montant))
    ExpenseLine.objects.create(
        expense=depense,
        vehicle=vehicule,
        category=ExpenseCategory.objects.get(code=ExpenseCategory.VEHICLE_PURCHASE),
        label="Achat",
        unit_price=D(montant),
    )
    return depense


def vendre(vehicule, partner, prix, date):
    vente = Sale.objects.create(vehicle=vehicule, price=D(prix), date=date)
    SaleReceipt.objects.create(sale=vente, partner=partner, amount=D(prix))
    Vehicle.objects.filter(pk=vehicule.pk).update(status=Vehicle.Status.SOLD)
    return vente


def abandonner(vehicule, date):
    Vehicle.objects.filter(pk=vehicule.pk).update(status=Vehicle.Status.ABANDONED)
    StatusChange.objects.create(
        vehicle=vehicule,
        from_status=Vehicle.Status.IN_REPAIR,
        to_status=Vehicle.Status.ABANDONED,
        changed_at=timezone.make_aware(dt.datetime.combine(date, dt.time(12))),
    )


@pytest.fixture
def annee_complete(make_vehicle, mathis, axel):
    """Cette année : une vente (marge 600 €, 50 jours), un abandon (perte 1 000 €) ;
    l'an dernier : une vente (marge 100 €). Un véhicule encore au garage (700 €)."""
    debut = dt.date(CETTE_ANNEE, 1, 10)
    vendue = make_vehicle(brand="Honda", model="CR", purchased_on=debut)
    achat(vendue, mathis, "2000", debut)
    vendre(vendue, mathis, "2600", debut + dt.timedelta(days=50))

    perdue = make_vehicle(brand="GG", model="Racing")
    achat(perdue, axel, "1000", debut)
    abandonner(perdue, dt.date(CETTE_ANNEE, 2, 1))

    ancienne = make_vehicle(brand="Sherco", model="80")
    achat(ancienne, axel, "400", dt.date(CETTE_ANNEE - 1, 3, 1))
    vendre(ancienne, axel, "500", dt.date(CETTE_ANNEE - 1, 4, 1))

    au_garage = make_vehicle(brand="Yamaha", model="YZ")
    achat(au_garage, mathis, "700", debut)
    return {"vendue": vendue, "perdue": perdue, "ancienne": ancienne, "au_garage": au_garage}


def test_year_tiles_and_vehicle_rows(as_mathis, annee_complete):
    contenu = page(as_mathis)

    assert f"Investi en {CETTE_ANNEE}" in contenu
    assert euros("3700") in contenu  # 2 000 + 1 000 + 700
    assert f"Bénéfice {CETTE_ANNEE}" in contenu
    # 600 − 1 000 = −400 : écrit sans signe moins.
    assert f"Perte de {euros('400')}" in contenu
    assert euros("-400") not in contenu
    assert "50 jours" in contenu
    assert annee_complete["vendue"].get_absolute_url() in contenu
    assert f"Perte de {euros('1000')}" in contenu
    assert f"Abandonné le 01/02/{CETTE_ANNEE}" in contenu
    # La vente de l'an dernier n'est pas dans cette année.
    assert annee_complete["ancienne"].display_name not in contenu.split("Encore au garage")[0]
    # Encore au garage aujourd'hui : le véhicule à 700 €.
    assert annee_complete["au_garage"].display_name in contenu
    assert f"Pour Mathis : Perte de {euros('200')}" in contenu


def test_another_year_and_the_year_selector(as_mathis, annee_complete):
    contenu = page(as_mathis, CETTE_ANNEE - 1)

    assert f'href="?annee={CETTE_ANNEE}"' in contenu
    assert euros("100") in contenu  # marge de la vente de l'an dernier
    assert annee_complete["vendue"].get_absolute_url() not in contenu.split("Encore au garage")[0]
    # Ce qui est encore au garage ne dépend pas de l'année choisie (D62).
    assert annee_complete["au_garage"].display_name in contenu


def test_partner_table_before_and_after_a_settlement(as_mathis, axel_owes, mathis, axel):
    """D61 : jamais de nombre négatif seul. Avant : Axel doit verser 205,44 €. Après :
    Axel a versé plus que ses encaissements, « Déjà revenu » le dit par une phrase."""
    avant = page(as_mathis)
    assert f"doit verser {euros('205.44')}" in avant
    assert f"doit recevoir {euros('205.44')}" in avant
    assert f"Perte de {euros('205.44')}" in avant  # part du bénéfice de chacun
    assert "Ce qui lui revient" in avant

    Settlement.objects.create(from_partner=axel, to_partner=mathis, amount=D("205.44"))
    apres = page(as_mathis)
    assert f"versé {euros('205.44')}" in apres
    assert f"reçu {euros('205.44')}" in apres
    assert "Rien pour l'instant" in apres
    assert f"Axel a versé {euros('205.44')} de plus que ses encaissements" in apres
    assert "Déjà récupéré par Mathis" in apres
    assert "À l'équilibre" in apres
    assert euros("-205.44") not in apres


def test_a_year_of_sales_at_a_loss_has_no_minus_sign(as_mathis, make_vehicle, mathis):
    """Une seule vente, à perte : la marge des ventes s'écrit « Perte de 150,00 € »."""
    vehicule = make_vehicle(purchased_on=dt.date(CETTE_ANNEE, 1, 2))
    achat(vehicule, mathis, "1000", dt.date(CETTE_ANNEE, 1, 2))
    vendre(vehicule, mathis, "850", dt.date(CETTE_ANNEE, 1, 20))
    contenu = page(as_mathis)

    assert euros("-150") not in contenu
    assert contenu.count(f"Perte de {euros('150')}") >= 3  # ligne, marges, bénéfice
    assert "Aucune perte" in contenu


def test_kept_deposit_is_mentioned_under_the_margin(as_mathis, make_vehicle, mathis, axel):
    """Marge = vente + acompte conservé − coût : la ligne dit d'où viennent les 400 €."""
    vehicule = make_vehicle()
    achat(vehicule, mathis, "2668.01")
    for _ in range(2):
        create_deposit(
            vehicle=vehicule, amount=D("200"), received_by=axel, status=Deposit.Status.KEPT
        )
    vendre(vehicule, mathis, "3000", timezone.localdate())
    contenu = page(as_mathis)

    assert euros("731.99") in contenu
    assert f"dont {euros('400')} d'acompte conservé" in contenu


def test_invested_tile_is_explained_under_the_table(as_mathis, make_vehicle, axel):
    """Achat l'an dernier, abandon cette année : « Investi » de l'an dernier vient d'un
    véhicule qui n'est pas dans le tableau de cette année-là."""
    vehicule = make_vehicle()
    achat(vehicule, axel, "3500.50", dt.date(CETTE_ANNEE - 1, 9, 9))
    abandonner(vehicule, dt.date(CETTE_ANNEE, 1, 5))

    an_dernier = page(as_mathis, CETTE_ANNEE - 1)
    assert f"Investi en {CETTE_ANNEE - 1} (argent dépensé dans l'année)" in an_dernier
    assert "pas encore vendus, ou vendus ou abandonnés une autre année" in an_dernier
    assert an_dernier.count(euros("3500.50")) >= 2  # tuile et ligne du détail

    cette_annee = page(as_mathis)
    assert "À retirer : dépensé une autre année pour les véhicules de ce tableau" in cette_annee


def test_trashed_vehicle_is_named_without_a_link(as_mathis, make_vehicle, mathis):
    vehicule = make_vehicle(brand="Kawasaki", model="KLX")
    achat(vehicule, mathis, "300")
    vehicule.soft_delete()
    contenu = page(as_mathis)

    assert "Kawasaki KLX" in contenu
    assert "À la corbeille" in contenu
    assert vehicule.get_absolute_url() not in contenu


def test_empty_garage_invites_nothing_and_does_not_break(as_mathis):
    contenu = page(as_mathis)
    assert f"Aucune vente en {CETTE_ANNEE}." in contenu
    assert "Aucun véhicule au garage." in contenu
    assert "Aucune vente" in contenu


def test_without_two_active_partners_asks_for_mathis(as_mathis, axel_owes, axel):
    axel.is_active = False
    axel.save()
    reponse = as_mathis.get(URL)

    assert reponse.status_code == 200
    assert "Préviens Mathis" in reponse.content.decode()


@pytest.mark.parametrize("annee", ["²", "abc", "1850"])
def test_odd_year_falls_back_to_this_year(as_mathis, annee):
    reponse = as_mathis.get(f"{URL}?annee={annee}")
    assert reponse.status_code == 200
    assert reponse.context["year"] == CETTE_ANNEE


def test_sidebar_marks_bilan_and_not_comptes(as_mathis):
    contenu = as_mathis.get(URL).content.decode()
    menu = contenu.split('href="/bilan/"')[1].split(">")[0]
    assert 'aria-current="page"' in menu
    comptes = contenu.split('href="/comptes/"')[1].split(">")[0]
    assert 'aria-current="page"' not in comptes


def test_home_tile_shows_the_profit_of_the_year(as_mathis, annee_complete):
    """D63 : la tuile de l'accueil donne le bénéfice de l'année et mène au bilan."""
    contenu = as_mathis.get("/").content.decode()
    assert f"Bénéfice {CETTE_ANNEE}" in contenu
    assert f"Perte de {euros('400')}" in contenu
    assert "Voir le bilan" in contenu


@pytest.mark.parametrize(
    ("montant", "texte"),
    [("400", euros("400")), ("0", euros("0")), ("-300.5", f"Perte de {euros('300.50')}")],
)
def test_resultat_filter_never_shows_a_minus_sign(montant, texte):
    assert resultat(SignedAmount(D(montant))) == texte

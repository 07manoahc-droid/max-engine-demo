"""Tests des écrans des véhicules : ajout, fiche, modification, changement de statut."""

import datetime as dt
import html
import re
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.utils import timezone

from accounting.services import Payment
from core.formatting import NBSP
from core.models import Contact, PurchaseOrigin, VehicleType
from expenses.models import ExpensePayment
from vehicles import services
from vehicles.models import StatusChange, Vehicle

Status = Vehicle.Status


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def moto(db):
    return VehicleType.objects.get(name="Moto")


@pytest.fixture
def purchased(make_vehicle, mathis):
    """Véhicule acheté 1 500 € par Mathis."""
    vehicule = make_vehicle(brand="GG", model="MC250", year=2010)
    services.record_purchase(
        vehicule,
        price=D("1500"),
        date=dt.date(2026, 10, 1),
        payments=[Payment(mathis, D("1500"))],
        seller=Contact(name="Jean Dupont", phone="06 12 34 56 78", city="Lyon"),
        partner=mathis,
    )
    vehicule.refresh_from_db()
    return vehicule


# --- Ajouter un véhicule ------------------------------------------------------


def test_create_form_is_short_and_proposes_moto(as_mathis, moto):
    response = as_mathis.get("/vehicules/ajouter/")
    assert response.status_code == 200
    form = response.context["form"]
    assert list(form.fields) == [
        "vehicle_type",
        "brand",
        "model",
        "year",
        "origin",
        "listing_url",
        "asking_price",
        "location",
    ]
    assert form.initial["vehicle_type"] == moto.pk
    assert "Ajouter le véhicule" in response.content.decode()


def test_create_vehicle(as_mathis, moto, mathis):
    leboncoin = PurchaseOrigin.objects.get(name="Leboncoin")
    response = as_mathis.post(
        "/vehicules/ajouter/",
        {
            "vehicle_type": moto.pk,
            "brand": "Yamaha",
            "model": "MT-07",
            "year": "2018",
            "origin": leboncoin.pk,
            "asking_price": "3 200,50 €",
        },
    )
    vehicule = Vehicle.objects.get()
    assert response.status_code == 302
    assert response.url == "/vehicules/1/"
    assert vehicule.status == Status.WAITING_PURCHASE
    assert vehicule.asking_price == D("3200.50")
    assert vehicule.origin == leboncoin
    assert StatusChange.objects.get().changed_by == mathis
    assert messages_of(response) == ["n°1 · Yamaha MT-07 2018 est ajouté au garage."]


def test_create_vehicle_shows_errors(as_mathis, moto):
    annee_max = timezone.localdate().year + 1
    response = as_mathis.post(
        "/vehicules/ajouter/",
        {"vehicle_type": moto.pk, "brand": "", "model": "MT-07", "year": "3000"},
    )
    assert response.status_code == 200
    contenu = response.content.decode()
    assert "Ce champ est obligatoire." in contenu
    assert f"Indique une année entre 1900 et {annee_max}." in contenu
    assert not Vehicle.objects.exists()


def test_create_form_hides_inactive_types(as_mathis, moto):
    VehicleType.objects.filter(name="Quad").update(is_active=False)
    form = as_mathis.get("/vehicules/ajouter/").context["form"]
    assert "Quad" not in [t.name for t in form.fields["vehicle_type"].queryset]


# --- Fiche ---------------------------------------------------------------------


def test_detail_of_a_waiting_vehicle(as_mathis, make_vehicle):
    vehicule = services.create_vehicle(
        Vehicle(vehicle_type=make_vehicle().vehicle_type, brand="Sherco", model="80"), None
    )
    response = as_mathis.get(f"/vehicules/{vehicule.number}/")
    assert response.status_code == 200
    contenu = response.content.decode()
    assert "n°2 · Sherco 80" in contenu
    assert "En attente d&#x27;achat" in contenu
    assert "Enregistrer l'achat" in contenu
    assert "Ajouté au garage" in contenu
    assert response.context["allowed_statuses"] == [Status.ABANDONED]


@pytest.mark.parametrize(
    ("statut", "principal"),
    [(Status.WAITING_PURCHASE, "Enregistrer l'achat"), (Status.IN_REPAIR, "Ajouter une dépense")],
)
def test_detail_main_action_comes_first(as_mathis, make_vehicle, statut, principal):
    contenu = as_mathis.get(f"/vehicules/{make_vehicle(status=statut).number}/").content.decode()
    assert contenu.index(principal) < contenu.index("Modifier la fiche")
    assert contenu.index("Modifier la fiche") < contenu.index("Changer le statut")


def test_detail_of_a_purchased_vehicle_shows_investment_and_seller(
    as_mathis, purchased, mathis, axel
):
    response = as_mathis.get(f"/vehicules/{purchased.number}/")
    contenu = response.content.decode()
    assert response.context["investments"] == [(mathis, D("1500.00")), (axel, D("0.00"))]
    assert response.context["invested_total"] == D("1500.00")
    assert "Investi par Mathis" in contenu and "Investi par Axel" in contenu
    assert f"1{NBSP}500,00{NBSP}€" in contenu
    assert "Jean Dupont" in contenu and "Lyon" in contenu
    assert "01/10/2026" in contenu
    assert "Ajouter une dépense" in contenu
    assert Status.IN_REPAIR in response.context["allowed_statuses"]


def test_detail_formats_numbers(as_mathis, make_vehicle):
    vehicule = make_vehicle(mileage_km=12500, displacement_cc=250)
    contenu = as_mathis.get(f"/vehicules/{vehicule.number}/").content.decode()
    assert f"12{NBSP}500 km" in contenu
    assert "250 cm³" in contenu


def test_vehicle_pages_show_no_template_comment(as_mathis, make_vehicle):
    vehicule = make_vehicle()
    for adresse in (
        "/vehicules/ajouter/",
        f"/vehicules/{vehicule.number}/",
        f"/vehicules/{vehicule.number}/modifier/",
    ):
        html = as_mathis.get(adresse).content.decode()
        assert "{#" not in html and "#}" not in html


def test_detail_of_unknown_or_deleted_vehicle_is_not_found(as_mathis, make_vehicle):
    make_vehicle().soft_delete()
    assert as_mathis.get("/vehicules/1/").status_code == 404
    assert as_mathis.get("/vehicules/99/").status_code == 404


def test_detail_hides_actions_in_read_only_mode(as_mathis, make_vehicle, settings):
    settings.READ_ONLY = True
    contenu = as_mathis.get(f"/vehicules/{make_vehicle().number}/").content.decode()
    assert "Modifier la fiche" not in contenu
    assert "Changer le statut" not in contenu


def test_sidebar_links_to_vehicles_in_garage(as_mathis, make_vehicle):
    au_garage = make_vehicle(brand="Yamaha", model="MT-07")
    make_vehicle(brand="Honda", model="CBR", status=Status.SOLD)
    contenu = as_mathis.get(f"/vehicules/{au_garage.number}/").content.decode()
    assert f'href="/vehicules/{au_garage.number}/"' in contenu
    assert 'aria-current="page"' in contenu
    assert "Honda CBR" not in contenu


# --- Modifier la fiche -------------------------------------------------------------


def test_edit_form_shows_all_groups(as_mathis, purchased):
    response = as_mathis.get(f"/vehicules/{purchased.number}/modifier/")
    assert response.status_code == 200
    titres = [titre for titre, _ in response.context["form"].grouped_fields()]
    assert titres == ["Le véhicule", "Achat", "Vente", "Notes"]
    assert "status" not in response.context["form"].fields
    assert "purchased_on" not in response.context["form"].fields


def test_edit_vehicle(as_mathis, purchased, moto):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/modifier/",
        {
            "vehicle_type": moto.pk,
            "brand": "GG",
            "model": "MC250",
            "year": "2010",
            "vin": "abc123",
            "mileage_km": "8000",
            "sale_price_target": "2400",
            "seller": purchased.seller_id,
            "notes": "Embrayage à revoir.",
        },
    )
    assert response.status_code == 302
    purchased.refresh_from_db()
    assert (purchased.vin, purchased.mileage_km) == ("ABC123", 8000)
    assert purchased.sale_price_target == D("2400.00")
    assert purchased.status == Status.PURCHASED
    assert purchased.purchased_on == dt.date(2026, 10, 1)
    assert messages_of(response) == ["La fiche est enregistrée."]


def test_edit_vehicle_shows_errors(as_mathis, purchased, moto):
    response = as_mathis.post(
        f"/vehicules/{purchased.number}/modifier/",
        {"vehicle_type": moto.pk, "brand": "GG", "model": "", "asking_price": "beaucoup"},
    )
    assert response.status_code == 200
    assert response.context["form"].errors.keys() == {"model", "asking_price"}
    purchased.refresh_from_db()
    assert purchased.model == "MC250"


def test_edit_keeps_an_inactive_type_already_chosen(as_mathis, make_vehicle):
    quad = VehicleType.objects.get(name="Quad")
    vehicule = make_vehicle(vehicle_type=quad)
    quad.is_active = False
    quad.save()
    form = as_mathis.get(f"/vehicules/{vehicule.number}/modifier/").context["form"]
    assert quad in form.fields["vehicle_type"].queryset


# --- Changer le statut ------------------------------------------------------------


def test_change_status(as_mathis, purchased, mathis):
    response = as_mathis.post(f"/vehicules/{purchased.number}/statut/", {"status": "IN_REPAIR"})
    assert response.status_code == 302
    assert response.url == f"/vehicules/{purchased.number}/"
    purchased.refresh_from_db()
    assert purchased.status == Status.IN_REPAIR
    assert purchased.status_changes.last().changed_by == mathis
    assert messages_of(response) == ["Nouveau statut : En réparation."]


@pytest.mark.parametrize("statut", ["WAITING_PURCHASE", "SOLD", "", "N'IMPORTE QUOI"])
def test_change_status_refused(as_mathis, purchased, statut):
    response = as_mathis.post(f"/vehicules/{purchased.number}/statut/", {"status": statut})
    assert response.status_code == 302
    purchased.refresh_from_db()
    assert purchased.status == Status.PURCHASED
    assert len(messages_of(response)) == 1


def test_change_status_needs_post(as_mathis, purchased):
    assert as_mathis.get(f"/vehicules/{purchased.number}/statut/").status_code == 405


# --- Enregistrer l'achat -----------------------------------------------------------


@pytest.fixture
def waiting(make_vehicle):
    return make_vehicle(brand="Yamaha", model="MT-07", year=2018, asking_price=D("4500"))


def buy(client, vehicule, **champs):
    donnees = {"price": "4 200,00", "date": "2026-10-20", "payer": "", "seller": "", **champs}
    return client.post(f"/vehicules/{vehicule.number}/acheter/", donnees)


def payments_of(vehicule):
    return {
        (p.partner.name, p.amount)
        for p in ExpensePayment.objects.filter(expense__lines__vehicle=vehicule)
    }


def test_purchase_form_preselects_the_current_partner(as_mathis, waiting, mathis):
    Contact.objects.create(name="Jean Dupont", city="Lyon")
    response = as_mathis.get(f"/vehicules/{waiting.number}/acheter/")
    assert response.status_code == 200
    form = response.context["form"]
    assert form["payer"].value() == str(mathis.pk)
    assert [libelle for _, libelle, _ in form.payer_options()] == [
        "Mathis",
        "Axel",
        "Moitié-moitié",
        "Montants différents",
    ]
    assert "Jean Dupont · Lyon" in [libelle for _, libelle in form.fields["seller"].choices]
    contenu = response.content.decode()
    assert f"Prix demandé dans l&#x27;annonce : 4{NBSP}500,00{NBSP}€." in contenu
    assert "Nouveau vendeur…" in contenu


def test_purchase_form_asks_who_paid(as_mathis, waiting):
    contenu = as_mathis.get(f"/vehicules/{waiting.number}/acheter/").content.decode()
    assert "Qui a payé ?" in html.unescape(contenu)


def test_purchase_form_preselects_the_known_seller(as_mathis, make_vehicle):
    vendeur = Contact.objects.create(name="Jean Dupont")
    vehicule = make_vehicle(seller=vendeur)
    response = as_mathis.get(f"/vehicules/{vehicule.number}/acheter/")
    assert response.context["form"]["seller"].value() == str(vendeur.pk)


def test_seller_in_the_trash_stays_on_the_vehicle_form(as_mathis, make_vehicle):
    """Sinon, enregistrer la fiche viderait le vendeur sans rien dire."""
    vendeur = Contact.objects.create(name="Jean Dupont")
    vehicule = make_vehicle(seller=vendeur)
    vendeur.soft_delete()

    response = as_mathis.get(f"/vehicules/{vehicule.number}/modifier/")

    assert vendeur in response.context["form"].fields["seller"].queryset
    assert response.context["form"]["seller"].value() == vendeur.pk


def test_trashed_contact_is_not_proposed_on_the_vehicle_form(as_mathis, make_vehicle):
    Contact.objects.create(name="Jean Dupont").soft_delete()
    response = as_mathis.get(f"/vehicules/{make_vehicle().number}/modifier/")
    assert not response.context["form"].fields["seller"].queryset.exists()


def test_purchase_form_styles_every_field(as_mathis, waiting):
    """Les champs « Payé par … », ajoutés après coup, ont aussi le style de la charte
    (sans lui, ils étaient invisibles) et ne sont pas marqués « facultatif »."""
    response = as_mathis.get(f"/vehicules/{waiting.number}/acheter/")
    form = response.context["form"]
    for champ in form.amount_fields():
        assert "field-input" in champ.field.widget.attrs["class"].split()
    assert re.search(r"Payé par Mathis\s*</label>", response.content.decode())


def test_purchase_paid_by_one_partner(as_mathis, waiting, axel):
    response = buy(as_mathis, waiting, payer=axel.pk)
    assert response.status_code == 302
    waiting.refresh_from_db()
    assert waiting.status == Status.PURCHASED
    assert waiting.purchased_on == dt.date(2026, 10, 20)
    assert payments_of(waiting) == {("Axel", D("4200.00"))}
    assert messages_of(response) == ["Achat enregistré : n°1 · Yamaha MT-07 2018 est acheté."]


def test_purchase_half_and_half_gives_the_odd_cent_to_the_first(as_mathis, waiting):
    buy(as_mathis, waiting, price="1500,01", payer="moitie")
    assert payments_of(waiting) == {("Mathis", D("750.01")), ("Axel", D("750.00"))}


def test_purchase_with_different_amounts(as_mathis, waiting, mathis, axel):
    buy(
        as_mathis,
        waiting,
        payer="montants",
        **{f"amount_{mathis.pk}": "3000", f"amount_{axel.pk}": "1 200"},
    )
    assert payments_of(waiting) == {("Mathis", D("3000.00")), ("Axel", D("1200.00"))}


@pytest.mark.parametrize(
    ("montants", "champ", "message"),
    [
        (("3000", "1000"), "payer", "Les parts payées font"),
        (("4200", ""), "amount_axel", "Indique combien Axel a payé."),
        (("4200", "0"), "amount_axel", "Si un seul associé a payé, choisis son prénom."),
    ],
)
def test_purchase_with_wrong_amounts(as_mathis, waiting, mathis, axel, montants, champ, message):
    response = buy(
        as_mathis,
        waiting,
        payer="montants",
        **{f"amount_{mathis.pk}": montants[0], f"amount_{axel.pk}": montants[1]},
    )
    assert response.status_code == 200
    champ = champ.replace("axel", str(axel.pk))
    assert any(message in erreur for erreur in response.context["form"].errors[champ])
    _assert_still_waiting(waiting)


@pytest.mark.parametrize(
    ("champs", "champ_en_erreur"),
    [({"price": "0"}, "price"), ({"price": ""}, "price"), ({"payer": ""}, "payer")],
)
def test_purchase_shows_errors(as_mathis, waiting, mathis, champs, champ_en_erreur):
    response = buy(as_mathis, waiting, **{"payer": mathis.pk, **champs})
    assert response.status_code == 200
    assert champ_en_erreur in response.context["form"].errors
    _assert_still_waiting(waiting)


def test_purchase_creates_a_new_seller(as_mathis, waiting, mathis):
    response = buy(
        as_mathis,
        waiting,
        payer=mathis.pk,
        seller="nouveau",
        **{
            "vendeur-name": "Paul Martin",
            "vendeur-phone": "06 00 00 00 00",
            "vendeur-city": "Lyon",
        },
    )
    assert response.status_code == 302
    waiting.refresh_from_db()
    assert waiting.seller == Contact.objects.get(name="Paul Martin", city="Lyon")


def test_purchase_new_seller_needs_a_name(as_mathis, waiting, mathis):
    response = buy(
        as_mathis, waiting, payer=mathis.pk, seller="nouveau", **{"vendeur-city": "Lyon"}
    )
    assert response.status_code == 200
    assert "name" in response.context["contact_form"].errors
    assert not Contact.objects.exists()
    _assert_still_waiting(waiting)


def test_purchase_ignores_new_seller_fields_when_not_chosen(as_mathis, waiting, mathis):
    """Des champs de nouveau vendeur remplis puis abandonnés ne créent rien."""
    response = buy(as_mathis, waiting, payer=mathis.pk, **{"vendeur-name": "Paul Martin"})
    assert response.status_code == 302
    assert not Contact.objects.exists()


def test_purchase_with_an_existing_seller(as_mathis, waiting, mathis):
    connu = Contact.objects.create(name="Jean Dupont")
    buy(as_mathis, waiting, payer=mathis.pk, seller=connu.pk)
    waiting.refresh_from_db()
    assert waiting.seller == connu


def test_purchase_of_a_vehicle_already_bought_redirects(as_mathis, purchased):
    for reponse in (
        as_mathis.get(f"/vehicules/{purchased.number}/acheter/"),
        buy(as_mathis, purchased, payer="moitie"),
    ):
        assert reponse.status_code == 302
        assert reponse.url == f"/vehicules/{purchased.number}/"
    assert ExpensePayment.objects.count() == 1


def test_detail_shows_the_purchase(as_mathis, waiting, mathis, axel):
    buy(as_mathis, waiting, price="1500,01", payer="moitie")
    response = as_mathis.get(f"/vehicules/{waiting.number}/")
    assert response.context["purchase"] == {
        "price": D("1500.01"),
        "payers": [(mathis, D("750.01")), (axel, D("750.00"))],
    }
    contenu = response.content.decode()
    assert f"1{NBSP}500,01{NBSP}€" in contenu
    assert f"Mathis · 750,01{NBSP}€" in contenu
    assert f"Axel · 750,00{NBSP}€" in contenu
    assert "20/10/2026" in contenu


def test_detail_without_purchase_has_no_purchase_summary(as_mathis, waiting):
    response = as_mathis.get(f"/vehicules/{waiting.number}/")
    assert response.context["purchase"] is None
    assert "Prix payé" not in response.content.decode()


def _assert_still_waiting(vehicule):
    vehicule.refresh_from_db()
    assert vehicule.status == Status.WAITING_PURCHASE
    assert not ExpensePayment.objects.exists()


# --- Accueil (indicateurs lus en base) et liste au garage ---------------------------


def test_home_indicators_count_vehicles_in_garage(as_mathis, purchased, make_vehicle, mathis):
    """Un véhicule vendu ne compte ni dans le nombre ni dans le coût des véhicules au garage."""
    make_vehicle(brand="Honda", model="CBR", status=Status.SOLD)
    response = as_mathis.get("/")
    assert response.context["overview"].garage_cost == D("1500.00")
    assert response.context["garage_count"] == 1
    assert f"1{NBSP}500,00{NBSP}€" in response.content.decode()


def test_list_shows_a_card_per_vehicle_in_garage(
    as_mathis, purchased, make_vehicle, mathis, axel
):
    make_vehicle(brand="Sherco", model="80")
    make_vehicle(brand="Honda", model="CBR", status=Status.SOLD)
    make_vehicle(brand="KTM", model="125", status=Status.ABANDONED)
    response = as_mathis.get("/vehicules/")
    assert response.status_code == 200
    cartes = response.context["cards"]
    assert [c["vehicle"].brand for c in cartes] == ["GG", "Sherco"]
    assert cartes[0]["invested"] == D("1500.00")
    assert cartes[0]["shares"] == [(mathis, D("1500.00")), (axel, D("0.00"))]
    assert cartes[1]["invested"] == D("0.00")
    assert response.context["status_counts"] == [
        (Status.WAITING_PURCHASE, 1),
        (Status.PURCHASED, 1),
    ]
    contenu = response.content.decode()
    assert "Honda CBR" not in contenu and "KTM 125" not in contenu
    assert f'href="/vehicules/{purchased.number}/"' in contenu


def test_list_filters_by_status(as_mathis, purchased, make_vehicle):
    make_vehicle(brand="Sherco", model="80")
    response = as_mathis.get("/vehicules/?statut=PURCHASED")
    assert [c["vehicle"].brand for c in response.context["cards"]] == ["GG"]
    assert response.context["current_status"] == "PURCHASED"
    # Le nombre de chaque statut reste celui de tout le garage.
    assert response.context["total_count"] == 2


@pytest.mark.parametrize("filtre", ["SOLD", "N'IMPORTE QUOI"])
def test_list_ignores_an_unknown_filter(as_mathis, purchased, filtre):
    response = as_mathis.get(f"/vehicules/?statut={filtre}")
    assert response.context["current_status"] == ""
    assert len(response.context["cards"]) == 1


def test_list_filter_with_htmx_returns_only_the_grid(as_mathis, purchased):
    response = as_mathis.get("/vehicules/?statut=PURCHASED", headers={"HX-Request": "true"})
    contenu = response.content.decode()
    assert response.status_code == 200
    assert contenu.lstrip().startswith('<div id="grille"')
    assert "<html" not in contenu
    assert 'hx-push-url="true"' in contenu


def test_empty_list_invites_to_add_a_vehicle(as_mathis):
    contenu = as_mathis.get("/vehicules/").content.decode()
    assert "Aucun véhicule au garage. Ajoute le premier." in contenu


def test_list_marks_the_menu_entry_as_current(as_mathis):
    contenu = as_mathis.get("/vehicules/").content.decode()
    assert re.search(r'href="/vehicules/"[^>]*aria-current="page"', contenu)


# --- Relecture de la tâche 1.3 -------------------------------------------------------


def test_purchase_form_escapes_posted_values_for_alpine(as_mathis, waiting):
    """Une valeur envoyée est remise dans `x-data` : elle doit être échappée pour JavaScript."""
    response = buy(as_mathis, waiting, payer="x'+alert(1)+'")
    contenu = response.content.decode()
    assert response.status_code == 200
    assert r"x\u0027+alert(1)+\u0027" in contenu
    assert "x&#x27;+alert" not in contenu


def test_purchase_shows_seller_errors_with_purchase_errors(as_mathis, waiting):
    """Prix manquant et nom du nouveau vendeur manquant : les deux erreurs d'un coup."""
    response = buy(as_mathis, waiting, price="", payer="moitie", seller="nouveau")
    assert "price" in response.context["form"].errors
    assert "name" in response.context["contact_form"].errors


def test_purchase_button_is_disabled_after_the_first_click(as_mathis, waiting):
    contenu = as_mathis.get(f"/vehicules/{waiting.number}/acheter/").content.decode()
    assert '@submit="envoi = true"' in contenu
    assert ':disabled="envoi"' in contenu


def test_list_history_restore_gets_the_full_page(as_mathis, purchased):
    """Bouton « Retour » : htmx redemande la page avec HX-Request, il lui faut toute la page."""
    response = as_mathis.get(
        "/vehicules/?statut=PURCHASED",
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )
    assert "<html" in response.content.decode()
    assert "HX-Request" in response["Vary"]

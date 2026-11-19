"""Contacts (D81) : recherche, rôles, ajout, modification et corbeille, qui ne détache pas
un contact de ses véhicules."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError

from accounting.services import Payment
from core import trash
from core.contacts import contact_roles, search_contacts, trash_contact
from core.forms import contact_choice_label
from core.models import Contact
from sales import services as sale_services
from sales.tests.helpers import record_deposit_for
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

DATE = dt.date(2026, 10, 20)


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def acheter(vehicule, partner, seller=None):
    vehicle_services.record_purchase(
        vehicule,
        price=D("1000"),
        date=DATE,
        payments=[Payment(partner, D("1000"))],
        seller=seller,
        partner=partner,
    )
    vehicule.refresh_from_db()


def vendre(vehicule, partner, buyer):
    return sale_services.record_sale(
        vehicule,
        price=D("1500"),
        date=dt.date(2026, 10, 30),
        buyer=buyer,
        sold_where="",
        notes="",
        receipts=[Payment(partner, D("1500"))],
        partner=partner,
    )


@pytest.fixture
def jean(db):
    return Contact.objects.create(name="Jean Martin", phone="06 12 34 56 78", city="Lyon")


@pytest.fixture
def lea(db):
    return Contact.objects.create(name="Léa Durand", phone="07.11.22.33.44", city="Annecy")


# ---------------------------------------------------------------------------
# Recherche et rôles
# ---------------------------------------------------------------------------


def test_search_by_name_city_or_phone(jean, lea):
    Contact.objects.create(name="Ancien", deleted_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert list(search_contacts()) == [jean, lea]
    assert list(search_contacts("mart")) == [jean]
    assert list(search_contacts("annecy")) == [lea]
    assert list(search_contacts("0612")) == [jean]  # sans les espaces du numéro
    assert list(search_contacts("07 11 22")) == [lea]  # avec des espaces, numéro à points
    assert list(search_contacts("  jean   martin ")) == [jean]
    assert list(search_contacts("personne")) == []
    assert list(search_contacts("ancien")) == []  # la corbeille n'est pas listée


def test_roles_seller_buyer_and_deposits(make_vehicle, mathis, axel, jean, lea):
    achete = make_vehicle(brand="GG", model="MC250")
    acheter(achete, mathis, seller=jean)
    vente = vendre(achete, axel, buyer=lea)

    annule = make_vehicle(brand="Yamaha", model="RM")
    acheter(annule, mathis)
    sale_services.cancel_sale(vendre(annule, axel, buyer=jean), None, axel)

    en_vente = make_vehicle(brand="Yamaha", model="YZ")
    acheter(en_vente, axel)
    vehicle_services.change_status(en_vente, "FOR_SALE", axel)
    record_deposit_for(
        en_vente, amount=D("100"), date=DATE, received_by=axel, buyer=lea, partner=axel
    )

    roles = contact_roles([jean, lea])
    assert [(r.text, r.note) for r in roles[jean.pk]] == [
        ("Vendeur du n°1", ""),
        ("Acheteur du n°2", "vente annulée"),
    ]
    assert [(r.text, r.note) for r in roles[lea.pk]] == [
        ("Acheteur du n°1", ""),
        ("Acompte sur le n°3", ""),
    ]
    assert roles[jean.pk][0].vehicle_url == achete.get_absolute_url()
    assert vente.buyer == lea


def test_applied_deposit_of_the_same_buyer_is_not_repeated(make_vehicle, axel, lea):
    vehicule = make_vehicle()
    acheter(vehicule, axel)
    vehicle_services.change_status(vehicule, "FOR_SALE", axel)
    record_deposit_for(
        vehicule, amount=D("100"), date=DATE, received_by=axel, buyer=lea, partner=axel
    )
    sale_services.record_sale(
        vehicule,
        price=D("1500"),
        date=dt.date(2026, 10, 30),
        buyer=lea,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("1400"))],
        partner=axel,
    )
    assert [r.text for r in contact_roles([lea])[lea.pk]] == ["Acheteur du n°1"]


def test_role_on_a_trashed_vehicle_has_no_link(make_vehicle, jean):
    vehicule = make_vehicle(seller=jean)
    vehicle_services.trash_vehicle(vehicule)
    (role,) = contact_roles([jean])[jean.pk]
    assert role.text == "Vendeur du n°1" and role.vehicle_url == ""


# ---------------------------------------------------------------------------
# Corbeille
# ---------------------------------------------------------------------------


def test_trash_keeps_the_links_and_restores(make_vehicle, jean):
    vehicule = make_vehicle(seller=jean)
    trash_contact(jean)
    assert Vehicle.objects.get(pk=vehicule.pk).seller == jean  # lien gardé
    assert list(search_contacts()) == []
    with pytest.raises(ValidationError, match="déjà dans la corbeille"):
        trash_contact(jean)
    assert trash.restore("contact", jean.pk) == "Le contact est sorti de la corbeille."
    assert list(search_contacts()) == [jean]


def test_trashed_contact_still_chosen_says_so(jean):
    assert contact_choice_label(jean) == "Jean Martin · Lyon"
    trash_contact(jean)
    jean.refresh_from_db()
    assert contact_choice_label(jean) == "Jean Martin · Lyon (à la corbeille)"


def test_deposit_form_lists_the_trashed_buyer_with_a_mark(as_mathis, make_vehicle, axel, jean):
    """Remarque de la feuille de route : un contact à la corbeille encore cité par une fiche
    reste dans la liste de choix, nommé « (à la corbeille) »."""
    vehicule = make_vehicle()
    acheter(vehicule, axel)
    vehicle_services.change_status(vehicule, "FOR_SALE", axel)
    acompte = record_deposit_for(
        vehicule, amount=D("100"), date=DATE, received_by=axel, buyer=jean, partner=axel
    )
    trash_contact(jean)
    page = as_mathis.get(f"/acomptes/{acompte.pk}/corriger/").content.decode()
    assert "Jean Martin · Lyon (à la corbeille)" in page


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


def test_list_page_and_htmx_search(as_mathis, make_vehicle, jean, lea):
    make_vehicle(seller=jean)
    page = as_mathis.get("/contacts/").content.decode()
    assert "Jean Martin" in page and "Léa Durand" in page
    assert "Vendeur du n°1" in page and "Ajouter un contact" in page
    assert 'href="/contacts/"' in page  # lien de la barre latérale
    fragment = as_mathis.get("/contacts/?q=0612", headers={"HX-Request": "true"})
    texte = fragment.content.decode()
    assert "<html" not in texte and "Jean Martin" in texte and "Léa" not in texte
    vide = as_mathis.get("/contacts/?q=zzz").content.decode()
    assert "Aucun contact ne correspond à « zzz »" in vide


def test_empty_list_invites(as_mathis):
    page = as_mathis.get("/contacts/").content.decode()
    assert (
        "Aucun contact pour l'instant. Ils s'ajoutent aussi depuis un achat ou une vente." in page
    )


def test_create_edit_and_errors(as_mathis):
    reponse = as_mathis.post("/contacts/ajouter/", {"name": ""})
    assert "Indique son nom." in reponse.content.decode()
    reponse = as_mathis.post(
        "/contacts/ajouter/",
        {"name": "Paul", "phone": "06 00", "email": "paul@", "city": "", "notes": ""},
    )
    assert "Indique une adresse e-mail valide" in reponse.content.decode()
    reponse = as_mathis.post(
        "/contacts/ajouter/",
        {
            "name": " Paul ",
            "phone": "06 00",
            "email": "paul@exemple.fr",
            "city": "Grenoble",
            "leboncoin_profile": "leboncoin.fr/profil/1",
            "notes": "Cherche une 125",
        },
    )
    paul = Contact.objects.get(name="Paul")
    assert reponse.url == f"/contacts/{paul.pk}/"
    assert paul.leboncoin_profile == "https://leboncoin.fr/profil/1"
    assert "Le contact Paul est ajouté." in messages_of(reponse)
    fiche = as_mathis.get(reponse.url).content.decode()
    assert "Cherche une 125" in fiche and "Ouvrir son profil" in fiche
    assert "Aucun véhicule lié pour l'instant." in fiche

    reponse = as_mathis.post(f"/contacts/{paul.pk}/modifier/", {"name": "Paul R.", "city": "Lyon"})
    assert reponse.url == f"/contacts/{paul.pk}/"
    paul.refresh_from_db()
    assert (paul.name, paul.city, paul.email) == ("Paul R.", "Lyon", "")


def test_trash_from_the_sheet(as_mathis, make_vehicle, jean):
    vehicule = make_vehicle(seller=jean)
    reponse = as_mathis.post(f"/contacts/{jean.pk}/corbeille/")
    assert reponse.url == "/contacts/"
    assert "toujours lié à ses véhicules" in messages_of(reponse)[0]
    assert as_mathis.get(f"/contacts/{jean.pk}/").status_code == 404
    assert as_mathis.get(f"/contacts/{jean.pk}/modifier/").status_code == 404
    # Second clic (autre onglet) : un message, pas une page introuvable.
    reponse = as_mathis.post(f"/contacts/{jean.pk}/corbeille/")
    assert any("déjà dans la corbeille" in m for m in messages_of(reponse))
    # La fiche du véhicule garde son vendeur, sans lien vers une page disparue.
    fiche = as_mathis.get(vehicule.get_absolute_url()).content.decode()
    assert "Jean Martin" in fiche and "(à la corbeille)" in fiche
    assert f'href="/contacts/{jean.pk}/"' not in fiche
    assert "Jean Martin" in as_mathis.get("/corbeille/").content.decode()


def test_vehicle_sheet_links_to_the_seller(as_mathis, make_vehicle, jean):
    vehicule = make_vehicle(seller=jean)
    fiche = as_mathis.get(vehicule.get_absolute_url()).content.decode()
    assert f'href="/contacts/{jean.pk}/"' in fiche


def test_read_only_shows_contacts_without_actions(as_mathis, settings, make_vehicle, jean):
    make_vehicle(seller=jean)
    settings.READ_ONLY = True
    liste = as_mathis.get("/contacts/").content.decode()
    fiche = as_mathis.get(f"/contacts/{jean.pk}/").content.decode()
    assert "Jean Martin" in liste and "Vendeur du n°1" in fiche
    for page in (liste, fiche):
        assert "Ajouter un contact" not in page
        assert "Modifier le contact" not in page and 'method="post"' not in page
    assert as_mathis.get("/contacts/ajouter/").status_code == 403
    assert as_mathis.post(f"/contacts/{jean.pk}/corbeille/").status_code == 403


# ---------------------------------------------------------------------------
# Relecture de la sous-étape 5
# ---------------------------------------------------------------------------


def test_search_ignores_accents_and_case(db):
    """Le `LIKE` de SQLite ne trouvait pas « Éric » en tapant « eric » (comme D43)."""
    eric = Contact.objects.create(name="Éric Lefèvre", city="BÉZIERS")
    zoe = Contact.objects.create(name="zoé")
    assert search_contacts("eric") == [eric]
    assert search_contacts("LEFEVRE") == [eric]
    assert search_contacts("béziers") == [eric]
    assert search_contacts("bezier") == [eric]
    assert search_contacts() == [eric, zoe]  # ordre alphabétique, accents compris


def test_htmx_search_updates_the_announced_count(as_mathis, jean, lea):
    texte = as_mathis.get("/contacts/?q=lyon", headers={"HX-Request": "true"}).content.decode()
    assert '<p id="compte-contacts" hx-swap-oob="innerHTML">1 contact pour « lyon »</p>' in texte
    page = as_mathis.get("/contacts/").content.decode()
    assert '<p id="compte-contacts" role="status"' in page and '<div id="liste-contacts">' in page


def test_same_name_is_reported_when_adding(as_mathis, jean):
    reponse = as_mathis.post("/contacts/ajouter/", {"name": "jean MARTIN"})
    textes = messages_of(reponse)
    assert "Le contact jean MARTIN est ajouté." in textes
    assert any("Un autre contact s'appelle déjà jean MARTIN (Lyon)" in m for m in textes)
    reponse = as_mathis.post("/contacts/ajouter/", {"name": "Personne d'autre"})
    assert not any("déjà Personne" in m for m in messages_of(reponse))

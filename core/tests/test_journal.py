"""Journal des modifications (D78) et réouvertures d'une fiche : des phrases lisibles,
regroupées par clic, filtrées par véhicule ou par dépense, sans jamais écrire en base."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from accounting.services import Payment
from accounting.settlements import cancel_settlement, record_settlement
from core import journal
from core.journal import NO_AUTHOR, SOURCES, journal_page, vehicle_reopenings
from expenses import services as expense_services
from expenses.services import LineEntry
from sales import services as sale_services
from sales.models import Deposit
from sales.services import TradeIn
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

DATE = dt.date(2026, 10, 20)


def vieillir(heures: int = 1) -> None:
    """Recule tout l'historique : ce qui suit ne vient plus « du même clic »."""
    for source in SOURCES:
        source.model.history.model.objects.update(
            history_date=F("history_date") - dt.timedelta(hours=heures)
        )


def textes(entry) -> list[str]:
    return [ligne.text for ligne in entry.lines]


def depense(lines, payments, total, expense=None):
    return expense_services.save_expense(
        expense,
        date=DATE,
        supplier_name="",
        order_number="",
        total_paid=D(total),
        is_personal=False,
        notes="",
        lines=lines,
        payments=payments,
    )


@pytest.fixture
def moto(make_vehicle):
    return make_vehicle(brand="GG", model="MC250", status="IN_REPAIR")


@pytest.fixture
def pieces(parts_category, moto, axel):
    return depense(
        [LineEntry("Disque", parts_category, D("60.00"), vehicle=moto, status="RECEIVED")],
        [Payment(axel, D("60.00"))],
        "60.00",
    )


def test_one_click_is_one_entry_in_the_order_it_was_typed(pieces, moto):
    """La dépense, sa ligne et son paiement, écrits ensemble, font une seule entrée ; une
    commande (sans associé) est signée « Sans associé (commande) »."""
    page = journal_page()
    entree = page.entries[0]
    assert entree.author is None and entree.author_name == NO_AUTHOR
    assert textes(entree)[0] == "a ajouté la dépense du 20/10/2026 (60,00 €)"
    assert set(textes(entree)[1:]) == {
        "a ajouté la ligne « Disque » (n°1 GG MC250)",
        "a ajouté le paiement d'Axel (60,00 €)",
    }
    assert entree.link_url == pieces.get_absolute_url()
    assert textes(page.entries[1]) == [f"a créé la fiche {moto.display_name}"]


def test_a_change_says_before_and_after_and_hides_what_did_not_change(
    pieces, parts_category, moto, axel
):
    vieillir()
    ligne = pieces.lines.get()
    depense(
        [
            LineEntry(
                "Disque", parts_category, D("75.00"), vehicle=moto, status="RECEIVED", pk=ligne.pk
            )
        ],
        [Payment(axel, D("75.00"))],
        "75.00",
        expense=pieces,
    )
    entree = journal_page().entries[0]
    phrases = {ligne.text: ligne.changes for ligne in entree.lines}
    assert phrases["a modifié la dépense du 20/10/2026 (75,00 €)"] == [
        "Montant payé : 60,00 € → 75,00 €"
    ]
    assert phrases["a modifié la ligne « Disque » (n°1 GG MC250)"] == [
        "Prix unitaire : 60,00 € → 75,00 €"
    ]
    assert phrases["a modifié le paiement d'Axel (75,00 €)"] == ["Montant : 60,00 € → 75,00 €"]

    # Réenregistrée telle quelle, la dépense ne donne aucune phrase.
    vieillir()
    avant = len(journal_page().entries)
    pieces.refresh_from_db()
    pieces.save()
    assert len(journal_page().entries) == avant


def test_trash_and_restore_are_signed_by_the_partner(as_mathis, pieces):
    vieillir()
    as_mathis.post(f"/depenses/{pieces.pk}/corbeille/")
    entree = journal_page().entries[0]
    assert entree.author_name == "Mathis"
    assert textes(entree) == ["a mis à la corbeille la dépense du 20/10/2026 (60,00 €)"]
    assert entree.link_url == ""  # la dépense est à la corbeille : pas de lien mort
    vieillir()
    as_mathis.post(f"/corbeille/depense/{pieces.pk}/restaurer/")
    assert textes(journal_page().entries[0]) == ["a restauré la dépense du 20/10/2026 (60,00 €)"]


def test_reopenings_of_a_sold_vehicle(moto):
    """D50 : chaque réouverture, son motif, et quand la fiche a été verrouillée de nouveau."""
    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    vehicle_services.reopen_vehicle(moto, "Facture oubliée")
    vehicle_services.lock_vehicle(moto)
    vehicle_services.reopen_vehicle(moto, "Erreur de prix")
    reouvertures = vehicle_reopenings(moto)
    assert [r.reason for r in reouvertures] == ["Erreur de prix", "Facture oubliée"]
    assert reouvertures[0].locked_at is None
    assert reouvertures[1].locked_at is not None
    assert reouvertures[0].by == NO_AUTHOR

    # Écrits dans la même seconde, sur la même fiche : une entrée, dans l'ordre de saisie.
    phrases = journal_page().entries[0].lines
    assert [p.text.split(" la fiche")[0] for p in phrases] == [
        "a créé",
        "a rouvert",
        "a verrouillé",
        "a rouvert",
    ]
    assert "Motif de la réouverture : — → Facture oubliée" in phrases[1].changes
    assert phrases[3].changes == ["Motif de la réouverture : Facture oubliée → Erreur de prix"]


def test_cancelled_sale_and_settlements_are_sentences(make_vehicle, mathis, axel):
    vehicule = make_vehicle(brand="Sherco", model="50")
    vehicle_services.record_purchase(
        vehicule,
        price=D("100"),
        date=DATE,
        payments=[Payment(mathis, D("100"))],
        seller=None,
        partner=mathis,
    )
    vente = sale_services.record_sale(
        vehicule,
        price=D("300"),
        date=DATE,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("300"))],
        partner=axel,
    )
    vieillir()
    sale_services.cancel_sale(vente, None, axel)
    vieillir()
    reglement = record_settlement(
        from_partner=axel, to_partner=mathis, amount=D("50"), date=DATE
    ).settlement
    cancel_settlement(reglement, reason="Saisi deux fois")
    tout = [ligne.text for entree in journal_page().entries for ligne in entree.lines]
    assert "a enregistré l'annulation d'un règlement de 50,00 €" in tout
    assert "a enregistré le règlement de 50,00 € versé par Axel à Mathis" in tout
    assert "a annulé la vente du n°1 Sherco 50 (300,00 €)" in tout


def test_filter_by_vehicle_or_by_expense(pieces, moto, make_vehicle, parts_category, axel):
    autre = make_vehicle(brand="Yamaha", model="RM")
    depense(
        [LineEntry("Chaîne", parts_category, D("30"), vehicle=autre, status="RECEIVED")],
        [Payment(axel, D("30"))],
        "30",
    )
    garage = depense(
        [LineEntry("Huile", parts_category, D("12"), status="RECEIVED")],
        [Payment(axel, D("12"))],
        "12",
    )

    def tout(**filtre):
        return " ".join(
            ligne.text for entree in journal_page(**filtre).entries for ligne in entree.lines
        )

    sur_la_tm = tout(vehicle=moto)
    assert "« Disque »" in sur_la_tm and "GG MC250" in sur_la_tm
    assert "Chaîne" not in sur_la_tm and "Huile" not in sur_la_tm
    assert "Yamaha" not in sur_la_tm
    de_l_huile = tout(expense=garage)
    assert "« Huile »" in de_l_huile and "Disque" not in de_l_huile
    assert "fiche" not in de_l_huile


def test_pages_of_fifty(make_vehicle):
    for numero in range(journal.PER_PAGE + 1):
        make_vehicle(model=f"modèle {numero}")
    premiere = journal_page()
    assert len(premiere.entries) == journal.PER_PAGE
    assert premiere.has_next and not premiere.has_previous
    seconde = journal_page(2)
    assert len(seconde.entries) == 1 and seconde.has_previous and not seconde.has_next


def test_journal_never_writes(pieces):
    """Le journal ne fait que lire, en un nombre de requêtes qui ne dépend pas de la page."""
    with CaptureQueriesContext(connection) as requetes:
        journal_page()
    ecritures = [
        q["sql"]
        for q in requetes.captured_queries
        if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert ecritures == []
    assert len(requetes.captured_queries) < 40


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


def test_journal_page_and_its_filters(as_mathis, pieces, moto):
    page = as_mathis.get("/journal/").content.decode()
    assert "Journal des modifications" in page
    assert "a ajouté la dépense du 20/10/2026" in page
    assert 'href="/journal/"' in page  # lien de la barre latérale
    assert as_mathis.get(f"/journal/?vehicule={moto.number}").status_code == 200
    page = as_mathis.get(f"/journal/?depense={pieces.pk}").content.decode()
    assert "Historique de la" in page and "Voir tout le journal" in page
    assert as_mathis.get("/journal/?vehicule=999").status_code == 404
    assert as_mathis.get("/journal/?vehicule=abc").status_code == 404
    assert as_mathis.get("/journal/?page=abc").status_code == 200


def test_empty_journal_invites(as_mathis):
    page = as_mathis.get("/journal/").content.decode()
    assert "Rien n'a encore été enregistré." in page


def test_links_from_the_sheets(as_mathis, pieces, moto):
    fiche = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert f'href="/journal/?vehicule={moto.number}"' in fiche
    assert "Réouvertures" not in fiche
    page = as_mathis.get(pieces.get_absolute_url()).content.decode()
    assert f'href="/journal/?depense={pieces.pk}"' in page and "Voir son historique" in page

    Vehicle.objects.filter(pk=moto.pk).update(status="SOLD")
    vehicle_services.reopen_vehicle(moto, "Facture oubliée")
    fiche = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert "Réouvertures" in fiche and "Facture oubliée" in fiche and "Encore rouverte" in fiche


# ---------------------------------------------------------------------------
# Relecture de la sous-étape 3b
# ---------------------------------------------------------------------------


def test_field_names_and_years_are_written_as_on_screen(moto):
    """« Numéro de série (VIN) » garde ses majuscules ; une année s'écrit « 2019 »."""
    vieillir()
    moto.vin = "JS1ABC"
    moto.year = 2019
    moto.mileage_km = 12500
    moto.save()
    changements = journal_page().entries[0].lines[0].changes
    assert "Numéro de série (VIN) : — → JS1ABC" in changements
    assert "Année : — → 2019" in changements
    assert "Kilométrage : — → 12\u00a0500" in changements


def test_cancelled_sale_of_a_reopened_sheet_is_not_a_lock(as_mathis, make_vehicle, mathis, axel):
    vehicule = make_vehicle(brand="Sherco", model="50")
    vehicle_services.record_purchase(
        vehicule,
        price=D("100"),
        date=DATE,
        payments=[Payment(mathis, D("100"))],
        seller=None,
        partner=mathis,
    )
    vente = sale_services.record_sale(
        vehicule,
        price=D("300"),
        date=DATE,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("300"))],
        partner=axel,
    )
    vehicule.refresh_from_db()
    vehicle_services.reopen_vehicle(vehicule, "Facture oubliée")
    vieillir()
    sale_services.cancel_sale(vente, None, axel)
    tout = [ligne.text for entree in journal_page().entries for ligne in entree.lines]
    assert not any(texte.startswith("a verrouillé") for texte in tout)
    (reouverture,) = vehicle_reopenings(vehicule)
    assert reouverture.ended_by_cancelled_sale
    vehicule.refresh_from_db()
    fiche = as_mathis.get(vehicule.get_absolute_url()).content.decode()
    assert "la vente a été annulée" in fiche


def test_trade_in_filter_leaves_out_the_other_vehicle_receipts(make_vehicle, mathis, axel):
    vehicule = make_vehicle(brand="GG", model="MC250")
    vehicle_services.record_purchase(
        vehicule,
        price=D("1500"),
        date=DATE,
        payments=[Payment(mathis, D("1500"))],
        seller=None,
        partner=mathis,
    )
    reprise = Vehicle(vehicle_type=vehicule.vehicle_type, brand="Yamaha", model="125")
    sale_services.record_sale(
        vehicule,
        price=D("2000"),
        date=DATE,
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("1300"))],
        partner=axel,
        trade_in=TradeIn(reprise, D("700")),
    )
    sur_la_reprise = " ".join(
        ligne.text
        for entree in journal_page(vehicle=Vehicle.objects.get(brand="Yamaha")).entries
        for ligne in entree.lines
    )
    assert "a enregistré la vente du n°1 GG MC250" in sur_la_reprise
    assert "encaissement" not in sur_la_reprise


def test_beyond_the_last_page_and_trashed_expense_filter(as_mathis, pieces):
    assert "Rien de plus ancien." in as_mathis.get("/journal/?page=99").content.decode()
    expense_services.trash_expense(pieces)
    page = as_mathis.get(f"/journal/?depense={pieces.pk}").content.decode()
    assert "(à la corbeille)" in page and 'href=""' not in page


# --- Acompte en plusieurs versements (D89) -----------------------------------------------


@pytest.fixture
def achetee(make_vehicle, mathis):
    vehicule = make_vehicle(brand="Sherco", model="80")
    vehicle_services.record_purchase(
        vehicule,
        price=D("400"),
        date=DATE,
        payments=[Payment(mathis, D("400"))],
        seller=None,
        partner=mathis,
    )
    vehicule.refresh_from_db()
    vieillir()
    return vehicule


def test_deposit_and_its_receipts_make_one_entry(achetee, mathis, axel):
    sale_services.record_deposit(
        achetee,
        date=DATE,
        receipts=[Payment(mathis, D("200")), Payment(axel, D("100"))],
        buyer=None,
        partner=mathis,
    )
    # Le passage de la fiche en « Réservé » a son entrée à part (D78 : un sujet par entrée).
    (entree,) = [e for e in journal_page().entries if e.key[0] == "acompte"]
    assert textes(entree) == [
        "a enregistré l'acompte sur le n°1 Sherco 80",
        "a ajouté le versement de Mathis (200,00\xa0€)",
        "a ajouté le versement d'Axel (100,00\xa0€)",
    ]
    assert entree.link_label == "Voir la fiche"
    filtre = [e for e in journal_page(vehicle=achetee).entries if e.key[0] == "acompte"]
    assert [textes(e) for e in filtre] == [textes(entree)]


def test_receipt_correction_and_removal_are_in_the_journal(achetee, mathis, axel):
    acompte = sale_services.record_deposit(
        achetee,
        date=DATE,
        receipts=[Payment(mathis, D("200")), Payment(axel, D("100"))],
        buyer=None,
        partner=mathis,
    )
    vieillir()
    mathis_versement, axel_versement = acompte.receipts.order_by("id")
    sale_services.update_deposit_receipt(
        mathis_versement, amount=D("250"), date=DATE, received_by=mathis
    )
    vieillir()
    sale_services.remove_deposit_receipt(axel_versement)

    retrait, correction, *_ = journal_page().entries
    assert textes(retrait) == ["a mis à la corbeille le versement d'Axel (100,00\xa0€)"]
    assert textes(correction) == ["a modifié le versement de Mathis (250,00\xa0€)"]
    assert correction.lines[0].changes == ["Montant : 200,00\xa0€ → 250,00\xa0€"]


def test_old_deposit_amount_stays_but_its_emptying_is_hidden(achetee, mathis):
    """Avant la 2.0.0, le montant était sur l'acompte : ses changements restent lisibles ;
    son passage à vide par la migration `sales.0005` n'est pas montré."""
    acompte = Deposit.objects.create(
        vehicle=achetee, date=DATE, legacy_amount=D("300"), legacy_received_by=mathis
    )
    vieillir()
    acompte.legacy_amount = D("350")
    acompte.save()
    vieillir()
    acompte.legacy_amount = None
    acompte.legacy_received_by = None
    acompte.status = Deposit.Status.KEPT
    acompte.save()

    vidage, correction, creation, *_ = journal_page().entries
    assert textes(vidage) == ["a modifié l'acompte sur le n°1 Sherco 80"]
    assert vidage.lines[0].changes == ["État : En cours → Conservé"]
    assert correction.lines[0].changes == ["Montant (avant la 2.0) : 300,00\xa0€ → 350,00\xa0€"]
    assert textes(creation) == ["a enregistré l'acompte de 300,00\xa0€ sur le n°1 Sherco 80"]

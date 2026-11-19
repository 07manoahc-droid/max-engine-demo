"""Consultation en lecture seule (D79) : chaque écran s'affiche sans aucune action, tout envoi
est refusé, et la base n'est jamais écrite, même ouverte sur un fichier en lecture seule."""

import datetime as dt
import os
import re
import sqlite3
import stat
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.urls import Resolver404, resolve

from accounting.services import Payment
from accounting.settlements import record_settlement
from config.settings import read_only_database
from core.backup import read_only_banner
from core.middleware import READ_ONLY_PAGES
from core.models import Attachment, Contact, VehicleType
from expenses import services as expense_services
from expenses.models import Expense
from expenses.services import LineEntry
from sales import services as sale_services
from sales.models import Deposit, DepositReceipt
from sales.services import TradeIn
from vehicles import services as vehicle_services
from vehicles.models import Vehicle

DATE = dt.date(2026, 10, 20)
BASE_DIR = Path(django_settings.BASE_DIR)


def depense(lines, payments, total):
    return expense_services.save_expense(
        None,
        date=DATE,
        supplier_name="Fournisseur",
        order_number="",
        total_paid=D(total),
        is_personal=False,
        notes="",
        lines=lines,
        payments=payments,
    )


def acheter(vehicule, partner, prix="1000"):
    vehicle_services.record_purchase(
        vehicule,
        price=D(prix),
        date=DATE,
        payments=[Payment(partner, D(prix))],
        seller=None,
        partner=partner,
    )
    vehicule.refresh_from_db()


@pytest.fixture
def garage(make_vehicle, parts_category, mathis, axel):
    """Un garage où chaque écran a quelque chose à proposer : achat à faire, réparation
    avec pièces, retour et photo, acompte, vente avec reprise, fiche rouverte, règlement,
    dépense du garage, corbeille."""
    attente = make_vehicle(brand="Honda", model="CR")
    reparation = make_vehicle(brand="GG", model="MC250")
    acheter(reparation, mathis)
    vehicle_services.change_status(reparation, "IN_REPAIR", mathis)
    pieces = depense(
        [
            LineEntry("Disque", parts_category, D("60"), vehicle=reparation, status="RECEIVED"),
            LineEntry("Joint", parts_category, D("20"), vehicle=reparation, status="RETURNED"),
        ],
        [Payment(axel, D("80"))],
        "80",
    )
    expense_services.record_refund(
        pieces.lines.get(label="Joint"), date=DATE, amount=D("20"), refunded_to=axel
    )
    expense_services.add_part_to_order(label="Câble", vehicle=reparation)
    Attachment.objects.create(vehicle=reparation, file="vehicules/2/moto.jpg")
    Attachment.objects.create(expense=pieces, kind=Attachment.Kind.INVOICE, file="f.pdf")

    en_vente = make_vehicle(brand="Yamaha", model="YZ")
    acheter(en_vente, axel)
    vehicle_services.change_status(en_vente, "FOR_SALE", axel)
    # Acompte reçu par les deux (D89) : la fiche montre ses deux versements, sans action.
    sale_services.record_deposit(
        en_vente,
        date=DATE,
        receipts=[Payment(axel, D("100")), Payment(mathis, D("50"))],
        buyer=None,
        partner=axel,
    )

    vendu = make_vehicle(brand="Yamaha", model="RM")
    acheter(vendu, mathis)
    reprise = Vehicle(vehicle_type=VehicleType.objects.first(), brand="Sherco", model="50")
    sale_services.record_sale(
        vendu,
        price=D("2000"),
        date=dt.date(2026, 10, 30),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(axel, D("1700"))],
        partner=axel,
        trade_in=TradeIn(reprise, D("300")),
    )
    rouvert = make_vehicle(brand="KTM", model="SX")
    acheter(rouvert, axel)
    sale_services.record_sale(
        rouvert,
        price=D("1500"),
        date=dt.date(2026, 10, 30),
        buyer=None,
        sold_where="",
        notes="",
        receipts=[Payment(mathis, D("1500"))],
        partner=mathis,
    )
    rouvert.refresh_from_db()
    vehicle_services.reopen_vehicle(rouvert, "Facture oubliée")

    depense(
        [LineEntry("Huile", parts_category, D("12"), status="RECEIVED")],
        [Payment(mathis, D("12"))],
        "12",
    )
    jetee = depense(
        [LineEntry("Erreur", parts_category, D("5"), vehicle=reparation, status="RECEIVED")],
        [Payment(mathis, D("5"))],
        "5",
    )
    expense_services.trash_expense(jetee)
    vehicle_services.trash_vehicle(make_vehicle(brand="Doublon", model="X"))
    comptes = record_settlement(from_partner=axel, to_partner=mathis, amount=D("1"), date=DATE)
    vendeur = Contact.objects.create(name="Jean Martin", phone="06 12", city="Lyon")
    Vehicle.objects.filter(pk=attente.pk).update(seller=vendeur)
    Contact.objects.create(name="Contact jeté").soft_delete()
    return {"attente": attente, "settlement": comptes.settlement}


def pages_to_visit() -> list[str]:
    adresses = [
        "/",
        "/corbeille/",
        "/journal/",
        "/vehicules/",
        "/vendus/",
        "/abandonnes/",
        "/pieces-a-commander/",
        "/comptes/",
        "/comptes/depenses-du-garage/",
        "/bilan/",
        "/contacts/",
        "/contacts/?q=jean",
    ]
    adresses += [f"/contacts/{c.pk}/" for c in Contact.objects.all()]
    adresses += [v.get_absolute_url() for v in Vehicle.objects.all()]
    adresses += [f"/journal/?vehicule={v.number}" for v in Vehicle.objects.all()]
    adresses += [e.get_absolute_url() for e in Expense.objects.all()]
    adresses += [f"/journal/?depense={e.pk}" for e in Expense.all_objects.all()]
    return adresses


LIEN = re.compile(r'(?:href|action|hx-get|hx-post|hx-put|hx-patch|hx-delete)="([^"]*)"')
ENVOI = re.compile(r'method="post"|hx-(?:post|put|patch|delete)=', re.IGNORECASE)


def action_links(page: str) -> list[str]:
    """Les liens d'une page vers autre chose qu'une page de consultation."""
    fautifs = []
    for adresse in LIEN.findall(page):
        chemin = adresse.split("?")[0].split("#")[0]
        if not chemin.startswith("/") or chemin.startswith(("/static/", "/media/")):
            continue
        try:
            nom = resolve(chemin).view_name
        except Resolver404:
            fautifs.append(adresse)
            continue
        if nom not in READ_ONLY_PAGES:
            fautifs.append(f"{adresse} ({nom})")
    return fautifs


@pytest.fixture
def lecture(settings, client):
    settings.READ_ONLY = True
    settings.READ_ONLY_SOURCE = "MAx Engine 2026-11-04 18-02 (fermeture).zip"
    return client


def test_every_screen_shows_without_any_action(lecture, garage):
    """Aucun formulaire d'envoi, aucun lien vers un formulaire, sur aucun écran."""
    problemes = {}
    for adresse in pages_to_visit():
        reponse = lecture.get(adresse)
        assert reponse.status_code == 200, adresse
        page = reponse.content.decode()
        assert "Consultation de la sauvegarde du 04/11/2026 à 18:02" in page, adresse
        fautifs = action_links(page) + ENVOI.findall(page)
        if fautifs:
            problemes[adresse] = fautifs
    assert problemes == {}


def test_export_still_works(lecture, garage):
    """D65 : l'export ne fait que lire."""
    reponse = lecture.get("/bilan/export/")
    assert reponse.status_code == 200
    assert reponse["Content-Type"].startswith("application/vnd.openxmlformats")


def test_no_partner_to_choose(lecture, garage):
    reponse = lecture.get("/")
    assert reponse.status_code == 200
    assert "Changer" not in reponse.content.decode()
    assert lecture.get("/qui-es-tu/").status_code == 403


@pytest.mark.parametrize(
    "adresse",
    [
        "/vehicules/ajouter/",
        "/depenses/ajouter/",
        "/pieces-a-commander/ajouter/",
        "/comptes/reglements/ajouter/",
        "/admin/",
    ],
)
def test_action_pages_are_refused_with_an_explanation(lecture, garage, adresse):
    reponse = lecture.get(adresse)
    assert reponse.status_code == 403
    assert "Rien ne peut être modifié ici" in reponse.content.decode()


def test_deposit_pages_are_refused(lecture, garage):
    """Les versements se voient sur la fiche ; les ajouter, les corriger ou les retirer est
    refusé, sans rien écrire."""
    acompte = Deposit.objects.get(status=Deposit.Status.ACTIVE)
    versement = DepositReceipt.objects.filter(deposit=acompte).first()
    fiche = lecture.get(acompte.vehicle.get_absolute_url()).content.decode()
    assert "Versements" in fiche and "Ajouter un versement" not in fiche
    for adresse in (
        f"/acomptes/{acompte.pk}/corriger/",
        f"/acomptes/{acompte.pk}/versements/ajouter/",
        f"/acomptes/versements/{versement.pk}/corriger/",
    ):
        assert lecture.get(adresse).status_code == 403, adresse
    assert lecture.post(f"/acomptes/versements/{versement.pk}/retirer/").status_code == 403
    versement.refresh_from_db()
    assert not versement.is_deleted


def test_every_post_is_refused_and_writes_nothing(lecture, garage, mathis):
    attente = garage["attente"]
    for adresse in [
        f"/vehicules/{attente.number}/statut/",
        f"/vehicules/{attente.number}/corbeille/",
        "/qui-es-tu/",
        f"/comptes/reglements/{garage['settlement'].pk}/annuler/",
        "/",
    ]:
        reponse = lecture.post(adresse, {"status": "ABANDONED", "partner": mathis.pk})
        assert reponse.status_code == 403, adresse
    attente.refresh_from_db()
    assert attente.status == "WAITING_PURCHASE" and not attente.is_deleted


def test_htmx_post_is_sent_to_the_explanation(lecture, garage):
    attente = garage["attente"]
    reponse = lecture.post(f"/vehicules/{attente.number}/statut/", headers={"HX-Request": "true"})
    assert reponse["HX-Redirect"] == "/consultation/"


@pytest.mark.parametrize("methode", ["get", "post"])
def test_unknown_page_in_read_only(lecture, db, methode):
    """Une adresse inconnue donne la page introuvable, avec le bandeau et sans action."""
    reponse = getattr(lecture, methode)("/nimporte-quoi/")
    assert reponse.status_code == 404
    page = reponse.content.decode()
    assert "Consultation de la sauvegarde du 04/11/2026" in page
    assert action_links(page) == [] and ENVOI.findall(page) == []


def test_explanation_page_outside_read_only_goes_home(as_mathis):
    assert as_mathis.get("/consultation/").url == "/"


def test_banner_without_a_known_backup():
    assert read_only_banner("") == "Consultation en lecture seule : rien ne peut être modifié ici."
    assert read_only_banner("autre.zip") == read_only_banner("")
    assert read_only_banner(
        r"C:\Temp\MAx Engine 2026-11-04 18-02 2 (manuelle - avant import).zip"
    ).startswith("Consultation de la sauvegarde du 04/11/2026 à 18:02")


def test_normal_mode_is_untouched(as_mathis, garage):
    """Sans READ_ONLY, aucun bandeau, et les envois marchent toujours."""
    page = as_mathis.get("/").content.decode()
    assert "rien ne peut être modifié ici" not in page
    assert "Ajouter" in page


def test_read_only_database_settings(tmp_path):
    reglages = {
        "NAME": tmp_path / "garage.sqlite3",
        "OPTIONS": {"transaction_mode": "IMMEDIATE"},
    }
    lecture_seule = read_only_database(reglages)
    assert lecture_seule["NAME"].startswith("file:///")
    assert lecture_seule["NAME"].endswith("garage.sqlite3?mode=ro")
    assert lecture_seule["OPTIONS"] == {}


# ---------------------------------------------------------------------------
# Sur un vrai fichier en lecture seule, dans un processus à part
# ---------------------------------------------------------------------------

SCRIPT = r"""
import django, sys
django.setup()
from django.test import Client
client = Client(HTTP_HOST="127.0.0.1")
codes = []
for adresse in sys.argv[1:]:
    codes.append(client.get(adresse).status_code)
codes.append(client.post("/vehicules/ajouter/", {"brand": "X"}).status_code)
print(" ".join(map(str, codes)))
"""


# Données enregistrées pour être copiées ; les associés créés par migration sont remis ensuite.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_real_read_only_file_is_never_written(garage, tmp_path):
    """La copie de la base est marquée en lecture seule (comme une sauvegarde qu'on consulte) :
    chaque écran s'ouvre, et le fichier ne change pas d'un octet."""
    base = tmp_path / "garage.sqlite3"
    destination = sqlite3.connect(base)
    connection.ensure_connection()
    connection.connection.backup(destination)
    destination.close()
    avant = base.read_bytes()
    os.chmod(base, stat.S_IREAD)
    adresses = pages_to_visit() + ["/bilan/export/"]
    env = {
        **os.environ,
        "READ_ONLY": "1",
        "DATABASE_PATH": str(base),
        "SECRET_KEY": "essai",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("DEBUG", None)
    try:
        resultat = subprocess.run(
            [sys.executable, "-c", SCRIPT, *adresses],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    finally:
        os.chmod(base, stat.S_IREAD | stat.S_IWRITE)
    assert resultat.returncode == 0, resultat.stderr[-3000:]
    codes = resultat.stdout.split()
    assert codes[:-1] == ["200"] * len(adresses), dict(zip(adresses, codes, strict=False))
    assert codes[-1] == "403"
    assert base.read_bytes() == avant
    assert sorted(p.name for p in tmp_path.iterdir()) == ["garage.sqlite3"]


def test_the_screen_check_does_see_actions(as_mathis, garage):
    """Contrôle du contrôle : hors consultation, la même recherche trouve bien les actions
    (sinon le premier test passerait sans rien vérifier)."""
    vehicule = Vehicle.objects.get(brand="GG")
    page = as_mathis.get(vehicule.get_absolute_url()).content.decode()
    assert any("vehicles:edit" in lien for lien in action_links(page))
    assert ENVOI.findall(page)


SCRIPT_ECRITURE = r"""
import django
django.setup()
from django.db import connection
try:
    connection.cursor().execute("CREATE TABLE essai (a integer)")
except Exception as erreur:
    print("refus :", erreur)
else:
    print("écrit")
"""


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_mode_ro_alone_refuses_writes(garage, tmp_path):
    """Relecture : sans le marquage du fichier, `?mode=ro` suffit à refuser une écriture ;
    dans un dossier avec espace et parenthèses, comme le vrai dossier du projet."""
    dossier = tmp_path / "MAx Engine (essai) lecture"
    dossier.mkdir()
    base = dossier / "garage.sqlite3"
    destination = sqlite3.connect(base)
    connection.ensure_connection()
    connection.connection.backup(destination)
    destination.close()
    avant = base.read_bytes()
    env = {
        **os.environ,
        "READ_ONLY": "1",
        "DATABASE_PATH": str(base),
        "SECRET_KEY": "essai",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHONIOENCODING": "utf-8",
    }
    resultat = subprocess.run(
        [sys.executable, "-c", SCRIPT_ECRITURE],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert resultat.returncode == 0, resultat.stderr[-3000:]
    assert resultat.stdout.startswith("refus :") and "readonly" in resultat.stdout
    assert base.read_bytes() == avant

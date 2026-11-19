"""Remplit une base vide avec un garage inventé, pour essayer MAx Engine et faire des captures.

    uv run python manage.py migrate
    uv run python manage.py donnees_demo

Tout passe par les mêmes services qu'à l'écran (achat, dépense, retour, acompte, vente avec
reprise, règlement) : les comptes de la démo obéissent aux mêmes règles que les vrais, et
`controle_comptes` doit les trouver cohérents. Motos, contacts, fournisseurs et montants sont
inventés ; les photos sont des images de démonstration fabriquées avec Pillow.

La commande refuse une base qui contient déjà un véhicule ou une dépense (corbeille
comprise) : elle ne mélange jamais la démo à de vraies données.
"""

import datetime as dt
import io
from decimal import Decimal as D

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from accounting import queries
from accounting import services as comptes
from accounting.services import Payment
from accounting.settlements import record_settlement
from core.models import Contact, ExpenseCategory, Partner, PurchaseOrigin, VehicleType
from expenses.models import Expense, ExpenseLine
from expenses.services import LineEntry, add_parts_to_order, record_refund, save_expense
from sales.services import TradeIn, record_deposit, record_sale
from vehicles.models import ComparableListing, Task, Vehicle, WorkLog
from vehicles.services import add_photos, change_status, create_vehicle, record_purchase

S = Vehicle.Status
LINE = ExpenseLine.Status

# Couleurs des photos de démonstration (une par moto), en RVB.
TEINTES = [(37, 99, 235), (220, 38, 38), (22, 163, 74), (234, 88, 12), (124, 58, 237)]


def jours(n):
    """La date d'il y a `n` jours : la démo reste « récente » quel que soit le jour."""
    return timezone.localdate() - dt.timedelta(days=n)


def photo(nom, titre, teinte):
    """Une image JPEG de démonstration : un fond coloré, une silhouette et un titre."""
    image = Image.new("RGB", (1200, 900), teinte)
    dessin = ImageDraw.Draw(image)
    clair = tuple(min(255, c + 70) for c in teinte)
    # Silhouette très simple : deux roues et un cadre.
    for x in (330, 870):
        dessin.ellipse((x - 170, 470, x + 170, 810), outline=clair, width=28)
    dessin.line((330, 640, 560, 420, 870, 640), fill=clair, width=30)
    dessin.line((560, 420, 760, 400, 870, 640), fill=clair, width=30)
    dessin.line((740, 400, 690, 300), fill=clair, width=24)
    police = ImageFont.load_default(size=64)
    dessin.text((60, 60), titre, fill="white", font=police)
    dessin.text((60, 150), "Photo de démonstration", fill=clair, font=ImageFont.load_default(40))
    tampon = io.BytesIO()
    image.save(tampon, "JPEG", quality=80)
    return SimpleUploadedFile(nom, tampon.getvalue(), content_type="image/jpeg")


class Command(BaseCommand):
    help = "Remplit une base vide avec un garage inventé (démonstration et captures d'écran)."

    def handle(self, *args, **options):
        if settings.READ_ONLY:
            raise CommandError("Mode lecture seule : la démo ne peut pas être créée.")
        if Vehicle.all_objects.exists() or Expense.all_objects.exists():
            raise CommandError(
                "La base contient déjà des véhicules ou des dépenses. La démo se crée sur une "
                "base vide : règle DATABASE_PATH et MEDIA_PATH vers un dossier à part."
            )
        associes = list(Partner.objects.filter(is_active=True).order_by("pk")[:2])
        if len(associes) != 2:
            raise CommandError("Il faut deux associés actifs : lance d'abord « migrate ».")
        try:
            with transaction.atomic():
                vehicules = Demo(*associes).creer()
        except ValidationError as erreur:
            raise CommandError(f"Démo refusée par les règles du logiciel : {erreur}") from erreur
        for vehicule in vehicules:
            self.stdout.write(f"  {vehicule} : {vehicule.get_status_display()}")
        self.stdout.write(self.style.SUCCESS(f"Démo créée : {len(vehicules)} véhicules."))


class Demo:
    """Le garage inventé, construit dans l'ordre des dates."""

    def __init__(self, premier, second):
        self.a, self.b = premier, second
        self.moto = VehicleType.objects.get_or_create(name="Moto", defaults={"order": 1})[0]
        self.scooter = VehicleType.objects.get_or_create(name="Scooter", defaults={"order": 2})[0]
        self.origines = {o.name: o for o in PurchaseOrigin.objects.all()}
        self.categories = {c.code: c for c in ExpenseCategory.objects.all()}
        self.photos = 0

    # --- Outils ----------------------------------------------------------------------------

    def vehicule(self, marque, modele, annee, **champs):
        vehicule = Vehicle(vehicle_type=self.moto, brand=marque, model=modele, year=annee, **champs)
        return create_vehicle(vehicule, self.a)

    def acheter(self, vehicule, prix, quand, payeurs, vendeur):
        record_purchase(
            vehicule,
            price=D(prix),
            date=jours(quand),
            payments=[Payment(associe, D(part)) for associe, part in payeurs],
            seller=vendeur,
            partner=self.a,
        )
        vehicule.refresh_from_db()

    def ligne(self, libelle, prix, vehicule=None, categorie="parts", ref="", etat=LINE.RECEIVED):
        return LineEntry(
            label=libelle,
            category=self.categories[categorie],
            unit_price=D(prix),
            reference=ref,
            vehicle=vehicule,
            status=etat if categorie == "parts" else "",
        )

    def depense(self, quand, fournisseur, total, payeurs, lignes, commande="", notes=""):
        return save_expense(
            None,
            date=jours(quand),
            supplier_name=fournisseur,
            order_number=commande,
            total_paid=D(total),
            is_personal=False,
            notes=notes,
            lines=lignes,
            payments=[Payment(associe, D(part)) for associe, part in payeurs],
        )

    def statut(self, vehicule, *statuts):
        for nouveau in statuts:
            change_status(vehicule, nouveau, self.a)
            vehicule.refresh_from_db()

    def galerie(self, vehicule, etapes):
        teinte = TEINTES[vehicule.number % len(TEINTES)]
        for etape in etapes:
            self.photos += 1
            fichier = photo(f"demo-{self.photos}.jpg", f"{vehicule.brand} {vehicule.model}", teinte)
            add_photos(vehicule, [fichier], etape)

    def intervention(self, vehicule, quand, texte, heures, par=None, sorte=WorkLog.Kind.REPAIR):
        WorkLog.objects.create(
            vehicle=vehicule, date=jours(quand), description=texte, kind=sorte,
            done_by=par, hours=D(heures) if heures else None,
        )

    # --- Le garage -------------------------------------------------------------------------

    def creer(self):
        a, b = self.a, self.b
        leboncoin = self.origines.get("Leboncoin")
        connaissance = self.origines.get("Connaissance")

        # 1. Yamaha YZ125 : achetée à deux, remise en état, vendue avec une reprise.
        yz = self.vehicule(
            "Yamaha", "YZ125", 2012, displacement_cc=125, color="Bleu",
            origin=leboncoin, location="Villeurbanne", asking_price=D("1600"),
        )
        self.acheter(
            yz, "1450", 120, [(a, "725"), (b, "725")],
            Contact(name="Julien Moreau", phone="06 00 00 00 01", city="Villeurbanne"),
        )
        self.statut(yz, S.IN_REPAIR)
        self.depense(
            112, "Pièces Cross Express", "164.80", [(a, "164.80")],
            [
                self.ligne("Kit piston complet", "89.90", yz, ref="PX-125-54", etat=LINE.FITTED),
                self.ligne("Kit chaîne et pignons", "64.50", yz, ref="KC-520-48", etat=LINE.FITTED),
            ],
            commande="DEMO-1001",
        )  # 10,40 € de frais de port, répartis au prorata
        self.depense(
            104, "Atelier Rectif 69", "119.99", [(b, "119.99")],
            [self.ligne("Rectification du cylindre", "119.99", yz, "subcontracting")],
        )
        self.intervention(yz, 100, "Remontage du haut moteur, piston neuf.", "4.5", a)
        self.intervention(yz, 98, "Chaîne, pignons et réglage de la carburation.", "2", b,
                          WorkLog.Kind.MAINTENANCE)
        self.galerie(yz, ["LISTING", "BEFORE", "AFTER"])
        self.statut(yz, S.READY_FOR_SALE, S.FOR_SALE)
        reprise = Vehicle(
            vehicle_type=self.scooter, brand="Peugeot", model="Kisbee 50", year=2015,
            displacement_cc=50,
        )
        record_sale(
            yz,
            price=D("2600"),
            date=jours(60),
            buyer=Contact(name="Sophie Bernard", phone="06 00 00 00 02", city="Lyon",
                          email="sophie.bernard@exemple.fr"),
            sold_where="Leboncoin, essai sur place",
            notes="Scooter repris dans la vente.",
            receipts=[Payment(a, D("2300"))],
            partner=a,
            trade_in=TradeIn(vehicle=reprise, value=D("300")),
        )

        # Règlement entre associés après cette vente : le solde du moment, réglé en entier.
        solde = comptes.balance(queries.positions(queries.load_accounts()))
        if solde.debtor is not None:
            associes = {a.pk: a, b.pk: b}
            record_settlement(
                from_partner=associes[solde.debtor], to_partner=associes[solde.creditor],
                amount=solde.amount, date=jours(58), notes="Après la vente de la YZ125.",
            )

        # 2. KTM 125 SX : achetée par un seul associé, en vente, avec un retour remboursé.
        ktm = self.vehicule(
            "KTM", "125 SX", 2016, displacement_cc=125, color="Orange", origin=leboncoin,
            location="Grenoble", asking_price=D("2400"),
        )
        self.acheter(ktm, "2100", 90, [(b, "2100")],
                     Contact(name="Thomas Petit", phone="06 00 00 00 03", city="Grenoble"))
        self.statut(ktm, S.IN_REPAIR)
        commande = self.depense(
            85, "Moto Pièces Discount", "112.40", [(b, "112.40")],
            [
                self.ligne("Plaquettes de frein avant", "32.90", ktm, ref="PL-FR-221"),
                self.ligne("Durite de frein avant", "41.50", ktm, ref="DU-FR-40"),
                self.ligne("Filtre à air", "29.90", ktm, ref="FA-125-01"),
            ],
            commande="DEMO-1002",
        )
        durite = commande.lines.get(label="Durite de frein avant")
        record_refund(durite, date=jours(70), amount=D("42.95"), refunded_to=b,
                      notes="Mauvaise longueur, renvoyée.")
        self.depense(
            80, "Moto Pièces Discount", "18.90", [(a, "18.90")],
            [self.ligne("Huile de boîte 1 L", "18.90", ktm, "consumable")],
        )
        self.intervention(ktm, 78, "Freinage avant refait, vidange de boîte.", "2.5", b)
        self.galerie(ktm, ["BEFORE", "AFTER"])
        self.statut(ktm, S.READY_FOR_SALE, S.FOR_SALE)
        ktm.sale_price_target = D("2990")
        ktm.listed_on = jours(30)
        ktm.save(update_fields=["sale_price_target", "listed_on"])
        for prix, annee, km in (("2850", 2016, None), ("3200", 2017, None), ("2700", 2015, None)):
            ComparableListing.objects.create(
                vehicle=ktm, url="https://www.exemple.fr/annonce", price=D(prix), year=annee,
                mileage_km=km, noted_on=jours(31),
            )

        # 3. Husqvarna TC 85 : en réparation, pièces à commander et tâches.
        tc = self.vehicule(
            "Husqvarna", "TC 85", 2015, displacement_cc=85, color="Blanc",
            origin=connaissance, location="Annecy",
        )
        self.acheter(tc, "900", 40, [(a, "900")],
                     Contact(name="Marc Lefèvre", phone="06 00 00 00 04", city="Annecy"))
        self.statut(tc, S.IN_REPAIR)
        self.depense(
            35, "Pièces Cross Express", "58.40", [(a, "29.20"), (b, "29.20")],
            [self.ligne("Kit joints spi de fourche", "52.90", tc, ref="JS-FO-36")],
            commande="DEMO-1003",
        )
        add_parts_to_order([
            {"label": "Embrayage complet", "reference": "EMB-85-07", "unit_price": D("119"),
             "vehicle": tc},
            {"label": "Poignée de gaz", "unit_price": D("24.90"), "vehicle": tc},
        ])
        for ordre, (tache, fait) in enumerate(
            [("Démonter la fourche", True), ("Changer les joints spi", False),
             ("Commander l'embrayage", False), ("Essai routier", False)]
        ):
            Task.objects.create(vehicle=tc, label=tache, is_done=fait, order=ordre,
                                done_at=jours(20) if fait else None)
        self.galerie(tc, ["BEFORE", "DURING"])

        # 4. Honda CR125R : réservée avec un acompte.
        cr = self.vehicule(
            "Honda", "CR125R", 2004, displacement_cc=125, color="Rouge", origin=leboncoin,
            location="Chambéry",
        )
        self.acheter(cr, "650", 75, [(a, "325"), (b, "325")],
                     Contact(name="Nicolas Girard", phone="06 00 00 00 05", city="Chambéry"))
        self.statut(cr, S.IN_REPAIR)
        self.depense(
            65, "Atelier Rectif 69", "180.00", [(a, "180")],
            [self.ligne("Réfection de la selle", "180", cr, "subcontracting")],
        )
        self.galerie(cr, ["AFTER"])
        self.statut(cr, S.READY_FOR_SALE, S.FOR_SALE)
        record_deposit(
            cr,
            date=jours(6),
            receipts=[Payment(b, D("300"))],
            buyer=Contact(name="Léa Rousseau", phone="06 00 00 00 06", city="Aix-les-Bains"),
            partner=b,
        )

        # 5. Beta RR 300 : repérée, en attente d'achat.
        self.vehicule(
            "Beta", "RR 300", 2013, displacement_cc=300, origin=leboncoin, location="Valence",
            asking_price=D("1800"), listing_url="https://www.exemple.fr/annonce-beta",
            notes="À voir samedi : demander les factures d'entretien.",
        )

        # 6. Yamaha PW50 : achetée pour les pièces, abandonnée.
        pw = self.vehicule("Yamaha", "PW50", 2009, displacement_cc=50, origin=connaissance)
        self.acheter(pw, "150", 55, [(b, "150")], None)
        self.statut(pw, S.ABANDONED)

        # Dépenses générales du garage (sans véhicule).
        self.depense(
            50, "Outillage Pro", "189.90", [(a, "189.90")],
            [self.ligne("Servante d'atelier", "189.90", None, "tooling")],
        )
        self.depense(
            12, "Moto Pièces Discount", "34.50", [(b, "34.50")],
            [self.ligne("Dégraissant et chiffons", "34.50", None, "consumable")],
        )

        return list(Vehicle.objects.order_by("number"))

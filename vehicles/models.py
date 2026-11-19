"""Modèles des véhicules : fiche, statuts, travaux, choses à faire, annonces comparables."""

from django.db import models
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone

from core.history import partner_history
from core.models import SoftDeleteModel, TimeStampedModel

MONEY = {"max_digits": 10, "decimal_places": 2}


class Vehicle(SoftDeleteModel):
    """Un véhicule du garage, de l'annonce à la vente.

    Le prix d'achat n'est pas un champ : c'est une ligne de dépense « Achat véhicule ».
    """

    class Status(models.TextChoices):
        WAITING_PURCHASE = "WAITING_PURCHASE", "En attente d'achat"
        PURCHASED = "PURCHASED", "Acheté"
        IN_REPAIR = "IN_REPAIR", "En réparation"
        READY_FOR_SALE = "READY_FOR_SALE", "Prêt à vendre"
        FOR_SALE = "FOR_SALE", "En vente"
        RESERVED = "RESERVED", "Réservé"
        SOLD = "SOLD", "Vendu"
        ABANDONED = "ABANDONED", "Abandonné"

    number = models.PositiveIntegerField(
        "numéro", unique=True, editable=False, help_text="Attribué automatiquement."
    )
    vehicle_type = models.ForeignKey(
        "core.VehicleType", on_delete=models.PROTECT, verbose_name="type de véhicule"
    )
    brand = models.CharField("marque", max_length=60)
    model = models.CharField("modèle", max_length=60)
    year = models.PositiveSmallIntegerField("année", null=True, blank=True)
    displacement_cc = models.PositiveIntegerField("cylindrée (cm³)", null=True, blank=True)
    vin = models.CharField("numéro de série (VIN)", max_length=17, blank=True)
    registration = models.CharField("immatriculation", max_length=15, blank=True)
    mileage_km = models.PositiveIntegerField("kilométrage", null=True, blank=True)
    color = models.CharField("couleur", max_length=30, blank=True)
    status = models.CharField(
        "statut", max_length=20, choices=Status.choices, default=Status.WAITING_PURCHASE
    )
    origin = models.ForeignKey(
        "core.PurchaseOrigin",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="origine d'achat",
    )
    seller = models.ForeignKey(
        "core.Contact",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="vehicles_sold",
        verbose_name="vendeur",
    )
    listing_url = models.URLField("annonce d'origine", max_length=500, blank=True)
    asking_price = models.DecimalField("prix demandé", **MONEY, null=True, blank=True)
    location = models.CharField("lieu", max_length=100, blank=True)
    purchased_on = models.DateField("acheté le", null=True, blank=True)
    sale_price_target = models.DecimalField("prix de vente affiché", **MONEY, null=True, blank=True)
    listed_on = models.DateField("mis en vente le", null=True, blank=True)
    notes = models.TextField("notes", blank=True)
    # « Rouvrir la fiche » d'un véhicule vendu (D22) : vide = dépenses verrouillées.
    reopened_at = models.DateTimeField("fiche rouverte le", null=True, blank=True)
    reopen_reason = models.TextField("motif de la réouverture", blank=True)

    history = partner_history()

    class Meta:
        verbose_name = "véhicule"
        verbose_name_plural = "véhicules"
        ordering = ["number"]
        constraints = [
            models.CheckConstraint(
                condition=Q(reopened_at__isnull=True) | ~Q(reopen_reason=""),
                name="vehicle_reopen_needs_reason",
                violation_error_message="Indique le motif de la réouverture.",
            ),
            models.CheckConstraint(
                condition=Q(asking_price__isnull=True) | Q(asking_price__gte=0),
                name="vehicle_asking_price_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(sale_price_target__isnull=True) | Q(sale_price_target__gte=0),
                name="vehicle_sale_price_target_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    def save(self, *args, **kwargs):
        if self.number is None:
            # Les véhicules supprimés comptent aussi : un numéro n'est jamais réutilisé.
            plus_grand = Vehicle.all_objects.aggregate(plus_grand=Max("number"))["plus_grand"]
            self.number = (plus_grand or 0) + 1
        self.vin = self.vin.strip().upper()
        # Un motif fait seulement d'espaces est vide : la contrainte de la base le refuse.
        self.reopen_reason = self.reopen_reason.strip()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("vehicles:detail", args=[self.number])

    @property
    def display_name(self) -> str:
        """`n°12 · Yamaha YZ250 2004 · JS1RJ…` ; les parties vides sont omises (D14)."""
        description = " ".join(
            str(partie) for partie in (self.brand, self.model, self.year) if partie
        )
        parties = [f"n°{self.number}", description, self.vin]
        return " · ".join(partie for partie in parties if partie)

    @property
    def expenses_locked(self) -> bool:
        """Dépenses verrouillées : véhicule vendu et fiche non rouverte (D22)."""
        return self.status == self.Status.SOLD and self.reopened_at is None


class StatusChange(TimeStampedModel):
    """Un changement de statut d'un véhicule (frise et durées)."""

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="status_changes", verbose_name="véhicule"
    )
    from_status = models.CharField(
        "ancien statut", max_length=20, choices=Vehicle.Status.choices, blank=True
    )
    to_status = models.CharField("nouveau statut", max_length=20, choices=Vehicle.Status.choices)
    changed_at = models.DateTimeField("changé le", default=timezone.now)
    changed_by = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="changé par",
    )

    class Meta:
        verbose_name = "changement de statut"
        verbose_name_plural = "changements de statut"
        ordering = ["changed_at", "id"]

    def __str__(self) -> str:
        return f"{self.get_from_status_display() or '—'} → {self.get_to_status_display()}"


class WorkLog(SoftDeleteModel):
    """Une intervention sur un véhicule (journal des travaux)."""

    class Kind(models.TextChoices):
        REPAIR = "REPAIR", "Réparation"
        MAINTENANCE = "MAINTENANCE", "Entretien"
        UPGRADE = "UPGRADE", "Amélioration"

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="work_logs", verbose_name="véhicule"
    )
    date = models.DateField("date", default=timezone.localdate)
    description = models.TextField("description")
    kind = models.CharField("type", max_length=20, choices=Kind.choices, default=Kind.REPAIR)
    done_by = models.ForeignKey(
        "core.Partner",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="fait par",
        help_text="Vide : travail sous-traité.",
    )
    hours = models.DecimalField(
        "heures", max_digits=5, decimal_places=2, null=True, blank=True, help_text="Pour mémoire."
    )

    class Meta:
        verbose_name = "intervention"
        verbose_name_plural = "interventions"
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} du {self.date:%d/%m/%Y}"


class Task(SoftDeleteModel):
    """Une chose à faire sur un véhicule."""

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="tasks", verbose_name="véhicule"
    )
    label = models.CharField("à faire", max_length=200)
    is_done = models.BooleanField("fait", default=False)
    done_at = models.DateField("fait le", null=True, blank=True)
    order = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "chose à faire"
        verbose_name_plural = "choses à faire"
        ordering = ["is_done", "order", "id"]

    def __str__(self) -> str:
        return self.label


class ComparableListing(SoftDeleteModel):
    """Une annonce comparable, saisie à la main pour fixer le prix (étape 2)."""

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="comparable_listings",
        verbose_name="véhicule",
    )
    url = models.URLField("annonce", max_length=500, blank=True)
    price = models.DecimalField("prix", **MONEY)
    year = models.PositiveSmallIntegerField("année", null=True, blank=True)
    mileage_km = models.PositiveIntegerField("kilométrage", null=True, blank=True)
    notes = models.TextField("notes", blank=True)
    noted_on = models.DateField("relevée le", default=timezone.localdate)

    class Meta:
        verbose_name = "annonce comparable"
        verbose_name_plural = "annonces comparables"
        ordering = ["price"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0), name="comparable_listing_price_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"Annonce à {self.price} €"

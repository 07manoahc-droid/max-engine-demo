"""Modèles communs du garage : briques de base, associés, paramètres, listes, contacts."""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Ajoute les dates de création et de modification (toutes les tables métier)."""

    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        abstract = True


class NotDeletedManager(models.Manager):
    """Gestionnaire par défaut : cache les éléments mis à la corbeille."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(TimeStampedModel):
    """Suppression logique (règle 6) : un élément supprimé part à la corbeille.

    `objects` exclut les éléments supprimés, `all_objects` les inclut tous.
    Ne jamais appeler `.delete()` sur une donnée métier : utiliser `soft_delete()`.
    """

    deleted_at = models.DateTimeField("supprimé le", null=True, blank=True)

    # L'ordre compte : le premier gestionnaire déclaré est celui par défaut. Ruff prend
    # `all_objects` pour un champ, d'où l'exception DJ012.
    objects = NotDeletedManager()
    all_objects = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Met l'élément à la corbeille."""
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self) -> None:
        """Sort l'élément de la corbeille."""
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])


class Partner(models.Model):
    """Un associé : celui qui a travaillé, payé ou encaissé.

    Le logiciel suppose exactement deux associés actifs (partage à parts égales).
    Les deux associés sont créés par une migration de données.
    """

    name = models.CharField("nom", max_length=50, unique=True)
    color = models.CharField(
        "couleur", max_length=20, help_text="Clé de couleur de la charte (indigo, teal)."
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "associé"
        verbose_name_plural = "associés"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name


class GarageSettings(TimeStampedModel):
    """Paramètres du garage : une seule ligne (clé 1), créée par migration."""

    garage_name = models.CharField("nom du garage", max_length=80, default="MAx Engine")
    backup_dir = models.CharField(
        "dossier des sauvegardes",
        max_length=255,
        blank=True,
        help_text="Dossier OneDrive où sont copiées les sauvegardes.",
    )
    stale_sale_days = models.PositiveIntegerField(
        "alerte « en vente depuis trop longtemps » (jours)", default=60
    )

    class Meta:
        verbose_name = "paramètres"
        verbose_name_plural = "paramètres"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="garage_settings_single_row"),
        ]

    def __str__(self) -> str:
        return "Paramètres du garage"

    @classmethod
    def load(cls) -> "GarageSettings":
        """Retourne la ligne des paramètres (la crée si elle manque)."""
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class EditableListModel(TimeStampedModel):
    """Liste modifiable par les associés. Une valeur utilisée n'est jamais supprimée,
    seulement désactivée."""

    name = models.CharField("nom", max_length=50, unique=True)
    order = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("active", default=True)

    class Meta:
        abstract = True
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class VehicleType(EditableListModel):
    class Meta(EditableListModel.Meta):
        verbose_name = "type de véhicule"
        verbose_name_plural = "types de véhicule"


class PurchaseOrigin(EditableListModel):
    class Meta(EditableListModel.Meta):
        verbose_name = "origine d'achat"
        verbose_name_plural = "origines d'achat"


class ExpenseCategory(EditableListModel):
    """Catégorie de dépense. Le code est stable et utilisé par le programme.

    Une catégorie système (« Achat véhicule ») ne peut ni changer de code ni être désactivée.
    """

    VEHICLE_PURCHASE = "vehicle_purchase"
    # Seules les lignes de cette catégorie ont un statut de pièce (À commander, Commandé…).
    PARTS = "parts"

    code = models.CharField("code", max_length=30, unique=True)
    is_system = models.BooleanField("catégorie système", default=False)

    class Meta(EditableListModel.Meta):
        verbose_name = "catégorie de dépense"
        verbose_name_plural = "catégories de dépense"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(is_system=True, is_active=False),
                name="expense_category_system_stays_active",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and self.is_system:
            ancien_code = (
                ExpenseCategory.objects.filter(pk=self.pk).values_list("code", flat=True).first()
            )
            if ancien_code is not None and ancien_code != self.code:
                raise ValidationError("Le code d'une catégorie système ne peut pas changer.")
        super().save(*args, **kwargs)


class Supplier(TimeStampedModel):
    """Fournisseur ou sous-traitant, proposé en autocomplétion."""

    name = models.CharField("nom", max_length=100)
    website = models.URLField("site internet", max_length=300, blank=True)
    notes = models.TextField("notes", blank=True)

    class Meta:
        verbose_name = "fournisseur"
        verbose_name_plural = "fournisseurs"
        ordering = [Lower("name")]
        constraints = [
            # « LEBONCOIN » et « Leboncoin » sont le même fournisseur.
            models.UniqueConstraint(
                Lower("name"),
                name="supplier_name_unique_ignoring_case",
                violation_error_message="Ce fournisseur existe déjà.",
            ),
        ]

    def __str__(self) -> str:
        return self.name


def attachment_upload_to(attachment: "Attachment", filename: str) -> str:
    """`vehicules/<numéro>/` pour un fichier lié à un véhicule, sinon `garage/`."""
    vehicule = attachment.vehicle or (attachment.work_log.vehicle if attachment.work_log else None)
    if vehicule is not None:
        return f"vehicules/{vehicule.number}/{filename}"
    return f"garage/{filename}"


class Attachment(SoftDeleteModel):
    """Fichier joint : photo, facture ou document, rattaché à au moins un élément."""

    class Kind(models.TextChoices):
        PHOTO = "PHOTO", "Photo"
        INVOICE = "INVOICE", "Facture"
        DOCUMENT = "DOCUMENT", "Document"

    class Stage(models.TextChoices):
        LISTING = "LISTING", "Annonce"
        BEFORE = "BEFORE", "Avant"
        DURING = "DURING", "Pendant"
        AFTER = "AFTER", "Après"

    file = models.FileField("fichier", upload_to=attachment_upload_to, max_length=255)
    kind = models.CharField("type", max_length=20, choices=Kind.choices, default=Kind.PHOTO)
    stage = models.CharField("étape", max_length=20, choices=Stage.choices, blank=True)
    caption = models.CharField("légende", max_length=200, blank=True)
    show_to_buyer = models.BooleanField("à montrer à l'acheteur", default=False)
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name="véhicule",
    )
    expense = models.ForeignKey(
        "expenses.Expense",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name="dépense",
    )
    work_log = models.ForeignKey(
        "vehicles.WorkLog",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name="intervention",
    )
    order = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "fichier joint"
        verbose_name_plural = "fichiers joints"
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(vehicle__isnull=False)
                | models.Q(expense__isnull=False)
                | models.Q(work_log__isnull=False),
                name="attachment_linked_to_something",
                violation_error_message="Un fichier joint doit être rattaché à quelque chose.",
            ),
        ]

    def __str__(self) -> str:
        return self.caption or self.file.name

    @property
    def file_name(self) -> str:
        """Nom du fichier, sans son dossier (« facture-12.pdf »)."""
        return Path(self.file.name).name


class Contact(SoftDeleteModel):
    """Vendeur ou client. Une même personne peut être les deux."""

    name = models.CharField("nom", max_length=100)
    phone = models.CharField("téléphone", max_length=30, blank=True)
    email = models.EmailField("e-mail", blank=True)
    city = models.CharField("ville", max_length=80, blank=True)
    leboncoin_profile = models.URLField("profil Leboncoin", max_length=300, blank=True)
    notes = models.TextField("notes", blank=True)

    class Meta:
        verbose_name = "contact"
        verbose_name_plural = "contacts"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

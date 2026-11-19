"""Administration de secours de l'application core.

L'administration sert seulement à contrôler les données à la main : rien n'y est supprimé
pour de bon (règle 6), sur aucun modèle.
"""

from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Attachment,
    Contact,
    ExpenseCategory,
    GarageSettings,
    Partner,
    PurchaseOrigin,
    Supplier,
    VehicleType,
)


class NoDeleteAdminMixin:
    """Retire la suppression (bouton et action groupée) : on désactive ou on met à la corbeille."""

    def has_delete_permission(self, request, obj=None):
        return False


class NoDeleteAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    pass


class NoDeleteHistoryAdmin(NoDeleteAdminMixin, SimpleHistoryAdmin):
    """Pour les modèles avec historique : l'onglet « Historique » montre chaque version."""


@admin.register(Partner)
class PartnerAdmin(NoDeleteAdmin):
    list_display = ("name", "color", "is_active")


@admin.register(GarageSettings)
class GarageSettingsAdmin(NoDeleteAdmin):
    list_display = ("garage_name", "backup_dir", "stale_sale_days")

    def has_add_permission(self, request):
        """Une seule ligne de paramètres, créée par migration."""
        return not GarageSettings.objects.exists()


@admin.register(VehicleType, PurchaseOrigin)
class EditableListAdmin(NoDeleteAdmin):
    list_display = ("name", "order", "is_active")
    list_editable = ("order", "is_active")


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(NoDeleteAdmin):
    list_display = ("name", "code", "is_system", "order", "is_active")


@admin.register(Supplier)
class SupplierAdmin(NoDeleteAdmin):
    list_display = ("name", "website")
    search_fields = ("name",)


@admin.register(Contact)
class ContactAdmin(NoDeleteAdmin):
    list_display = ("name", "phone", "city")
    search_fields = ("name", "phone", "email")


@admin.register(Attachment)
class AttachmentAdmin(NoDeleteAdmin):
    list_display = ("__str__", "kind", "stage", "vehicle", "expense")
    list_filter = ("kind",)

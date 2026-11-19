"""Administration de secours de l'application vehicles."""

from django.contrib import admin

from core.admin import NoDeleteAdmin, NoDeleteHistoryAdmin

from .models import ComparableListing, StatusChange, Task, Vehicle, WorkLog


@admin.register(Vehicle)
class VehicleAdmin(NoDeleteHistoryAdmin):
    list_display = ("number", "brand", "model", "year", "status", "purchased_on")
    list_filter = ("status", "vehicle_type")
    search_fields = ("brand", "model", "vin", "registration")


@admin.register(StatusChange)
class StatusChangeAdmin(NoDeleteAdmin):
    list_display = ("vehicle", "from_status", "to_status", "changed_at", "changed_by")


@admin.register(WorkLog)
class WorkLogAdmin(NoDeleteAdmin):
    list_display = ("vehicle", "date", "kind", "done_by")


@admin.register(Task)
class TaskAdmin(NoDeleteAdmin):
    list_display = ("vehicle", "label", "is_done")


@admin.register(ComparableListing)
class ComparableListingAdmin(NoDeleteAdmin):
    list_display = ("vehicle", "price", "year", "mileage_km", "noted_on")

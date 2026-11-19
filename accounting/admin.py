"""Administration de secours de l'application accounting.

Les règlements y sont en lecture seule : ils se créent et s'annulent depuis l'écran
des comptes (1.6), jamais à la main.
"""

from django.contrib import admin

from core.admin import NoDeleteHistoryAdmin

from .models import Settlement


@admin.register(Settlement)
class SettlementAdmin(NoDeleteHistoryAdmin):
    list_display = ("date", "from_partner", "to_partner", "amount", "cancels")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

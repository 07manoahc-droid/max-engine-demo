"""Administration de secours de l'application sales."""

from django.contrib import admin

from core.admin import NoDeleteHistoryAdmin

from .models import Deposit, DepositReceipt, Sale, SaleReceipt


@admin.register(Sale)
class SaleAdmin(NoDeleteHistoryAdmin):
    list_display = ("vehicle", "date", "price", "trade_in_value", "cancelled_at")


@admin.register(SaleReceipt)
class SaleReceiptAdmin(NoDeleteHistoryAdmin):
    list_display = ("sale", "partner", "amount", "date")


@admin.register(Deposit)
class DepositAdmin(NoDeleteHistoryAdmin):
    list_display = ("vehicle", "date", "status", "buyer")
    list_filter = ("status",)
    # Vides depuis la 2.0.0 (D89) : l'argent est dans les versements.
    readonly_fields = ("legacy_amount", "legacy_received_by")


@admin.register(DepositReceipt)
class DepositReceiptAdmin(NoDeleteHistoryAdmin):
    list_display = ("deposit", "partner", "amount", "date")

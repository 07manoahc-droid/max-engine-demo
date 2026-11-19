"""Administration de secours de l'application expenses."""

from django.contrib import admin

from core.admin import NoDeleteHistoryAdmin

from .models import Expense, ExpenseLine, ExpensePayment, PartTransfer, Refund


@admin.register(Expense)
class ExpenseAdmin(NoDeleteHistoryAdmin):
    list_display = ("date", "supplier", "order_number", "total_paid", "is_personal")
    list_filter = ("is_personal",)
    search_fields = ("order_number", "supplier__name")


@admin.register(ExpensePayment)
class ExpensePaymentAdmin(NoDeleteHistoryAdmin):
    list_display = ("expense", "partner", "amount")


@admin.register(ExpenseLine)
class ExpenseLineAdmin(NoDeleteHistoryAdmin):
    list_display = ("label", "vehicle", "category", "quantity", "unit_price", "status")
    list_filter = ("category", "status")
    search_fields = ("label", "reference")


@admin.register(Refund)
class RefundAdmin(NoDeleteHistoryAdmin):
    list_display = ("line", "date", "amount", "refunded_to")


@admin.register(PartTransfer)
class PartTransferAdmin(NoDeleteHistoryAdmin):
    list_display = ("line", "from_vehicle", "to_vehicle", "date")

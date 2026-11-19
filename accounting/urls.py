"""Adresses des comptes."""

from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("bilan/", views.report, name="report"),
    path("bilan/export/", views.report_export, name="report_export"),
    path("comptes/", views.accounts, name="accounts"),
    path("comptes/depenses-du-garage/", views.garage_expenses, name="garage_expenses"),
    path("comptes/reglements/ajouter/", views.settlement_create, name="settlement_create"),
    path("comptes/reglements/apercu/", views.settlement_preview, name="settlement_preview"),
    path(
        "comptes/reglements/<int:pk>/annuler/",
        views.settlement_cancel,
        name="settlement_cancel",
    ),
]

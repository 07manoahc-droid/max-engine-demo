"""Adresses des ventes."""

from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("vehicules/<int:number>/acompte/", views.deposit_create, name="deposit_create"),
    path("acomptes/<int:pk>/corriger/", views.deposit_edit, name="deposit_edit"),
    path("acomptes/<int:pk>/annuler/", views.deposit_cancel, name="deposit_cancel"),
    path(
        "acomptes/<int:pk>/versements/ajouter/",
        views.deposit_receipts_add,
        name="deposit_receipts_add",
    ),
    path(
        "acomptes/versements/<int:pk>/corriger/",
        views.deposit_receipt_edit,
        name="deposit_receipt_edit",
    ),
    path(
        "acomptes/versements/<int:pk>/retirer/",
        views.deposit_receipt_remove,
        name="deposit_receipt_remove",
    ),
    path("vehicules/<int:number>/vente/", views.sale_create, name="sale_create"),
    path("vehicules/<int:number>/vente/apercu/", views.sale_preview_view, name="sale_preview"),
    path("ventes/<int:pk>/modifier/", views.sale_edit, name="sale_edit"),
    path("ventes/<int:pk>/annuler/", views.sale_cancel, name="sale_cancel"),
]

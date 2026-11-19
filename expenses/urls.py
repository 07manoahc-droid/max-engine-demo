"""Adresses de l'application expenses."""

from django.urls import path

from . import views

app_name = "expenses"

urlpatterns = [
    path("depenses/ajouter/", views.expense_create, name="create"),
    path("depenses/apercu/", views.expense_preview_view, name="preview"),
    path("depenses/<int:pk>/", views.expense_detail, name="detail"),
    path("depenses/<int:pk>/modifier/", views.expense_edit, name="edit"),
    path("depenses/<int:pk>/corbeille/", views.expense_trash, name="trash"),
    path(
        "depenses/<int:pk>/factures/<int:invoice_id>/corbeille/",
        views.invoice_delete,
        name="invoice_delete",
    ),
    path("depenses/lignes/<int:pk>/statut/", views.line_status_change, name="line_status"),
    path("depenses/lignes/<int:pk>/retour/", views.refund_create, name="refund_create"),
    path("depenses/lignes/<int:pk>/transferer/", views.line_transfer, name="line_transfer"),
    path("depenses/retours/<int:pk>/modifier/", views.refund_edit, name="refund_edit"),
    path("depenses/retours/<int:pk>/corbeille/", views.refund_delete, name="refund_delete"),
    path("pieces-a-commander/", views.parts_to_order, name="parts_to_order"),
    path("pieces-a-commander/ajouter/", views.part_to_order_create, name="part_create"),
    path("pieces-a-commander/<int:pk>/modifier/", views.part_to_order_edit, name="part_edit"),
    path(
        "pieces-a-commander/<int:pk>/corbeille/",
        views.part_to_order_delete,
        name="part_delete",
    ),
]

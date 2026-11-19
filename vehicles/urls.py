"""Adresses de l'application vehicles. Un véhicule est désigné par son numéro interne."""

from django.urls import path

from . import views

app_name = "vehicles"

urlpatterns = [
    path("vehicules/", views.vehicle_list, name="list"),
    path("vendus/", views.sold_list, name="sold"),
    path("abandonnes/", views.abandoned_list, name="abandoned"),
    path("vehicules/ajouter/", views.vehicle_create, name="create"),
    path("vehicules/<int:number>/", views.vehicle_detail, name="detail"),
    path("vehicules/<int:number>/modifier/", views.vehicle_edit, name="edit"),
    path("vehicules/<int:number>/statut/", views.vehicle_status, name="status"),
    path("vehicules/<int:number>/acheter/", views.vehicle_purchase, name="purchase"),
    path("vehicules/<int:number>/rouvrir/", views.vehicle_reopen, name="reopen"),
    path("vehicules/<int:number>/verrouiller/", views.vehicle_lock, name="lock"),
    path("vehicules/<int:number>/corbeille/", views.vehicle_trash, name="trash"),
    path("vehicules/<int:number>/photos/", views.vehicle_photos_add, name="photos_add"),
    path(
        "vehicules/<int:number>/photos/<int:photo_id>/corbeille/",
        views.photo_delete,
        name="photo_delete",
    ),
]

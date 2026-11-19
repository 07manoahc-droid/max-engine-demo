"""Adresses de l'application core."""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("sante/", views.health, name="health"),
    path("qui-es-tu/", views.choose_partner, name="choose_partner"),
    path("corbeille/", views.trash_list, name="trash"),
    path("corbeille/<str:kind>/<int:pk>/restaurer/", views.trash_restore, name="trash_restore"),
    path("journal/", views.journal, name="journal"),
    path("consultation/", views.read_only_refused, name="read_only"),
    path("photos/<int:pk>/miniature/", views.photo_thumbnail, name="photo_thumbnail"),
    path("contacts/", views.contact_list, name="contacts"),
    path("contacts/ajouter/", views.contact_create, name="contact_create"),
    path("contacts/<int:pk>/", views.contact_detail, name="contact_detail"),
    path("contacts/<int:pk>/modifier/", views.contact_edit, name="contact_edit"),
    path("contacts/<int:pk>/corbeille/", views.contact_trash, name="contact_trash"),
]

"""Les commentaires de gabarit ne s'affichent jamais à l'écran.

Un commentaire `{# … #}` ne tient que sur une ligne : écrit sur plusieurs, Django l'affiche
tel quel dans la page. Sur plusieurs lignes, on écrit `{% comment %}…{% endcomment %}`.
"""

from pathlib import Path

import pytest
from django.conf import settings

from core.models import Contact

TEMPLATES = Path(settings.BASE_DIR) / "templates"


def test_no_template_comment_spans_several_lines():
    """Chaque `{#` est refermé par `#}` sur la même ligne, dans tous les gabarits (même
    ceux qu'aucun test n'affiche)."""
    fautifs = []
    for gabarit in sorted(TEMPLATES.rglob("*.html")):
        lignes = gabarit.read_text(encoding="utf-8").splitlines()
        for numero, ligne in enumerate(lignes, start=1):
            debut = ligne.find("{#")
            while debut != -1:
                fin = ligne.find("#}", debut + 2)
                if fin == -1:
                    fautifs.append(f"{gabarit.relative_to(TEMPLATES)}:{numero}")
                    break
                debut = ligne.find("{#", fin + 2)
    assert fautifs == [], "Commentaire {# … #} sur plusieurs lignes : " + ", ".join(fautifs)


def assert_no_comment_shown(reponse, adresse: str) -> None:
    assert reponse.status_code == 200, adresse
    page = reponse.content.decode()
    assert "{#" not in page, f"« {{# » affiché sur {adresse}"
    assert "#}" not in page, f"« #}} » affiché sur {adresse}"


@pytest.mark.django_db
def test_rendered_pages_show_no_template_comment(as_mathis, make_vehicle):
    """Les pages principales, la recherche HTMX et les messages (toast) ne montrent aucun
    `{#` : ni la page Contacts, ni la fiche d'un contact après une confirmation."""
    paul = Contact.objects.create(name="Paul Durand", city="Lyon", phone="06 12 34 56 78")
    vehicule = make_vehicle(seller=paul)
    adresses = [
        "/",
        "/vehicules/",
        f"/vehicules/{vehicule.number}/",
        "/vendus/",
        "/pieces-a-commander/",
        "/depenses/ajouter/",
        "/comptes/",
        "/bilan/",
        "/corbeille/",
        "/journal/",
        "/contacts/",
        "/contacts/?q=paul",
        "/contacts/ajouter/",
        f"/contacts/{paul.pk}/",
        f"/contacts/{paul.pk}/modifier/",
    ]
    for adresse in adresses:
        assert_no_comment_shown(as_mathis.get(adresse), adresse)

    # La recherche pendant la frappe (fragment HTMX).
    fragment = as_mathis.get("/contacts/?q=lyon", HTTP_HX_REQUEST="true")
    assert_no_comment_shown(fragment, "recherche HTMX")

    # Un message de confirmation, puis un avertissement (même nom), dans leur toast.
    for nom in ("Jean Martin", "Jean Martin"):
        reponse = as_mathis.post("/contacts/ajouter/", {"name": nom}, follow=True)
        assert_no_comment_shown(reponse, f"ajout de {nom}")
        assert len(list(reponse.context["messages"])) >= 1

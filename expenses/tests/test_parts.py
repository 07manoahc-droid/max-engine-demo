"""Tests des pièces à commander et du statut des pièces : règles et écrans."""

import datetime as dt
from decimal import Decimal as D

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError

from accounting import queries
from accounting.services import Payment
from core.models import ExpenseCategory
from expenses import services
from expenses.models import ExpenseLine
from expenses.services import LineEntry

Status = ExpenseLine.Status


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def moto(make_vehicle):
    return make_vehicle(brand="GG", model="MC250", status="IN_REPAIR")


@pytest.fixture
def disque(moto):
    return services.add_part_to_order(
        label="Disque avant", reference="DF-12", unit_price=D("60.00"), vehicle=moto
    )


@pytest.fixture
def filtre(moto):
    return services.add_part_to_order(label="Filtre à huile", vehicle=moto)


def order(lines, payments, total="86.00"):
    return services.save_expense(
        None,
        date=dt.date(2026, 10, 20),
        supplier_name="Moto Axxe",
        order_number="",
        total_paid=D(total),
        is_personal=False,
        notes="",
        lines=lines,
        payments=payments,
    )


def entry(line, price):
    """La ligne du formulaire qui reprend une pièce à commander, au prix réel."""
    return LineEntry(
        label=line.label,
        category=line.category,
        unit_price=D(price),
        quantity=line.quantity,
        vehicle=line.vehicle,
        status=Status.ORDERED,
        pk=line.pk,
    )


# --- Règles ------------------------------------------------------------------------


def test_part_to_order_has_no_payment_and_counts_nowhere(disque, moto):
    assert disque.expense is None
    assert disque.status == Status.TO_ORDER
    assert disque.category.code == ExpenseCategory.PARTS
    assert list(services.parts_to_order()) == [disque]
    assert queries.vehicle_cost(moto.pk).total == D("0")


def test_ordering_attaches_the_parts_to_the_payment(disque, filtre, moto, mathis):
    """La commande rattache les pièces au paiement : elles passent en « Commandé » et
    comptent dans le coût du véhicule, frais de port compris."""
    depense = order([entry(disque, "71.90"), entry(filtre, "8.40")], [Payment(mathis, D("86.00"))])
    disque.refresh_from_db()
    filtre.refresh_from_db()
    assert disque.expense == depense and filtre.expense == depense
    assert disque.status == filtre.status == Status.ORDERED
    assert disque.unit_price == D("71.90")
    assert not services.parts_to_order().exists()
    assert queries.vehicle_cost(moto.pk).total == D("86.00")


def test_a_part_cannot_be_ordered_twice(disque, mathis):
    order([entry(disque, "86.00")], [Payment(mathis, D("86.00"))])
    with pytest.raises(ValidationError, match="déjà été commandée"):
        order([entry(disque, "86.00")], [Payment(mathis, D("86.00"))])


def test_an_ordered_part_is_no_longer_editable_as_to_order(disque, mathis):
    order([entry(disque, "86.00")], [Payment(mathis, D("86.00"))])
    disque.refresh_from_db()
    with pytest.raises(ValidationError, match="déjà été commandée"):
        services.remove_part_to_order(disque)
    with pytest.raises(ValidationError, match="déjà été commandée"):
        services.update_part_to_order(
            disque, label="X", reference="", quantity=D("1"), unit_price=D("0"), vehicle=None
        )


def test_part_to_order_is_edited_and_binned(disque):
    services.update_part_to_order(
        disque,
        label="Disque arrière",
        reference="DR-3",
        quantity=D("2"),
        unit_price=D("0"),
        vehicle=None,
    )
    disque.refresh_from_db()
    assert (disque.label, disque.quantity, disque.vehicle) == ("Disque arrière", D("2"), None)
    services.remove_part_to_order(disque)
    assert not services.parts_to_order().exists()
    assert ExpenseLine.all_objects.get(pk=disque.pk).is_deleted


@pytest.fixture
def ordered(disque, mathis):
    order([entry(disque, "86.00")], [Payment(mathis, D("86.00"))])
    disque.refresh_from_db()
    return disque


@pytest.mark.parametrize("statut", [Status.RECEIVED, Status.FITTED, Status.ORDERED])
def test_paid_part_status_moves_freely(ordered, statut):
    services.change_part_status(ordered, statut)
    ordered.refresh_from_db()
    assert ordered.status == statut


def test_fitted_part_can_go_back_to_received(ordered):
    services.change_part_status(ordered, Status.FITTED)
    services.change_part_status(ordered, Status.RECEIVED)
    ordered.refresh_from_db()
    assert ordered.status == Status.RECEIVED


@pytest.mark.parametrize("statut", [Status.TO_ORDER, Status.RETURNED, "", "INCONNU"])
def test_to_order_and_returned_are_not_chosen_by_hand(ordered, statut):
    with pytest.raises(ValidationError):
        services.change_part_status(ordered, statut)


def test_status_of_an_unpaid_or_returned_part_or_of_a_non_part_is_refused(disque, ordered, mathis):
    autre = services.add_part_to_order(label="Joint")
    with pytest.raises(ValidationError, match="pas encore payée"):
        services.change_part_status(autre, Status.RECEIVED)

    ordered.status = Status.RETURNED
    ordered.save()
    with pytest.raises(ValidationError, match="retournée"):
        services.change_part_status(ordered, Status.FITTED)

    outillage = ExpenseCategory.objects.get(code="tooling")
    depense = order(
        [LineEntry("Clé", outillage, D("10.00"))], [Payment(mathis, D("10.00"))], total="10.00"
    )
    with pytest.raises(ValidationError, match="Seules les pièces"):
        services.change_part_status(depense.lines.get(), Status.RECEIVED)


def test_line_rows_offer_the_next_step(ordered):
    (rang,) = services.line_rows([services.paid_lines().get(pk=ordered.pk)])
    assert (rang["next_status"], rang["next_label"]) == (Status.RECEIVED, "Marquer reçue")
    assert rang["cost"] == D("86.00")
    services.change_part_status(ordered, Status.FITTED)
    (rang,) = services.line_rows([services.paid_lines().get(pk=ordered.pk)])
    assert rang["next_status"] is None


# --- Écrans ------------------------------------------------------------------------


def test_list_groups_parts_by_vehicle_with_garage_last(as_mathis, disque, moto):
    services.add_part_to_order(label="Graisse")
    response = as_mathis.get("/pieces-a-commander/")
    assert response.status_code == 200
    groupes = response.context["groups"]
    assert [v for v, _ in groupes] == [moto, None]
    page = response.content.decode()
    assert "Disque avant" in page and "Graisse" in page
    assert "Enregistrer la commande" in page
    assert "/depenses/ajouter/" in page  # « Ajouter une dépense » sans véhicule


def test_empty_list_invites_to_add(as_mathis, db):
    page = as_mathis.get("/pieces-a-commander/").content.decode()
    assert "Aucune pièce à commander. Ajoute la première" in page


def test_sidebar_shows_the_count(as_mathis, disque, filtre):
    page = as_mathis.get("/").content.decode()
    assert "À commander" in page
    assert '2<span class="sr-only"> pièces à commander</span>' in page


def test_sidebar_hides_the_count_when_nothing_to_order(as_mathis, db):
    assert "à commander</span>" not in as_mathis.get("/").content.decode()


def parts_data(*lignes: dict) -> dict:
    """Les données du formulaire « Ajouter des pièces à commander » (préfixe `lignes`)."""
    donnees = {
        "lignes-TOTAL_FORMS": str(len(lignes)),
        "lignes-INITIAL_FORMS": "0",
        "lignes-MIN_NUM_FORMS": "0",
        "lignes-MAX_NUM_FORMS": "1000",
    }
    for i, ligne in enumerate(lignes):
        valeurs = {"label": "", "reference": "", "quantity": "1", "unit_price": "", "vehicle": ""}
        valeurs |= ligne
        donnees |= {f"lignes-{i}-{nom}": valeur for nom, valeur in valeurs.items()}
    return donnees


def test_add_part_from_the_vehicle_sheet(as_mathis, moto):
    adresse = f"/pieces-a-commander/ajouter/?vehicule={moto.number}&depuis=fiche"
    response = as_mathis.get(adresse)
    assert response.status_code == 200
    (ligne,) = response.context["formset"].forms
    assert ligne["vehicle"].value() == moto.pk
    page = response.content.decode()
    assert "Ajouter une ligne" in page and "Enregistrer les pièces" in page

    response = as_mathis.post(adresse, parts_data({"label": "Chaîne", "vehicle": moto.pk}))
    assert response.status_code == 302
    assert response.url == moto.get_absolute_url()
    assert "« Chaîne » est à commander." in messages_of(response)
    piece = services.parts_to_order().get()
    assert (piece.label, piece.unit_price, piece.vehicle) == ("Chaîne", D("0"), moto)


def test_add_several_parts_at_once_ignoring_empty_and_removed_lines(as_mathis, moto):
    """Une ligne vide (même avec le véhicule repris de la précédente) est ignorée, une
    ligne retirée n'est pas enregistrée."""
    response = as_mathis.post(
        "/pieces-a-commander/ajouter/",
        parts_data(
            {"label": "Chaîne", "reference": "520-118", "vehicle": moto.pk},
            {"label": "Couronne", "quantity": "2", "unit_price": "35,50", "vehicle": moto.pk},
            {"vehicle": moto.pk},
            {"label": "Pignon", "DELETE": "on"},
            {"label": "Graisse"},
        ),
    )
    assert response.status_code == 302
    assert "3 pièces sont à commander." in messages_of(response)
    pieces = list(services.parts_to_order().order_by("id"))
    assert [(p.label, p.reference, p.quantity, p.unit_price, p.vehicle) for p in pieces] == [
        ("Chaîne", "520-118", D("1"), D("0"), moto),
        ("Couronne", "", D("2"), D("35.50"), moto),
        ("Graisse", "", D("1"), D("0"), None),
    ]


def test_add_parts_needs_one_line_and_shows_errors_on_each_line(as_mathis, moto):
    response = as_mathis.post("/pieces-a-commander/ajouter/", parts_data({}, {"vehicle": moto.pk}))
    assert response.status_code == 200
    assert response.context["formset"].non_form_errors() == ["Indique au moins une pièce."]

    response = as_mathis.post(
        "/pieces-a-commander/ajouter/",
        parts_data(
            {"label": "Chaîne"},
            {"label": "", "unit_price": "12"},
            {"label": "Joint", "quantity": "0"},
        ),
    )
    assert response.status_code == 200
    formes = response.context["formset"].forms
    assert formes[0].errors == {}
    assert "label" in formes[1].errors
    assert "quantity" in formes[2].errors
    assert not services.parts_to_order().exists()  # tout ou rien


def test_add_parts_is_all_or_nothing(db, monkeypatch):
    """Une erreur pendant l'enregistrement de la 2e pièce annule aussi la 1re."""
    original = services.add_part_to_order

    def echoue_au_deuxieme(**piece):
        if piece["label"] == "Couronne":
            raise RuntimeError("panne")
        return original(**piece)

    monkeypatch.setattr(services, "add_part_to_order", echoue_au_deuxieme)
    with pytest.raises(RuntimeError):
        services.add_parts_to_order([{"label": "Chaîne"}, {"label": "Couronne"}])
    assert not ExpenseLine.all_objects.exists()


def test_reference_is_never_prefilled(as_mathis, moto, disque):
    """La référence ne reprend jamais la désignation, et le navigateur n'y propose rien."""
    for adresse in (
        f"/pieces-a-commander/ajouter/?vehicule={moto.number}",
        f"/depenses/ajouter/?vehicule={moto.number}",
    ):
        formset = as_mathis.get(adresse).context["formset"]
        for forme in [*formset.forms, formset.empty_form]:
            assert not forme["reference"].value()
            assert 'autocomplete="off"' in str(forme["reference"])
    formset = as_mathis.get(f"/depenses/ajouter/?pieces={disque.pk}").context["formset"]
    (forme,) = formset.forms
    assert (forme["label"].value(), forme["reference"].value()) == ("Disque avant", "DF-12")


def champ(page: str, nom: str) -> str:
    """La balise du premier champ dont le nom commence par `nom`."""
    return page[page.index(f'name="{nom}') :].split(">", 1)[0]


def test_money_fields_have_no_browser_suggestions(as_mathis, moto):
    page = as_mathis.get(f"/depenses/ajouter/?vehicule={moto.number}").content.decode()
    for nom in ("total_paid", "lignes-0-unit_price", "amount_"):
        assert 'autocomplete="off"' in champ(page, nom), nom
    page = as_mathis.get("/pieces-a-commander/ajouter/").content.decode()
    assert 'autocomplete="off"' in champ(page, "lignes-0-unit_price")


def test_edit_and_bin_part_from_the_list(as_mathis, disque):
    response = as_mathis.post(
        f"/pieces-a-commander/{disque.pk}/modifier/",
        {
            "label": "Disque arrière",
            "reference": "",
            "quantity": "1",
            "unit_price": "55",
            "vehicle": "",
        },
    )
    assert response.status_code == 302
    assert response.url == "/pieces-a-commander/"
    disque.refresh_from_db()
    assert disque.label == "Disque arrière"

    response = as_mathis.post(f"/pieces-a-commander/{disque.pk}/corbeille/")
    assert response.status_code == 302
    assert "est dans la corbeille" in messages_of(response)[-1]
    assert not services.parts_to_order().exists()


def test_vehicle_sheet_lists_parts_to_order(as_mathis, disque, moto):
    page = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert "Disque avant" in page
    assert "À commander" in page
    assert f"pieces={disque.pk}" in page


def test_order_form_is_prefilled_with_the_checked_parts(as_mathis, disque, filtre):
    response = as_mathis.get(f"/depenses/ajouter/?pieces={disque.pk}&pieces={filtre.pk}")
    assert response.status_code == 200
    formes = response.context["formset"].forms
    assert [f["line_id"].value() for f in formes] == [disque.pk, filtre.pk]
    assert [f["status"].value() for f in formes] == [Status.ORDERED, Status.ORDERED]
    assert "Commande des pièces cochées" in response.content.decode()


def test_order_form_post_orders_the_parts(as_mathis, disque, filtre, moto, parts_category, mathis):
    donnees = {
        "date": "2026-10-20",
        "supplier_name": "Moto Axxe",
        "order_number": "",
        "total_paid": "86,00",
        "payer": str(mathis.pk),
        "notes": "",
        "lignes-TOTAL_FORMS": "2",
        "lignes-INITIAL_FORMS": "2",
        "lignes-MIN_NUM_FORMS": "0",
        "lignes-MAX_NUM_FORMS": "1000",
    }
    for i, (piece, prix) in enumerate([(disque, "71,90"), (filtre, "8,40")]):
        donnees |= {
            f"lignes-{i}-line_id": piece.pk,
            f"lignes-{i}-label": piece.label,
            f"lignes-{i}-reference": "",
            f"lignes-{i}-quantity": "1",
            f"lignes-{i}-unit_price": prix,
            f"lignes-{i}-category": parts_category.pk,
            f"lignes-{i}-vehicle": moto.pk,
            f"lignes-{i}-status": "ORDERED",
        }
    response = as_mathis.post(f"/depenses/ajouter/?pieces={disque.pk}&pieces={filtre.pk}", donnees)
    assert response.status_code == 302
    assert response.url == "/pieces-a-commander/"
    assert not services.parts_to_order().exists()
    assert queries.vehicle_cost(moto.pk).total == D("86.00")


def test_mark_received_with_htmx_returns_the_row(as_mathis, ordered):
    response = as_mathis.post(
        f"/depenses/lignes/{ordered.pk}/statut/", {"status": "RECEIVED"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    page = response.content.decode()
    assert page.lstrip().startswith("<li")
    assert "Reçu" in page and "Marquer montée" in page
    ordered.refresh_from_db()
    assert ordered.status == Status.RECEIVED


def test_mark_status_without_htmx_goes_back_to_the_sheet(as_mathis, ordered, moto):
    response = as_mathis.post(f"/depenses/lignes/{ordered.pk}/statut/", {"status": "FITTED"})
    assert response.status_code == 302
    assert response.url == moto.get_absolute_url()
    assert "Monté" in messages_of(response)[-1]


def test_refused_status_reloads_the_page_with_a_message(as_mathis, ordered, moto):
    response = as_mathis.post(
        f"/depenses/lignes/{ordered.pk}/statut/", {"status": "RETURNED"}, HTTP_HX_REQUEST="true"
    )
    assert response.headers["HX-Redirect"] == moto.get_absolute_url()
    assert "Choisis Commandé, Reçu ou Monté." in messages_of(response)
    ordered.refresh_from_db()
    assert ordered.status == Status.ORDERED


def test_status_of_an_unpaid_part_is_not_found(as_mathis, disque):
    assert (
        as_mathis.post(f"/depenses/lignes/{disque.pk}/statut/", {"status": "RECEIVED"}).status_code
        == 404
    )


def test_sidebar_counts_every_part_even_on_a_vehicle_sheet(as_mathis, disque, make_vehicle):
    """La section « À commander » de la fiche ne remplace pas le compteur de la barre latérale."""
    autre = make_vehicle(brand="KTM", model="125 SX", status="IN_REPAIR")
    services.add_part_to_order(label="Pignon", vehicle=autre)
    page = as_mathis.get(autre.get_absolute_url()).content.decode()
    assert '2<span class="sr-only"> pièces à commander</span>' in page


def test_order_of_already_ordered_parts_goes_back_to_the_list(as_mathis, ordered):
    response = as_mathis.get(f"/depenses/ajouter/?pieces={ordered.pk}")
    assert response.status_code == 302
    assert response.url == "/pieces-a-commander/"
    (message,) = messages_of(response)
    assert "« Disque avant » a déjà été commandée" in message
    assert "Rien n'est enregistré" in message


def test_status_badge_opens_a_menu_to_go_back_from_fitted(as_mathis, ordered, moto):
    """« Monté » par erreur : le badge propose Commandé, Reçu et Monté."""
    services.change_part_status(ordered, Status.FITTED)
    page = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert "Statut de Disque avant : Monté. Changer le statut" in page
    for statut in ("ORDERED", "RECEIVED"):
        assert f'name="status" value="{statut}"' in page
    # Le statut en place est coché et referme seulement la liste.
    assert 'name="status" value="FITTED"' not in page
    assert 'aria-current="true"' in page
    assert f'aria-controls="statuts-{ordered.pk}"' in page

    response = as_mathis.post(
        f"/depenses/lignes/{ordered.pk}/statut/", {"status": "RECEIVED"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert "Marquer montée" in response.content.decode()
    ordered.refresh_from_db()
    assert ordered.status == Status.RECEIVED


def test_line_rows_offer_status_choices_only_for_paid_parts(ordered):
    (rang,) = services.line_rows([services.paid_lines().get(pk=ordered.pk)])
    assert [valeur for valeur, _ in rang["status_choices"]] == ["ORDERED", "RECEIVED", "FITTED"]
    ordered.status = Status.RETURNED
    ordered.save()
    (rang,) = services.line_rows([services.paid_lines().get(pk=ordered.pk)])
    assert rang["status_choices"] == []


def test_sidebar_entry_stays_on_one_line(as_mathis, disque):
    page = as_mathis.get("/").content.decode()
    assert '<span class="whitespace-nowrap collapsed:sr-only">À commander</span>' in page


def test_removed_invalid_line_is_ignored(as_mathis):
    """Une ligne retirée n'est pas vérifiée : ses erreurs ne bloquent pas les autres."""
    response = as_mathis.post(
        "/pieces-a-commander/ajouter/",
        parts_data({"label": "Chaîne"}, {"label": "", "unit_price": "12", "DELETE": "on"}),
    )
    assert response.status_code == 302
    assert [p.label for p in services.parts_to_order()] == ["Chaîne"]


def test_status_menu_is_hidden_in_read_only_mode(as_mathis, ordered, moto, settings):
    settings.READ_ONLY = True
    page = as_mathis.get(moto.get_absolute_url()).content.decode()
    assert "Changer le statut" not in page
    assert 'name="status"' not in page
    assert "Commandé" in page  # le badge reste affiché

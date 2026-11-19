"""Tests des retours et remboursements : règles (montant, statut de la pièce, à qui) et
écrans."""

import datetime as dt
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command

from accounting import queries
from accounting.services import Payment
from core.models import ExpenseCategory
from expenses import services
from expenses.models import ExpenseLine, Refund
from expenses.services import LineEntry

Status = ExpenseLine.Status
DATE = dt.date(2026, 10, 25)


def messages_of(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def moto(make_vehicle):
    return make_vehicle(brand="GG", model="MC250", status="IN_REPAIR")


def save(lines, payments, total, **champs):
    valeurs = {
        "date": dt.date(2026, 10, 20),
        "supplier_name": "",
        "order_number": "",
        "total_paid": D(total),
        "is_personal": False,
        "notes": "",
    }
    return services.save_expense(None, lines=lines, payments=payments, **valeurs | champs)


@pytest.fixture
def joint(parts_category, moto, axel):
    """Cas 14 : joint de 21,99 € payé par Axel, reçu."""
    depense = save(
        [LineEntry("Joint SPI", parts_category, D("21.99"), vehicle=moto, status="RECEIVED")],
        [Payment(axel, D("21.99"))],
        "21.99",
    )
    return depense.lines.get()


@pytest.fixture
def shared(parts_category, moto, mathis, axel):
    """Disque 60 € payé moitié-moitié."""
    depense = save(
        [LineEntry("Disque", parts_category, D("60.00"), vehicle=moto)],
        [Payment(mathis, D("30.00")), Payment(axel, D("30.00"))],
        "60.00",
    )
    return depense.lines.get()


def refund(line, amount, to, **champs):
    return services.record_refund(line, date=DATE, amount=D(amount), refunded_to=to, **champs)


def status_of(line) -> str:
    line.refresh_from_db()
    return line.status


# --- Règles ------------------------------------------------------------------------


def test_full_refund_returns_the_part_and_cancels_its_cost(joint, moto, axel):
    """Cas 14 : coût net 0, la pièce est « Retourné », l'argent revient à Axel."""
    refund(joint, "21.99", axel)
    assert status_of(joint) == Status.RETURNED
    assert queries.vehicle_cost(moto.pk).total == D("0")
    assert services.line_cost(joint).net == D("0")


def test_partial_refund_lowers_the_cost_but_keeps_the_status(joint, moto, axel):
    refund(joint, "10.00", axel)
    assert status_of(joint) == Status.RECEIVED
    assert queries.vehicle_cost(moto.pk).total == D("11.99")
    refund(joint, "11.99", axel)
    assert status_of(joint) == Status.RETURNED


def test_refund_to_be_specified_is_accepted_and_changes_nothing(joint, moto, axel):
    """0,00 € : accepté (montant « à préciser », reprise de l'Excel), sans effet."""
    remboursement = refund(joint, "0.00", axel)
    assert remboursement.amount == D("0.00")
    assert status_of(joint) == Status.RECEIVED
    assert queries.vehicle_cost(moto.pk).total == D("21.99")


def test_refund_cannot_exceed_what_remains(joint, axel):
    refund(joint, "20.00", axel)
    with pytest.raises(ValidationError, match="Il ne reste que 1,99"):
        refund(joint, "2.00", axel)
    assert Refund.objects.count() == 1


def test_correcting_a_refund_counts_without_itself(joint, axel):
    """Préciser un montant « à préciser » : le plafond ne compte pas l'ancien montant."""
    remboursement = refund(joint, "0.00", axel)
    refund(joint, "21.99", axel, refund=remboursement)
    remboursement.refresh_from_db()
    assert remboursement.amount == D("21.99")
    assert Refund.objects.count() == 1
    assert status_of(joint) == Status.RETURNED
    refund(joint, "21.99", axel, refund=remboursement)  # même montant : accepté


def test_binned_refund_brings_the_part_back_to_received(joint, moto, axel):
    remboursement = refund(joint, "21.99", axel)
    services.remove_refund(remboursement)
    assert status_of(joint) == Status.RECEIVED
    assert queries.vehicle_cost(moto.pk).total == D("21.99")
    assert Refund.all_objects.get().is_deleted


def test_refund_goes_into_the_balance_of_who_received_it(shared, mathis, axel):
    """Disque 60 € payé moitié-moitié, 60 € remboursés à Mathis seul : Mathis doit 30 € à
    Axel (il a récupéré l'argent des deux)."""
    refund(shared, "60.00", mathis)
    sortie = StringIO()

    call_command("controle_comptes", stdout=sortie)
    assert "Mathis doit 30,00" in sortie.getvalue()


def test_tool_refund_has_no_status(mathis):
    outillage = ExpenseCategory.objects.get(code="tooling")
    depense = save([LineEntry("Clé", outillage, D("10.00"))], [Payment(mathis, D("10"))], "10")
    cle = depense.lines.get()
    refund(cle, "10.00", mathis)
    assert status_of(cle) == ""


def test_no_refund_on_an_unpaid_part_or_a_personal_purchase(mathis):
    a_commander = services.add_part_to_order(label="Joint")
    with pytest.raises(ValidationError, match="pas encore payée"):
        refund(a_commander, "1.00", mathis)
    outillage = ExpenseCategory.objects.get(code="tooling")
    perso = save(
        [LineEntry("Gants", outillage, D("10.00"))],
        [Payment(mathis, D("10"))],
        "10",
        is_personal=True,
    )
    with pytest.raises(ValidationError, match="Un achat personnel"):
        refund(perso.lines.get(), "10.00", mathis)


def test_refund_to_a_former_partner_is_refused(joint, axel):
    axel.is_active = False
    axel.save()
    with pytest.raises(ValidationError, match="n'est plus associé"):
        refund(joint, "1.00", axel)


def test_editing_the_price_keeps_the_returned_status_in_step(joint, parts_category, moto, axel):
    """Une pièce remboursée de 21,99 € dont le prix monte à 30 € n'est plus entièrement
    remboursée : elle redevient « Reçu »."""
    refund(joint, "21.99", axel)
    services.save_expense(
        joint.expense,
        date=dt.date(2026, 10, 20),
        supplier_name="",
        order_number="",
        total_paid=D("30.00"),
        is_personal=False,
        notes="",
        lines=[
            LineEntry(
                "Joint SPI",
                parts_category,
                D("30.00"),
                vehicle=moto,
                status="RETURNED",
                pk=joint.pk,
            )
        ],
        payments=[Payment(axel, D("30.00"))],
    )
    assert status_of(joint) == Status.RECEIVED


def test_default_refunded_to_is_the_only_payer(joint, shared, axel):
    assert services.default_refunded_to(joint.expense) == axel
    assert services.default_refunded_to(shared.expense) is None


# --- Écrans ------------------------------------------------------------------------


def test_refund_form_is_prefilled(as_mathis, joint, axel):
    response = as_mathis.get(f"/depenses/lignes/{joint.pk}/retour/")
    assert response.status_code == 200
    form = response.context["form"]
    assert form["amount"].value() == D("21.99")
    assert form["refunded_to"].value() == str(axel.pk)
    page = response.content.decode()
    assert "Reste à rembourser" in page
    assert 'autocomplete="off"' in page


def test_refund_form_with_two_payers_preselects_nobody(as_mathis, shared):
    response = as_mathis.get(f"/depenses/lignes/{shared.pk}/retour/")
    assert response.context["form"]["refunded_to"].value() is None
    assert "Choisis qui a reçu l&#x27;argent." in response.content.decode()

    response = as_mathis.post(
        f"/depenses/lignes/{shared.pk}/retour/", {"date": "2026-10-25", "amount": "60,00"}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["refunded_to"] == ["Choisis qui a reçu l'argent."]
    assert not Refund.objects.exists()


def test_refund_form_records_and_goes_back_to_the_expense(as_mathis, joint, axel):
    response = as_mathis.post(
        f"/depenses/lignes/{joint.pk}/retour/",
        {"date": "2026-10-25", "amount": "21,99", "refunded_to": axel.pk, "notes": "Mauvais"},
    )
    assert response.status_code == 302
    assert response.url == joint.expense.get_absolute_url()
    assert "Le retour de « Joint SPI » est enregistré." in messages_of(response)
    assert status_of(joint) == Status.RETURNED


def test_refund_form_shows_the_amount_problem(as_mathis, joint, axel):
    response = as_mathis.post(
        f"/depenses/lignes/{joint.pk}/retour/",
        {"date": "2026-10-25", "amount": "30", "refunded_to": axel.pk},
    )
    assert response.status_code == 200
    assert "Il ne reste que 21,99" in response.context["form"].errors["amount"][0]
    assert not Refund.objects.exists()


def test_expense_detail_lists_refunds_and_offers_a_return(as_mathis, joint, axel):
    page = as_mathis.get(joint.expense.get_absolute_url()).content.decode()
    assert "Enregistrer un retour" in page

    a_preciser = refund(joint, "0.00", axel)
    page = as_mathis.get(joint.expense.get_absolute_url()).content.decode()
    assert "montant à préciser" in page
    assert "Préciser le montant" in page
    assert f"/depenses/retours/{a_preciser.pk}/modifier/" in page

    refund(joint, "21.99", axel, refund=a_preciser)
    page = as_mathis.get(joint.expense.get_absolute_url()).content.decode()
    assert "remboursés à" in page
    assert "Enregistrer un retour" not in page  # plus rien à rembourser


def test_refund_edit_and_bin_from_the_expense(as_mathis, joint, axel):
    remboursement = refund(joint, "0.00", axel)
    response = as_mathis.get(f"/depenses/retours/{remboursement.pk}/modifier/")
    # « Préciser le montant » propose ce qui reste à rembourser.
    assert response.context["form"]["amount"].value() == D("21.99")
    response = as_mathis.post(
        f"/depenses/retours/{remboursement.pk}/modifier/",
        {"date": "2026-10-26", "amount": "21,99", "refunded_to": axel.pk},
    )
    assert response.status_code == 302
    remboursement.refresh_from_db()
    assert (remboursement.amount, remboursement.date) == (D("21.99"), dt.date(2026, 10, 26))

    response = as_mathis.post(f"/depenses/retours/{remboursement.pk}/corbeille/")
    assert response.status_code == 302
    assert "Le remboursement est dans la corbeille." in messages_of(response)
    assert status_of(joint) == Status.RECEIVED
    assert as_mathis.get(f"/depenses/retours/{remboursement.pk}/modifier/").status_code == 404


def test_no_return_on_a_personal_purchase_page(as_mathis, mathis):
    outillage = ExpenseCategory.objects.get(code="tooling")
    perso = save(
        [LineEntry("Gants", outillage, D("10.00"))],
        [Payment(mathis, D("10"))],
        "10",
        is_personal=True,
    )
    assert "Enregistrer un retour" not in as_mathis.get(perso.get_absolute_url()).content.decode()
    response = as_mathis.get(f"/depenses/lignes/{perso.lines.get().pk}/retour/")
    assert response.status_code == 302
    assert "Un achat personnel" in messages_of(response)[-1]


def test_refund_actions_are_hidden_in_read_only_mode(as_mathis, joint, axel, settings):
    refund(joint, "0.00", axel)
    settings.READ_ONLY = True
    page = as_mathis.get(joint.expense.get_absolute_url()).content.decode()
    assert "montant à préciser" in page
    assert "Enregistrer un retour" not in page and "Préciser le montant" not in page


def test_returned_line_with_a_refund_to_be_specified_stays_returned(joint, axel):
    """Reprise de l'Excel : ligne « Retourné/Remboursé » + remboursement de 0,00 € à
    préciser. Elle reste « Retourné », même après un autre enregistrement (D45)."""
    a_preciser = Refund.objects.create(line=joint, date=DATE, amount=D("0"), refunded_to=axel)
    joint.status = Status.RETURNED
    joint.save()
    refund(joint, "0.00", axel, refund=a_preciser, notes="Montant à demander")
    assert status_of(joint) == Status.RETURNED
    # Mis à la corbeille, plus rien ne dit qu'elle est retournée : elle redevient « Reçu ».
    services.remove_refund(a_preciser)
    assert status_of(joint) == Status.RECEIVED


def test_expense_with_a_refund_cannot_become_personal(parts_category, mathis):
    depense = save(
        [LineEntry("Joint", parts_category, D("10.00"))], [Payment(mathis, D("10"))], "10"
    )
    ligne = depense.lines.get()
    refund(ligne, "4.00", mathis)
    with pytest.raises(ValidationError, match="Cette dépense a un remboursement"):
        services.save_expense(
            depense,
            date=dt.date(2026, 10, 20),
            supplier_name="",
            order_number="",
            total_paid=D("10"),
            is_personal=True,
            notes="",
            lines=[LineEntry("Joint", parts_category, D("10.00"), pk=ligne.pk)],
            payments=[Payment(mathis, D("10"))],
        )
    depense.refresh_from_db()
    assert not depense.is_personal


def test_refund_of_a_former_partner_can_still_be_corrected(joint, mathis, axel):
    remboursement = refund(joint, "0.00", axel)
    axel.is_active = False
    axel.save()
    refund(joint, "21.99", axel, refund=remboursement)  # même associé : accepté
    remboursement.refresh_from_db()
    assert remboursement.amount == D("21.99")


def test_editing_shows_room_without_the_refund_being_corrected(as_mathis, joint, axel):
    remboursement = refund(joint, "21.99", axel)
    response = as_mathis.get(f"/depenses/retours/{remboursement.pk}/modifier/")
    marge = response.context["room"]
    assert (marge.allocated, marge.others, marge.left) == (D("21.99"), D("0"), D("21.99"))
    assert "Autres remboursements" in response.content.decode()


def test_help_shows_only_when_nobody_is_preselected(as_mathis, joint):
    page = as_mathis.get(f"/depenses/lignes/{joint.pk}/retour/").content.decode()
    assert "Choisis qui a reçu" not in page


def test_former_partner_is_refused_under_the_partner_field(as_mathis, joint, axel):
    axel.is_active = False
    axel.save()
    # Axel n'est plus proposé : choisi quand même (vieux formulaire), il est refusé.
    response = as_mathis.post(
        f"/depenses/lignes/{joint.pk}/retour/",
        {"date": "2026-10-25", "amount": "1", "refunded_to": axel.pk},
    )
    assert response.status_code == 200
    assert "refunded_to" in response.context["form"].errors
    assert not Refund.objects.exists()


@pytest.mark.parametrize("cas", ["depense", "ligne"])
def test_refund_pages_of_binned_expenses_or_lines_are_not_found(as_mathis, joint, axel, cas):
    remboursement = refund(joint, "0.00", axel)
    (joint.expense if cas == "depense" else joint).soft_delete()
    assert as_mathis.get(f"/depenses/lignes/{joint.pk}/retour/").status_code == 404
    assert as_mathis.post(f"/depenses/lignes/{joint.pk}/retour/", {}).status_code == 404
    assert as_mathis.get(f"/depenses/retours/{remboursement.pk}/modifier/").status_code == 404
    assert as_mathis.post(f"/depenses/retours/{remboursement.pk}/corbeille/").status_code == 404
    remboursement.refresh_from_db()
    assert not remboursement.is_deleted


def test_returned_status_is_read_only_in_the_expense_form(as_mathis, joint, axel, moto):
    """Le statut « Retourné » est affiché sans champ à modifier ; les autres lignes gardent
    leur liste."""
    refund(joint, "21.99", axel)
    response = as_mathis.get(f"/depenses/{joint.expense_id}/modifier/")
    (forme,) = response.context["formset"].forms
    assert forme.status_read_only
    page = response.content.decode()
    assert "Ce statut suit le remboursement." in page
    assert "Retourné" in page
    assert 'name="lignes-0-status"' not in page
    assert 'name="lignes-__prefix__-status"' in page  # le modèle d'une nouvelle ligne


def test_received_part_keeps_its_status_choice_in_the_expense_form(as_mathis, joint):
    response = as_mathis.get(f"/depenses/{joint.expense_id}/modifier/")
    (forme,) = response.context["formset"].forms
    assert not forme.status_read_only
    assert 'name="lignes-0-status"' in response.content.decode()


def expense_post(line, moto, parts_category, axel, status) -> dict:
    return {
        "date": "2026-10-20",
        "supplier_name": "",
        "order_number": "",
        "total_paid": "21,99",
        "payer": str(axel.pk),
        "notes": "",
        "lignes-TOTAL_FORMS": "1",
        "lignes-INITIAL_FORMS": "1",
        "lignes-MIN_NUM_FORMS": "0",
        "lignes-MAX_NUM_FORMS": "1000",
        "lignes-0-line_id": line.pk,
        "lignes-0-label": line.label,
        "lignes-0-reference": "",
        "lignes-0-quantity": "1",
        "lignes-0-unit_price": "21,99",
        "lignes-0-category": parts_category.pk,
        "lignes-0-vehicle": moto.pk,
        "lignes-0-status": status,
    }


def test_returned_status_cannot_be_forced_by_the_expense_form(
    as_mathis, joint, moto, parts_category, axel
):
    """Même avec un « Monté » envoyé à la main, une ligne « à préciser » reste « Retourné »."""
    Refund.objects.create(line=joint, date=DATE, amount=D("0"), refunded_to=axel)
    joint.status = Status.RETURNED
    joint.save()
    response = as_mathis.post(
        f"/depenses/{joint.expense_id}/modifier/",
        expense_post(joint, moto, parts_category, axel, "FITTED"),
    )
    assert response.status_code == 302
    assert status_of(joint) == Status.RETURNED


def test_returned_status_survives_a_service_call_with_another_status(
    joint, moto, parts_category, axel
):
    Refund.objects.create(line=joint, date=DATE, amount=D("0"), refunded_to=axel)
    joint.status = Status.RETURNED
    joint.save()
    services.save_expense(
        joint.expense,
        date=dt.date(2026, 10, 20),
        supplier_name="",
        order_number="",
        total_paid=D("21.99"),
        is_personal=False,
        notes="",
        lines=[
            LineEntry(
                "Joint SPI", parts_category, D("21.99"), vehicle=moto, status="FITTED", pk=joint.pk
            )
        ],
        payments=[Payment(axel, D("21.99"))],
    )
    assert status_of(joint) == Status.RETURNED


def check_output() -> str:
    sortie = StringIO()
    call_command("controle_comptes", stdout=sortie)
    return sortie.getvalue()


def test_check_warns_about_each_refund_to_be_specified(joint, axel):
    """Un avertissement par retour à 0,00 €, sans erreur : les comptes restent cohérents."""
    refund(joint, "0.00", axel)
    refund(joint, "0.00", axel)
    sortie = check_output()  # pas de CommandError
    assert sortie.count("Attention : retour à 0,00 € à préciser") == 2
    assert f"« Joint SPI », dépense n°{joint.expense_id} du 20/10/2026, pour Axel" in sortie
    assert "Les comptes sont cohérents." in sortie


def test_check_does_not_warn_about_real_or_binned_refunds(joint, axel):
    a_preciser = refund(joint, "0.00", axel)
    refund(joint, "10.00", axel)
    services.remove_refund(a_preciser)
    assert "Attention" not in check_output()


def test_check_ignores_a_refund_to_be_specified_of_a_binned_expense(joint, axel):
    refund(joint, "0.00", axel)
    joint.expense.soft_delete()
    assert "Attention" not in check_output()


@pytest.fixture
def returned_to_specify(joint, axel):
    """La ligne type de l'import : « Retourné » avec un remboursement « à préciser »."""
    Refund.objects.create(line=joint, date=DATE, amount=D("0"), refunded_to=axel)
    joint.status = Status.RETURNED
    joint.save()
    return joint


def test_invalid_post_keeps_the_returned_status_read_only(
    as_mathis, returned_to_specify, moto, parts_category, axel
):
    donnees = expense_post(returned_to_specify, moto, parts_category, axel, "")
    del donnees["lignes-0-status"]
    donnees["lignes-0-label"] = ""
    response = as_mathis.post(f"/depenses/{returned_to_specify.expense_id}/modifier/", donnees)
    assert response.status_code == 200
    page = response.content.decode()
    assert "Ce statut suit le remboursement." in page
    assert 'name="lignes-0-status"' not in page
    assert status_of(returned_to_specify) == Status.RETURNED


def test_returned_status_is_kept_when_the_browser_sends_no_status(
    as_mathis, returned_to_specify, moto, parts_category, axel
):
    """Le champ est verrouillé : le navigateur n'envoie aucun statut."""
    donnees = expense_post(returned_to_specify, moto, parts_category, axel, "")
    del donnees["lignes-0-status"]
    response = as_mathis.post(f"/depenses/{returned_to_specify.expense_id}/modifier/", donnees)
    assert response.status_code == 302
    assert status_of(returned_to_specify) == Status.RETURNED


def test_page_opened_before_the_refund_was_binned_keeps_received(
    as_mathis, joint, moto, parts_category, axel
):
    """Page ouverte sur une ligne « Retourné », remboursement mis à la corbeille entre-temps :
    aucun statut n'est envoyé et la ligne reste « Reçu » (D45)."""
    donnees = expense_post(joint, moto, parts_category, axel, "")
    del donnees["lignes-0-status"]
    response = as_mathis.post(f"/depenses/{joint.expense_id}/modifier/", donnees)
    assert response.status_code == 302
    assert status_of(joint) == Status.RECEIVED


def test_returned_line_keeps_its_category_in_the_expense_form(
    as_mathis, returned_to_specify, moto, parts_category, axel
):
    """La catégorie est verrouillée : une autre valeur envoyée à la main est ignorée."""
    response = as_mathis.get(f"/depenses/{returned_to_specify.expense_id}/modifier/")
    assert response.context["formset"].forms[0].fields["category"].disabled

    autre = ExpenseCategory.objects.get(code="tooling")
    donnees = expense_post(returned_to_specify, moto, parts_category, axel, "")
    donnees["lignes-0-category"] = autre.pk
    response = as_mathis.post(f"/depenses/{returned_to_specify.expense_id}/modifier/", donnees)
    assert response.status_code == 302
    returned_to_specify.refresh_from_db()
    assert returned_to_specify.category == parts_category
    assert returned_to_specify.status == Status.RETURNED


def test_service_refuses_a_category_change_on_a_returned_line(returned_to_specify, moto, axel):
    autre = ExpenseCategory.objects.get(code="tooling")
    with pytest.raises(ValidationError, match="elle garde sa catégorie"):
        services.save_expense(
            returned_to_specify.expense,
            date=dt.date(2026, 10, 20),
            supplier_name="",
            order_number="",
            total_paid=D("21.99"),
            is_personal=False,
            notes="",
            lines=[
                LineEntry("Joint SPI", autre, D("21.99"), vehicle=moto, pk=returned_to_specify.pk)
            ],
            payments=[Payment(axel, D("21.99"))],
        )
    assert status_of(returned_to_specify) == Status.RETURNED


def test_warning_is_shown_even_when_the_check_fails(joint, axel):
    """Avertissement et incohérence ensemble : l'avertissement reste affiché, seule
    l'incohérence fait échouer la commande."""
    refund(joint, "0.00", axel)
    Refund.objects.create(line=joint, date=DATE, amount=D("30"), refunded_to=axel)
    sortie = StringIO()
    with pytest.raises(CommandError, match="1 incohérence"):
        call_command("controle_comptes", stdout=sortie)
    assert "Attention : retour à 0,00 € à préciser" in sortie.getvalue()


def test_warnings_ignore_a_refund_on_a_line_without_expense(axel):
    """Seul l'admin peut créer ce cas : le contrôle ne doit pas planter."""
    piece = services.add_part_to_order(label="Joint")
    Refund.objects.create(line=piece, date=DATE, amount=D("0"), refunded_to=axel)
    assert queries.refund_warnings() == []

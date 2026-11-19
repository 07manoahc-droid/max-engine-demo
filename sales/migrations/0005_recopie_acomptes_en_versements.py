"""Chaque acompte devient un acompte à un versement (D89).

Aller : pour chaque acompte, un versement du même montant, reçu par le même associé, à la
même date ; puis le montant et « reçu par » de l'acompte sont vidés (le total se lit dans
les versements, il n'est jamais stocké). Les soldes ne bougent pas : `controle_comptes`
donne les mêmes chiffres avant et après.

Les versements recopiés n'ont pas de ligne d'historique : l'historique de l'acompte dit
déjà qui l'a enregistré et quand. Les lignes d'historique de l'acompte gardent leurs
anciennes valeurs.

Aller refusé si des versements existent déjà (base laissée à mi-chemin par un retour
interrompu) : les recopier compterait l'argent deux fois.

Retour : le montant et « reçu par » sont remis depuis l'unique versement de chaque acompte.
Il est refusé si un acompte a plusieurs versements ou un versement à la corbeille, ou si un
acompte ou un versement a été saisi ou corrigé depuis la mise à jour (une ligne
d'historique sans montant, ou une ligne d'historique de versement : les versements recopiés
n'en ont pas) : l'ancienne forme ne saurait pas les garder, et le retour du schéma
(`sales.0004`) échouerait à mi-chemin. Le retour arrière est de toute façon interdit
sur la vraie base (feuille de route) : on restaure la sauvegarde d'avant la mise à jour.
"""

from django.db import migrations


def recopier_en_versements(apps, schema_editor):
    Deposit = apps.get_model("sales", "Deposit")
    DepositReceipt = apps.get_model("sales", "DepositReceipt")
    # Le gestionnaire des migrations voit aussi la corbeille.
    if DepositReceipt.objects.exists():
        raise RuntimeError(
            "Des versements d'acompte existent déjà : les recopier compterait l'argent deux "
            "fois. Restaure la sauvegarde d'avant la mise à jour."
        )
    for acompte in Deposit.objects.order_by("id"):
        if acompte.legacy_amount is None or acompte.legacy_received_by_id is None:
            raise RuntimeError(
                f"L'acompte n°{acompte.pk} n'a pas de montant ou pas d'associé : "
                "rien n'est recopié."
            )
        versement = DepositReceipt.objects.create(
            deposit_id=acompte.pk,
            partner_id=acompte.legacy_received_by_id,
            amount=acompte.legacy_amount,
            date=acompte.date,
        )
        # Daté comme l'acompte, pas du jour de la mise à jour (`update` ne touche pas aux
        # dates automatiques).
        DepositReceipt.objects.filter(pk=versement.pk).update(
            created_at=acompte.created_at, updated_at=acompte.updated_at
        )
    Deposit.objects.update(legacy_amount=None, legacy_received_by=None)


def remettre_les_anciens_champs(apps, schema_editor):
    Deposit = apps.get_model("sales", "Deposit")
    DepositReceipt = apps.get_model("sales", "DepositReceipt")
    # Le gestionnaire des migrations voit aussi la corbeille.
    a_la_corbeille = DepositReceipt.objects.filter(deleted_at__isnull=False).count()
    if a_la_corbeille:
        raise RuntimeError(
            f"Retour arrière refusé : {a_la_corbeille} versement(s) d'acompte à la corbeille "
            "seraient perdus. Restaure plutôt la sauvegarde d'avant la mise à jour."
        )
    HistoricalDeposit = apps.get_model("sales", "HistoricalDeposit")
    HistoricalDepositReceipt = apps.get_model("sales", "HistoricalDepositReceipt")
    if (
        HistoricalDepositReceipt.objects.exists()
        or HistoricalDeposit.objects.filter(legacy_amount__isnull=True).exists()
    ):
        raise RuntimeError(
            "Retour arrière refusé : des acomptes ou des versements ont été enregistrés ou "
            "corrigés depuis la mise à jour ; leur historique ne tient pas dans l'ancienne "
            "forme. Restaure plutôt la sauvegarde d'avant la mise à jour."
        )
    anciens = []
    for acompte in Deposit.objects.order_by("id"):
        versements = list(DepositReceipt.objects.filter(deposit_id=acompte.pk))
        if len(versements) != 1:
            raise RuntimeError(
                f"Retour arrière refusé : l'acompte n°{acompte.pk} a {len(versements)} "
                "versements, l'ancienne forme n'en garde qu'un. Restaure plutôt la "
                "sauvegarde d'avant la mise à jour."
            )
        anciens.append((acompte.pk, versements[0]))
    # Tout est vérifié avant d'écrire quoi que ce soit.
    for pk, versement in anciens:
        # La date de l'acompte (de la réservation) reste la sienne : seule la date du
        # versement, si elle a été corrigée à part, n'a pas de place dans l'ancienne forme.
        Deposit.objects.filter(pk=pk).update(
            legacy_amount=versement.amount, legacy_received_by_id=versement.partner_id
        )


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0004_versements_acompte"),
    ]

    operations = [
        migrations.RunPython(recopier_en_versements, remettre_les_anciens_champs),
    ]

"""Un encaissement retiré en modifiant une vente part à la corbeille (D49).

Le retour arrière est refusé tant qu'un encaissement est à la corbeille : sans `deleted_at`,
il redeviendrait actif et fausserait les comptes (même raison que D40).
"""

from django.db import migrations, models


def refuser_si_corbeille(apps, schema_editor):
    SaleReceipt = apps.get_model("sales", "SaleReceipt")
    nombre = SaleReceipt._base_manager.filter(deleted_at__isnull=False).count()
    if nombre:
        raise RuntimeError(
            f"Retour arrière refusé : {nombre} encaissement(s) à la corbeille "
            "redeviendraient actifs et fausseraient les comptes."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_valeurs_initiales"),
        ("sales", "0002_un_seul_acompte_en_cours"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalsalereceipt",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="supprimé le"),
        ),
        migrations.AddField(
            model_name="salereceipt",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="supprimé le"),
        ),
        # En dernier : au retour arrière, la garde s'exécute en premier, colonne encore là.
        migrations.RunPython(migrations.RunPython.noop, refuser_si_corbeille),
    ]

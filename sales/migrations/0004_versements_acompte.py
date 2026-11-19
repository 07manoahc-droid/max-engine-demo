"""Versements d'acompte (D89) : un acompte peut être reçu par les deux associés ou en
plusieurs fois.

Crée la table des versements. Le montant et « reçu par » de l'acompte sont renommés en
`legacy_…` et deviennent facultatifs, sans rien perdre : leur recopie en versements est
la migration suivante (`0005`). Au retour arrière, `0005` les remplit d'abord de nouveau.
"""

import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_valeurs_initiales'),
        ('sales', '0003_encaissements_suppression_logique'),
        ('vehicles', '0001_creation_vehicules'),
    ]

    operations = [
        migrations.CreateModel(
            name='DepositReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='créé le')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='modifié le')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='supprimé le')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='montant')),
                ('date', models.DateField(default=django.utils.timezone.localdate, verbose_name='date')),
            ],
            options={
                'verbose_name': "versement d'acompte",
                'verbose_name_plural': "versements d'acompte",
                'ordering': ['date', 'id'],
            },
        ),
        migrations.CreateModel(
            name='HistoricalDepositReceipt',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, editable=False, verbose_name='créé le')),
                ('updated_at', models.DateTimeField(blank=True, editable=False, verbose_name='modifié le')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='supprimé le')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='montant')),
                ('date', models.DateField(default=django.utils.timezone.localdate, verbose_name='date')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
            ],
            options={
                'verbose_name': "historical versement d'acompte",
                'verbose_name_plural': "historical versements d'acompte",
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.RemoveConstraint(
            model_name='deposit',
            name='deposit_positive',
        ),
        # Renommés, jamais retirés : l'historique des acomptes garde ses anciennes valeurs
        # (D47). Ils deviennent facultatifs ; `sales.0005` les recopie puis les vide.
        migrations.RenameField(
            model_name='deposit',
            old_name='amount',
            new_name='legacy_amount',
        ),
        migrations.RenameField(
            model_name='deposit',
            old_name='received_by',
            new_name='legacy_received_by',
        ),
        migrations.RenameField(
            model_name='historicaldeposit',
            old_name='amount',
            new_name='legacy_amount',
        ),
        migrations.RenameField(
            model_name='historicaldeposit',
            old_name='received_by',
            new_name='legacy_received_by',
        ),
        migrations.AlterField(
            model_name='deposit',
            name='legacy_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='montant (avant la 2.0)'),
        ),
        migrations.AlterField(
            model_name='deposit',
            name='legacy_received_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='legacy_deposits', to='core.partner', verbose_name='reçu par (avant la 2.0)'),
        ),
        migrations.AlterField(
            model_name='historicaldeposit',
            name='legacy_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='montant (avant la 2.0)'),
        ),
        migrations.AlterField(
            model_name='historicaldeposit',
            name='legacy_received_by',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='core.partner', verbose_name='reçu par (avant la 2.0)'),
        ),
        migrations.AlterField(
            model_name='deposit',
            name='date',
            field=models.DateField(default=django.utils.timezone.localdate, verbose_name='date de la réservation'),
        ),
        migrations.AlterField(
            model_name='historicaldeposit',
            name='date',
            field=models.DateField(default=django.utils.timezone.localdate, verbose_name='date de la réservation'),
        ),
        migrations.AddConstraint(
            model_name='deposit',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('legacy_amount__isnull', True), ('legacy_received_by__isnull', True)), models.Q(('legacy_amount__gt', 0), ('legacy_received_by__isnull', False)), _connector='OR'), name='deposit_legacy_fields_together'),
        ),
        migrations.AddField(
            model_name='depositreceipt',
            name='deposit',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='receipts', to='sales.deposit', verbose_name='acompte'),
        ),
        migrations.AddField(
            model_name='depositreceipt',
            name='partner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='deposit_receipts', to='core.partner', verbose_name='reçu par'),
        ),
        migrations.AddField(
            model_name='historicaldepositreceipt',
            name='deposit',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='sales.deposit', verbose_name='acompte'),
        ),
        migrations.AddField(
            model_name='historicaldepositreceipt',
            name='history_user',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.partner'),
        ),
        migrations.AddField(
            model_name='historicaldepositreceipt',
            name='partner',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='core.partner', verbose_name='reçu par'),
        ),
        migrations.AddConstraint(
            model_name='depositreceipt',
            constraint=models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='deposit_receipt_positive'),
        ),
    ]

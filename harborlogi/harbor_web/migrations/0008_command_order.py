import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count


def backfill_order(apps, schema_editor):
    Command = apps.get_model('harbor_web', 'Command')
    commands = Command.objects.using(schema_editor.connection.alias)
    if commands.values('plan__order_id').annotate(total=Count('pk')).filter(total__gt=1).exists():
        raise RuntimeError('Multiple execution intents exist for one order. Operator reconciliation of the original command keys is required before migration; no records were changed.')
    for command in commands.select_related('plan').iterator():
        commands.filter(pk=command.pk).update(order_id=command.plan.order_id)


class Migration(migrations.Migration):
    dependencies = [('harbor_web', '0007_deploymentstate_relocation')]
    operations = [
        migrations.AddField(model_name='command', name='order', field=models.OneToOneField(
            null=True, on_delete=django.db.models.deletion.PROTECT, to='harbor_web.order')),
        migrations.RunPython(backfill_order, migrations.RunPython.noop),
        migrations.AlterField(model_name='command', name='order', field=models.OneToOneField(
            on_delete=django.db.models.deletion.PROTECT, to='harbor_web.order')),
    ]

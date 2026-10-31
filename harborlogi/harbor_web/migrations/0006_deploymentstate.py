from django.conf import settings
from django.db import migrations, models


def initialize(apps, schema_editor):
    apps.get_model('harbor_web', 'DeploymentState').objects.using(schema_editor.connection.alias).create(id=1, mode=settings.MODE)


class Migration(migrations.Migration):
    dependencies = [('harbor_web', '0005_commandfact')]
    operations = [migrations.CreateModel(name='DeploymentState', fields=[('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)), ('mode', models.CharField(max_length=11, choices=[(m, m) for m in ('development', 'test', 'live')])), ('restored_at', models.DateTimeField(blank=True, null=True))], options={'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='deployment_singleton')]}), migrations.RunPython(initialize, migrations.RunPython.noop)]

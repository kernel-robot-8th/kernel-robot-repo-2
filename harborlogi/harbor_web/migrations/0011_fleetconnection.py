from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('harbor_web', '0010_order_auto_replan')]
    operations = [migrations.CreateModel(name='FleetConnection', fields=[
        ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
        ('origin', models.CharField(max_length=500)),
        ('principal', models.CharField(max_length=192)),
        ('arm_console_enabled', models.BooleanField(default=False)),
        ('version', models.PositiveIntegerField(default=1)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ], options={'constraints': [models.CheckConstraint(condition=models.Q(id=1, version__gt=0), name='fleet_connection_singleton')]})]

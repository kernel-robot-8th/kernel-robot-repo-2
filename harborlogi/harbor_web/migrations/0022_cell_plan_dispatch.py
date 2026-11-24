import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('harbor_web', '0021_observation_port_event'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CellPlanDispatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('dispatch_mode', models.CharField(choices=[('HARBOR_PLAN', 'Harbor Plan'), ('ROSY_DIRECTIVE', 'ROSY Directive')], max_length=16)),
                ('directive_mode', models.CharField(max_length=16)),
                ('request_key', models.CharField(max_length=128, unique=True)),
                ('rosy_plan_id', models.CharField(max_length=96)),
                ('order_source', models.CharField(choices=[('harbor', 'harbor'), ('rosy_r', 'rosy_r')], max_length=16)),
                ('state', models.CharField(default='PROPOSED', max_length=24)),
                ('planned_steps', models.JSONField(default=list)),
                ('rosy_task_ids', models.JSONField(default=list)),
                ('reason', models.CharField(blank=True, max_length=192)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('observation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='harbor_web.observation')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cell_plan_dispatches', to='harbor_web.order')),
                ('plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cell_plan_dispatches', to='harbor_web.plan')),
            ],
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('harbor_web', '0009_cargofootprint')]
    operations = [
        migrations.AddField(model_name='order', name='auto_replan', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='order', name='planning_observation', field=models.ForeignKey(
            null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='harbor_web.observation')),
    ]

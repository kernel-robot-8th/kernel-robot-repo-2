from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('harbor_web', '0006_deploymentstate')]
    operations = [migrations.AddField(model_name='deploymentstate', name='relocation', field=models.JSONField(blank=True, default=dict))]

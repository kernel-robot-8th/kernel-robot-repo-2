# Django makemigrations로 생성한 출발 허락 출처 제약.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('harbor_web', '0019_order_cargo_message'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='departureintent',
            name='actor',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='departureintent',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='departureintent',
            name='release_event_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AlterField(
            model_name='departureintent',
            name='confirmation',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='harbor_web.fieldconfirmation'),
        ),
        migrations.AddConstraint(
            model_name='departureintent',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('confirmation__isnull', False), ('release_event_id', '')), models.Q(('confirmation__isnull', True), models.Q(('release_event_id', ''), _negated=True)), _connector='OR'), name='departure_exactly_one_authorization'),
        ),
    ]

from django.db import migrations, models
from django.db.models import Count


def _dedupe_loaded_event_ids(apps, schema_editor):
    OutboundDispatch = apps.get_model('harbor_web', 'OutboundDispatch')
    dup_ids = list(
        OutboundDispatch.objects.exclude(loaded_event_id='')
        .values('loaded_event_id')
        .annotate(n=Count('id'))
        .filter(n__gt=1)
        .values_list('loaded_event_id', flat=True)
    )
    if not dup_ids:
        return
    used = set(
        OutboundDispatch.objects.exclude(loaded_event_id='').values_list('loaded_event_id', flat=True)
    )
    loaded_states = {'LOADED', 'EXIT_PROPOSED', 'EXITED'}
    for event_id in dup_ids:
        rows = list(OutboundDispatch.objects.filter(loaded_event_id=event_id).order_by('id'))
        for dup in rows[1:]:
            if dup.state in loaded_states and dup.loaded_by_id is None:
                candidate = f'dup-{dup.pk}:{event_id}'[:64]
                if candidate in used:
                    candidate = f'dup-{dup.pk}'[:64]
                used.add(candidate)
                OutboundDispatch.objects.filter(pk=dup.pk).update(loaded_event_id=candidate)
            else:
                OutboundDispatch.objects.filter(pk=dup.pk).update(loaded_event_id='')


class Migration(migrations.Migration):

    dependencies = [
        ('harbor_web', '0030_commercial_manifest'),
    ]

    operations = [
        migrations.RunPython(_dedupe_loaded_event_ids, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='outbounddispatch',
            constraint=models.UniqueConstraint(
                condition=models.Q(('loaded_event_id', ''), _negated=True),
                fields=('loaded_event_id',),
                name='outbound_dispatch_unique_loaded_event_id',
            ),
        ),
    ]

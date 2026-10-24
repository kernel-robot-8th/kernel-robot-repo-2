from django.db import models


class DeploymentState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=11, choices=[(m, m) for m in ('development', 'test', 'live')])
    relocation = models.JSONField(default=dict, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='deployment_singleton')]

"""Validate Fleet ARM facts once; business progression remains in the ledger."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ArmExecution:
    job: dict
    receipt: dict | None

    @property
    def mission_id(self):
        return self.job['mission_id']

    @property
    def attempt_id(self):
        return self.receipt['attempt_id'] if self.receipt is not None else None

    @property
    def goal_confirmed(self):
        return self.job['status'] == 'GOAL_CONFIRMED' and self.receipt is not None

    @property
    def held(self):
        return self.job['status'] == 'HOLD'


def _bound_job(binding, request_key, job, *, mission_id, principal):
    job = dict(binding.check(job, request_key))
    if mission_id is not None and job['mission_id'] != mission_id:
        raise ValueError('Physical job identity changed')
    if principal is not None and job.get('proposal_principal_id') != principal:
        raise ValueError('Fleet proposal principal differs')
    return job


def validate_arm_execution(binding, request_key, job, receipt, *, mission_id=None, principal=None):
    job = _bound_job(binding, request_key, job, mission_id=mission_id, principal=principal)
    return _with_receipt(binding, job, receipt)


def _with_receipt(binding, job, receipt):
    if receipt is not None:
        receipt = dict(binding.check_receipt(job, receipt))
    elif job['status'] == 'GOAL_CONFIRMED':
        raise ValueError('A confirmed physical job requires its receipt')
    return ArmExecution(job, receipt)


def read_arm_execution(client, binding, request_key, *, mission_id=None, principal=None):
    response = client.arm_request(request_key)
    # Bind identity before requesting a receipt from the returned mission.
    principal = principal if principal is not None else getattr(client, 'expected_principal', None)
    job = _bound_job(binding, request_key, response['job'], mission_id=mission_id, principal=principal)
    receipt = (client.physical_receipt(job['mission_id'])['receipt']
               if job['status'] == 'GOAL_CONFIRMED' else None)
    return _with_receipt(binding, job, receipt)

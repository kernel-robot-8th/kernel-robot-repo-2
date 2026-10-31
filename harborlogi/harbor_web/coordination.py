"""Single-process queue/dispatch reservation for the initial movement profile."""
from functools import wraps
from threading import RLock

queue_lock = RLock()


def serialized(operation):
    @wraps(operation)
    def locked(*args, **kwargs):
        # Dispatch holds this through its outcome; Fleet requests have a 10-second timeout.
        with queue_lock:
            return operation(*args, **kwargs)
    return locked

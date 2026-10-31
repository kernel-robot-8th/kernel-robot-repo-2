"""Process lifetime writer ownership, acquired before ledger recovery."""
import os
from pathlib import Path


class WriterLease:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(str(self.path) + '.writer.lock', 'a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            self.file = None
            raise RuntimeError('Ledger already has an execution owner') from error
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.close()
            self.file = None

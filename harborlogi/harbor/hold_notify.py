"""Business HOLD notification outbox (ROSY D-997 Decision 6, Harbor row).

Each non-HOLD → HOLD transition of the business ledger records one occurrence
in the same SQLite transaction. External delivery stays off until an approved
receiving channel and a delivery client are configured; no completion notify,
auto-resume, or chat approval exists here. The platform notification delivery
service itself is still a target of ROSY D-997, not an existing dependency.
"""
from contextlib import closing
from datetime import datetime, timezone
import ipaddress
import json
from urllib.parse import urlsplit

from harbor.model import aware_time, identifier

CLASSIFICATION = "INTERVENTION_REQUIRED"

OUTBOX_SCHEMA = """
    CREATE TABLE IF NOT EXISTS hold_outbox (
        occurrence_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, hold_sequence INTEGER NOT NULL,
        occurred_at TEXT NOT NULL, expires_at TEXT NOT NULL, payload TEXT NOT NULL,
        delivery_state TEXT NOT NULL DEFAULT 'PENDING', delivery_note TEXT);
"""


def occurrence_id(key, sequence):
    return f"{key}#hold-{sequence}"


def validated_screen_base(value):
    """HTTPS (or loopback HTTP) work-screen base without credentials, query, or fragment."""
    if value is None:
        return None
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = False
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in {'https', 'http'} or (parsed.scheme == 'http' and not local)
            or parsed.path not in ('', '/') or any(ord(c) <= 32 for c in value) or '\\' in value):
        raise ValueError("Expected an HTTPS (or loopback HTTP) work screen base without credentials or query")
    parsed.port  # Reject malformed ports before emitting any link.
    return value.rstrip('/')


def screen_url(base, key):
    """Tokenless protected business screen link; the operator still authenticates to open it."""
    return None if base is None else f"{base}/work/{key}"


def record_hold(c, key, sequence, *, now, expires_at, screen_url):
    """Write one HOLD occurrence inside the caller's business transaction (D-997 atomicity)."""
    aware_time(now)
    aware_time(expires_at)
    occurrence = occurrence_id(key, sequence)
    # Only the D-997 fields: classification, opaque ids, times, and the tokenless link.
    payload = json.dumps({"classification": CLASSIFICATION, "business_id": key, "occurrence_id": occurrence,
                          "occurred_at": now.isoformat(), "expires_at": expires_at.isoformat(),
                          "screen_url": screen_url},
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    c.execute("INSERT INTO hold_outbox(occurrence_id,request_key,hold_sequence,occurred_at,expires_at,payload) "
              "VALUES(?,?,?,?,?,?)",
              (occurrence, key, sequence, now.isoformat(), expires_at.isoformat(), payload))
    return occurrence


def _undeliverable(c, row, now):
    if row["delivery_state"] != "PENDING":
        return "Occurrence is not waiting for delivery"
    if now > datetime.fromisoformat(row["expires_at"]):
        return "Occurrence expired"
    business = c.execute("SELECT state,hold_sequence FROM business WHERE request_key=?",
                         (row["request_key"],)).fetchone()
    if business is None or business["state"] != "HOLD" or business["hold_sequence"] != row["hold_sequence"]:
        return "Business left this HOLD occurrence"
    return None


def pre_send_check(c, occurrence, *, channel, channels, now):
    """D-997 pre-send validity: same business, same occurrence still HOLD, channel in receive scope."""
    aware_time(now)
    if channel not in channels:
        raise ValueError("Channel is outside the approved receive scope")
    row = c.execute("SELECT * FROM hold_outbox WHERE occurrence_id=?", (occurrence,)).fetchone()
    if row is None:
        raise ValueError("Unknown HOLD occurrence")
    reason = _undeliverable(c, row, now)
    if reason is not None:
        raise ValueError(reason)
    return dict(row)


class HoldNotifier:
    """Delivery-side gate; without approved channels and a client nothing is sent (D-997 default)."""

    def __init__(self, ledger, *, channels=(), client=None):
        self.ledger, self.channels, self.client = ledger, tuple(channels), client
        for channel in self.channels:
            identifier(channel)
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Duplicate receiving channel")
        if self.client is not None and not self.channels:
            raise ValueError("A delivery client needs at least one approved receiving channel")

    def outbox(self):
        with closing(self.ledger._connect()) as c:
            return [dict(row) for row in c.execute(
                "SELECT * FROM hold_outbox ORDER BY occurred_at, occurrence_id")]

    def deliver_pending(self, *, now=None):
        """Send each waiting occurrence once per approved channel; invalid occurrences are rejected."""
        now = datetime.now(timezone.utc) if now is None else now
        aware_time(now)
        if not self.channels or self.client is None:
            return []  # External sending is off by default; the local HOLD and outbox stay untouched.
        sent = []
        with closing(self.ledger._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT * FROM hold_outbox WHERE delivery_state='PENDING' "
                             "ORDER BY occurred_at, occurrence_id").fetchall()
            for row in rows:
                reason = _undeliverable(c, row, now)
                if reason is not None:
                    c.execute("UPDATE hold_outbox SET delivery_state='REJECTED',delivery_note=? "
                              "WHERE occurrence_id=?", (reason, row["occurrence_id"]))
                    continue
                body = json.loads(row["payload"])
                # A client failure rolls this transaction back; the occurrence stays PENDING
                # and is retried until it expires or the HOLD ends.
                for channel in self.channels:
                    self.client(channel, body)
                c.execute("UPDATE hold_outbox SET delivery_state='SENT' WHERE occurrence_id=?",
                          (row["occurrence_id"],))
                sent.append(row["occurrence_id"])
        return sent

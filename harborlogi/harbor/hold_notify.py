"""Business HOLD notification outbox (ROSY D-997 Decision 6, Harbor row).

Each non-HOLD → HOLD transition of the business ledger records one occurrence
in the same SQLite transaction. Notification bodies carry only opaque
identifiers — never the business request key — and the screen link resolves
through the outbox notification id. Delivery attempts hold per-(occurrence,
channel) state, send outside any SQLite write lock, and treat an unclear
submission (timeout, ambiguous failure, restart) as UNKNOWN with no automatic
resend. The platform notification delivery service itself is still a target of
ROSY D-997, not an existing dependency.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import secrets
from urllib.parse import urlsplit

from harbor.model import aware_time, identifier

CLASSIFICATION = "INTERVENTION_REQUIRED"

OUTBOX_SCHEMA = """
    CREATE TABLE IF NOT EXISTS hold_outbox (
        occurrence_id TEXT PRIMARY KEY, notification_id TEXT NOT NULL UNIQUE,
        request_key TEXT NOT NULL, hold_sequence INTEGER NOT NULL,
        occurred_at TEXT NOT NULL, expires_at TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS hold_delivery (
        occurrence_id TEXT NOT NULL, channel TEXT NOT NULL,
        state TEXT NOT NULL, note TEXT,
        PRIMARY KEY(occurrence_id,channel));
"""


class DefiniteDeliveryFailure(Exception):
    """The channel refused the delivery before any ambiguity; a later retry is allowed."""


class UnknownDeliveryFailure(Exception):
    """The submission outcome is unclear (timeout/5xx-style); never blindly resend (D-997)."""


def occurrence_id(key, sequence):
    return f"{key}#hold-{sequence}"


def new_notification_id():
    """Opaque public id for one HOLD occurrence; the raw business key never leaves the ledger."""
    return secrets.token_hex(16)


def business_ref(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]


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


def screen_url(base, notification_id):
    """Tokenless link; the protected screen resolves the business through this notification id."""
    return None if base is None else f"{base}/work/{notification_id}"


def record_hold(c, key, sequence, *, notification_id, now, expires_at, screen_url):
    """Write one HOLD occurrence inside the caller's business transaction (D-997 atomicity)."""
    aware_time(now)
    aware_time(expires_at)
    occurrence = occurrence_id(key, sequence)
    # Only the D-997 fields: classification, opaque ids, times, and the tokenless link.
    payload = json.dumps({"classification": CLASSIFICATION, "notification_id": notification_id,
                          "business_ref": business_ref(key),
                          "occurred_at": now.isoformat(), "expires_at": expires_at.isoformat(),
                          "screen_url": screen_url},
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    c.execute("INSERT INTO hold_outbox(occurrence_id,notification_id,request_key,hold_sequence,"
              "occurred_at,expires_at,payload) VALUES(?,?,?,?,?,?,?)",
              (occurrence, notification_id, key, sequence, now.isoformat(), expires_at.isoformat(), payload))
    return occurrence


def _undeliverable(c, row, now):
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
    """Delivery-side gate; without approved channels and a client nothing is sent (D-997 default).

    One attempt is: a short transaction marking SENDING, the network call with no
    SQLite lock held, then a short transaction recording SENT / retry / UNKNOWN.
    """

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

    def deliveries(self):
        with closing(self.ledger._connect()) as c:
            return [dict(row) for row in c.execute(
                "SELECT * FROM hold_delivery ORDER BY occurrence_id, channel")]

    def deliver_pending(self, *, now=None):
        """Send each waiting (occurrence, channel) once; unclear submissions never auto-resend."""
        now = datetime.now(timezone.utc) if now is None else now
        aware_time(now)
        if not self.channels or self.client is None:
            return []  # External sending is off by default; the local HOLD and outbox stay untouched.
        self._settle_inflight()
        sent = []
        for occurrence in self._waiting():
            for channel in self.channels:
                if self._attempt(occurrence, channel, now) == "SENT":
                    sent.append(occurrence)
        return sent

    def _settle_inflight(self):
        """A leftover SENDING row means the previous send's outcome is unknown (restart/crash)."""
        with closing(self.ledger._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE hold_delivery SET state='UNKNOWN',note=? WHERE state='SENDING'",
                      ("Interrupted send left the submission unclear",))

    def _waiting(self):
        with closing(self.ledger._connect()) as c:
            return [row[0] for row in c.execute(
                "SELECT o.occurrence_id FROM hold_outbox o LEFT JOIN hold_delivery d "
                "ON d.occurrence_id=o.occurrence_id "
                "WHERE d.occurrence_id IS NULL OR d.state='PENDING' "
                "GROUP BY o.occurrence_id ORDER BY o.occurred_at, o.occurrence_id")]

    def _attempt(self, occurrence, channel, now):
        with closing(self.ledger._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            try:
                pre_send_check(c, occurrence, channel=channel, channels=self.channels, now=now)
            except ValueError as reason:
                c.execute("INSERT INTO hold_delivery VALUES(?,?,?,?) ON CONFLICT(occurrence_id,channel) "
                          "DO UPDATE SET state=excluded.state,note=excluded.note",
                          (occurrence, channel, "REJECTED", str(reason)))
                return "REJECTED"
            settled = c.execute("SELECT state FROM hold_delivery WHERE occurrence_id=? AND channel=?",
                                (occurrence, channel)).fetchone()
            if settled is not None and settled["state"] != "PENDING":
                return settled["state"]  # SENT/UNKNOWN/REJECTED are final.
            c.execute("INSERT INTO hold_delivery VALUES(?,?,?,?) ON CONFLICT(occurrence_id,channel) "
                      "DO UPDATE SET state='SENDING',note=NULL", (occurrence, channel, "SENDING", None))
            body = json.loads(c.execute("SELECT payload FROM hold_outbox WHERE occurrence_id=?",
                                        (occurrence,)).fetchone()[0])
        try:
            self.client(channel, body)  # Outside any SQLite transaction: no write lock while sending.
        except DefiniteDeliveryFailure as failure:
            state, note = "PENDING", str(failure) or "definite delivery failure"
        except BaseException as failure:  # Timeouts and unclassified errors are unclear, never resent.
            state, note = "UNKNOWN", f"{type(failure).__name__}: {failure}"[:200]
        else:
            state, note = "SENT", None
        with closing(self.ledger._connect()) as c, c:
            c.execute("UPDATE hold_delivery SET state=?,note=? WHERE occurrence_id=? AND channel=? "
                      "AND state='SENDING'", (state, note, occurrence, channel))
        return state

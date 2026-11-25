"""Shared container physical color vocabulary for Harbor ledger, 3D display, and ROSY recognition."""

CARGO_COLORS = ('orange', 'pink', 'yellow', 'sky')
CARGO_COLOR_LABELS = {
    'orange': '주황',
    'pink': '분홍',
    'yellow': '노랑',
    'sky': '하늘',
}
CARGO_COLOR_CHOICES = tuple((code, CARGO_COLOR_LABELS[code]) for code in CARGO_COLORS)

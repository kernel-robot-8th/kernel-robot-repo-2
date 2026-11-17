"""Size-mixing stacking rules shared by observation checks and planners (design 33)."""


STACK_RULES = ('any', 'same', 'same_1x1_on_wide')


def stack_ok(area, upper, n, lengths, by_id, occupied):
    """The one place that decides whether the cargo below may support `upper` (design 33).

    `occupied` maps (area, row, column, level) to cargo ids, `by_id` cargo ids to placements.
    Full-support geometry is checked elsewhere; this only applies the area's size-mixing rule.
    """
    if area.stack_rule == 'any' or upper.level == 1:
        return True
    for cargo in {occupied.get((upper.area_id, r, c, level - 1)) for _, r, c, level in _cells(upper, n)} - {None}:
        below = by_id[cargo]
        if (lengths[cargo], below.orientation, below.row, below.column) == (n, upper.orientation, upper.row, upper.column):
            continue
        if area.stack_rule == 'same_1x1_on_wide' and n == 1:
            continue
        return False
    return True


def _cells(p, n):
    return tuple((p.area_id, p.row + (i if p.orientation == 90 else 0),
                  p.column + (i if p.orientation == 0 else 0), p.level) for i in range(n))

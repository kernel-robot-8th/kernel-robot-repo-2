"""Configured logical yard addresses, separate from robot geometry and reach."""
from dataclasses import dataclass

from harbor.model import identifier


@dataclass(frozen=True)
class YardCell:
    bay_id: str
    row: int
    column: int


@dataclass(frozen=True)
class YardLayout:
    rows: int
    columns: int
    levels: int
    cells: tuple[YardCell, ...]

    def __post_init__(self):
        if any(type(n) is not int or not 1 <= n <= limit
               for n, limit in ((self.rows, 12), (self.columns, 12), (self.levels, 6))):
            raise ValueError('Yard dimensions exceed the configured bounds.')
        count = self.rows * self.columns
        if not isinstance(self.cells, tuple) or len(self.cells) != count:
            raise ValueError('Every configured yard column is required.')
        for cell in self.cells:
            if not isinstance(cell, YardCell):
                raise ValueError('Expected configured yard cells.')
            identifier(cell.bay_id)
            if any(type(n) is not int or not 1 <= n <= limit
                   for n, limit in ((cell.row, self.rows), (cell.column, self.columns))):
                raise ValueError('Yard coordinates must lie inside the configured rectangle.')
        if len({c.bay_id for c in self.cells}) != count or len({(c.row, c.column) for c in self.cells}) != count:
            raise ValueError('Yard IDs and row/column coordinates must be unique.')

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {'rows', 'columns', 'levels', 'cells'}:
            raise ValueError('Yard layout requires rows, columns, levels and cells.')
        if any(type(payload[key]) is not int or not 1 <= payload[key] <= limit
               for key, limit in (('rows', 12), ('columns', 12), ('levels', 6))):
            raise ValueError('Yard dimensions exceed the configured bounds.')
        if not isinstance(payload['cells'], list) or len(payload['cells']) != payload['rows'] * payload['columns']:
            raise ValueError('Every configured yard column is required.')
        if any(
                not isinstance(c, dict) or set(c) != {'bay_id', 'row', 'column'} for c in payload['cells']):
            raise ValueError('Each yard cell requires bay_id, row and column.')
        return cls(payload['rows'], payload['columns'], payload['levels'], tuple(YardCell(**c) for c in payload['cells']))

    def at(self, row, column):
        return next(c for c in self.cells if (c.row, c.column) == (row, column))

    def validate_bays(self, bays, *, auxiliary_ids):
        by_id = {bay.bay_id: bay for bay in bays}
        configured = {c.bay_id for c in self.cells}
        if not configured <= by_id.keys() or by_id.keys() - configured - set(auxiliary_ids):
            raise ValueError('Observation must include every configured column and no unconfigured storage column.')
        if any(by_id[c.bay_id].capacity != self.levels for c in self.cells):
            raise ValueError('Configured yard columns must match the configured levels.')

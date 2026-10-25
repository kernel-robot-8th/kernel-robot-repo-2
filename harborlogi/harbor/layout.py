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
        if any(type(n) is not int or n != 3 for n in (self.rows, self.columns, self.levels)):
            raise ValueError('The current yard requires 3 rows, 3 columns and 3 levels.')
        if not isinstance(self.cells, tuple) or len(self.cells) != 9:
            raise ValueError('All nine configured yard columns are required.')
        for cell in self.cells:
            if not isinstance(cell, YardCell):
                raise ValueError('Expected configured yard cells.')
            identifier(cell.bay_id)
            if any(type(n) is not int or not 1 <= n <= 3 for n in (cell.row, cell.column)):
                raise ValueError('Yard row and column must be integers from 1 to 3.')
        if len({c.bay_id for c in self.cells}) != 9 or len({(c.row, c.column) for c in self.cells}) != 9:
            raise ValueError('Yard IDs and row/column coordinates must be unique.')

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {'rows', 'columns', 'levels', 'cells'}:
            raise ValueError('Yard layout requires rows, columns, levels and cells.')
        if not isinstance(payload['cells'], list) or any(
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
            raise ValueError('Configured yard columns must each have three levels.')

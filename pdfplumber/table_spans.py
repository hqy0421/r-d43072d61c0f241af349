"""Span-aware table export.

``pdfplumber``'s regular :class:`~pdfplumber.table.Table` model discovers the
smallest rectangles outlined by a page's lines.  When a table contains merged
cells, those merges show up as *larger* native rectangles.  Flattening such a
table into a plain two-dimensional list loses the information needed to tell
apart:

* an empty cell (a native rectangle whose text is ``""``),
* a grid position for which no native rectangle exists at all, and
* a grid position covered by a merged neighbour.

This module reconstructs an explicit grid from the native rectangles, records
the row/column span of every cell and the cell that covers each position,
reports geometric conflicts, and builds multi-level header paths for the data
columns.

Nothing here changes the behavior of :meth:`pdfplumber.table.Table.extract`;
the span-aware views are strictly additive.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from . import utils
from ._typing import T_bbox, T_num, T_obj

DEFAULT_SPAN_X_TOLERANCE = 1
DEFAULT_SPAN_Y_TOLERANCE = 1

KIND_ANCHOR = "anchor"
KIND_COVERED = "covered"
KIND_MISSING = "missing"
KIND_CONFLICT = "conflict"

T_grid_point = Tuple[int, int]
T_span_settings = Union["SpanSettings", Dict[str, Any]]


@dataclass
class SpanSettings:
    """Settings for :meth:`pdfplumber.table.Table.span_grid`.

    :param header_rows: Number of header rows at the top of the table.  Used to
        build multi-level header paths and to flag cells that cross the
        header/data boundary.
    :param x_tolerance: Distance within which two x coordinates are treated as
        the same vertical grid boundary.
    :param y_tolerance: Distance within which two y coordinates are treated as
        the same horizontal grid boundary.
    :param text_settings: Keyword arguments passed to text extraction for each
        cell (the same ``text_*`` settings accepted by ``extract_text``).
    """

    header_rows: int = 0
    x_tolerance: T_num = DEFAULT_SPAN_X_TOLERANCE
    y_tolerance: T_num = DEFAULT_SPAN_Y_TOLERANCE
    text_settings: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.header_rows < 0:
            raise ValueError("header_rows cannot be negative")
        if self.x_tolerance < 0:
            raise ValueError("x_tolerance cannot be negative")
        if self.y_tolerance < 0:
            raise ValueError("y_tolerance cannot be negative")
        if self.text_settings is None:
            self.text_settings = {}

    @classmethod
    def resolve(cls, settings: Optional[T_span_settings]) -> "SpanSettings":
        if settings is None:
            return cls()
        if isinstance(settings, cls):
            return settings
        if isinstance(settings, dict):
            core: Dict[str, Any] = {}
            text_settings: Dict[str, Any] = {}
            for k, v in settings.items():
                if k.startswith("text_"):
                    text_settings[k[5:]] = v
                else:
                    core[k] = v
            base_text = dict(core.pop("text_settings", {}) or {})
            base_text.update(text_settings)
            core["text_settings"] = base_text
            return cls(**core)
        raise ValueError(f"Cannot resolve span settings: {settings}")

    def with_text_settings(self, text_settings: Dict[str, Any]) -> "SpanSettings":
        """Return a copy with ``text_settings`` taking precedence."""
        merged = dict(self.text_settings or {})
        merged.update(text_settings)
        return SpanSettings(
            header_rows=self.header_rows,
            x_tolerance=self.x_tolerance,
            y_tolerance=self.y_tolerance,
            text_settings=merged,
        )

    def with_fallback_text_settings(self, fallback: Dict[str, Any]) -> "SpanSettings":
        """Return a copy where ``fallback`` only fills unset text settings."""
        return SpanSettings(
            header_rows=self.header_rows,
            x_tolerance=self.x_tolerance,
            y_tolerance=self.y_tolerance,
            text_settings={**fallback, **(self.text_settings or {})},
        )


@dataclass
class GeometricConflict:
    """A geometric inconsistency discovered while building the grid."""

    kind: str
    message: str
    positions: List[T_grid_point] = field(default_factory=list)
    cells: List[T_bbox] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "positions": [list(p) for p in self.positions],
            "cells": [list(c) for c in self.cells],
        }


@dataclass
class SpanCell:
    """A native table cell mapped onto the reconstructed grid."""

    row: int
    col: int
    rowspan: int
    colspan: int
    bbox: T_bbox
    text: str
    covered_positions: List[T_grid_point] = field(default_factory=list)

    @property
    def positions(self) -> List[T_grid_point]:
        """All grid positions covered by this cell (including its anchor)."""
        return list(self.covered_positions)

    @property
    def grid_range(self) -> Tuple[int, int, int, int]:
        """``(row0, col0, row1, col1)`` with exclusive end coordinates."""
        return (
            self.row,
            self.col,
            self.row + self.rowspan,
            self.col + self.colspan,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "grid_range": list(self.grid_range),
            "bbox": list(self.bbox),
            "text": self.text,
            "covered_positions": [list(p) for p in self.covered_positions],
        }


@dataclass
class GridPosition:
    """One position in the reconstructed grid."""

    row: int
    col: int
    kind: str
    grid_bbox: T_bbox
    owners: List[SpanCell] = field(default_factory=list)

    @property
    def anchor(self) -> Optional[SpanCell]:
        """The single cell covering this position, if unambiguous."""
        return self.owners[0] if len(self.owners) == 1 else None

    @property
    def source(self) -> Optional[SpanCell]:
        """The native cell covering this position, if unambiguous."""
        return self.anchor

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "row": self.row,
            "col": self.col,
            "kind": self.kind,
            "grid_bbox": list(self.grid_bbox),
            "rowspan": None,
            "colspan": None,
            "text": None,
            "source": None,
        }
        if len(self.owners) == 1:
            owner = self.owners[0]
            result["rowspan"] = owner.rowspan
            result["colspan"] = owner.colspan
            result["source"] = {
                "row": owner.row,
                "col": owner.col,
                "bbox": list(owner.bbox),
            }
            if self.kind == KIND_ANCHOR:
                # Only the anchor carries the native text.  Covered positions
                # are explicitly ``None``: never "" (blank) and never a copy.
                result["anchor"] = [owner.row, owner.col]
                result["text"] = owner.text
            elif self.kind == KIND_COVERED:
                result["covered_by"] = [owner.row, owner.col]
        elif self.kind == KIND_CONFLICT:
            result["owners"] = [[c.row, c.col] for c in self.owners]
        return result


@dataclass
class HeaderPath:
    """The multi-level header path for one data column."""

    col: int
    path: Tuple[Optional[str], ...]
    key: str
    labels: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "col": self.col,
            "path": list(self.path),
            "key": self.key,
            "labels": list(self.labels),
        }


def _cluster_boundaries(
    values: List[T_num], tolerance: T_num
) -> Tuple[List[float], Dict[T_num, int]]:
    """Cluster coordinate values and return representatives plus a lookup."""
    if not values:
        return [], {}

    clusters = utils.cluster_list(list(set(values)), tolerance)
    reps: List[float] = []
    lookup: Dict[T_num, int] = {}
    for i, cluster in enumerate(clusters):
        reps.append(sum(cluster) / len(cluster))
        for value in cluster:
            lookup[value] = i
    return reps, lookup


def _char_in_bbox(char: T_obj, bbox: T_bbox) -> bool:
    v_mid = (char["top"] + char["bottom"]) / 2
    h_mid = (char["x0"] + char["x1"]) / 2
    x0, top, x1, bottom = bbox
    return bool((h_mid >= x0) and (h_mid < x1) and (v_mid >= top) and (v_mid < bottom))


def _cell_text(chars: List[T_obj], bbox: T_bbox, settings: Dict[str, Any]) -> str:
    cell_chars = [char for char in chars if _char_in_bbox(char, bbox)]
    if not len(cell_chars):
        return ""
    kwargs = dict(settings)
    if "layout" in kwargs:
        kwargs["layout_width"] = bbox[2] - bbox[0]
        kwargs["layout_height"] = bbox[3] - bbox[1]
        kwargs["layout_bbox"] = bbox
    return utils.extract_text(cell_chars, **kwargs)


def _normalize_label(text: str) -> str:
    """Collapse internal whitespace so a header label fits on one line."""
    return " ".join(text.split())


class SpanTable:
    """A grid reconstruction of a :class:`~pdfplumber.table.Table`.

    Instances are produced by :meth:`pdfplumber.table.Table.span_grid`.
    """

    def __init__(
        self,
        table: Any,
        settings: SpanSettings,
        xs: List[float],
        ys: List[float],
        cells: List[SpanCell],
        grid: List[List[GridPosition]],
        conflicts: List[GeometricConflict],
    ) -> None:
        self.table = table
        self.page = table.page
        self.settings = settings
        self.header_rows = settings.header_rows
        self.xs = xs
        self.ys = ys
        self.cells = cells
        self.grid = grid
        self.conflicts = conflicts
        self._conflicted_cells = {id(c) for c in self.conflicts_cells()}
        self.n_rows = max(len(ys) - 1, 0)
        self.n_cols = max(len(xs) - 1, 0)

    def conflicts_cells(self) -> List[SpanCell]:
        """Cells participating in an ambiguous geometric overlap."""
        conflicting = [
            c for c in self.conflicts if c.kind in ("overlap", "duplicate_cell")
        ]
        bboxes = {tuple(bbox) for conflict in conflicting for bbox in conflict.cells}
        return [cell for cell in self.cells if tuple(cell.bbox) in bboxes]

    @property
    def bbox(self) -> T_bbox:
        result: T_bbox = self.table.bbox
        return result

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    @property
    def header_cells(self) -> List[SpanCell]:
        """Cells anchored within the header rows."""
        return [c for c in self.cells if c.row < self.header_rows]

    @property
    def data_cells(self) -> List[SpanCell]:
        """Cells anchored in the data rows, in row/column order."""
        return [
            c
            for c in self.cells
            if c.row >= self.header_rows and id(c) not in self._conflicted_cells
        ]

    def position(self, row: int, col: int) -> GridPosition:
        """Return the grid position at ``row``/``col``."""
        return self.grid[row][col]

    def cell_at(self, row: int, col: int) -> Optional[SpanCell]:
        """Return the cell covering ``row``/``col``, if unambiguous."""
        return self.grid[row][col].anchor

    def headers(self) -> List[HeaderPath]:
        """Build a multi-level header path for every data column.

        Covered positions inherit the header text of a horizontal merge (a
        cell anchored in the same header row).  Vertical continuations of a
        merged header cell produce ``None`` at the lower levels, so a
        ``rowspan=2`` label appears once rather than being repeated.

        Columns whose full paths are identical still receive distinct
        ``key`` values (``"Name"``, ``"Name #2"``, ...).
        """
        paths: List[HeaderPath] = []
        for col in range(self.n_cols):
            labels: List[Optional[str]] = []
            for row in range(self.header_rows):
                pos = self.grid[row][col]
                label: Optional[str] = None
                if pos.kind in (KIND_ANCHOR, KIND_COVERED):
                    if len(pos.owners) == 1:
                        owner = pos.owners[0]
                        if owner.row == row:
                            label = _normalize_label(owner.text)
                elif pos.kind == KIND_CONFLICT:
                    same_row = [c for c in pos.owners if c.row == row]
                    if same_row:
                        label = _normalize_label(same_row[0].text)
                labels.append(label)

            path = tuple(labels)
            used: Tuple[str, ...] = tuple(
                label for label in labels if label is not None and label != ""
            )
            paths.append(HeaderPath(col=col, path=path, key="", labels=used))

        counts: Dict[str, int] = {}
        for item in paths:
            base = " / ".join(item.labels) or f"Column {item.col + 1}"
            counts[base] = counts.get(base, 0) + 1

        seen: Dict[str, int] = {}
        for item in paths:
            base = " / ".join(item.labels) or f"Column {item.col + 1}"
            seen[base] = seen.get(base, 0) + 1
            item.key = base if counts[base] == 1 else f"{base} #{seen[base]}"

        return paths

    def data_records(self) -> List[Dict[str, Any]]:
        """Export one record per data cell.

        Merged cells produce a single record carrying ``rowspan``/``colspan``;
        covered positions are listed in ``covered`` but never emitted as
        separate values.  Missing positions produce no record at all.
        """
        headers = {h.col: h for h in self.headers()}
        records: List[Dict[str, Any]] = []
        for cell in self.data_cells:
            cols = range(cell.col, cell.col + cell.colspan)
            record: Dict[str, Any] = {
                "row": cell.row,
                "col": cell.col,
                "rowspan": cell.rowspan,
                "colspan": cell.colspan,
                "bbox": list(cell.bbox),
                "text": cell.text,
                "covered": [
                    list(p) for p in cell.covered_positions if p != (cell.row, cell.col)
                ],
                "header_keys": [headers[c].key for c in cols],
                "header_paths": [list(headers[c].path) for c in cols],
            }
            if cell.colspan == 1:
                record["header"] = headers[cell.col].key
            records.append(record)
        return records

    def to_rows(self) -> List[List[Dict[str, Any]]]:
        """Return the grid as rows of position dictionaries."""
        return [[pos.to_dict() for pos in row] for row in self.grid]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable description of the whole table."""
        return {
            "header_rows": self.header_rows,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "xs": list(self.xs),
            "ys": list(self.ys),
            "bbox": list(self.bbox),
            "cells": [cell.to_dict() for cell in self.cells],
            "rows": self.to_rows(),
            "headers": [header.to_dict() for header in self.headers()],
            "data": self.data_records(),
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    def __repr__(self) -> str:
        return (
            f"<SpanTable rows={self.n_rows} cols={self.n_cols} "
            f"cells={len(self.cells)} conflicts={len(self.conflicts)}>"
        )


def span_grid(table: Any, settings: Optional[T_span_settings] = None) -> SpanTable:
    """Build a :class:`SpanTable` from a native table.

    The native ``table.cells`` rectangles are used as-is; text is extracted
    from the page's character objects using the table's usual rules.
    """
    resolved = SpanSettings.resolve(settings)
    text_settings = resolved.text_settings or {}

    native_cells: List[T_bbox] = list(table.cells)
    raw_xs: List[T_num] = []
    raw_ys: List[T_num] = []
    for cell in native_cells:
        raw_xs.extend([cell[0], cell[2]])
        raw_ys.extend([cell[1], cell[3]])

    xs, x_lookup = _cluster_boundaries(raw_xs, resolved.x_tolerance)
    ys, y_lookup = _cluster_boundaries(raw_ys, resolved.y_tolerance)

    if resolved.header_rows > max(len(ys) - 1, 0):
        raise ValueError(
            f"header_rows={resolved.header_rows} exceeds the number of "
            f"grid rows ({max(len(ys) - 1, 0)})"
        )

    conflicts: List[GeometricConflict] = []
    cells: List[SpanCell] = []
    seen_bboxes: Dict[T_bbox, int] = {}
    page_chars: List[T_obj] = list(table.page.chars)

    for bbox in native_cells:
        if bbox in seen_bboxes:
            seen_bboxes[bbox] += 1
            continue
        seen_bboxes[bbox] = 1

        row0 = y_lookup[bbox[1]]
        row1 = y_lookup[bbox[3]]
        col0 = x_lookup[bbox[0]]
        col1 = x_lookup[bbox[2]]
        rowspan = row1 - row0
        colspan = col1 - col0

        if rowspan <= 0 or colspan <= 0:
            conflicts.append(
                GeometricConflict(
                    kind="degenerate_cell",
                    message=(
                        "Cell collapses to zero span after applying "
                        f"x_tolerance={resolved.x_tolerance}, "
                        f"y_tolerance={resolved.y_tolerance}"
                    ),
                    cells=[bbox],
                )
            )
            continue

        cells.append(
            SpanCell(
                row=row0,
                col=col0,
                rowspan=rowspan,
                colspan=colspan,
                bbox=bbox,
                text=_cell_text(page_chars, bbox, text_settings),
            )
        )

    for bbox, count in seen_bboxes.items():
        if count > 1:
            conflicts.append(
                GeometricConflict(
                    kind="duplicate_cell",
                    message=f"Identical cell rectangle appears {count} times",
                    cells=[bbox],
                )
            )

    # Map each grid position to the cell(s) that cover it.  Positions with
    # more than one distinct owner are geometric conflicts.
    owners: Dict[T_grid_point, List[SpanCell]] = {}
    for span_cell in cells:
        span_cell.covered_positions = []
        for row in range(span_cell.row, span_cell.row + span_cell.rowspan):
            for col in range(span_cell.col, span_cell.col + span_cell.colspan):
                point = (row, col)
                span_cell.covered_positions.append(point)
                owners.setdefault(point, []).append(span_cell)

    grid: List[List[GridPosition]] = []
    for row in range(max(len(ys) - 1, 0)):
        grid_row: List[GridPosition] = []
        for col in range(max(len(xs) - 1, 0)):
            point = (row, col)
            point_owners = owners.get(point, [])
            if not point_owners:
                kind = KIND_MISSING
            elif len(point_owners) > 1:
                kind = KIND_CONFLICT
            elif point == (point_owners[0].row, point_owners[0].col):
                kind = KIND_ANCHOR
            else:
                kind = KIND_COVERED
            grid_row.append(
                GridPosition(
                    row=row,
                    col=col,
                    kind=kind,
                    grid_bbox=(xs[col], ys[row], xs[col + 1], ys[row + 1]),
                    owners=point_owners,
                )
            )
        grid.append(grid_row)

    # Group overlapping positions by the exact set of owners, so one conflict
    # covers the whole overlap region instead of one conflict per position.
    overlap_groups: Dict[Tuple[int, ...], List[T_grid_point]] = {}
    cell_index = {id(cell): i for i, cell in enumerate(cells)}
    for point, point_owners in owners.items():
        if len(point_owners) > 1:
            key = tuple(sorted(cell_index[id(c)] for c in point_owners))
            overlap_groups.setdefault(key, []).append(point)

    for indices, points in overlap_groups.items():
        involved = [cells[i] for i in indices]
        conflicts.append(
            GeometricConflict(
                kind="overlap",
                message=(
                    f"{len(involved)} cells claim the same grid position(s); "
                    "the table geometry is ambiguous"
                ),
                positions=sorted(points),
                cells=[c.bbox for c in involved],
            )
        )

    if resolved.header_rows > 0:
        for span_cell in cells:
            if span_cell.row < resolved.header_rows < span_cell.row + span_cell.rowspan:
                conflicts.append(
                    GeometricConflict(
                        kind="header_data_span",
                        message=(
                            "Cell crosses the header/data boundary; "
                            "it is treated as a header cell and excluded "
                            "from data records"
                        ),
                        positions=[(span_cell.row, span_cell.col)],
                        cells=[span_cell.bbox],
                    )
                )

    # Text inside a grid position that no native rectangle covers would be
    # silently dropped by the export.  Report it instead of hiding it.
    if table.page is not None:
        for grid_row in grid:
            for pos in grid_row:
                if pos.kind != KIND_MISSING:
                    continue
                orphans = [
                    char
                    for char in page_chars
                    if char["text"].strip() and _char_in_bbox(char, pos.grid_bbox)
                ]
                if orphans:
                    conflicts.append(
                        GeometricConflict(
                            kind="unassigned_text",
                            message=(
                                "Text lies in a grid position with no native "
                                "cell; the geometry does not cover it"
                            ),
                            positions=[(pos.row, pos.col)],
                            cells=[pos.grid_bbox],
                        )
                    )

    cells.sort(key=lambda c: (c.row, c.col))

    return SpanTable(
        table=table,
        settings=resolved,
        xs=xs,
        ys=ys,
        cells=cells,
        grid=grid,
        conflicts=conflicts,
    )

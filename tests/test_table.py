#!/usr/bin/env python
import logging
import os
import unittest

import pytest

import pdfplumber
from pdfplumber import table

logging.disable(logging.ERROR)

HERE = os.path.abspath(os.path.dirname(__file__))


class Test(unittest.TestCase):
    @classmethod
    def setup_class(self):
        path = os.path.join(HERE, "pdfs/pdffill-demo.pdf")
        self.pdf = pdfplumber.open(path)

    @classmethod
    def teardown_class(self):
        self.pdf.close()

    def test_orientation_errors(self):
        with pytest.raises(ValueError):
            table.join_edge_group([], "x")

    def test_table_settings_errors(self):
        with pytest.raises(ValueError):
            tf = table.TableFinder(self.pdf.pages[0], tuple())

        with pytest.raises(TypeError):
            tf = table.TableFinder(self.pdf.pages[0], {"strategy": "x"})
            tf.get_edges()

        with pytest.raises(ValueError):
            tf = table.TableFinder(self.pdf.pages[0], {"vertical_strategy": "x"})

        with pytest.raises(ValueError):
            tf = table.TableFinder(
                self.pdf.pages[0],
                {
                    "vertical_strategy": "explicit",
                    "explicit_vertical_lines": [],
                },
            )

        with pytest.raises(ValueError):
            tf = table.TableFinder(self.pdf.pages[0], {"join_tolerance": -1})
            tf.get_edges()

    def test_edges_strict(self):
        path = os.path.join(HERE, "pdfs/issue-140-example.pdf")
        with pdfplumber.open(path) as pdf:
            t = pdf.pages[0].extract_table(
                {
                    "vertical_strategy": "lines_strict",
                    "horizontal_strategy": "lines_strict",
                }
            )

        assert t[-1] == [
            "",
            "0085648100300",
            "CENTRAL KMA",
            "LILYS 55% DARK CHOC BAR",
            "415",
            "$ 0.61",
            "$ 253.15",
            "0.0000",
            "",
        ]

    def test_rows_and_columns(self):
        path = os.path.join(HERE, "pdfs/issue-140-example.pdf")
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            table = page.find_table()
            row = [page.crop(bbox).extract_text() for bbox in table.rows[0].cells]
            assert row == [
                "Line no",
                "UPC code",
                "Location",
                "Item Description",
                "Item Quantity",
                "Bill Amount",
                "Accrued Amount",
                "Handling Rate",
                "PO number",
            ]
            col = [page.crop(bbox).extract_text() for bbox in table.columns[1].cells]
            assert col == [
                "UPC code",
                "0085648100305",
                "0085648100380",
                "0085648100303",
                "0085648100300",
            ]

    def test_explicit_desc_decimalization(self):
        """
        See issue #290
        """
        tf = table.TableFinder(
            self.pdf.pages[0],
            {
                "vertical_strategy": "explicit",
                "explicit_vertical_lines": [100, 200, 300],
                "horizontal_strategy": "explicit",
                "explicit_horizontal_lines": [100, 200, 300],
            },
        )
        assert tf.tables[0].extract()

    def test_text_tolerance(self):
        path = os.path.join(HERE, "pdfs/senate-expenditures.pdf")
        with pdfplumber.open(path) as pdf:
            bbox = (70.332, 130.986, 420, 509.106)
            cropped = pdf.pages[0].crop(bbox)
            t = cropped.extract_table(
                {
                    "horizontal_strategy": "text",
                    "vertical_strategy": "text",
                    "min_words_vertical": 20,
                }
            )
            t_tol = cropped.extract_table(
                {
                    "horizontal_strategy": "text",
                    "vertical_strategy": "text",
                    "min_words_vertical": 20,
                    "text_x_tolerance": 1,
                }
            )
            t_tol_from_tables = cropped.extract_tables(
                {
                    "horizontal_strategy": "text",
                    "vertical_strategy": "text",
                    "min_words_vertical": 20,
                    "text_x_tolerance": 1,
                }
            )[0]

        assert t[-1] == [
            "DHAW20190070",
            "09/09/2019",
            "CITIBANK-TRAVELCBACARD",
            "08/12/2019",
            "08/14/2019",
        ]
        assert t_tol[-1] == [
            "DHAW20190070",
            "09/09/2019",
            "CITIBANK - TRAVEL CBA CARD",
            "08/12/2019",
            "08/14/2019",
        ]
        assert t_tol[-1] == t_tol_from_tables[-1]

    def test_text_layout(self):
        path = os.path.join(HERE, "pdfs/issue-53-example.pdf")
        with pdfplumber.open(path) as pdf:
            table = pdf.pages[0].extract_table(
                {
                    "text_layout": True,
                }
            )
            assert table[3][0] == "   FY2013   \n   FY2014   "

    def test_text_without_words(self):
        assert table.words_to_edges_h([]) == []
        assert table.words_to_edges_v([]) == []

    def test_order(self):
        """
        See issue #336
        """
        path = os.path.join(HERE, "pdfs/issue-336-example.pdf")
        with pdfplumber.open(path) as pdf:
            tables = pdf.pages[0].extract_tables()
            assert len(tables) == 3
            assert len(tables[0]) == 8
            assert len(tables[1]) == 11
            assert len(tables[2]) == 2

    def test_issue_466_mixed_strategy(self):
        """
        See issue #466
        """
        path = os.path.join(HERE, "pdfs/issue-466-example.pdf")
        with pdfplumber.open(path) as pdf:
            tables = pdf.pages[0].extract_tables(
                {
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "text",
                    "snap_tolerance": 8,
                    "intersection_tolerance": 4,
                }
            )

            # The engine only extracts the tables which have drawn horizontal
            # lines.
            # For the 3 extracted tables, some common properties are expected:
            # - 4 rows
            # - 3 columns
            # - Data in last row contains the string 'last'
            for t in tables:
                assert len(t) == 4
                assert len(t[0]) == 3

                # Verify that all cell contain real data
                for cell in t[3]:
                    assert "last" in cell

    def test_discussion_539_null_value(self):
        """
        See discussion #539
        """
        path = os.path.join(HERE, "pdfs/nics-background-checks-2015-11.pdf")
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            table_settings = {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "explicit_vertical_lines": [],
                "explicit_horizontal_lines": [],
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "edge_min_length": 3,
                "min_words_vertical": 3,
                "min_words_horizontal": 1,
                "text_keep_blank_chars": False,
                "text_tolerance": 3,
                "intersection_tolerance": 3,
            }
            assert page.extract_table(table_settings)
            assert page.extract_tables(table_settings)

    def test_table_curves(self):
        # See https://github.com/jsvine/pdfplumber/discussions/808
        path = os.path.join(HERE, "pdfs/table-curves-example.pdf")
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            assert len(page.curves)
            tables = page.extract_tables()
            assert len(tables) == 1
            t = tables[0]
            assert t[-2][-2] == "Uncommon"

            assert len(page.extract_tables({"vertical_strategy": "lines_strict"})) == 0

    def test_span_grid_merged_headers(self):
        path = os.path.join(HERE, "pdfs/span-table-example.pdf")
        with pdfplumber.open(path) as pdf:
            table = pdf.pages[0].find_table()
            span = table.span_grid({"header_rows": 2})

        # Grid dimensions come from clustered native boundaries.
        assert (span.n_rows, span.n_cols) == (5, 6)

        # Top-left header cell spans both header rows.
        top_left = span.cell_at(0, 0)
        assert top_left is not None
        assert (top_left.row, top_left.col) == (0, 0)
        assert (top_left.rowspan, top_left.colspan) == (2, 1)
        assert top_left.text == "Region"

        # "Q1" and "Q2" are horizontal merges of three and two columns.
        q1 = span.cell_at(0, 1)
        q2 = span.cell_at(0, 4)
        assert q1 is not None and q2 is not None
        assert (q1.rowspan, q1.colspan) == (1, 3)
        assert (q2.rowspan, q2.colspan) == (1, 2)

        # Covered positions point back at their anchor and carry no text.
        covered = span.position(0, 2)
        assert covered.kind == "covered"
        assert covered.source is q1
        assert covered.to_dict()["text"] is None

    def test_span_grid_header_paths(self):
        path = os.path.join(HERE, "pdfs/span-table-example.pdf")
        with pdfplumber.open(path) as pdf:
            span = pdf.pages[0].find_table().span_grid({"header_rows": 2})

        headers = {h.col: h for h in span.headers()}
        assert headers[0].key == "Region"
        assert headers[0].path == ("Region", None)
        assert headers[1].key == "Q1 / Sales"
        assert headers[2].key == "Q1 / Cost"
        assert headers[3].key == "Q1 / Profit"
        assert headers[4].key == "Q2 / Sales"
        assert headers[5].key == "Q2 / Cost"

        # A repeated leaf name under different parents stays distinguishable.
        assert headers[1].key != headers[4].key

    def test_span_grid_records_emit_merge_once(self):
        path = os.path.join(HERE, "pdfs/span-table-example.pdf")
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[0]
            span = page.find_table().span_grid({"header_rows": 2})
            exported = page.extract_table_with_spans(span_settings={"header_rows": 2})

        # The merged amount is anchored once, spanning two rows.
        merged = span.cell_at(3, 5)
        assert merged is not None
        assert merged.bbox == (450, 232, 530, 292)
        assert (merged.rowspan, merged.colspan) == (2, 1)
        assert merged.text == "999"

        assert span.position(4, 5).kind == "covered"
        assert span.position(4, 5).source is merged

        values = [record["text"] for record in exported["data"]]
        assert values.count("999") == 1

        record = next(r for r in exported["data"] if r["text"] == "999")
        assert record["rowspan"] == 2
        assert record["covered"] == [[4, 5]]
        assert record["header_keys"] == ["Q2 / Cost"]

    def test_span_grid_missing_position(self):
        path = os.path.join(HERE, "pdfs/span-table-missing-example.pdf")
        with pdfplumber.open(path) as pdf:
            span = pdf.pages[0].find_table().span_grid({"header_rows": 2})

        missing = span.position(4, 5)
        assert missing.kind == "missing"
        assert missing.source is None
        assert missing.to_dict()["text"] is None

        # Orphan text in a position with no native cell is surfaced, not lost.
        kinds = {conflict.kind for conflict in span.conflicts}
        assert "unassigned_text" in kinds
        unassigned = next(c for c in span.conflicts if c.kind == "unassigned_text")
        assert unassigned.positions == [(4, 5)]

    def test_span_grid_geometry_conflict(self):
        from pdfplumber.table_spans import span_grid

        class FakePage:
            chars = []

            def __init__(self, bbox):
                self.bbox = bbox

        class FakeTable:
            page = FakePage((0, 0, 200, 200))

            def __init__(self):
                # Two cells whose rectangles overlap ambiguously.
                self.cells = [(0, 0, 100, 50), (50, 0, 150, 50)]

            @property
            def bbox(self):
                return (0, 0, 150, 50)

        result = span_grid(FakeTable())
        kinds = [conflict.kind for conflict in result.conflicts]
        assert "overlap" in kinds
        overlap = next(c for c in result.conflicts if c.kind == "overlap")
        assert (0, 1) in overlap.positions

    def test_span_grid_settings_validation(self):
        path = os.path.join(HERE, "pdfs/span-table-example.pdf")
        with pdfplumber.open(path) as pdf:
            table = pdf.pages[0].find_table()
            with pytest.raises(ValueError):
                table.span_grid({"header_rows": -1})
            with pytest.raises(ValueError):
                table.span_grid({"header_rows": 99})
            # Legacy 2-D extraction is unchanged.
            legacy = table.extract()
            assert legacy[0][0] == "Region"
            assert legacy[3][5] == "999"

    def test_span_json_serializable(self):
        path = os.path.join(HERE, "pdfs/span-table-example.pdf")
        with pdfplumber.open(path) as pdf:
            span = pdf.pages[0].find_table().span_grid({"header_rows": 2})
            payload = span.to_json()
        assert '"header_rows": 2' in payload
        assert '"kind": "covered"' in payload

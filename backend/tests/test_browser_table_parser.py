"""Unit tests for `parse_html_table` — the pure-function table parser
that backs `PlaywrightConnector.read_table`. No browser involved.

Covers the three header-source modes called out in the docstring (thead,
first-row-th, headerless-fallback) + edge cases (empty input, malformed
HTML, multi-cell content with whitespace).
"""

from __future__ import annotations

from workflow_platform.connectors.browser.playwright_connector import parse_html_table

# --- header source #1: <thead><tr><th> ---


def test_thead_with_tbody() -> None:
    html = """
    <table>
        <thead><tr><th>A</th><th>B</th></tr></thead>
        <tbody>
            <tr><td>1</td><td>2</td></tr>
            <tr><td>3</td><td>4</td></tr>
        </tbody>
    </table>
    """
    assert parse_html_table(html) == [
        {"A": "1", "B": "2"},
        {"A": "3", "B": "4"},
    ]


def test_thead_without_tbody() -> None:
    """thead present but rows live directly in <table>."""
    html = """
    <table>
        <thead><tr><th>X</th><th>Y</th></tr></thead>
        <tr><td>a</td><td>b</td></tr>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"X": "a", "Y": "b"}]


# --- header source #2: first <tr>'s <th> (no thead) ---


def test_first_row_th_no_thead() -> None:
    html = """
    <table>
        <tr><th>Col1</th><th>Col2</th></tr>
        <tr><td>11</td><td>22</td></tr>
        <tr><td>33</td><td>44</td></tr>
    </table>
    """
    assert parse_html_table(html) == [
        {"Col1": "11", "Col2": "22"},
        {"Col1": "33", "Col2": "44"},
    ]


# --- header source #3: headerless (synthesize col_N) ---


def test_headerless_synthesizes_col_names() -> None:
    """No thead, no <th> anywhere — first <td> row is data, headers
    are synthesized col_0 / col_1 / ..."""
    html = """
    <table>
        <tr><td>1</td><td>2</td><td>3</td></tr>
        <tr><td>4</td><td>5</td><td>6</td></tr>
    </table>
    """
    assert parse_html_table(html) == [
        {"col_0": "1", "col_1": "2", "col_2": "3"},
        {"col_0": "4", "col_1": "5", "col_2": "6"},
    ]


# --- edge cases ---


def test_empty_input_returns_empty_list() -> None:
    assert parse_html_table("") == []
    assert parse_html_table("<p>not a table</p>") == []


def test_table_with_no_rows() -> None:
    assert parse_html_table("<table></table>") == []
    assert parse_html_table("<table><thead></thead></table>") == []


def test_cell_text_is_stripped_and_whitespace_normalized() -> None:
    html = """
    <table>
        <thead><tr><th>  Title  </th></tr></thead>
        <tbody>
            <tr><td>
                Multi-line
                content
            </td></tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    # Header stripped, multi-line cell whitespace-normalized to spaces.
    assert "Title" in rows[0]
    assert rows[0]["Title"] == "Multi-line content"


def test_cell_with_nested_tags_flattens_to_text() -> None:
    """Nested tags flatten to text. A single `<a href>` also produces an
    `_href` sibling key (D8a) — keep both assertions to pin the
    interaction."""
    html = """
    <table>
        <thead><tr><th>Item</th></tr></thead>
        <tbody>
            <tr><td><a href="#"><b>Click me</b></a></td></tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"Item": "Click me", "Item_href": "#"}]


def test_row_with_fewer_cells_than_headers_keeps_only_present() -> None:
    """Real DataTables sometimes emit narrower rows. We preserve what's
    there and skip the missing columns rather than fabricating empty
    strings — the consumer can detect missing keys."""
    html = """
    <table>
        <thead><tr><th>A</th><th>B</th><th>C</th></tr></thead>
        <tbody>
            <tr><td>1</td><td>2</td></tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"A": "1", "B": "2"}]


def test_row_with_more_cells_than_headers_drops_overflow() -> None:
    """Defensive — if a body row has extra <td>, ignore them."""
    html = """
    <table>
        <thead><tr><th>A</th></tr></thead>
        <tbody>
            <tr><td>1</td><td>extra</td></tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"A": "1"}]


def test_real_world_rpa_challenge_shape() -> None:
    """The RPA challenge OCR site uses DataTables, which renders a
    thead with sorting-controls inside the <th>. The header text should
    still come out clean."""
    html = """
    <table id="tableSandbox">
        <thead>
            <tr>
                <th class="sorting">ID<span class="sort-icon"></span></th>
                <th class="sorting">Due Date<span class="sort-icon"></span></th>
                <th class="sorting">Invoice<span class="sort-icon"></span></th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>2233</td>
                <td>21-08-2024</td>
                <td><a href="/invoices/2233.jpg">Download</a></td>
            </tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert len(rows) == 1
    # Sort icon span flattened away — header text is just "ID" etc.
    assert rows[0]["ID"] == "2233"
    assert rows[0]["Due Date"] == "21-08-2024"
    assert rows[0]["Invoice"] == "Download"
    # D8a: the single anchor in the Invoice cell shows up as `Invoice_href`.
    assert rows[0]["Invoice_href"] == "/invoices/2233.jpg"


# ---------- D8a: href capture ----------


def test_cell_with_single_anchor_captures_href() -> None:
    html = """
    <table>
        <thead><tr><th>Link</th></tr></thead>
        <tbody><tr><td><a href="/invoices/5.jpg">Download</a></td></tr></tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"Link": "Download", "Link_href": "/invoices/5.jpg"}]


def test_cell_with_icon_only_anchor_still_captures_href() -> None:
    """The actual RPA challenge case: anchor wraps a glyphicon span, no
    visible text. read_table without href capture loses the URL
    completely — the bug D8a fixes."""
    html = """
    <table>
        <thead><tr><th>Invoice</th></tr></thead>
        <tbody>
            <tr><td><a href="/invoices/5.jpg" target="_blank">
                <span class="glyphicon glyphicon-download-alt"></span>
            </a></td></tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows[0]["Invoice"] == ""
    assert rows[0]["Invoice_href"] == "/invoices/5.jpg"


def test_relative_href_resolves_against_base_url() -> None:
    html = """
    <table>
        <thead><tr><th>Link</th></tr></thead>
        <tbody><tr><td><a href="/invoices/5.jpg">D</a></td></tr></tbody>
    </table>
    """
    rows = parse_html_table(html, base_url="https://example.com/page")
    assert rows[0]["Link_href"] == "https://example.com/invoices/5.jpg"


def test_absolute_href_passes_through() -> None:
    html = """
    <table>
        <thead><tr><th>Link</th></tr></thead>
        <tbody><tr><td><a href="https://other.example.com/x.jpg">D</a></td></tr></tbody>
    </table>
    """
    rows = parse_html_table(html, base_url="https://example.com/page")
    assert rows[0]["Link_href"] == "https://other.example.com/x.jpg"


def test_cell_with_no_anchor_omits_href_key() -> None:
    html = """
    <table>
        <thead><tr><th>Name</th></tr></thead>
        <tbody><tr><td>Alice</td></tr></tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"Name": "Alice"}]
    assert "Name_href" not in rows[0]


def test_cell_with_multiple_anchors_omits_href_key() -> None:
    """Disambiguation is ambiguous; don't guess. Agent can read_html
    the cell if they really need both URLs."""
    html = """
    <table>
        <thead><tr><th>Links</th></tr></thead>
        <tbody>
            <tr><td><a href="/a">A</a> | <a href="/b">B</a></td></tr>
        </tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows[0]["Links"] == "A | B"
    assert "Links_href" not in rows[0]


def test_anchor_without_href_is_ignored() -> None:
    """`<a>` without `href` (e.g. JS handlers) doesn't contribute a URL."""
    html = """
    <table>
        <thead><tr><th>Link</th></tr></thead>
        <tbody><tr><td><a>Click</a></td></tr></tbody>
    </table>
    """
    rows = parse_html_table(html)
    assert rows == [{"Link": "Click"}]

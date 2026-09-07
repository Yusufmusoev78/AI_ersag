import os
from pathlib import Path

from docx import Document
from openpyxl import Workbook, load_workbook

import storage


def _abs(path: str) -> Path:
    return Path(path).expanduser().resolve()


def list_office_files(directory: str) -> str:
    """List Word (.docx) and Excel (.xlsx) files in a directory.

    Args:
        directory: Folder to scan, e.g. "C:\\Users\\me\\Documents".
    """
    folder = _abs(directory)
    if not folder.is_dir():
        return f"Error: '{folder}' is not a directory."
    files = sorted(
        p.name for p in folder.iterdir() if p.suffix.lower() in (".docx", ".xlsx")
    )
    return "\n".join(files) if files else "No .docx or .xlsx files found."


def read_text_file(path: str, max_chars: int = 20000) -> str:
    """Read a plain text file, e.g. .txt, .csv, .md, .json, .py, .js, .log.

    Args:
        path: Path to the text file.
        max_chars: Maximum number of characters to return, to avoid huge files (default 20000).
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file: {exc}"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... [truncated, {len(text)} total characters]"
    return text


def open_in_app(path: str) -> str:
    """Open a file in its default Windows application (e.g. Word or Excel).

    Args:
        path: Path to the file to open.
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    os.startfile(file_path)
    storage.log_operation("open_in_app", str(file_path))
    return f"Opened {file_path} in its default application."


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------


def read_docx(path: str) -> str:
    """Read a Word document's full text: paragraphs (with style) and tables.

    Args:
        path: Path to the .docx file.
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    doc = Document(file_path)
    lines = []
    for i, p in enumerate(doc.paragraphs):
        if p.text.strip():
            lines.append(f"[{i}] ({p.style.name}) {p.text}")
    for t_idx, table in enumerate(doc.tables):
        lines.append(f"-- Table {t_idx} --")
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines) if lines else "(empty document)"


def create_docx(path: str, title: str, paragraphs: list[str]) -> str:
    """Create a new Word document with a title heading and a list of paragraphs.

    Args:
        path: Path to save the new .docx file.
        title: Document title, added as a Heading 1.
        paragraphs: List of paragraph strings to add after the title.
    """
    file_path = _abs(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    if title:
        doc.add_heading(title, level=1)
    for para in paragraphs:
        doc.add_paragraph(para)
    doc.save(file_path)
    storage.log_operation("create_docx", str(file_path), title)
    os.startfile(file_path)
    storage.log_operation("open_in_app", str(file_path))
    return f"Created and opened {file_path}"


def edit_docx_text(path: str, find: str, replace: str) -> str:
    """Find and replace text across all paragraphs in a Word document.

    Args:
        path: Path to the .docx file.
        find: Exact text to search for.
        replace: Text to replace it with.
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    doc = Document(file_path)
    count = 0
    for p in doc.paragraphs:
        for run in p.runs:
            if find in run.text:
                run.text = run.text.replace(find, replace)
                count += 1
    doc.save(file_path)
    storage.log_operation("edit_docx_text", str(file_path), f"'{find}' -> '{replace}' ({count} runs)")
    return f"Replaced {count} occurrence(s) of '{find}' in {file_path}"


def append_docx_paragraph(path: str, text: str, style: str = "") -> str:
    """Append a paragraph (optionally a heading) to the end of a Word document.

    Args:
        path: Path to the .docx file.
        text: Paragraph text to add.
        style: Optional paragraph style name, e.g. "Heading 2". Empty for normal text.
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    doc = Document(file_path)
    if style:
        doc.add_paragraph(text, style=style)
    else:
        doc.add_paragraph(text)
    doc.save(file_path)
    storage.log_operation("append_docx_paragraph", str(file_path), text[:80])
    return f"Appended paragraph to {file_path}"


def add_docx_table(path: str, rows: list[list[str]]) -> str:
    """Append a table to the end of a Word document from a 2D list of strings.

    Args:
        path: Path to the .docx file.
        rows: Table rows, each a list of cell strings. First row is treated as the header.
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    if not rows:
        return "Error: rows is empty."
    doc = Document(file_path)
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Light Grid Accent 1"
    for r, row_data in enumerate(rows):
        for c, value in enumerate(row_data):
            table.cell(r, c).text = str(value)
    doc.save(file_path)
    storage.log_operation("add_docx_table", str(file_path), f"{len(rows)} rows")
    return f"Added a {len(rows)}x{len(rows[0])} table to {file_path}"


# ---------------------------------------------------------------------------
# Excel (.xlsx)
# ---------------------------------------------------------------------------


def read_excel(path: str, sheet_name: str = "") -> str:
    """Read an Excel workbook's sheet names and grid data for one sheet.

    Args:
        path: Path to the .xlsx file.
        sheet_name: Sheet to read. Empty string uses the active sheet.
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    wb = load_workbook(file_path)
    sheet = wb[sheet_name] if sheet_name else wb.active
    lines = [f"Sheets: {', '.join(wb.sheetnames)}", f"Reading: {sheet.title}"]
    for row in sheet.iter_rows(values_only=True):
        if any(cell is not None for cell in row):
            lines.append(" | ".join("" if c is None else str(c) for c in row))
    return "\n".join(lines)


def _coerce(value: str):
    """Turn a numeric-looking string into an int/float so Excel stores it as a number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def create_excel(path: str, sheet_name: str, headers: list[str], rows: list[list[str]]) -> str:
    """Create a new Excel workbook with one sheet, a header row, and data rows.

    Args:
        path: Path to save the new .xlsx file.
        sheet_name: Name for the sheet.
        headers: Column header strings.
        rows: List of data rows; each cell is a string (numeric strings are stored as numbers).
    """
    file_path = _abs(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    sheet = wb.active
    sheet.title = sheet_name or "Sheet1"
    if headers:
        sheet.append(headers)
    for row in rows:
        sheet.append([_coerce(cell) for cell in row])
    wb.save(file_path)
    storage.log_operation("create_excel", str(file_path), sheet_name)
    os.startfile(file_path)
    storage.log_operation("open_in_app", str(file_path))
    return f"Created and opened {file_path}"


def write_excel_cell(path: str, sheet_name: str, cell: str, value: str) -> str:
    """Set a single cell's value in an Excel workbook.

    Args:
        path: Path to the .xlsx file.
        sheet_name: Sheet to write to.
        cell: Cell reference, e.g. "B3".
        value: Value to write into the cell, as a string (numeric strings are stored as numbers).
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    wb = load_workbook(file_path)
    sheet = wb[sheet_name] if sheet_name else wb.active
    sheet[cell] = _coerce(value)
    wb.save(file_path)
    storage.log_operation("write_excel_cell", str(file_path), f"{sheet.title}!{cell}={value}")
    return f"Set {sheet.title}!{cell} = {value} in {file_path}"


def write_excel_range(path: str, sheet_name: str, start_cell: str, rows: list[list[str]]) -> str:
    """Bulk-write a 2D list of values into an Excel sheet, starting at a given cell.

    Args:
        path: Path to the .xlsx file.
        sheet_name: Sheet to write to.
        start_cell: Top-left cell to start writing at, e.g. "A1".
        rows: 2D list of string values (numeric strings are stored as numbers).
    """
    file_path = _abs(path)
    if not file_path.is_file():
        return f"Error: file not found: {file_path}"
    wb = load_workbook(file_path)
    sheet = wb[sheet_name] if sheet_name else wb.active
    start_col, start_row = _split_cell_ref(start_cell)
    for r, row_data in enumerate(rows):
        for c, value in enumerate(row_data):
            sheet.cell(row=start_row + r, column=start_col + c, value=_coerce(value))
    wb.save(file_path)
    storage.log_operation("write_excel_range", str(file_path), f"{sheet.title}@{start_cell} ({len(rows)} rows)")
    return f"Wrote {len(rows)} row(s) into {sheet.title} starting at {start_cell} in {file_path}"


def _split_cell_ref(ref: str) -> tuple[int, int]:
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    col_letters, row = coordinate_from_string(ref)
    return column_index_from_string(col_letters), row


PLAIN_TOOLS = [
    list_office_files,
    open_in_app,
    read_text_file,
    read_docx,
    create_docx,
    edit_docx_text,
    append_docx_paragraph,
    add_docx_table,
    read_excel,
    create_excel,
    write_excel_cell,
    write_excel_range,
]

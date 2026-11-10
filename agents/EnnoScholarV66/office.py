"""Read Word and Excel as text with stable paragraph, table and row locators."""
from io import BytesIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile


OFFICE_EXTENSIONS = {".docx", ".xlsx", ".xls"}


def _word(data):
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError
    from docx.table import Table
    from lxml.etree import XMLSyntaxError

    try:
        document = Document(BytesIO(data))
    except (PackageNotFoundError, XMLSyntaxError, KeyError) as exc:
        raise ValueError("Le fichier Word DOCX est illisible ou protégé.") from exc
    for index, block in enumerate(document.iter_inner_content(), 1):
        if isinstance(block, Table):
            for number, row in enumerate(block.rows, 1):
                yield f"Tableau {index}, ligne {number} | " + " | ".join(cell.text for cell in row.cells)
        elif block.text.strip():
            yield f"Paragraphe {index} | {block.text}"


def _excel(data, extension):
    if extension == ".xlsx":
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException

        with BytesIO(data) as stream:
            try:
                workbook = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
            except (InvalidFileException, BadZipFile, KeyError, ParseError) as exc:
                raise ValueError("Le fichier Excel XLSX est illisible ou protégé.") from exc
            try:
                for sheet in workbook.worksheets:
                    yield f"Feuille « {sheet.title} »"
                    sheet.reset_dimensions()
                    for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                        yield _row(sheet.title, number, row)
            finally:
                workbook.close()
    else:
        import xlrd

        try:
            workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
        except (xlrd.XLRDError, OSError) as exc:
            raise ValueError("Le fichier Excel XLS est illisible ou protégé.") from exc
        try:
            for index in range(workbook.nsheets):
                sheet = workbook.sheet_by_index(index)
                yield f"Feuille « {sheet.name} »"
                for row in range(sheet.nrows):
                    yield _row(sheet.name, row + 1, sheet.row_values(row))
                workbook.unload_sheet(index)
        finally:
            workbook.release_resources()


def _row(sheet, number, values):
    if not any(value is not None and value != "" for value in values):
        return ""
    cells = " | ".join("" if value is None else str(value) for value in values).rstrip(" |")
    return f"Feuille « {sheet} », ligne {number} | {cells}"


def office_text(data, extension, max_chars, check):
    """Bound expansion before opening OOXML and retain whole readable rows."""
    if extension != ".xls":
        try:
            with ZipFile(BytesIO(data)) as archive:
                if sum(member.file_size for member in archive.infolist()) > 80 * 1024 * 1024:
                    raise ValueError("Document Office trop volumineux après décompression.")
        except BadZipFile as exc:
            raise ValueError("Le document Office est illisible ou protégé.") from exc
    lines, size = [], 0
    reader = _word(data) if extension == ".docx" else _excel(data, extension)
    try:
        for index, line in enumerate(reader):
            check()
            if index >= 50000 or size + len(line) + 1 > max_chars:
                return "\n".join(lines), False
            if line:
                lines.append(line)
                size += len(line) + 1
    except ParseError as exc:
        raise ValueError("Le contenu Office contient une section illisible.") from exc
    finally:
        reader.close()
    return "\n".join(lines), True

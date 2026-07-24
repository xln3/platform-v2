# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


def render_html(title: str, sections: Sequence[Mapping[str, object]]) -> bytes:
    content = "".join(
        f"<section><h2>{escape(str(section.get('title', '')))}</h2>"
        f"<p>{escape(str(section.get('body', '')))}</p></section>"
        for section in sections
    )
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><body><h1>{escape(title)}</h1>{content}</body></html>"
    ).encode()


def render_docx(title: str, sections: Sequence[Mapping[str, object]]) -> bytes:
    paragraphs = [
        f"<w:p><w:r><w:t>{_xml(title)}</w:t></w:r></w:p>",
        *[
            f"<w:p><w:r><w:t>{_xml(str(section.get('title', '')))}</w:t></w:r></w:p>"
            f"<w:p><w:r><w:t>{_xml(str(section.get('body', '')))}</w:t></w:r></w:p>"
            for section in sections
        ],
    ]
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
    )
    return _zip(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/></Relationships>'
            ),
            "word/document.xml": document,
        }
    )


def render_xlsx(rows: Sequence[Mapping[str, object]]) -> bytes:
    columns = sorted({key for row in rows for key in row})
    all_rows: list[list[object]] = [
        list(columns),
        *[[row.get(column, "") for column in columns] for row in rows],
    ]
    sheet_rows: list[str] = []
    for row_number, values in enumerate(all_rows, 1):
        cells = "".join(
            f'<c r="{_column_name(index)}{row_number}" t="inlineStr">'
            f"<is><t>{_xml(str(value))}</t></is></c>"
            for index, value in enumerate(values, 1)
        )
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    return _zip(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="xl/workbook.xml"/></Relationships>'
            ),
            "xl/workbook.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Metrics" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/></Relationships>'
            ),
            "xl/worksheets/sheet1.xml": sheet,
        }
    )


def render_pdf(title: str, sections: Sequence[Mapping[str, object]]) -> bytes:
    # Minimal standards-conforming PDF. Non-Latin report content is preserved in metadata JSON;
    # HTML/DOCX are the rich-text renderings until a shared font/rendering dependency is accepted.
    ascii_title = title.encode("ascii", errors="replace").decode()
    lines = [ascii_title, *[str(item.get("title", "")) for item in sections]]
    commands = ["BT /F1 12 Tf 72 760 Td"]
    for index, line in enumerate(lines):
        safe = line.encode("ascii", errors="replace").decode().replace("\\", "\\\\")
        safe = safe.replace("(", "\\(").replace(")", "\\)")
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({safe}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode()
    metadata = json.dumps({"title": title, "sections": list(sections)}, ensure_ascii=False).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Type /Metadata /Subtype /XML /Length {len(metadata)} >>\nstream\n".encode()
        + metadata
        + b"\nendstream",
    ]
    output = BytesIO()
    output.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return output.getvalue()


def _zip(files: Mapping[str, str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _xml(value: str) -> str:
    return escape(value, quote=True)


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name

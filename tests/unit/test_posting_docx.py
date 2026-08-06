from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from geo_platform.posting.docx import DocxInvalid, parse_docx


def build_docx(
    *, title: str = "测试标题", body: str = "第一段正文", with_image: bool = True
) -> bytes:
    image = (
        """
        <w:p><w:r><w:drawing>
          <a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                  r:embed="rId1"/>
        </w:drawing></w:r></w:p>
        """
        if with_image
        else ""
    )
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{title}</w:t></w:r></w:p>
        <w:p><w:r><w:t>{body}</w:t></w:r></w:p>
        {image}
      </w:body>
    </w:document>
    """
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        Target="media/image1.png"/>
    </Relationships>
    """
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        if with_image:
            archive.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nposting-image")
    return output.getvalue()


def test_parse_docx_extracts_title_text_and_inline_image() -> None:
    parsed = parse_docx(build_docx(), "图文稿件.docx")
    assert parsed.title == "测试标题"
    assert parsed.content_text == "测试标题\n\n第一段正文\n\n[图片1]"
    assert parsed.image_count == 1
    assert "<h1>测试标题</h1>" in parsed.content_html
    assert "<p>第一段正文</p>" in parsed.content_html
    assert 'src="data:image/png;base64,' in parsed.content_html
    assert len(parsed.sha256) == 64


def test_parse_docx_rejects_non_docx_and_path_traversal() -> None:
    with pytest.raises(DocxInvalid, match="docx_signature_invalid"):
        parse_docx(b"not a zip", "article.docx")

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("../word/document.xml", "<invalid/>")
    with pytest.raises(DocxInvalid, match="docx_archive_path_invalid"):
        parse_docx(output.getvalue(), "article.docx")

"""Small real files for the converters, made in code so nothing binary lives in the repo."""
import zipfile
from pathlib import Path

import pymupdf


def pdf(path: Path, text: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=12)
    doc.save(path)
    return path


def scanned_pdf(path: Path, text: str) -> Path:
    """A PDF whose only page is a picture of text, as "Print to PDF" and scanners make."""
    src = pymupdf.open()
    page = src.new_page(width=600, height=200)
    page.insert_text((30, 100), text, fontsize=28)
    png = page.get_pixmap(dpi=150).tobytes('png')
    doc = pymupdf.open()
    doc.new_page(width=600, height=200).insert_image(pymupdf.Rect(0, 0, 600, 200), stream=png)
    doc.save(path)
    return path


def docx(path: Path, heading: str, para: str) -> Path:
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = (f'<w:document xmlns:w="{ns}"><w:body>'
            f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{heading}</w:t></w:r></w:p>'
            f'<w:p><w:r><w:t>{para}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml',
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr('_rels/.rels',
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr('word/document.xml', body)
    return path


def xlsx(path: Path, rows: list[list]) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    wb.save(path)
    return path


def page(title: str, text: str) -> str:
    return (f'<html><head><title>{title}</title></head><body>'
            f'<nav>Home | Shop | Basket</nav><article><h1>{title}</h1><p>{text}</p></article>'
            f'<footer>Copyright</footer></body></html>')

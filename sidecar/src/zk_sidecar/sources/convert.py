"""Turn an original file into Markdown text for search, summaries and GraphRAG.

Choices from the Phase 0 spike (spike/REPORT.md):
- PDFs: PyMuPDF. MarkItDown's pdfminer glued words together in 16 of 19 papers.
- PDF pages with no text layer: RapidOCR. "Print to PDF" files draw letters as shapes.
- Web pages: trafilatura keeps the article and drops menus, shop links and footers.
- Word, Excel, PowerPoint: MarkItDown.
"""
from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: File types a source can be, by extension. The value is the source kind.
KINDS = {
    '.pdf': 'pdf',
    '.docx': 'docx',
    '.xlsx': 'xlsx',
    '.pptx': 'pptx',
    '.html': 'html',
    '.htm': 'html',
    '.md': 'text',
    '.txt': 'text',
}

#: A page with fewer words than this in its text layer gets OCR.
OCR_BELOW_WORDS = 20


@dataclass
class Converted:
    text: str
    title: str
    method: str


def kind_of(name: str) -> str | None:
    return KINDS.get(Path(name).suffix.lower())


@cache
def _ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _pdf(path: Path) -> Converted:
    import pymupdf

    pages, ocr_pages = [], 0
    with pymupdf.open(path) as doc:
        title = (doc.metadata or {}).get('title') or ''
        for page in doc:
            text = page.get_text()
            if len(text.split()) < OCR_BELOW_WORDS:
                result, _ = _ocr()(page.get_pixmap(dpi=200).tobytes('png'))
                ocr_text = '\n'.join(r[1] for r in (result or []))
                if len(ocr_text.split()) > len(text.split()):
                    text, ocr_pages = ocr_text, ocr_pages + 1
            pages.append(text.strip())
    method = f'pymupdf+ocr ({ocr_pages} {"page" if ocr_pages == 1 else "pages"})' if ocr_pages else 'pymupdf'
    return Converted('\n\n'.join(p for p in pages if p), title.strip(), method)


def _html(path: Path) -> Converted:
    import trafilatura

    html = path.read_text(errors='replace')
    text = trafilatura.extract(html, output_format='markdown', include_tables=True, include_links=False) or ''
    meta = trafilatura.extract_metadata(html)
    return Converted(text, (meta.title if meta and meta.title else '').strip(), 'trafilatura')


def _office(path: Path) -> Converted:
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(path))
    return Converted(result.text_content or '', (result.title or '').strip(), 'markitdown')


def convert(path: Path) -> Converted:
    kind = kind_of(path.name)
    if kind == 'pdf':
        return _pdf(path)
    if kind == 'html':
        return _html(path)
    if kind in ('docx', 'xlsx', 'pptx'):
        return _office(path)
    if kind == 'text':
        return Converted(path.read_text(errors='replace'), '', 'plain')
    raise ValueError(f'unsupported file type: {path.suffix or path.name}')

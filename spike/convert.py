"""Convert the spike corpus to Markdown. Records time and size per file.

PDFs: PyMuPDF. MarkItDown's pdfminer backend glued words together in 16 of 19 papers.
PDFs with no text layer (printed to PDF as vector shapes): RapidOCR on each page.
Web pages: trafilatura keeps the article and drops menus, shop links and footers.
Office files: MarkItDown.
"""
import glob, json, re, sys, time
from pathlib import Path
from markitdown import MarkItDown
import pymupdf
import trafilatura
from rapidocr_onnxruntime import RapidOCR

ocr = RapidOCR()

def pdf_text(path):
    """Text layer per page. Pages with almost no text get OCR."""
    pages, ocr_pages = [], 0
    for page in pymupdf.open(path):
        text = page.get_text()
        if len(text.split()) < 20:
            res, _ = ocr(page.get_pixmap(dpi=200).tobytes('png'))
            ocr_text = '\n'.join(r[1] for r in (res or []))
            if len(ocr_text.split()) > len(text.split()):
                text, ocr_pages = ocr_text, ocr_pages + 1
        pages.append(text)
    return '\n\n'.join(pages), f'pymupdf+ocr:{ocr_pages}' if ocr_pages else 'pymupdf'


ROOT = Path('/media/Books')
OUT = Path('corpus'); OUT.mkdir(exist_ok=True)
md = MarkItDown()
rows = []
for line in Path('corpus.txt').read_text().splitlines():
    if not line.strip() or line.startswith('#'):
        continue
    for src in sorted(glob.glob(str(ROOT / line))):
        p = Path(src)
        slug = re.sub(r'[^a-z0-9]+', '-', p.stem.lower()).strip('-')[:80]
        t = time.time()
        try:
            if p.suffix == '.pdf':
                text, how = pdf_text(str(p))
            elif p.suffix == '.html':
                text = trafilatura.extract(p.read_text(errors='replace'), output_format='markdown',
                                           include_tables=True, include_links=False) or ''
                how = 'trafilatura'
            else:
                text, how = md.convert(str(p)).text_content, 'markitdown'
            err = None
        except Exception as e:
            text, err, how = '', repr(e), None
        dt = time.time() - t
        (OUT / f'{slug}.txt').write_text(text)
        rows.append({'file': str(p.relative_to(ROOT)), 'slug': slug, 'kind': p.suffix[1:], 'via': how,
                     'words': len(text.split()), 'seconds': round(dt, 2), 'error': err})
        print(f"{rows[-1]['words']:7d} {dt:6.1f}s {p.suffix} {slug}" + (f'  ERROR {err}' if err else ''), flush=True)
json.dump(rows, open('convert.json', 'w'), indent=1)
print(len(rows), 'docs,', sum(r['words'] for r in rows), 'words')

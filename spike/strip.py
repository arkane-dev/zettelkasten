"""Copy the corpus with reference lists and appendices cut from the papers.

Cuts at the first of these:
- a heading line past 20% of the paper: References, Bibliography, Appendix, Appendices (optionally numbered)
- past 40%, the start of a numbered reference list: "[1]" or "1." followed by "[2]" or "2." within 12 lines,
  with a year or "arXiv" in the first lines. Numbered lists in the body don't have those.
Other domains are copied as is. Prints one line per paper so every cut can be checked.

Usage: python strip.py <src_dir> <dst_dir>
"""
import json, re, sys
from pathlib import Path

HEADING = re.compile(r'^\s*(?:(?:[A-Z]|\d+)\.?\s+)?(references|bibliography|appendix|appendices)\s*$', re.I)
REF_START = re.compile(r'^\s*(\[1\]|1\.)\s+\S')
REF_NEXT = re.compile(r'^\s*(\[2\]|2\.)\s+\S')
CITATION = re.compile(r'\b(19|20)\d\d\b|arXiv', re.I)

domain = {r['slug']: r['file'].split('/')[0] for r in json.load(open('convert.json'))}


def cut_at(lines):
    for i in range(int(len(lines) * 0.2), len(lines)):
        if HEADING.match(lines[i]):
            return i, lines[i].strip()
        if (i >= len(lines) * 0.4 and REF_START.match(lines[i])
                and any(REF_NEXT.match(l) for l in lines[i + 1:i + 12])
                and CITATION.search(' '.join(lines[i:i + 4]))):
            return i, lines[i].strip()[:50]
    return None, None


src, dst = Path(sys.argv[1]), Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
for f in sorted(src.glob('*.txt')):
    text = f.read_text()
    if domain.get(f.stem) == 'papers':
        lines = text.splitlines()
        i, why = cut_at(lines)
        if i is not None:
            text = '\n'.join(lines[:i]) + '\n'
        kept = len(text.splitlines())
        print(f'{f.stem[:60]:60} {kept:5}/{len(lines):5} lines  cut at: {why}')
    (dst / f.name).write_text(text)

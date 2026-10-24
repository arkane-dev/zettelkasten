"""What the graph reads: the text of every source, and your own notes and notebooks.

It never reads the community notes it writes, so the index doesn't feed on itself.
Each document links back to one note: a source's note, or the note itself.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..sources.store import CONTENT, SourceStore, split_frontmatter
from ..text import strip_references

SKIP = ('.zk', '.obsidian', '.trash', 'Communities', 'Sources')

FENCE = re.compile(r'^(`{3,}|~{3,}).*?^\1[ \t]*$', re.S | re.M)
COMMENTED = re.compile(r'<!-- zk:(output|ai) -->.*?<!-- /zk:\1 -->', re.S)
COMMENT = re.compile(r'<!--.*?-->|%%.*?%%', re.S)
EMBED = re.compile(r'!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)')
WIKILINK = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]')
MDLINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')

@dataclass(frozen=True)
class Doc:
    #: The note to link to, relative to the vault.
    path: str
    title: str
    kind: str  # source, note, notebook
    text: str


def clean(text: str) -> str:
    """Prose only: no code, generated blocks, comments, embeds or link syntax."""
    text = COMMENTED.sub('', text)
    text = FENCE.sub('', text)
    text = COMMENT.sub('', text)
    text = EMBED.sub('', text)
    text = WIKILINK.sub(lambda m: m.group(2) or Path(m.group(1)).name, text)
    text = MDLINK.sub(r'\1', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def notes_section(note: str) -> str:
    """The `## Notes` section of a note: the part you write."""
    m = re.search(r'^## Notes[ \t]*$', note, re.M)
    if not m:
        return ''
    rest = note[m.end():]
    nxt = re.search(r'^## ', rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def _rel(vault: Path, p: Path) -> str:
    return p.relative_to(vault).as_posix()


def _skipped(rel: str) -> bool:
    top = rel.split('/', 1)[0]
    return top in SKIP or any(part.startswith('.') for part in rel.split('/')[:-1])


def collect(vault: Path) -> list[Doc]:
    docs: list[Doc] = []
    store = SourceStore(vault)
    for src in store.all():
        content = src.folder / CONTENT
        text = content.read_text(errors='replace') if content.exists() else ''
        _, body = split_frontmatter(text)
        if src.meta.get('kind') == 'pdf':
            body = strip_references(body)
        mine = notes_section(src.note.read_text(errors='replace')) if src.note.exists() else ''
        full = clean(body + ('\n\n' + mine if mine else ''))
        if full:
            docs.append(Doc(_rel(vault, src.note), str(src.meta.get('title') or src.slug), 'source', full))

    for p in sorted(vault.rglob('*.md')):
        rel = _rel(vault, p)
        if _skipped(rel):
            continue
        meta, body = split_frontmatter(p.read_text(errors='replace'))
        if meta.get('type') in ('community', 'source'):
            continue
        if text := clean(body):
            docs.append(Doc(rel, p.stem, 'note', text))

    for p in sorted(vault.rglob('*.ipynb')):
        rel = _rel(vault, p)
        if _skipped(rel):
            continue
        try:
            cells = json.loads(p.read_text(errors='replace')).get('cells', [])
        except (json.JSONDecodeError, AttributeError):
            continue
        md = [''.join(c.get('source', '')) for c in cells if c.get('cell_type') == 'markdown']
        if text := clean('\n\n'.join(md)):
            docs.append(Doc(rel, p.stem, 'notebook', text))
    return docs

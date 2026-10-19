"""Source folders in the vault.

    Sources/<slug>/
      <Title>.md       the note: reader, summary, notes. Named by title, so [[Title]] links read well.
      original.pdf     or .docx, .xlsx, … or snapshot-2026-10-19.html for web pages
      content.md       extracted text, for search and GraphRAG

Software writes the frontmatter, the reader and the summary between the zk:ai
markers. It never touches anything under ## Notes.
"""
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

AI_START = '<!-- zk:ai -->'
AI_END = '<!-- /zk:ai -->'
AI_BLOCK = re.compile(re.escape(AI_START) + r'.*?' + re.escape(AI_END), re.S)
SLUG_MAX = 60
NAME_MAX = 100
#: Characters Obsidian can't use in a file name or a link.
NOT_IN_NAMES = re.compile(r'[\\/:*?"<>|#^\[\]]')
CONTENT = 'content.md'


def slugify(title: str) -> str:
    s = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s).strip('-').lower()
    s = s[:SLUG_MAX].rstrip('-')
    return s or 'source'


def clean_title(title: str) -> str:
    return re.sub(r'\s+', ' ', title).strip()


def note_name(title: str) -> str:
    """A file name for the note that Obsidian can link to."""
    name = clean_title(NOT_IN_NAMES.sub(' ', title)).strip('. ')
    name = name[:NAME_MAX].rstrip('. ') or 'Untitled'
    return f'{name} (source)' if f'{name.lower()}.md' == CONTENT else name


def split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end >= 0:
            meta = yaml.safe_load(text[4:end]) or {}
            if isinstance(meta, dict):
                return meta, text[end + 5:]
    return {}, text


def join_frontmatter(meta: dict, body: str) -> str:
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    return f'---\n{head}---\n{body}'


def ai_block(text: str) -> str:
    return f'{AI_START}\n{text.strip()}\n{AI_END}'


@dataclass
class Source:
    slug: str
    folder: Path
    meta: dict
    note: Path


class SourceStore:
    def __init__(self, vault: Path):
        self.vault = vault
        self.root = vault / 'Sources'

    def rel(self, path: Path) -> str:
        """Vault-relative path with forward slashes, as Obsidian links use."""
        return path.relative_to(self.vault).as_posix()

    def new_folder(self, title: str) -> tuple[str, Path]:
        base = slugify(title)
        slug, n = base, 2
        while (self.root / slug).exists():
            slug, n = f'{base}-{n}', n + 1
        folder = self.root / slug
        folder.mkdir(parents=True)
        return slug, folder

    def _load(self, folder: Path) -> Source | None:
        """The source note in a folder: the Markdown file with `type: source`."""
        for note in sorted(folder.glob('*.md')):
            if note.name == CONTENT:
                continue
            meta, _ = split_frontmatter(note.read_text(encoding='utf-8'))
            if meta.get('type') == 'source':
                return Source(folder.name, folder, meta, note)
        return None

    def all(self) -> list[Source]:
        if not self.root.is_dir():
            return []
        folders = sorted(p for p in self.root.iterdir() if p.is_dir())
        return [s for f in folders if (s := self._load(f))]

    def get(self, slug: str) -> Source:
        folder = self.root / slug
        if '/' in slug or '\\' in slug or slug.startswith('.') or not folder.is_dir():
            raise KeyError(slug)
        if not (src := self._load(folder)):
            raise KeyError(slug)
        return src

    def find(self, sha256: str | None = None, url: str | None = None) -> Source | None:
        for s in self.all():
            if sha256 and s.meta.get('sha256') == sha256:
                return s
            if url and s.meta.get('url') == url:
                return s
        return None

    def new_note(self, slug: str, folder: Path, meta: dict, reader: str, summary: str) -> Source:
        src = Source(slug, folder, meta, folder / f'{note_name(meta["title"])}.md')
        body = '\n'.join([
            reader, '',
            '## Summary', '',
            ai_block(summary), '',
            '## Notes', '', '',
        ])
        src.note.write_text(join_frontmatter(src.meta, body), encoding='utf-8')
        return src

    def update(self, src: Source, meta: dict | None = None, summary: str | None = None) -> bool:
        """Change frontmatter keys and the summary. Other keys and all other text stay as they are.

        Returns False when the summary markers are gone, so the summary wasn't written.
        """
        for _ in range(3):
            before = src.note.stat().st_mtime_ns
            old_meta, body = split_frontmatter(src.note.read_text(encoding='utf-8'))
            new_meta = {**old_meta, **(meta or {})}
            wrote_summary = True
            if summary is not None:
                if AI_BLOCK.search(body):
                    body = AI_BLOCK.sub(lambda _: ai_block(summary), body, count=1)
                else:
                    wrote_summary = False
            # Don't overwrite an edit that landed while we worked. Read again instead.
            if src.note.stat().st_mtime_ns != before:
                continue
            tmp = src.note.with_suffix('.md.zk-tmp')
            tmp.write_text(join_frontmatter(new_meta, body), encoding='utf-8')
            os.replace(tmp, src.note)
            src.meta = new_meta
            return wrote_summary
        raise RuntimeError(f'{self.rel(src.note)} kept changing. Try again.')

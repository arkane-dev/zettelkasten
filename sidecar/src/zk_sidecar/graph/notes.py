"""Community notes in Communities/L<level>/.

Software writes the frontmatter and the part between the zk:ai markers. `## Notes` is yours.
A community that disappears keeps its note if you wrote in it: the note moves to _retired.
"""
import os
import re
import shutil
from datetime import date
from pathlib import Path

from ..sources.store import AI_BLOCK, AI_END, AI_START, join_frontmatter, note_name, split_frontmatter

FOLDER = 'Communities'
RETIRED = f'{FOLDER}/_retired'
PENDING = '_Report pending. The model was not available. "Rebuild communities" tries again._'
#: Frontmatter keys software writes. Any other key is yours and stays.
OWNED = {'type', 'level', 'zk_id', 'title', 'parent', 'children', 'sources', 'updated', 'provisional'}
CITE = re.compile(r'\[S(\d+(?:\s*[,;]\s*S?\d+)*)\]')


def link(path: str, label: str) -> str:
    target = path[:-3] if path.endswith('.md') else path
    label = label.replace('|', '-').replace(']', ')').replace('[', '(')
    return f'[[{target}|{label}]]'


def cite(text: str, docs: list[str]) -> str:
    """[S1, S3] → links to those sources, labelled by number."""
    def repl(m: re.Match) -> str:
        nums = [int(n) for n in re.findall(r'\d+', m.group(1))]
        links = [link(docs[n - 1], str(n)) for n in nums if 0 < n <= len(docs)]
        return f'({", ".join(links)})' if links else ''
    return CITE.sub(repl, text)


def has_notes(text: str) -> bool:
    m = re.search(r'^## Notes[ \t]*$', text, re.M)
    return bool(m and text[m.end():].strip())


def find_notes(vault: Path) -> dict[str, Path]:
    """Community notes by zk_id, wherever you moved them (except _retired)."""
    out: dict[str, Path] = {}
    root = vault / FOLDER
    if not root.is_dir():
        return out
    for p in root.rglob('*.md'):
        if p.relative_to(vault).as_posix().startswith(RETIRED + '/'):
            continue
        meta, _ = split_frontmatter(p.read_text(errors='replace'))
        if meta.get('type') == 'community' and meta.get('zk_id'):
            out[str(meta['zk_id'])] = p
    return out


def body(c: dict, by_id: dict[str, dict], doc_title: dict[str, str]) -> str:
    """The generated part of a community note."""
    lines: list[str] = []
    report = c.get('report')
    if report:
        docs = c.get('report_docs', [])
        lines += ['## Summary', '', cite(report['summary'], docs), '']
        if report.get('findings'):
            lines += ['## Findings', '']
            for f in report['findings']:
                lines += [f'### {f["summary"].strip()}', '', cite(f['explanation'].strip(), docs), '']
    else:
        lines += ['## Summary', '', PENDING, '']
    if c.get('key'):
        lines += ['## Key ideas', '', ' · '.join(c['key']), '']
    if c.get('docs'):
        lines += ['## Sources', '']
        for path, n in c['docs'][:15]:
            lines.append(f'- {link(path, doc_title.get(path, Path(path).stem))} ({n} {"passage" if n == 1 else "passages"})')
        if len(c['docs']) > 15:
            lines.append(f'- and {len(c["docs"]) - 15} more')
        lines.append('')
    related = []
    if (p := by_id.get(c.get('parent') or '')) and p.get('note'):
        related.append(f'Part of {link(p["note"], p["title"])}.')
    kids = [by_id[k] for k in c.get('children', []) if by_id.get(k, {}).get('note')]
    if kids:
        related.append('Sub-communities: ' + ' · '.join(link(k['note'], k['title']) for k in kids))
    if related:
        lines += ['## Related', '', *related, '']
    return '\n'.join(lines).rstrip()


def frontmatter(c: dict, by_id: dict[str, dict], today: str) -> dict:
    meta = {'type': 'community', 'level': c['level'], 'zk_id': c['zk_id'], 'title': c['title']}
    if (p := by_id.get(c.get('parent') or '')) and p.get('note'):
        meta['parent'] = link(p['note'], p['title'])
    kids = [by_id[k] for k in c.get('children', []) if by_id.get(k, {}).get('note')]
    if kids:
        meta['children'] = [link(k['note'], k['title']) for k in kids]
    meta['sources'] = len(c.get('docs', []))
    meta['updated'] = today
    if not c.get('report'):
        meta['provisional'] = True
    return meta


def place(vault: Path, c: dict, existing: Path | None) -> Path:
    """Where the note lives. An existing note stays put: renames go through Obsidian, which fixes links."""
    return existing or unique(vault / FOLDER / f'L{c["level"]}', note_name(c['title']))


def wants_rename(path: str, title: str) -> bool:
    """True if the file name no longer matches the title. "Title 2" style names still match."""
    stem, want = Path(path).stem, note_name(title)
    return stem != want and not re.fullmatch(re.escape(want) + r' \d+', stem)


def unique(folder: Path, name: str, current: Path | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f'{name}.md'
    i = 2
    while p.exists() and p != current:
        p = folder / f'{name} {i}.md'
        i += 1
    return p


def write(path: Path, meta: dict, generated: str) -> None:
    """Update the frontmatter and the zk:ai part. Anything else in the note stays as it is."""
    block = f'{AI_START}\n{generated}\n{AI_END}'
    if path.exists():
        old_meta, text = split_frontmatter(path.read_text(errors='replace'))
        keep = {k: v for k, v in old_meta.items() if k not in OWNED}  # your own properties stay
        text = AI_BLOCK.sub(lambda _: block, text, count=1) if AI_BLOCK.search(text) else f'{block}\n\n{text.lstrip()}'
        meta = {**meta, **keep}
    else:
        text = f'{block}\n\n## Notes\n\n'
    tmp = path.with_suffix('.zk-tmp')
    tmp.write_text(join_frontmatter(meta, text))
    os.replace(tmp, path)


def retire(vault: Path, path: Path, today: str | None = None) -> Path | None:
    """A community that's gone. Keep the note if you wrote in it, else delete it."""
    if not path.exists():
        return None
    meta, text = split_frontmatter(path.read_text(errors='replace'))
    if not has_notes(text):
        path.unlink()
        return None
    dest = unique(vault / RETIRED, path.stem)
    meta['retired'] = today or date.today().isoformat()
    path.write_text(join_frontmatter(meta, text))
    shutil.move(path, dest)
    return dest

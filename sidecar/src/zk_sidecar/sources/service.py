"""Add, recapture and summarise sources. Each method runs inside a background job."""
import hashlib
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..jobs import Job
from ..summarise import Summariser
from .capture import check_url
from .convert import Converted, convert, kind_of
from .store import CONTENT, Source, SourceStore, clean_title

PENDING = '_Summary pending._'
READER_BLOCK = '```zk-reader\n```'

Capture = Callable[[str, Path], None]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


class SourceService:
    def __init__(self, store: SourceStore, summariser: Callable[[], Summariser], capture: Capture,
                 now: Callable[[], datetime] = datetime.now):
        self.store = store
        self.summariser = summariser
        self.capture = capture
        self.now = now

    # Adding

    def add_file(self, job: Job, name: str, data: bytes) -> None:
        ext = Path(name).suffix.lower()
        digest = hashlib.sha256(data).hexdigest()
        if existing := self.store.find(sha256=digest):
            self._duplicate(job, existing)
            return
        title = clean_title(Path(name).stem.replace('_', ' ')) or 'Untitled'
        slug, folder = self.store.new_folder(title)
        original = folder / f'original{ext}'
        original.write_bytes(data)

        job.step = 'converting'
        conv = self._convert(original)
        meta = {
            'type': 'source',
            'kind': kind_of(name),
            'title': title,
            'captured': self._today(),
            'original': original.name,
            'sha256': digest,
            'converted_with': conv.method,
        }
        self._finish_new(job, slug, folder, meta, conv, self._reader(meta['kind'], original))

    def add_url(self, job: Job, url: str) -> None:
        url = check_url(url)
        if existing := self.store.find(url=url):
            self._duplicate(job, existing)
            return
        with tempfile.TemporaryDirectory(dir=self._tmp_root()) as tmp:
            snap = Path(tmp) / 'page.html'
            job.step = 'capturing'
            self.capture(url, snap)
            job.step = 'converting'
            conv = self._convert(snap)
            title = clean_title(conv.title) or url.split('://', 1)[1].rstrip('/')
            slug, folder = self.store.new_folder(title)
            name = self._snapshot_name(folder)
            shutil.move(snap, folder / name)
        meta = {
            'type': 'source',
            'kind': 'web',
            'title': title,
            'url': url,
            'captured': self._today(),
            'original': name,
            'snapshots': [name],
            'sha256': sha256(folder / name),
            'converted_with': conv.method,
        }
        reader = f'Saved from <{url}>\n\n{READER_BLOCK}'
        self._finish_new(job, slug, folder, meta, conv, reader)

    # Changing

    def recapture(self, job: Job, slug: str) -> None:
        src = self.store.get(slug)
        job.note = self.store.rel(src.note)
        if src.meta.get('kind') != 'web' or not src.meta.get('url'):
            raise ValueError('Only web pages can be captured again.')
        with tempfile.TemporaryDirectory(dir=self._tmp_root()) as tmp:
            snap = Path(tmp) / 'page.html'
            job.step = 'capturing'
            self.capture(src.meta['url'], snap)
            job.step = 'converting'
            conv = self._convert(snap)
            if conv.text.strip() == self._content(src).strip():
                job.unchanged = True
                return
            # A new dated snapshot. Old snapshots are never overwritten.
            name = self._snapshot_name(src.folder)
            shutil.move(snap, src.folder / name)
        (src.folder / CONTENT).write_text(conv.text, encoding='utf-8')
        snapshots = [*src.meta.get('snapshots', []), name]
        self.store.update(src, meta={
            'original': name,
            'snapshots': snapshots,
            'sha256': sha256(src.folder / name),
            'recaptured': self._today(),
            'converted_with': conv.method,
        }, summary=PENDING)
        self._summarise(job, src, conv.text)

    def resummarise(self, job: Job, slug: str) -> None:
        src = self.store.get(slug)
        job.note = self.store.rel(src.note)
        self._summarise(job, src, self._content(src))

    # Helpers

    def _finish_new(self, job: Job, slug: str, folder: Path, meta: dict, conv: Converted, reader: str) -> None:
        (folder / CONTENT).write_text(conv.text, encoding='utf-8')
        src = self.store.new_note(slug, folder, meta, reader, PENDING)
        job.note = self.store.rel(src.note)
        if conv.method.startswith('failed'):
            job.warning = f'Could not extract text: {conv.method}'
        self._summarise(job, src, conv.text)

    def _summarise(self, job: Job, src: Source, text: str) -> None:
        job.step = 'summarising'
        try:
            summary = self.summariser().summarise(src.meta.get('title', src.slug), text)
        except Exception as e:  # noqa: BLE001 - the source stays, only the summary failed
            job.warning = f'Summary failed: {e}'
            summary = f'_Summary failed: {e}. Run "Summarise this source again" to retry._'
        if not self.store.update(src, summary=summary):
            job.warning = 'The summary markers are gone from the note, so the summary was not written.'

    def _duplicate(self, job: Job, existing: Source) -> None:
        job.note = self.store.rel(existing.note)
        job.duplicate = True

    def _convert(self, path: Path) -> Converted:
        try:
            return convert(path)
        except Exception as e:  # noqa: BLE001 - keep the original even if conversion fails
            return Converted('', '', f'failed: {e}')

    def _reader(self, kind: str, original: Path) -> str:
        # The link line comes first. A new note opens with the cursor on line one,
        # and Live Preview shows raw source on the cursor's line.
        link = f'Original: [[{self.store.rel(original)}|{original.name}]]'
        if kind == 'pdf':
            return f'{link}\n\n![[{self.store.rel(original)}]]'
        if kind in ('docx', 'xlsx', 'html'):
            return f'{link}\n\n{READER_BLOCK}'
        return link

    def _content(self, src: Source) -> str:
        path = src.folder / CONTENT
        return path.read_text(encoding='utf-8') if path.exists() else ''

    def _snapshot_name(self, folder: Path) -> str:
        now = self.now()
        name = f'snapshot-{now:%Y-%m-%d}.html'
        if (folder / name).exists():
            name = f'snapshot-{now:%Y-%m-%d-%H%M%S}.html'
        return name

    def _today(self) -> str:
        return self.now().date().isoformat()

    def _tmp_root(self) -> Path:
        root = self.store.vault / '.zk' / 'tmp'
        root.mkdir(parents=True, exist_ok=True)
        return root

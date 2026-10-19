from datetime import datetime
from pathlib import Path

import pytest
import yaml

from zk_sidecar.jobs import Job
from zk_sidecar.sources.convert import convert
from zk_sidecar.sources.service import PENDING, SourceService
from zk_sidecar.sources.store import AI_END, AI_START, SourceStore, slugify, split_frontmatter
from zk_sidecar.summarise import Summariser

from . import fixtures

ARTICLE = ' '.join(['Damson jam needs equal weights of fruit and sugar.'] * 12)


class FakeLLM:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, system: str, user: str) -> str:
        if self.fail:
            raise ConnectionError('Ollama is not running')
        self.calls.append((system, user))
        return f'<think>hmm</think>Summary number {len(self.calls)}.'


class Clock:
    def __init__(self, when: datetime):
        self.when = when

    def __call__(self) -> datetime:
        return self.when


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 19, 9, 30))


@pytest.fixture
def pages() -> dict[str, str]:
    """What the fake web serves, by URL."""
    return {}


@pytest.fixture
def service(tmp_path: Path, llm: FakeLLM, clock: Clock, pages: dict[str, str]) -> SourceService:
    def capture(url: str, out: Path) -> None:
        if url not in pages:
            raise RuntimeError('SingleFile failed: 404')
        out.write_text(pages[url])

    return SourceService(SourceStore(tmp_path), lambda: Summariser(llm, 8192), capture, now=clock)


def run(fn, *args) -> Job:
    job = Job(id='t', action='test')
    fn(job, *args)
    return job


def read_note(vault: Path, job: Job) -> tuple[dict, str]:
    return split_frontmatter((vault / job.note).read_text())


# Store


@pytest.mark.parametrize('title, slug', [
    ('ReAct: Synergizing Reasoning and Acting', 'react-synergizing-reasoning-and-acting'),
    ('Crème brûlée', 'creme-brulee'),
    ('川菜', 'source'),
    ('x' * 100, 'x' * 60),
])
def test_slugify(title: str, slug: str):
    assert slugify(title) == slug


def test_update_keeps_notes_and_user_keys(tmp_path: Path, service: SourceService):
    job = run(service.add_file, 'paper.txt', b'Some text.')
    note = tmp_path / job.note
    note.write_text(note.read_text().replace('type: source', 'type: source\ntags: [mine]')
                    + 'My own thoughts.\n')
    src = service.store.get('paper')
    assert service.store.update(src, meta={'kind': 'text'}, summary='New summary.')
    meta, body = split_frontmatter(note.read_text())
    assert meta['tags'] == ['mine']
    assert f'{AI_START}\nNew summary.\n{AI_END}' in body
    assert body.endswith('## Notes\n\nMy own thoughts.\n')


def test_update_reports_missing_markers(tmp_path: Path, service: SourceService):
    job = run(service.add_file, 'paper.txt', b'Some text.')
    note = tmp_path / job.note
    note.write_text(note.read_text().replace(AI_START, ''))
    assert service.store.update(service.store.get('paper'), summary='x') is False


def test_get_rejects_paths(service: SourceService):
    with pytest.raises(KeyError):
        service.store.get('../secret')


# Converters


def test_pdf(tmp_path: Path):
    c = convert(fixtures.pdf(tmp_path / 'a.pdf', 'Hierarchical reasoning model'))
    assert 'Hierarchical reasoning model' in c.text
    assert c.method == 'pymupdf'


def test_scanned_pdf_gets_ocr(tmp_path: Path):
    c = convert(fixtures.scanned_pdf(tmp_path / 'scan.pdf', 'Mapo tofu with Sichuan pepper'))
    assert 'Sichuan' in c.text
    assert c.method == 'pymupdf+ocr (1 page)'


def test_docx(tmp_path: Path):
    c = convert(fixtures.docx(tmp_path / 'a.docx', 'Worbla basics', 'Heat it to shape it.'))
    assert '# Worbla basics' in c.text
    assert 'Heat it to shape it.' in c.text


def test_xlsx(tmp_path: Path):
    c = convert(fixtures.xlsx(tmp_path / 'a.xlsx', [['GPU', 'VRAM'], ['RTX 5090', 32]]))
    assert 'RTX 5090' in c.text


def test_html_keeps_the_article_only(tmp_path: Path):
    p = tmp_path / 'a.html'
    p.write_text(fixtures.page('Damson Jam', ARTICLE))
    c = convert(p)
    assert c.title == 'Damson Jam'
    assert 'equal weights' in c.text
    assert 'Basket' not in c.text


# Adding files


def test_add_pdf(tmp_path: Path, service: SourceService, llm: FakeLLM):
    data = fixtures.pdf(tmp_path / 'in.pdf', 'Prompt caching cuts latency.').read_bytes()
    job = run(service.add_file, 'Prompt_Cache.pdf', data)

    assert job.note == 'Sources/prompt-cache/Prompt Cache.md'
    folder = tmp_path / 'Sources/prompt-cache'
    assert (folder / 'original.pdf').read_bytes() == data
    assert 'Prompt caching cuts latency.' in (folder / 'content.md').read_text()

    meta, body = read_note(tmp_path, job)
    assert meta['type'] == 'source'
    assert meta['kind'] == 'pdf'
    assert meta['title'] == 'Prompt Cache'
    assert meta['captured'] == '2026-10-19'
    assert len(meta['sha256']) == 64
    assert body.startswith('Original: [[Sources/prompt-cache/original.pdf|original.pdf]]\n\n![[Sources/prompt-cache/original.pdf]]')
    assert 'Summary number 1.' in body
    assert '<think>' not in body
    assert len(llm.calls) == 1


def test_same_file_twice_is_a_duplicate(tmp_path: Path, service: SourceService, llm: FakeLLM):
    first = run(service.add_file, 'a.txt', b'Same bytes.')
    second = run(service.add_file, 'renamed.txt', b'Same bytes.')
    assert second.duplicate
    assert second.note == first.note
    assert len(list((tmp_path / 'Sources').iterdir())) == 1


def test_same_title_gets_a_new_folder(service: SourceService):
    assert run(service.add_file, 'notes.txt', b'one').note == 'Sources/notes/notes.md'
    assert run(service.add_file, 'notes.txt', b'two').note == 'Sources/notes-2/notes.md'


def test_docx_uses_the_reader_block(tmp_path: Path, service: SourceService):
    data = fixtures.docx(tmp_path / 'in.docx', 'Armor', 'Foam and heat.').read_bytes()
    _, body = read_note(tmp_path, run(service.add_file, 'armor.docx', data))
    assert '```zk-reader\n```' in body


def test_summary_failure_keeps_the_source(tmp_path: Path, clock: Clock):
    svc = SourceService(SourceStore(tmp_path), lambda: Summariser(FakeLLM(fail=True), 8192),
                        lambda u, o: None, now=clock)
    job = run(svc.add_file, 'a.txt', b'Text.')
    assert 'Ollama is not running' in job.warning
    _, body = read_note(tmp_path, job)
    assert 'Summary failed' in body
    assert (tmp_path / 'Sources/a/original.txt').exists()


def test_conversion_failure_keeps_the_original(tmp_path: Path, service: SourceService):
    job = run(service.add_file, 'broken.pdf', b'not a pdf')
    assert 'Could not extract text' in job.warning
    assert (tmp_path / 'Sources/broken/original.pdf').read_bytes() == b'not a pdf'
    _, body = read_note(tmp_path, job)
    assert 'No text could be extracted' in body


# Web pages


URL = 'https://oakden.co.uk/damson-jam/'


def test_add_url(tmp_path: Path, service: SourceService, pages: dict[str, str]):
    pages[URL] = fixtures.page('Damson Jam', ARTICLE)
    job = run(service.add_url, URL)
    assert job.note == 'Sources/damson-jam/Damson Jam.md'
    meta, body = read_note(tmp_path, job)
    assert meta['kind'] == 'web'
    assert meta['url'] == URL
    assert meta['snapshots'] == ['snapshot-2026-10-19.html']
    assert meta['original'] == 'snapshot-2026-10-19.html'
    assert f'Saved from <{URL}>' in body
    assert '```zk-reader\n```' in body
    assert not list((tmp_path / '.zk/tmp').iterdir())


def test_same_url_twice_is_a_duplicate(service: SourceService, pages: dict[str, str]):
    pages[URL] = fixtures.page('Damson Jam', ARTICLE)
    first = run(service.add_url, URL)
    assert run(service.add_url, URL).duplicate
    assert first.note


def test_failed_capture_leaves_nothing(tmp_path: Path, service: SourceService):
    with pytest.raises(RuntimeError):
        run(service.add_url, 'https://example.com/gone')
    assert not (tmp_path / 'Sources').exists()


def test_recapture_unchanged_adds_nothing(tmp_path: Path, service: SourceService, pages, clock):
    pages[URL] = fixtures.page('Damson Jam', ARTICLE)
    run(service.add_url, URL)
    clock.when = datetime(2026, 11, 1)
    job = run(service.recapture, 'damson-jam')
    assert job.unchanged
    assert sorted(p.name for p in (tmp_path / 'Sources/damson-jam').glob('snapshot-*')) == ['snapshot-2026-10-19.html']


def test_recapture_changed_adds_a_snapshot(tmp_path: Path, service: SourceService, pages, clock, llm):
    pages[URL] = fixtures.page('Damson Jam', ARTICLE)
    run(service.add_url, URL)
    note = tmp_path / 'Sources/damson-jam/Damson Jam.md'
    note.write_text(note.read_text() + 'Use less sugar next time.\n')

    pages[URL] = fixtures.page('Damson Jam', ARTICLE + ' ' + ' '.join(['Add lemon juice to help it set.'] * 6))
    clock.when = datetime(2026, 11, 1)
    job = run(service.recapture, 'damson-jam')

    assert not job.unchanged
    meta, body = split_frontmatter(note.read_text())
    assert meta['snapshots'] == ['snapshot-2026-10-19.html', 'snapshot-2026-11-01.html']
    assert meta['original'] == 'snapshot-2026-11-01.html'
    assert meta['captured'] == '2026-10-19'
    assert meta['recaptured'] == '2026-11-01'
    assert (tmp_path / 'Sources/damson-jam/snapshot-2026-10-19.html').exists()
    assert 'lemon juice' in (tmp_path / 'Sources/damson-jam/content.md').read_text()
    assert 'Summary number 2.' in body
    assert body.endswith('Use less sugar next time.\n')


def test_recapture_same_day_keeps_both(tmp_path: Path, service: SourceService, pages, clock):
    pages[URL] = fixtures.page('Damson Jam', ARTICLE)
    run(service.add_url, URL)
    pages[URL] = fixtures.page('Damson Jam', ARTICLE + ' ' + ' '.join(['Stones float, skim them off.'] * 6))
    clock.when = datetime(2026, 10, 19, 15, 0, 5)
    run(service.recapture, 'damson-jam')
    names = sorted(p.name for p in (tmp_path / 'Sources/damson-jam').glob('snapshot-*'))
    assert names == ['snapshot-2026-10-19-150005.html', 'snapshot-2026-10-19.html']


def test_recapture_needs_a_web_page(service: SourceService):
    run(service.add_file, 'a.txt', b'x')
    with pytest.raises(ValueError):
        run(service.recapture, 'a')


def test_resummarise(tmp_path: Path, service: SourceService, llm: FakeLLM):
    job = run(service.add_file, 'a.txt', b'Some text.')
    run(service.resummarise, 'a')
    _, body = read_note(tmp_path, job)
    assert 'Summary number 2.' in body
    assert PENDING not in body


def test_note_is_named_by_title(tmp_path: Path, service: SourceService):
    job = run(service.add_file, 'ReAct_ Synergizing Reasoning.txt', b'x')
    assert job.note == 'Sources/react-synergizing-reasoning/ReAct Synergizing Reasoning.md'
    meta, body = read_note(tmp_path, job)
    assert meta['title'] == 'ReAct Synergizing Reasoning'
    assert not body.startswith('#')


def test_a_source_called_content_keeps_its_text(tmp_path: Path, service: SourceService):
    job = run(service.add_file, 'content.txt', b'the text')
    assert job.note == 'Sources/content/content (source).md'
    assert (tmp_path / 'Sources/content/content.md').read_text() == 'the text'
    assert service.store.get('content').note.name == 'content (source).md'


def test_note_frontmatter_is_valid_yaml_with_odd_titles(tmp_path: Path, service: SourceService):
    job = run(service.add_file, 'Q: "why" #1 [draft].txt', b'x')
    meta, _ = read_note(tmp_path, job)
    assert meta['title'] == 'Q: "why" #1 [draft]'
    head = (tmp_path / job.note).read_text().split('---\n')[1]
    assert yaml.safe_load(head)['title'] == meta['title']

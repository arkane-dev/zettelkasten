"""Communities: reading the vault, the graph, matching, notes, reports and the nightly schedule."""
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zk_sidecar.app import create_app
from zk_sidecar.config import Config
from zk_sidecar.graph import notes
from zk_sidecar.graph.build import Graph, cluster, noun_graph, percentile, prune
from zk_sidecar.graph.chunks import CHUNK_TOKENS, chunk
from zk_sidecar.graph.corpus import Doc, clean, collect, notes_section, strip_references
from zk_sidecar.graph.extract import NounPhrases
from zk_sidecar.graph.match import jaccard, match
from zk_sidecar.graph.reports import build_context, parse
from zk_sidecar.graph.schedule import Scheduler, due, parse as parse_schedule
from zk_sidecar.graph.service import GraphService
from zk_sidecar.jobs import Job
from zk_sidecar.sources.store import join_frontmatter, split_frontmatter
from zk_sidecar.summarise import estimate_tokens

DATA = Path('~/.cache/zk-sidecar/nltk').expanduser()


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ---- reading the vault

def test_collect_reads_sources_notes_and_notebooks(tmp_path: Path):
    src = tmp_path / 'Sources' / 'paper'
    write(src / 'content.md', 'The paper text.\n\n' + 'Body line.\n' * 20 + 'References\n[1] A. Author 2020.\n')
    write(src / 'Paper.md', join_frontmatter({'type': 'source', 'kind': 'pdf', 'title': 'Paper', 'original': 'original.pdf'},
                                             '<!-- zk:ai -->\nSummary.\n<!-- /zk:ai -->\n\n## Notes\n\nMy own thought.\n'))
    write(tmp_path / 'Ideas' / 'Wok.md', '# Wok\n\nSee [[Rice|fried rice]] and [x](http://a).\n\n```python run\ncode = 1\n```\n![[img.png]]\n')
    write(tmp_path / 'Communities' / 'L0' / 'C.md', join_frontmatter({'type': 'community'}, 'generated'))
    write(tmp_path / '.obsidian' / 'x.md', 'settings')
    write(tmp_path / 'Empty.md', '---\na: 1\n---\n')
    write(tmp_path / 'nb.ipynb', json.dumps({'cells': [
        {'cell_type': 'markdown', 'source': ['# Plot\n', 'A sine wave.']},
        {'cell_type': 'code', 'source': ['import math']}]}))

    docs = {d.path: d for d in collect(tmp_path)}
    assert set(docs) == {'Sources/paper/Paper.md', 'Ideas/Wok.md', 'nb.ipynb'}
    paper = docs['Sources/paper/Paper.md']
    assert paper.kind == 'source' and paper.title == 'Paper'
    assert 'My own thought.' in paper.text and 'A. Author' not in paper.text and 'Summary.' not in paper.text
    wok = docs['Ideas/Wok.md'].text
    assert 'fried rice' in wok and 'code = 1' not in wok and 'img.png' not in wok and 'http' not in wok
    assert docs['nb.ipynb'].text == '# Plot\nA sine wave.'


def test_strip_references_needs_a_late_heading():
    early = 'References\n' + 'text\n' * 10
    assert strip_references(early) == early
    body = 'text\n' * 10
    assert strip_references(body + '## References\n[1] X 2020\n') == body


def test_clean_and_notes_section():
    assert clean('a <!-- c --> b %% hidden %% [[Path/Note]]') == 'a  b  Note'
    assert notes_section('## Summary\nx\n## Notes\nmine\n## Later\ny') == 'mine'
    assert notes_section('no notes') == ''


def test_chunks_keep_short_notes_whole():
    short = Doc('a.md', 'a', 'note', 'one idea')
    assert [c.text for c in chunk(short)] == ['one idea']
    long = Doc('b.md', 'b', 'note', '\n\n'.join(f'Paragraph {i} ' + 'word ' * 200 for i in range(30)))
    parts = chunk(long)
    assert len(parts) > 1 and all(estimate_tokens(c.text) <= CHUNK_TOKENS for c in parts)
    assert chunk(long)[0].id == parts[0].id


# ---- the graph

def test_noun_phrases():
    np_ = NounPhrases(DATA)
    try:
        found = np_.extract('Heat the Worbla sheet with a hot air gun. See Figure 3 in the conference paper.')
    except RuntimeError as e:
        pytest.skip(str(e))
    assert 'HOT AIR GUN' in found and 'WORBLA' in found
    assert not any('FIGURE' in p or p == 'CONFERENCE PAPER' for p in found)


def two_topics() -> dict[str, list[str]]:
    a = ['SOY SAUCE', 'RICE WINE', 'SESAME OIL', 'SPRING ONION', 'WOK HEI', 'STAR ANISE']
    b = ['NEURAL NETWORK', 'GRADIENT DESCENT', 'LOSS FUNCTION', 'ATTENTION HEAD', 'TRAINING DATA', 'BATCH SIZE']
    out = {}
    for i in range(12):
        out[f'a{i}'] = [a[i % 6], a[(i + 1) % 6], a[(i + 2) % 6]]
        out[f'b{i}'] = [b[i % 6], b[(i + 1) % 6], b[(i + 2) % 6]]
    return out


def test_graph_prune_and_cluster_separate_topics():
    g = noun_graph(two_topics())
    assert len(g.nodes) == 12 and g.weights
    p = prune(g)
    assert len(p.nodes) == 11  # the most connected phrase goes
    clusters = cluster(p)
    top = [c for c in clusters if c.level == 0]
    assert top, 'expected communities'
    for c in top:
        assert c.members <= set(two_topics_flat('a')) or c.members <= set(two_topics_flat('b'))
        assert c.chunks
    # both topics survive, though they share no phrase
    found = set().union(*(c.members for c in top))
    assert found & set(two_topics_flat('a')) and found & set(two_topics_flat('b'))
    assert [(c.level, sorted(c.members)) for c in cluster(p)] == [(c.level, sorted(c.members)) for c in clusters]


def two_topics_flat(t: str) -> list[str]:
    return sorted({p for k, ps in two_topics().items() if k.startswith(t) for p in ps})


def test_percentile_matches_numpy():
    assert percentile([1, 2, 3, 4], 40) == pytest.approx(2.2)
    assert percentile([], 40) == 0.0
    assert prune(Graph({}, {})).nodes == {}


def test_match_keeps_ids_by_overlap():
    old = [('c1', 0, {'a', 'b', 'c'}), ('c2', 0, {'x', 'y'}), ('c3', 1, {'a', 'b', 'c'})]
    new = [(0, {'x', 'y', 'z'}), (0, {'a', 'b', 'c', 'd'}), (0, {'q'}), (1, {'a', 'b'})]
    assert match(new, old) == ['c2', 'c1', None, 'c3']
    assert jaccard(set(), set()) == 1.0


# ---- reports

def test_parse_report_tolerates_wrapping():
    r = parse('<think>hmm</think>Sure:\n{"title": " Wok cooking ", "summary": "S.", "findings": [{"summary": "a", "explanation": "b"},],}')
    assert r.title == 'Wok cooking' and r.findings[0].explanation == 'b'
    with pytest.raises(ValueError):
        parse('no json here')


def test_context_puts_connected_chunks_first_and_numbers_sources():
    text = {'c1': 'one', 'c2': 'two two', 'c3': 'x' * 40_000}
    doc = {'c1': 'a.md', 'c2': 'b.md', 'c3': 'a.md'}
    phrases = {'c1': ['P'], 'c2': ['P', 'Q'], 'c3': []}
    ctx, docs = build_context({'c1', 'c2', 'c3'}, text, doc, {'a.md': 'A', 'b.md': 'B'}, {'P', 'Q'}, phrases,
                              {'P': 1, 'Q': 5}, max_tokens=1000)
    assert ctx.startswith('[S1] B\ntwo two\n\n[S2] A\none')
    assert docs == ['b.md', 'a.md']
    assert 'xxxx' not in ctx


def test_key_ideas_drop_plurals_and_generic_words():
    from zk_sidecar.graph.service import distinct
    assert distinct(['llms', 'llm', 'models', 'model', 'classes', 'class', 'gas', 'batches', 'batch', 'eva foam']) == \
        ['llms', 'classes', 'gas', 'batches', 'eva foam']


def test_context_lets_sources_take_turns():
    text = {f'a{i}': f'a{i}' for i in range(5)} | {'b0': 'b0', 'c0': 'c0'}
    doc = {k: k[0] + '.md' for k in text}
    phrases = {k: ['P'] for k in text}
    ctx, docs = build_context(set(text), text, doc, {}, {'P'}, phrases, {'P': 1}, max_tokens=1000)
    heads = [line for line in ctx.split('\n') if line.startswith('[S')]
    assert heads[:3] == ['[S1] a.md', '[S2] b.md', '[S3] c.md'] and docs == ['a.md', 'b.md', 'c.md']


def test_citations_become_links():
    assert notes.cite('Fact [S1, S2]. More [S9].', ['Sources/a/A.md', 'n.md']) == \
        'Fact ([[Sources/a/A|1]], [[n|2]]). More .'


# ---- the whole rebuild

COOK = ['soy sauce', 'rice wine', 'sesame oil', 'spring onion', 'star anise', 'oyster sauce']
LEARN = ['neural network', 'gradient descent', 'loss function', 'attention head', 'training data', 'batch size']


def vault_with_topics(root: Path) -> Path:
    for i in range(10):
        c = [COOK[i % 6], COOK[(i + 1) % 6], COOK[(i + 2) % 6]]
        write(root / 'Cooking' / f'Dish {i}.md', f'Mix the {c[0]} with the {c[1]}. Add the {c[2]} last.\n')
        m = [LEARN[i % 6], LEARN[(i + 1) % 6], LEARN[(i + 2) % 6]]
        write(root / 'ML' / f'Model {i}.md', f'The {m[0]} needs a {m[1]}. Tune the {m[2]} first.\n')
    return root


def fake_model(calls: list):
    def complete(system: str, user: str) -> str:
        calls.append(user)
        topic = 'Wok cooking' if 'sauce' in user or 'onion' in user or 'oil' in user else 'Model training'
        return json.dumps({'title': topic, 'summary': f'About {topic.lower()} [S1].',
                           'findings': [{'summary': 'Key point', 'explanation': 'It says so [S1, S2].'}]})
    return lambda: complete


@pytest.fixture
def graph(tmp_path: Path):
    vault_with_topics(tmp_path)
    calls: list = []
    svc = GraphService(tmp_path, fake_model(calls), 8192, DATA, entities=False)
    try:
        NounPhrases(DATA).extract('a test sentence')
    except RuntimeError as e:
        pytest.skip(str(e))
    return svc, calls


def run(svc: GraphService, force=False) -> Job:
    job = Job(id='j', action='rebuild-graph')
    svc.rebuild(job, force)
    return job


def community_notes(vault: Path) -> dict[str, tuple[dict, str]]:
    out = {}
    for p in sorted((vault / 'Communities').rglob('*.md')):
        meta, body = split_frontmatter(p.read_text())
        out[p.relative_to(vault).as_posix()] = (meta, body)
    return out


def test_rebuild_writes_community_notes(graph, tmp_path: Path):
    svc, calls = graph
    job = run(svc)
    assert not job.warning, job.warning
    found = community_notes(tmp_path)
    assert found, 'no community notes'
    titles = {m['title'] for m, _ in found.values()}
    assert titles <= {'Wok cooking', 'Model training'} | {f'Wok cooking {i}' for i in range(2, 9)}
    meta, body = next(iter(found.values()))
    assert meta['type'] == 'community' and meta['zk_id'].startswith('c') and meta['level'] in (0, 1)
    assert '<!-- zk:ai -->' in body and '## Notes' in body and '## Sources' in body
    assert '[[Cooking/Dish' in body or '[[ML/Model' in body
    status = svc.status()
    assert status['notes'] == len(found) and status['pending_reports'] == 0
    assert len(calls) == len(found)


def test_rebuild_skips_when_nothing_changed(graph):
    svc, calls = graph
    run(svc)
    n = len(calls)
    job = run(svc)
    assert job.unchanged and len(calls) == n
    assert not svc.changed()


def test_your_notes_survive_a_rebuild(graph, tmp_path: Path):
    svc, calls = graph
    run(svc)
    path, (meta, body) = next(iter(community_notes(tmp_path).items()))
    (tmp_path / path).write_text(join_frontmatter(meta, body.replace('## Notes\n', '## Notes\n\nMy insight.\n')))
    write(tmp_path / 'Cooking' / 'Dish 99.md', 'Mix the soy sauce with the star anise and the rice wine.\n')
    n = len(calls)
    run(svc)
    after = community_notes(tmp_path)
    assert path in after and 'My insight.' in after[path][1]
    assert after[path][0]['zk_id'] == meta['zk_id']
    assert len(calls) - n < len(after), 'unchanged communities should keep their reports'


def test_rewrite_drops_stale_keys_and_keeps_yours(tmp_path: Path):
    p = tmp_path / 'C.md'
    notes.write(p, {'type': 'community', 'zk_id': 'c1', 'children': ['[[old]]'], 'provisional': True}, 'first')
    meta, body = split_frontmatter(p.read_text())
    p.write_text(join_frontmatter({**meta, 'tags': ['mine']}, body.replace('## Notes\n', '## Notes\n\nKeep.\n')))
    notes.write(p, {'type': 'community', 'zk_id': 'c1'}, 'second')
    meta, body = split_frontmatter(p.read_text())
    assert meta == {'type': 'community', 'zk_id': 'c1', 'tags': ['mine']}
    assert 'second' in body and 'first' not in body and 'Keep.' in body


def test_gone_communities_retire_or_go(tmp_path: Path):
    mine = write(tmp_path / 'Communities' / 'L0' / 'Mine.md',
                 join_frontmatter({'type': 'community', 'zk_id': 'c1'}, '<!-- zk:ai -->\nx\n<!-- /zk:ai -->\n\n## Notes\n\nKeep me.\n'))
    empty = write(tmp_path / 'Communities' / 'L0' / 'Empty.md',
                  join_frontmatter({'type': 'community', 'zk_id': 'c2'}, '<!-- zk:ai -->\nx\n<!-- /zk:ai -->\n\n## Notes\n\n'))
    dest = notes.retire(tmp_path, mine, '2026-10-24')
    assert dest == tmp_path / 'Communities' / '_retired' / 'Mine.md'
    assert split_frontmatter(dest.read_text())[0]['retired'] == '2026-10-24'
    assert notes.retire(tmp_path, empty) is None and not empty.exists()
    assert notes.find_notes(tmp_path) == {}


def test_report_failure_leaves_pending_notes(tmp_path: Path):
    vault_with_topics(tmp_path)

    def broken():
        def complete(system, user):
            raise ConnectionError('Connection refused.')
        return complete
    svc = GraphService(tmp_path, broken, 8192, DATA, entities=False)
    job = run(svc)
    assert 'could not be written: Connection refused.' in job.warning
    found = community_notes(tmp_path)
    assert all(m.get('provisional') for m, _ in found.values())
    assert all(notes.PENDING in b for _, b in found.values())
    assert svc.status()['pending_reports'] == len(found)

    svc.complete = fake_model([])
    run(svc)  # pending reports make it run again, though nothing changed
    renamed = community_notes(tmp_path)
    assert not any(m.get('provisional') for m, _ in renamed.values())
    assert {m['zk_id'] for m, _ in renamed.values()} == {m['zk_id'] for m, _ in found.values()}


def test_renames_follow_new_titles_but_not_yours(graph, tmp_path: Path):
    svc, _ = graph
    run(svc)
    st = svc.store.state()
    notes_ = [c for c in st['communities'] if c.get('note')]
    a, b = notes_[0], notes_[1]
    a['title'], b['title'] = 'Fresh title', 'Another title'
    svc.store.save_state(st)
    mine = tmp_path / 'Communities' / 'Mine.md'
    (tmp_path / b['note']).rename(mine)  # you moved this one
    assert svc.renames() == [{'from': a['note'], 'to': f'Communities/L{a["level"]}/Fresh title.md'}]

    (tmp_path / a['note']).rename(tmp_path / f'Communities/L{a["level"]}/Fresh title.md')  # what Obsidian does
    svc.refresh_notes()
    st = svc.store.state()
    assert {c['zk_id']: c['note'] for c in st['communities'] if c.get('note')}[a['zk_id']].endswith('Fresh title.md')
    assert svc.renames() == []
    view = {c['id']: c['note'] for c in svc.view()['communities']}
    assert view[b['zk_id']] == 'Communities/Mine.md'


def test_wants_rename():
    assert notes.wants_rename('Communities/L0/Old.md', 'New')
    assert not notes.wants_rename('Communities/L0/New.md', 'New')
    assert not notes.wants_rename('Communities/L0/New 2.md', 'New')


# ---- schedule

def test_schedule_parse():
    assert parse_schedule('03:30') == (3, 30)
    assert parse_schedule('off') is None and parse_schedule('') is None
    with pytest.raises(ValueError):
        parse_schedule('25:00')


def test_due():
    now = datetime(2026, 10, 24, 12, 0)
    started = datetime(2026, 10, 24, 11, 0)
    at = (3, 0)
    assert due(at, '2026-10-23T22:00:00', started, now) == datetime(2026, 10, 24, 3, 0)
    assert due(at, '2026-10-24T04:00:00', started, now) is None
    assert due(at, '', started, now) is None  # never built: wait for a slot after start
    assert due(at, '', started, datetime(2026, 10, 25, 3, 5)) == datetime(2026, 10, 25, 3, 0)
    assert due(None, '', started, now) is None


def test_scheduler_runs_once_per_slot_and_only_if_changed():
    runs, changed = [], [False]
    s = Scheduler((3, 0), built=lambda: '2026-10-23T22:00:00', changed=lambda: changed[0],
                  run=lambda: runs.append(1), now=lambda: datetime(2026, 10, 24, 12, 0))
    assert not s.tick()
    changed[0] = True
    assert not s.tick()  # this slot was already checked
    s._checked = None
    assert s.tick() and runs == [1]


def test_config_rejects_a_bad_schedule(tmp_path: Path):
    with pytest.raises(ValueError, match='HH:MM'):
        Config.from_env(tmp_path, {'ZK_TOKEN': 't', 'ZK_GRAPH_SCHEDULE': 'nightly'})


def test_graph_routes(graph, tmp_path: Path):
    svc, _ = graph
    auth = {'Authorization': 'Bearer t'}
    with TestClient(create_app(Config(vault=tmp_path, token='t', graph_schedule='off'), graph=svc)) as client:
        assert client.get('/graph/status', headers=auth).json()['built'] == ''
        first = client.post('/graph/rebuild', json={}, headers=auth).json()
        again = client.post('/graph/rebuild', json={}, headers=auth).json()
        assert first['action'] == 'rebuild-graph' and again['id'] == first['id']
        for _ in range(200):
            job = client.get(f'/jobs/{first["id"]}', headers=auth).json()
            if job['state'] in ('done', 'error'):
                break
            import time
            time.sleep(0.05)
        assert job['state'] == 'done', job
        view = client.get('/graph', headers=auth).json()
        assert view['communities'] and {'id', 'level', 'title', 'note', 'docs', 'key'} <= set(view['communities'][0])
        assert client.get('/graph/status', headers=auth).json()['notes'] > 0


def test_phrases_in_worker_processes_match_inline(graph, tmp_path: Path, monkeypatch):
    from zk_sidecar.graph import service as service_mod
    from zk_sidecar.graph.chunks import chunk as make_chunks
    svc, _ = graph
    chunks = [c for d in collect(tmp_path) for c in make_chunks(d)][:6]
    monkeypatch.setattr(service_mod, 'POOL_AT', 0)
    pooled = svc._extract_phrases(Job(id='p', action='x'), chunks)
    inline = {c.id: svc.np.extract(c.text) for c in chunks}
    assert pooled == inline and any(pooled.values())

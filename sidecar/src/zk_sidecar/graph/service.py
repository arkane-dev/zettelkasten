"""Rebuild the communities. Runs inside a background job.

1. Read every source and note. Chunk them.
2. Extract phrases and entities, for new or changed chunks only (cached by text).
3. Build the phrase graph and cluster all of it again, with a fixed seed.
4. Match the new communities to the old ones, so ids, notes and your writing carry over.
5. Write a report only where a community is new or its membership moved more than 20%.
6. Write the notes for levels 0 and 1. Retire the notes of communities that are gone.
"""
import hashlib
import multiprocessing
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from ..jobs import Job
from ..summarise import Complete
from . import notes
from .build import cluster, noun_graph, prune
from .chunks import chunk
from .corpus import collect
from .extract import Entities, NounPhrases, gliner_available
from .match import REPORT_KEEP, jaccard, match, new_id
from .reports import budget, build_context, write_report
from .store import GraphStore

NOTE_LEVELS = (0, 1)
#: Keep a community's title while at least this share of its phrases stays.
TITLE_KEEP = 0.5
REPORT_WORKERS = 4
KEY_IDEAS = 12
POOL_AT = 100  # use worker processes for phrases past this many new chunks

_np: NounPhrases | None = None


def _phrases_worker(args: tuple[str, str]) -> list[str]:
    global _np
    data_dir, text = args
    if _np is None:
        _np = NounPhrases(Path(data_dir))
    return _np.extract(text)


#: Too general to name what a community is about.
GENERIC = {'model', 'method', 'approach', 'system', 'data', 'task', 'result', 'paper', 'work', 'example',
           'tool', 'material', 'concept', 'process', 'technique', 'way', 'thing', 'step', 'part'}


def singular(word: str) -> str:
    if len(word) <= 3 or word.endswith('ss'):
        return word
    if word.endswith('es') and word[:-2].endswith(('s', 'x', 'ch', 'sh')):
        return word[:-2]
    return word[:-1] if word.endswith('s') else word


def distinct(ideas: list[str]) -> list[str]:
    """Drop plurals of ideas already listed, and words too general to help."""
    seen: set[str] = set()
    out = []
    for idea in ideas:
        stem = singular(idea.lower())
        if stem in seen or stem in GENERIC:
            continue
        seen.add(stem)
        out.append(idea)
    return out


class GraphService:
    def __init__(self, vault: Path, complete: Callable[[], Complete], context_tokens: int,
                 data_dir: Path, entities: bool | None = None, model: str = '',
                 now: Callable[[], datetime] = datetime.now):
        self.vault = vault
        self.store = GraphStore(vault / '.zk' / 'graph')
        self.complete = complete
        self.context_tokens = context_tokens
        self.np = NounPhrases(data_dir)
        self.data_dir = data_dir
        self.use_entities = gliner_available() if entities is None else entities
        self.model = model
        self.now = now
        self._last_error = ''

    # ---- status

    @staticmethod
    def fingerprint(docs) -> str:
        h = hashlib.sha256()
        for d in docs:
            h.update(f'{d.path}\0{hashlib.sha256(d.text.encode()).hexdigest()}\n'.encode())
        return h.hexdigest()

    def changed(self) -> bool:
        return self.store.state().get('fingerprint') != self.fingerprint(collect(self.vault))

    def status(self) -> dict:
        st = self.store.state()
        comms = st.get('communities', [])
        return {
            'built': st.get('built', ''),
            'seconds': st.get('seconds'),
            'docs': st.get('docs', 0),
            'chunks': st.get('chunks', 0),
            'phrases': st.get('phrases', 0),
            'levels': dict(sorted(Counter(str(c['level']) for c in comms).items())),
            'notes': sum(1 for c in comms if c.get('note')),
            'reports_written': st.get('reports_written'),
            'timings': st.get('timings'),
            'pending_reports': sum(1 for c in comms if c['level'] in NOTE_LEVELS and not c.get('report')),
            'entities': self.use_entities,
        }

    # ---- rebuild

    def rebuild(self, job: Job, force: bool = False) -> None:
        started = time.monotonic()
        old = self.store.state()
        job.step = 'reading notes'
        docs = collect(self.vault)
        doc_title = {d.path: d.title for d in docs}
        chunks = [c for d in docs for c in chunk(d)]
        fingerprint = self.fingerprint(docs)
        if not force and old.get('fingerprint') == fingerprint and not self._pending(old):
            job.unchanged = True
            return

        phrases = self._extract_phrases(job, chunks)
        typed = self._extract_entities(job, chunks) if self.use_entities else {}

        job.step = 'clustering'
        t_clustered = time.monotonic()
        graph = prune(noun_graph({c.id: phrases[c.id] for c in chunks}))
        clusters = cluster(graph)
        degree = graph.degree()
        chunk_doc = {c.id: c.doc for c in chunks}
        chunk_text = {c.id: c.text for c in chunks}

        old_comms = {c['zk_id']: c for c in old.get('communities', [])}
        ids = match([(cl.level, cl.members) for cl in clusters],
                    [(c['zk_id'], c['level'], set(c['members'])) for c in old_comms.values()])
        by_cluster: dict[int, dict] = {}
        comms: list[dict] = []
        for cl, zk in zip(clusters, ids):
            prev = old_comms.get(zk) if zk else None
            docs_count = Counter(chunk_doc[ch] for ch in cl.chunks)
            c = {
                'zk_id': zk or new_id(),
                'level': cl.level,
                'cluster': cl.id,
                'parent_cluster': cl.parent,
                'members': sorted(cl.members),
                'chunks': sorted(cl.chunks),
                'docs': sorted(docs_count.items(), key=lambda kv: (-kv[1], kv[0])),
                'key': self._key_ideas(cl.members, cl.chunks, typed, degree),
                'title': prev['title'] if prev else '',
                'note': prev.get('note') if prev else None,
                'named': prev.get('named') if prev else None,
            }
            if prev and prev.get('report') and not force and \
                    jaccard(cl.members, set(prev.get('report_members', []))) >= REPORT_KEEP:
                c['report'], c['report_members'], c['report_docs'] = \
                    prev['report'], prev['report_members'], prev['report_docs']
            comms.append(c)
            by_cluster[cl.id] = c
        for c in comms:
            parent = by_cluster.get(c['parent_cluster'])
            c['parent'] = parent['zk_id'] if parent else None
            c['children'] = [k['zk_id'] for k in comms if k['parent_cluster'] == c['cluster']]

        written_before = sum(1 for c in comms if c.get('report'))
        t_reports = time.monotonic()
        failed = self._write_reports(job, comms, chunk_text, chunk_doc, doc_title, phrases, degree)
        timings = {'extract': round(t_clustered - started, 1)}
        for c in comms:
            # A title is a name: it changes only when the community changed a lot, or never had one.
            prev = old_comms.get(c['zk_id'])
            stable = prev and prev.get('report') and jaccard(set(c['members']), set(prev['members'])) >= TITLE_KEEP
            if c.get('report') and not stable:
                c['title'] = c['report']['title']
            elif not c['title']:
                c['title'] = c['report']['title'] if c.get('report') else self._provisional(c)

        job.step = 'writing notes'
        self._write_notes(comms, old_comms, doc_title)
        self.store.save_state({
            'built': self.now().isoformat(timespec='seconds'),
            'seconds': round(time.monotonic() - started, 1),
            'fingerprint': fingerprint,
            'docs': len(docs), 'chunks': len(chunks), 'phrases': len(graph.nodes),
            'doc_titles': doc_title,
            'links': self._links(comms, graph),
            'reports_written': sum(1 for c in comms if c.get('report')) - written_before,
            'timings': {**timings, 'reports': round(time.monotonic() - t_reports, 1)},
            'communities': comms,
        })
        job.note = notes.FOLDER
        if not comms:
            job.warning = 'Not enough connected text yet to form communities. Add more notes or sources.'
        elif failed:
            job.warning = (f'{failed} {"report" if failed == 1 else "reports"} could not be written: '
                           f'{self._last_error} "Rebuild communities" tries again.')

    def _pending(self, state: dict) -> bool:
        return any(c['level'] in NOTE_LEVELS and not c.get('report') for c in state.get('communities', []))

    def _extract_phrases(self, job: Job, chunks) -> dict[str, list[str]]:
        key = {c.id: f'{self.np.name}:{c.sha}' for c in chunks}
        have = self.store.cached(list(set(key.values())))
        todo = [c for c in chunks if key[c.id] not in have]
        uniq = list({key[c.id]: c for c in todo}.values())
        if uniq:
            job.step = f'finding phrases in {len(uniq)} new {"passage" if len(uniq) == 1 else "passages"}'
            if len(uniq) > POOL_AT:
                ctx = multiprocessing.get_context('spawn')
                with ProcessPoolExecutor(min(8, multiprocessing.cpu_count()), mp_context=ctx) as ex:
                    found = list(ex.map(_phrases_worker, [(str(self.data_dir), c.text) for c in uniq], chunksize=4))
            else:
                found = [self.np.extract(c.text) for c in uniq]
            new = {key[c.id]: f for c, f in zip(uniq, found)}
            self.store.cache(new)
            have.update(new)
        return {c.id: have[key[c.id]] for c in chunks}

    def _extract_entities(self, job: Job, chunks) -> dict[str, list[list[str]]]:
        ent = Entities()
        key = {c.id: f'{ent.name}:{c.sha}' for c in chunks}
        have = self.store.cached(list(set(key.values())))
        uniq = list({key[c.id]: c for c in chunks if key[c.id] not in have}.values())
        if uniq:
            def step(i: int, n: int) -> None:
                job.step = f'finding key ideas ({i} of {n} passages)'
            step(0, len(uniq))
            try:
                found = ent.extract_many([c.text for c in uniq], step)
            except Exception as e:  # noqa: BLE001  entities are a nicety; phrases still work
                job.warning = f'Key ideas fell back to phrases: {e}'
                return {}
            new = {key[c.id]: [list(p) for p in f] for c, f in zip(uniq, found)}
            self.store.cache(new)
            have.update(new)
        return {c.id: have[key[c.id]] for c in chunks}

    @staticmethod
    def _key_ideas(members: set[str], chunks: set[str], typed: dict, degree: dict[str, int]) -> list[str]:
        ranked: list[str] = []
        if typed:
            count: Counter[str] = Counter()
            for ch in chunks:
                count.update({name for name, _ in typed.get(ch, [])})
            ranked = [n for n, k in count.most_common() if k >= 2]
        if not ranked:
            ranked = [m.lower() for m in sorted(members, key=lambda m: (-degree.get(m, 0), m))]
        return distinct(ranked)[:KEY_IDEAS]

    @staticmethod
    def _provisional(c: dict) -> str:
        return ', '.join(c['key'][:3]).capitalize() or f'Community {c["zk_id"]}'

    def _write_reports(self, job: Job, comms: list[dict], chunk_text, chunk_doc, doc_title, phrases, degree) -> int:
        todo = [c for c in comms if c['level'] in NOTE_LEVELS and not c.get('report')]
        if not todo:
            return 0
        limit = budget(self.context_tokens)
        try:
            complete = self.complete()
        except Exception as e:  # noqa: BLE001
            self._last_error = str(e)
            return len(todo)
        done = 0
        failed = 0

        def one(c: dict) -> None:
            context, docs = build_context(set(c['chunks']), chunk_text, chunk_doc, doc_title,
                                          set(c['members']), phrases, degree, limit)
            report = write_report(complete, context)
            c['report'] = report.public()
            c['report_members'] = c['members']
            c['report_docs'] = docs

        job.step = f'writing reports (0 of {len(todo)})'
        with ThreadPoolExecutor(REPORT_WORKERS) as ex:
            for fut in [ex.submit(one, c) for c in todo]:
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001  one bad report doesn't stop the rest
                    failed += 1
                    self._last_error = str(e)
                done += 1
                job.step = f'writing reports ({done} of {len(todo)})'
        return failed

    def _write_notes(self, comms: list[dict], old: dict[str, dict], doc_title: dict[str, str],
                     retire: bool = True) -> None:
        today = date.today().isoformat()
        on_disk = notes.find_notes(self.vault)
        by_id = {c['zk_id']: c for c in comms}
        writing = [c for c in comms if c['level'] in NOTE_LEVELS]
        # Place every note first, so links between them point at the final paths.
        for c in writing:
            path = notes.place(self.vault, c, on_disk.get(c['zk_id']))
            c['note'] = path.relative_to(self.vault).as_posix()
        for c in writing:
            notes.write(self.vault / c['note'], notes.frontmatter(c, by_id, today), notes.body(c, by_id, doc_title))
            # The path we chose, to tell our renames from yours: set when we made the note or named it after its title.
            if not c.get('named') or not notes.wants_rename(c['note'], c['title']):
                c['named'] = c['note']
        if not retire:
            return
        for zk, path in on_disk.items():
            if zk not in by_id or by_id[zk]['level'] not in NOTE_LEVELS:
                notes.retire(self.vault, path, today)

    @staticmethod
    def _links(comms: list[dict], graph) -> list[list]:
        """Edge counts between sibling communities, for the graph view."""
        where: dict[int, dict[str, str]] = defaultdict(dict)
        for c in comms:
            for m in c['members']:
                where[c['level']][m] = c['zk_id']
        counts: Counter[tuple[str, str]] = Counter()
        for a, b in graph.edges:
            for level in where.values():
                ca, cb = level.get(a), level.get(b)
                if ca and cb and ca != cb:
                    counts[tuple(sorted((ca, cb)))] += 1
        return [[a, b, n] for (a, b), n in counts.most_common()]

    # ---- renames, done by the plugin so Obsidian updates links

    def renames(self) -> list[dict]:
        """Notes whose title changed. Skips notes you renamed or moved yourself."""
        on_disk = notes.find_notes(self.vault)
        out = []
        for c in self.store.state().get('communities', []):
            path = on_disk.get(c['zk_id'])
            if not path or not c.get('named'):
                continue
            rel = path.relative_to(self.vault).as_posix()
            if rel != c['named'] or not notes.wants_rename(rel, c['title']):
                continue
            target = notes.unique(path.parent, notes.note_name(c['title']), path)
            out.append({'from': rel, 'to': target.relative_to(self.vault).as_posix()})
        return out

    def refresh_notes(self) -> None:
        """Rewrite the notes with their current paths, after renames. No model calls."""
        st = self.store.state()
        comms = st.get('communities', [])
        on_disk = notes.find_notes(self.vault)
        for c in comms:
            if c['zk_id'] in on_disk:
                c['note'] = on_disk[c['zk_id']].relative_to(self.vault).as_posix()
        self._write_notes(comms, {}, st.get('doc_titles', {}), retire=False)
        self.store.save_state(st)

    # ---- for the graph view

    def view(self) -> dict:
        st = self.store.state()
        on_disk = notes.find_notes(self.vault)
        for c in st.get('communities', []):
            if c['zk_id'] in on_disk:
                c['note'] = on_disk[c['zk_id']].relative_to(self.vault).as_posix()
        return {
            'built': st.get('built', ''),
            'communities': [{
                'id': c['zk_id'], 'level': c['level'], 'parent': c.get('parent'), 'title': c['title'],
                'note': c.get('note'), 'size': len(c['members']), 'key': c.get('key', [])[:8],
                'docs': [[p, st.get('doc_titles', {}).get(p, Path(p).stem), n] for p, n in c['docs'][:12]],
                'children': c.get('children', []),
            } for c in st.get('communities', [])],
            'links': st.get('links', []),
        }

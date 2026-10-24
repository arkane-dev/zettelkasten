"""How often do summaries say things their sources don't?

Summarise a set of text files with the sidecar's Summariser, split each summary into claims,
and have a second model judge each claim against the most relevant passages of the full source.
A different model judges, so the writer doesn't grade its own work.

    python eval/summary_eval.py summarise --corpus DIR --out runs/base --files LIST [--variant base|strip|...]
    python eval/summary_eval.py judge --out runs/base
    python eval/summary_eval.py report runs/base runs/strip ...
    python eval/summary_eval.py reports --vault VAULT --out runs/reports   # community reports in a built vault

LIST is a text file with one file name per line. The corpus isn't in the repo.
"""
import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sidecar' / 'src'))

from zk_sidecar import summarise as S  # noqa: E402
from zk_sidecar.graph.corpus import strip_references  # noqa: E402

BASE_URL = 'http://127.0.0.1:11434/v1'
WRITER = 'gemma3-27b-16k:latest'
JUDGE = 'qwen3-30b-a3b-16k:latest'
CONTEXT = 16384

# What shipped in 0.1.0, for the "base" variant.
BASE_SUMMARY = """You summarise a source for a personal knowledge base.
Write in English, in plain words and short sentences.
Only state what the source says. Don't add facts, opinions or praise.

Format:
One paragraph of 2 to 4 sentences: what the source is and its main point.
Then 3 to 7 bullet points with the key ideas, findings, methods or steps.
No heading and no preamble."""
BASE_PART = """You take notes on one part of a longer source.
List its key points as short bullet points, in English and plain words.
Only state what the text says. No heading and no preamble."""


def summariser(variant: str, complete) -> S.Summariser:
    if variant == 'base':
        S.SUMMARY_PROMPT, S.PART_PROMPT = BASE_SUMMARY, BASE_PART
        s = S.Summariser(complete, CONTEXT, check=False)
        s._ask = lambda system, user: S.THINK.sub('', complete(system, user)).strip()
        return s
    return S.Summariser(complete, CONTEXT, check=variant != 'strict')


JUDGE_PROMPT = """You check one claim from a summary of a document against passages from that document.
Read the passages carefully. Judge only by what they say.

Reply with one JSON object and nothing else:
{"verdict": "supported" | "contradicted" | "unsupported", "reason": "one short sentence"}

- supported: the passages state it, or it follows directly from them.
- contradicted: the passages say something that makes the claim wrong.
- unsupported: the passages don't say it either way.
Vague but true claims are supported. Judge the facts, not the style."""

WORD = re.compile(r"[a-z0-9][a-z0-9\-']+")
STOP = set('the a an and or of to in on for with by is are was were be been as at from that this it its their'
           ' which into than then also can not no but such these those both each other more most using use used'.split())


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


def passages(text: str, size: int = 220, step: int = 160) -> list[str]:
    w = text.split()
    return [' '.join(w[i:i + size]) for i in range(0, max(1, len(w) - size // 2), step)]


def top_passages(claim: str, chunks: list[str], k: int = 5) -> list[str]:
    """BM25 over overlapping passages of the full source."""
    docs = [Counter(words(c)) for c in chunks]
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / max(n, 1)
    df = Counter(t for d in docs for t in d)
    q = set(words(claim))
    def score(d: Counter) -> float:
        L = sum(d.values())
        return sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * 2.2 / (d[t] + 1.2 * (0.25 + 0.75 * L / avg))
                   for t in q if t in d)
    best = sorted(range(n), key=lambda i: -score(docs[i]))[:k]
    return [chunks[i] for i in sorted(best)]


claims = S.claims


def summarise(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    complete = S.openai_complete(BASE_URL, WRITER, '')
    for name in Path(args.files).read_text().split():
        dest = out / f'{name}.json'
        if dest.exists():
            continue
        text = (Path(args.corpus) / name).read_text()
        used = strip_references(text) if args.variant != 'base' else text
        t = time.time()
        sm = summariser(args.variant, complete)
        summary = sm.summarise(Path(name).stem, used)
        dest.write_text(json.dumps({'name': name, 'variant': args.variant, 'seconds': round(time.time() - t, 1),
                                    'summarised_tokens': S.estimate_tokens(used), 'check': sm.last_check,
                                    'summary': summary}, indent=1))
        print(f'{name}: {time.time() - t:.0f}s, {S.estimate_tokens(used)} tokens', flush=True)


def judge(args) -> None:
    out = Path(args.out)
    complete = S.openai_complete(BASE_URL, JUDGE, '')
    for f in sorted(out.glob('*.json')):
        run = json.loads(f.read_text())
        if 'verdicts' in run:
            continue
        chunks = passages((Path(args.corpus) / run['name']).read_text())
        verdicts = []
        for c in claims(run['summary']):
            ctx = '\n\n'.join(f'[P{i + 1}] {p}' for i, p in enumerate(top_passages(c, chunks)))
            reply = S.THINK.sub('', complete(JUDGE_PROMPT, f'Passages:\n\n{ctx}\n\nClaim: {c}'))
            m = re.search(r'\{.*\}', reply, re.S)
            try:
                v = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                v = {}
            verdicts.append({'claim': c, 'verdict': v.get('verdict', 'unparsed'), 'reason': v.get('reason', reply[:200])})
        run['verdicts'] = verdicts
        f.write_text(json.dumps(run, indent=1))
        bad = sum(v['verdict'] != 'supported' for v in verdicts)
        print(f'{run["name"]}: {len(verdicts)} claims, {bad} not supported', flush=True)


CITE = re.compile(r'\[S\d+(?:\s*[,;]\s*S?\d+)*\]')
META = re.compile(r'^\s*(?:these|the|this)\s+(?:\w+\s+)?(?:excerpts?|sources?|reports?|passages?|documents?|stud(?:y|ies))\b', re.I)


def reports(args) -> None:
    """Judge each sentence of each community report against the excerpts it was written from."""
    from zk_sidecar.graph.chunks import chunk
    from zk_sidecar.graph.corpus import collect
    from zk_sidecar.graph.store import GraphStore

    vault, out = Path(args.vault), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = {c.id: c.text for d in collect(vault) for c in chunk(d)}
    state = GraphStore(vault / '.zk' / 'graph').state()
    complete = S.openai_complete(BASE_URL, JUDGE, '')
    picked = sorted((c for c in state.get('communities', []) if c.get('report')), key=lambda c: (c['level'], c['zk_id']))
    for c in picked[:args.limit]:
        rep = c.get('report')
        dest = out / f'{c["zk_id"]}.json'
        if not rep or dest.exists():
            continue
        chunks = [p for ch in c['chunks'] if ch in text for p in passages(text[ch])]
        sentences = claims(rep['summary']) + [x for f in rep['findings'] for x in claims(f['explanation'])]
        verdicts = []
        for claim in sentences:
            clean = CITE.sub('', claim).strip()
            ctx = '\n\n'.join(f'[P{i + 1}] {p}' for i, p in enumerate(top_passages(clean, chunks)))
            reply = S.THINK.sub('', complete(JUDGE_PROMPT, f'Passages:\n\n{ctx}\n\nClaim: {clean}'))
            m = re.search(r'\{.*\}', reply, re.S)
            try:
                v = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                v = {}
            verdicts.append({'claim': clean, 'verdict': v.get('verdict', 'unparsed'), 'reason': v.get('reason', '')})
        dest.write_text(json.dumps({'name': c['title'], 'seconds': 0, 'meta_opening': bool(META.match(rep['summary'])),
                                    'summary': rep['summary'], 'verdicts': verdicts}, indent=1))
        print(f'{c["title"]}: {len(verdicts)} claims, {sum(v["verdict"] != "supported" for v in verdicts)} not supported', flush=True)


def report(args) -> None:
    for d in args.runs:
        runs = [json.loads(f.read_text()) for f in sorted(Path(d).glob('*.json'))]
        v = Counter(x['verdict'] for r in runs for x in r.get('verdicts', []))
        n = sum(v.values()) or 1
        secs = sum(r['seconds'] for r in runs)
        fixed = Counter()
        for r in runs:
            fixed.update(r.get('check') or {})
        meta = sum(1 for r in runs if r.get('meta_opening'))
        print(f'\n== {d}: {len(runs)} docs, {n} claims, {secs / 60:.1f} min writing, {meta} meta openings. '
              f'supported {100 * v["supported"] / n:.0f}%, contradicted {v["contradicted"]}, unsupported {v["unsupported"]}, '
              f'unparsed {v["unparsed"]}' + (f'. check pass: {fixed["claims"]} checked, {fixed["corrected"]} corrected, {fixed["dropped"]} dropped' if fixed else ''))
        if args.verbose:
            for r in runs:
                for x in r.get('verdicts', []):
                    if x['verdict'] != 'supported':
                        print(f'  [{x["verdict"][:5]}] {r["name"][:40]}: {x["claim"][:150]}\n           {x["reason"][:160]}')


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('summarise')
    s.add_argument('--corpus', required=True)
    s.add_argument('--files', required=True)
    s.add_argument('--out', required=True)
    s.add_argument('--variant', default='base')
    j = sub.add_parser('judge')
    j.add_argument('--corpus', required=True)
    j.add_argument('--out', required=True)
    c = sub.add_parser('reports')
    c.add_argument('--vault', required=True)
    c.add_argument('--out', required=True)
    c.add_argument('--limit', type=int, default=20, help='reports to judge, top level first')
    r = sub.add_parser('report')
    r.add_argument('runs', nargs='+')
    r.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args()
    {'summarise': summarise, 'judge': judge, 'reports': reports, 'report': report}[args.cmd](args)


if __name__ == '__main__':
    main()

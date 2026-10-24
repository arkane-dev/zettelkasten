"""Summaries through any OpenAI-compatible API.

Ollama's OpenAI API can't set the context length per request, so a long source is
split into parts that fit the model's context. Each part gets notes, then the notes
get the summary.

Then a check pass: each claim in the summary is tested against the passages of the
source that match it best. A claim the passages contradict is corrected. A claim they
don't support is dropped. Small models fill gaps with guesses, most of all when a long
source is split and a term is explained in a part they can't see.
"""
import json
import math
import re
from collections import Counter
from collections.abc import Callable

RULES = """Only state what the source says. Don't add facts, opinions or praise.
Use the source's own terms. Don't expand an abbreviation unless the source spells it out.
Keep comparisons as the source states them: which one did better, on what, and by how much. If the source doesn't compare two things, don't.
Leave out anything you're not sure the source says."""

SUMMARY_PROMPT = f"""You summarise a source for a personal knowledge base.
Write in English, in plain words and short sentences.
{RULES}

Format:
One paragraph of 2 to 4 sentences: what the source is and its main point.
Then 3 to 7 bullet points with the key ideas, findings, methods or steps.
No heading and no preamble."""

PART_PROMPT = f"""You take notes on one part of a longer source.
Other parts may explain terms used here. If this part doesn't explain a term, keep it as written and don't guess what it means.
{RULES}
List the key points as short bullet points, in English and plain words. No heading and no preamble."""

CHECK_PROMPT = """You check one claim from a summary against passages from the source.
Judge only by what the passages say.

Reply with one JSON object and nothing else:
{"verdict": "supported" | "contradicted" | "unsupported", "fix": "..."}

- supported: the passages state it, or it follows directly from them. "fix" is empty.
- contradicted: the passages show it's wrong. "fix" is the claim rewritten to match the passages, in the same style and about the same length.
- unsupported: the passages don't say it either way. "fix" is empty."""

NO_TEXT = '_No text could be extracted from this source._'

#: Tokens kept free for the prompt and the reply.
RESERVED_TOKENS = 1500
MAX_ROUNDS = 4

CJK = re.compile(r'[　-鿿가-힯豈-﫿]')
THINK = re.compile(r'<think>.*?</think>', re.S)

Complete = Callable[[str, str], str]


def estimate_tokens(text: str) -> int:
    """Rough count: about 4 characters per token, but one per CJK character."""
    cjk = len(CJK.findall(text))
    return cjk + (len(text) - cjk) // 4 + 1


def split_to_fit(text: str, max_tokens: int) -> list[str]:
    """Split on paragraphs, then on lines, then by length, so each part fits."""
    parts: list[str] = []
    current: list[str] = []
    size = 0

    def flush():
        nonlocal current, size
        if current:
            parts.append('\n\n'.join(current))
        current, size = [], 0

    for para in re.split(r'\n\s*\n', text):
        n = estimate_tokens(para)
        if n > max_tokens:
            flush()
            # Too big for one part: cut by characters, scaled from the estimate.
            step = max(1, int(len(para) * max_tokens / n))
            parts.extend(para[i:i + step] for i in range(0, len(para), step))
            continue
        if size + n > max_tokens:
            flush()
        current.append(para)
        size += n
    flush()
    return [p for p in parts if p.strip()]


WORD = re.compile(r"[a-z0-9][a-z0-9\-']+")
STOP = set('the a an and or of to in on for with by is are was were be been as at from that this it its their'
           ' which into than then also can not no but such these those both each other more most using use used'.split())
PASSAGE_WORDS = 220
CHECK_PASSAGES = 5


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


def passages(text: str, size: int = PASSAGE_WORDS, step: int = PASSAGE_WORDS * 3 // 4) -> list[str]:
    """Overlapping windows of the source, so a fact split across a break is still whole in one."""
    w = text.split()
    return [' '.join(w[i:i + size]) for i in range(0, max(1, len(w) - size // 2), step)]


def best_passages(claim: str, chunks: list[str], k: int = CHECK_PASSAGES) -> list[str]:
    """BM25: the passages that share the claim's rarer words, in source order."""
    docs = [Counter(words(c)) for c in chunks]
    n = len(docs)
    if not n:
        return []
    avg = sum(sum(d.values()) for d in docs) / n or 1
    df = Counter(t for d in docs for t in d)
    q = set(words(claim))

    def score(d: Counter) -> float:
        size = sum(d.values())
        return sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * 2.2
                   / (d[t] + 1.2 * (0.25 + 0.75 * size / avg)) for t in q if t in d)
    best = sorted(range(n), key=lambda i: -score(docs[i]))[:k]
    return [chunks[i] for i in sorted(best)]


SENTENCE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“(])')
#: Words that end in a full stop without ending the sentence.
ABBREV = re.compile(r'(?:\b(?:Mr|Mrs|Ms|Dr|Prof|St|Mt|Jr|Sr|vs|etc|al|Fig|Figs|Eq|No|Vol|approx|ca|cf|e\.g|i\.e))\.$', re.I)


def sentences(text: str) -> list[str]:
    """Split on sentence ends, but not after Mrs., e.g., et al. and the like."""
    out: list[str] = []
    for piece in SENTENCE.split(text.strip()):
        if out and ABBREV.search(out[-1]):
            out[-1] += ' ' + piece
        elif piece.strip():
            out.append(piece)
    return out


#: Lead-ins the prompt forbids but models write anyway: "Here are the key points:".
PREAMBLE = re.compile(r'^\s*(?:here(?:\s+is|\s+are|\'s)|below\s+(?:is|are))\b[^\n]*:\s*$', re.I | re.M)
BULLET = re.compile(r'^(\s*[-*•]\s+)(.*)$')


def claims(summary: str) -> list[str]:
    """Each sentence of the opening paragraph and each bullet point."""
    out = []
    for line in summary.splitlines():
        if m := BULLET.match(line):
            out.append(m.group(2).strip())
        elif line.strip():
            out += sentences(line)
    return out


def apply_checks(summary: str, verdicts: dict[str, tuple[str, str]]) -> str:
    """Rewrite the summary: keep supported claims, correct contradicted ones, drop the rest."""
    def fixed(claim: str) -> str | None:
        verdict, fix = verdicts.get(claim, ('supported', ''))
        if verdict == 'contradicted' and fix.strip():
            return fix.strip()
        return None if verdict in ('contradicted', 'unsupported') else claim

    lines = []
    for line in summary.splitlines():
        if m := BULLET.match(line):
            if (c := fixed(m.group(2).strip())) is not None:
                lines.append(m.group(1) + c)
        elif line.strip():
            kept = [c for x in sentences(line) if (c := fixed(x)) is not None]
            if kept:
                lines.append(' '.join(kept))
        else:
            lines.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def parse_verdict(reply: str) -> tuple[str, str]:
    m = re.search(r'\{.*\}', THINK.sub('', reply), re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    verdict = str(data.get('verdict', '')).strip().lower()
    # Can't read the reply: keep the claim. The check should only ever remove what it's sure of.
    return (verdict if verdict in ('supported', 'contradicted', 'unsupported') else 'supported',
            str(data.get('fix') or ''))


class Summariser:
    def __init__(self, complete: Complete, context_tokens: int, check: bool = True):
        self.complete = complete
        self.budget = max(context_tokens - RESERVED_TOKENS, 500)
        self.check = check
        #: What the last check pass did: claims checked, corrected and dropped.
        self.last_check = {'claims': 0, 'corrected': 0, 'dropped': 0}

    def summarise(self, title: str, text: str) -> str:
        text = text.strip()
        if not text:
            return NO_TEXT
        summary = self._write(title, text)
        return self._check(summary, text) if self.check else summary

    def _write(self, title: str, text: str) -> str:
        source = text
        for _ in range(MAX_ROUNDS):
            if estimate_tokens(text) <= self.budget:
                return self._ask(SUMMARY_PROMPT, f'Title: {title}\n\n{text}')
            parts = split_to_fit(text, self.budget)
            notes = [self._ask(PART_PROMPT, f'Title: {title}\nPart {i} of {len(parts)}\n\n{p}')
                     for i, p in enumerate(parts, 1)]
            text = '\n\n'.join(notes)
        return self._ask(SUMMARY_PROMPT, f'Title: {title}\n\n{text[: self.budget * 3] or source[: self.budget * 3]}')

    def _check(self, summary: str, source: str) -> str:
        chunks = passages(source)
        verdicts: dict[str, tuple[str, str]] = {}
        for claim in claims(summary):
            context = '\n\n'.join(f'[P{i}] {p}' for i, p in enumerate(best_passages(claim, chunks), 1))
            verdicts[claim] = parse_verdict(self.complete(CHECK_PROMPT, f'Passages:\n\n{context}\n\nClaim: {claim}'))
        self.last_check = {
            'claims': len(verdicts),
            'corrected': sum(1 for v, f in verdicts.values() if v == 'contradicted' and f.strip()),
            'dropped': sum(1 for v, f in verdicts.values() if v == 'unsupported' or (v == 'contradicted' and not f.strip())),
        }
        checked = apply_checks(summary, verdicts)
        return checked or summary

    def _ask(self, system: str, user: str) -> str:
        reply = PREAMBLE.sub('', THINK.sub('', self.complete(system, user)))
        return re.sub(r'\n{3,}', '\n\n', reply).strip()


def list_models(base_url: str, api_key: str) -> list[str]:
    """Model names the API offers. Ollama lists what's installed."""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key or 'none', timeout=10, max_retries=0)
    return sorted(m.id for m in client.models.list())


def openai_complete(base_url: str, model: str, api_key: str) -> Complete:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key or 'none', timeout=600, max_retries=1)

    def complete(system: str, user: str) -> str:
        r = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        )
        return r.choices[0].message.content or ''

    return complete

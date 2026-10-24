"""Community reports, written by the model from the community's own text.

The context is GraphRAG's text-unit context: the community's chunks, most connected first,
until the budget is full. Each excerpt carries its source's number, so findings can cite it.
"""
import json
import re
from dataclasses import dataclass, field

from ..summarise import THINK, Complete, estimate_tokens

REPORT_PROMPT = """You write a short report on one community in a personal knowledge base.
A community is a group of ideas that keep appearing together across the user's notes and sources.
You get numbered excerpts. Each starts with its source, like [S2].

Rules:
- Use only facts in the excerpts. Don't add facts, opinions or praise.
- Write in English, in plain words and short sentences.
- Cite the sources behind each finding, like [S1] or [S2, S4]. Draw on as many sources as the excerpts allow.
- Write about the ideas. Don't mention "this report", "the excerpts" or "the study".
- Never open the summary with "These excerpts", "This report", "The sources" or the like. Start with the topic itself.
- Only state what the excerpts say. Don't expand an abbreviation they don't spell out. Keep comparisons as they state them.

Reply with one JSON object and nothing else:
{"title": "...", "summary": "...", "findings": [{"summary": "...", "explanation": "..."}]}

- title: 2 to 6 words that name the shared topic. Don't use the words community or report.
- summary: 2 to 4 sentences on what ties the excerpts together. Start with the topic itself, like "EVA foam and worbla are the base materials for…"
- findings: 3 to 6 key points. "summary" is one short line. "explanation" is 2 to 4 sentences, with citations."""

#: Tokens kept free for the prompt and the reply.
RESERVED = 2500
MAX_INPUT = 8000


@dataclass
class Finding:
    summary: str
    explanation: str


@dataclass
class Report:
    title: str
    summary: str
    findings: list[Finding] = field(default_factory=list)

    def public(self) -> dict:
        return {'title': self.title, 'summary': self.summary,
                'findings': [{'summary': f.summary, 'explanation': f.explanation} for f in self.findings]}

    @classmethod
    def from_dict(cls, d: dict) -> 'Report':
        return cls(d.get('title', ''), d.get('summary', ''),
                   [Finding(f.get('summary', ''), f.get('explanation', '')) for f in d.get('findings', [])])


def budget(context_tokens: int) -> int:
    return max(1000, min(MAX_INPUT, context_tokens - RESERVED))


def build_context(chunk_ids: set[str], chunk_text: dict[str, str], chunk_doc: dict[str, str],
                  doc_title: dict[str, str], members: set[str], phrases: dict[str, list[str]],
                  degree: dict[str, int], max_tokens: int) -> tuple[str, list[str]]:
    """Excerpts for one community, and the documents they came from, in citation order.

    GraphRAG takes the most connected chunks first, so one long source can fill the budget.
    Here sources take turns: each one's best chunk, then each one's next best, and so on.
    """
    def weight(cid: str) -> int:
        return sum(degree.get(p, 0) for p in phrases.get(cid, []) if p in members)

    per_doc: dict[str, list[str]] = {}
    for cid in sorted(chunk_ids, key=lambda c: (-weight(c), c)):
        per_doc.setdefault(chunk_doc[cid], []).append(cid)
    queues = sorted(per_doc.values(), key=lambda q: (-weight(q[0]), q[0]))
    order = [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]
    docs: list[str] = []
    parts: list[str] = []
    used = 0
    for cid in order:
        doc = chunk_doc[cid]
        num = docs.index(doc) + 1 if doc in docs else len(docs) + 1
        head = f'[S{num}] {doc_title.get(doc, doc)}\n'
        text = chunk_text[cid]
        n = estimate_tokens(head + text)
        if used + n > max_tokens:
            if parts:
                break
            text = text[: (max_tokens - estimate_tokens(head)) * 4]  # one huge chunk: take its start
            n = max_tokens
        if doc not in docs:
            docs.append(doc)
        parts.append(head + text)
        used += n
    return '\n\n'.join(parts), docs


def parse(reply: str) -> Report:
    reply = THINK.sub('', reply).strip()
    m = re.search(r'\{.*\}', reply, re.S)
    if not m:
        raise ValueError('The model did not reply with JSON.')
    raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(re.sub(r',\s*([}\]])', r'\1', raw))  # trailing commas
    report = Report.from_dict(data)
    if not report.title.strip() or not report.summary.strip():
        raise ValueError('The report has no title or summary.')
    report.title = re.sub(r'\s+', ' ', report.title).strip().strip('"')
    return report


#: An opening that talks about the text instead of the topic: "These excerpts discuss…", "This report examines…".
META_OPENING = re.compile(
    r'^\s*(?:these|the|this|our|both|all)\s+(?:\w+\s+)?(?:excerpts?|sources?|passages?|documents?|texts?|reports?|'
    r'notes?|stud(?:y|ies)|papers?|community|materials?|works?)\s+(?:\w+\s+)?'
    r'(?:discuss|describe|detail|cover|explore|examine|focus|highlight|present|show|outline|address|provide|'
    r'investigate|introduce|reveal|center|centre|concern|deal|emphasi[sz]e|delve|offer|look)\w*\s+(?:on\s+|in\s+|with\s+)?',
    re.I)

#: A rewrite that still starts with the text as its subject is no better, verb or not.
META_NOUN = re.compile(r'^\s*(?:these|this|the)\s+(?:\w+\s+)?(?:excerpts?|sources?|reports?|passages?|documents?)\b', re.I)

REWRITE_PROMPT = """Rewrite this paragraph so it starts with the topic itself.
Don't open with "these excerpts", "this report", "the sources" or the like.
Keep every fact and every citation like [S1]. Reply with the paragraph only."""


def fix_opening(complete: Complete, summary: str) -> str:
    """Ask once for a rewrite. If the model still opens with the meta phrase, cut it off."""
    if not META_OPENING.match(summary):
        return summary
    try:
        again = THINK.sub('', complete(REWRITE_PROMPT, summary)).strip().strip('"')
    except Exception:  # noqa: BLE001  the cut below still works
        again = ''
    if again and not META_OPENING.match(again) and not META_NOUN.match(again) and len(again) > len(summary) // 2:
        return again
    rest = META_OPENING.sub('', summary, count=1)
    return rest[:1].upper() + rest[1:]


def write_report(complete: Complete, context: str) -> Report:
    try:
        report = parse(complete(REPORT_PROMPT, f'Excerpts:\n\n{context}'))
    except (ValueError, json.JSONDecodeError):
        # One retry: small models sometimes wrap or break the JSON.
        report = parse(complete(REPORT_PROMPT + '\n\nReply with the JSON object only.', f'Excerpts:\n\n{context}'))
    report.summary = fix_opening(complete, report.summary)
    return report

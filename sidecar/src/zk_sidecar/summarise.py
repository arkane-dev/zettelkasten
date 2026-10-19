"""Summaries through any OpenAI-compatible API.

Ollama's OpenAI API can't set the context length per request, so a long source is
split into parts that fit the model's context. Each part gets notes, then the notes
get the summary.
"""
import re
from collections.abc import Callable

SUMMARY_PROMPT = """You summarise a source for a personal knowledge base.
Write in English, in plain words and short sentences.
Only state what the source says. Don't add facts, opinions or praise.

Format:
One paragraph of 2 to 4 sentences: what the source is and its main point.
Then 3 to 7 bullet points with the key ideas, findings, methods or steps.
No heading and no preamble."""

PART_PROMPT = """You take notes on one part of a longer source.
List its key points as short bullet points, in English and plain words.
Only state what the text says. No heading and no preamble."""

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


class Summariser:
    def __init__(self, complete: Complete, context_tokens: int):
        self.complete = complete
        self.budget = max(context_tokens - RESERVED_TOKENS, 500)

    def summarise(self, title: str, text: str) -> str:
        text = text.strip()
        if not text:
            return NO_TEXT
        for _ in range(MAX_ROUNDS):
            if estimate_tokens(text) <= self.budget:
                return self._ask(SUMMARY_PROMPT, f'Title: {title}\n\n{text}')
            parts = split_to_fit(text, self.budget)
            notes = [self._ask(PART_PROMPT, f'Title: {title}\nPart {i} of {len(parts)}\n\n{p}')
                     for i, p in enumerate(parts, 1)]
            text = '\n\n'.join(notes)
        return self._ask(SUMMARY_PROMPT, f'Title: {title}\n\n{text[: self.budget * 3]}')

    def _ask(self, system: str, user: str) -> str:
        return THINK.sub('', self.complete(system, user)).strip()


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

from zk_sidecar.summarise import NO_TEXT, PART_PROMPT, SUMMARY_PROMPT, Summariser, estimate_tokens, split_to_fit


def test_estimate_counts_cjk_per_character():
    assert estimate_tokens('a' * 400) == 101
    assert estimate_tokens('麻婆豆腐' * 10) == 41


def test_split_keeps_paragraphs_whole():
    paras = [f'Paragraph {i}. ' + 'word ' * 50 for i in range(10)]
    parts = split_to_fit('\n\n'.join(paras), 200)
    assert len(parts) > 1
    assert all(estimate_tokens(p) <= 200 for p in parts)
    assert '\n\n'.join(parts) == '\n\n'.join(paras)


def test_split_cuts_one_huge_paragraph():
    parts = split_to_fit('x' * 10_000, 500)
    assert all(estimate_tokens(p) <= 501 for p in parts)
    assert ''.join(parts) == 'x' * 10_000


class Recorder:
    def __init__(self):
        self.systems: list[str] = []

    def __call__(self, system: str, user: str) -> str:
        self.systems.append(system)
        return '- a short point'


def test_short_text_is_one_call():
    r = Recorder()
    Summariser(r, 8192).summarise('T', 'short text')
    assert r.systems == [SUMMARY_PROMPT]


def test_long_text_is_split_then_summarised():
    r = Recorder()
    text = '\n\n'.join('word ' * 400 for _ in range(60))  # about 30k tokens
    Summariser(r, 8192).summarise('T', text)
    assert r.systems[-1] == SUMMARY_PROMPT
    assert r.systems[:-1] == [PART_PROMPT] * (len(r.systems) - 1)
    assert len(r.systems) >= 5


def test_empty_text_needs_no_llm():
    r = Recorder()
    assert Summariser(r, 8192).summarise('T', '  ') == NO_TEXT
    assert r.systems == []

import json

from zk_sidecar.summarise import (CHECK_PROMPT, NO_TEXT, PART_PROMPT, SUMMARY_PROMPT, Summariser, apply_checks,
                                  best_passages, claims, estimate_tokens, parse_verdict, passages, split_to_fit)


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
    Summariser(r, 8192, check=False).summarise('T', 'short text')
    assert r.systems == [SUMMARY_PROMPT]


def test_long_text_is_split_then_summarised():
    r = Recorder()
    text = '\n\n'.join('word ' * 400 for _ in range(60))  # about 30k tokens
    Summariser(r, 8192, check=False).summarise('T', text)
    assert r.systems[-1] == SUMMARY_PROMPT
    assert r.systems[:-1] == [PART_PROMPT] * (len(r.systems) - 1)
    assert len(r.systems) >= 5


def test_empty_text_needs_no_llm():
    r = Recorder()
    assert Summariser(r, 8192).summarise('T', '  ') == NO_TEXT
    assert r.systems == []


def test_prompts_forbid_guessing():
    for prompt in (SUMMARY_PROMPT, PART_PROMPT):
        assert "Don't expand an abbreviation" in prompt and 'Keep comparisons as the source states them' in prompt
    assert "don't guess what it means" in PART_PROMPT


def test_claims_are_sentences_and_bullets():
    s = 'ReAct is a method. It "thinks" first. Short.\n\n- One point.\n* Two point.'
    assert claims(s) == ['ReAct is a method.', 'It "thinks" first.', 'Short.', 'One point.', 'Two point.']


def test_best_passages_find_the_rare_words():
    text = ' '.join(['filler'] * 2000) + ' ReAct-IM is ReAct with Inner Monologue style thoughts. ' + ' '.join(['filler'] * 2000)
    found = best_passages('ReAct-IM uses Inner Monologue', passages(text), k=1)
    assert 'Inner Monologue' in found[0]


def test_unreadable_verdicts_keep_the_claim():
    assert parse_verdict('no json') == ('supported', '')
    assert parse_verdict('<think>x</think>{"verdict": "Contradicted", "fix": "B beats A."}') == ('contradicted', 'B beats A.')
    assert parse_verdict('{"verdict": "maybe"}') == ('supported', '')


def test_apply_checks_keeps_format():
    s = 'Para one. Para two.\n\n* Keep.\n* Wrong.\n* Gone.'
    out = apply_checks(s, {'Para two.': ('unsupported', ''), 'Wrong.': ('contradicted', 'Right.'), 'Gone.': ('contradicted', '')})
    assert out == 'Para one.\n\n* Keep.\n* Right.'


class Checker:
    """Writes a summary with one wrong and one made-up claim, then judges them."""
    def __init__(self):
        self.calls = []

    def __call__(self, system: str, user: str) -> str:
        self.calls.append(system)
        if system == SUMMARY_PROMPT:
            return 'ReAct mixes thinking and acting.\n\n- ReAct-IM beats ReAct.\n- ReAct-IM adds memory.\n- ReAct uses Wikipedia.'
        claim = user.rsplit('Claim: ', 1)[1]
        verdict = {'ReAct-IM beats ReAct.': {'verdict': 'contradicted', 'fix': 'ReAct beats ReAct-IM.'},
                   'ReAct-IM adds memory.': {'verdict': 'unsupported', 'fix': ''}}.get(claim, {'verdict': 'supported', 'fix': ''})
        return json.dumps(verdict)


def test_check_pass_corrects_and_drops():
    llm = Checker()
    s = Summariser(llm, 8192)
    out = s.summarise('ReAct', 'ReAct mixes thinking and acting. It uses a Wikipedia API. ReAct beats ReAct-IM on ALFWorld.')
    assert out == 'ReAct mixes thinking and acting.\n\n- ReAct beats ReAct-IM.\n- ReAct uses Wikipedia.'
    assert llm.calls.count(CHECK_PROMPT) == 4
    assert s.last_check == {'claims': 4, 'corrected': 1, 'dropped': 1}


def test_check_never_empties_a_summary():
    class Doubter(Checker):
        def __call__(self, system, user):
            return 'One claim.' if system == SUMMARY_PROMPT else '{"verdict": "unsupported"}'
    assert Summariser(Doubter(), 8192).summarise('T', 'text') == 'One claim.'


def test_sentences_skip_abbreviations():
    from zk_sidecar.summarise import sentences
    assert sentences('It follows Mrs. Beeton, with modern steps. See Fig. 3 and Smith et al. for more. Done.') == [
        'It follows Mrs. Beeton, with modern steps.', 'See Fig. 3 and Smith et al. for more.', 'Done.']


def test_preamble_is_stripped():
    out = Summariser(lambda s, u: 'Here are the key points:\n\n* One.\n* Two.', 8192, check=False).summarise('T', 'x')
    assert out == '* One.\n* Two.'

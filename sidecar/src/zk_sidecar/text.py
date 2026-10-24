"""Text clean-up shared by summaries and the graph."""
import re

# Reference lists and appendices add authors and venues, not ideas.
REF_HEADING = re.compile(r'^\s*(?:#+\s*)?(?:(?:[A-Z]|\d+)\.?\s+)?(references|bibliography|appendix|appendices)\s*$', re.I)
REF_START = re.compile(r'^\s*(\[1\]|1\.)\s+\S')
REF_NEXT = re.compile(r'^\s*(\[2\]|2\.)\s+\S')
CITATION = re.compile(r'\b(19|20)\d\d\b|arXiv', re.I)


def strip_references(text: str) -> str:
    """Cut a paper at its reference list or appendix. Headings count only past 20% of the text."""
    lines = text.splitlines()
    for i in range(int(len(lines) * 0.2), len(lines)):
        if REF_HEADING.match(lines[i]):
            return '\n'.join(lines[:i]) + '\n'
        if (i >= len(lines) * 0.4 and REF_START.match(lines[i])
                and any(REF_NEXT.match(x) for x in lines[i + 1:i + 12])
                and CITATION.search(' '.join(lines[i:i + 4]))):
            return '\n'.join(lines[:i]) + '\n'
    return text

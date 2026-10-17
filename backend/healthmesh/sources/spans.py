"""Half-open Unicode character offsets into the sanitized decoded view."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SpanDraft:
    start: int
    end: int
    quoted_text: str


def line_spans(text):
    offset = 0
    spans = []
    for line in text.splitlines(keepends=True):
        quote = line.rstrip('\r\n')
        if quote.strip():
            spans.append(SpanDraft(offset, offset + len(quote), quote))
        offset += len(line)
    return spans

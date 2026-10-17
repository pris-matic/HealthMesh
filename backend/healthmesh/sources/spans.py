"""Half-open Unicode character offsets into the sanitized decoded view."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SpanDraft:
    start: int
    end: int
    quoted_text: str


def valid_span_offsets(span, text):
    """Check half-open Unicode offsets before slicing any cited evidence."""
    start, end = getattr(span, 'start', None), getattr(span, 'end', None)
    return (isinstance(text, str) and type(start) is int and type(end) is int
            and 0 <= start < end <= len(text))


def line_spans(text):
    offset = 0
    spans = []
    for line in text.splitlines(keepends=True):
        quote = line.rstrip('\r\n')
        if quote.strip():
            spans.append(SpanDraft(offset, offset + len(quote), quote))
        offset += len(line)
    return spans

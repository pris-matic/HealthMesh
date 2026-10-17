"""Strict UTF-8 text and explicit synthetic inventory CSV contracts."""
import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import ValidationError

from .sanitizer import sanitize
from .spans import SpanDraft, line_spans

REQUIRED = {'synthetic', 'product_code', 'batch_code', 'base_unit', 'reserve_accounting',
            'on_hand', 'reserved', 'protected_reserve', 'observed_at'}


class SourceTooLarge(ValidationError):
    pass


@dataclass(frozen=True)
class ParsedSource:
    kind: str
    amd_text: str
    spans: list
    rows: list


def reject(message):
    raise ValidationError({'file': message})


def parse_source(filename, content):
    if len(content) > settings.SOURCE_MAX_BYTES:
        raise SourceTooLarge({'file': 'The source exceeds the 1 MiB upload limit.'})
    extension = PurePath(filename).suffix.lower()
    if extension not in {'.csv', '.txt'}:
        reject('Choose a UTF-8 .csv or .txt source.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        reject('Source text must be valid UTF-8.')
    if not text.strip() or any(ord(c) < 32 and c not in '\t\r\n' for c in text):
        reject('A nonempty source without binary control characters is required.')
    if len(text.splitlines()) > 2000:
        reject('Sources may contain at most 2000 lines.')
    amd_text = sanitize(text)
    if extension == '.txt':
        if not re.match(r'\s*SYNTHETIC\b', text, re.I):
            reject('Plain text must begin with SYNTHETIC and contain only fictional supply information.')
        return ParsedSource('RAW_TEXT', amd_text, line_spans(amd_text), [])
    return parse_csv(amd_text)


def parse_csv(text):
    # Use the CSV reader's physical line count to issue a complete row span,
    # including embedded newlines when a quoted CSV value spans multiple lines.
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    rows, spans = [], []
    try:
        headers = next(reader)
        if len(headers) != len(REQUIRED) or set(headers) != REQUIRED:
            reject('CSV headers must be exactly: ' + ', '.join(sorted(REQUIRED)) + '.')
        previous = reader.line_num
        for cells in reader:
            start = offsets[previous]
            end = offsets[reader.line_num]
            previous = reader.line_num
            if len(cells) != len(headers):
                reject('Every CSV row must contain exactly the declared fields.')
            row = dict(zip(headers, cells))
            if row['synthetic'] != 'true' or row['product_code'] != 'SIM-MX-01':
                reject('Only synthetic=true and product_code=SIM-MX-01 are accepted.')
            if row['base_unit'] != 'box' or row['reserve_accounting'] != 'separate':
                reject('CSV requires base_unit=box and reserve_accounting=separate.')
            for field in ('on_hand', 'reserved', 'protected_reserve'):
                if not re.fullmatch(r'[0-9]{1,9}', row[field]):
                    reject('Quantities must be nonnegative whole numbers of at most nine digits.')
                row[field] = int(row[field])
            try:
                if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)', row['observed_at']):
                    raise ValueError
                observed = datetime.fromisoformat(row['observed_at'])
                if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
                    raise ValueError
            except ValueError:
                reject('observed_at must be an ISO timestamp with a UTC timezone.')
            row['observed_at'] = observed
            quote = text[start:end].rstrip('\r\n')
            spans.append(SpanDraft(start, start + len(quote), quote))
            rows.append(row)
    except (csv.Error, StopIteration):
        reject('The source is not a valid CSV inventory table.')
    if not rows:
        reject('CSV must contain at least one inventory row.')
    return ParsedSource('CSV', text, spans, rows)

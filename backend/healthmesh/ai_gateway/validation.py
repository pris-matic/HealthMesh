"""Pure v1 extraction validation. Source and model instructions are only data.

Input is a JSON string or decoded {"claims": [...]} object. Each claim uses
ExtractedClaim's schema. Units must match the supplied base unit; the sole
spelling alias is `boxes` for `box`. No conversion or implicit unit is inferred.
No database writes, tools, inference calls, or canonical promotion occur here.
Pass the original model JSON content string, not a float-decoded intermediate.
JSON fractional amounts and decoded Decimal values retain exact precision;
decoded Python float amounts are rejected because their lexemes are lost.
"""
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
import json
import re
from typing import TYPE_CHECKING
import unicodedata

from pydantic import ValidationError

from healthmesh.sources.spans import valid_span_offsets
from .types import ExtractedClaim, QuarantinedClaim, ValidatedClaim, ValidatedClaims

if TYPE_CHECKING:
    from healthmesh.models import SourceSpan


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _supported_quantity(value, unit, quote, field):
    units = '(?:box|boxes)' if unit == 'box' else re.escape(unit)
    # Prevent matching a suffix of a negative, decimal, grouped number or date.
    pattern = rf'(?<![\w.,+\-])([0-9]+(?:\.[0-9]+)?)\s+{units}(?!\w)'
    counted_offsets = {match.span(1) for match in re.finditer(
        r'(?<!\w)counted\s+([0-9]+(?:\.[0-9]+)?)\s+box(?:es)?\b', quote,
        flags=re.IGNORECASE)} if unit == 'box' else set()
    reserved_offsets = {match.span(1) for match in re.finditer(
        r'(?<![\w.,+\-])([0-9]+(?:\.[0-9]+)?)\s+box(?:es)?\s+reserved\b',
        quote, flags=re.IGNORECASE)} if unit == 'box' else set()
    for match in re.finditer(pattern, quote):
        if (field == 'reserved' and match.span(1) in counted_offsets
                or field == 'on_hand' and match.span(1) in reserved_offsets):
            continue
        prefix = quote[:match.start()]
        previous = prefix[-1:]
        nearest = prefix.rstrip()[-1:]
        # Unicode minus/dashes and grouping marks belong to the numeric token.
        # Also reject a suffix after a spaced sign or digit grouping separator.
        if previous and (previous in "'’ʼ" or unicodedata.category(previous) in {'Pd', 'Sm'}):
            continue
        if nearest and (nearest.isdigit() or unicodedata.category(nearest) in {'Pd', 'Sm'}):
            continue
        if Decimal(match.group(1)) == Decimal(str(value)):
            return True
    return False


def _reserved_counted_clause(value, unit, quote):
    """The Brook note's explicit unit scopes one adjacent reserved amount.

    This is deliberately narrower than general unit inheritance: the two
    unsigned, ungrouped integers must occur in the same exact counted clause.
    """
    if unit != 'box':
        return False
    pattern = r'(?<!\w)counted\s+[0-9]+\s+box(?:es)?,\s+([0-9]+)\s+reserved(?!\w)'
    return any(Decimal(match.group(1)) == Decimal(str(value))
               for match in re.finditer(pattern, quote, flags=re.IGNORECASE))


def validate_extraction(raw: object, allowed_spans: Sequence['SourceSpan'], base_unit: str) -> ValidatedClaims:
    """Accept only supported claims; quarantine invalid candidates by index."""
    def invalid_output():
        return ValidatedClaims(quarantined=(QuarantinedClaim(reason='INVALID_OUTPUT'),))

    if not isinstance(base_unit, str) or not base_unit or base_unit.strip() != base_unit:
        return invalid_output()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw, object_pairs_hook=_unique_object, parse_float=Decimal,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, ArithmeticError, RecursionError):
            return invalid_output()
    if (type(raw) is not dict or set(raw) != {'claims'} or type(raw['claims']) is not list):
        return invalid_output()

    spans = {}
    duplicates = set()
    for span in allowed_spans:
        span_id = str(getattr(span, 'id', None))
        if span_id in spans:
            duplicates.add(span_id)
        spans[span_id] = span
    accepted, quarantined = [], []
    for index, item in enumerate(raw['claims']):
        def reject(reason):
            quarantined.append(QuarantinedClaim(index=index, reason=reason))

        if type(item) is not dict:
            reject('INVALID_CLAIM')
            continue
        if isinstance(item.get('field'), str) and item['field'] not in {'on_hand', 'reserved', 'observed_at', 'uncertainty'}:
            reject('UNSUPPORTED_FIELD')
            continue
        try:
            claim = ExtractedClaim.model_validate(item)
        except (ValidationError, ValueError):
            reject('INVALID_CLAIM')
            continue
        span_id = str(claim.source_span_id)
        span = spans.get(span_id)
        if span is None:
            reject('UNKNOWN_SPAN')
            continue
        text = getattr(getattr(span, 'source', None), 'decoded_text', None)
        if span_id in duplicates or not valid_span_offsets(span, text):
            reject('INVALID_SPAN')
            continue
        quote = text[span.start:span.end]
        if claim.quote is not None and claim.quote != quote:
            reject('QUOTE_MISMATCH')
            continue
        if claim.field in {'on_hand', 'reserved'}:
            if claim.unit != base_unit:
                reject('UNIT_MISMATCH')
                continue
            supported = (_supported_quantity(claim.value, base_unit, quote, claim.field)
                         or claim.field == 'reserved' and _reserved_counted_clause(claim.value, base_unit, quote))
        else:
            supported = claim.unit is None and re.search(rf'(?<!\w){re.escape(claim.value)}(?!\w)', quote) is not None
            if claim.field == 'observed_at':
                try:
                    date = datetime.fromisoformat(claim.value)
                    supported = supported and date.tzinfo is not None and date.utcoffset() is not None
                except ValueError:
                    supported = False
        if not supported:
            reject('VALUE_NOT_SUPPORTED')
            continue
        accepted.append(ValidatedClaim(**(claim.model_dump() | {'quote': quote})))
    return ValidatedClaims(accepted=tuple(accepted), quarantined=tuple(quarantined))

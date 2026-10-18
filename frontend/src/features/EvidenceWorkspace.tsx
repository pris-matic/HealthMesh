import { useEffect, useRef, useState } from 'react';
import type { EvidenceClaimDto, EvidenceDto, GroundedWordingDto } from '../types';

function label(field: string) {
  return field.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
}

function valueText(claim: EvidenceClaimDto) {
  if (typeof claim.value === 'number') return `${claim.value} ${['on_hand', 'reserved', 'protected_reserve', 'quantity'].includes(claim.field) ? 'boxes' : ''}`.trim();
  return typeof claim.value === 'string' ? claim.value : JSON.stringify(claim.value);
}

function observed(iso: string) {
  return new Date(iso).toLocaleString('en-PH', { timeZone: 'Asia/Manila', dateStyle: 'medium', timeStyle: 'short' }) + ' Asia/Manila';
}

function statusLabel(status: EvidenceClaimDto['status']) {
  return ({ OBSERVED_UNVERIFIED: 'Observed, unverified', CONFLICTED: 'Conflicted',
    HUMAN_VERIFIED: 'Human verified', DERIVED_FROM_VERIFIED: 'Derived from verified',
    UNKNOWN: 'Unknown', STALE: 'Stale' })[status];
}

function ClaimCard({ claim }: { claim: EvidenceClaimDto }) {
  return <article className="evidence-claim">
    <strong>{valueText(claim)}</strong>
    <p><span className="badge">{statusLabel(claim.status)}</span>{claim.field_status === 'CONFLICTED' &&
      <span className="badge conflict">Conflicted</span>} <span>{claim.freshness === 'STALE' ? 'Stale observation' : 'Fresh observation'}</span></p>
    <p>Observed {observed(claim.observed_at)}</p>
    <blockquote>{claim.span?.quote ?? 'Exact source quote unavailable for this historical claim.'}</blockquote>
    <p>Source {claim.source.id} · {claim.source.kind.replaceAll('_', ' ').toLowerCase()}</p>
    <p>Claim {claim.id}{claim.receipt_id && <> · AMD receipt {claim.receipt_id}</>}</p>
  </article>;
}

export function EvidenceWorkspace({ evidence, onExplain }: {
  evidence: EvidenceDto;
  onExplain?: (conflictId: string) => Promise<GroundedWordingDto>;
}) {
  const [wording, setWording] = useState<Record<string, GroundedWordingDto>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [wordingError, setWordingError] = useState<Record<string, string>>({});
  const evidenceKey = `${evidence.incident_id}:${evidence.revision}`;
  const currentEvidenceKey = useRef(evidenceKey);
  currentEvidenceKey.current = evidenceKey;
  useEffect(() => { setWording({}); setWordingError({}); }, [evidenceKey]);
  const open = evidence.conflicts.filter(conflict => conflict.state === 'OPEN');
  const conflictingIds = new Set(open.flatMap(conflict => conflict.claim_ids));
  const other = evidence.claims.filter(claim => !conflictingIds.has(claim.id));
  return <section className="panel evidence-workspace" aria-labelledby="evidence-title">
    <h2 id="evidence-title">Evidence workspace</h2>
    <p>Observations retain their original source and time. A conflict requires human resolution before the affected fact can support a ready plan.</p>
    <p>Incident revision {evidence.revision} · {open.length} open conflict{open.length === 1 ? '' : 's'}</p>
    {open.map(conflict => <section key={conflict.id} role="region" aria-label={`${label(conflict.field)} conflict`}>
      <h3>{label(conflict.field)} · Conflicted</h3>
      <div className="evidence-grid">{evidence.claims.filter(claim => conflict.claim_ids.includes(claim.id)).map(claim =>
        <ClaimCard key={claim.id} claim={claim} />)}</div>
      {onExplain && conflict.field === 'on_hand' && <button className="secondary" type="button" disabled={busy !== null}
        onClick={async () => { const requestedKey = evidenceKey;
          setBusy(conflict.id); setWordingError(current => ({ ...current, [conflict.id]: '' }));
          try { const result = await onExplain(conflict.id);
            if (currentEvidenceKey.current === requestedKey) setWording(current => ({ ...current, [conflict.id]: result })); }
          catch (err) { if (currentEvidenceKey.current === requestedKey) setWordingError(current => ({ ...current, [conflict.id]:
            err instanceof Error ? err.message : 'Could not request explanation.' })); }
          finally { setBusy(null); }
        }}>Ask AMD to explain {conflict.field.replaceAll('_', ' ')} conflict</button>}
      {wordingError[conflict.id] && <p role="alert">{wordingError[conflict.id]}</p>}
      {wording[conflict.id] && <aside className="panel" aria-label="Conflict explanation" role="status">
        <strong>{wording[conflict.id].source === 'AMD_GENERATED_UNVERIFIED_WORDING'
          ? 'AI generated wording, unverified' : 'Deterministic explanation'}</strong>
        <p>{wording[conflict.id].text}</p>
        <p>Source spans: {wording[conflict.id].cited_span_ids.join(', ')}</p>
        {wording[conflict.id].receipt_id && <p>AMD receipt {wording[conflict.id].receipt_id}</p>}
      </aside>}
    </section>)}
    <details><summary>Other source observations ({other.length})</summary>
      <div className="evidence-grid">{other.map(claim => <ClaimCard key={claim.id} claim={claim} />)}</div>
    </details>
  </section>;
}

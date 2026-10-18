import { useEffect, useState } from 'react';
import type { GroundedWordingDto, RankedVerificationDto, RankingDto } from '../types';

function readableStatus(value: string) {
  return value === 'READY_FOR_REVIEW' ? 'Ready for review' :
    value === 'INFEASIBLE' ? 'Infeasible' : 'Provisional';
}

export function VerificationDesk({ ranking, onSelect, canSelect, selectedFallback, onDraft }: {
  ranking: RankingDto;
  onSelect: (subjectId: string, field: RankedVerificationDto['field']) => void;
  canSelect: boolean;
  selectedFallback?: GroundedWordingDto | null;
  onDraft?: (taskId: string) => Promise<GroundedWordingDto>;
}) {
  const [wording, setWording] = useState<GroundedWordingDto | null>(selectedFallback ?? null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftError, setDraftError] = useState('');
  useEffect(() => { setWording(selectedFallback ?? null); setDraftError(''); }, [ranking.selected_task?.id, selectedFallback]);
  return <section aria-labelledby="verification-title">
    <div className="section-heading"><h2 id="verification-title">Verification desk</h2>
      <span>{ranking.remaining_questions} question · {ranking.remaining_staff_minutes} staff minutes remaining</span></div>
    <p>Questions are ranked by whether an answer can change the preferred guarded action. {ranking.valuation_mode === 'DETERMINISTIC_SENSITIVITY'
      ? 'No probabilities are assigned to the public possibilities.'
      : 'Explicit public synthetic weights determine expected loss under LOSS_POLICY_V1.'}</p>
    {ranking.net_value_estimate !== null && <p>Best estimated gain: {ranking.net_value_estimate.toFixed(2)} synthetic loss units.</p>}
    {ranking.must_verify_before_approval && <p className="badge">Mandatory verification before approval</p>}
    {ranking.selected_task && <p role="status">Selected {ranking.selected_task.field.replaceAll('_', ' ')} question · due at simulated time {ranking.selected_task.due_at}.</p>}
    {ranking.selected_task && <div className="panel">
      {wording && <><strong>{wording.source === 'AMD_GENERATED_UNVERIFIED_WORDING'
        ? 'AI generated wording, unverified' : 'Deterministic question'}</strong>
        <p>{wording.text}</p>
        <p>Source spans: {wording.cited_span_ids.join(', ') || 'Unavailable'}</p>
        {wording.receipt_id && <p>AMD receipt {wording.receipt_id}</p>}</>}
      {onDraft && <button className="secondary" type="button" disabled={draftBusy}
        onClick={async () => { setDraftBusy(true); setDraftError('');
          try { setWording(await onDraft(ranking.selected_task!.id)); }
          catch (err) { setDraftError(err instanceof Error ? err.message : 'Could not request a draft.'); }
          finally { setDraftBusy(false); }
        }}>Ask AMD to draft selected question</button>}
      {draftError && <p role="alert">{draftError}</p>}
    </div>}
    {ranking.tasks.length === 0 && <p>No positive optional question is available within the remaining budget and deadline.</p>}
    <div className="verification-options">{ranking.tasks.map(option =>
      <article className="panel" aria-label={`${option.facility_name} ${option.field.replaceAll('_', ' ')}`} key={`${option.subject_id}:${option.field}`}>
        <h3>{option.facility_name} · {option.field.replaceAll('_', ' ')}</h3>
        <p>{option.staff_minutes} staff minutes · {option.response_delay_minutes} minute response delay</p>
        <p>{option.sensitivity_count} public-world preferred action changes · {option.sensitivity_per_staff_minute.toFixed(2)} changes per staff minute</p>
        {option.net_value_estimate !== null && <p>Estimated gain: {option.net_value_estimate.toFixed(2)} synthetic loss units.</p>}
        <ul>{option.possible_effects.map(effect => <li key={effect.answer}>
          {effect.answer} → {option.facility_name} branch: {readableStatus(effect.target_branch_status)}
        </li>)}</ul>
        <p className="muted">Evidence IDs: {option.evidence_ids.join(', ') || 'No prior observation'}</p>
        <button type="button" disabled={!canSelect} onClick={() => onSelect(option.subject_id, option.field)}>
          Select {option.facility_name} question
        </button>
      </article>)}</div>
  </section>;
}

import { useEffect, useState, type FormEvent } from 'react';
import type { GroundedWordingDto, RankedVerificationDto, RankingDto, VerificationAnswerInput, VerificationResponseDto } from '../types';

function readableStatus(value: string) {
  return value === 'READY_FOR_REVIEW' ? 'Ready for review' :
    value === 'INFEASIBLE' ? 'Infeasible' : 'Provisional';
}

export function VerificationDesk({ ranking, onSelect, canSelect, selectedFallback, onDraft, canRespond = false, onRespond }: {
  ranking: RankingDto;
  onSelect: (subjectId: string, field: RankedVerificationDto['field']) => void;
  canSelect: boolean;
  selectedFallback?: GroundedWordingDto | null;
  onDraft?: (taskId: string) => Promise<GroundedWordingDto>;
  canRespond?: boolean;
  onRespond?: (taskId: string, answer: VerificationAnswerInput) => Promise<VerificationResponseDto>;
}) {
  const [wording, setWording] = useState<GroundedWordingDto | null>(selectedFallback ?? null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [answerStatus, setAnswerStatus] = useState<VerificationAnswerInput['status']>('CONFIRMED');
  const [answerValue, setAnswerValue] = useState('');
  const [claimedSource, setClaimedSource] = useState('');
  const [responseBusy, setResponseBusy] = useState(false);
  const [responseError, setResponseError] = useState('');
  const [response, setResponse] = useState<VerificationResponseDto | null>(null);
  useEffect(() => { setWording(selectedFallback ?? null); setDraftError(''); }, [ranking.selected_task?.id, selectedFallback]);
  useEffect(() => {
    setAnswerStatus('CONFIRMED'); setAnswerValue(''); setClaimedSource('');
    setResponseBusy(false); setResponseError(''); setResponse(null);
  }, [ranking.selected_task?.id]);
  async function submitResponse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const task = ranking.selected_task;
    if (!task || !canRespond || !onRespond || response || responseBusy) return;
    const source = claimedSource.trim();
    if (!source) { setResponseError('Name the synthetic human source.'); return; }
    const answer: VerificationAnswerInput = { status: answerStatus, claimed_source: source,
      expected_revision: ranking.revision };
    if (answerStatus === 'CONFIRMED') {
      if (task.field === 'on_hand') {
        const count = Number(answerValue);
        if (!answerValue.trim() || !Number.isSafeInteger(count) || count < 0 || !task.unit) {
          setResponseError('Enter a nonnegative whole count in the selected unit.'); return;
        }
        answer.value = count; answer.unit = task.unit;
      } else answer.value = answerValue === 'CLOSED' ? 'CLOSED' : 'OPEN';
    }
    setResponseBusy(true); setResponseError('');
    try { setResponse(await onRespond(task.id, answer)); }
    catch (err) { setResponseError(err instanceof Error ? err.message : 'Could not record response.'); }
    finally { setResponseBusy(false); }
  }
  return <section aria-labelledby="verification-title">
    <div className="section-heading"><h2 id="verification-title">Verification desk</h2>
      <span>{ranking.remaining_questions} question · {ranking.remaining_staff_minutes} staff minutes remaining</span></div>
    <p>Questions are ranked by whether an answer can change the preferred guarded action. {ranking.valuation_mode === 'DETERMINISTIC_SENSITIVITY'
      ? 'No probabilities are assigned to the public possibilities.'
      : 'Explicit public synthetic weights determine expected loss under LOSS_POLICY_V1.'}</p>
    {ranking.net_value_estimate !== null && <p>Best estimated gain: {ranking.net_value_estimate.toFixed(2)} synthetic loss units.</p>}
    {ranking.must_verify_before_approval && <p className="badge">Mandatory verification before approval</p>}
    {ranking.selected_task && <p role="status">Selected {ranking.selected_task.field.replaceAll('_', ' ')} question · scheduled simulated arrival {ranking.selected_task.due_at} · request deadline {ranking.selected_task.need_by ?? 'unavailable'}.</p>}
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
      {canRespond && onRespond && !response && <form onSubmit={submitResponse}>
        <h3>Record human response</h3>
        <label>Response status <select value={answerStatus} onChange={event => setAnswerStatus(event.target.value as VerificationAnswerInput['status'])}>
          <option value="CONFIRMED">Confirmed</option><option value="REJECTED">Rejected</option><option value="UNRESOLVED">Unresolved</option>
        </select></label>
        {answerStatus === 'CONFIRMED' && <label>Confirmed value {ranking.selected_task.field === 'on_hand' ?
          <input type="number" min="0" step="1" value={answerValue} onChange={event => setAnswerValue(event.target.value)} /> :
          <select value={answerValue || 'OPEN'} onChange={event => setAnswerValue(event.target.value)}>
            <option value="OPEN">Open</option><option value="CLOSED">Closed</option>
          </select>}</label>}
        {answerStatus === 'CONFIRMED' && ranking.selected_task.unit && <p>Unit: {ranking.selected_task.unit}</p>}
        <label>Named synthetic source <input value={claimedSource} maxLength={200}
          onChange={event => setClaimedSource(event.target.value)} placeholder="For example, Brook physical recount" /></label>
        <button type="submit" disabled={responseBusy}>{responseBusy ? 'Recording…' : 'Record response'}</button>
      </form>}
      {!canRespond && <p>Coordinator sign-in is required to record a response.</p>}
      {responseError && <p role="alert">{responseError}</p>}
      {response && <p role="status">{response.outcome === 'LATE' ? 'Recorded late' : `Recorded ${response.outcome.toLowerCase()}`}
        {' at simulated time '}{response.simulated_at}. {response.outcome === 'CONFIRMED' && response.claim_id
          ? `Human verified claim ${response.claim_id}.` : 'No verified fact was added.'}
        {' Incident revision '}{response.new_revision}.</p>}
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

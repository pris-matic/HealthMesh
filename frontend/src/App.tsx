import { useEffect, useState } from 'react';
import { amdHealth, createScenario, draftVerification, explainConflict, getEvidence, getVerificationOptions, requestPlan, respondToVerification, selectVerification, session } from './api';
import type { EvidenceDto, GroundedWordingDto, PlanDto, RankingDto, ScenarioDto, SessionDto, VerificationAnswerInput, VerificationResponseDto } from './types';
import { Login } from './features/Login';
import { PlanPreview } from './features/PlanPreview';
import { SourceUpload } from './features/SourceUpload';
import { EvidenceWorkspace } from './features/EvidenceWorkspace';
import { VerificationDesk } from './features/VerificationDesk';

export default function App() {
  const [account, setAccount] = useState<SessionDto | null>(null);
  const [scenario, setScenario] = useState<ScenarioDto | null>(null);
  const [plan, setPlan] = useState<PlanDto | null>(null);
  const [evidence, setEvidence] = useState<EvidenceDto | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidenceVersion, setEvidenceVersion] = useState(0);
  const [evidenceError, setEvidenceError] = useState('');
  const [ranking, setRanking] = useState<RankingDto | null>(null);
  const [selectedFallback, setSelectedFallback] = useState<GroundedWordingDto | null>(null);
  const [rankingError, setRankingError] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [amdStatus, setAMDStatus] = useState<'checking' | 'UNAVAILABLE' | 'CONFIGURED'>('checking');
  useEffect(() => { session().then(setAccount).catch(() => setError('Cannot connect to the demo API. Refresh to retry.')); }, []);
  useEffect(() => {
    if (!account?.user) { setAMDStatus('checking'); return; }
    let active = true;
    amdHealth().then(result => { if (active) setAMDStatus(result.status); })
      .catch(() => { if (active) setAMDStatus('UNAVAILABLE'); });
    return () => { active = false; };
  }, [account]);
  const incidentId = scenario?.id;
  const hasPlan = plan !== null;
  useEffect(() => {
    if (!showEvidence || !incidentId) return;
    let active = true;
    getEvidence(incidentId).then(result => { if (active) { setEvidence(result); setEvidenceError(''); } })
      .catch(err => { if (active) setEvidenceError(err instanceof Error ? err.message : 'Could not load evidence.'); });
    return () => { active = false; };
  }, [showEvidence, incidentId, evidenceVersion]);
  useEffect(() => {
    if (!plan || !incidentId) return;
    let active = true;
    getVerificationOptions(incidentId).then(result => { if (active) { setRanking(result); setRankingError(''); } })
      .catch(err => { if (active) setRankingError(err instanceof Error ? err.message : 'Could not load questions.'); });
    return () => { active = false; };
  }, [hasPlan, incidentId, evidenceVersion]);
  async function chooseQuestion(subjectId: string, field: 'on_hand' | 'route_status') {
    if (!incidentId || !ranking) return;
    setBusy(true); setRankingError('');
    try {
      const selection = await selectVerification(incidentId, subjectId, field, ranking.revision);
      setSelectedFallback(selection.grounded_wording);
      setRanking(await getVerificationOptions(incidentId));
    } catch (err) { setRankingError(err instanceof Error ? err.message : 'Could not select question.'); }
    finally { setBusy(false); }
  }
  async function recordVerification(taskId: string, answer: VerificationAnswerInput): Promise<VerificationResponseDto> {
    const result = await respondToVerification(taskId, answer);
    setSelectedFallback(null);
    setEvidenceVersion(version => version + 1);
    if (incidentId) {
      try { setPlan(await requestPlan(incidentId)); }
      catch (err) { setError(`Response recorded, but plan refresh failed: ${err instanceof Error ? err.message : 'please retry.'}`); }
    }
    return result;
  }
  async function generate() {
    setBusy(true); setError(''); setPlan(null);
    try { const incident = scenario ?? await createScenario();
      if (!scenario) setSelectedFallback(null);
      setScenario(incident); setPlan(await requestPlan(incident.id)); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not generate a plan.'); }
    finally { setBusy(false); }
  }
  return <><div className="demo-banner">Synthetic operations demo — no real medical actions</div><main>
    <header><div><p className="eyebrow">HealthMesh / Supply coordination</p><h1>Evidence before action.</h1><p>Compare conditional supply options for three fictional facilities.</p></div>
      {account?.user && <div><p>{account.user.username} · {account.user.role}</p><button className="secondary" disabled={busy} onClick={async () => {
        setBusy(true); setError('');
        try { setAccount(await session('DELETE')); setScenario(null); setPlan(null); setEvidence(null); setRanking(null); setSelectedFallback(null); setShowEvidence(false); }
        catch (err) { setError(err instanceof Error ? err.message : 'Sign-out failed.'); }
        finally { setBusy(false); }
      }}>Sign out</button></div>}
    </header>
    {error && <p role="alert" className="error">{error}</p>}
    {account?.user && <><p role="status">AMD inference: {amdStatus === 'checking' ? 'Checking availability…' : amdStatus}</p>
      {amdStatus === 'UNAVAILABLE' && <p>AI extraction and grounded wording are unavailable. Synthetic fixture planning remains available.</p>}</>}
    {account?.user && amdStatus === 'CONFIGURED' && <p>Configured reports local settings, not a successful AMD call. Each extraction shows its own result.</p>}
    {!account && !error && <p role="status">Connecting…</p>}
    {account && !account.user && <Login onLogin={setAccount} />}
    {account?.user && <><section className="panel scenario"><div><p className="eyebrow">Storm supply · Public synthetic fixture</p><h2>Aster needs 40 boxes</h2><p>Brook has a stale stock count. Cedar’s route is unverified.</p></div>
      <button disabled={busy || account.user.role !== 'coordinator'} onClick={generate}>{busy ? 'Computing…' : plan ? 'Recompute plan' : 'Load scenario and plan'}</button>
      {account.user.role === 'reviewer' && <p>Sign in as a coordinator to create a demo scenario.</p>}</section>
      {scenario && account.user.role === 'coordinator' && <SourceUpload scenario={scenario} onEvidenceChanged={() => setEvidenceVersion(version => version + 1)} />}
      {scenario && <><button className="secondary" type="button" onClick={() => setShowEvidence(value => !value)}>
        {showEvidence ? 'Hide evidence' : 'View evidence'}
      </button>{showEvidence && (evidenceError ? <p role="alert">{evidenceError}</p> : evidence ?
        <EvidenceWorkspace evidence={evidence} onExplain={account.user.role === 'coordinator' ? explainConflict : undefined} /> : <p role="status">Loading evidence…</p>)}</>}
      {plan && <><PlanPreview plan={plan} />
        {rankingError && <p role="alert">{rankingError}</p>}
        {ranking && <VerificationDesk ranking={ranking} onSelect={chooseQuestion}
          selectedFallback={selectedFallback}
          onDraft={account.user.role === 'coordinator' ? draftVerification : undefined}
          onRespond={account.user.role === 'coordinator' ? recordVerification : undefined}
          canRespond={account.user.role === 'coordinator'}
          canSelect={account.user.role === 'coordinator' && !busy && !ranking.selected_task} />}</>}</>}
  </main></>;
}

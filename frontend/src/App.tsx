import { useEffect, useState } from 'react';
import { amdHealth, createScenario, requestPlan, session } from './api';
import type { PlanDto, ScenarioDto, SessionDto } from './types';
import { Login } from './features/Login';
import { PlanPreview } from './features/PlanPreview';
import { SourceUpload } from './features/SourceUpload';

export default function App() {
  const [account, setAccount] = useState<SessionDto | null>(null);
  const [scenario, setScenario] = useState<ScenarioDto | null>(null);
  const [plan, setPlan] = useState<PlanDto | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [amdStatus, setAMDStatus] = useState<'checking' | 'UNAVAILABLE'>('checking');
  useEffect(() => { session().then(setAccount).catch(() => setError('Cannot connect to the demo API. Refresh to retry.')); }, []);
  useEffect(() => {
    if (!account?.user) { setAMDStatus('checking'); return; }
    let active = true;
    amdHealth().then(() => { if (active) setAMDStatus('UNAVAILABLE'); })
      .catch(() => { if (active) setAMDStatus('UNAVAILABLE'); });
    return () => { active = false; };
  }, [account]);
  async function generate() {
    setBusy(true); setError(''); setPlan(null);
    try { const incident = scenario ?? await createScenario(); setScenario(incident); setPlan(await requestPlan(incident.id)); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not generate a plan.'); }
    finally { setBusy(false); }
  }
  return <><div className="demo-banner">Synthetic operations demo — no real medical actions</div><main>
    <header><div><p className="eyebrow">HealthMesh / Supply coordination</p><h1>Evidence before action.</h1><p>Compare conditional supply options for three fictional facilities.</p></div>
      {account?.user && <div><p>{account.user.username} · {account.user.role}</p><button className="secondary" disabled={busy} onClick={async () => {
        setBusy(true); setError('');
        try { setAccount(await session('DELETE')); setScenario(null); setPlan(null); }
        catch (err) { setError(err instanceof Error ? err.message : 'Sign-out failed.'); }
        finally { setBusy(false); }
      }}>Sign out</button></div>}
    </header>
    {error && <p role="alert" className="error">{error}</p>}
    {account?.user && <><p role="status">AMD inference: {amdStatus === 'checking' ? 'Checking availability…' : 'UNAVAILABLE'}</p>
      {amdStatus === 'UNAVAILABLE' && <p>AI extraction and grounded wording are unavailable. Synthetic fixture planning remains available.</p>}</>}
    {!account && !error && <p role="status">Connecting…</p>}
    {account && !account.user && <Login onLogin={setAccount} />}
    {account?.user && <><section className="panel scenario"><div><p className="eyebrow">Storm supply · Public synthetic fixture</p><h2>Aster needs 40 boxes</h2><p>Brook has a stale stock count. Cedar’s route is unverified.</p></div>
      <button disabled={busy || account.user.role !== 'coordinator'} onClick={generate}>{busy ? 'Computing…' : plan ? 'Recompute plan' : 'Load scenario and plan'}</button>
      {account.user.role === 'reviewer' && <p>Sign in as a coordinator to create a demo scenario.</p>}</section>
      {scenario && account.user.role === 'coordinator' && <SourceUpload scenario={scenario} />}
      {plan && <PlanPreview plan={plan} />}</>}
  </main></>;
}

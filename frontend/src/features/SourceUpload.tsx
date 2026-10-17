import { useState } from 'react';
import type { FormEvent } from 'react';
import { uploadSource } from '../api';
import type { ScenarioDto, SourceDto } from '../types';

export function SourceUpload({ scenario }: { scenario: ScenarioDto }) {
  const [facility, setFacility] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<SourceDto | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !facility) return;
    setBusy(true); setError(''); setSource(null);
    try { setSource(await uploadSource(scenario.id, facility, file)); }
    catch (err) { setError(err instanceof Error ? err.message : 'Source import failed.'); }
    finally { setBusy(false); }
  }
  return <section className="panel" aria-labelledby="upload-title">
    <h2 id="upload-title">Import synthetic supply evidence</h2>
    <p>Use fictional administrative supply information only. UTF-8 CSV or text, up to 1 MiB and 2000 lines. Text must begin with SYNTHETIC.</p>
    <details><summary>CSV format</summary><p>Use these exact headers:</p>
      <p style={{ overflowWrap: 'anywhere' }}>synthetic,product_code,batch_code,base_unit,reserve_accounting,on_hand,reserved,protected_reserve,observed_at</p>
      <p>Example row for the selected facility:</p>
      <p style={{ overflowWrap: 'anywhere' }}>true,SIM-MX-01,MX-01,box,separate,80,25,20,2026-10-17T11:00:00Z</p>
    </details>
    <form onSubmit={submit}>
      <label>Facility<select value={facility} onChange={event => setFacility(event.target.value)} required disabled={busy}>
        <option value="">Choose a facility</option>
        {scenario.facilities.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
      </select></label>
      <label>Source file<input type="file" accept=".csv,.txt" required disabled={busy} onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
      <button disabled={busy || !facility || !file}>{busy ? 'Importing…' : 'Import source'}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {source && <div role="status" style={{ overflowWrap: 'anywhere' }}>
      <p>{source.duplicate ? 'Already imported' : 'Imported'} source: {source.id}</p>
      <p>SHA-256: {source.sha256}</p>
      <p>Evidence remains unverified. Text extraction has not run. The saved plan remains at its frozen revision.</p>
    </div>}
  </section>;
}

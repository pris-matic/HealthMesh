import { useState } from 'react';
import { session } from '../api';
import type { SessionDto } from '../types';

export function Login({ onLogin }: { onLogin: (value: SessionDto) => void }) {
  const [username, setUsername] = useState('demo-coordinator');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <form className="panel login" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    try { onLogin(await session('POST', { username, password })); }
    catch (err) { setError(err instanceof Error ? err.message : 'Sign-in failed.'); }
    finally { setBusy(false); }
  }}>
    <h2>Sign in to the synthetic demo</h2>
    <label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label>
    <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
    {error && <p role="alert">{error}</p>}
    <button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
  </form>;
}

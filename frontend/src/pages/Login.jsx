import React, { useState } from 'react';
import axios from 'axios';

export default function Login({ methods, onSuccess }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handlePassword = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await axios.post('/api/auth/login', { username, password });
      onSuccess();
    } catch {
      setError('Invalid credentials.');
    } finally {
      setLoading(false);
    }
  };

  const handleOidc = () => {
    window.location.href = '/auth/oidc/start';
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg)',
    }}>
      <div style={{ width: '100%', maxWidth: 360, padding: '0 1rem' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <span style={{ fontSize: 32, marginRight: 8 }}>⦿</span>
          <span style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text)' }}>Digarr</span>
        </div>

        <div className="card" style={{ padding: '1.5rem' }}>
          {methods.includes('password') && (
            <form onSubmit={handlePassword} style={{ marginBottom: methods.includes('oidc') ? '1.25rem' : 0 }}>
              <div className="field" style={{ marginBottom: '0.75rem' }}>
                <label>Username</label>
                <input
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  autoFocus
                  autoComplete="username"
                  placeholder="Username"
                />
              </div>
              <div className="field" style={{ marginBottom: '1rem' }}>
                <label>Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  autoFocus={false}
                  autoComplete="current-password"
                  placeholder="Password"
                />
              </div>
              {error && (
                <div className="alert alert-error" style={{ marginBottom: '0.75rem', padding: '0.5rem 0.75rem', fontSize: 13 }}>
                  {error}
                </div>
              )}
              <button
                type="submit"
                className="btn btn-primary"
                disabled={loading || !password || !username}
                style={{ width: '100%' }}
              >
                {loading ? <><span className="spinner" /> Signing in...</> : 'Sign in'}
              </button>
            </form>
          )}

          {methods.includes('password') && methods.includes('oidc') && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              marginBottom: '1.25rem',
              color: 'var(--text-muted)',
              fontSize: 12,
            }}>
              <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
              <span>or</span>
              <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
            </div>
          )}

          {methods.includes('oidc') && (
            <button
              className="btn btn-ghost"
              onClick={handleOidc}
              style={{ width: '100%' }}
            >
              Sign in with SSO
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

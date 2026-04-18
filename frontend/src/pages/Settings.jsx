import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';

export default function Settings() {
  const [config, setConfig] = useState({});
  const [loading, setLoading] = useState(true);
  const [authStatus, setAuthStatus] = useState(null);
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState(null);
  const [profiles, setProfiles] = useState(null);
  const [loadingProfiles, setLoadingProfiles] = useState(false);
  const [plexSections, setPlexSections] = useState(null);
  const [loadingPlexSections, setLoadingPlexSections] = useState(false);
  const [spotifyStatus, setSpotifyStatus] = useState(null);
  const [jellyfinStatus, setJellyfinStatus] = useState(null);
  const [jellyfinTesting, setJellyfinTesting] = useState(false);
  const [jellyfinCacheStatus, setJellyfinCacheStatus] = useState(null);
  const jellyfinCachePollerRef = useRef(null);
  const [navidromeStatus, setNavidromeStatus] = useState(null);
  const [navidromeTesting, setNavidromeTesting] = useState(false);
  const [navidromeCacheStatus, setNavidromeCacheStatus] = useState(null);
  const navidromeCachePollerRef = useRef(null);
  const [deemixStatus, setDeemixStatus] = useState(null);
  const [deemixTesting, setDeemixTesting] = useState(false);
  const [slskdStatus, setSlskdStatus] = useState(null);
  const [slskdTesting, setSlskdTesting] = useState(false);
  const [spotifyDisconnecting, setSpotifyDisconnecting] = useState(false);
  const [openSections, setOpenSections] = useState(() => new Set(['general']));
  // Seed from localStorage so the button shows the right state on first paint,
  // before the API call comes back — no flash when navigating back mid-refresh.
  const [cacheStatus, setCacheStatus] = useState(() =>
    localStorage.getItem('digarr_cache_refreshing') === 'true'
      ? { refresh_state: 'running' }
      : null
  );
  const cachePollerRef = useRef(null);

  const toggleSection = (key) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const SectionTitle = ({ sectionKey, children }) => (
    <div
      className="card-title"
      style={{ cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: openSections.has(sectionKey) ? undefined : 0 }}
      onClick={() => toggleSection(sectionKey)}
    >
      <span style={{
        color: 'var(--accent)', fontSize: 22, fontWeight: 700,
        transition: 'transform 0.15s', display: 'inline-block',
        transform: openSections.has(sectionKey) ? 'rotate(90deg)' : 'rotate(0deg)',
        lineHeight: 1,
      }}>›</span>
      {children}
    </div>
  );

  useEffect(() => {
    axios.get('/api/config').then(r => {
      setConfig(r.data);
      setLoading(false);
    }).catch(() => setLoading(false));
    axios.get('/api/auth/status').then(r => setAuthStatus(r.data)).catch(() => {});
    axios.get('/api/spotify/status').then(r => setSpotifyStatus(r.data)).catch(() => {});

    // Show spotify_error toast if redirected back from OAuth callback
    const params = new URLSearchParams(window.location.search);
    const spotifyErr = params.get('spotify_error');
    if (spotifyErr) {
      setError(`Spotify connection failed: ${spotifyErr}`);
      window.history.replaceState({}, '', window.location.pathname);
    }
    axios.get('/api/library/cache/status').then(r => {
      setCacheStatus(r.data);
      if (r.data.refresh_state === 'running') startCachePoller();
    }).catch(() => {});
    axios.get('/api/jellyfin/cache/status').then(r => setJellyfinCacheStatus(r.data)).catch(() => {});
    axios.get('/api/navidrome/cache/status').then(r => setNavidromeCacheStatus(r.data)).catch(() => {});
  }, []);

  const handleSetPassword = async () => {
    setPasswordError('');
    if (!newPassword) return;
    if (!config.auth_username?.trim()) { setPasswordError('Enter a username above before setting a password.'); return; }
    if (newPassword !== confirmPassword) { setPasswordError('Passwords do not match.'); return; }
    if (newPassword.length < 8) { setPasswordError('Password must be at least 8 characters.'); return; }
    setPasswordSaving(true);
    try {
      // Save username alongside password in one shot
      await axios.post('/api/config', { auth_username: config.auth_username.trim() });
      await axios.post('/api/auth/set-password', { password: newPassword });
      setNewPassword('');
      setConfirmPassword('');
      setPasswordSaved(true);
      setTimeout(() => setPasswordSaved(false), 3000);
      const r = await axios.get('/api/auth/status');
      setAuthStatus(r.data);
    } catch (e) {
      setPasswordError(e.response?.data?.detail || 'Failed to set password.');
    }
    setPasswordSaving(false);
  };

  const handleClearPassword = async () => {
    setPasswordSaving(true);
    try {
      await axios.delete('/api/auth/password');
      const r = await axios.get('/api/auth/status');
      setAuthStatus(r.data);
    } catch {}
    setPasswordSaving(false);
  };

  const handleChange = (key, value) => {
    setConfig(prev => ({ ...prev, [key]: value }));
    setSaved(false);
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // Re-fetch current config before saving so fields managed outside Settings
      // (e.g. artist_blocklist on History page) are never overwritten by stale state.
      const current = await axios.get('/api/config');
      await axios.post('/api/config', { ...current.data, ...config });
      setSaved(true);
      setDirty(false);
      setTimeout(() => setSaved(false), 3000);
      // Refresh auth status in case username changed
      axios.get('/api/auth/status').then(r => setAuthStatus(r.data)).catch(() => {});
    } catch (e) {
      setError('Failed to save settings.');
    } finally {
      setSaving(false);
    }
  };

  const startCachePoller = () => {
    if (cachePollerRef.current) clearInterval(cachePollerRef.current);
    cachePollerRef.current = setInterval(async () => {
      try {
        const res = await axios.get('/api/library/cache/status');
        setCacheStatus(res.data);
        if (res.data.refresh_state !== 'running') {
          localStorage.removeItem('digarr_cache_refreshing');
          clearInterval(cachePollerRef.current);
          cachePollerRef.current = null;
        }
      } catch {}
    }, 1500);
  };

  const handleRefreshCache = async () => {
    try {
      await axios.post('/api/library/cache/refresh');
      localStorage.setItem('digarr_cache_refreshing', 'true');
      setCacheStatus(prev => ({ ...(prev || {}), refresh_state: 'running' }));
      startCachePoller();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start library cache refresh.');
    }
  };

  useEffect(() => () => {
    if (cachePollerRef.current) clearInterval(cachePollerRef.current);
    if (jellyfinCachePollerRef.current) clearInterval(jellyfinCachePollerRef.current);
    if (navidromeCachePollerRef.current) clearInterval(navidromeCachePollerRef.current);
  }, []);

  const handleLoadProfiles = async () => {
    setLoadingProfiles(true);
    setError(null);
    try {
      const res = await axios.get('/api/lidarr/profiles');
      setProfiles(res.data);
    } catch (e) {
      setError('Could not connect to Lidarr. Check your URL and API key.');
    } finally {
      setLoadingProfiles(false);
    }
  };

  const handleLoadPlexSections = async () => {
    setLoadingPlexSections(true);
    setError(null);
    try {
      const res = await axios.get('/api/plex/sections');
      setPlexSections(res.data.sections);
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not connect to Plex. Check your URL and token, then save before loading sections.');
    } finally {
      setLoadingPlexSections(false);
    }
  };

  const handleTestJellyfin = async () => {
    setJellyfinTesting(true);
    setJellyfinStatus(null);
    try {
      const res = await axios.get('/api/jellyfin/status');
      setJellyfinStatus(res.data);
    } catch (e) {
      setJellyfinStatus({ error: e.response?.data?.detail || 'Connection failed.' });
    } finally {
      setJellyfinTesting(false);
    }
  };

  const handleTestNavidrome = async () => {
    setNavidromeTesting(true);
    setNavidromeStatus(null);
    try {
      const res = await axios.get('/api/navidrome/status');
      setNavidromeStatus(res.data);
    } catch (e) {
      setNavidromeStatus({ error: e.response?.data?.detail || 'Connection failed.' });
    } finally {
      setNavidromeTesting(false);
    }
  };

  const startJellyfinCachePoller = () => {
    if (jellyfinCachePollerRef.current) clearInterval(jellyfinCachePollerRef.current);
    jellyfinCachePollerRef.current = setInterval(async () => {
      try {
        const res = await axios.get('/api/jellyfin/cache/status');
        setJellyfinCacheStatus(res.data);
        if (res.data.refresh_state !== 'running') {
          clearInterval(jellyfinCachePollerRef.current);
          jellyfinCachePollerRef.current = null;
        }
      } catch {}
    }, 1500);
  };

  const handleRefreshJellyfinCache = async () => {
    try {
      await axios.post('/api/jellyfin/cache/refresh');
      setJellyfinCacheStatus(prev => ({ ...(prev || {}), refresh_state: 'running' }));
      startJellyfinCachePoller();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start Jellyfin cache refresh.');
    }
  };

  const startNavidromeCachePoller = () => {
    if (navidromeCachePollerRef.current) clearInterval(navidromeCachePollerRef.current);
    navidromeCachePollerRef.current = setInterval(async () => {
      try {
        const res = await axios.get('/api/navidrome/cache/status');
        setNavidromeCacheStatus(res.data);
        if (res.data.refresh_state !== 'running') {
          clearInterval(navidromeCachePollerRef.current);
          navidromeCachePollerRef.current = null;
        }
      } catch {}
    }, 1500);
  };

  const handleRefreshNavidromeCache = async () => {
    try {
      await axios.post('/api/navidrome/cache/refresh');
      setNavidromeCacheStatus(prev => ({ ...(prev || {}), refresh_state: 'running' }));
      startNavidromeCachePoller();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start Navidrome cache refresh.');
    }
  };

  const handleTestDeemix = async () => {
    setDeemixTesting(true);
    setDeemixStatus(null);
    try {
      const res = await axios.get('/api/deemix/status');
      setDeemixStatus(res.data);
    } catch (e) {
      setDeemixStatus({ error: e.response?.data?.detail || 'Connection failed.' });
    } finally {
      setDeemixTesting(false);
    }
  };

  const handleTestSlskd = async () => {
    setSlskdTesting(true);
    setSlskdStatus(null);
    try {
      const res = await axios.get('/api/slskd/status');
      setSlskdStatus(res.data);
    } catch (e) {
      setSlskdStatus({ error: e.response?.data?.detail || 'Connection failed.' });
    } finally {
      setSlskdTesting(false);
    }
  };

  if (loading) {
    return (
      <div>
        <h1 className="page-title">Settings</h1>
        <div className="empty"><span className="spinner" style={{ width: 32, height: 32 }} /></div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="page-title">Settings</h1>
      <p className="page-subtitle">Configure your connections</p>

      {/* Floating save button — fixed bottom-right, only when unsaved changes exist */}
      {(dirty || saved) && (
        <div style={{ position: 'fixed', bottom: '2rem', right: '2rem', zIndex: 200, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {saved && !dirty && <span style={{ fontSize: 12, color: 'var(--green)', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 12px' }}>✓ Saved</span>}
          {dirty && (
            <button className="btn btn-primary" onClick={handleSave} disabled={saving} style={{ boxShadow: '0 4px 16px rgba(0,0,0,0.4)' }}>
              {saving ? <><span className="spinner" /> Saving...</> : '✓ Save Settings'}
            </button>
          )}
        </div>
      )}

      {error && <div className="alert alert-error">{error}</div>}

      {/* General */}
      <div className="card">
        <SectionTitle sectionKey="general">General</SectionTitle>
        {openSections.has('general') && <>
          <div className="field">
            <label>Timezone</label>
            <select
              value={config.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'}
              onChange={e => handleChange('timezone', e.target.value)}
            >
              <option value="America/New_York">America/New_York</option>
              <option value="America/Chicago">America/Chicago</option>
              <option value="America/Denver">America/Denver</option>
              <option value="America/Phoenix">America/Phoenix</option>
              <option value="America/Los_Angeles">America/Los_Angeles</option>
              <option value="America/Anchorage">America/Anchorage</option>
              <option value="Pacific/Honolulu">Pacific/Honolulu</option>
              <option value="Europe/London">Europe/London</option>
              <option value="Europe/Paris">Europe/Paris</option>
              <option value="Europe/Berlin">Europe/Berlin</option>
              <option value="Europe/Madrid">Europe/Madrid</option>
              <option value="Europe/Rome">Europe/Rome</option>
              <option value="Europe/Amsterdam">Europe/Amsterdam</option>
              <option value="Europe/Stockholm">Europe/Stockholm</option>
              <option value="Europe/Helsinki">Europe/Helsinki</option>
              <option value="Europe/Warsaw">Europe/Warsaw</option>
              <option value="Europe/Lisbon">Europe/Lisbon</option>
              <option value="Europe/Athens">Europe/Athens</option>
              <option value="Europe/Istanbul">Europe/Istanbul</option>
              <option value="Europe/Moscow">Europe/Moscow</option>
              <option value="Asia/Dubai">Asia/Dubai</option>
              <option value="Asia/Kolkata">Asia/Kolkata</option>
              <option value="Asia/Bangkok">Asia/Bangkok</option>
              <option value="Asia/Singapore">Asia/Singapore</option>
              <option value="Asia/Shanghai">Asia/Shanghai</option>
              <option value="Asia/Tokyo">Asia/Tokyo</option>
              <option value="Asia/Seoul">Asia/Seoul</option>
              <option value="Australia/Sydney">Australia/Sydney</option>
              <option value="Australia/Melbourne">Australia/Melbourne</option>
              <option value="Australia/Perth">Australia/Perth</option>
              <option value="Pacific/Auckland">Pacific/Auckland</option>
              <option value="UTC">UTC</option>
            </select>
          </div>
          {/* Password */}
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem', marginTop: '0.5rem' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Password</div>
            {authStatus?.password_source === 'env' && (
              <div className="alert" style={{ marginBottom: '0.75rem', fontSize: 12, padding: '0.5rem 0.75rem' }}>
                Password is currently set via the <span className="text-mono">DIGARR_PASSWORD</span> environment variable.
                Set one here to use a config-stored password instead — the env var will be ignored once saved.
              </div>
            )}
            {authStatus?.password_source === 'config' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
                <span style={{ fontSize: 12, color: 'var(--green)' }}>
                  ✓ Password set{authStatus?.username_set ? ` · username set` : ''}
                </span>
                <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={handleClearPassword} disabled={passwordSaving}>
                  Clear
                </button>
              </div>
            )}
            {!authStatus?.password_source && !authStatus?.methods?.includes('oidc') && (
              <p className="text-muted" style={{ fontSize: 12, marginBottom: '0.75rem' }}>No password set — Digarr is accessible to anyone who can reach it.</p>
            )}
            <div className="field">
              <label>Username</label>
              <input type="text" value={config.auth_username || ''}
                onChange={e => handleChange('auth_username', e.target.value)}
                placeholder="e.g. admin"
                autoComplete="off" />
              <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
                Required when using password auth.
              </p>
            </div>
            <div className="grid-2">
              <div className="field">
                <label>{authStatus?.password_source === 'config' ? 'New Password' : 'Set Password'}</label>
                <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)}
                  placeholder="Password" onKeyDown={e => e.key === 'Enter' && handleSetPassword()} />
              </div>
              <div className="field">
                <label>Confirm</label>
                <input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)}
                  placeholder="Confirm password" onKeyDown={e => e.key === 'Enter' && handleSetPassword()} />
              </div>
            </div>
            {passwordError && <div className="alert alert-error" style={{ marginBottom: '0.5rem', padding: '0.5rem 0.75rem', fontSize: 12 }}>{passwordError}</div>}
            {passwordSaved && <div style={{ fontSize: 12, color: 'var(--green)', marginBottom: '0.5rem' }}>✓ Password updated</div>}
            <button className="btn btn-ghost" onClick={handleSetPassword} disabled={passwordSaving || !newPassword}>
              {passwordSaving ? <><span className="spinner" /> Saving…</> : 'Set Password'}
            </button>
          </div>

          {/* OIDC */}
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem', marginTop: '1rem' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>SSO / OIDC <span style={{ textTransform: 'none', fontWeight: 400 }}>— optional, Authentik, Keycloak, etc.</span></div>
            <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
              Password and OIDC can both be active simultaneously. Sessions last until the browser closes.
            </p>
            <div className="field">
              <label>Issuer URL</label>
              <input
                value={config.oidc_issuer || ''}
                onChange={e => handleChange('oidc_issuer', e.target.value)}
                placeholder="https://auth.example.com/application/o/digarr"
              />
              <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>Authentik: Application → OpenID Configuration Issuer.</p>
            </div>
            <div className="grid-2">
              <div className="field">
                <label>Client ID</label>
                <input
                  value={config.oidc_client_id || ''}
                  onChange={e => handleChange('oidc_client_id', e.target.value)}
                  placeholder="client-id"
                />
              </div>
              <div className="field">
                <label>Client Secret</label>
                <input
                  type="password"
                  value={config.oidc_client_secret || ''}
                  onChange={e => handleChange('oidc_client_secret', e.target.value)}
                  placeholder="client-secret"
                />
              </div>
            </div>
            <div className="field">
              <label>Redirect URI</label>
              <input
                value={config.oidc_redirect_uri || ''}
                onChange={e => handleChange('oidc_redirect_uri', e.target.value)}
                placeholder="http://digarr.example.com/auth/oidc/callback"
              />
              <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>Must match exactly what you registered in your OIDC provider: <span className="text-mono">http://your-digarr-host/auth/oidc/callback</span></p>
            </div>
            <div className="field">
              <label>Allowed Emails <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>— optional</span></label>
              <input
                value={(config.oidc_allowed_emails || []).join(', ')}
                onChange={e => handleChange('oidc_allowed_emails', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
                placeholder="you@example.com, other@example.com"
              />
              <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>Comma-separated. When set, only these email addresses can sign in via SSO. Leave blank to allow any valid account.</p>
            </div>
          </div>
        </>}
      </div>

      {/* AI */}
      <div className="card">
        <SectionTitle sectionKey="ai">AI Provider</SectionTitle>
        {openSections.has('ai') && <>
          <div className="grid-2">
            <div className="field">
              <label>Active Provider</label>
              <select value={config.active_ai_provider || 'claude'}
                onChange={e => handleChange('active_ai_provider', e.target.value)}>
                <option value="claude">Claude (Anthropic)</option>
                <option value="openai">OpenAI</option>
              </select>
            </div>
            <div className="field">
              <label>Model</label>
              {(config.active_ai_provider || 'claude') === 'claude' ? (
                <select value={config.claude_model || 'claude-sonnet-4-6'}
                  onChange={e => handleChange('claude_model', e.target.value)}>
                  <option value="claude-haiku-4-5-20251001">Haiku 4.5 — fastest, cheapest</option>
                  <option value="claude-sonnet-4-6">Sonnet 4.6 — recommended</option>
                  <option value="claude-opus-4-6">Opus 4.6 — most capable</option>
                </select>
              ) : (
                <select value={config.openai_model || 'gpt-4o-mini'}
                  onChange={e => handleChange('openai_model', e.target.value)}>
                  <option value="gpt-4o-mini">GPT-4o mini — recommended</option>
                  <option value="gpt-4o">GPT-4o — most capable</option>
                </select>
              )}
            </div>
          </div>
          <div className="field">
            {(config.active_ai_provider || 'claude') === 'claude' ? (
              <>
                <label>Anthropic API Key</label>
                <input type="password" value={config.anthropic_api_key || ''}
                  onChange={e => handleChange('anthropic_api_key', e.target.value)}
                  placeholder="sk-ant-..." />
              </>
            ) : (
              <>
                <label>OpenAI API Key</label>
                <input type="password" value={config.openai_api_key || ''}
                  onChange={e => handleChange('openai_api_key', e.target.value)}
                  placeholder="sk-..." />
              </>
            )}
          </div>
        </>}
      </div>

      {/* Lidarr */}
      <div className="card">
        <SectionTitle sectionKey="lidarr">Lidarr</SectionTitle>
        {openSections.has('lidarr') && <>
          <div className="grid-2">
            <div className="field">
              <label>Lidarr URL</label>
              <input value={config.lidarr_url || ''}
                onChange={e => handleChange('lidarr_url', e.target.value)}
                placeholder="https://lidarr.yourdomain.com" />
            </div>
            <div className="field">
              <label>API Key</label>
              <input type="password" value={config.lidarr_api_key || ''}
                onChange={e => handleChange('lidarr_api_key', e.target.value)}
                placeholder="Your Lidarr API key" />
            </div>
          </div>
          <div style={{ marginBottom: '1rem' }}>
            <button className="btn btn-ghost" onClick={handleLoadProfiles} disabled={loadingProfiles}>
              {loadingProfiles ? <><span className="spinner" /> Connecting...</> : '⟳ Load Profiles from Lidarr'}
            </button>
          </div>
          <div className="grid-3">
            <div className="field">
              <label>Quality Profile</label>
              {profiles ? (
                <select value={config.lidarr_quality_profile_id || ''}
                  onChange={e => handleChange('lidarr_quality_profile_id', e.target.value)}>
                  {profiles.quality_profiles.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              ) : (
                <input value={config.lidarr_quality_profile_id || ''}
                  onChange={e => handleChange('lidarr_quality_profile_id', e.target.value)}
                  placeholder="Profile ID (e.g. 2)" />
              )}
            </div>
            <div className="field">
              <label>Metadata Profile</label>
              {profiles ? (
                <select value={config.lidarr_metadata_profile_id || ''}
                  onChange={e => handleChange('lidarr_metadata_profile_id', e.target.value)}>
                  {profiles.metadata_profiles.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              ) : (
                <input value={config.lidarr_metadata_profile_id || ''}
                  onChange={e => handleChange('lidarr_metadata_profile_id', e.target.value)}
                  placeholder="Profile ID (e.g. 1)" />
              )}
            </div>
            <div className="field">
              <label>Root Folder</label>
              {profiles ? (
                <select value={config.lidarr_root_folder || ''}
                  onChange={e => handleChange('lidarr_root_folder', e.target.value)}>
                  {profiles.root_folders.map(f => (
                    <option key={f.id} value={f.path}>{f.path}</option>
                  ))}
                </select>
              ) : (
                <input value={config.lidarr_root_folder || ''}
                  onChange={e => handleChange('lidarr_root_folder', e.target.value)}
                  placeholder="/music" />
              )}
            </div>
          </div>
        </>}
      </div>

      {/* Spotify */}
      <div className="card">
        <SectionTitle sectionKey="spotify">Spotify <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>optional — public playlists + OAuth for editorial/personal playlists</span></SectionTitle>
        {openSections.has('spotify') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Create a free app at <strong>developer.spotify.com</strong> → Dashboard → Create App.
            Set the redirect URI in your Spotify app to match the field below.
            Connect your account to unlock Discover Weekly, Daily Mixes, Liked Songs, and Plex↔Spotify sync.
          </p>

          {/* OAuth connection status */}
          <div style={{ marginBottom: '1rem', padding: '0.6rem 0.75rem', background: 'var(--surface-raised)', borderRadius: 4, display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            {spotifyStatus?.connected ? (
              <>
                <span style={{ color: 'var(--green)', fontSize: 13 }}>Connected as <strong>{spotifyStatus.display_name}</strong></span>
                <button
                  className="btn btn-sm"
                  style={{ marginLeft: 'auto' }}
                  disabled={spotifyDisconnecting}
                  onClick={async () => {
                    setSpotifyDisconnecting(true);
                    try {
                      await axios.delete('/api/spotify/disconnect');
                      setSpotifyStatus({ connected: false });
                    } finally {
                      setSpotifyDisconnecting(false);
                    }
                  }}
                >
                  {spotifyDisconnecting ? 'Disconnecting…' : 'Disconnect'}
                </button>
              </>
            ) : (
              <>
                <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Not connected</span>
                <button
                  className="btn btn-sm btn-primary"
                  style={{ marginLeft: 'auto' }}
                  onClick={() => { window.location.href = '/auth/spotify/start'; }}
                >
                  Connect with Spotify
                </button>
              </>
            )}
          </div>

          <div className="grid-2">
            <div className="field">
              <label>Client ID</label>
              <input type="password" value={config.spotify_client_id || ''}
                onChange={e => handleChange('spotify_client_id', e.target.value)}
                placeholder="Spotify client ID" />
            </div>
            <div className="field">
              <label>Client Secret</label>
              <input type="password" value={config.spotify_client_secret || ''}
                onChange={e => handleChange('spotify_client_secret', e.target.value)}
                placeholder="Spotify client secret" />
            </div>
          </div>
          <div className="field" style={{ marginTop: '0.75rem' }}>
            <label>OAuth Redirect URI</label>
            <input type="text" value={config.spotify_redirect_uri || ''}
              onChange={e => handleChange('spotify_redirect_uri', e.target.value)}
              placeholder="https://digarr.yourdomain.com/auth/spotify/callback" />
            <p className="text-muted" style={{ fontSize: 11, marginTop: '0.25rem' }}>Must match exactly what you registered in your Spotify app dashboard.</p>
          </div>
        </>}
      </div>

      {/* ListenBrainz */}
      <div className="card">
        <SectionTitle sectionKey="listenbrainz">ListenBrainz <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>optional — Discover page</span></SectionTitle>
        {openSections.has('listenbrainz') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Enter your ListenBrainz username to pull Weekly Jams, Daily Jams, and Weekly Exploration on the Discover page. No API key required.
          </p>
          <div className="field">
            <label>Username</label>
            <input value={config.listenbrainz_username || ''}
              onChange={e => handleChange('listenbrainz_username', e.target.value)}
              placeholder="your ListenBrainz username" style={{ maxWidth: 300 }} />
          </div>
        </>}
      </div>

      {/* Last.fm API Key (Similar to Library) */}
      <div className="card">
        <SectionTitle sectionKey="lastfm">Last.fm API Key <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>optional — Similar to Library</span></SectionTitle>
        {openSections.has('lastfm') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Used by the Similar to Library feature on the Discover page to find artists related to your Lidarr library.
            Get a free key at <strong>last.fm/api/account/create</strong>.
          </p>
          <div className="field" style={{ maxWidth: 400 }}>
            <label>API Key</label>
            <input type="password" value={config.lastfm_api_key || ''}
              onChange={e => handleChange('lastfm_api_key', e.target.value)}
              placeholder="Last.fm API key" />
          </div>
        </>}
      </div>

      {/* Discogs */}
      <div className="card">
        <SectionTitle sectionKey="discogs">Discogs <span className="text-muted" style={{ fontWeight: 400, fontSize: 12 }}>optional — Discover page</span></SectionTitle>
        {openSections.has('discogs') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Import your Discogs wantlist on the Discover page. Generate a personal access token at <strong>discogs.com/settings/developers</strong>.
          </p>
          <div className="grid-2">
            <div className="field">
              <label>Username</label>
              <input value={config.discogs_username || ''}
                onChange={e => handleChange('discogs_username', e.target.value)}
                placeholder="your Discogs username" />
            </div>
            <div className="field">
              <label>Personal Access Token</label>
              <input type="password" value={config.discogs_token || ''}
                onChange={e => handleChange('discogs_token', e.target.value)}
                placeholder="Discogs personal access token" />
            </div>
          </div>
        </>}
      </div>

      {/* Plex */}
      <div className="card">
        <SectionTitle sectionKey="plex">Plex</SectionTitle>
        {openSections.has('plex') && <>
          <div className="grid-2">
            <div className="field">
              <label>Plex URL</label>
              <input value={config.plex_url || ''}
                onChange={e => handleChange('plex_url', e.target.value)}
                placeholder="http://192.168.68.69:32400" />
            </div>
            <div className="field">
              <label>Plex Token</label>
              <input type="password" value={config.plex_token || ''}
                onChange={e => handleChange('plex_token', e.target.value)}
                placeholder="Your Plex token" />
            </div>
          </div>
          <div style={{ marginBottom: '1rem' }}>
            <button className="btn btn-ghost" onClick={handleLoadPlexSections} disabled={loadingPlexSections}>
              {loadingPlexSections ? <><span className="spinner" /> Connecting...</> : '⟳ Load Sections from Plex'}
            </button>
          </div>
          <div className="field">
            <label>Music Library Section ID</label>
            {plexSections ? (
              <select value={config.plex_library_section_id || ''}
                onChange={e => handleChange('plex_library_section_id', e.target.value)}>
                <option value="">— select a library —</option>
                {plexSections.map(s => (
                  <option key={s.id} value={s.id}>{s.title} ({s.type}, id={s.id})</option>
                ))}
              </select>
            ) : (
              <input value={config.plex_library_section_id || ''}
                onChange={e => handleChange('plex_library_section_id', e.target.value)}
                placeholder="Click 'Load Sections from Plex' to pick, or enter ID manually" />
            )}
          </div>
          <div className="field">
            <label>Plex Sync Interval</label>
            <select value={config.plex_sync_interval_hours || 0}
              onChange={e => handleChange('plex_sync_interval_hours', parseInt(e.target.value))}>
              <option value={0}>Off</option>
              <option value={1}>Every hour</option>
              <option value={2}>Every 2 hours</option>
              <option value={3}>Every 3 hours</option>
              <option value={4}>Every 4 hours</option>
              <option value={6}>Every 6 hours</option>
              <option value={8}>Every 8 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option>
              <option value={48}>Every 2 days</option>
              <option value={72}>Every 3 days</option>
              <option value={168}>Weekly</option>
            </select>
            <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
              Re-syncs all Plex playlists on a schedule — fills them in as Lidarr downloads complete. Useful for text and file imports that have no refresh URL.
            </p>
          </div>
          {[
            { key: 'plex_append_digarr', defaultVal: true,  label: <>Append <span className="text-mono" style={{ fontSize: 12 }}> — Digarr</span> to playlist names in Plex</> },
            { key: 'plex_delete_on_remove', defaultVal: false, label: 'Delete playlist from Plex when deleted from Digarr' },
          ].map(({ key, defaultVal, label }) => {
            const on = config[key] !== undefined ? config[key] : defaultVal;
            return (
              <div key={key} className="field" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', cursor: 'pointer' }}
                onClick={() => handleChange(key, !on)}>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700,
                  color: on ? 'var(--green)' : 'var(--text-muted)',
                }}>
                  {on ? '✓' : '○'}
                </span>
                <span style={{ fontSize: 13 }}>{label}</span>
              </div>
            );
          })}

          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem', marginTop: '0.5rem' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>
              Library Cache
            </div>
            <p className="text-muted" style={{ fontSize: 12, marginBottom: '0.75rem' }}>
              Caches all tracks from your Plex library for fast manual track matching on the History page.
              Run a refresh after adding new music.
            </p>
            {cacheStatus && (() => {
              const isError = cacheStatus.refresh_state === 'error';
              return (
                <div style={{ fontSize: 12, marginBottom: '0.75rem', fontFamily: 'var(--font-mono)' }}>
                  {isError ? (
                    <span style={{ color: 'var(--red)' }}>✕ Refresh failed: {cacheStatus.refresh_error}</span>
                  ) : cacheStatus.track_count > 0 ? (
                    <span style={{ color: 'var(--text-dim)' }}>
                      <span style={{ color: 'var(--green)' }}>✓</span>{' '}
                      {cacheStatus.track_count.toLocaleString()} tracks cached
                      {cacheStatus.cached_at && (
                        <span className="text-muted" style={{ marginLeft: 8 }}>
                          · {new Date(cacheStatus.cached_at).toLocaleString()}
                        </span>
                      )}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>Cache is empty — run a refresh.</span>
                  )}
                </div>
              );
            })()}
            <button
              className="btn btn-ghost"
              onClick={handleRefreshCache}
              disabled={cacheStatus?.refresh_state === 'running'}
            >
              ⟳ Refresh Library Cache
            </button>
          </div>
        </>}
      </div>

      {/* Jellyfin */}
      <div className="card">
        <SectionTitle sectionKey="jellyfin">Jellyfin</SectionTitle>
        {openSections.has('jellyfin') && <>
          <div className="grid-2">
            <div className="field">
              <label>Jellyfin URL</label>
              <input value={config.jellyfin_url || ''}
                onChange={e => handleChange('jellyfin_url', e.target.value)}
                placeholder="http://192.168.68.69:8096" />
            </div>
            <div className="field">
              <label>API Key</label>
              <input type="password" value={config.jellyfin_api_key || ''}
                onChange={e => handleChange('jellyfin_api_key', e.target.value)}
                placeholder="Your Jellyfin API key" />
            </div>
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <button className="btn btn-ghost" onClick={handleTestJellyfin} disabled={jellyfinTesting}>
              {jellyfinTesting ? <><span className="spinner" /> Connecting…</> : '⟳ Test Connection'}
            </button>
          </div>
          {jellyfinStatus && (
            <div style={{ fontSize: 12, marginBottom: '0.75rem', fontFamily: 'var(--font-mono)' }}>
              {jellyfinStatus.error ? (
                <span style={{ color: 'var(--red)' }}>✕ {jellyfinStatus.error}</span>
              ) : !jellyfinStatus.configured ? (
                <span style={{ color: 'var(--text-muted)' }}>Enter a URL and API key above, then save before testing.</span>
              ) : (
                <span style={{ color: 'var(--green)' }}>✓ Connected — {jellyfinStatus.server_name} v{jellyfinStatus.version}</span>
              )}
            </div>
          )}
          <div className="field">
            <label>Jellyfin Sync Interval</label>
            <select value={config.jellyfin_sync_interval_hours || 0}
              onChange={e => handleChange('jellyfin_sync_interval_hours', parseInt(e.target.value))}>
              <option value={0}>Off</option>
              <option value={1}>Every hour</option>
              <option value={2}>Every 2 hours</option>
              <option value={3}>Every 3 hours</option>
              <option value={4}>Every 4 hours</option>
              <option value={6}>Every 6 hours</option>
              <option value={8}>Every 8 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option>
              <option value={48}>Every 2 days</option>
              <option value={72}>Every 3 days</option>
              <option value={168}>Weekly</option>
            </select>
          </div>
          {[
            { key: 'jellyfin_append_digarr', defaultVal: false, label: <>Append <span className="text-mono" style={{ fontSize: 12 }}> — Digarr</span> to playlist names in Jellyfin</> },
            { key: 'jellyfin_delete_on_remove', defaultVal: false, label: 'Delete playlist from Jellyfin when deleted from Digarr' },
          ].map(({ key, defaultVal, label }) => {
            const on = config[key] !== undefined ? config[key] : defaultVal;
            return (
              <div key={key} className="field" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', cursor: 'pointer' }}
                onClick={() => handleChange(key, !on)}>
                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700, color: on ? 'var(--green)' : 'var(--text-muted)' }}>
                  {on ? '✓' : '○'}
                </span>
                <span style={{ fontSize: 13 }}>{label}</span>
              </div>
            );
          })}
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem', marginTop: '0.5rem' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Library Cache</div>
            <p className="text-muted" style={{ fontSize: 12, marginBottom: '0.75rem' }}>
              Caches all tracks from your Jellyfin library for faster playlist matching. Run a refresh after adding new music.
            </p>
            {jellyfinCacheStatus && (() => {
              const isError = jellyfinCacheStatus.refresh_state === 'error';
              return (
                <div style={{ fontSize: 12, marginBottom: '0.75rem', fontFamily: 'var(--font-mono)' }}>
                  {isError ? (
                    <span style={{ color: 'var(--red)' }}>✕ Refresh failed: {jellyfinCacheStatus.refresh_error}</span>
                  ) : jellyfinCacheStatus.track_count > 0 ? (
                    <span style={{ color: 'var(--text-dim)' }}>
                      <span style={{ color: 'var(--green)' }}>✓</span>{' '}
                      {jellyfinCacheStatus.track_count.toLocaleString()} tracks cached
                      {jellyfinCacheStatus.cached_at && <span className="text-muted" style={{ marginLeft: 8 }}>· {new Date(jellyfinCacheStatus.cached_at).toLocaleString()}</span>}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>Cache is empty — run a refresh.</span>
                  )}
                </div>
              );
            })()}
            <button className="btn btn-ghost" onClick={handleRefreshJellyfinCache}
              disabled={jellyfinCacheStatus?.refresh_state === 'running'}>
              ⟳ Refresh Library Cache
            </button>
          </div>
        </>}
      </div>

      {/* Navidrome */}
      <div className="card">
        <SectionTitle sectionKey="navidrome">Navidrome</SectionTitle>
        {openSections.has('navidrome') && <>
          <div className="grid-2">
            <div className="field">
              <label>Navidrome URL</label>
              <input value={config.navidrome_url || ''}
                onChange={e => handleChange('navidrome_url', e.target.value)}
                placeholder="http://192.168.68.69:4533" />
            </div>
            <div className="field">
              <label>Username</label>
              <input value={config.navidrome_username || ''}
                onChange={e => handleChange('navidrome_username', e.target.value)}
                placeholder="Your Navidrome username" />
            </div>
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={config.navidrome_password || ''}
              onChange={e => handleChange('navidrome_password', e.target.value)}
              placeholder="Your Navidrome password" />
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <button className="btn btn-ghost" onClick={handleTestNavidrome} disabled={navidromeTesting}>
              {navidromeTesting ? <><span className="spinner" /> Connecting…</> : '⟳ Test Connection'}
            </button>
          </div>
          {navidromeStatus && (
            <div style={{ fontSize: 12, marginBottom: '0.75rem', fontFamily: 'var(--font-mono)' }}>
              {navidromeStatus.error ? (
                <span style={{ color: 'var(--red)' }}>✕ {navidromeStatus.error}</span>
              ) : !navidromeStatus.configured ? (
                <span style={{ color: 'var(--text-muted)' }}>Enter a URL and credentials above, then save before testing.</span>
              ) : (
                <span style={{ color: 'var(--green)' }}>✓ Connected — Subsonic API v{navidromeStatus.version}</span>
              )}
            </div>
          )}
          <div className="field">
            <label>Navidrome Sync Interval</label>
            <select value={config.navidrome_sync_interval_hours || 0}
              onChange={e => handleChange('navidrome_sync_interval_hours', parseInt(e.target.value))}>
              <option value={0}>Off</option>
              <option value={1}>Every hour</option>
              <option value={2}>Every 2 hours</option>
              <option value={3}>Every 3 hours</option>
              <option value={4}>Every 4 hours</option>
              <option value={6}>Every 6 hours</option>
              <option value={8}>Every 8 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option>
              <option value={48}>Every 2 days</option>
              <option value={72}>Every 3 days</option>
              <option value={168}>Weekly</option>
            </select>
          </div>
          {[
            { key: 'navidrome_append_digarr', defaultVal: false, label: <>Append <span className="text-mono" style={{ fontSize: 12 }}> — Digarr</span> to playlist names in Navidrome</> },
            { key: 'navidrome_delete_on_remove', defaultVal: false, label: 'Delete playlist from Navidrome when deleted from Digarr' },
          ].map(({ key, defaultVal, label }) => {
            const on = config[key] !== undefined ? config[key] : defaultVal;
            return (
              <div key={key} className="field" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', cursor: 'pointer' }}
                onClick={() => handleChange(key, !on)}>
                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700, color: on ? 'var(--green)' : 'var(--text-muted)' }}>
                  {on ? '✓' : '○'}
                </span>
                <span style={{ fontSize: 13 }}>{label}</span>
              </div>
            );
          })}
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem', marginTop: '0.5rem' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Library Cache</div>
            <p className="text-muted" style={{ fontSize: 12, marginBottom: '0.75rem' }}>
              Caches all tracks from your Navidrome library for faster playlist matching. Run a refresh after adding new music.
            </p>
            {navidromeCacheStatus && (() => {
              const isError = navidromeCacheStatus.refresh_state === 'error';
              return (
                <div style={{ fontSize: 12, marginBottom: '0.75rem', fontFamily: 'var(--font-mono)' }}>
                  {isError ? (
                    <span style={{ color: 'var(--red)' }}>✕ Refresh failed: {navidromeCacheStatus.refresh_error}</span>
                  ) : navidromeCacheStatus.track_count > 0 ? (
                    <span style={{ color: 'var(--text-dim)' }}>
                      <span style={{ color: 'var(--green)' }}>✓</span>{' '}
                      {navidromeCacheStatus.track_count.toLocaleString()} tracks cached
                      {navidromeCacheStatus.cached_at && <span className="text-muted" style={{ marginLeft: 8 }}>· {new Date(navidromeCacheStatus.cached_at).toLocaleString()}</span>}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>Cache is empty — run a refresh.</span>
                  )}
                </div>
              );
            })()}
            <button className="btn btn-ghost" onClick={handleRefreshNavidromeCache}
              disabled={navidromeCacheStatus?.refresh_state === 'running'}>
              ⟳ Refresh Library Cache
            </button>
          </div>
        </>}
      </div>

      {/* Deemix */}
      <div className="card">
        <SectionTitle sectionKey="deemix">Deemix</SectionTitle>
        {openSections.has('deemix') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Connect a self-hosted <a href="https://deemix.app" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)' }}>Deemix</a> instance
            to automatically queue playlist tracks to Deezer/downloads during import.
          </p>
          <div className="field">
            <label>Deemix URL</label>
            <input value={config.deemix_url || ''} onChange={e => handleChange('deemix_url', e.target.value)}
              placeholder="http://192.168.1.x:6595" />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.25rem' }}>
            <button className="btn btn-ghost" onClick={handleTestDeemix} disabled={deemixTesting}>
              {deemixTesting ? <><span className="spinner" /> Testing...</> : 'Test Connection'}
            </button>
            {deemixStatus && (() => {
              if (!deemixStatus.configured) return <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Enter URL and save before testing.</span>;
              if (deemixStatus.error) return <span style={{ fontSize: 12, color: 'var(--red)' }}>✗ {deemixStatus.error}</span>;
              return <span style={{ fontSize: 12, color: 'var(--green)' }}>✓ Connected{deemixStatus.version ? ` — v${deemixStatus.version}` : ''}</span>;
            })()}
          </div>
        </>}
      </div>

      {/* slskd / Soulseek */}
      <div className="card">
        <SectionTitle sectionKey="slskd">Soulseek (slskd)</SectionTitle>
        {openSections.has('slskd') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Connect a self-hosted <a href="https://github.com/slskd/slskd" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)' }}>slskd</a> instance
            to automatically search and download tracks via the Soulseek P2P network. Candidates are scored against
            MusicBrainz metadata; high-confidence matches are auto-queued and low-confidence tracks are flagged for
            manual review in the History detail panel.
          </p>
          <div className="field">
            <label>slskd URL</label>
            <input value={config.slskd_url || ''} onChange={e => handleChange('slskd_url', e.target.value)}
              placeholder="http://192.168.1.x:5030" />
          </div>
          <div className="field">
            <label>slskd API Key</label>
            <input type="password" value={config.slskd_api_key || ''} onChange={e => handleChange('slskd_api_key', e.target.value)}
              placeholder="your slskd API key" />
            <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
              Found in slskd → Settings → API Keys.
            </p>
          </div>
          <div className="field">
            <label>Confidence Threshold — {config.slskd_confidence_threshold ?? 85}%</label>
            <input type="range" min={50} max={99} step={1}
              value={config.slskd_confidence_threshold ?? 85}
              onChange={e => handleChange('slskd_confidence_threshold', parseInt(e.target.value))}
              style={{ width: '100%' }} />
            <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
              Matches scoring at or above this threshold are auto-queued. Below-threshold tracks are flagged for review.
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.25rem' }}>
            <button className="btn btn-ghost" onClick={handleTestSlskd} disabled={slskdTesting}>
              {slskdTesting ? <><span className="spinner" /> Testing...</> : 'Test Connection'}
            </button>
            {slskdStatus && (() => {
              if (!slskdStatus.configured) return <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Enter URL and API key, then save before testing.</span>;
              if (slskdStatus.error) return <span style={{ fontSize: 12, color: 'var(--red)' }}>✗ {slskdStatus.error}</span>;
              return <span style={{ fontSize: 12, color: 'var(--green)' }}>✓ Connected{slskdStatus.version ? ` — v${slskdStatus.version}` : ''}</span>;
            })()}
          </div>
        </>}
      </div>

      {/* Local Export */}
      <div className="card">
        <SectionTitle sectionKey="export">Local Playlist Export</SectionTitle>
        {openSections.has('export') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            When set, Digarr writes an M3U file to this path whenever a playlist is imported or refreshed —
            useful for music servers that watch a playlist folder (Plex, Jellyfin, Navidrome, etc.).
            This is a path <strong>inside the container</strong>. Use <span className="text-mono">/data/playlists</span> to
            stay within the existing volume, or add a second volume mount to your <span className="text-mono">docker run</span> command
            pointing at your music server's playlist directory (e.g. <span className="text-mono">-v /path/to/playlists:/playlists</span>)
            and set this to <span className="text-mono">/playlists</span>.
          </p>
          <div className="field">
            <label>Export Path</label>
            <input value={config.playlist_export_path || ''}
              onChange={e => handleChange('playlist_export_path', e.target.value)}
              placeholder="/data/playlists" />
          </div>
        </>}
      </div>

      {/* Scheduled Refresh */}
      <div className="card">
        <SectionTitle sectionKey="refresh">Scheduled Refresh</SectionTitle>
        {openSections.has('refresh') && <>
          <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
            Automatically re-fetch and update all playlists that have a source URL. New artists are added to Lidarr.
            Use the History page to run a sweep manually and see results.
          </p>
          <div className="field">
            <label>Refresh Interval</label>
            <select value={config.refresh_interval_hours || 0}
              onChange={e => handleChange('refresh_interval_hours', parseInt(e.target.value))}>
              <option value={0}>Off</option>
              <option value={1}>Every hour</option>
              <option value={2}>Every 2 hours</option>
              <option value={3}>Every 3 hours</option>
              <option value={4}>Every 4 hours</option>
              <option value={6}>Every 6 hours</option>
              <option value={8}>Every 8 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option>
              <option value={48}>Every 2 days</option>
              <option value={72}>Every 3 days</option>
              <option value={168}>Weekly</option>
              <option value={336}>Every 2 weeks</option>
            </select>
          </div>
          <div className="field">
            <label>Webhook URL <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>— optional</span></label>
            <input value={config.webhook_url || ''}
              onChange={e => handleChange('webhook_url', e.target.value)}
              placeholder="https://hooks.example.com/..." />
            <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
              A POST request with a JSON summary is sent here after each scheduled refresh completes.
            </p>
          </div>

          {config.webhook_url && (() => {
            const on = config.refresh_webhook_on_changes_only || false;
            return (
              <div className="field" style={{ display: 'flex', alignItems: 'flex-start', gap: '0.6rem', cursor: 'pointer' }}
                onClick={() => handleChange('refresh_webhook_on_changes_only', !on)}>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700, marginTop: 2,
                  color: on ? 'var(--green)' : 'var(--text-muted)',
                }}>
                  {on ? '✓' : '○'}
                </span>
                <div>
                  <span style={{ fontSize: 13 }}>Only fire webhook when new content is found</span>
                  <p className="text-muted" style={{ fontSize: 11, marginTop: '0.2rem' }}>
                    Suppresses the webhook when a scheduled refresh completes with no new artists added.
                  </p>
                </div>
              </div>
            );
          })()}

          <div className="field" style={{ display: 'flex', gap: '1rem' }}>
            <div style={{ flex: 1 }}>
              <label>Delay Between Playlists <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>— seconds</span></label>
              <input type="number" min={0} max={300}
                value={config.refresh_delay_between_playlists || 0}
                onChange={e => handleChange('refresh_delay_between_playlists', Math.max(0, parseInt(e.target.value) || 0))}
                style={{ width: '100%' }} />
              <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
                Wait this many seconds between each playlist. Helps avoid rate limits on external sources.
              </p>
            </div>
            <div style={{ flex: 1 }}>
              <label>Max New Artists Per Run <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>— 0 = unlimited</span></label>
              <input type="number" min={0}
                value={config.refresh_max_new_artists || 0}
                onChange={e => handleChange('refresh_max_new_artists', Math.max(0, parseInt(e.target.value) || 0))}
                style={{ width: '100%' }} />
              <p className="text-muted" style={{ marginTop: '0.35rem', fontSize: 11 }}>
                Stop adding new artists to Lidarr once this many have been added in a single run.
              </p>
            </div>
          </div>

          {(() => {
            const on = config.refresh_merge_tracks !== undefined ? config.refresh_merge_tracks : false;
            return (
              <div className="field" style={{ display: 'flex', alignItems: 'flex-start', gap: '0.6rem', cursor: 'pointer' }}
                onClick={() => handleChange('refresh_merge_tracks', !on)}>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700, marginTop: 2,
                  color: on ? 'var(--green)' : 'var(--text-muted)',
                }}>
                  {on ? '✓' : '○'}
                </span>
                <div>
                  <span style={{ fontSize: 13 }}>Append tracks on refresh</span>
                  <p className="text-muted" style={{ fontSize: 11, marginTop: '0.2rem' }}>
                    When enabled, refreshes append new tracks to the existing playlist instead of replacing them. Useful for curated lists you've manually edited.
                  </p>
                </div>
              </div>
            );
          })()}

        </>}
      </div>

    </div>
  );
}


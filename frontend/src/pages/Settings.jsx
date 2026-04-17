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
  const [spotifyDisconnecting, setSpotifyDisconnecting] = useState(false);
  const [refreshablePlaylists, setRefreshablePlaylists] = useState([]);
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
    axios.get('/api/playlists').then(r => {
      const all = r.data.playlists || [];
      setRefreshablePlaylists(all.filter(p => p.source_url && (p.source_type === 'url' || p.source_type === 'm3u_url')));
    }).catch(() => {});
    axios.get('/api/library/cache/status').then(r => {
      setCacheStatus(r.data);
      if (r.data.refresh_state === 'running') startCachePoller();
    }).catch(() => {});
  }, []);

  const toggleExcluded = (id) => {
    setConfig(prev => {
      const excluded = new Set(prev.refresh_excluded_playlist_ids || []);
      if (excluded.has(id)) excluded.delete(id);
      else excluded.add(id);
      return { ...prev, refresh_excluded_playlist_ids: [...excluded] };
    });
    setSaved(false);
    setDirty(true);
  };

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

  // Stop poller interval on unmount (the background task keeps running on the server).
  useEffect(() => () => { if (cachePollerRef.current) clearInterval(cachePollerRef.current); }, []);

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
            <input
              list="tz-list"
              value={config.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'}
              onChange={e => handleChange('timezone', e.target.value)}
              placeholder={Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'}
            />
            <datalist id="tz-list">
              <option value="America/New_York" />
              <option value="America/Chicago" />
              <option value="America/Denver" />
              <option value="America/Phoenix" />
              <option value="America/Los_Angeles" />
              <option value="America/Anchorage" />
              <option value="Pacific/Honolulu" />
              <option value="Europe/London" />
              <option value="Europe/Paris" />
              <option value="Europe/Berlin" />
              <option value="Europe/Madrid" />
              <option value="Europe/Rome" />
              <option value="Europe/Amsterdam" />
              <option value="Europe/Stockholm" />
              <option value="Europe/Helsinki" />
              <option value="Europe/Warsaw" />
              <option value="Europe/Lisbon" />
              <option value="Europe/Athens" />
              <option value="Europe/Istanbul" />
              <option value="Europe/Moscow" />
              <option value="Asia/Dubai" />
              <option value="Asia/Kolkata" />
              <option value="Asia/Bangkok" />
              <option value="Asia/Singapore" />
              <option value="Asia/Shanghai" />
              <option value="Asia/Tokyo" />
              <option value="Asia/Seoul" />
              <option value="Australia/Sydney" />
              <option value="Australia/Melbourne" />
              <option value="Australia/Perth" />
              <option value="Pacific/Auckland" />
              <option value="UTC" />
            </datalist>
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
            {!authStatus?.password_source && (
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
              <option value={6}>Every 6 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option>
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
              <option value={6}>Every 6 hours</option>
              <option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option>
              <option value={48}>Every 2 days</option>
              <option value={168}>Weekly</option>
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

          {refreshablePlaylists.length > 0 && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.5rem' }}>
                <span className="text-muted" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Playlists</span>
                <span className="text-muted" style={{ fontSize: 11 }}>
                  <span style={{ color: 'var(--green)' }}>✓</span> will refresh &nbsp;·&nbsp; <span style={{ opacity: 0.5 }}>✓</span> skipped
                </span>
              </div>
              <div style={{ display: 'grid', gap: '0.3rem' }}>
                {refreshablePlaylists.map(pl => {
                  const excluded = (config.refresh_excluded_playlist_ids || []).includes(pl.id);
                  return (
                    <div key={pl.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}
                      onClick={() => toggleExcluded(pl.id)}>
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                        width: 18, height: 18, flexShrink: 0,
                        fontSize: 13, lineHeight: 1, fontWeight: 700,
                        color: excluded ? 'var(--text-muted)' : 'var(--green)',
                        opacity: excluded ? 0.4 : 1,
                      }}>✓</span>
                      <span style={{ fontSize: 13, color: excluded ? 'var(--text-muted)' : 'var(--text)', textDecoration: excluded ? 'line-through' : 'none' }}>
                        {pl.name}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>}
      </div>

    </div>
  );
}


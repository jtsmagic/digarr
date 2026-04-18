import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const LibraryBadge = ({ inLibrary }) => {
  if (inLibrary == null) return null;
  return (
    <span style={{
      fontSize: 10, padding: '2px 6px', borderRadius: 3, fontWeight: 600, letterSpacing: '0.03em',
      background: inLibrary ? 'var(--green-dim)' : 'var(--red-dim)',
      color: inLibrary ? 'var(--green)' : 'var(--red)',
    }}>
      {inLibrary ? 'in library' : 'missing'}
    </span>
  );
};

const ImportedBanner = ({ name }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '0.6rem 0.75rem', background: 'var(--green-dim)', borderRadius: 4, fontSize: 13 }}>
    <span style={{ color: 'var(--green)' }}>Importing "{name}"…</span>
    <Link to="/history" style={{ color: 'var(--accent)', marginLeft: 'auto' }}>View in History →</Link>
  </div>
);

function useImport() {
  const [importing, setImporting] = useState(false);
  const [importedJob, setImportedJob] = useState(null);
  const [error, setError] = useState(null);

  const startImport = useCallback(async ({ artists, tracks, name, sourceUrl, sourceType, syncTargets }) => {
    setImporting(true);
    setError(null);
    try {
      const res = await axios.post('/api/import/start', {
        playlist_name: name,
        artists,
        tracks,
        source_url: sourceUrl,
        source_type: sourceType,
        include_in_refresh: true,
        sync_targets: syncTargets ? [...syncTargets] : [],
      });
      setImportedJob({ job_id: res.data.job_id, playlist_id: res.data.playlist_id, name });
    } catch (e) {
      setError(e.response?.data?.detail || 'Import failed.');
    } finally {
      setImporting(false);
    }
  }, []);

  return { importing, importedJob, error, startImport, setError };
}

// ---------------------------------------------------------------------------
// ListenBrainz card
// ---------------------------------------------------------------------------

const LB_FEEDS = [
  { id: 'weekly_jams',        label: 'Weekly Jams'        },
  { id: 'daily_jams',         label: 'Daily Jams'         },
  { id: 'weekly_exploration', label: 'Weekly Exploration' },
];

function ListenBrainzCard({ configured, syncProps = {} }) {
  const [syncTargets, setSyncTargets] = useState(new Set(['plex', 'spotify']));
  const [feed, setFeed]         = useState('weekly_jams');
  const [loading, setLoading]   = useState(false);
  const [fetchError, setFetchError] = useState(null);
  const [results, setResults]   = useState(null);
  const [lidarrStatus, setLidarrStatus] = useState({});
  const [selected, setSelected] = useState(new Set());
  const [nameOverride, setNameOverride] = useState('');
  const { importing, importedJob, error: importError, startImport, setError } = useImport();

  const fetch = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    setResults(null);
    setLidarrStatus({});
    setSelected(new Set());
    setNameOverride('');
    try {
      const r = await axios.get(`/api/discover/listenbrainz/recommendations?type=${feed}`);
      setResults(r.data);
      setSelected(new Set(r.data.tracks.map((_, i) => i)));
      const artists = [...new Set(r.data.tracks.map(t => t.artist))];
      if (artists.length) {
        axios.post('/api/lidarr/check-artists', { artists })
          .then(res => setLidarrStatus(res.data.results || {}))
          .catch(() => {});
      }
    } catch (e) {
      setFetchError(e.response?.data?.detail || 'Failed to fetch.');
    } finally {
      setLoading(false);
    }
  }, [feed]);

  const toggle = i => setSelected(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });
  const toggleAll = () => setSelected(prev => prev.size === results.tracks.length ? new Set() : new Set(results.tracks.map((_, i) => i)));

  const handleImport = () => {
    if (!results || selected.size === 0) return;
    const picked = results.tracks.filter((_, i) => selected.has(i));
    const seen = new Set();
    const artists = [];
    for (const t of picked) { if (!seen.has(t.artist)) { seen.add(t.artist); artists.push({ name: t.artist }); } }
    startImport({ artists, tracks: picked, name: nameOverride.trim() || results.name, sourceUrl: `listenbrainz:${feed}`, sourceType: 'listenbrainz', syncTargets });
  };

  if (!configured) return (
    <div className="card">
      <div className="card-title">ListenBrainz</div>
      <p className="text-muted" style={{ fontSize: 13 }}>Set your ListenBrainz username in <Link to="/settings" style={{ color: 'var(--accent)' }}>Settings</Link>.</p>
    </div>
  );

  return (
    <div className="card">
      <div className="card-title">ListenBrainz</div>
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <div className="field" style={{ margin: 0, flex: '1 1 200px', minWidth: 0 }}>
          <label>Feed</label>
          <select value={feed} onChange={e => setFeed(e.target.value)}>
            {LB_FEEDS.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </div>
        <button className="btn btn-primary" onClick={fetch} disabled={loading} style={{ flexShrink: 0 }}>
          {loading ? 'Digging…' : 'Dig'}
        </button>
      </div>
      {(fetchError || importError) && <div className="error-box" style={{ marginBottom: '1rem' }}>{fetchError || importError}</div>}
      {results && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{results.tracks.length} tracks</span>
            <button className="btn btn-sm btn-ghost" onClick={toggleAll} style={{ fontSize: 11, padding: '2px 8px' }}>
              {selected.size === results.tracks.length ? 'Deselect all' : 'Select all'}
            </button>
          </div>
          <TrackTable tracks={results.tracks} selected={selected} onToggle={toggle} lidarrStatus={lidarrStatus} />
          {importedJob ? <ImportedBanner name={importedJob.name} /> : (
            <ImportBar name={nameOverride} placeholder={results.name} onNameChange={setNameOverride}
              onImport={handleImport} importing={importing} count={selected.size} unit="track"
              syncTargets={syncTargets} onSyncTargetChange={(id, checked) => setSyncTargets(prev => { const s = new Set(prev); checked ? s.add(id) : s.delete(id); return s; })}
              {...syncProps} />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Similar to Library card
// ---------------------------------------------------------------------------

function SimilarToLibraryCard({ configured, syncProps = {} }) {
  const [syncTargets, setSyncTargets] = useState(new Set(['plex', 'spotify']));
  const [loading, setLoading]   = useState(false);
  const [fetchError, setFetchError] = useState(null);
  const [results, setResults]   = useState(null);
  const [lidarrStatus, setLidarrStatus] = useState({});
  const [selected, setSelected] = useState(new Set());
  const [nameOverride, setNameOverride] = useState('');
  const { importing, importedJob, error: importError, startImport } = useImport();

  const fetch = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    setResults(null);
    setLidarrStatus({});
    setSelected(new Set());
    setNameOverride('');
    try {
      const r = await axios.get('/api/discover/similar-to-library');
      setResults(r.data);
      setSelected(new Set(r.data.artists.map((_, i) => i)));
      const names = r.data.artists.map(a => a.name);
      if (names.length) {
        axios.post('/api/lidarr/check-artists', { artists: names })
          .then(res => setLidarrStatus(res.data.results || {}))
          .catch(() => {});
      }
    } catch (e) {
      setFetchError(e.response?.data?.detail || 'Failed to fetch.');
    } finally {
      setLoading(false);
    }
  }, []);

  const toggle = i => setSelected(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });
  const toggleAll = () => setSelected(prev => prev.size === results.artists.length ? new Set() : new Set(results.artists.map((_, i) => i)));

  const handleImport = () => {
    if (!results || selected.size === 0) return;
    const artists = results.artists.filter((_, i) => selected.has(i)).map(a => ({ name: a.name }));
    startImport({ artists, tracks: [], name: nameOverride.trim() || results.name, sourceUrl: 'similar-to-library', sourceType: 'similar', syncTargets });
  };

  if (!configured) return (
    <div className="card">
      <div className="card-title">Similar to Library</div>
      <p className="text-muted" style={{ fontSize: 13 }}>
        Requires a Last.fm API key and Lidarr. Add both in <Link to="/settings" style={{ color: 'var(--accent)' }}>Settings</Link>.
      </p>
    </div>
  );

  return (
    <div className="card">
      <div className="card-title">Similar to Library</div>
      <p className="text-muted" style={{ fontSize: 12, marginBottom: '1rem' }}>
        Finds artists similar to ones already in your Lidarr library, then filters out what you already have.
        Takes a few seconds — samples up to 75 library artists.
      </p>
      <div style={{ marginBottom: '1rem' }}>
        <button className="btn btn-primary" onClick={fetch} disabled={loading}>
          {loading ? 'Digging…' : 'Find Similar Artists'}
        </button>
      </div>
      {(fetchError || importError) && <div className="error-box" style={{ marginBottom: '1rem' }}>{fetchError || importError}</div>}
      {results && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{results.artists.length} artists</span>
            <button className="btn btn-sm btn-ghost" onClick={toggleAll} style={{ fontSize: 11, padding: '2px 8px' }}>
              {selected.size === results.artists.length ? 'Deselect all' : 'Select all'}
            </button>
          </div>
          <div style={{ overflowX: 'auto', marginBottom: '1rem' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ width: 32, padding: '6px 8px' }}></th>
                  <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>Artist</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontWeight: 500 }}>Similar to</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontWeight: 500 }}>Library</th>
                </tr>
              </thead>
              <tbody>
                {results.artists.map((a, i) => (
                  <tr key={i} onClick={() => toggle(i)}
                      style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', opacity: selected.has(i) ? 1 : 0.45 }}>
                    <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                      <input type="checkbox" checked={selected.has(i)} readOnly style={{ cursor: 'pointer' }} />
                    </td>
                    <td style={{ padding: '6px 8px' }}>{a.name}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontSize: 11 }}>
                      {a.similarity_count} {a.similarity_count === 1 ? 'artist' : 'artists'}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                      <LibraryBadge inLibrary={lidarrStatus[a.name]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {importedJob ? <ImportedBanner name={importedJob.name} /> : (
            <ImportBar name={nameOverride} placeholder={results.name} onNameChange={setNameOverride}
              onImport={handleImport} importing={importing} count={selected.size} unit="artist"
              syncTargets={syncTargets} onSyncTargetChange={(id, checked) => setSyncTargets(prev => { const s = new Set(prev); checked ? s.add(id) : s.delete(id); return s; })}
              {...syncProps} />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared table / import bar sub-components
// ---------------------------------------------------------------------------

function TrackTable({ tracks, selected, onToggle, lidarrStatus, titleLabel = 'Track', artistLabel = 'Artist', showAlbum = false }) {
  return (
    <div style={{ overflowX: 'auto', marginBottom: '1rem' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            <th style={{ width: 32, padding: '6px 8px' }}></th>
            <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>{titleLabel}</th>
            <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>{artistLabel}</th>
            <th style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)', fontWeight: 500 }}>Library</th>
          </tr>
        </thead>
        <tbody>
          {tracks.map((t, i) => (
            <tr key={i} onClick={() => onToggle(i)}
                style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', opacity: selected.has(i) ? 1 : 0.45 }}>
              <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                <input type="checkbox" checked={selected.has(i)} readOnly style={{ cursor: 'pointer' }} />
              </td>
              <td style={{ padding: '6px 8px' }}>{t.title}</td>
              <td style={{ padding: '6px 8px', color: 'var(--text-dim)' }}>{t.artist}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                <LibraryBadge inLibrary={lidarrStatus[t.artist]} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ImportBar({ name, placeholder, onNameChange, onImport, importing, count, unit, syncTargets, onSyncTargetChange, plexConfigured, spotifyConfigured }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div className="field" style={{ margin: 0, flex: '1 1 200px', minWidth: 0 }}>
          <label>Playlist name</label>
          <input value={name} onChange={e => onNameChange(e.target.value)} placeholder={placeholder} />
        </div>
        <button className="btn btn-accent" onClick={onImport} disabled={importing || count === 0} style={{ flexShrink: 0 }}>
          {importing ? 'Importing…' : `Import ${count} ${unit}${count !== 1 ? 's' : ''} as Playlist`}
        </button>
      </div>
      {plexConfigured && spotifyConfigured && syncTargets && (
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>Sync to:</span>
          {[{ id: 'plex', label: 'Plex' }, { id: 'spotify', label: 'Spotify' }].map(({ id, label }) => (
            <label key={id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: 13, cursor: 'pointer' }}>
              <input type="checkbox" checked={syncTargets.has(id)}
                onChange={e => onSyncTargetChange(id, e.target.checked)} />
              {label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Discover() {
  const [config, setConfig] = useState(null);
  const [spotifyConnected, setSpotifyConnected] = useState(false);

  useEffect(() => {
    axios.get('/api/config').then(r => setConfig(r.data)).catch(() => {});
    axios.get('/api/spotify/status').then(r => setSpotifyConnected(r.data.connected)).catch(() => {});
  }, []);

  const lbConfigured      = !!(config?.listenbrainz_username);
  const similarConfigured = !!(config?.lastfm_api_key && config?.lidarr_url && config?.lidarr_api_key);
  const plexConfigured    = !!(config?.plex_url && config?.plex_token && config?.plex_library_section_id);

  const syncProps = { plexConfigured, spotifyConfigured: spotifyConnected };

  return (
    <div className="page">
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 28, letterSpacing: '0.04em', marginBottom: '0.25rem' }}>
          Discover
        </h1>
        <p className="text-muted" style={{ fontSize: 13 }}>
          Find new artists and releases to add to your library.
          Imported playlists refresh automatically and sync to Plex just like any other source.
        </p>
      </div>

      {config ? (
        <>
          <ListenBrainzCard configured={lbConfigured} syncProps={syncProps} />
          <SimilarToLibraryCard configured={similarConfigured} syncProps={syncProps} />
        </>
      ) : (
        <div className="card">
          <p className="text-muted" style={{ fontSize: 13 }}>Loading…</p>
        </div>
      )}
    </div>
  );
}

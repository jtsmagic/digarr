import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

const INPUT_TYPES = [
  { id: 'url', label: 'URL', placeholder: 'https://pitchfork.com/features/lists-and-guides/... or https://example.com/playlist.m3u' },
  { id: 'text', label: 'Text / Paste', placeholder: 'Paste a song list, tracklist, artist list, blog excerpt...' },
  { id: 'file', label: 'M3U File', placeholder: null },
  { id: 'spotify', label: 'Spotify', placeholder: null },
  { id: 'deemix', label: 'Deezer', placeholder: null },
];

export default function Import() {
  const [inputType, setInputType] = useState('url');
  const [content, setContent] = useState('');
  const [playlistName, setPlaylistName] = useState('');
  const [loading, setLoading] = useState(false);
  const [parseStatus, setParseStatus] = useState('');
  const [error, setError] = useState(null);
  const [parsed, setParsed] = useState(null);
  const [parseUsage, setParseUsage] = useState(null);
  const [queuedJob, setQueuedJob] = useState(null); // { job_id, playlist_id, name }
  const [dragOver, setDragOver] = useState(false);
  const [trackStatuses, setTrackStatuses] = useState({});
  const [statusLoading, setStatusLoading] = useState(false);
  const [lidarrExisting, setLidarrExisting] = useState({});
  const [includeInRefresh, setIncludeInRefresh] = useState(true);
  const [sourceConflict, setSourceConflict] = useState(null); // [{id, name, created_at}]
  const [bypassConflict, setBypassConflict] = useState(false);
  const [plexConfigured, setPlexConfigured] = useState(false);
  const [spotifyConfigured, setSpotifyConfigured] = useState(false);
  const [jellyfinConfigured, setJellyfinConfigured] = useState(false);
  const [navidromeConfigured, setNavidromeConfigured] = useState(false);
  const [deemixConfigured, setDeemixConfigured] = useState(false);
  const [slskdConfigured, setSlskdConfigured] = useState(false);
  const [syncTargets, setSyncTargets] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem('syncTargets')) || ['plex', 'spotify']); }
    catch { return new Set(['plex', 'spotify']); }
  });
  const [setupItems, setSetupItems] = useState([]);
  const fileRef = useRef();

  useEffect(() => {
    axios.get('/api/spotify/status').then(r => {
      setSpotifyConfigured(r.data.connected);
      if (!r.data.connected) setInputType(t => t === 'spotify' ? 'url' : t);
    }).catch(() => {});
    axios.get('/api/jellyfin/status').then(r => setJellyfinConfigured(r.data.configured)).catch(() => {});
    axios.get('/api/navidrome/status').then(r => setNavidromeConfigured(r.data.configured)).catch(() => {});
    axios.get('/api/deemix/status').then(r => setDeemixConfigured(r.data.configured)).catch(() => {});
    axios.get('/api/slskd/status').then(r => setSlskdConfigured(r.data.configured)).catch(() => {});
    axios.get('/api/config').then(r => {
      const d = r.data;
      setPlexConfigured(!!(d.plex_url && d.plex_token));
      const missing = [];
      const provider = d.active_ai_provider || 'claude';
      const hasAI = provider === 'openai' ? !!d.openai_api_key : !!d.anthropic_api_key;
      if (!hasAI) missing.push({ key: 'ai', label: 'AI provider', desc: 'Required for parsing URLs and text' });
      if (!d.lidarr_url || !d.lidarr_api_key) missing.push({ key: 'lidarr', label: 'Lidarr', desc: 'Optional — artists won\'t be added to Lidarr without it', warning: true });
      setSetupItems(missing);
    }).catch(() => {});
  }, []);

  const fetchTrackStatuses = async (tracks) => {
    if (!tracks?.length) return;
    setStatusLoading(true);
    try {
      const res = await axios.post('/api/lidarr/trackstatus', { tracks });
      const map = {};
      for (const t of res.data.tracks) {
        const key = `${t.artist}||${t.title}`;
        map[key] = t.status;
      }
      setTrackStatuses(map);
    } catch {
      // best-effort
    } finally {
      setStatusLoading(false);
    }
  };

  const checkLidarrArtists = async (artists) => {
    if (!artists?.length) return;
    try {
      const names = artists.map(a => a.name);
      const res = await axios.post('/api/lidarr/check-artists', { artists: names });
      setLidarrExisting(res.data.results || {});
    } catch {
      // best-effort
    }
  };

  const resetState = () => {
    setParsed(null);
    setTrackStatuses({});
    setLidarrExisting({});
    setIncludeInRefresh(true);
    setSourceConflict(null);
    setBypassConflict(false);
    setQueuedJob(null);
  };

  const _queueImport = async (parsedData, sourceUrl, sourceType, name) => {
    if (!parsedData?.artists?.length) return;
    setParseStatus('Queuing import…');
    try {
      const res = await axios.post('/api/import/start', {
        artists: parsedData.artists,
        tracks: parsedData.tracks || [],
        playlist_name: name || '',
        source_url: sourceUrl || null,
        source_type: parsedData.detected_source_type || sourceType,
        include_in_refresh: includeInRefresh,
        sync_targets: [...syncTargets],
      });
      // Clear the form inputs so the user can start another import immediately,
      // but keep parsed/trackStatuses visible below the queued notification.
      setContent('');
      setPlaylistName('');
      setSourceConflict(null);
      setBypassConflict(false);
      setQueuedJob({ job_id: res.data.job_id, playlist_id: res.data.playlist_id, name: name || 'Import' });
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to queue import.');
    }
  };

  const handleParse = async (forceBypass = false) => {
    if (!content && inputType !== 'file') return;

    // Duplicate source check for URLs (skip if user already chose to proceed)
    if (inputType === 'url' && !bypassConflict && !forceBypass) {
      try {
        const check = await axios.get('/api/playlists/check-source', { params: { url: content } });
        if (check.data.matches?.length > 0) {
          setSourceConflict(check.data.matches);
          return;
        }
      } catch { /* non-fatal */ }
    }

    setLoading(true);
    setParseStatus(inputType === 'url' ? 'Fetching page…' : 'Reading input…');
    setError(null);
    setParseUsage(null);
    resetState();
    try {
      await new Promise(r => setTimeout(r, 400));
      setParseStatus('Parsing with AI…');
      const res = await axios.post('/api/parse', {
        input_type: inputType,
        content,
        playlist_name: playlistName || undefined,
      });
      setParseStatus('Checking library…');
      setParsed(res.data);
      if (res.data.usage) setParseUsage(res.data.usage);
      await Promise.all([
        checkLidarrArtists(res.data.artists),
        fetchTrackStatuses(res.data.tracks),
      ]);
      if (res.data.artists?.length > 0) {
        await _queueImport(res.data, inputType === 'url' ? content : null, res.data.detected_source_type || inputType, playlistName);
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to parse input. Check your API key and try again.');
    } finally {
      setLoading(false);
      setParseStatus('');
    }
  };

  const handleFileUpload = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    resetState();
    const formData = new FormData();
    formData.append('file', file);
    const resolvedName = playlistName || file.name.replace('.m3u', '').replace('.m3u8', '');
    if (!playlistName) setPlaylistName(resolvedName);
    try {
      const res = await axios.post('/api/parse/upload', formData);
      setParsed(res.data);
      await Promise.all([
        checkLidarrArtists(res.data.artists),
        fetchTrackStatuses(res.data.tracks),
      ]);
      if (res.data.artists?.length > 0) {
        await _queueImport(res.data, null, res.data.detected_source_type || 'file', resolvedName);
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to parse file.');
    } finally {
      setLoading(false);
      setParseStatus('');
    }
  };

  const handleDownloadM3U = () => {
    if (!parsed?.tracks?.length) return;
    const lines = ['#EXTM3U', `#PLAYLIST:${playlistName || 'Digarr Export'}`];
    for (const track of parsed.tracks) {
      const artist = track.artist || 'Unknown';
      const title = track.title || 'Unknown';
      lines.push(`#EXTINF:-1,${artist} - ${title}`);
      lines.push(`# ${artist} - ${title}`);
    }
    const blob = new Blob([lines.join('\n')], { type: 'audio/x-mpegurl' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(playlistName || 'digarr').replace(/\s+/g, '_')}.m3u`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadJSPF = () => {
    if (!parsed?.tracks?.length) return;
    const tracks = parsed.tracks.map(t => ({
      creator: t.artist || '',
      title: t.title || '',
      ...(t.album && t.album !== 'null' ? { album: t.album } : {}),
    }));
    const payload = JSON.stringify({ playlist: { title: playlistName || 'Digarr Export', track: tracks } }, null, 2);
    const blob = new Blob([payload], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(playlistName || 'digarr').replace(/\s+/g, '_')}.jspf`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Build artist → tracks map
  const tracksByArtist = {};
  for (const track of (parsed?.tracks || [])) {
    const key = track.artist || '';
    if (!tracksByArtist[key]) tracksByArtist[key] = [];
    tracksByArtist[key].push(track);
  }

  const hasAlbums = (parsed?.tracks || []).some(
    t => t.album && t.album !== 'null' && t.album !== null
  );

  // Confidence indicator: dims/flags low-confidence AI results
  const confidenceStyle = (confidence) => {
    if (confidence == null) return {};
    if (confidence < 70) return { opacity: 0.55, fontStyle: 'italic' };
    if (confidence < 85) return { opacity: 0.8 };
    return {};
  };

  const confidenceBadge = (confidence) => {
    if (confidence == null || confidence >= 85) return null;
    if (confidence < 70) return { label: '?', title: `Low confidence (${confidence}%)`, color: 'var(--red)' };
    return { label: '~', title: `Uncertain (${confidence}%)`, color: '#ffb300' };
  };

  const statusDot = (status) => {
    if (status === 'green') return { color: '#4caf50', title: 'Downloaded' };
    if (status === 'yellow') return { color: '#ffb300', title: 'Monitored, not downloaded' };
    if (status === 'red') return { color: '#f44336', title: 'Not in Lidarr' };
    return null;
  };

  const totalArtists = parsed?.artists?.length || 0;
  const totalTracks = parsed?.tracks?.length || 0;
  const inLidarrCount = Object.values(lidarrExisting).filter(Boolean).length;

  return (
    <div>
      <h1 className="page-title">Import</h1>
      <p className="page-subtitle">Drop a URL, paste a list, or upload a file — AI does the rest</p>

      {setupItems.filter(i => !i.warning).length > 0 && (
        <div className="card" style={{ marginBottom: '1.5rem', borderLeft: '3px solid var(--accent)' }}>
          <div className="card-title" style={{ marginBottom: '0.5rem' }}>Finish setting up Digarr</div>
          <div style={{ display: 'grid', gap: '0.4rem', marginBottom: '0.75rem' }}>
            {setupItems.filter(i => !i.warning).map(item => (
              <div key={item.key} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: 13 }}>
                <span style={{ color: 'var(--red)', fontSize: 10 }}>●</span>
                <strong>{item.label}</strong>
                <span className="text-muted">{item.desc}</span>
              </div>
            ))}
          </div>
          <Link to="/settings" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>
            Go to Settings →
          </Link>
        </div>
      )}
      {setupItems.filter(i => i.warning).map(item => (
        <div key={item.key} className="alert" style={{ marginBottom: '1rem', fontSize: 12, borderLeft: '3px solid var(--yellow, #f5c518)', padding: '0.5rem 0.75rem' }}>
          <strong>{item.label} not configured</strong> — {item.desc}.{' '}
          <Link to="/settings" style={{ color: 'var(--accent)', textDecoration: 'none' }}>Configure in Settings →</Link>
        </div>
      ))}

      {inputType !== 'spotify' && (
        <div className="field">
          <label>Playlist Name</label>
          <input value={playlistName} onChange={e => setPlaylistName(e.target.value)}
            placeholder="My Punk Playlist (optional)" />
        </div>
      )}

      {/* Input Type Tabs */}
      <div className="tabs">
        {INPUT_TYPES.filter(t =>
          (t.id !== 'spotify' || spotifyConfigured) &&
          (t.id !== 'deemix' || deemixConfigured)
        ).map(t => (
          <button key={t.id} className={`tab ${inputType === t.id ? 'active' : ''}`}
            onClick={() => { setInputType(t.id); setContent(''); resetState(); }}>
            {t.label}
          </button>
        ))}
      </div>

      {inputType === 'file' ? (
        <div className="field">
          <div
            className={`dropzone ${dragOver ? 'active' : ''}`}
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); handleFileUpload(e.dataTransfer.files[0]); }}
            onClick={() => fileRef.current?.click()}
          >
            <span style={{ fontSize: 32, display: 'block', marginBottom: 8 }}>⬆</span>
            Drop an M3U file here or click to browse
            <input ref={fileRef} type="file" accept=".m3u,.m3u8" style={{ display: 'none' }}
              onChange={e => handleFileUpload(e.target.files[0])} />
          </div>
        </div>
      ) : inputType === 'spotify' ? (
        <SpotifyImportTab
          spotifyConfigured={spotifyConfigured}
          plexConfigured={plexConfigured}
          jellyfinConfigured={jellyfinConfigured}
          navidromeConfigured={navidromeConfigured}
          deemixConfigured={deemixConfigured}
          slskdConfigured={slskdConfigured}
          onQueued={setQueuedJob}
        />
      ) : inputType === 'deemix' ? (
        <DeemixImportTab
          plexConfigured={plexConfigured}
          jellyfinConfigured={jellyfinConfigured}
          navidromeConfigured={navidromeConfigured}
          slskdConfigured={slskdConfigured}
          onQueued={setQueuedJob}
        />
      ) : (
        <div className="field">
          <label>{INPUT_TYPES.find(t => t.id === inputType)?.label}</label>
          {inputType === 'text' ? (
            <textarea value={content} onChange={e => setContent(e.target.value)}
              placeholder={INPUT_TYPES.find(t => t.id === inputType)?.placeholder}
              style={{ minHeight: 180 }} />
          ) : (
            <input value={content} onChange={e => setContent(e.target.value)}
              placeholder={INPUT_TYPES.find(t => t.id === inputType)?.placeholder} />
          )}
        </div>
      )}

      {inputType === 'url' && (
        <div className="field" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', cursor: 'pointer', marginBottom: '0.5rem' }}
          onClick={() => setIncludeInRefresh(v => !v)}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700,
            color: includeInRefresh ? 'var(--green)' : 'var(--text-muted)',
          }}>
            {includeInRefresh ? '✓' : '○'}
          </span>
          <span style={{ fontSize: 13 }}>Add to scheduled refresh</span>
        </div>
      )}

      {/* Sync targets — only shown when 2+ are configured, not needed for Spotify/Deemix tabs (handled internally) */}
      {inputType !== 'spotify' && inputType !== 'deemix' && [plexConfigured, spotifyConfigured, jellyfinConfigured, navidromeConfigured, deemixConfigured, slskdConfigured].filter(Boolean).length >= 2 && (
        <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>Sync to:</span>
          {[
            { id: 'plex', label: 'Plex', show: plexConfigured },
            { id: 'spotify', label: 'Spotify', show: spotifyConfigured },
            { id: 'jellyfin', label: 'Jellyfin', show: jellyfinConfigured },
            { id: 'navidrome', label: 'Navidrome', show: navidromeConfigured },
            { id: 'deemix', label: 'Deemix', show: deemixConfigured },
            { id: 'slskd', label: 'Soulseek', show: slskdConfigured },
          ].filter(t => t.show).map(({ id, label }) => (
            <label key={id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: 13, cursor: 'pointer' }}>
              <input type="checkbox" checked={syncTargets.has(id)}
                onChange={e => setSyncTargets(prev => {
                  const s = new Set(prev);
                  e.target.checked ? s.add(id) : s.delete(id);
                  localStorage.setItem('syncTargets', JSON.stringify([...s]));
                  return s;
                })} />
              {label}
            </label>
          ))}
        </div>
      )}

      {inputType !== 'spotify' && inputType !== 'deemix' && sourceConflict && (
        <div className="alert alert-info mt-2" style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, marginBottom: '0.3rem' }}>Already imported</div>
            {sourceConflict.map(p => (
              <div key={p.id} style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {p.name} — <span style={{ opacity: 0.7 }}>{new Date(p.created_at).toLocaleDateString()}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
            <button className="btn btn-ghost" style={{ fontSize: 11 }}
              onClick={() => { setBypassConflict(true); setSourceConflict(null); handleParse(true); }}>
              Import anyway
            </button>
            <button className="btn btn-ghost" style={{ fontSize: 11 }}
              onClick={() => { setSourceConflict(null); setContent(''); setPlaylistName(''); }}>
              ✕
            </button>
          </div>
        </div>
      )}

      {inputType !== 'file' && inputType !== 'spotify' && inputType !== 'deemix' && (
        <button className="btn btn-primary" onClick={handleParse} disabled={loading || !content}>
          {loading ? <><span className="spinner" /> {parseStatus || 'Parsing…'}</> : "Let's Dig It!"}
        </button>
      )}

      {inputType !== 'spotify' && inputType !== 'deemix' && error && <div className="alert alert-error mt-2">{error}</div>}

      {parseUsage && !loading && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: '0.4rem', textAlign: 'right' }}>
          {parseUsage.provider} · {parseUsage.model} · {parseUsage.input_tokens?.toLocaleString()} in / {parseUsage.output_tokens?.toLocaleString()} out tokens
        </div>
      )}

      {/* Queued confirmation banner */}
      {queuedJob && (
        <div style={{ marginTop: '1rem', background: 'rgba(76,175,80,0.1)', border: '1px solid var(--green)', borderRadius: 8, padding: '0.9rem 1.1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem' }}>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--green)', marginBottom: 3 }}>✓ Import queued</div>
            <div className="text-muted" style={{ fontSize: 12 }}>"{queuedJob.name}" is running in the background — track progress in History.</div>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
            <Link to="/history" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none', whiteSpace: 'nowrap' }}>
              History →
            </Link>
            <button className="btn btn-ghost" style={{ fontSize: 11 }} onClick={() => setQueuedJob(null)}>✕</button>
          </div>
        </div>
      )}

      {inputType !== 'spotify' && parsed && (
        <div style={{ marginTop: '2rem' }}>

          {/* Stats */}
          <div className="stats" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            <div className="stat-box">
              <div className="stat-num">{totalArtists}</div>
              <div className="stat-label">Artists Found</div>
            </div>
            <div className="stat-box">
              <div className="stat-num">{totalTracks}</div>
              <div className="stat-label">Tracks Found</div>
            </div>
            <div className="stat-box">
              <div className="stat-num" style={{ color: inLidarrCount > 0 ? 'var(--text-muted)' : undefined }}>
                {statusLoading ? '…' : inLidarrCount}
              </div>
              <div className="stat-label">Already in Library</div>
            </div>
          </div>

          {totalArtists === 0 && (
            <div className="alert alert-info">No artists found. Try a different input or check your source.</div>
          )}

          {totalArtists > 0 && (
            <div className="card">
              <div className="flex-between mb-2">
                <div className="card-title" style={{ fontSize: 13 }}>
                  What was found
                  {statusLoading && (
                    <span className="text-muted" style={{ fontSize: 10, marginLeft: 8, fontWeight: 400 }}>
                      checking library…
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  {Object.keys(trackStatuses).length > 0 && (
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', gap: '0.5rem' }}>
                      <span><span style={{ color: '#4caf50' }}>●</span> Downloaded</span>
                      <span><span style={{ color: '#ffb300' }}>●</span> Monitored</span>
                      <span><span style={{ color: '#f44336' }}>●</span> Missing</span>
                    </span>
                  )}
                  {parsed.tracks?.length > 0 && (
                    <>
                      <button className="btn btn-ghost" onClick={handleDownloadM3U} style={{ fontSize: 10, padding: '2px 8px' }}>
                        ↓ M3U
                      </button>
                      <button className="btn btn-ghost" onClick={handleDownloadJSPF} style={{ fontSize: 10, padding: '2px 8px' }}>
                        ↓ JSPF
                      </button>
                    </>
                  )}
                </div>
              </div>

              <table className="table" style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ width: 60, padding: '4px 6px' }}></th>
                    <th style={{ padding: '4px 6px' }}>Artist / Title</th>
                    {hasAlbums && <th style={{ padding: '4px 6px' }}>Album</th>}
                    <th style={{ width: 24, padding: '4px 6px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {parsed.artists.map((artist, i) => {
                    const tracks = tracksByArtist[artist.name] || [];
                    const inLidarr = lidarrExisting[artist.name];
                    const confStyle = confidenceStyle(artist.confidence);
                    const confBadge = confidenceBadge(artist.confidence);

                    return (
                      <React.Fragment key={i}>
                        <tr style={{ borderTop: i > 0 ? '1px solid var(--border, rgba(255,255,255,0.07))' : undefined }}>
                          <td style={{ padding: '6px 6px 4px', verticalAlign: 'middle' }}>
                            {inLidarr ? (
                              <span className="badge badge-exists" style={{ fontSize: 8, padding: '1px 5px' }}>In Library</span>
                            ) : null}
                          </td>
                          <td style={{ padding: '6px 6px 4px', fontWeight: 600, fontSize: 13, lineHeight: 1.2, ...confStyle }}>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                              {artist.name}
                              {confBadge && (
                                <span title={confBadge.title} style={{ color: confBadge.color, fontSize: 10, fontStyle: 'normal', fontWeight: 700, cursor: 'default' }}>
                                  {confBadge.label}
                                </span>
                              )}
                            </span>
                            {tracks.length > 0 && (
                              <span className="text-muted" style={{ fontWeight: 400, fontSize: 10, marginLeft: 6 }}>
                                {tracks.length} track{tracks.length !== 1 ? 's' : ''}
                              </span>
                            )}
                          </td>
                          {hasAlbums && <td style={{ padding: '6px 6px 4px' }}></td>}
                          <td style={{ padding: '6px 6px 4px' }}></td>
                        </tr>

                        {tracks.map((track, j) => {
                          const key = `${track.artist}||${track.title}`;
                          const dot = statusDot(trackStatuses[key]);
                          return (
                            <tr key={`${i}-${j}`} style={{ opacity: 0.65 }}>
                              <td style={{ padding: '2px 6px' }}></td>
                              <td style={{ padding: '2px 6px 2px 18px', fontSize: 11 }}>
                                {track.title && track.title !== 'null' ? track.title : '—'}
                              </td>
                              {hasAlbums && (
                                <td className="text-muted" style={{ padding: '2px 6px', fontSize: 11 }}>
                                  {track.album && track.album !== 'null' ? track.album : ''}
                                </td>
                              )}
                              <td style={{ padding: '2px 6px', textAlign: 'center' }}>
                                {dot && (
                                  <span style={{ color: dot.color, fontSize: 12, cursor: 'default', lineHeight: 1 }} title={dot.title}>●</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>

            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spotify import tab
// ---------------------------------------------------------------------------

function SpotifyImportTab({ spotifyConfigured, plexConfigured, jellyfinConfigured, navidromeConfigured, deemixConfigured, slskdConfigured, onQueued }) {
  const [playlists, setPlaylists]         = useState(null);
  const [listsLoading, setListsLoading]   = useState(false);
  const [selectedPlaylist, setSelectedPlaylist] = useState('');
  const [loading, setLoading]             = useState(false);
  const [results, setResults]             = useState(null);
  const [playlistName, setPlaylistName]   = useState('');
  const [selected, setSelected]           = useState(new Set());
  const [lidarrStatus, setLidarrStatus]   = useState({});
  const [trackStatuses, setTrackStatuses] = useState({});
  const [error, setError]                 = useState(null);
  const [importing, setImporting]         = useState(false);
  const [syncTargets, setSyncTargets]     = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem('syncTargets')) || ['plex', 'spotify']); }
    catch { return new Set(['plex', 'spotify']); }
  });
  const [includeInRefresh, setIncludeInRefresh] = useState(true);

  useEffect(() => {
    if (!spotifyConfigured) return;
    (async () => {
      setListsLoading(true);
      try {
        const r = await axios.get('/api/spotify/playlists', { params: { filter: 'user' } });
        const all = r.data.playlists || [];
        setPlaylists(all);
        if (all.length) setSelectedPlaylist(all[0].id);
      } catch (e) {
        setError(e.response?.data?.detail || 'Failed to load playlists.');
      } finally {
        setListsLoading(false);
      }
    })();
  }, [spotifyConfigured]);

  const fetchTracks = useCallback(async () => {
    if (!selectedPlaylist) return;
    setLoading(true);
    setResults(null);
    setSelected(new Set());
    setTrackStatuses({});
    setLidarrStatus({});
    setError(null);
    try {
      const r = await axios.get(`/api/spotify/playlist/${selectedPlaylist}`);
      const data = r.data;
      setResults(data);
      setSelected(new Set(data.tracks.map((_, i) => i)));
      if (!playlistName) setPlaylistName(data.name);
      if (data.artists?.length) {
        axios.post('/api/lidarr/check-artists', { artists: data.artists.map(a => a.name) })
          .then(res => setLidarrStatus(res.data.results || {}))
          .catch(() => {});
      }
      if (data.tracks?.length) {
        axios.post('/api/lidarr/trackstatus', { tracks: data.tracks })
          .then(res => {
            const map = {};
            for (const t of res.data.tracks) map[`${t.artist}||${t.title}`] = t.status;
            setTrackStatuses(map);
          })
          .catch(() => {});
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to fetch playlist.');
    } finally {
      setLoading(false);
    }
  }, [selectedPlaylist, playlistName]);

  const toggle = i => setSelected(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });
  const toggleAll = () => setSelected(prev =>
    prev.size === results?.tracks.length ? new Set() : new Set(results.tracks.map((_, i) => i))
  );

  const handleImport = async () => {
    if (!results || selected.size === 0) return;
    const picked = results.tracks.filter((_, i) => selected.has(i));
    const seen = new Set();
    const artists = [];
    for (const t of picked) {
      if (!seen.has(t.artist)) { seen.add(t.artist); artists.push({ name: t.artist }); }
    }
    const name = playlistName.trim() || results.name;
    setImporting(true);
    setError(null);
    try {
      const res = await axios.post('/api/import/start', {
        artists,
        tracks: picked,
        playlist_name: name,
        source_url: `spotify:${selectedPlaylist}`,
        source_type: 'spotify',
        include_in_refresh: includeInRefresh,
        sync_targets: [...syncTargets],
      });
      onQueued({ job_id: res.data.job_id, playlist_id: res.data.playlist_id, name });
      setResults(null);
      setSelected(new Set());
      setPlaylistName('');
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to queue import.');
    } finally {
      setImporting(false);
    }
  };

  const statusDot = status => {
    if (status === 'green')  return { color: '#4caf50', title: 'Downloaded' };
    if (status === 'yellow') return { color: '#ffb300', title: 'Monitored, not downloaded' };
    if (status === 'red')    return { color: '#f44336', title: 'Not in Lidarr' };
    return null;
  };

  if (!spotifyConfigured) return (
    <p className="text-muted" style={{ fontSize: 13, marginTop: '0.5rem' }}>
      Connect your Spotify account in <Link to="/settings" style={{ color: 'var(--accent)' }}>Settings → Spotify</Link> to import your playlists and Liked Songs.
    </p>
  );

  if (listsLoading) return <p className="text-muted" style={{ fontSize: 13, marginTop: '0.5rem' }}>Loading playlists…</p>;

  return (
    <div style={{ marginTop: '0.5rem' }}>
      {error && <div className="alert alert-error mt-2" style={{ marginBottom: '1rem' }}>{error}</div>}

      {playlists && playlists.length === 0 && (
        <p className="text-muted" style={{ fontSize: 13 }}>No playlists found on your Spotify account.</p>
      )}

      {playlists && playlists.length > 0 && (
        <>
          <div className="field">
            <label>Playlist Name</label>
            <input value={playlistName} onChange={e => setPlaylistName(e.target.value)}
              placeholder="My Punk Playlist (optional)" />
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', marginBottom: '1rem', flexWrap: 'wrap' }}>
            <div className="field" style={{ margin: 0, flex: '1 1 200px' }}>
              <label>Playlist</label>
              <select value={selectedPlaylist} onChange={e => { setSelectedPlaylist(e.target.value); setResults(null); }}>
                {playlists.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <button className="btn btn-primary" onClick={fetchTracks} disabled={loading || !selectedPlaylist} style={{ flexShrink: 0 }}>
              {loading ? 'Digging…' : 'Dig Tracks'}
            </button>
          </div>

          {results && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{results.tracks.length} tracks</span>
                <button className="btn btn-ghost" onClick={toggleAll} style={{ fontSize: 11, padding: '2px 8px' }}>
                  {selected.size === results.tracks.length ? 'Deselect all' : 'Select all'}
                </button>
                {Object.keys(trackStatuses).length > 0 && (
                  <span style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', gap: '0.5rem', marginLeft: 'auto' }}>
                    <span><span style={{ color: '#4caf50' }}>●</span> Downloaded</span>
                    <span><span style={{ color: '#ffb300' }}>●</span> Monitored</span>
                    <span><span style={{ color: '#f44336' }}>●</span> Missing</span>
                  </span>
                )}
              </div>

              <div style={{ overflowX: 'auto', marginBottom: '1rem' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <th style={{ width: 32, padding: '6px 8px' }}></th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>Track</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>Artist</th>
                      <th style={{ width: 24, padding: '6px 8px' }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.tracks.map((t, i) => {
                      const key = `${t.artist}||${t.title}`;
                      const dot = statusDot(trackStatuses[key]);
                      return (
                        <tr key={i} onClick={() => toggle(i)}
                          style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', opacity: selected.has(i) ? 1 : 0.4 }}>
                          <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                            <input type="checkbox" checked={selected.has(i)} readOnly style={{ cursor: 'pointer' }} />
                          </td>
                          <td style={{ padding: '6px 8px' }}>{t.title}</td>
                          <td style={{ padding: '6px 8px', color: 'var(--text-dim)' }}>{t.artist}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                            {dot && <span style={{ color: dot.color, fontSize: 12, cursor: 'default' }} title={dot.title}>●</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', cursor: 'pointer', marginBottom: '0.5rem' }}
                onClick={() => setIncludeInRefresh(v => !v)}>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700,
                  color: includeInRefresh ? 'var(--green)' : 'var(--text-muted)',
                }}>
                  {includeInRefresh ? '✓' : '○'}
                </span>
                <span style={{ fontSize: 13 }}>Add to scheduled refresh</span>
              </div>

              {[plexConfigured, jellyfinConfigured, navidromeConfigured, deemixConfigured, slskdConfigured].filter(Boolean).length >= 1 && (
                <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>Sync to:</span>
                  {[
                    { id: 'plex', label: 'Plex', show: plexConfigured },
                    { id: 'jellyfin', label: 'Jellyfin', show: jellyfinConfigured },
                    { id: 'navidrome', label: 'Navidrome', show: navidromeConfigured },
                    { id: 'deemix', label: 'Deemix', show: deemixConfigured },
                    { id: 'slskd', label: 'Soulseek', show: slskdConfigured },
                  ].filter(t => t.show).map(({ id, label }) => (
                    <label key={id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: 13, cursor: 'pointer' }}>
                      <input type="checkbox" checked={syncTargets.has(id)}
                        onChange={e => setSyncTargets(prev => {
                          const s = new Set(prev);
                          e.target.checked ? s.add(id) : s.delete(id);
                          localStorage.setItem('syncTargets', JSON.stringify([...s]));
                          return s;
                        })} />
                      {label}
                    </label>
                  ))}
                </div>
              )}

              <button className="btn btn-primary" onClick={handleImport} disabled={importing || selected.size === 0}>
                {importing ? 'Importing…' : `Import ${selected.size} track${selected.size !== 1 ? 's' : ''} as Playlist`}
              </button>
            </>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Deemix / Deezer import tab
// ---------------------------------------------------------------------------

function DeemixImportTab({ plexConfigured, jellyfinConfigured, navidromeConfigured, slskdConfigured, onQueued }) {
  const [playlists, setPlaylists] = useState(null);
  const [listsLoading, setListsLoading] = useState(false);
  const [selectedPlaylist, setSelectedPlaylist] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [playlistName, setPlaylistName] = useState('');
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState(null);
  const [importing, setImporting] = useState(false);
  const [syncTargets, setSyncTargets] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem('syncTargets')) || ['plex']); }
    catch { return new Set(['plex']); }
  });
  const [includeInRefresh, setIncludeInRefresh] = useState(true);

  useEffect(() => {
    (async () => {
      setListsLoading(true);
      try {
        const r = await axios.get('/api/deemix/playlists');
        const all = r.data.playlists || [];
        setPlaylists(all);
        if (all.length) setSelectedPlaylist(all[0].id);
      } catch (e) {
        setError(e.response?.data?.detail || 'Failed to load playlists. Make sure you are logged into Deezer in your Deemix instance.');
      } finally {
        setListsLoading(false);
      }
    })();
  }, []);

  const fetchTracks = useCallback(async () => {
    if (!selectedPlaylist) return;
    setLoading(true);
    setResults(null);
    setSelected(new Set());
    setError(null);
    try {
      const r = await axios.get(`/api/deemix/playlist/${selectedPlaylist}`);
      setResults(r.data);
      setSelected(new Set(r.data.tracks.map((_, i) => i)));
      const pl = playlists?.find(p => p.id === selectedPlaylist);
      if (!playlistName && pl) setPlaylistName(pl.name);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to fetch playlist.');
    } finally {
      setLoading(false);
    }
  }, [selectedPlaylist, playlistName, playlists]);

  const toggle = i => setSelected(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });
  const toggleAll = () => setSelected(prev =>
    prev.size === results?.tracks.length ? new Set() : new Set(results.tracks.map((_, i) => i))
  );

  const handleImport = async () => {
    if (!results || selected.size === 0) return;
    const picked = results.tracks.filter((_, i) => selected.has(i));
    const seen = new Set();
    const artists = [];
    for (const t of picked) {
      if (t.artist && !seen.has(t.artist)) { seen.add(t.artist); artists.push({ name: t.artist }); }
    }
    const name = playlistName.trim() || playlists?.find(p => p.id === selectedPlaylist)?.name || 'Deezer Import';
    setImporting(true);
    setError(null);
    try {
      const res = await axios.post('/api/import/start', {
        artists,
        tracks: picked,
        playlist_name: name,
        source_url: `https://www.deezer.com/playlist/${selectedPlaylist}`,
        source_type: 'deemix',
        include_in_refresh: includeInRefresh,
        sync_targets: [...syncTargets],
      });
      onQueued({ job_id: res.data.job_id, playlist_id: res.data.playlist_id, name });
      setResults(null);
      setSelected(new Set());
      setPlaylistName('');
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to queue import.');
    } finally {
      setImporting(false);
    }
  };

  if (listsLoading) return <p className="text-muted" style={{ fontSize: 13, marginTop: '0.5rem' }}>Loading playlists…</p>;

  return (
    <div style={{ marginTop: '0.5rem' }}>
      {error && <div className="alert alert-error mt-2" style={{ marginBottom: '1rem' }}>{error}</div>}

      {playlists && playlists.length === 0 && (
        <p className="text-muted" style={{ fontSize: 13 }}>No playlists found. Make sure you are logged into Deezer in your Deemix instance.</p>
      )}

      {playlists && playlists.length > 0 && (
        <>
          <div className="field">
            <label>Playlist Name</label>
            <input value={playlistName} onChange={e => setPlaylistName(e.target.value)}
              placeholder="My Deezer Playlist (optional)" />
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', marginBottom: '1rem', flexWrap: 'wrap' }}>
            <div className="field" style={{ margin: 0, flex: '1 1 200px' }}>
              <label>Playlist</label>
              <select value={selectedPlaylist} onChange={e => { setSelectedPlaylist(e.target.value); setResults(null); }}>
                {playlists.map(p => <option key={p.id} value={p.id}>{p.name}{p.nb_tracks ? ` (${p.nb_tracks})` : ''}</option>)}
              </select>
            </div>
            <button className="btn btn-primary" onClick={fetchTracks} disabled={loading || !selectedPlaylist} style={{ flexShrink: 0 }}>
              {loading ? 'Loading…' : 'Load Tracks'}
            </button>
          </div>

          {results && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{results.tracks.length} tracks</span>
                <button className="btn btn-ghost" onClick={toggleAll} style={{ fontSize: 11, padding: '2px 8px' }}>
                  {selected.size === results.tracks.length ? 'Deselect all' : 'Select all'}
                </button>
              </div>

              <div style={{ overflowX: 'auto', marginBottom: '1rem' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <th style={{ width: 32, padding: '6px 8px' }}></th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>Track</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>Artist</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500 }}>Album</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.tracks.map((t, i) => (
                      <tr key={i} onClick={() => toggle(i)}
                        style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', opacity: selected.has(i) ? 1 : 0.4 }}>
                        <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                          <input type="checkbox" checked={selected.has(i)} readOnly style={{ cursor: 'pointer' }} />
                        </td>
                        <td style={{ padding: '6px 8px' }}>{t.title}</td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-dim)' }}>{t.artist}</td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-dim)', fontSize: 11 }}>{t.album}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', cursor: 'pointer', marginBottom: '0.5rem' }}
                onClick={() => setIncludeInRefresh(v => !v)}>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 18, height: 18, flexShrink: 0, fontSize: 14, fontWeight: 700,
                  color: includeInRefresh ? 'var(--green)' : 'var(--text-muted)',
                }}>
                  {includeInRefresh ? '✓' : '○'}
                </span>
                <span style={{ fontSize: 13 }}>Add to scheduled refresh</span>
              </div>

              {[plexConfigured, jellyfinConfigured, navidromeConfigured, slskdConfigured].filter(Boolean).length >= 1 && (
                <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>Sync to:</span>
                  {[
                    { id: 'plex', label: 'Plex', show: plexConfigured },
                    { id: 'jellyfin', label: 'Jellyfin', show: jellyfinConfigured },
                    { id: 'navidrome', label: 'Navidrome', show: navidromeConfigured },
                    { id: 'slskd', label: 'Soulseek', show: slskdConfigured },
                  ].filter(t => t.show).map(({ id, label }) => (
                    <label key={id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: 13, cursor: 'pointer' }}>
                      <input type="checkbox" checked={syncTargets.has(id)}
                        onChange={e => setSyncTargets(prev => {
                          const s = new Set(prev);
                          e.target.checked ? s.add(id) : s.delete(id);
                          localStorage.setItem('syncTargets', JSON.stringify([...s]));
                          return s;
                        })} />
                      {label}
                    </label>
                  ))}
                </div>
              )}

              <button className="btn btn-primary" onClick={handleImport} disabled={importing || selected.size === 0}>
                {importing ? 'Importing…' : `Import ${selected.size} track${selected.size !== 1 ? 's' : ''} as Playlist`}
              </button>
            </>
          )}
        </>
      )}
    </div>
  );
}

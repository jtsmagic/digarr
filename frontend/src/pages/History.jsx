import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { formatDate } from '../utils';

function safeHref(url) {
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') ? url : null;
  } catch { return null; }
}

export default function History() {
  const [playlists, setPlaylists] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [syncStates, setSyncStates] = useState({});  // { [id]: { loading, result, error } }
  const [jellyfinSyncStates, setJellyfinSyncStates] = useState({});
  const [navidromeSyncStates, setNavidromeSyncStates] = useState({});
  const [spotifyPushStates, setSpotifyPushStates] = useState({});  // { [id]: { loading, result, error } }
  const [spotifyConnected, setSpotifyConnected] = useState(false);
  const [jellyfinConfigured, setJellyfinConfigured] = useState(false);
  const [navidromeConfigured, setNavidromeConfigured] = useState(false);
  const [refreshStates, setRefreshStates] = useState({});  // { [id]: { loading, result, error } }
  const [syncAllLoading, setSyncAllLoading] = useState(false);
  const [syncAllResult, setSyncAllResult] = useState(null);
  const [jellyfinSyncAllLoading, setJellyfinSyncAllLoading] = useState(false);
  const [jellyfinSyncAllResult, setJellyfinSyncAllResult] = useState(null);
  const [navidromeSyncAllLoading, setNavidromeSyncAllLoading] = useState(false);
  const [navidromeSyncAllResult, setNavidromeSyncAllResult] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null); // playlist id pending delete
  const [confirmRefresh, setConfirmRefresh] = useState(null); // playlist id pending refresh
  const [renamingId, setRenamingId] = useState(null); // playlist id being renamed
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState(null);
  const [menuOpenId, setMenuOpenId] = useState(null); // overflow menu open for playlist id
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
  const [globalMerge, setGlobalMerge] = useState(false);
  const [schedulerStatus, setSchedulerStatus] = useState(null);
  const [runningNow, setRunningNow] = useState(false);
  const [runSummary, setRunSummary] = useState(null);
  const [excludedIds, setExcludedIds] = useState(new Set());
  const [blocklist, setBlocklist] = useState([]);
  const [newBlocklistEntry, setNewBlocklistEntry] = useState('');
  const [blocklistSaving, setBlocklistSaving] = useState(false);
  const [wantedData, setWantedData] = useState(null);
  const [wantedLoading, setWantedLoading] = useState(false);
  const [lidarrConfigured, setLidarrConfigured] = useState(false);
  const [importJobs, setImportJobs] = useState([]);
  const completedJobIds = useRef(new Set());
  const dismissedJobIds = useRef(new Set());

  // Manual track matching
  const [searchModal, setSearchModal] = useState(null); // { track, playlistId }
  const [searchQuery, setSearchQuery] = useState('');
  const [searchSource, setSearchSource] = useState('plex');
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchSaved, setSearchSaved] = useState(null); // track key that was just saved
  const [manualMatches, setManualMatches] = useState({}); // { "artist||title" → matched track }
  const [ignoredTracks, setIgnoredTracks] = useState(new Set()); // Set of "artist_lower||title_lower"
  const [expandedIgnored, setExpandedIgnored] = useState({}); // { playlistId → bool }

  // Close overflow menu on any outside click
  useEffect(() => {
    if (!menuOpenId) return;
    const handler = () => setMenuOpenId(null);
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [menuOpenId]);

  useEffect(() => {
    axios.get('/api/spotify/status').then(r => setSpotifyConnected(r.data.connected)).catch(() => {});
    axios.get('/api/jellyfin/status').then(r => setJellyfinConfigured(r.data.configured)).catch(() => {});
    axios.get('/api/navidrome/status').then(r => setNavidromeConfigured(r.data.configured)).catch(() => {});
    axios.get('/api/config').then(r => {
      setTimezone(r.data.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
      setBlocklist(r.data.artist_blocklist || []);
      setGlobalMerge(r.data.refresh_merge_tracks || false);
      setExcludedIds(new Set(r.data.refresh_excluded_playlist_ids || []));
      setLidarrConfigured(!!(r.data.lidarr_url && r.data.lidarr_api_key));
    }).catch(() => {});
    axios.get('/api/scheduler/status').then(r => setSchedulerStatus(r.data)).catch(() => {});
    axios.get('/api/playlists').then(r => {
      setPlaylists(r.data.playlists);
      setLoading(false);
    }).catch(() => setLoading(false));
    axios.get('/api/import/jobs').then(r => setImportJobs((r.data.jobs || []).filter(j => !dismissedJobIds.current.has(j.id)))).catch(() => {});
    axios.get('/api/library/ignored-tracks').then(r => {
      const keys = new Set((r.data.ignored || []).map(t => `${t.artist.toLowerCase()}||${t.title.toLowerCase()}`));
      setIgnoredTracks(keys);
    }).catch(() => {});
  }, []);

  // Poll job progress while any job is running/queued
  useEffect(() => {
    const active = importJobs.filter(j => j.status === 'queued' || j.status === 'running');
    if (!active.length) return;
    const activeIds = new Set(active.map(j => j.id));

    // Immediately reload playlists so newly-queued imports appear without waiting for completion
    axios.get('/api/playlists').then(r => setPlaylists(r.data.playlists)).catch(() => {});

    const timer = setInterval(async () => {
      try {
        const res = await axios.get('/api/import/jobs');
        const newJobs = res.data.jobs || [];

        // When a job we were watching just finished, reload the playlist list
        const newlyDone = newJobs.filter(j =>
          activeIds.has(j.id) && j.status === 'done' && !completedJobIds.current.has(j.id)
        );
        if (newlyDone.length > 0) {
          newlyDone.forEach(j => completedJobIds.current.add(j.id));
          axios.get('/api/playlists').then(r => setPlaylists(r.data.playlists)).catch(() => {});
        }

        setImportJobs(newJobs.filter(j => !dismissedJobIds.current.has(j.id)));
      } catch {}
    }, 2000);

    return () => clearInterval(timer);
  }, [importJobs]);

  const handleRunNow = async () => {
    setRunningNow(true);
    setRunSummary(null);
    try {
      const res = await axios.post('/api/scheduler/run-now');
      setSchedulerStatus(prev => ({ ...prev, last_run: res.data.last_run }));
      setRunSummary(res.data.summary || []);
    } catch {}
    setRunningNow(false);
  };

  const addToBlocklist = async (name) => {
    if (!name || blocklist.map(a => a.toLowerCase()).includes(name.toLowerCase())) return;
    const updated = [...blocklist, name];
    setBlocklist(updated);
    setBlocklistSaving(true);
    try { await axios.post('/api/config', { artist_blocklist: updated }); } catch {}
    setBlocklistSaving(false);
  };

  const removeFromBlocklist = async (i) => {
    const updated = blocklist.filter((_, j) => j !== i);
    setBlocklist(updated);
    setBlocklistSaving(true);
    try { await axios.post('/api/config', { artist_blocklist: updated }); } catch {}
    setBlocklistSaving(false);
  };

  // Search modal helpers
  const openSearchModal = (track, playlistId) => {
    setSearchModal({ track, playlistId });
    const q = [track.artist, track.title].filter(Boolean).join(' ');
    setSearchQuery(q);
    setSearchResults([]);
    setSearchSaved(null);
  };

  const closeSearchModal = () => {
    setSearchModal(null);
    setSearchQuery('');
    setSearchResults([]);
  };

  const runLibrarySearch = async (q, source) => {
    if (!q || q.trim().length < 2) { setSearchResults([]); return; }
    setSearchLoading(true);
    try {
      const res = await axios.get('/api/library/search', { params: { q: q.trim(), limit: 20, source: source || searchSource } });
      setSearchResults(res.data.results || []);
    } catch {
      setSearchResults([]);
    } finally {
      setSearchLoading(false);
    }
  };

  const handleIgnoreTrack = async (track) => {
    const key = `${(track.artist || '').toLowerCase()}||${(track.title || '').toLowerCase()}`;
    setIgnoredTracks(prev => new Set([...prev, key]));
    try {
      await axios.post('/api/library/ignore-track', { artist: track.artist || '', title: track.title || '' });
    } catch {
      setIgnoredTracks(prev => { const n = new Set(prev); n.delete(key); return n; });
    }
  };

  const handleUnignoreTrack = async (track) => {
    const key = `${(track.artist || '').toLowerCase()}||${(track.title || '').toLowerCase()}`;
    setIgnoredTracks(prev => { const n = new Set(prev); n.delete(key); return n; });
    try {
      await axios.delete('/api/library/ignore-track', { data: { artist: track.artist || '', title: track.title || '' } });
    } catch {
      setIgnoredTracks(prev => new Set([...prev, key]));
    }
  };

  const confirmManualMatch = async (result) => {
    if (!searchModal) return;
    const { track } = searchModal;
    try {
      await axios.post('/api/library/manual-match', {
        artist: track.artist || '',
        title: track.title || '',
        external_id: result.external_id,
        source: result.source || 'plex',
      });
      const key = `${(track.artist || '').toLowerCase()}||${(track.title || '').toLowerCase()}`;
      setManualMatches(prev => ({ ...prev, [key]: result }));
      setSearchSaved(key);
      setTimeout(closeSearchModal, 800);
    } catch {
      // ignore — modal stays open so user can retry
    }
  };

  const handlePushToSpotify = async (pl) => {
    setSpotifyPushStates(prev => ({ ...prev, [pl.id]: { loading: true, result: null, error: null } }));
    try {
      const res = await axios.post(`/api/spotify/push/${pl.id}`);
      setSpotifyPushStates(prev => ({ ...prev, [pl.id]: { loading: false, result: res.data, error: null } }));
      setPlaylists(prev => prev.map(p =>
        p.id === pl.id ? { ...p, spotify_playlist_id: res.data.playlist_id } : p
      ));
    } catch (err) {
      setSpotifyPushStates(prev => ({ ...prev, [pl.id]: { loading: false, result: null, error: err.response?.data?.detail || 'Spotify push failed.' } }));
    }
  };

  const handleDownloadM3U = (pl) => {
    window.open(`/api/playlists/${pl.id}/m3u`, '_blank');
  };

  const handleDownloadJSPF = (pl) => {
    window.open(`/api/playlists/${pl.id}/jspf`, '_blank');
  };

  const handleLoadWanted = async () => {
    setWantedLoading(true);
    try {
      const res = await axios.get('/api/lidarr/wanted');
      setWantedData(res.data);
    } catch (err) {
      setWantedData({ error: err.response?.data?.detail || 'Failed to load wanted list.' });
    }
    setWantedLoading(false);
  };

  const handleSyncPlex = async (e, pl) => {
    e.stopPropagation();
    setSyncStates(prev => ({ ...prev, [pl.id]: { loading: true, result: null, error: null } }));
    try {
      const res = await axios.post(`/api/plex/playlist/${pl.id}/sync`);
      setSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: res.data, error: null } }));
      setPlaylists(prev => prev.map(p =>
        p.id === pl.id ? {
          ...p,
          plex_playlist_id: res.data.plex_playlist_id,
          plex_matched_count: res.data.matched,
          plex_total_count: res.data.total,
        } : p
      ));
    } catch (err) {
      const msg = err.response?.data?.detail || 'Plex sync failed.';
      setSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: null, error: msg } }));
    }
  };

  const handleSyncJellyfin = async (e, pl) => {
    e.stopPropagation();
    setJellyfinSyncStates(prev => ({ ...prev, [pl.id]: { loading: true, result: null, error: null } }));
    try {
      const res = await axios.post(`/api/jellyfin/playlist/${pl.id}/sync`);
      setJellyfinSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: res.data, error: null } }));
      setPlaylists(prev => prev.map(p =>
        p.id === pl.id ? { ...p, jellyfin_playlist_id: res.data.jellyfin_playlist_id, jellyfin_matched_count: res.data.matched, jellyfin_total_count: res.data.total } : p
      ));
    } catch (err) {
      setJellyfinSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: null, error: err.response?.data?.detail || 'Jellyfin sync failed.' } }));
    }
  };

  const handleSyncNavidrome = async (e, pl) => {
    e.stopPropagation();
    setNavidromeSyncStates(prev => ({ ...prev, [pl.id]: { loading: true, result: null, error: null } }));
    try {
      const res = await axios.post(`/api/navidrome/playlist/${pl.id}/sync`);
      setNavidromeSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: res.data, error: null } }));
      setPlaylists(prev => prev.map(p =>
        p.id === pl.id ? { ...p, navidrome_playlist_id: res.data.navidrome_playlist_id, navidrome_matched_count: res.data.matched, navidrome_total_count: res.data.total } : p
      ));
    } catch (err) {
      setNavidromeSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: null, error: err.response?.data?.detail || 'Navidrome sync failed.' } }));
    }
  };

  const handleRefresh = async (e, pl) => {
    e.stopPropagation();
    if (confirmRefresh !== pl.id) {
      setConfirmRefresh(pl.id);
      return;
    }
    setConfirmRefresh(null);
    setRefreshStates(prev => ({ ...prev, [pl.id]: { loading: true, result: null, error: null } }));
    try {
      const res = await axios.post(`/api/playlists/${pl.id}/refresh`);

      // Use the backend's auto-sync results if they fired (only fires when matched count grew)
      let mediaUpdate = {};
      const ps = res.data.plex_sync;
      if (ps) {
        mediaUpdate = {
          ...mediaUpdate,
          plex_playlist_id: ps.plex_playlist_id ?? pl.plex_playlist_id,
          plex_matched_count: ps.matched,
          plex_total_count: ps.total,
          plex_unmatched_tracks: ps.unmatched ?? [],
        };
        setSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: ps, error: null } }));
      }
      const js = res.data.jellyfin_sync;
      if (js) {
        mediaUpdate = { ...mediaUpdate, jellyfin_matched_count: js.matched, jellyfin_total_count: js.total };
        setJellyfinSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: js, error: null } }));
      }
      const ns = res.data.navidrome_sync;
      if (ns) {
        mediaUpdate = { ...mediaUpdate, navidrome_matched_count: ns.matched, navidrome_total_count: ns.total };
        setNavidromeSyncStates(prev => ({ ...prev, [pl.id]: { loading: false, result: ns, error: null } }));
      }

      setRefreshStates(prev => ({ ...prev, [pl.id]: { loading: false, result: res.data, error: null } }));
      setPlaylists(prev => prev.map(p =>
        p.id === pl.id
          ? { ...p, last_refreshed_at: new Date().toISOString(), ...mediaUpdate }
          : p
      ));
    } catch (err) {
      const msg = err.response?.data?.detail || 'Refresh failed.';
      setRefreshStates(prev => ({ ...prev, [pl.id]: { loading: false, result: null, error: msg } }));
    }
  };

  const handleDelete = async (e, pl) => {
    e.stopPropagation();
    if (confirmDelete === pl.id) {
      try {
        await axios.delete(`/api/playlists/${pl.id}`);
        setPlaylists(prev => prev.filter(p => p.id !== pl.id));
        if (selected?.id === pl.id) setSelected(null);
      } catch {
        // deletion failed — leave playlist in list
      }
      setConfirmDelete(null);
    } else {
      setConfirmDelete(pl.id);
    }
  };

  const handleToggleExcluded = async (e, pl) => {
    e.stopPropagation();
    const next = new Set(excludedIds);
    if (next.has(pl.id)) next.delete(pl.id);
    else next.add(pl.id);
    setExcludedIds(next);
    axios.post('/api/config', { refresh_excluded_playlist_ids: [...next] }).catch(() => {});
  };

  const handleSetMergeTracks = async (e, pl, value) => {
    e.stopPropagation();
    await axios.post(`/api/playlists/${pl.id}/set-merge-tracks`, { merge_tracks: value });
    setPlaylists(prev => prev.map(p => p.id === pl.id ? { ...p, merge_tracks: value } : p));
    if (selected?.id === pl.id) setSelected(prev => ({ ...prev, merge_tracks: value }));
  };

  const handleSyncAll = async () => {
    setSyncAllLoading(true);
    setSyncAllResult(null);
    try {
      const res = await axios.post('/api/plex/sync-all');
      setSyncAllResult(res.data);
      // Update local playlist state with fresh match counts
      setPlaylists(prev => prev.map(p => {
        const r = res.data.results?.find(r => r.id === p.id);
        if (!r || r.status !== 'ok') return p;
        return {
          ...p,
          plex_playlist_id: r.plex_playlist_id ?? p.plex_playlist_id,
          plex_matched_count: r.matched,
          plex_total_count: r.total,
          plex_unmatched_tracks: r.unmatched ?? p.plex_unmatched_tracks,
        };
      }));
    } catch (err) {
      setSyncAllResult({ error: err.response?.data?.detail || 'Sync all failed.' });
    }
    setSyncAllLoading(false);
  };

  const handleSyncAllJellyfin = async () => {
    setJellyfinSyncAllLoading(true);
    setJellyfinSyncAllResult(null);
    try {
      const res = await axios.post('/api/jellyfin/sync-all');
      setJellyfinSyncAllResult(res.data);
      setPlaylists(prev => prev.map(p => {
        const r = res.data.results?.find(r => r.id === p.id);
        if (!r || r.status !== 'ok') return p;
        return { ...p, jellyfin_playlist_id: r.jellyfin_playlist_id ?? p.jellyfin_playlist_id, jellyfin_matched_count: r.matched, jellyfin_total_count: r.total };
      }));
    } catch (err) {
      setJellyfinSyncAllResult({ error: err.response?.data?.detail || 'Jellyfin sync all failed.' });
    }
    setJellyfinSyncAllLoading(false);
  };

  const handleSyncAllNavidrome = async () => {
    setNavidromeSyncAllLoading(true);
    setNavidromeSyncAllResult(null);
    try {
      const res = await axios.post('/api/navidrome/sync-all');
      setNavidromeSyncAllResult(res.data);
      setPlaylists(prev => prev.map(p => {
        const r = res.data.results?.find(r => r.id === p.id);
        if (!r || r.status !== 'ok') return p;
        return { ...p, navidrome_playlist_id: r.navidrome_playlist_id ?? p.navidrome_playlist_id, navidrome_matched_count: r.matched, navidrome_total_count: r.total };
      }));
    } catch (err) {
      setNavidromeSyncAllResult({ error: err.response?.data?.detail || 'Navidrome sync all failed.' });
    }
    setNavidromeSyncAllLoading(false);
  };

  const startRename = (e, pl) => {
    e.stopPropagation();
    setRenamingId(pl.id);
    setRenameValue(displayName(pl.name));
    setRenameError(null);
  };

  const commitRename = async (pl) => {
    const trimmed = renameValue.trim();
    if (!trimmed || trimmed === pl.name) {
      setRenamingId(null);
      return;
    }
    try {
      await axios.put(`/api/playlists/${pl.id}/name`, { name: trimmed });
      setPlaylists(prev => prev.map(p => p.id === pl.id ? { ...p, name: trimmed } : p));
      if (selected?.id === pl.id) setSelected(prev => ({ ...prev, name: trimmed }));
      setRenamingId(null);
      setRenameError(null);
    } catch (err) {
      setRenameError(err.response?.data?.detail || 'Rename failed.');
    }
  };

  const cancelRename = () => {
    setRenamingId(null);
    setRenameError(null);
  };

  const fmt = (iso) => formatDate(iso, timezone);

  // Strip any "— Digarr" / "-- Digarr" / "- Digarr" suffix from display names.
  // The suffix belongs in Plex only (plex_playlist_name); some older playlists have
  // it baked into the name column. We strip it at render time without touching the DB.
  const displayName = (name) => name.replace(/\s+[\u2014\-]{1,2}\s*Digarr\s*$/i, '').trim();

  const sourceIcon = (type) => {
    switch (type) {
      case 'url': return '🌐';
      case 'm3u_url': return '📡';
      case 'file': return '📁';
      case 'text': return '📝';
      default: return '⦿';
    }
  };

  // Debounce search as the user types or switches source
  useEffect(() => {
    if (!searchModal) return;
    const timer = setTimeout(() => runLibrarySearch(searchQuery, searchSource), 280);
    return () => clearTimeout(timer);
  }, [searchQuery, searchSource, searchModal]);

  // Close search modal on Escape
  useEffect(() => {
    if (!searchModal) return;
    const handler = (e) => { if (e.key === 'Escape') closeSearchModal(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [searchModal]);

  if (loading) {
    return (
      <div>
        <h1 className="page-title">History</h1>
        <p className="page-subtitle">All your past imports</p>
        <div className="empty"><span className="spinner" style={{ width: 32, height: 32 }} /></div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="page-title">History</h1>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <p className="page-subtitle" style={{ margin: 0 }}>All your past imports — {playlists.length} total</p>
        {playlists.some(p => p.plex_playlist_id) && (
          <button className="btn btn-ghost" style={{ fontSize: 11, color: 'var(--accent)', borderColor: 'var(--accent)' }}
            disabled={syncAllLoading} onClick={handleSyncAll}>
            {syncAllLoading ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Syncing…</> : '⟳ Sync All to Plex'}
          </button>
        )}
        {jellyfinConfigured && playlists.some(p => p.jellyfin_playlist_id) && (
          <button className="btn btn-ghost" style={{ fontSize: 11, color: '#00a4dc', borderColor: '#00a4dc' }}
            disabled={jellyfinSyncAllLoading} onClick={handleSyncAllJellyfin}>
            {jellyfinSyncAllLoading ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Syncing…</> : '⟳ Sync All to Jellyfin'}
          </button>
        )}
        {navidromeConfigured && playlists.some(p => p.navidrome_playlist_id) && (
          <button className="btn btn-ghost" style={{ fontSize: 11, color: '#fc6e51', borderColor: '#fc6e51' }}
            disabled={navidromeSyncAllLoading} onClick={handleSyncAllNavidrome}>
            {navidromeSyncAllLoading ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Syncing…</> : '⟳ Sync All to Navidrome'}
          </button>
        )}
      </div>
      {syncAllResult && !syncAllResult.error && (
        <div style={{ marginBottom: '1rem', fontSize: 12, display: 'grid', gap: '0.2rem' }}>
          {syncAllResult.results?.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <span style={{ color: r.status === 'error' ? 'var(--red)' : 'var(--text-muted)' }}>
                {r.status === 'error' ? '✕' : '✓'}
              </span>
              <span>{r.name}</span>
              {r.status === 'ok' && (
                <span className="text-muted">{r.matched}/{r.total} matched</span>
              )}
              {r.status === 'error' && (
                <span style={{ color: 'var(--red)', fontSize: 11 }}>{r.error}</span>
              )}
            </div>
          ))}
        </div>
      )}
      {syncAllResult?.error && (
        <div className="alert alert-error" style={{ marginBottom: '1rem' }}>{syncAllResult.error}</div>
      )}
      {jellyfinSyncAllResult && !jellyfinSyncAllResult.error && (
        <div style={{ marginBottom: '1rem', fontSize: 12, display: 'grid', gap: '0.2rem' }}>
          {jellyfinSyncAllResult.results?.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <span style={{ color: r.status === 'error' ? 'var(--red)' : 'var(--text-muted)' }}>{r.status === 'error' ? '✕' : '✓'}</span>
              <span>{r.name}</span>
              {r.status === 'ok' && <span className="text-muted">{r.matched}/{r.total} matched</span>}
              {r.status === 'error' && <span style={{ color: 'var(--red)', fontSize: 11 }}>{r.error}</span>}
            </div>
          ))}
        </div>
      )}
      {jellyfinSyncAllResult?.error && <div className="alert alert-error" style={{ marginBottom: '1rem' }}>{jellyfinSyncAllResult.error}</div>}
      {navidromeSyncAllResult && !navidromeSyncAllResult.error && (
        <div style={{ marginBottom: '1rem', fontSize: 12, display: 'grid', gap: '0.2rem' }}>
          {navidromeSyncAllResult.results?.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <span style={{ color: r.status === 'error' ? 'var(--red)' : 'var(--text-muted)' }}>{r.status === 'error' ? '✕' : '✓'}</span>
              <span>{r.name}</span>
              {r.status === 'ok' && <span className="text-muted">{r.matched}/{r.total} matched</span>}
              {r.status === 'error' && <span style={{ color: 'var(--red)', fontSize: 11 }}>{r.error}</span>}
            </div>
          ))}
        </div>
      )}
      {navidromeSyncAllResult?.error && <div className="alert alert-error" style={{ marginBottom: '1rem' }}>{navidromeSyncAllResult.error}</div>}

      {/* Scheduled Refresh panel */}
      {schedulerStatus && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
            <div>
              <div className="card-title" style={{ marginBottom: '0.2rem' }}>Scheduled Refresh</div>
              <p className="text-muted" style={{ fontSize: 12, margin: 0 }}>
                Re-fetches each playlist from its source URL, adds new artists to Lidarr, and re-syncs connected media servers. Only applies to playlists with a source URL (not static imports).
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem', marginTop: '0.75rem' }}>
            <div style={{ display: 'flex', gap: '1.5rem', fontSize: 12 }}>
              <span className="text-muted">
                Last run: <span style={{ color: 'var(--text)' }}>{fmt(schedulerStatus.last_run)}</span>
              </span>
              {schedulerStatus.interval_hours > 0 && (
                <span className="text-muted">
                  Next: <span style={{ color: 'var(--text)' }}>{fmt(schedulerStatus.next_run)}</span>
                </span>
              )}
            </div>
            <button className="btn btn-ghost" onClick={handleRunNow} disabled={runningNow} style={{ fontSize: 12 }}>
              {runningNow ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Running…</> : '▶ Run Now'}
            </button>
          </div>
          {runSummary?.length > 0 && (() => {
            const totalNew = runSummary.reduce((n, r) => n + (r.new_artists || 0), 0);
            const errors = runSummary.filter(r => r.status === 'error');
            return (
              <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
                <div className="text-muted" style={{ fontSize: 11, marginBottom: '0.4rem' }}>
                  {runSummary.length} playlist{runSummary.length !== 1 ? 's' : ''} · {totalNew} new artist{totalNew !== 1 ? 's' : ''} added
                  {errors.length > 0 && <span style={{ color: 'var(--red)', marginLeft: 8 }}>{errors.length} error{errors.length !== 1 ? 's' : ''}</span>}
                </div>
                <div style={{ display: 'grid', gap: '0.2rem' }}>
                  {runSummary.map((r, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: 12 }}>
                      <span style={{ color: r.status === 'error' ? 'var(--red)' : r.new_artists > 0 ? 'var(--green)' : 'var(--text-muted)', minWidth: 24 }}>
                        {r.status === 'error' ? '✕' : r.new_artists > 0 ? `+${r.new_artists}` : '✓'}
                      </span>
                      <span>{r.name}</span>
                      {r.status === 'error' && <span className="text-muted">{r.error}</span>}
                      {r.status === 'ok' && <span className="text-muted">{r.total_tracks} tracks</span>}
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* Active import jobs */}
      {importJobs.length > 0 && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div className="card-title" style={{ marginBottom: '0.9rem' }}>Imports</div>
          {importJobs.map(job => {
            const isDone = job.status === 'done';
            const isError = job.status === 'error';
            const pct = job.total > 0 ? Math.round((job.current / job.total) * 100) : 0;
            const added = isDone ? (job.results || []).filter(r => r.status === 'added').length : 0;
            const errors = isDone ? (job.results || []).filter(r => r.status === 'error').length : 0;
            return (
              <div key={job.id} style={{ marginBottom: '0.9rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
                    <span style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {job.playlist_name}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
                    <span style={{ fontSize: 11, color: isDone ? 'var(--green)' : isError ? 'var(--red)' : 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                      {isDone
                        ? `✓ ${added} new artist${added !== 1 ? 's' : ''}${errors > 0 ? ` · ${errors} failed` : ''}${job.plex_result ? ` · Plex: ${job.plex_result.matched}/${job.plex_result.total}` : ''}${job.jellyfin_result ? ` · Jellyfin: ${job.jellyfin_result.matched}/${job.jellyfin_result.total}` : ''}${job.navidrome_result ? ` · Navidrome: ${job.navidrome_result.matched}/${job.navidrome_result.total}` : ''}${job.deemix_result ? ` · Deemix: ${job.deemix_result.queued}/${job.deemix_result.total}` : ''}${job.slskd_result ? ` · Soulseek: ${job.slskd_result.queued} queued${job.slskd_result.flagged > 0 ? ` · ${job.slskd_result.flagged} flagged` : ''}` : ''}`
                        : isError ? `Error: ${job.error}`
                        : job.status === 'queued' ? 'Queued…'
                        : `${job.current}/${job.total}${job.current_artist ? `: ${job.current_artist}` : ''}`}
                    </span>
                    {(isDone || isError) && (
                      <button className="btn btn-ghost" style={{ fontSize: 10, padding: '1px 6px' }}
                        onClick={() => {
                          dismissedJobIds.current.add(job.id);
                          axios.delete(`/api/import/jobs/${job.id}`).catch(() => {});
                          setImportJobs(prev => prev.filter(j => j.id !== job.id));
                        }}>
                        ✕
                      </button>
                    )}
                  </div>
                </div>
                <div style={{ height: 5, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${isDone || isError ? 100 : pct}%`,
                    background: isDone ? 'var(--green)' : isError ? 'var(--red)' : 'var(--accent)',
                    borderRadius: 3,
                    transition: 'width 0.4s ease',
                  }} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {playlists.length === 0 ? (
        <div className="empty">
          <span className="empty-icon">⦿</span>
          No digs yet.{' '}
          <Link to="/" style={{ color: 'var(--accent)', textDecoration: 'none' }}>Head to Import to get started →</Link>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '1rem' }}>
          {playlists.map(pl => {
            const sync = syncStates[pl.id] || {};
            const jellyfinSync = jellyfinSyncStates[pl.id] || {};
            const navidromeSync = navidromeSyncStates[pl.id] || {};
            const refresh = refreshStates[pl.id] || {};
            const spotifyPush = spotifyPushStates[pl.id] || {};
            const canRefresh = pl.source_url && ['url', 'm3u_url', 'listenbrainz', 'similar', 'discogs', 'spotify'].includes(pl.source_type);
            const activeJob = importJobs.find(j => j.playlist_id === pl.id && (j.status === 'queued' || j.status === 'running'));
            return (
              <div key={pl.id} className="card">
                <div className="flex-between" style={{ cursor: 'pointer', borderRadius: 6, margin: '-0.25rem', padding: '0.25rem', transition: 'background 0.12s' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  onClick={() => setSelected(selected?.id === pl.id ? null : pl)}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <span style={{ color: 'var(--accent)', fontSize: 29, fontWeight: 700, transition: 'transform 0.15s', display: 'inline-block', transform: selected?.id === pl.id ? 'rotate(90deg)' : 'rotate(0deg)', userSelect: 'none', lineHeight: 1 }}>›</span>
                      {sourceIcon(pl.source_type)}{' '}
                      {renamingId === pl.id ? (
                        <span onClick={e => e.stopPropagation()} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                          <input
                            autoFocus
                            value={renameValue}
                            onChange={e => setRenameValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') commitRename(pl);
                              if (e.key === 'Escape') cancelRename();
                            }}
                            style={{
                              background: 'var(--input-bg, rgba(255,255,255,0.08))',
                              border: '1px solid var(--accent)',
                              borderRadius: 4,
                              color: 'var(--text)',
                              padding: '2px 6px',
                              fontSize: 14,
                              fontWeight: 600,
                              width: 260,
                            }}
                          />
                          <button className="btn btn-ghost" style={{ fontSize: 10, color: 'var(--accent)', borderColor: 'var(--accent)' }}
                            onClick={e => { e.stopPropagation(); commitRename(pl); }}>Save</button>
                          <button className="btn btn-ghost" style={{ fontSize: 10 }}
                            onClick={e => { e.stopPropagation(); cancelRename(); }}>Cancel</button>
                          {renameError && <span style={{ fontSize: 11, color: 'var(--red)', marginLeft: 4 }}>{renameError}</span>}
                        </span>
                      ) : (
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                          {displayName(pl.name)}
                          <span
                            title="Rename"
                            onClick={e => startRename(e, pl)}
                            style={{ cursor: 'pointer', opacity: 0.4, fontSize: 11, lineHeight: 1 }}
                            onMouseEnter={e => e.currentTarget.style.opacity = 1}
                            onMouseLeave={e => e.currentTarget.style.opacity = 0.4}
                          >✎</span>
                        </span>
                      )}
                    </div>
                    <div className="text-muted text-mono">
                      {fmt(pl.created_at)}
                      {safeHref(pl.source_url) && (
                        <> · <a href={safeHref(pl.source_url)} target="_blank" rel="noreferrer"
                          style={{ color: 'var(--accent)', textDecoration: 'none' }}
                          onClick={e => e.stopPropagation()}>
                          source ↗
                        </a></>
                      )}
                    </div>
                  </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {activeJob ? (
                      <span className="badge" style={{ background: 'rgba(var(--accent-rgb,99,102,241),0.15)', color: 'var(--accent)', fontSize: 10, display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                        <span className="spinner" style={{ width: 8, height: 8 }} /> Importing…
                      </span>
                    ) : (
                      <span className="badge badge-added">{pl.artists_added?.length || 0} new artists</span>
                    )}
                    {pl.plex_playlist_id && (
                      <span className="badge" style={{ background: 'var(--accent)', color: '#fff', fontSize: 10, cursor: 'default' }}
                        title={pl.plex_matched_count != null
                          ? `${pl.plex_matched_count} of ${pl.plex_total_count} tracks matched in Plex`
                          : 'Playlist exists in Plex'}>
                        {pl.plex_matched_count != null
                          ? `Plex: ${pl.plex_matched_count}/${pl.plex_total_count} tracks`
                          : 'In Plex'}
                      </span>
                    )}
                    {pl.jellyfin_playlist_id && (
                      <span className="badge" style={{ background: '#00a4dc', color: '#fff', fontSize: 10, cursor: 'default' }}
                        title={pl.jellyfin_matched_count != null
                          ? `${pl.jellyfin_matched_count} of ${pl.jellyfin_total_count} tracks matched in Jellyfin`
                          : 'Playlist exists in Jellyfin'}>
                        {pl.jellyfin_matched_count != null
                          ? `Jellyfin: ${pl.jellyfin_matched_count}/${pl.jellyfin_total_count}`
                          : 'In Jellyfin'}
                      </span>
                    )}
                    {pl.navidrome_playlist_id && (
                      <span className="badge" style={{ background: '#fc6e51', color: '#fff', fontSize: 10, cursor: 'default' }}
                        title={pl.navidrome_matched_count != null
                          ? `${pl.navidrome_matched_count} of ${pl.navidrome_total_count} tracks matched in Navidrome`
                          : 'Playlist exists in Navidrome'}>
                        {pl.navidrome_matched_count != null
                          ? `Navidrome: ${pl.navidrome_matched_count}/${pl.navidrome_total_count}`
                          : 'In Navidrome'}
                      </span>
                    )}

                    {confirmDelete === pl.id ? (
                      /* Delete confirmation — replaces all other actions */
                      <>
                        <span style={{ fontSize: 11, color: 'var(--red)' }}>Delete?</span>
                        <button className="btn btn-ghost" style={{ fontSize: 10, color: 'var(--red)', borderColor: 'var(--red)' }}
                          onClick={e => handleDelete(e, pl)}>
                          Confirm
                        </button>
                        <button className="btn btn-ghost" style={{ fontSize: 10 }}
                          onClick={e => { e.stopPropagation(); setConfirmDelete(null); }}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {/* Refresh — primary action, visible when available */}
                        {canRefresh && (confirmRefresh === pl.id ? (
                          <>
                            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Overwrite?</span>
                            <button className="btn btn-ghost" style={{ fontSize: 10, color: 'var(--accent)', borderColor: 'var(--accent)' }}
                              onClick={e => handleRefresh(e, pl)}>
                              Confirm
                            </button>
                            <button className="btn btn-ghost" style={{ fontSize: 10 }}
                              onClick={e => { e.stopPropagation(); setConfirmRefresh(null); }}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button className="btn btn-ghost" style={{ fontSize: 10, color: 'var(--accent)', borderColor: 'var(--accent)' }}
                            disabled={refresh.loading} onClick={e => handleRefresh(e, pl)}>
                            {refresh.loading
                              ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Refreshing…</>
                              : '↻ Refresh Source'}
                          </button>
                        ))}
                        {refresh.result && (
                          <span style={{ fontSize: 11, color: refresh.result.new_artists?.length > 0 ? 'var(--green)' : 'var(--text-muted)' }}>
                            {refresh.result.new_artists?.length > 0
                              ? `+${refresh.result.new_artists.length} new`
                              : 'Up to date'}
                          </span>
                        )}
                        {refresh.error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{refresh.error}</span>}

                        {/* Sync Plex — primary action */}
                        <button className="btn btn-ghost" style={{ fontSize: 10, color: 'var(--accent)', borderColor: 'var(--accent)' }}
                          disabled={sync.loading} onClick={e => handleSyncPlex(e, pl)}>
                          {sync.loading
                            ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Syncing…</>
                            : pl.plex_playlist_id ? '⟳ Sync Plex' : '▶ Push to Plex'}
                        </button>
                        {sync.result && (
                          <span style={{ fontSize: 11, color: sync.result.matched > 0 ? 'var(--green)' : 'var(--text-muted)' }}>
                            {sync.result.matched}/{sync.result.total} matched
                          </span>
                        )}
                        {sync.error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{sync.error}</span>}

                        {/* Spotify push state */}
                        {spotifyPush.loading && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Pushing to Spotify…</span>}
                        {spotifyPush.result && (
                          <span style={{ fontSize: 11, color: 'var(--green)' }}>
                            Spotify: {spotifyPush.result.matched_count}/{spotifyPush.result.total_count} matched
                          </span>
                        )}
                        {spotifyPush.error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{spotifyPush.error}</span>}

                        {/* Jellyfin — primary button only when already synced */}
                        {jellyfinConfigured && pl.jellyfin_playlist_id && (
                          <button className="btn btn-ghost" style={{ fontSize: 10, color: '#00a4dc', borderColor: '#00a4dc' }}
                            disabled={jellyfinSync.loading} onClick={e => handleSyncJellyfin(e, pl)}>
                            {jellyfinSync.loading
                              ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Syncing…</>
                              : '⟳ Sync Jellyfin'}
                          </button>
                        )}
                        {jellyfinSync.result && <span style={{ fontSize: 11, color: 'var(--green)' }}>Jellyfin: {jellyfinSync.result.matched}/{jellyfinSync.result.total}</span>}
                        {jellyfinSync.error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{jellyfinSync.error}</span>}

                        {/* Navidrome — primary button only when already synced */}
                        {navidromeConfigured && pl.navidrome_playlist_id && (
                          <button className="btn btn-ghost" style={{ fontSize: 10, color: '#fc6e51', borderColor: '#fc6e51' }}
                            disabled={navidromeSync.loading} onClick={e => handleSyncNavidrome(e, pl)}>
                            {navidromeSync.loading
                              ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Syncing…</>
                              : '⟳ Sync Navidrome'}
                          </button>
                        )}
                        {navidromeSync.result && <span style={{ fontSize: 11, color: 'var(--green)' }}>Navidrome: {navidromeSync.result.matched}/{navidromeSync.result.total}</span>}
                        {navidromeSync.error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{navidromeSync.error}</span>}

                        {/* Overflow menu — M3U download + Delete */}
                        <div style={{ position: 'relative' }} onClick={e => e.stopPropagation()}>
                          <button className="btn btn-ghost" style={{ fontSize: 13, padding: '2px 8px', lineHeight: 1 }}
                            onClick={e => { e.stopPropagation(); setMenuOpenId(menuOpenId === pl.id ? null : pl.id); }}>
                            ⋯
                          </button>
                          {menuOpenId === pl.id && (
                            <div style={{
                              position: 'absolute', right: 0, top: 'calc(100% + 4px)', zIndex: 100,
                              background: 'var(--card-bg, #1e1e2e)',
                              border: '1px solid var(--border)',
                              borderRadius: 6,
                              boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
                              minWidth: 140,
                              overflow: 'hidden',
                            }}>
                              <button
                                style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: 'var(--text)', padding: '8px 14px', fontSize: 12, cursor: 'pointer' }}
                                onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
                                onMouseLeave={e => e.currentTarget.style.background = 'none'}
                                onClick={() => { setMenuOpenId(null); handleDownloadM3U(pl); }}>
                                ↓ Download M3U
                              </button>
                              <button
                                style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: 'var(--text)', padding: '8px 14px', fontSize: 12, cursor: 'pointer' }}
                                onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
                                onMouseLeave={e => e.currentTarget.style.background = 'none'}
                                onClick={() => { setMenuOpenId(null); handleDownloadJSPF(pl); }}>
                                ↓ Download JSPF
                              </button>
                              {spotifyConnected && (
                                <>
                                  <div style={{ height: 1, background: 'var(--border)', margin: '0 8px' }} />
                                  <button
                                    style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: 'var(--text)', padding: '8px 14px', fontSize: 12, cursor: 'pointer' }}
                                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
                                    onMouseLeave={e => e.currentTarget.style.background = 'none'}
                                    onClick={() => { setMenuOpenId(null); handlePushToSpotify(pl); }}>
                                    {pl.spotify_playlist_id ? '⟳ Sync to Spotify' : '▶ Push to Spotify'}
                                  </button>
                                </>
                              )}
                              {jellyfinConfigured && (
                                <>
                                  <div style={{ height: 1, background: 'var(--border)', margin: '0 8px' }} />
                                  <button
                                    style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: 'var(--text)', padding: '8px 14px', fontSize: 12, cursor: 'pointer' }}
                                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
                                    onMouseLeave={e => e.currentTarget.style.background = 'none'}
                                    onClick={e => { setMenuOpenId(null); handleSyncJellyfin(e, pl); }}>
                                    {pl.jellyfin_playlist_id ? '⟳ Sync to Jellyfin' : '▶ Push to Jellyfin'}
                                  </button>
                                </>
                              )}
                              {navidromeConfigured && (
                                <>
                                  <div style={{ height: 1, background: 'var(--border)', margin: '0 8px' }} />
                                  <button
                                    style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: 'var(--text)', padding: '8px 14px', fontSize: 12, cursor: 'pointer' }}
                                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
                                    onMouseLeave={e => e.currentTarget.style.background = 'none'}
                                    onClick={e => { setMenuOpenId(null); handleSyncNavidrome(e, pl); }}>
                                    {pl.navidrome_playlist_id ? '⟳ Sync to Navidrome' : '▶ Push to Navidrome'}
                                  </button>
                                </>
                              )}
                              <div style={{ height: 1, background: 'var(--border)', margin: '0 8px' }} />
                              <button
                                style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: 'var(--red)', padding: '8px 14px', fontSize: 12, cursor: 'pointer' }}
                                onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.06)'}
                                onMouseLeave={e => e.currentTarget.style.background = 'none'}
                                onClick={() => { setMenuOpenId(null); setConfirmDelete(pl.id); }}>
                                Delete
                              </button>
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* Expanded detail */}
                {selected?.id === pl.id && (
                  <div onClick={e => e.stopPropagation()} style={{ marginTop: '1rem', borderTop: '1px solid var(--border)', paddingTop: '1rem', display: 'grid', gap: '1rem' }}>
                    {pl.last_refreshed_at && (
                      <div className="text-muted text-mono" style={{ fontSize: 11 }}>
                        Last refreshed {fmt(pl.last_refreshed_at)}
                      </div>
                    )}
                    {(() => {
                      const results = pl.lidarr_results || [];
                      if (results.length === 0 && !pl.artists_added?.length) return null;

                      // Use full lidarr_results if available, otherwise fall back to artists_added list
                      if (results.length > 0) {
                        const added = results.filter(r => r.status === 'added');
                        const exists = results.filter(r => r.status === 'already_exists');
                        const errors = results.filter(r => r.status === 'error');
                        return (
                          <div>
                            <div className="card-title" style={{ marginBottom: '0.75rem' }}>Lidarr</div>
                            {added.length > 0 && (
                              <div style={{ marginBottom: '0.5rem' }}>
                                <div className="text-muted" style={{ fontSize: 11, marginBottom: '0.3rem' }}>
                                  Added ({added.length})
                                </div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                                  {added.map((r, i) => (
                                    <span key={i} className="badge badge-added" style={{ fontSize: 11 }}>
                                      {r.artist}{r.album_monitored ? ` — ${r.album_monitored}` : ''}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {exists.length > 0 && (
                              <div style={{ marginBottom: '0.5rem' }}>
                                <div className="text-muted" style={{ fontSize: 11, marginBottom: '0.3rem' }}>
                                  Already in library ({exists.length})
                                </div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                                  {exists.map((r, i) => (
                                    <span key={i} className="badge badge-exists" style={{ fontSize: 11 }}>{r.artist}</span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {errors.length > 0 && (
                              <div>
                                <div className="text-muted" style={{ fontSize: 11, marginBottom: '0.3rem' }}>
                                  Errors ({errors.length})
                                </div>
                                <div style={{ display: 'grid', gap: '0.25rem' }}>
                                  {errors.map((r, i) => (
                                    <div key={i} style={{ display: 'flex', gap: '0.5rem', fontSize: 12, alignItems: 'center' }}>
                                      <span className="badge badge-error" style={{ fontSize: 10 }}>Error</span>
                                      <span style={{ fontWeight: 500 }}>{r.artist}</span>
                                      {r.message && <span className="text-muted">{r.message}</span>}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      }

                      // Legacy fallback — old imports only stored artists_added
                      return (
                        <div>
                          <div className="card-title" style={{ marginBottom: '0.5rem' }}>Artists Added to Lidarr</div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                            {pl.artists_added.map((a, i) => (
                              <span key={i} className="badge badge-added">{a}</span>
                            ))}
                          </div>
                        </div>
                      );
                    })()}
                    {(() => {
                      // Prefer live sync result unmatched, fall back to stored
                      const syncResult = syncStates[pl.id]?.result;
                      const unmatched = syncResult?.unmatched ?? pl.plex_unmatched_tracks ?? [];
                      const matchedCount = syncResult?.matched ?? pl.plex_matched_count;
                      const totalCount = syncResult?.total ?? pl.plex_total_count;
                      if (!pl.plex_playlist_id && !syncResult) return null;
                      return (
                        <div>
                          <div className="card-title" style={{ marginBottom: '0.5rem' }}>
                            Plex Match
                            {matchedCount != null && (
                              <span className="text-muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                                {matchedCount}/{totalCount} tracks
                              </span>
                            )}
                          </div>
                          {unmatched.length > 0 ? (
                            <>
                              {(() => {
                                const activeRows = unmatched.filter(t => !ignoredTracks.has(`${(t.artist||'').toLowerCase()}||${(t.title||'').toLowerCase()}`));
                                const ignoredRows = unmatched.filter(t => ignoredTracks.has(`${(t.artist||'').toLowerCase()}||${(t.title||'').toLowerCase()}`));
                                const showIgnored = expandedIgnored[pl.id];
                                const visibleRows = showIgnored ? [...activeRows, ...ignoredRows] : activeRows;
                                const activeCount = activeRows.length;
                                return (
                                  <>
                                    {activeCount > 0 && (
                                      <div className="text-muted" style={{ fontSize: 11, marginBottom: '0.4rem' }}>
                                        {activeCount} track{activeCount !== 1 ? 's' : ''} not found in Plex:
                                      </div>
                                    )}
                                    {visibleRows.length > 0 && (
                                      <table className="table" style={{ fontSize: 12 }}>
                                        <thead>
                                          <tr><th>Artist</th><th>Title</th><th>Album</th><th style={{ width: 140 }}></th></tr>
                                        </thead>
                                        <tbody>
                                          {visibleRows.map((t, i) => {
                                            const artistLower = (t.artist || '').toLowerCase();
                                            const titleLower = (t.title || '').toLowerCase();
                                            const matchKey = `${artistLower}||${titleLower}`;
                                            const isMatched = !!manualMatches[matchKey];
                                            const justSaved = searchSaved === matchKey;
                                            const isIgnored = ignoredTracks.has(matchKey);
                                            const inLidarr = artistLower && (pl.artists_added || []).some(
                                              a => a.toLowerCase() === artistLower
                                            );
                                            return (
                                              <tr key={i} style={isIgnored ? { opacity: 0.4 } : undefined}>
                                                <td className="text-muted">
                                                  {t.artist && t.artist !== 'null' ? t.artist : '—'}
                                                  {!isIgnored && !inLidarr && artistLower && (
                                                    <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--red)' }}>✕ not monitored</span>
                                                  )}
                                                </td>
                                                <td>{t.title && t.title !== 'null' ? t.title : '—'}</td>
                                                <td className="text-muted">{t.album && t.album !== 'null' ? t.album : '—'}</td>
                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                  {isIgnored ? (
                                                    <button
                                                      className="btn btn-ghost"
                                                      style={{ fontSize: 9, padding: '3px 8px', letterSpacing: 1 }}
                                                      onClick={() => handleUnignoreTrack(t)}
                                                    >
                                                      Unignore
                                                    </button>
                                                  ) : isMatched || justSaved ? (
                                                    <span style={{ fontSize: 10, color: 'var(--green)', fontFamily: 'var(--font-mono)' }}>
                                                      ✓ matched
                                                    </span>
                                                  ) : (
                                                    <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                                                      <button
                                                        className="btn btn-ghost"
                                                        style={{ fontSize: 9, padding: '3px 8px', letterSpacing: 1 }}
                                                        onClick={() => openSearchModal(t, pl.id)}
                                                      >
                                                        Search library
                                                      </button>
                                                      <button
                                                        className="btn btn-ghost"
                                                        style={{ fontSize: 9, padding: '3px 8px', letterSpacing: 1, borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                                                        onClick={() => handleIgnoreTrack(t)}
                                                      >
                                                        Ignore
                                                      </button>
                                                    </div>
                                                  )}
                                                </td>
                                              </tr>
                                            );
                                          })}
                                        </tbody>
                                      </table>
                                    )}
                                    {ignoredRows.length > 0 && (
                                      <button
                                        onClick={() => setExpandedIgnored(prev => ({ ...prev, [pl.id]: !prev[pl.id] }))}
                                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)', padding: '4px 0', marginTop: 4 }}
                                      >
                                        {showIgnored ? '▲' : '▼'} {ignoredRows.length} ignored
                                      </button>
                                    )}
                                    {activeCount === 0 && ignoredRows.length === 0 && null}
                                  </>
                                );
                              })()}
                              {syncResult?.lidarr_monitored?.length > 0 && (
                                <div style={{ marginTop: '0.5rem', fontSize: 11, color: 'var(--green)' }}>
                                  Triggered Lidarr search for {syncResult.lidarr_monitored.length} album{syncResult.lidarr_monitored.length !== 1 ? 's' : ''}:{' '}
                                  {syncResult.lidarr_monitored.map((m, i) => (
                                    <span key={i}>
                                      {i > 0 && ', '}
                                      <strong>{m.artist}</strong> — {m.album}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </>
                          ) : matchedCount != null ? (
                            <div className="text-muted" style={{ fontSize: 12 }}>All tracks matched ✓</div>
                          ) : null}
                        </div>
                      );
                    })()}
                    {/* Soulseek flagged tracks — manual review */}
                    {pl.slskd_flagged_count > 0 && (() => {
                      const flaggedKey = `slskd_${pl.id}`;
                      const flagged = window._slskdFlaggedCache?.[pl.id];
                      if (!flagged) {
                        axios.get(`/api/playlists/${pl.id}/slskd-flagged`).then(r => {
                          if (!window._slskdFlaggedCache) window._slskdFlaggedCache = {};
                          window._slskdFlaggedCache[pl.id] = r.data.flagged;
                          // Force re-render
                          setPlaylists(prev => [...prev]);
                        }).catch(() => {});
                        return null;
                      }
                      if (!flagged.length) return null;
                      return (
                        <div>
                          <div className="card-title" style={{ marginBottom: '0.5rem' }}>
                            Soulseek — Flagged Tracks
                            <span className="text-muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                              {pl.slskd_queued_count} queued · {pl.slskd_flagged_count} need review
                            </span>
                          </div>
                          <p className="text-muted" style={{ fontSize: 11, marginBottom: '0.5rem' }}>
                            These tracks scored below your confidence threshold. Pick the best match to queue, or skip.
                          </p>
                          {flagged.map((track, ti) => (
                            <div key={ti} style={{ marginBottom: '0.75rem', padding: '0.5rem', background: 'var(--bg)', borderRadius: 6, border: '1px solid var(--border)' }}>
                              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: '0.35rem' }}>
                                {track.artist} — {track.title}
                                <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>
                                  best score: {track.score?.toFixed(0)}%
                                </span>
                              </div>
                              {(track.candidates || []).slice(0, 3).map((c, ci) => (
                                <div key={ci} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: 11, marginBottom: '0.2rem' }}>
                                  <span style={{ color: 'var(--text-muted)', minWidth: 36 }}>{c.score?.toFixed(0)}%</span>
                                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                                    {c.filename?.split(/[\\/]/).pop()}
                                  </span>
                                  <span className="text-muted" style={{ fontSize: 10 }}>{c.username}</span>
                                  <button className="btn btn-ghost" style={{ fontSize: 9, padding: '2px 6px', flexShrink: 0 }}
                                    onClick={() => {
                                      axios.post('/api/slskd/queue', {
                                        artist: track.artist, title: track.title,
                                        username: c.username, filename: c.filename, size: c.size || 0,
                                      }).then(() => {
                                        if (!window._slskdFlaggedCache) window._slskdFlaggedCache = {};
                                        window._slskdFlaggedCache[pl.id] = (window._slskdFlaggedCache[pl.id] || []).filter((_, i) => i !== ti);
                                        setPlaylists(prev => [...prev]);
                                      }).catch(e => alert(e.response?.data?.detail || 'Queue failed'));
                                    }}>
                                    Queue
                                  </button>
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>
                      );
                    })()}

                    {canRefresh && (() => {
                      const excluded = excludedIds.has(pl.id);
                      return (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', userSelect: 'none' }}
                          onClick={e => handleToggleExcluded(e, pl)}>
                          <span style={{ fontSize: 14, fontWeight: 700, color: excluded ? 'var(--text-muted)' : 'var(--green)' }}>
                            {excluded ? '○' : '✓'}
                          </span>
                          <span style={{ fontSize: 12 }}>Include in scheduled refresh</span>
                        </div>
                      );
                    })()}

                    {pl.source_url && ['url', 'm3u_url', 'listenbrainz', 'similar', 'discogs'].includes(pl.source_type) && (() => {
                      const effective = pl.merge_tracks !== null && pl.merge_tracks !== undefined ? !!pl.merge_tracks : globalMerge;
                      return (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', userSelect: 'none' }}
                          onClick={e => handleSetMergeTracks(e, pl, !effective)}>
                          <span style={{ fontSize: 14, fontWeight: 700, color: effective ? 'var(--green)' : 'var(--text-muted)' }}>
                            {effective ? '✓' : '○'}
                          </span>
                          <span style={{ fontSize: 12 }}>Append tracks on refresh</span>
                          {(pl.merge_tracks === null || pl.merge_tracks === undefined) && (
                            <span className="text-muted" style={{ fontSize: 10 }}>(global default)</span>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Wanted / Missing — Lidarr only */}
      {lidarrConfigured && <div className="card" style={{ marginTop: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <div className="card-title" style={{ marginBottom: 0 }}>Wanted / Missing</div>
          <button className="btn btn-ghost" style={{ fontSize: 11 }} onClick={handleLoadWanted} disabled={wantedLoading}>
            {wantedLoading ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Loading…</> : '↻ Load from Lidarr'}
          </button>
        </div>
        <p className="text-muted" style={{ fontSize: 12, marginBottom: '0.75rem' }}>
          Albums Lidarr is monitoring but hasn't downloaded yet.
        </p>
        {wantedData?.error && (
          <div className="alert alert-error" style={{ fontSize: 12 }}>{wantedData.error}</div>
        )}
        {wantedData && !wantedData.error && (
          wantedData.albums?.length > 0 ? (
            <>
              <div className="text-muted" style={{ fontSize: 11, marginBottom: '0.4rem' }}>
                {wantedData.total} album{wantedData.total !== 1 ? 's' : ''} missing across {wantedData.digarr_artist_count} Digarr artists
              </div>
              <table className="table" style={{ fontSize: 12 }}>
                <thead>
                  <tr><th>Artist</th><th>Album</th><th>Released</th></tr>
                </thead>
                <tbody>
                  {wantedData.albums.map((a, i) => (
                    <tr key={i}>
                      <td className="text-muted">{a.artist || '—'}</td>
                      <td>{a.title || '—'}</td>
                      <td className="text-muted">{a.release_date || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <div style={{ fontSize: 12 }}>
              <div className="text-muted">Nothing missing for your {wantedData.digarr_artist_count} Digarr artists. ✓</div>
              {wantedData.lidarr_total > 0 && (
                <div className="text-muted" style={{ fontSize: 11, marginTop: '0.3rem' }}>
                  (Lidarr has {wantedData.lidarr_total} wanted album{wantedData.lidarr_total !== 1 ? 's' : ''} total — none match artists in your playlists)
                </div>
              )}
            </div>
          )
        )}
      </div>}

      {/* Artist Blocklist */}
      <div className="card" style={{ marginTop: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <div className="card-title" style={{ marginBottom: 0 }}>Artist Blocklist</div>
          {blocklistSaving && <span className="text-muted" style={{ fontSize: 11 }}>Saving…</span>}
        </div>
        <p className="text-muted" style={{ fontSize: 12, marginBottom: '0.75rem' }}>
          Artists on this list are silently skipped during imports and scheduled refreshes.
        </p>
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <input
            value={newBlocklistEntry}
            onChange={e => setNewBlocklistEntry(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && newBlocklistEntry.trim()) {
                addToBlocklist(newBlocklistEntry.trim());
                setNewBlocklistEntry('');
              }
            }}
            placeholder="Artist name to block"
            style={{ flex: 1 }}
          />
          <button className="btn btn-ghost" onClick={() => {
            addToBlocklist(newBlocklistEntry.trim());
            setNewBlocklistEntry('');
          }}>Add</button>
        </div>
        {blocklist.length > 0 ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
            {blocklist.map((a, i) => (
              <span key={i} style={{
                display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                background: 'rgba(255,255,255,0.06)', border: '1px solid var(--border)',
                borderRadius: 4, padding: '3px 8px', fontSize: 12,
              }}>
                {a}
                <button onClick={() => removeFromBlocklist(i)}
                  style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0, fontSize: 12, lineHeight: 1 }}>
                  ✕
                </button>
              </span>
            ))}
          </div>
        ) : (
          <span className="text-muted" style={{ fontSize: 12 }}>No artists blocked.</span>
        )}
      </div>

      {/* ── Manual track search modal ── */}
      {searchModal && (
        <div className="modal-overlay" onClick={closeSearchModal}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Find in library</div>
                <div className="modal-subtitle">
                  {searchModal.track.artist && searchModal.track.artist !== 'null' ? searchModal.track.artist : ''}
                  {searchModal.track.artist && searchModal.track.title ? ' — ' : ''}
                  {searchModal.track.title && searchModal.track.title !== 'null' ? searchModal.track.title : ''}
                </div>
              </div>
              <button className="modal-close" onClick={closeSearchModal}>✕</button>
            </div>

            <div className="modal-body">
              {/* Source tabs — shown when multiple libraries are configured */}
              {(jellyfinConfigured || navidromeConfigured) && (
                <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.75rem' }}>
                  {[
                    { id: 'plex', label: 'Plex', show: true },
                    { id: 'jellyfin', label: 'Jellyfin', show: jellyfinConfigured },
                    { id: 'navidrome', label: 'Navidrome', show: navidromeConfigured },
                  ].filter(t => t.show).map(({ id, label }) => (
                    <button key={id} className="btn btn-ghost"
                      style={{ fontSize: 11, padding: '3px 10px', background: searchSource === id ? 'var(--accent)' : 'none', color: searchSource === id ? '#fff' : 'var(--text-muted)', borderColor: searchSource === id ? 'var(--accent)' : 'var(--border)' }}
                      onClick={() => setSearchSource(id)}>
                      {label}
                    </button>
                  ))}
                </div>
              )}
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                <input
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder="Search artist or title…"
                  autoFocus
                  style={{ flex: 1 }}
                />
                {searchLoading && (
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    <span className="spinner" />
                  </div>
                )}
              </div>

              {searchResults.length > 0 ? (
                <table className="table" style={{ fontSize: 12 }}>
                  <thead>
                    <tr><th>Artist</th><th>Title</th><th>Album</th><th style={{ width: 60 }}></th></tr>
                  </thead>
                  <tbody>
                    {searchResults.map((r, i) => (
                      <tr key={i} style={{ cursor: 'pointer' }} onClick={() => confirmManualMatch(r)}>
                        <td className="text-muted">{r.artist || '—'}</td>
                        <td>{r.title || '—'}</td>
                        <td className="text-muted">{r.album || '—'}</td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            className="btn btn-ghost"
                            style={{ fontSize: 9, padding: '3px 8px', letterSpacing: 1 }}
                            onClick={e => { e.stopPropagation(); confirmManualMatch(r); }}
                          >
                            Match
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : searchQuery.trim().length >= 2 && !searchLoading ? (
                <div className="text-muted" style={{ textAlign: 'center', padding: '1.5rem 0' }}>
                  No tracks found. Try a different search.
                  <div style={{ marginTop: '0.5rem', fontSize: 11 }}>
                    If your library cache is empty, refresh it in Settings → {searchSource === 'jellyfin' ? 'Jellyfin' : searchSource === 'navidrome' ? 'Navidrome' : 'Plex'}.
                  </div>
                </div>
              ) : !searchLoading && (
                <div className="text-muted" style={{ fontSize: 11, textAlign: 'center', padding: '0.5rem 0' }}>
                  Type to search your music library
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button className="btn btn-ghost" onClick={closeSearchModal}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import axios from 'axios';

/**
 * Manual "find this track in my library" dialog.
 *
 * Lives in its own module and is loaded lazily: it is an occasional workflow, so
 * its markup, state and search logic should not sit in the initial bundle for
 * every visit to History. All of its state is local — History only needs to know
 * which track is open, and to hear about a confirmed match.
 */
export default function TrackSearchModal({
  track,
  jellyfinConfigured,
  navidromeConfigured,
  onClose,
  onMatched,
}) {
  const [query, setQuery] = useState(
    () => [track.artist, track.title].filter(Boolean).join(' ')
  );
  const [source, setSource] = useState('plex');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  // Debounced search as the user types or switches source.
  useEffect(() => {
    if (!query || query.trim().length < 2) {
      setResults([]);
      return undefined;
    }
    let alive = true;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await axios.get('/api/library/search', {
          params: { q: query.trim(), limit: 20, source },
        });
        if (alive) setResults(res.data.results || []);
      } catch {
        if (alive) setResults([]);
      } finally {
        if (alive) setLoading(false);
      }
    }, 280);
    return () => { alive = false; clearTimeout(timer); };
  }, [query, source]);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const confirmMatch = async (result) => {
    try {
      await axios.post('/api/library/manual-match', {
        artist: track.artist || '',
        title: track.title || '',
        external_id: result.external_id,
        source: result.source || 'plex',
      });
      const key = `${(track.artist || '').toLowerCase()}||${(track.title || '').toLowerCase()}`;
      onMatched(key, result);
      setTimeout(onClose, 800);
    } catch {
      // stays open so the user can retry
    }
  };

  const artist = track.artist && track.artist !== 'null' ? track.artist : '';
  const title = track.title && track.title !== 'null' ? track.title : '';

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-title">Find in library</div>
            <div className="modal-subtitle">
              {artist}{artist && title ? ' — ' : ''}{title}
            </div>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          {(jellyfinConfigured || navidromeConfigured) && (
            <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.75rem' }}>
              {[
                { id: 'plex', label: 'Plex', show: true },
                { id: 'jellyfin', label: 'Jellyfin', show: jellyfinConfigured },
                { id: 'navidrome', label: 'Navidrome', show: navidromeConfigured },
              ].filter(t => t.show).map(({ id, label }) => (
                <button key={id} className="btn btn-ghost"
                  style={{ fontSize: 11, padding: '3px 10px', background: source === id ? 'var(--accent)' : 'none', color: source === id ? '#fff' : 'var(--text-muted)', borderColor: source === id ? 'var(--accent)' : 'var(--border)' }}
                  onClick={() => setSource(id)}>
                  {label}
                </button>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search artist or title…"
              autoFocus
              style={{ flex: 1 }}
            />
            {loading && (
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <span className="spinner" />
              </div>
            )}
          </div>

          {results.length > 0 ? (
            <table className="table" style={{ fontSize: 12 }}>
              <thead>
                <tr><th>Artist</th><th>Title</th><th>Album</th><th style={{ width: 60 }}></th></tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} style={{ cursor: 'pointer' }} onClick={() => confirmMatch(r)}>
                    <td className="text-muted">{r.artist || '—'}</td>
                    <td>{r.title || '—'}</td>
                    <td className="text-muted">{r.album || '—'}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        className="btn btn-ghost"
                        style={{ fontSize: 9, padding: '3px 8px', letterSpacing: 1 }}
                        onClick={e => { e.stopPropagation(); confirmMatch(r); }}
                      >
                        Match
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : query.trim().length >= 2 && !loading ? (
            <div className="text-muted" style={{ textAlign: 'center', padding: '1.5rem 0' }}>
              No tracks found. Try a different search.
              <div style={{ marginTop: '0.5rem', fontSize: 11 }}>
                If your library cache is empty, refresh it in Settings → {source === 'jellyfin' ? 'Jellyfin' : source === 'navidrome' ? 'Navidrome' : 'Plex'}.
              </div>
            </div>
          ) : !loading && (
            <div className="text-muted" style={{ fontSize: 11, textAlign: 'center', padding: '0.5rem 0' }}>
              Type to search your music library
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

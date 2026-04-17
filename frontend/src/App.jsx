import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Link } from 'react-router-dom';
import axios from 'axios';
import Import from './pages/Import';
import History from './pages/History';
import Settings from './pages/Settings';
import Discover from './pages/Discover';
import Login from './pages/Login';
import './App.css';

function App() {
  const [authRequired, setAuthRequired] = useState(false);
  const [authMethods, setAuthMethods] = useState([]);
  const [authUsername, setAuthUsername] = useState(null);
  const [authenticated, setAuthenticated] = useState(true); // optimistic until status loads
  const [authLoading, setAuthLoading] = useState(true);

  const checkAuth = async () => {
    try {
      const r = await axios.get('/api/auth/status');
      setAuthRequired(r.data.auth_required);
      setAuthMethods(r.data.methods || []);
      setAuthUsername(r.data.username || null);
      setAuthenticated(r.data.authenticated);
    } catch {
      // If status endpoint fails, assume no auth required
      setAuthenticated(true);
    } finally {
      setAuthLoading(false);
    }
  };

  useEffect(() => {
    checkAuth();

    // Intercept 401s — any protected API call returning 401 means session expired
    const interceptor = axios.interceptors.response.use(
      r => r,
      err => {
        if (err.response?.status === 401 && err.config?.url !== '/api/auth/status') {
          setAuthenticated(false);
        }
        return Promise.reject(err);
      }
    );
    return () => axios.interceptors.response.eject(interceptor);
  }, []);

  // Handle ?oidc_error= and ?spotify_error= from OAuth callback redirects
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('oidc_error') || params.get('spotify_error')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const handleLogout = async () => {
    try {
      await axios.post('/api/auth/logout');
    } catch {}
    setAuthenticated(false);
  };

  if (authLoading) return null;

  if (authRequired && !authenticated) {
    return <Login methods={authMethods} onSuccess={() => setAuthenticated(true)} />;
  }

  return (
    <BrowserRouter>
      <div className="app">
        <header className="header">
          <div className="header-inner">
            <Link to="/" className="logo" style={{ textDecoration: 'none' }}>
              <span className="logo-icon">⦿</span>
              <span className="logo-text">Digarr</span>
              <span className="logo-tag">v{process.env.REACT_APP_VERSION || '1.0.1'}</span>
            </Link>
            <nav className="nav">
              {authRequired && (
                <>
                  <span style={{
                    display: 'flex', alignItems: 'center', gap: '0.4rem',
                    fontSize: 12, color: 'var(--text-muted)',
                    paddingRight: '0.75rem',
                    borderRight: '1px solid var(--border)',
                    marginRight: '0.25rem',
                  }}>
                    {authUsername && <span style={{ color: 'var(--text)' }}>{authUsername}</span>}
                    <button onClick={handleLogout} style={{
                      background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                      color: 'var(--text-muted)', fontSize: 12, lineHeight: 1,
                    }}>
                      sign out
                    </button>
                  </span>
                </>
              )}
              <NavLink to="/" end className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>
                Import
              </NavLink>
              <NavLink to="/history" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>
                History
              </NavLink>
              <NavLink to="/discover" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>
                Discover
              </NavLink>
              <NavLink to="/settings" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>
                Settings
              </NavLink>
            </nav>
          </div>
        </header>
        <main className="main">
          <Routes>
            <Route path="/" element={<Import />} />
            <Route path="/history" element={<History />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
        <footer className="footer">
          <span>Digarr — the crates don't fill themselves</span>
        </footer>
      </div>
    </BrowserRouter>
  );
}

export default App;

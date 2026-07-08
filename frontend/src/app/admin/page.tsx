'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Server, Database, Wifi, Activity, AlertTriangle, CheckCircle2,
  XCircle, Clock, Download, RefreshCw, Trash2, LogOut, Lock,
  ChevronDown, ChevronUp, AlertCircle, Info, Shield,
} from 'lucide-react';
const API = '/api/v1/admin';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ServiceStatus {
  status: 'up' | 'down' | 'warning' | 'degraded';
  detail: string;
}

interface Services {
  database: ServiceStatus;
  redis: ServiceStatus;
  delta: ServiceStatus;
  kite: ServiceStatus;
  scheduler: ServiceStatus;
}

interface DbTable {
  name: string;
  rows: number;
  size: string;
}

interface DbStats {
  total_db_size: string;
  tables: DbTable[];
}

interface DiskEntry {
  size: string;
  size_bytes: number;
  reclaimable?: string;
  reclaimable_bytes?: number;
  count: number;
}

interface DiskImage {
  repo_tags: string;
  containers: number;
  size: string;
  size_bytes: number;
  shared_size: string;
  unique_size: string;
  created?: string | null;
}

interface DiskVolume {
  name: string;
  driver: string;
  ref_count: number;
  size: string;
  mountpoint: string;
}

interface DiskCache {
  id: string;
  type: string;
  description: string;
  in_use: boolean;
  shared: boolean;
  size: string;
  usage_count: number;
}

interface DiskStats {
  host_filesystems: Array<{
    path: string;
    total: string;
    used: string;
    free: string;
    used_percent: number;
  } | null>;
  layers_size: string;
  images: DiskEntry;
  containers: DiskEntry;
  volumes: DiskEntry;
  build_cache: DiskEntry;
  top_images: DiskImage[];
  top_volumes: DiskVolume[];
  top_build_cache: DiskCache[];
}

interface ErrorEntry {
  id: number;
  level: 'ERROR' | 'WARNING';
  service: string;
  logger: string;
  message: string;
  detail: string | null;
  captured_at: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function authHeader(token: string) {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

function StatusBadge({ status }: { status: string }) {
  const cfg: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
    up:       { color: 'text-emerald-400 bg-emerald-400/10 border-emerald-500/30', icon: <CheckCircle2 size={13} />, label: 'UP' },
    down:     { color: 'text-red-400 bg-red-400/10 border-red-500/30',             icon: <XCircle size={13} />,       label: 'DOWN' },
    warning:  { color: 'text-amber-400 bg-amber-400/10 border-amber-500/30',       icon: <AlertTriangle size={13} />, label: 'WARN' },
    degraded: { color: 'text-orange-400 bg-orange-400/10 border-orange-500/30',    icon: <AlertCircle size={13} />,   label: 'DEGRADED' },
  };
  const c = cfg[status] ?? cfg.warning;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-semibold ${c.color}`}>
      {c.icon}{c.label}
    </span>
  );
}

function LevelBadge({ level }: { level: string }) {
  return level === 'ERROR'
    ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold bg-red-500/15 text-red-400 border border-red-500/30"><XCircle size={11} />ERROR</span>
    : <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold bg-amber-500/15 text-amber-400 border border-amber-500/30"><AlertTriangle size={11} />WARN</span>;
}

function fmt(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) + ' ' +
         d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

// ── Login page ────────────────────────────────────────────────────────────────

function LoginPage({ onLogin }: { onLogin: (tok: string) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true); setError('');
    try {
      const res = await fetch(`${API}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) { setError('Invalid credentials'); return; }
      const { access_token } = await res.json();
      sessionStorage.setItem('admin_token', access_token);
      onLogin(access_token);
    } catch {
      setError('Cannot reach server');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-slate-800 border border-slate-700 mb-4">
            <Shield className="text-emerald-400" size={28} />
          </div>
          <h1 className="text-2xl font-bold text-slate-100">Admin Console</h1>
          <p className="text-slate-500 text-sm mt-1">NSE Platform</p>
        </div>

        <form onSubmit={submit} className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
          {error && (
            <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              <XCircle size={15} />{error}
            </div>
          )}
          <div>
            <label className="block text-xs text-slate-500 mb-1 font-medium">Email</label>
            <input
              type="text" autoComplete="username" value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-slate-100 text-sm focus:outline-none focus:border-emerald-500"
              placeholder="admin@admin"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1 font-medium">Password</label>
            <input
              type="password" autoComplete="current-password" value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-slate-100 text-sm focus:outline-none focus:border-emerald-500"
              placeholder="••••••••"
            />
          </div>
          <button
            type="submit" disabled={loading}
            className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-sm font-semibold text-white transition-colors"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Main dashboard ────────────────────────────────────────────────────────────

const SERVICE_META: Record<string, { label: string; icon: React.ReactNode }> = {
  database:  { label: 'PostgreSQL',       icon: <Database size={18} /> },
  redis:     { label: 'Redis Cache',      icon: <Server size={18} /> },
  delta:     { label: 'Delta (Java)',     icon: <Activity size={18} /> },
  kite:      { label: 'Kite WebSocket',   icon: <Wifi size={18} /> },
  scheduler: { label: 'APScheduler',      icon: <Clock size={18} /> },
};

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(null);
  const [services, setServices] = useState<Services | null>(null);
  const [dbStats, setDbStats] = useState<DbStats | null>(null);
  const [diskStats, setDiskStats] = useState<DiskStats | null>(null);
  const [errors, setErrors] = useState<ErrorEntry[]>([]);
  const [levelFilter, setLevelFilter] = useState<'ALL' | 'ERROR' | 'WARNING'>('ALL');
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState<'services' | 'db' | 'disk' | 'errors' | 'logs'>('services');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  // Logs tab state
  const [logs, setLogs] = useState<any[]>([]);
  const [logStats, setLogStats] = useState<any[]>([]);
  const [logSource, setLogSource] = useState('all');
  const [logLevel, setLogLevel] = useState('ALL');
  const [logSearch, setLogSearch] = useState('');
  const [logSearchInput, setLogSearchInput] = useState('');
  const [logsLoading, setLogsLoading] = useState(false);

  // Restore session token on mount
  useEffect(() => {
    const saved = sessionStorage.getItem('admin_token');
    if (saved) setToken(saved);
  }, []);

  const fetchAll = useCallback(async (tok: string) => {
    setRefreshing(true);
    try {
      const h = authHeader(tok);
      const [svcRes, dbRes, diskRes, errRes, statsRes] = await Promise.all([
        fetch(`${API}/services`, { headers: h }),
        fetch(`${API}/db`, { headers: h }),
        fetch(`${API}/disk`, { headers: h }),
        fetch(`${API}/errors?limit=300`, { headers: h }),
        fetch(`${API}/logs/stats`, { headers: h }),
      ]);
      if (svcRes.status === 401 || dbRes.status === 401 || diskRes.status === 401) { logout(); return; }
      if (svcRes.ok)   setServices(await svcRes.json());
      if (dbRes.ok)    setDbStats(await dbRes.json());
      if (diskRes.ok)  setDiskStats(await diskRes.json());
      if (errRes.ok)   setErrors(await errRes.json());
      if (statsRes.ok) setLogStats(await statsRes.json());
      setLastRefresh(new Date());
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (token) fetchAll(token);
  }, [token, fetchAll]);

  // Auto-refresh every 30s
  useEffect(() => {
    if (!token) return;
    const id = setInterval(() => fetchAll(token), 30_000);
    return () => clearInterval(id);
  }, [token, fetchAll]);

  function logout() {
    sessionStorage.removeItem('admin_token');
    setToken(null);
  }

  const fetchLogs = useCallback(async (tok: string, src: string, lvl: string, q: string) => {
    setLogsLoading(true);
    try {
      const params = new URLSearchParams({ limit: '500' });
      if (src !== 'all') params.set('source', src);
      if (lvl !== 'ALL') params.set('level', lvl);
      if (q)             params.set('search', q);
      const res = await fetch(`${API}/logs?${params}`, { headers: authHeader(tok) });
      if (res.ok) setLogs(await res.json());
    } finally {
      setLogsLoading(false);
    }
  }, []);

  // Fetch logs whenever tab/filter changes
  useEffect(() => {
    if (token && tab === 'logs') fetchLogs(token, logSource, logLevel, logSearch);
  }, [token, tab, logSource, logLevel, logSearch, fetchLogs]);

  async function clearErrors() {
    if (!token || !confirm('Clear all error logs?')) return;
    await fetch(`${API}/errors`, { method: 'DELETE', headers: authHeader(token) });
    setErrors([]);
  }

  function downloadErrors() {
    if (!token) return;
    const a = document.createElement('a');
    a.href = `${API}/errors/download`;
    // Append auth header via fetch + blob approach
    fetch(`${API}/errors/download`, { headers: authHeader(token) })
      .then(r => r.blob())
      .then(blob => {
        a.href = URL.createObjectURL(blob);
        a.download = `errors_${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
      });
  }

  function downloadLogs() {
    if (!token) return;
    fetch(`${API}/logs/download`, { headers: authHeader(token) })
      .then(r => r.blob())
      .then(blob => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'nse_backend.log';
        a.click();
      });
  }

  if (!token) return <LoginPage onLogin={setToken} />;

  const filtered = errors.filter(e => levelFilter === 'ALL' || e.level === levelFilter);
  const errorCount = errors.filter(e => e.level === 'ERROR').length;
  const warnCount  = errors.filter(e => e.level === 'WARNING').length;
  const upCount    = services ? Object.values(services).filter(s => s.status === 'up').length : 0;
  const totalSvc   = services ? Object.keys(services).length : 0;

  // Log stats helper
  const logTotal = logStats.reduce((a, r) => a + r.count, 0);
  const logErrCnt = logStats.filter(r => r.level === 'ERROR').reduce((a, r) => a + r.count, 0);
  const SOURCES = ['all', 'backend', 'delta', 'db', 'redis', 'frontend', 'system'];
  const SOURCE_COLORS: Record<string, string> = {
    backend: 'text-emerald-400', delta: 'text-sky-400', db: 'text-violet-400',
    redis: 'text-red-400', frontend: 'text-amber-400', system: 'text-orange-400',
  };
  const LEVEL_COLORS: Record<string, string> = {
    ERROR: 'text-red-400 bg-red-500/10 border-red-500/30',
    WARNING: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
    INFO: 'text-sky-400 bg-sky-500/10 border-sky-500/30',
    DEBUG: 'text-slate-500 bg-slate-700/20 border-slate-600/30',
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">

      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Shield className="text-emerald-400" size={20} />
            <span className="font-bold text-slate-100">NSE Admin</span>
            <span className="text-slate-600">|</span>
            <span className="text-xs text-slate-500">System Dashboard</span>
          </div>
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <span className="text-xs text-slate-600 hidden sm:block">
                Refreshed {fmt(lastRefresh.toISOString())}
              </span>
            )}
            <button onClick={() => fetchAll(token)} disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs text-slate-300 transition-colors">
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />Refresh
            </button>
            <button onClick={logout}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs text-slate-300 transition-colors">
              <LogOut size={12} />Logout
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">

        {/* Summary cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Services Up', value: `${upCount}/${totalSvc}`, sub: 'running', color: upCount === totalSvc ? 'text-emerald-400' : 'text-amber-400', icon: <Server size={16}/> },
            { label: 'DB Size',     value: dbStats?.total_db_size ?? '…', sub: 'PostgreSQL', color: 'text-sky-400', icon: <Database size={16}/> },
            { label: 'Errors',      value: errorCount, sub: 'captured', color: errorCount > 0 ? 'text-red-400' : 'text-emerald-400', icon: <XCircle size={16}/> },
            { label: 'Log Entries', value: logTotal > 0 ? logTotal.toLocaleString() : '…', sub: '24h · ' + (logErrCnt > 0 ? `${logErrCnt} errors` : 'clean'), color: logErrCnt > 0 ? 'text-red-400' : 'text-emerald-400', icon: <Info size={16}/> },
          ].map(c => (
            <div key={c.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-slate-500">{c.label}</span>
                <span className={c.color}>{c.icon}</span>
              </div>
              <div className={`text-2xl font-bold ${c.color}`}>{String(c.value)}</div>
              <div className="text-xs text-slate-600 mt-0.5">{c.sub}</div>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div className="flex gap-1 bg-slate-900/50 border border-slate-800 rounded-xl p-1 w-fit">
          {(['services', 'db', 'disk', 'errors', 'logs'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors capitalize ${
                tab === t ? 'bg-slate-800 text-slate-100' : 'text-slate-500 hover:text-slate-300'
              }`}>
              {t === 'services' ? 'Services' : t === 'db' ? 'Database' : t === 'disk' ? 'Disk' : t === 'errors' ? `Errors (${errors.length})` : 'Logs'}
            </button>
          ))}
        </div>

        {/* ── Services tab ── */}
        {tab === 'services' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {services && Object.entries(services).map(([key, svc]) => {
              const meta = SERVICE_META[key] ?? { label: key, icon: <Server size={18}/> };
              return (
                <div key={key} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2 text-slate-300">
                      <span className="text-slate-500">{meta.icon}</span>
                      <span className="font-semibold text-sm">{meta.label}</span>
                    </div>
                    <StatusBadge status={svc.status} />
                  </div>
                  <p className="text-xs text-slate-500 leading-relaxed break-all">{svc.detail}</p>
                </div>
              );
            })}
            <div className="sm:col-span-2 lg:col-span-3 bg-slate-900 border border-slate-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-semibold text-slate-300">Log Download</span>
                <Download size={15} className="text-slate-500" />
              </div>
              <button onClick={downloadLogs}
                className="flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm text-slate-300 transition-colors">
                <Download size={14} />Download nse_backend.log
              </button>
            </div>
          </div>
        )}

        {/* ── Database tab ── */}
        {tab === 'db' && dbStats && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
              <span className="text-sm font-semibold text-slate-200">Table Statistics</span>
              <span className="text-xs text-slate-500">Total: <span className="text-emerald-400 font-semibold">{dbStats.total_db_size}</span></span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800">
                    <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Table</th>
                    <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Rows</th>
                    <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Size</th>
                  </tr>
                </thead>
                <tbody>
                  {dbStats.tables.map(t => (
                    <tr key={t.name} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                      <td className="px-5 py-2.5 font-mono text-xs text-slate-300">{t.name}</td>
                      <td className="px-5 py-2.5 text-right text-slate-400">{t.rows.toLocaleString()}</td>
                      <td className="px-5 py-2.5 text-right text-slate-500">{t.size}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── Disk tab ── */}
        {tab === 'disk' && diskStats && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: 'Docker Layers', value: diskStats.layers_size, sub: 'all image layers', color: 'text-sky-400' },
                { label: 'Images', value: diskStats.images.size, sub: `${diskStats.images.count} images · ${diskStats.images.reclaimable ?? '0 B'} reclaimable`, color: 'text-emerald-400' },
                { label: 'Volumes', value: diskStats.volumes.size, sub: `${diskStats.volumes.count} volumes · ${diskStats.volumes.reclaimable ?? '0 B'} reclaimable`, color: 'text-violet-400' },
                { label: 'Build Cache', value: diskStats.build_cache.size, sub: `${diskStats.build_cache.count} records · ${diskStats.build_cache.reclaimable ?? '0 B'} reclaimable`, color: 'text-amber-400' },
              ].map(card => (
                <div key={card.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <div className="text-xs text-slate-500">{card.label}</div>
                  <div className={`mt-1 text-2xl font-bold ${card.color}`}>{card.value}</div>
                  <div className="mt-1 text-xs text-slate-600">{card.sub}</div>
                </div>
              ))}
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-200">Host Filesystems</span>
                <span className="text-xs text-slate-500">Overall disk usage on the server</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Path</th>
                      <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Used</th>
                      <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Free</th>
                      <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Total</th>
                      <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Used %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diskStats.host_filesystems.filter(Boolean).map(fs => (
                      <tr key={fs!.path} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                        <td className="px-5 py-2.5 text-xs text-slate-300 font-mono break-all">{fs!.path}</td>
                        <td className="px-5 py-2.5 text-right text-slate-400">{fs!.used}</td>
                        <td className="px-5 py-2.5 text-right text-slate-500">{fs!.free}</td>
                        <td className="px-5 py-2.5 text-right text-slate-400">{fs!.total}</td>
                        <td className="px-5 py-2.5 text-right text-slate-400">{fs!.used_percent}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-sm font-semibold text-slate-200">Largest Images</span>
                  <span className="text-xs text-slate-500">Top 10 by size</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-800">
                        <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Image</th>
                        <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Containers</th>
                        <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Size</th>
                        <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Unique</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diskStats.top_images.map(img => (
                        <tr key={img.repo_tags} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                          <td className="px-5 py-2.5 text-xs text-slate-300 font-mono break-all">
                            <div>{img.repo_tags}</div>
                            <div className="mt-1 text-[11px] text-slate-500">Created: {img.created ? fmt(img.created) : '—'}</div>
                          </td>
                          <td className="px-5 py-2.5 text-right text-slate-400">{img.containers}</td>
                          <td className="px-5 py-2.5 text-right text-slate-400">{img.size}</td>
                          <td className="px-5 py-2.5 text-right text-slate-500">{img.unique_size}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-sm font-semibold text-slate-200">Build Cache</span>
                  <span className="text-xs text-slate-500">Top 10 by size</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-800">
                        <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">ID</th>
                        <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Type</th>
                        <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Size</th>
                        <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Uses</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diskStats.top_build_cache.map(item => (
                        <tr key={item.id} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                          <td className="px-5 py-2.5 text-xs text-slate-300 font-mono break-all">{item.id}</td>
                          <td className="px-5 py-2.5 text-xs text-slate-400">{item.type}{item.in_use ? ' · in use' : ''}</td>
                          <td className="px-5 py-2.5 text-right text-slate-400">{item.size}</td>
                          <td className="px-5 py-2.5 text-right text-slate-500">{item.usage_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-200">Volumes</span>
                <span className="text-xs text-slate-500">Mounted data and caches</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Name</th>
                      <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Driver</th>
                      <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Refs</th>
                      <th className="text-right px-5 py-2.5 text-xs text-slate-500 font-medium">Size</th>
                      <th className="text-left px-5 py-2.5 text-xs text-slate-500 font-medium">Mountpoint</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diskStats.top_volumes.map(vol => (
                      <tr key={vol.name} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                        <td className="px-5 py-2.5 text-xs text-slate-300 font-mono break-all">{vol.name}</td>
                        <td className="px-5 py-2.5 text-xs text-slate-400">{vol.driver}</td>
                        <td className="px-5 py-2.5 text-right text-slate-400">{vol.ref_count}</td>
                        <td className="px-5 py-2.5 text-right text-slate-400">{vol.size}</td>
                        <td className="px-5 py-2.5 text-xs text-slate-500 font-mono break-all">{vol.mountpoint}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ── Errors tab ── */}
        {tab === 'errors' && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {(['ALL', 'ERROR', 'WARNING'] as const).map(l => (
                <button key={l} onClick={() => setLevelFilter(l)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                    levelFilter === l
                      ? 'bg-emerald-600/20 border-emerald-500/50 text-emerald-400'
                      : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
                  }`}>
                  {l}{l === 'ERROR' ? ` (${errorCount})` : l === 'WARNING' ? ` (${warnCount})` : ` (${errors.length})`}
                </button>
              ))}
              <div className="ml-auto flex gap-2">
                <button onClick={downloadErrors}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs text-slate-300 transition-colors">
                  <Download size={12} />CSV
                </button>
                <button onClick={clearErrors}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-red-900/20 hover:bg-red-900/30 border border-red-800/40 rounded-lg text-xs text-red-400 transition-colors">
                  <Trash2 size={12} />Clear all
                </button>
              </div>
            </div>

            {filtered.length === 0 ? (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-12 text-center">
                <CheckCircle2 className="mx-auto text-emerald-500 mb-2" size={32} />
                <p className="text-slate-400">No {levelFilter === 'ALL' ? '' : levelFilter.toLowerCase() + ' '}entries</p>
              </div>
            ) : (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className="text-left px-4 py-3 text-xs text-slate-500 font-medium w-28">Time</th>
                      <th className="text-left px-4 py-3 text-xs text-slate-500 font-medium w-20">Level</th>
                      <th className="text-left px-4 py-3 text-xs text-slate-500 font-medium w-28">Service</th>
                      <th className="text-left px-4 py-3 text-xs text-slate-500 font-medium">Message</th>
                      <th className="px-4 py-3 w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(e => (
                      <React.Fragment key={e.id}>
                        <tr
                          className={`border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors cursor-pointer ${e.detail ? '' : 'cursor-default'}`}
                          onClick={() => e.detail && setExpandedRow(expandedRow === e.id ? null : e.id)}
                        >
                          <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">{fmt(e.captured_at)}</td>
                          <td className="px-4 py-2.5"><LevelBadge level={e.level} /></td>
                          <td className="px-4 py-2.5 text-xs text-slate-400 font-mono">{e.service ?? '—'}</td>
                          <td className="px-4 py-2.5 text-xs text-slate-300 max-w-0 truncate">{e.message}</td>
                          <td className="px-4 py-2.5 text-slate-600">
                            {e.detail && (expandedRow === e.id ? <ChevronUp size={13}/> : <ChevronDown size={13}/>)}
                          </td>
                        </tr>
                        {expandedRow === e.id && e.detail && (
                          <tr className="border-b border-slate-800/50 bg-slate-800/20">
                            <td colSpan={5} className="px-4 py-3">
                              <pre className="text-xs text-slate-400 bg-slate-950 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all max-h-48">
                                {e.detail}
                              </pre>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
        {/* ── Logs tab ── */}
        {tab === 'logs' && (
          <div className="space-y-3">

            {/* Source + level + search bar */}
            <div className="flex flex-wrap gap-2 items-center">
              {/* Source pills */}
              <div className="flex flex-wrap gap-1">
                {SOURCES.map(s => (
                  <button key={s} onClick={() => setLogSource(s)}
                    className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors ${
                      logSource === s
                        ? 'bg-slate-700 border-slate-500 text-slate-100'
                        : 'bg-slate-800/50 border-slate-700 text-slate-500 hover:text-slate-300'
                    }`}>
                    <span className={s !== 'all' ? SOURCE_COLORS[s] : 'text-slate-400'}>{s}</span>
                  </button>
                ))}
              </div>

              {/* Level filter */}
              <div className="flex gap-1">
                {['ALL','INFO','WARNING','ERROR'].map(l => (
                  <button key={l} onClick={() => setLogLevel(l)}
                    className={`px-2.5 py-1 rounded-lg text-xs font-semibold border transition-colors ${
                      logLevel === l
                        ? 'bg-emerald-600/20 border-emerald-500/50 text-emerald-400'
                        : 'bg-slate-800 border-slate-700 text-slate-500 hover:text-slate-300'
                    }`}>{l}</button>
                ))}
              </div>

              {/* Search */}
              <form className="flex gap-1 ml-auto" onSubmit={e => { e.preventDefault(); setLogSearch(logSearchInput); }}>
                <input value={logSearchInput} onChange={e => setLogSearchInput(e.target.value)}
                  placeholder="Search messages…"
                  className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-emerald-500 w-48" />
                <button type="submit" className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 border border-slate-600 rounded-lg text-xs text-slate-300 transition-colors">Go</button>
                {logSearch && <button type="button" onClick={() => { setLogSearch(''); setLogSearchInput(''); }} className="px-2 py-1.5 text-slate-500 hover:text-slate-300 text-xs">✕</button>}
              </form>

              {/* Download */}
              <button onClick={() => {
                fetch(`${API}/logs/download/csv${logSource !== 'all' ? `?source=${logSource}` : ''}`, { headers: authHeader(token!) })
                  .then(r => r.blob()).then(blob => {
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = `logs_${logSource}_${new Date().toISOString().slice(0,10)}.csv`;
                    a.click();
                  });
              }} className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs text-slate-300 transition-colors">
                <Download size={12} />CSV
              </button>
            </div>

            {/* 24h source breakdown */}
            {logStats.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {SOURCES.filter(s => s !== 'all').map(src => {
                  const srcStats = logStats.filter(r => r.source === src);
                  if (!srcStats.length) return null;
                  const total = srcStats.reduce((a, r) => a + r.count, 0);
                  const errs  = srcStats.find(r => r.level === 'ERROR')?.count ?? 0;
                  return (
                    <button key={src} onClick={() => setLogSource(src)}
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs transition-colors ${
                        errs > 0 ? 'border-red-500/30 bg-red-500/5' : 'border-slate-700 bg-slate-900'
                      }`}>
                      <span className={`font-semibold ${SOURCE_COLORS[src] ?? 'text-slate-400'}`}>{src}</span>
                      <span className="text-slate-500">{total.toLocaleString()}</span>
                      {errs > 0 && <span className="text-red-400 font-bold">{errs}e</span>}
                    </button>
                  );
                })}
              </div>
            )}

            {/* Log table */}
            {logsLoading ? (
              <div className="text-center py-12 text-slate-500 text-sm">
                <RefreshCw size={18} className="animate-spin mx-auto mb-2" />Loading logs…
              </div>
            ) : logs.length === 0 ? (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-12 text-center">
                <CheckCircle2 className="mx-auto text-emerald-500 mb-2" size={32} />
                <p className="text-slate-400">No log entries match your filters</p>
              </div>
            ) : (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between">
                  <span className="text-xs text-slate-500">{logs.length.toLocaleString()} entries (newest first)</span>
                </div>
                <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-slate-900 z-10">
                      <tr className="border-b border-slate-800">
                        <th className="text-left px-3 py-2 text-slate-500 font-medium w-36">Time</th>
                        <th className="text-left px-3 py-2 text-slate-500 font-medium w-20">Source</th>
                        <th className="text-left px-3 py-2 text-slate-500 font-medium w-16">Level</th>
                        <th className="text-left px-3 py-2 text-slate-500 font-medium">Message</th>
                      </tr>
                    </thead>
                    <tbody>
                      {logs.map(l => (
                        <tr key={l.id} className="border-b border-slate-800/40 hover:bg-slate-800/20 transition-colors">
                          <td className="px-3 py-1.5 text-slate-600 whitespace-nowrap font-mono">{fmt(l.captured_at)}</td>
                          <td className="px-3 py-1.5">
                            <span className={`font-semibold ${SOURCE_COLORS[l.source] ?? 'text-slate-400'}`}>{l.source}</span>
                          </td>
                          <td className="px-3 py-1.5">
                            <span className={`inline-block px-1.5 py-0.5 rounded border text-2xs font-bold ${LEVEL_COLORS[l.level] ?? 'text-slate-400 border-slate-700'}`}>
                              {l.level.slice(0, 4)}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-slate-300 max-w-0">
                            <span className="block truncate font-mono text-2xs leading-relaxed">{l.message}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

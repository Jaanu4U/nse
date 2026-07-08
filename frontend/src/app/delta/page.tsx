'use client';

import React, { useEffect, useState, useRef, useCallback } from 'react';
import Link from 'next/link';
import { ArrowLeft, RefreshCw, Activity, Bell, BellOff, Zap, TrendingUp, TrendingDown, Shield, BarChart3 } from 'lucide-react';

const DELTA_API    = process.env.NEXT_PUBLIC_DELTA_API_URL    || '/delta-api';
const DELTA_STREAM = process.env.NEXT_PUBLIC_DELTA_STREAM_URL || '/delta-stream';

interface Stock {
  symbol: string; ltp: number; open: number; high: number; low: number;
  vwap: number; volume: number; buyVolume: number; sellVolume: number;
  delta: number; cumulativeDelta: number; deltaStrength: number; volumeRatio: number; deltaPercent: number;
  obi: number; rsi: number;
  ema20: number; ema50: number; atr14: number;
  macdLine: number; macdSignal: number; macdHist: number;
  bbUpper: number; bbLower: number; bbBandwidth: number;
  superTrendDir: number; predScore: number; bullishProb: number; signal: string;
  // Level 2 / 3 enriched
  historicalUpProb: number; mlUpProb: number;
  expectedMove: number; expectedTarget: number; absorption: boolean;
  ts: string;
}

interface AlertRule { id: number; symbol: string; type: string; threshold: number; once: boolean; active: boolean; }
interface AlertFired { id: number; ruleId: number; symbol: string; type: string; triggeredValue: number; threshold: number; firedAt: string; }

const ALERT_TYPES = [
  'PRICE_ABOVE','PRICE_BELOW','DELTA_SPIKE_BUY','DELTA_SPIKE_SELL',
  'VOLUME_SPIKE','RSI_OVERBOUGHT','RSI_OVERSOLD',
  'MACD_CROSSOVER_BULLISH','MACD_CROSSOVER_BEARISH',
  'BB_BREAKOUT_UP','BB_BREAKOUT_DOWN','BB_SQUEEZE',
  'SUPERTREND_BUY','SUPERTREND_SELL'
];

export default function DeltaPage() {
  const [stocks, setStocks]     = useState<Stock[]>([]);
  const [alerts, setAlerts]     = useState<AlertFired[]>([]);
  const [rules, setRules]       = useState<AlertRule[]>([]);
  const [connected, setConnected] = useState(false);
  const [tab, setTab]           = useState<'live'|'indicators'|'alerts'|'paper'>('live');
  const [paperTrades, setPaperTrades] = useState<any[]>([]);
  const [paperDaily, setPaperDaily]   = useState<any[]>([]);
  const [paperStats, setPaperStats]   = useState<any>({});
  const [paperDate, setPaperDate]     = useState('today');
  const [selectedTradeId, setSelectedTradeId] = useState<number|null>(null);
  const [search, setSearch]     = useState('');
  const [sortBy, setSortBy]     = useState<keyof Stock>('predScore');
  const [sortDesc, setSortDesc] = useState(true);
  const [newRule, setNewRule]   = useState({ symbol:'', type:'PRICE_ABOVE', threshold:'', once: false });
  const [svcError, setSvcError] = useState('');
  const esRef = useRef<EventSource | null>(null);

  // ---- SSE Connection ----
  const connectSSE = useCallback(() => {
    if (esRef.current) esRef.current.close();
    const es = new EventSource(`${DELTA_STREAM}/market`);
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setSvcError('');
    };

    es.addEventListener('market', (e) => {
      try {
        const data: Stock[] = JSON.parse(e.data);
        setStocks(data);
        setSvcError('');
      } catch {}
    });

    es.addEventListener('alert', (e) => {
      try {
        const alert: AlertFired = JSON.parse(e.data);
        setAlerts(prev => [alert, ...prev].slice(0, 100));
      } catch {}
    });

    es.onerror = () => {
      setConnected(false);
      setSvcError('Java delta service not reachable on port 8090. Start: ./gradlew bootRun');
    };
  }, []);

  useEffect(() => {
    connectSSE();
    fetch(`${DELTA_API}/alerts/rules`).then(r => r.ok ? r.json() : []).then(setRules).catch(() => {});
    fetch(`${DELTA_API}/alerts/fired?hours=8`).then(r => r.ok ? r.json() : []).then(setAlerts).catch(() => {});
    // Load paper trading data
    const loadPaper = () => {
      fetch(`${DELTA_API}/paper/trades?date=today`).then(r => r.ok ? r.json() : []).then(setPaperTrades).catch(() => {});
      fetch(`${DELTA_API}/paper/trades/daily`).then(r => r.ok ? r.json() : []).then(setPaperDaily).catch(() => {});
      fetch(`${DELTA_API}/paper/trades/stats`).then(r => r.ok ? r.json() : {}).then(setPaperStats).catch(() => {});
    };
    loadPaper();
    const paperInterval = setInterval(loadPaper, 60000); // refresh every minute
    return () => { esRef.current?.close(); clearInterval(paperInterval); };
  }, [connectSSE]);

  const addRule = async () => {
    if (!newRule.symbol || !newRule.threshold) return;
    const res = await fetch(`${DELTA_API}/alerts/rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: newRule.symbol.toUpperCase(), type: newRule.type, threshold: parseFloat(newRule.threshold), once: newRule.once })
    });
    if (res.ok) {
      const r = await res.json();
      setRules(prev => [...prev, r]);
      setNewRule({ symbol:'', type:'PRICE_ABOVE', threshold:'', once: false });
    }
  };

  const deleteRule = async (id: number) => {
    await fetch(`${DELTA_API}/alerts/rules/${id}`, { method: 'DELETE' });
    setRules(prev => prev.filter(r => r.id !== id));
  };

  const filtered = stocks
    .filter(s => !search || s.symbol.includes(search.toUpperCase()))
    .sort((a, b) => sortDesc ? (b[sortBy] as number) - (a[sortBy] as number) : (a[sortBy] as number) - (b[sortBy] as number));

  const col = (key: keyof Stock) => () => { if (sortBy === key) setSortDesc(d => !d); else { setSortBy(key); setSortDesc(true); } };
  const th = (label: string, key: keyof Stock, cls = '') => (
    <th onClick={col(key)} className={`p-2 text-right cursor-pointer hover:text-amber-400 font-semibold select-none ${sortBy === key ? 'text-amber-400' : 'text-slate-500'} ${cls}`}>
      {label}{sortBy === key ? (sortDesc ? ' ↓' : ' ↑') : ''}
    </th>
  );

  const scoreColor = (s: number) => s >= 65 ? 'text-emerald-400' : s <= 35 ? 'text-red-400' : 'text-amber-400';
  const stLabel = (d: number) => d === 1 ? <span className="text-emerald-400 font-bold">▲ BUY</span> : d === -1 ? <span className="text-red-400 font-bold">▼ SELL</span> : <span className="text-slate-500">—</span>;
  const macdColor = (h: number) => h > 0 ? 'text-emerald-400' : h < 0 ? 'text-red-400' : 'text-slate-500';
  const fmt = (v: number, d = 2) => isNaN(v) || v === 0 ? '—' : v.toFixed(d);

  return (
    <main className="min-h-screen bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black text-slate-100 flex flex-col">

      {/* HEADER */}
      <header className="sticky top-0 z-20 bg-slate-950/90 backdrop-blur border-b border-slate-800/60 px-4 py-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span className="w-9 h-9 flex items-center justify-center rounded-xl bg-violet-500/15 border border-violet-500/30 flex-shrink-0">
            <Activity className="text-violet-400 w-4 h-4" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-extrabold">DELTA SYSTEM</h1>
              <span className={`flex items-center gap-1 text-3xs font-bold px-1.5 py-0.5 rounded ${connected ? 'bg-emerald-950/60 text-emerald-400' : 'bg-red-950/60 text-red-400'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
                {connected ? 'LIVE' : 'OFFLINE'}
              </span>
              <span className="text-3xs text-slate-500">{stocks.length} symbols</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search symbol…"
            className="bg-slate-900 border border-slate-700 rounded-lg text-xs text-slate-300 px-2 py-1 w-28 focus:outline-none focus:border-violet-500" />
          <button onClick={connectSSE} className="text-slate-400 hover:text-violet-400"><RefreshCw className={`w-4 h-4 ${!connected ? 'animate-spin text-red-400' : ''}`} /></button>
          <Link href="/" className="flex items-center gap-1 text-3xs text-slate-400 hover:text-amber-400 bg-slate-900 border border-slate-800 px-2 py-1.5 rounded-lg transition-all">
            <ArrowLeft className="w-3 h-3" /> Back
          </Link>
        </div>
      </header>

      {svcError && (
        <div className="mx-4 mt-3 bg-red-950/30 border border-red-800/50 rounded-xl p-3 text-3xs text-red-400">
          ⚠ {svcError}
        </div>
      )}

      {/* TABS */}
      <div className="flex items-center gap-2 px-4 pt-3 pb-0">
        {[['live','⚡ Delta & Price'],['indicators','📈 Indicators'],['alerts','🔔 Alerts'],['paper','📋 Paper Trades']].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key as any)}
            className={`text-xs font-semibold px-4 py-1.5 rounded-lg border transition-all ${
              tab === key ? 'bg-violet-950/40 border-violet-600/50 text-violet-300' : 'border-slate-800 text-slate-500 hover:text-slate-300'
            }`}>{label}
            {key === 'alerts' && alerts.length > 0 && (
              <span className="ml-1 bg-red-500 text-white text-3xs rounded-full px-1">{alerts.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* ---- LIVE TAB ---- */}
      {tab === 'live' && (
        <div className="flex-1 overflow-auto p-4">
          <table className="w-full text-3xs min-w-[1100px]">
            <thead className="sticky top-0 bg-slate-950/90">
              <tr>
                <th className="text-left p-2 text-slate-500 font-semibold">Symbol</th>
                {th('LTP','ltp','w-20')}
                {th('VWAP','vwap','w-20')}
                {th('ΔStrength%','deltaStrength','w-24')}
                {th('Vol×','volumeRatio','w-16')}
                {th('Delta','delta','w-20')}
                {th('Cum Δ','cumulativeDelta','w-20')}
                {th('H.Prob%','historicalUpProb','w-20')}
                {th('ML Prob%','mlUpProb','w-20')}
                {th('Exp.Move%','expectedMove','w-24')}
                {th('Target','expectedTarget','w-20')}
                {th('Score','predScore','w-16')}
                <th className="p-2 text-right text-slate-500 font-semibold w-20">Signal</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={11} className="text-center py-10 text-slate-500">
                  {connected ? 'Waiting for ticks…' : 'Java delta service offline (port 8090)'}
                </td></tr>
              )}
              {filtered.map(s => {
                const ds = s.deltaStrength ?? 0;
                const vr = s.volumeRatio ?? 1;
                const aboveVwap = s.ltp > s.vwap;
                return (
                  <tr key={s.symbol} className="border-t border-slate-800/40 hover:bg-slate-800/20">
                    <td className="p-2 font-bold text-slate-100">{s.symbol}</td>
                    <td className="p-2 text-right text-slate-100">₹{fmt(s.ltp)}</td>
                    <td className={`p-2 text-right ${aboveVwap ? 'text-emerald-400' : 'text-red-400'}`}>₹{fmt(s.vwap)}</td>
                    {/* Delta Strength — core Level 1 signal */}
                    <td className={`p-2 text-right font-bold text-base ${
                      ds > 10 ? 'text-emerald-300' : ds > 5 ? 'text-emerald-500' :
                      ds > 0 ? 'text-emerald-700' : ds < -10 ? 'text-red-300' :
                      ds < -5 ? 'text-red-500' : ds < 0 ? 'text-red-700' : 'text-slate-500'
                    }`}>{ds >= 0 ? '+' : ''}{ds.toFixed(1)}%</td>
                    {/* Volume Ratio */}
                    <td className={`p-2 text-right ${vr >= 1.5 ? 'text-amber-400' : vr >= 1.0 ? 'text-slate-300' : 'text-slate-600'}`}>{vr.toFixed(1)}×</td>
                    <td className={`p-2 text-right font-bold ${s.delta >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{s.delta >= 0 ? '+' : ''}{(s.delta/1000).toFixed(1)}K</td>
                    <td className={`p-2 text-right font-bold ${s.cumulativeDelta >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{s.cumulativeDelta >= 0 ? '+' : ''}{(s.cumulativeDelta/1000).toFixed(1)}K</td>
                    <td className={`p-2 text-right ${s.obi > 0.1 ? 'text-emerald-400' : s.obi < -0.1 ? 'text-red-400' : 'text-slate-500'}`}>{(s.obi*100).toFixed(1)}%</td>
                    <td className={`p-2 text-right ${s.rsi > 65 ? 'text-amber-400' : s.rsi < 35 ? 'text-sky-400' : 'text-slate-400'}`}>{fmt(s.rsi,1)}</td>
                    {/* Level 2: Historical Up Probability */}
                    <td className={`p-2 text-right font-bold ${
                      s.historicalUpProb > 60 ? 'text-emerald-400' : s.historicalUpProb > 50 ? 'text-emerald-600' :
                      s.historicalUpProb > 0 ? 'text-slate-400' : 'text-slate-600'
                    }`}>{s.historicalUpProb > 0 ? s.historicalUpProb.toFixed(1)+'%' : '—'}</td>
                    {/* Level 3: ML Up Probability */}
                    <td className={`p-2 text-right font-bold ${
                      s.mlUpProb > 0.6 ? 'text-emerald-400' : s.mlUpProb > 0.5 ? 'text-emerald-600' :
                      s.mlUpProb > 0 ? 'text-slate-400' : 'text-slate-600'
                    }`}>{s.mlUpProb > 0 ? (s.mlUpProb*100).toFixed(1)+'%' : '—'}</td>
                    {/* Expected Move */}
                    <td className={`p-2 text-right font-bold ${
                      s.expectedMove > 0.5 ? 'text-emerald-400' : s.expectedMove > 0 ? 'text-emerald-600' :
                      s.expectedMove < -0.5 ? 'text-red-400' : s.expectedMove < 0 ? 'text-red-600' : 'text-slate-500'
                    }`}>
                      {s.expectedMove !== 0 ? (s.expectedMove >= 0 ? '+' : '') + s.expectedMove.toFixed(2)+'%' : '—'}
                      {s.absorption && <span className="ml-1 text-amber-400 text-xs" title="Absorption: positive delta but price not moving">⚠</span>}
                    </td>
                    {/* Expected Target */}
                    <td className="p-2 text-right text-slate-300 text-xs">
                      {s.expectedTarget > 0 ? '₹'+s.expectedTarget.toFixed(1) : '—'}
                    </td>
                    <td className={`p-2 text-right font-extrabold text-sm ${scoreColor(s.predScore)}`}>{fmt(s.predScore,1)}</td>
                    <td className="p-2 text-right">
                      <span className={`text-3xs font-bold px-1.5 py-0.5 rounded ${s.signal === 'BULLISH' ? 'bg-emerald-950/60 text-emerald-400' : s.signal === 'BEARISH' ? 'bg-red-950/60 text-red-400' : 'text-slate-500'}`}>
                        {s.signal}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* ---- INDICATORS TAB ---- */}
      {tab === 'indicators' && (
        <div className="flex-1 overflow-auto p-4">
          <table className="w-full text-3xs min-w-[1000px]">
            <thead className="sticky top-0 bg-slate-950/90">
              <tr>
                <th className="text-left p-2 text-slate-500 font-semibold">Symbol</th>
                {th('LTP','ltp')}
                {th('EMA20','ema20')}
                {th('EMA50','ema50')}
                {th('ATR14','atr14')}
                {th('MACD','macdLine')}
                {th('Signal','macdSignal')}
                {th('Hist','macdHist')}
                {th('BB Upper','bbUpper')}
                {th('BB Lower','bbLower')}
                {th('BB BW%','bbBandwidth')}
                <th className="p-2 text-right text-slate-500 font-semibold">SuperTrend</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(s => (
                <tr key={s.symbol} className="border-t border-slate-800/40 hover:bg-slate-800/20">
                  <td className="p-2 font-bold text-slate-100">{s.symbol}</td>
                  <td className="p-2 text-right text-slate-100">₹{fmt(s.ltp)}</td>
                  <td className={`p-2 text-right ${s.ltp > s.ema20 ? 'text-emerald-400' : 'text-red-400'}`}>₹{fmt(s.ema20)}</td>
                  <td className={`p-2 text-right ${s.ltp > s.ema50 ? 'text-emerald-400' : 'text-red-400'}`}>₹{fmt(s.ema50)}</td>
                  <td className="p-2 text-right text-slate-400">₹{fmt(s.atr14)}</td>
                  <td className={`p-2 text-right ${macdColor(s.macdLine)}`}>{fmt(s.macdLine,4)}</td>
                  <td className="p-2 text-right text-slate-400">{fmt(s.macdSignal,4)}</td>
                  <td className={`p-2 text-right font-bold ${macdColor(s.macdHist)}`}>{fmt(s.macdHist,4)}</td>
                  <td className="p-2 text-right text-slate-400">₹{fmt(s.bbUpper)}</td>
                  <td className="p-2 text-right text-slate-400">₹{fmt(s.bbLower)}</td>
                  <td className="p-2 text-right text-slate-400">{fmt(s.bbBandwidth,2)}%</td>
                  <td className="p-2 text-right">{stLabel(s.superTrendDir)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ---- ALERTS TAB ---- */}
      {tab === 'alerts' && (
        <div className="flex-1 overflow-auto p-4 flex flex-col gap-4">
          {/* Create rule */}
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4">
            <h3 className="text-xs font-bold mb-3 text-violet-300">Create Alert Rule</h3>
            <div className="flex flex-wrap gap-2 items-end">
              <input value={newRule.symbol} onChange={e => setNewRule(r => ({...r, symbol: e.target.value}))}
                placeholder="Symbol e.g. RELIANCE"
                className="bg-slate-950 border border-slate-700 rounded-lg text-xs text-slate-300 px-2 py-1.5 w-36 focus:outline-none focus:border-violet-500" />
              <select value={newRule.type} onChange={e => setNewRule(r => ({...r, type: e.target.value}))}
                className="bg-slate-950 border border-slate-700 rounded-lg text-xs text-slate-300 px-2 py-1.5 focus:outline-none focus:border-violet-500">
                {ALERT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <input value={newRule.threshold} onChange={e => setNewRule(r => ({...r, threshold: e.target.value}))}
                placeholder="Threshold" type="number"
                className="bg-slate-950 border border-slate-700 rounded-lg text-xs text-slate-300 px-2 py-1.5 w-28 focus:outline-none focus:border-violet-500" />
              <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
                <input type="checkbox" checked={newRule.once} onChange={e => setNewRule(r => ({...r, once: e.target.checked}))} className="accent-violet-500" />
                Fire once
              </label>
              <button onClick={addRule}
                className="bg-violet-600 hover:bg-violet-500 text-white text-xs font-bold px-4 py-1.5 rounded-lg transition-all">
                + Add Rule
              </button>
            </div>
            {rules.length > 0 && (
              <div className="mt-3 flex flex-col gap-1">
                <span className="text-3xs text-slate-500 font-semibold">Active Rules ({rules.length})</span>
                {rules.map(r => (
                  <div key={r.id} className="flex items-center gap-2 text-3xs bg-slate-950/60 px-2 py-1 rounded-lg">
                    <span className="text-violet-300 font-bold">{r.symbol}</span>
                    <span className="text-slate-400">{r.type}</span>
                    <span className="text-amber-400">{r.threshold}</span>
                    {r.once && <span className="text-slate-600">(once)</span>}
                    <button onClick={() => deleteRule(r.id)} className="ml-auto text-red-500 hover:text-red-300 text-3xs">✕</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Alert history */}
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 flex-1">
            <h3 className="text-xs font-bold mb-3 text-amber-300">Alert History (last 8h)</h3>
            {alerts.length === 0 ? (
              <p className="text-3xs text-slate-500 text-center py-8">No alerts fired yet. Create rules above to get started.</p>
            ) : (
              <div className="flex flex-col gap-1 max-h-[60vh] overflow-y-auto">
                {alerts.map((a, i) => (
                  <div key={i} className="flex items-center gap-3 text-3xs border border-slate-800 rounded-lg px-2 py-1.5 bg-slate-950/40">
                    <span className={`font-bold w-20 flex-shrink-0 ${a.type.includes('BUY') || a.type.includes('BULLISH') || a.type === 'SUPERTREND_BUY' ? 'text-emerald-400' : 'text-red-400'}`}>{a.symbol}</span>
                    <span className="text-slate-300 flex-1">{a.type.replace(/_/g,' ')}</span>
                    <span className="text-amber-400">@ {a.triggeredValue?.toFixed(2)}</span>
                    <span className="text-slate-600 flex-shrink-0">{a.firedAt ? new Date(a.firedAt).toLocaleTimeString('en-IN') : '—'}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---- PAPER TRADING TAB ---- */}
      {tab === 'paper' && (
        <div className="flex-1 overflow-auto p-4 flex flex-col gap-4">

          {/* Stats Bar */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {[
              { label: 'Total Trades', val: paperStats.total_trades ?? 0, cls: 'text-slate-200' },
              { label: 'Win Rate', val: `${paperStats.win_rate_pct ?? 0}%`, cls: parseFloat(paperStats.win_rate_pct) >= 55 ? 'text-emerald-400' : 'text-amber-400' },
              { label: 'Total P&L', val: `₹${(paperStats.total_pnl ?? 0).toLocaleString('en-IN', {minimumFractionDigits:2})}`, cls: parseFloat(paperStats.total_pnl) >= 0 ? 'text-emerald-400' : 'text-red-400' },
              { label: 'Best Trade', val: `₹${paperStats.best_trade ?? 0}`, cls: 'text-emerald-400' },
              { label: 'Open Now', val: paperStats.live_open ?? 0, cls: 'text-amber-400' },
            ].map(({ label, val, cls }) => (
              <div key={label} className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
                <div className="text-3xs text-slate-500 mb-1">{label}</div>
                <div className={`text-sm font-bold ${cls}`}>{val}</div>
              </div>
            ))}
          </div>

          {/* Day-wise Summary */}
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4">
            <h3 className="text-xs font-bold mb-3 text-violet-300">Day-Wise P&L (Last 30 Days)</h3>
            {paperDaily.length === 0 ? (
              <p className="text-3xs text-slate-500 text-center py-4">No paper trades recorded yet. Trades will auto-start during market hours (9:20 AM IST).</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-3xs">
                  <thead><tr className="text-slate-500">
                    <th className="text-left p-1.5">Date</th>
                    <th className="text-right p-1.5">Trades</th>
                    <th className="text-right p-1.5">Wins</th>
                    <th className="text-right p-1.5">Losses</th>
                    <th className="text-right p-1.5">Win%</th>
                    <th className="text-right p-1.5">Best</th>
                    <th className="text-right p-1.5">Worst</th>
                    <th className="text-right p-1.5 font-bold">Day P&L</th>
                  </tr></thead>
                  <tbody>
                    {paperDaily.map((d: any) => (
                      <tr key={d.trade_date}
                        className={`border-t border-slate-800/40 hover:bg-slate-800/20 cursor-pointer ${paperDate === d.trade_date ? 'bg-slate-800/40' : ''}`}
                        onClick={() => {
                          setPaperDate(d.trade_date);
                          fetch(`${DELTA_API}/paper/trades?date=${d.trade_date}`).then(r => r.json()).then(setPaperTrades).catch(() => {});
                        }}>
                        <td className="p-1.5 text-slate-300">{d.trade_date}</td>
                        <td className="p-1.5 text-right text-slate-400">{d.closed}/{d.total_trades}</td>
                        <td className="p-1.5 text-right text-emerald-400">{d.wins}</td>
                        <td className="p-1.5 text-right text-red-400">{d.losses}</td>
                        <td className={`p-1.5 text-right font-bold ${parseFloat(d.win_rate_pct) >= 55 ? 'text-emerald-400' : parseFloat(d.win_rate_pct) >= 45 ? 'text-amber-400' : 'text-red-400'}`}>{d.win_rate_pct}%</td>
                        <td className="p-1.5 text-right text-emerald-400">₹{d.best_trade ?? '—'}</td>
                        <td className="p-1.5 text-right text-red-400">₹{d.worst_trade ?? '—'}</td>
                        <td className={`p-1.5 text-right font-bold ${parseFloat(d.total_pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>₹{parseFloat(d.total_pnl ?? 0).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Trade Detail for Selected Date */}
          <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-bold text-amber-300">Trades — {paperDate === 'today' ? 'Today' : paperDate}
                <span className="ml-2 text-slate-500 font-normal">({paperTrades.length} trades)</span>
              </h3>
              <button onClick={() => {
                setPaperDate('today');
                fetch(`${DELTA_API}/paper/trades?date=today`).then(r => r.json()).then(setPaperTrades).catch(() => {});
              }} className="text-3xs text-violet-400 hover:text-violet-300">← Today</button>
            </div>
            {paperTrades.length === 0 ? (
              <p className="text-3xs text-slate-500 text-center py-4">No trades for this date.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-3xs">
                  <thead><tr className="text-slate-500">
                    <th className="text-left p-1.5">Symbol</th>
                    <th className="text-right p-1.5">Entry</th>
                    <th className="text-right p-1.5">Entry₹</th>
                    <th className="text-right p-1.5">Exit₹</th>
                    <th className="text-right p-1.5">Qty</th>
                    <th className="text-right p-1.5">P&L</th>
                    <th className="text-right p-1.5">Score</th>
                    <th className="text-right p-1.5">H.Prob</th>
                    <th className="text-right p-1.5">ML%</th>
                    <th className="text-right p-1.5">ΔStr%</th>
                    <th className="text-right p-1.5">Status</th>
                    <th className="text-right p-1.5">Reason</th>
                  </tr></thead>
                  <tbody>
                    {paperTrades.map((t: any) => {
                      const pnl = parseFloat(t.pnl ?? 0);
                      const isOpen = t.status === 'OPEN';
                      const isExpanded = selectedTradeId === t.id;
                      const entryTime = t.entry_ist ? t.entry_ist.split(' ')[1]?.slice(0,5) : '—';
                      const exitTime = t.exit_ist ? t.exit_ist.split(' ')[1]?.slice(0,5) : '—';
                      const holdMins = t.entry_ist && t.exit_ist
                        ? Math.round((new Date(t.exit_ist.replace(' ','T')+'+05:30').getTime() - new Date(t.entry_ist.replace(' ','T')+'+05:30').getTime()) / 60000)
                        : null;
                      const priceMoveAmt = t.exit_price ? (parseFloat(t.exit_price) - parseFloat(t.entry_price)) : null;
                      const priceMoveRatio = priceMoveAmt != null ? (priceMoveAmt / parseFloat(t.entry_price) * 100) : null;

                      // Entry reason pills
                      const entrySignals: {label:string; val:string; color:string; tip:string}[] = [
                        { label:'Score', val:`${t.pred_score}`, color: parseFloat(t.pred_score)>=70 ? 'bg-emerald-900/60 text-emerald-300' : parseFloat(t.pred_score)>=65 ? 'bg-amber-900/60 text-amber-300' : 'bg-red-900/60 text-red-300', tip:'Composite signal score (0–100). ≥70 = strong, 65–70 = moderate' },
                        { label:'Hist%', val:`${t.hist_prob}%`, color: parseFloat(t.hist_prob)>=55 ? 'bg-emerald-900/60 text-emerald-300' : parseFloat(t.hist_prob)>=50 ? 'bg-amber-900/60 text-amber-300' : 'bg-red-900/60 text-red-300', tip:'Historical win probability from similar past setups. ≥55% = bullish edge' },
                        { label:'ML%', val:`${t.ml_prob}%`, color: parseFloat(t.ml_prob)>=30 ? 'bg-violet-900/60 text-violet-300' : parseFloat(t.ml_prob)>=20 ? 'bg-amber-900/60 text-amber-300' : 'bg-slate-800 text-slate-400', tip:'Machine learning model confidence. ≥30% = strong ML signal (note: uncalibrated raw output)' },
                        { label:'Δ-Str', val:`${parseFloat(t.delta_strength??0).toFixed(1)}`, color: parseFloat(t.delta_strength)>=50 ? 'bg-cyan-900/60 text-cyan-300' : parseFloat(t.delta_strength)>=10 ? 'bg-amber-900/60 text-amber-300' : 'bg-slate-800 text-slate-400', tip:'Delta strength = net buy pressure (buy vol − sell vol normalised). Higher = more aggressive buying' },
                        { label:'Exp.Move', val:`${parseFloat(t.expected_move??0).toFixed(3)}`, color: parseFloat(t.expected_move)>0 ? 'bg-emerald-900/60 text-emerald-300' : parseFloat(t.expected_move)<0 ? 'bg-red-900/60 text-red-300' : 'bg-slate-800 text-slate-400', tip:'Model-predicted price move direction (+ve=up, -ve=down). Used as directional bias at entry' },
                        { label:'VIX', val:`${t.vix_level}`, color: t.vix_level==='NORMAL' ? 'bg-emerald-900/60 text-emerald-300' : t.vix_level==='HIGH' ? 'bg-amber-900/60 text-amber-300' : 'bg-red-900/60 text-red-300', tip:'Market volatility regime at time of entry. NORMAL = low fear, HIGH = caution, EXTREME = risky' },
                        ...(t.is_expiry_day ? [{ label:'EXPIRY', val:'⚠', color:'bg-red-900/60 text-red-300', tip:'Expiry day — options pinning & extreme volatility can distort delta signals' }] : []),
                      ];

                      // Exit reason explanation
                      const exitReasonMap: Record<string,{label:string;desc:string;color:string}> = {
                        '30MIN_TARGET': { label:'30-Min Hold', desc:'Held for the full 30-minute window then exited at market price. No stop or target was hit — exit was time-based. (legacy rule)', color:'text-slate-300' },
                        'TIME_EXIT':    { label:'Max Hold (60m)', desc:'Held for the maximum 60-minute window then exited at market price. Neither the ATR stop nor the ATR target was reached.', color:'text-slate-300' },
                        'STOP_LOSS':    { label:'ATR Stop Loss', desc:'Price fell to entry − 1.5×ATR — the hard risk-control stop. Loss capped at the planned risk amount.', color:'text-red-400' },
                        'BREAKEVEN_STOP': { label:'Breakeven Stop', desc:'After the partial profit was banked, the stop moved to entry price. Price came back and tagged it — remaining position exited flat, keeping the banked partial profit.', color:'text-amber-300' },
                        'TRAIL_STOP':   { label:'Trailing Stop', desc:'Price ran in profit, the stop trailed 1×ATR below the high, and the pullback tagged it — locking in gains.', color:'text-emerald-300' },
                        'TARGET':       { label:'Profit Target', desc:'Price reached entry + 2.5×ATR — the full profit target. Best-case exit.', color:'text-emerald-300' },
                        'REVERSAL':     { label:'Reversal Stop', desc:'Signal flipped BEARISH (score ≤35) with confirmed selling pressure (negative 1-min delta) — engine cut the trade early.', color:'text-red-300' },
                        'EOD':          { label:'End of Day', desc:'Market closing — all open paper trades force-exited at last available price.', color:'text-amber-300' },
                        'ORPHANED_RESTART': { label:'Orphaned', desc:'Position left open across a restart from a previous day — closed flat at entry price.', color:'text-slate-500' },
                      };
                      const exitInfo = exitReasonMap[t.exit_reason] ?? { label: t.exit_reason ?? '—', desc: 'Unknown exit trigger.', color: 'text-slate-400' };

                      return (
                        <React.Fragment key={t.id}>
                          <tr
                            className={`border-t border-slate-800/40 hover:bg-slate-800/30 cursor-pointer transition-colors ${isExpanded ? 'bg-slate-800/40' : ''}`}
                            onClick={() => setSelectedTradeId(isExpanded ? null : t.id)}
                          >
                            <td className="p-1.5 font-bold text-slate-100">
                              <span className={`mr-1 text-slate-600 text-3xs`}>{isExpanded ? '▲' : '▶'}</span>{t.symbol}
                            </td>
                            <td className="p-1.5 text-right text-slate-400">{t.entry_ist ?? '—'}</td>
                            <td className="p-1.5 text-right text-slate-300">₹{parseFloat(t.entry_price ?? 0).toFixed(2)}</td>
                            <td className="p-1.5 text-right text-slate-300">{t.exit_price ? `₹${parseFloat(t.exit_price).toFixed(2)}` : '—'}</td>
                            <td className="p-1.5 text-right text-slate-500">{t.qty}</td>
                            <td className={`p-1.5 text-right font-bold ${isOpen ? 'text-amber-400' : pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {isOpen ? 'OPEN' : `₹${pnl.toFixed(2)}`}
                            </td>
                            <td className="p-1.5 text-right text-slate-400">{t.pred_score}</td>
                            <td className="p-1.5 text-right text-slate-400">{t.hist_prob}%</td>
                            <td className="p-1.5 text-right text-slate-400">{t.ml_prob}%</td>
                            <td className={`p-1.5 text-right ${parseFloat(t.delta_strength) > 5 ? 'text-emerald-400' : 'text-slate-400'}`}>{parseFloat(t.delta_strength ?? 0).toFixed(1)}%</td>
                            <td className={`p-1.5 text-right text-3xs font-bold px-1 rounded ${isOpen ? 'text-amber-400' : pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{t.status}</td>
                            <td className="p-1.5 text-right text-slate-500">{t.exit_reason ?? '—'}</td>
                          </tr>
                          {isExpanded && (
                            <tr className="bg-slate-950/80 border-t border-slate-700/60">
                              <td colSpan={12} className="px-4 py-3">
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">

                                  {/* WHY ENTRY */}
                                  <div className="bg-slate-900/80 border border-slate-700/60 rounded-xl p-3">
                                    <div className="flex items-center gap-2 mb-2">
                                      <span className="text-emerald-400 text-xs">▶ WHY ENTERED</span>
                                      <span className="text-slate-500 text-3xs">{entryTime} IST</span>
                                    </div>
                                    <p className="text-3xs text-slate-400 mb-2 leading-relaxed">
                                      All {entrySignals.length} entry conditions passed for <span className="text-white font-bold">{t.symbol}</span> at ₹{parseFloat(t.entry_price).toFixed(2)}.
                                      The engine scanned live delta flow, scored this setup at <span className="text-amber-300">{t.pred_score}</span> and triggered a <span className="text-emerald-300">{t.signal}</span> paper trade.
                                    </p>
                                    <div className="flex flex-wrap gap-1.5">
                                      {entrySignals.map(s => (
                                        <div key={s.label} className={`px-2 py-1 rounded-lg text-3xs font-mono ${s.color}`} title={s.tip}>
                                          <span className="text-slate-500 mr-1">{s.label}:</span>{s.val}
                                        </div>
                                      ))}
                                    </div>
                                    <div className="mt-2 grid grid-cols-2 gap-1.5 text-3xs">
                                      {entrySignals.map(s => (
                                        <div key={s.label+'-tip'} className="text-slate-500"><span className={`font-bold ${s.color.split(' ').pop()}`}>{s.label}</span> — {s.tip}</div>
                                      ))}
                                    </div>
                                  </div>

                                  {/* WHY EXIT */}
                                  <div className="bg-slate-900/80 border border-slate-700/60 rounded-xl p-3">
                                    <div className="flex items-center gap-2 mb-2">
                                      <span className={`text-xs ${isOpen ? 'text-amber-400' : pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                        {isOpen ? '⏳ STILL OPEN' : `${pnl >= 0 ? '✓' : '✗'} WHY EXITED`}
                                      </span>
                                      {!isOpen && <span className="text-slate-500 text-3xs">{exitTime} IST{holdMins != null ? ` · held ${holdMins}m` : ''}</span>}
                                    </div>
                                    {!isOpen && (
                                      <>
                                        <div className={`inline-block px-2 py-0.5 rounded text-3xs font-bold mb-2 ${pnl>=0 ? 'bg-emerald-900/60 text-emerald-300' : 'bg-red-900/60 text-red-300'}`}>
                                          {exitInfo.label}
                                        </div>
                                        <p className="text-3xs text-slate-400 leading-relaxed mb-2">{exitInfo.desc}</p>
                                        {t.atr_entry != null && (
                                          <p className="text-3xs text-slate-500 mb-2 font-mono">
                                            Plan: Stop ₹{parseFloat(t.stop_price ?? 0).toFixed(2)} (1.5×ATR) · Target ₹{(parseFloat(t.entry_price) + 2.5*parseFloat(t.atr_entry)).toFixed(2)} (2.5×ATR) · ATR ₹{parseFloat(t.atr_entry).toFixed(2)}
                                            {t.partial_pnl != null && <span className="text-emerald-400"> · Partial banked ₹{parseFloat(t.partial_pnl).toFixed(2)}</span>}
                                          </p>
                                        )}
                                        <div className="grid grid-cols-3 gap-2 text-3xs">
                                          <div className="bg-slate-800/60 rounded-lg p-2">
                                            <div className="text-slate-500 mb-0.5">Price Move</div>
                                            <div className={`font-bold ${priceMoveAmt != null && priceMoveAmt >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                                              {priceMoveAmt != null ? `${priceMoveAmt >= 0 ? '+' : ''}₹${priceMoveAmt.toFixed(2)}` : '—'}
                                            </div>
                                            <div className={`text-3xs ${priceMoveRatio != null && priceMoveRatio >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
                                              {priceMoveRatio != null ? `${priceMoveRatio >= 0 ? '+' : ''}${priceMoveRatio.toFixed(3)}%` : ''}
                                            </div>
                                          </div>
                                          <div className="bg-slate-800/60 rounded-lg p-2">
                                            <div className="text-slate-500 mb-0.5">Hold Time</div>
                                            <div className="font-bold text-slate-200">{holdMins != null ? `${holdMins} min` : '—'}</div>
                                          </div>
                                          <div className="bg-slate-800/60 rounded-lg p-2">
                                            <div className="text-slate-500 mb-0.5">Final P&L</div>
                                            <div className={`font-bold text-sm ${pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>₹{pnl.toFixed(2)}</div>
                                            <div className="text-3xs text-slate-500">{t.qty} qty</div>
                                          </div>
                                        </div>
                                      </>
                                    )}
                                    {isOpen && <p className="text-3xs text-amber-300/70">Trade is still running. Exit triggers: ATR stop-loss, trailing stop, 2.5×ATR target, 60-min max hold, reversal, or EOD (15:20).</p>}
                                  </div>

                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-slate-700">
                      <td colSpan={5} className="p-1.5 text-right text-slate-500 font-bold text-3xs">Day Total:</td>
                      <td className={`p-1.5 text-right font-bold text-sm ${paperTrades.reduce((s: number, t: any) => s + parseFloat(t.pnl ?? 0), 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        ₹{paperTrades.reduce((s: number, t: any) => s + parseFloat(t.pnl ?? 0), 0).toFixed(2)}
                      </td>
                      <td colSpan={6}></td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---- LEGEND ---- */}
      <div className="px-4 py-2 border-t border-slate-800/60 flex flex-wrap gap-x-4 gap-y-1 text-3xs text-slate-600">
        <span><span className="text-violet-300">Δ</span> = BuyVol−SellVol</span>
        <span><span className="text-violet-300">Cum Δ</span> = running total</span>
        <span><span className="text-violet-300">OBI</span> = (Bid−Ask)/(Bid+Ask)</span>
        <span><span className="text-violet-300">ATR14</span> = Wilder avg true range</span>
        <span><span className="text-violet-300">MACD</span> = EMA12−EMA26 / Signal EMA9</span>
        <span><span className="text-violet-300">BB</span> = 20-SMA ± 2σ</span>
        <span><span className="text-emerald-400">Score≥65</span> bullish · <span className="text-red-400">≤35</span> bearish · SSE push every 1s</span>
      </div>
    </main>
  );
}

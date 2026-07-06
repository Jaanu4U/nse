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
  const [tab, setTab]           = useState<'live'|'indicators'|'alerts'>('live');
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

    es.addEventListener('market', (e) => {
      try {
        const data: Stock[] = JSON.parse(e.data);
        setStocks(data);
        setConnected(true);
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
    return () => esRef.current?.close();
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
        {[['live','⚡ Delta & Price'],['indicators','📈 Indicators'],['alerts','🔔 Alerts']].map(([key, label]) => (
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
                {th('OBI','obi','w-14')}
                {th('RSI','rsi','w-14')}
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

      {/* LEGEND */}
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

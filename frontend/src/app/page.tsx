'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useStockStore, API_BASE, Stock } from '../store/useStockStore';
import StockChart from '../components/StockChart';
import { 
  TrendingUp, TrendingDown, Search, Shield, RefreshCw, 
  Filter, AlertTriangle, Cpu, Globe, Activity, Rocket, BarChart3, ArrowRight,
  XCircle, ChevronDown, ChevronUp, AlertCircle,
} from 'lucide-react';

// Custom lightweight Markdown renderer to support AI Analyst tab cleanly without dependencies
const renderMarkdown = (text: string) => {
  if (!text) return null;
  const lines = text.split('\n');
  return lines.map((line, index) => {
    // Headers
    if (line.startsWith('# ')) {
      return <h1 key={index} className="text-xl font-bold text-emerald-400 mt-4 mb-2 border-b border-slate-800 pb-1">{line.slice(2)}</h1>;
    }
    if (line.startsWith('## ')) {
      return <h2 key={index} className="text-lg font-bold text-slate-200 mt-3 mb-2">{line.slice(3)}</h2>;
    }
    if (line.startsWith('### ')) {
      return <h3 key={index} className="text-md font-bold text-emerald-300 mt-2 mb-1">{line.slice(4)}</h3>;
    }
    if (line.startsWith('**DISCLAIMER**') || line.startsWith('DISCLAIMER:')) {
      return (
        <div key={index} className="p-3 bg-red-950/20 border border-red-900/50 rounded-xl my-4 flex gap-2">
          <AlertTriangle className="text-red-400 shrink-0 w-5 h-5" />
          <span className="text-xs text-red-300 font-medium leading-relaxed">{line}</span>
        </div>
      );
    }
    // Lists
    if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
      const formatted = line.trim().slice(2).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      return (
        <li key={index} className="text-xs text-slate-350 ml-4 list-disc my-1" dangerouslySetInnerHTML={{ __html: formatted }}></li>
      );
    }
    // Bold text parsing
    if (line.includes('**')) {
      const formatted = line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      return <p key={index} className="text-xs text-slate-350 my-1 leading-relaxed" dangerouslySetInnerHTML={{ __html: formatted }}></p>;
    }
    
    if (line.trim() === '---') {
      return <hr key={index} className="border-slate-800 my-3" />;
    }
    
    return line.trim() ? <p key={index} className="text-xs text-slate-350 my-1 leading-relaxed">{line}</p> : <div key={index} className="h-2" />;
  });
};

export default function Home() {
  const {
    selectedSymbol, setSelectedSymbol,
    activeTab, setActiveTab,
    stocks, fetchStocks,
    fiiDiiData, insiderTrades, optionsChain,
    fetchFiiDiiData, fetchInsiderTrades, fetchOptionsChain
  } = useStockStore();

  // Selected Stock metadata
  const currentStock = stocks.find(s => s.symbol === selectedSymbol);

  // Search input state
  const [searchQuery, setSearchQuery] = useState('');
  const [showDropdown, setShowDropdown] = useState(false);

  // Chart configuration toggles
  const [showEma20, setShowEma20] = useState(true);
  const [showEma50, setShowEma50] = useState(false);
  const [showEma200, setShowEma200] = useState(false);
  const [showBb, setShowBb] = useState(false);
  const [showSrLevels, setShowSrLevels] = useState(true);

  // Analysis data states
  const [analysisData, setAnalysisData] = useState<any>(null);
  const [similarityData, setSimilarityData] = useState<any>(null);
  const [aiReport, setAiReport] = useState<string>('');
  const [newsData, setNewsData] = useState<any[]>([]);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);
  const [loadingAi, setLoadingAi] = useState(false);
  const [analyticsData, setAnalyticsData] = useState<any>(null);
  const [backtestData, setBacktestData] = useState<any>(null);
  const [extraIndicators, setExtraIndicators] = useState<any>(null);
  const [fundamentals, setFundamentals] = useState<any>(null);
  const [correlation, setCorrelation] = useState<any>(null);

  // Screener states
  const [peFilter, setPeFilter] = useState<number>(50);
  const [rsiMin, setRsiMin] = useState<number>(30);
  const [rsiMax, setRsiMax] = useState<number>(70);
  const [roeMin, setRoeMin] = useState<number>(15);
  const [screenerResults, setScreenerResults] = useState<any[]>([]);
  const [screenerLoading, setScreenerLoading] = useState(false);

  // Breakout-ready scanner states
  const [breakoutReady, setBreakoutReady] = useState<any[]>([]);
  const [breakoutLoading, setBreakoutLoading] = useState(false);
  const [stockBreakouts, setStockBreakouts] = useState<any>(null);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [showCatalog, setShowCatalog] = useState(false);
  const [breakoutFilter, setBreakoutFilter] = useState<string>('all');

  // Market pulse (breadth + sector rotation)
  const [marketBreadth, setMarketBreadth] = useState<any>(null);
  const [sectorPerf, setSectorPerf] = useState<any>(null);

  // Background job progress (daily sync / full regeneration)
  const [jobStatus, setJobStatus] = useState<any>(null);
  const [showJobDone, setShowJobDone] = useState(false);

  // System error/warning panel
  const [sysErrors, setSysErrors] = useState<any[]>([]);
  const [errPanelOpen, setErrPanelOpen] = useState(true);
  const [errDismissed, setErrDismissed] = useState(false);

  // Fetch stocks list on mount
  useEffect(() => {
    fetchStocks();
  }, [fetchStocks]);

  // Poll system errors/warnings every 60s — shows inline on dashboard
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/admin/errors/recent?limit=30`);
        if (r.ok) setSysErrors(await r.json());
      } catch { /* silent — non-critical */ }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  // Poll background job status so the UI can show a live progress bar
  useEffect(() => {
    let lastStatus = '';
    const poll = async () => {
      try {
        const r = await fetch(`${API_BASE}/metrics/job-status`);
        if (!r.ok) return;
        const d = await r.json();
        setJobStatus(d);
        // When a running job transitions to completed, show a "done" banner briefly.
        if (lastStatus === 'running' && d.status === 'completed') {
          setShowJobDone(true);
          setTimeout(() => setShowJobDone(false), 8000);
        }
        lastStatus = d.status;
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  // Fetch market pulse on mount
  useEffect(() => {
    fetch(`${API_BASE}/market/breadth`).then(r => r.ok ? r.json() : null).then(d => d && setMarketBreadth(d)).catch(() => {});
    fetch(`${API_BASE}/market/sectors`).then(r => r.ok ? r.json() : null).then(d => d && setSectorPerf(d)).catch(() => {});
  }, []);

  // Fetch data for selected Stock symbol
  const loadStockData = async (symbol: string) => {
    setLoadingAnalysis(true);
    try {
      // 1. Fetch main tech analysis (S&R, Breakout, Patterns, Preds)
      const res = await fetch(`${API_BASE}/stocks/${symbol}/analysis`);
      if (res.ok) {
        const data = await res.json();
        setAnalysisData(data);
      }

      // 2. Fetch similarity matches
      const simRes = await fetch(`${API_BASE}/stocks/${symbol}/similarity`);
      if (simRes.ok) {
        const simData = await simRes.json();
        setSimilarityData(simData);
      }

      // 3. Fetch News
      const newsRes = await fetch(`${API_BASE}/stocks/${symbol}/news`);
      if (newsRes.ok) {
        const news = await newsRes.json();
        setNewsData(news);
      }

      // 3b. Fetch quant analytics (risk metrics, trend, trade levels) + backtest
      setAnalyticsData(null);
      setBacktestData(null);
      fetch(`${API_BASE}/stocks/${symbol}/analytics`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setAnalyticsData(d))
        .catch(() => {});
      fetch(`${API_BASE}/stocks/${symbol}/backtest`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setBacktestData(d))
        .catch(() => {});

      // 3c. Fetch detected breakout patterns
      setStockBreakouts(null);
      fetch(`${API_BASE}/stocks/${symbol}/breakouts`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setStockBreakouts(d))
        .catch(() => {});

      // 3d. Fetch extra technical indicators (SAR, Donchian, Keltner, CMF, etc.)
      setExtraIndicators(null);
      fetch(`${API_BASE}/stocks/${symbol}/extra-indicators`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setExtraIndicators(d))
        .catch(() => {});

      // 3e. Fetch real fundamentals + DCF and return correlations
      setFundamentals(null);
      setCorrelation(null);
      fetch(`${API_BASE}/stocks/${symbol}/fundamentals`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setFundamentals(d))
        .catch(() => {});
      fetch(`${API_BASE}/stocks/${symbol}/correlation`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setCorrelation(d))
        .catch(() => {});

      // 4. Fetch Advanced modules from store
      await fetchInsiderTrades(symbol);
      await fetchOptionsChain(symbol);
      await fetchFiiDiiData();
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingAnalysis(false);
    }
  };

  useEffect(() => {
    if (selectedSymbol) {
      loadStockData(selectedSymbol);
      // Reset AI Report when symbol changes
      setAiReport('');
    }
  }, [selectedSymbol]);

  // Load AI report on demand
  const handleLoadAiReport = async () => {
    setLoadingAi(true);
    try {
      const res = await fetch(`${API_BASE}/stocks/${selectedSymbol}/ai-report`);
      if (res.ok) {
        const data = await res.json();
        setAiReport(data.report);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingAi(false);
    }
  };

  // Run Screener filter
  const handleRunScreener = async () => {
    setScreenerLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pe_max: peFilter,
          rsi_min: rsiMin,
          rsi_max: rsiMax,
          roe_min: roeMin
        })
      });
      if (res.ok) {
        const data = await res.json();
        setScreenerResults(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setScreenerLoading(false);
    }
  };

  // Run initial screener
  useEffect(() => {
    handleRunScreener();
  }, []);


  const fetchBreakoutReady = async () => {
    setBreakoutLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/breakout-ready?limit=25&min_readiness=55`);
      if (res.ok) setBreakoutReady(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setBreakoutLoading(false);
    }
  };

  // Load breakout scanner + catalog on mount
  useEffect(() => {
    fetchBreakoutReady();
    fetch(`${API_BASE}/screener/breakout-catalog`)
      .then(r => r.ok ? r.json() : [])
      .then(d => setCatalog(d))
      .catch(() => {});
  }, []);

  // Filter stocks for autocomplete
  const filteredStocks = searchQuery 
    ? stocks.filter(s => 
        s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) || 
        s.company_name.toLowerCase().includes(searchQuery.toLowerCase())
      ).slice(0, 8)
    : [];

  // Format a job timestamp (UTC ISO from backend) into local IST date & time.
  const formatJobTime = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleString('en-IN', {
        day: '2-digit', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit', hour12: true,
        timeZone: 'Asia/Kolkata',
      }) + ' IST';
    } catch {
      return iso;
    }
  };

  return (
    <main className="min-h-screen bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black text-slate-100 p-6 flex flex-col gap-6">
      {/* HEADER */}
      <header className="flex flex-col md:flex-row items-center justify-between gap-4 p-5 bg-slate-900/30 backdrop-blur-lg border border-slate-800/70 rounded-3xl shadow-xl">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center shadow-lg shadow-emerald-500/20">
            <TrendingUp className="text-slate-950 w-6 h-6" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight bg-gradient-to-r from-emerald-400 to-teal-300 bg-clip-text text-transparent">NSE ANALYTICA</h1>
            <p className="text-2xs text-slate-400">Institutional Stock Analysis Platform</p>
          </div>
        </div>

        {/* Stock Search Autocomplete */}
        <div className="relative w-full max-w-md">
          <div className="relative flex items-center">
            <Search className="absolute left-3.5 text-slate-500 w-4 h-4" />
            <input
              type="text"
              placeholder="Search NSE stock (e.g. RELIANCE, TCS)..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setShowDropdown(true);
              }}
              onFocus={() => setShowDropdown(true)}
              className="w-full pl-10 pr-4 py-2 bg-slate-950/60 border border-slate-800 hover:border-slate-800 focus:border-emerald-500/50 rounded-2xl text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500/20 transition-all text-slate-200"
            />
          </div>

          {showDropdown && filteredStocks.length > 0 && (
            <div className="absolute top-full left-0 right-0 mt-2 bg-slate-900/90 border border-slate-800 rounded-xl shadow-2xl z-50 backdrop-blur-lg max-h-60 overflow-y-auto">
              {filteredStocks.map((s) => (
                <button
                  key={s.id}
                  onClick={() => {
                    setSelectedSymbol(s.symbol);
                    setSearchQuery('');
                    setShowDropdown(false);
                  }}
                  className="w-full text-left px-4 py-2 hover:bg-slate-800/50 text-xs flex justify-between border-b border-slate-800/50"
                >
                  <span className="font-bold text-slate-200">{s.symbol}</span>
                  <span className="text-slate-400 truncate max-w-[200px]">{s.company_name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Selected stock quick quote */}
        {currentStock && (
          <div className="flex items-center gap-6 px-4 py-1.5 bg-slate-950/30 border border-slate-800/50 rounded-2xl">
            <div className="flex flex-col">
              <span className="text-2xs text-slate-400 uppercase font-semibold">Active Ticker</span>
              <span className="text-sm font-bold text-slate-200">{currentStock.symbol}</span>
            </div>
            {analysisData?.breakout && (
              <div className="flex flex-col text-right">
                <span className="text-2xs text-slate-400 font-semibold">Trend Trigger</span>
                <span className={`text-xs font-bold ${analysisData.breakout.breakout_detected ? 'text-emerald-400' : 'text-slate-400'}`}>
                  {analysisData.breakout.breakout_detected ? analysisData.breakout.type.replace('_', ' ') : 'STABLE'}
                </span>
              </div>
            )}
          </div>
        )}
      </header>

      {/* BACKGROUND JOB PROGRESS BANNER — always visible */}
      <div className={`flex items-center gap-3 px-5 py-3 rounded-2xl border backdrop-blur-lg shadow-lg ${
        jobStatus?.status === 'running'  ? 'bg-sky-950/40 border-sky-700/60' :
        jobStatus?.status === 'failed'   ? 'bg-red-950/40 border-red-700/60' :
        jobStatus?.status === 'completed'? 'bg-emerald-950/30 border-emerald-800/50' :
                                           'bg-slate-900/30 border-slate-800/50'
      }`}>
        {jobStatus?.status === 'running' ? (
          <RefreshCw className="w-4 h-4 text-sky-400 animate-spin flex-shrink-0" />
        ) : jobStatus?.status === 'failed' ? (
          <span className="text-red-400 text-base flex-shrink-0">✗</span>
        ) : jobStatus?.status === 'completed' ? (
          <Activity className="w-4 h-4 text-emerald-400 flex-shrink-0" />
        ) : (
          <Activity className="w-4 h-4 text-slate-500 flex-shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-3 mb-1">
            <span className={`text-2xs font-bold ${
              jobStatus?.status === 'running'   ? 'text-sky-300' :
              jobStatus?.status === 'failed'    ? 'text-red-300' :
              jobStatus?.status === 'completed' ? 'text-emerald-300' : 'text-slate-400'
            }`}>
              {jobStatus?.status === 'running'
                ? `⏳ Running: ${jobStatus?.message || 'processing…'}`
                : jobStatus?.status === 'failed'
                ? `✗ Failed: ${jobStatus?.message || 'background job error'}`
                : jobStatus?.status === 'completed'
                ? `✓ Data ready · ${jobStatus?.message || 'update complete'}`
                : 'Data status: idle'}
            </span>
            <span className="text-2xs text-slate-400 flex-shrink-0">
              {jobStatus?.status === 'running'
                ? `${jobStatus?.current ?? 0}${jobStatus?.total ? ` / ${jobStatus.total}` : ''}${jobStatus?.percent != null ? ` · ${jobStatus.percent}%` : ''}`
                : jobStatus?.completed_at
                ? formatJobTime(jobStatus.completed_at)
                : '—'}
            </span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                jobStatus?.status === 'running'   ? 'bg-sky-500' :
                jobStatus?.status === 'failed'    ? 'bg-red-500' :
                jobStatus?.status === 'completed' ? 'bg-emerald-500' : 'bg-slate-700'
              }`}
              style={{ width: `${
                jobStatus?.status === 'running'   ? (jobStatus?.percent ?? 5) :
                jobStatus?.status === 'completed' ? 100 :
                jobStatus?.status === 'failed'    ? (jobStatus?.percent ?? 0) : 0
              }%` }}
            />
          </div>
        </div>
      </div>

      {/* SYSTEM ERROR / WARNING PANEL — only when errors exist and not dismissed */}
      {sysErrors.length > 0 && !errDismissed && (() => {
        const errCount  = sysErrors.filter(e => e.level === 'ERROR').length;
        const warnCount = sysErrors.filter(e => e.level === 'WARNING').length;
        const hasCrit   = errCount > 0;
        const fmt = (iso: string) => {
          const d = new Date(iso);
          return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) +
                 ' ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
        };
        return (
          <div className={`rounded-2xl border backdrop-blur-lg ${hasCrit ? 'bg-red-950/25 border-red-700/50' : 'bg-amber-950/20 border-amber-700/40'}`}>
            {/* Header row */}
            <div className="flex items-center gap-3 px-5 py-3">
              {hasCrit
                ? <XCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
                : <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />}
              <span className={`text-xs font-semibold flex-1 ${hasCrit ? 'text-red-300' : 'text-amber-300'}`}>
                System Alerts —&nbsp;
                {errCount > 0 && <span className="text-red-400">{errCount} error{errCount !== 1 ? 's' : ''}</span>}
                {errCount > 0 && warnCount > 0 && <span className="text-slate-500"> · </span>}
                {warnCount > 0 && <span className="text-amber-400">{warnCount} warning{warnCount !== 1 ? 's' : ''}</span>}
              </span>
              <div className="flex items-center gap-2">
                <Link href="/admin" className={`text-2xs px-2.5 py-1 rounded-lg border transition-colors ${hasCrit ? 'border-red-700/50 text-red-400 hover:bg-red-900/30' : 'border-amber-700/40 text-amber-400 hover:bg-amber-900/20'}`}>
                  View all →
                </Link>
                <button onClick={() => setErrPanelOpen(o => !o)}
                  className="text-slate-500 hover:text-slate-300 transition-colors p-1">
                  {errPanelOpen ? <ChevronUp size={15}/> : <ChevronDown size={15}/>}
                </button>
                <button onClick={() => setErrDismissed(true)}
                  className="text-slate-600 hover:text-slate-400 transition-colors p-1 text-xs leading-none">✕</button>
              </div>
            </div>

            {/* Expandable rows */}
            {errPanelOpen && (
              <div className="border-t border-slate-800/60 divide-y divide-slate-800/40 max-h-48 overflow-y-auto">
                {sysErrors.slice(0, 10).map(e => (
                  <div key={e.id} className="flex items-start gap-3 px-5 py-2.5 hover:bg-slate-800/20 transition-colors">
                    <span className={`mt-0.5 flex-shrink-0 text-3xs font-bold px-1.5 py-0.5 rounded border ${
                      e.level === 'ERROR'
                        ? 'bg-red-500/15 text-red-400 border-red-500/30'
                        : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                    }`}>{e.level === 'ERROR' ? 'ERR' : 'WRN'}</span>
                    <span className="text-3xs text-slate-500 whitespace-nowrap mt-0.5 flex-shrink-0 w-28">{fmt(e.captured_at)}</span>
                    <span className="text-3xs text-slate-400 font-mono flex-shrink-0 w-20 truncate mt-0.5">{e.service ?? e.logger?.split('.').pop() ?? '—'}</span>
                    <span className="text-3xs text-slate-300 leading-relaxed line-clamp-2">{e.message}</span>
                  </div>
                ))}
                {sysErrors.length > 10 && (
                  <div className="px-5 py-2 text-3xs text-slate-500 text-center">
                    +{sysErrors.length - 10} more — <Link href="/admin" className="text-emerald-500 hover:underline">open admin →</Link>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })()}

      {/* DASHBOARD CONTENT GRID */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">

        {/* CENTER PANEL: CHART & DETAILED ANALYTICS */}
        <div className="xl:col-span-2 flex flex-col gap-6">
          
          {/* CHART CONTROLS & CHART CONTAINER */}
          <div className="flex flex-col gap-3">
            {/* Chart Control Toggles */}
            <div className="flex flex-wrap items-center justify-between gap-2 p-3 bg-slate-900/30 border border-slate-800/60 rounded-2xl">
              <span className="text-2xs text-slate-400 font-semibold flex items-center gap-1">
                <Activity className="w-3.5 h-3.5 text-emerald-400" /> Chart Controls
              </span>
              
              <div className="flex flex-wrap gap-2 text-3xs font-bold">
                <label className="flex items-center gap-1.5 cursor-pointer bg-slate-950/50 border border-slate-800/80 px-2 py-1 rounded-lg hover:border-slate-700 select-none">
                  <input type="checkbox" checked={showEma20} onChange={(e) => setShowEma20(e.target.checked)} className="accent-emerald-500" />
                  <span className="text-blue-400">EMA20</span>
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer bg-slate-950/50 border border-slate-800/80 px-2 py-1 rounded-lg hover:border-slate-700 select-none">
                  <input type="checkbox" checked={showEma50} onChange={(e) => setShowEma50(e.target.checked)} className="accent-emerald-500" />
                  <span className="text-yellow-400">EMA50</span>
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer bg-slate-950/50 border border-slate-800/80 px-2 py-1 rounded-lg hover:border-slate-700 select-none">
                  <input type="checkbox" checked={showEma200} onChange={(e) => setShowEma200(e.target.checked)} className="accent-emerald-500" />
                  <span className="text-pink-400">EMA200</span>
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer bg-slate-950/50 border border-slate-800/80 px-2 py-1 rounded-lg hover:border-slate-700 select-none">
                  <input type="checkbox" checked={showBb} onChange={(e) => setShowBb(e.target.checked)} className="accent-emerald-500" />
                  <span className="text-slate-400">Bollinger</span>
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer bg-slate-950/50 border border-slate-800/80 px-2 py-1 rounded-lg hover:border-slate-700 select-none">
                  <input type="checkbox" checked={showSrLevels} onChange={(e) => setShowSrLevels(e.target.checked)} className="accent-emerald-500" />
                  <span className="text-emerald-400 font-semibold">S & R Zones</span>
                </label>
              </div>
            </div>

            {/* RENDER StockChart */}
            <StockChart
              symbol={selectedSymbol}
              showEma20={showEma20}
              showEma50={showEma50}
              showEma200={showEma200}
              showBb={showBb}
              showVwap={false}
              showSrLevels={showSrLevels}
            />
          </div>

          {/* DETAILED ANALYTICS TABS */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            
            {/* Tab selection header */}
            <div className="flex border-b border-slate-800/70 overflow-x-auto text-xs font-semibold gap-2 pb-2">
              <button
                onClick={() => setActiveTab('chart')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap ${activeTab === 'chart' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                Calculations & Breakout
              </button>
              <button
                onClick={() => setActiveTab('similarity')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap ${activeTab === 'similarity' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                KNN Similarity Matches
              </button>
              <button
                onClick={() => setActiveTab('fii_dii')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap ${activeTab === 'fii_dii' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                FII/DII Flows
              </button>
              <button
                onClick={() => setActiveTab('insider_trades')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap ${activeTab === 'insider_trades' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                Promoter Trades
              </button>
              <button
                onClick={() => setActiveTab('options_chain')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap ${activeTab === 'options_chain' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                Options Chain
              </button>
              <button
                onClick={() => setActiveTab('news')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap ${activeTab === 'news' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                News Sentiment Feed
              </button>
              <button
                onClick={() => setActiveTab('ai')}
                className={`px-3 py-1.5 rounded-lg transition-all whitespace-nowrap flex items-center gap-1.5 ${activeTab === 'ai' ? 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-450' : 'text-slate-400 hover:text-slate-200'}`}
              >
                <Cpu className="w-3.5 h-3.5 text-emerald-450 animate-pulse" /> AI Analyst
              </button>
            </div>

            {/* TAB CONTENTS */}
            {loadingAnalysis ? (
              <div className="py-10 flex items-center justify-center">
                <RefreshCw className="animate-spin text-emerald-500 w-8 h-8" />
              </div>
            ) : (
              <div>
                
                {/* 1. CALCULATIONS AND PATTERNS TAB */}
                {activeTab === 'chart' && (
                  <div className="space-y-4">
                    {/* Support & Resistance Table */}
                    {analysisData && (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        
                        {/* Supports */}
                        <div className="space-y-2">
                          <span className="text-2xs font-bold text-emerald-400">Horizontal Support Levels</span>
                          <table className="w-full text-left text-2xs border-collapse">
                            <thead>
                              <tr className="border-b border-slate-800 text-slate-500 font-semibold">
                                <th className="pb-1.5">Price Level</th>
                                <th className="pb-1.5">Touches</th>
                                <th className="pb-1.5">Last Touch</th>
                                <th className="pb-1.5">Confidence</th>
                              </tr>
                            </thead>
                            <tbody>
                              {analysisData.supports.length > 0 ? (
                                analysisData.supports.map((s: any, idx: number) => (
                                  <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950/20">
                                    <td className="py-1.5 font-bold text-slate-350">₹{s.price.toFixed(1)}</td>
                                    <td className="py-1.5 text-slate-400">{s.touches}</td>
                                    <td className="py-1.5 text-slate-400">{s.last_touch}</td>
                                    <td className="py-1.5"><span className="text-2xs bg-emerald-950/30 text-emerald-400 border border-emerald-550/20 px-1.5 py-0.5 rounded-md">{s.confidence}%</span></td>
                                  </tr>
                                ))
                              ) : (
                                <tr>
                                  <td colSpan={4} className="py-3 text-center text-slate-500">No support levels detected</td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>

                        {/* Resistances */}
                        <div className="space-y-2">
                          <span className="text-2xs font-bold text-red-400">Horizontal Resistance Levels</span>
                          <table className="w-full text-left text-2xs border-collapse">
                            <thead>
                              <tr className="border-b border-slate-800 text-slate-500 font-semibold">
                                <th className="pb-1.5">Price Level</th>
                                <th className="pb-1.5">Touches</th>
                                <th className="pb-1.5">Last Touch</th>
                                <th className="pb-1.5">Confidence</th>
                              </tr>
                            </thead>
                            <tbody>
                              {analysisData.resistances.length > 0 ? (
                                analysisData.resistances.map((r: any, idx: number) => (
                                  <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950/20">
                                    <td className="py-1.5 font-bold text-slate-350">₹{r.price.toFixed(1)}</td>
                                    <td className="py-1.5 text-slate-400">{r.touches}</td>
                                    <td className="py-1.5 text-slate-400">{r.last_touch}</td>
                                    <td className="py-1.5"><span className="text-2xs bg-red-950/30 text-red-400 border border-red-550/20 px-1.5 py-0.5 rounded-md">{r.confidence}%</span></td>
                                  </tr>
                                ))
                              ) : (
                                <tr>
                                  <td colSpan={4} className="py-3 text-center text-slate-500">No resistance levels detected</td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>

                      </div>
                    )}

                    {/* Chart patterns detected */}
                    {analysisData && (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border-t border-slate-800 pt-4">
                        
                        {/* Candlestick & Chart patterns */}
                        <div className="space-y-2">
                          <span className="text-2xs font-bold text-slate-200">Detected Signals (Last 10 Days)</span>
                          <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                            {analysisData.patterns.length > 0 ? (
                              analysisData.patterns.map((p: any, idx: number) => (
                                <div key={idx} className="flex justify-between items-center bg-slate-950/40 border border-slate-800/60 p-2 rounded-xl text-2xs">
                                  <div>
                                    <span className="font-bold text-slate-300">{p.pattern_name.replace('_', ' ')}</span>
                                    <span className="block text-3xs text-slate-500">Detected on {p.date}</span>
                                  </div>
                                  <div className="flex gap-2">
                                    <span className={`text-3xs font-semibold px-1.5 py-0.5 rounded-md ${p.signal_type === 'BULLISH' ? 'bg-emerald-950/30 text-emerald-450 border border-emerald-500/20' : 'bg-red-950/30 text-red-450 border border-red-500/20'}`}>{p.signal_type}</span>
                                    <span className="text-3xs text-slate-400">{p.confidence}% conf</span>
                                  </div>
                                </div>
                              ))
                            ) : (
                              <p className="text-2xs text-slate-500 py-3 text-center">No structural patterns detected in the immediate history.</p>
                            )}
                          </div>
                        </div>

                        {/* XGBoost probability bars */}
                        {analysisData.predictions && (
                          <div className="space-y-2">
                            <span className="text-2xs font-bold text-slate-200">Next-Day Return Probabilities (ML Engine)</span>
                            <div className="space-y-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl">
                              {/* +1% move */}
                              <div>
                                <div className="flex justify-between text-3xs font-semibold text-slate-400 mb-1">
                                  <span>Probability of &gt;= +1% Close tomorrow</span>
                                  <span className="font-bold text-emerald-400">{(analysisData.predictions.prob_plus_1 * 100).toFixed(1)}%</span>
                                </div>
                                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                  <div className="bg-emerald-550 h-full rounded-full" style={{ width: `${analysisData.predictions.prob_plus_1 * 100}%` }}></div>
                                </div>
                              </div>

                              {/* +2% move */}
                              <div>
                                <div className="flex justify-between text-3xs font-semibold text-slate-400 mb-1">
                                  <span>Probability of &gt;= +2% Close tomorrow</span>
                                  <span className="font-bold text-emerald-400">{(analysisData.predictions.prob_plus_2 * 100).toFixed(1)}%</span>
                                </div>
                                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                  <div className="bg-emerald-550 h-full rounded-full" style={{ width: `${analysisData.predictions.prob_plus_2 * 100}%` }}></div>
                                </div>
                              </div>

                              {/* +3% move */}
                              <div>
                                <div className="flex justify-between text-3xs font-semibold text-slate-400 mb-1">
                                  <span>Probability of &gt;= +3% Close tomorrow</span>
                                  <span className="font-bold text-emerald-400">{(analysisData.predictions.prob_plus_3 * 100).toFixed(1)}%</span>
                                </div>
                                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                  <div className="bg-emerald-550 h-full rounded-full" style={{ width: `${analysisData.predictions.prob_plus_3 * 100}%` }}></div>
                                </div>
                              </div>

                              {/* +5% move */}
                              <div>
                                <div className="flex justify-between text-3xs font-semibold text-slate-400 mb-1">
                                  <span>Probability of &gt;= +5% Close tomorrow</span>
                                  <span className="font-bold text-emerald-400">{(analysisData.predictions.prob_plus_5 * 100).toFixed(1)}%</span>
                                </div>
                                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                  <div className="bg-emerald-550 h-full rounded-full" style={{ width: `${analysisData.predictions.prob_plus_5 * 100}%` }}></div>
                                </div>
                              </div>
                              
                              <p className="text-3xs text-slate-500 italic mt-2 text-center">Estimation uses an XGBoost + LightGBM ensemble trained on 3-year feature runs.</p>
                            </div>
                          </div>
                        )}

                        {/* Quant Analytics: Trade Levels + Risk Metrics + Backtest */}
                        {analyticsData && (
                          <div className="space-y-3">
                            {/* Trade Plan */}
                            {analyticsData.trade_levels && analyticsData.trade_levels.stop_loss && (
                              <div className="space-y-2">
                                <span className="text-2xs font-bold text-amber-300">ATR Trade Plan</span>
                                <div className="grid grid-cols-3 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-center">
                                  <div>
                                    <p className="text-3xs text-slate-500">Entry</p>
                                    <p className="text-xs font-bold text-slate-100">₹{analyticsData.trade_levels.entry}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Stop-Loss</p>
                                    <p className="text-xs font-bold text-red-400">₹{analyticsData.trade_levels.stop_loss}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Target</p>
                                    <p className="text-xs font-bold text-emerald-400">₹{analyticsData.trade_levels.target}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Risk:Reward</p>
                                    <p className="text-xs font-bold text-amber-300">1:{analyticsData.trade_levels.risk_reward}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">ATR (14)</p>
                                    <p className="text-xs font-bold text-slate-300">{analyticsData.trade_levels.atr}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Pivot</p>
                                    <p className="text-xs font-bold text-slate-300">₹{analyticsData.trade_levels.pivot}</p>
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Risk / Trend Metrics */}
                            {analyticsData.risk_metrics && analyticsData.risk_metrics.sharpe !== undefined && (
                              <div className="space-y-2">
                                <span className="text-2xs font-bold text-sky-300">Risk-Adjusted Metrics (2Y)
                                  {analyticsData.trend?.regime && (
                                    <span className="ml-2 text-3xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{analyticsData.trend.regime.replace('_', ' ')}</span>
                                  )}
                                </span>
                                <div className="grid grid-cols-4 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-center">
                                  <div>
                                    <p className="text-3xs text-slate-500">Sharpe</p>
                                    <p className={`text-xs font-bold ${analyticsData.risk_metrics.sharpe >= 1 ? 'text-emerald-400' : analyticsData.risk_metrics.sharpe >= 0 ? 'text-slate-200' : 'text-red-400'}`}>{analyticsData.risk_metrics.sharpe}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Sortino</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.risk_metrics.sortino}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">CAGR</p>
                                    <p className={`text-xs font-bold ${analyticsData.risk_metrics.cagr >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{analyticsData.risk_metrics.cagr}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Volatility</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.risk_metrics.volatility}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Max DD</p>
                                    <p className="text-xs font-bold text-red-400">{analyticsData.risk_metrics.max_drawdown}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Calmar</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.risk_metrics.calmar}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Trend %/yr</p>
                                    <p className={`text-xs font-bold ${(analyticsData.trend?.trend_slope_annual ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{analyticsData.trend?.trend_slope_annual ?? '-'}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Ulcer</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.risk_metrics.ulcer_index}</p>
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Advanced Risk: VaR / beta / alpha / range vols */}
                            {analyticsData.advanced_risk && analyticsData.advanced_risk.var_95_hist !== undefined && (
                              <div className="space-y-2">
                                <span className="text-2xs font-bold text-rose-300">Tail Risk &amp; Benchmark Metrics (2Y)</span>
                                <div className="grid grid-cols-4 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-center">
                                  <div>
                                    <p className="text-3xs text-slate-500">VaR 95%</p>
                                    <p className="text-xs font-bold text-red-400">{analyticsData.advanced_risk.var_95_hist}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">VaR 99%</p>
                                    <p className="text-xs font-bold text-red-400">{analyticsData.advanced_risk.var_99_hist}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">CVaR 95%</p>
                                    <p className="text-xs font-bold text-red-400">{analyticsData.advanced_risk.cvar_95}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Beta</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.beta ?? '-'}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Alpha %/yr</p>
                                    <p className={`text-xs font-bold ${(analyticsData.advanced_risk.alpha ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{analyticsData.advanced_risk.alpha ?? '-'}{analyticsData.advanced_risk.alpha != null ? '%' : ''}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Treynor</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.treynor ?? '-'}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Info Ratio</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.information_ratio ?? '-'}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Omega</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.omega}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Skew</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.skew}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Kurtosis</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.kurtosis}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Tail Ratio</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.tail_ratio}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Gain/Pain</p>
                                    <p className={`text-xs font-bold ${(analyticsData.advanced_risk.gain_to_pain ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{analyticsData.advanced_risk.gain_to_pain}</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Vol Parkinson</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.vol_parkinson}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Vol G-Klass</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.vol_garman_klass}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Vol Yang-Zhang</p>
                                    <p className="text-xs font-bold text-slate-200">{analyticsData.advanced_risk.vol_yang_zhang ?? '-'}{analyticsData.advanced_risk.vol_yang_zhang != null ? '%' : ''}</p>
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Backtest */}
                            {backtestData && !backtestData.error && (
                              <div className="space-y-2">
                                <span className="text-2xs font-bold text-violet-300">Model Backtest (+1% signal)</span>
                                <div className="grid grid-cols-3 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-center">
                                  <div>
                                    <p className="text-3xs text-slate-500">Hit Rate</p>
                                    <p className="text-xs font-bold text-emerald-400">{backtestData.signal_hit_rate}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Edge vs Base</p>
                                    <p className={`text-xs font-bold ${backtestData.edge_vs_base >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{backtestData.edge_vs_base > 0 ? '+' : ''}{backtestData.edge_vs_base}%</p>
                                  </div>
                                  <div>
                                    <p className="text-3xs text-slate-500">Accuracy</p>
                                    <p className="text-xs font-bold text-slate-200">{backtestData.directional_accuracy}%</p>
                                  </div>
                                </div>
                                <p className="text-3xs text-slate-500 italic text-center">{backtestData.signals_fired} signals over {backtestData.samples_evaluated} sessions · base rate {backtestData.base_rate}%</p>
                              </div>
                            )}

                            {/* Detected Breakout Patterns */}
                            {stockBreakouts && (
                              <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                  <span className="text-2xs font-bold text-fuchsia-300 flex items-center gap-1">
                                    <Rocket className="w-3 h-3" /> Breakout Patterns
                                  </span>
                                  {stockBreakouts.readiness != null && (
                                    <span className={`text-2xs font-bold ${stockBreakouts.readiness >= 70 ? 'text-fuchsia-300' : stockBreakouts.readiness >= 50 ? 'text-amber-300' : 'text-slate-400'}`}>
                                      Readiness {stockBreakouts.readiness}/100
                                    </span>
                                  )}
                                </div>
                                {stockBreakouts.nearest_breakout != null && (
                                  <div className="grid grid-cols-2 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-center">
                                    <div>
                                      <p className="text-3xs text-slate-500">Nearest Breakout</p>
                                      <p className="text-xs font-bold text-slate-200">₹{stockBreakouts.nearest_breakout?.toFixed(1)}</p>
                                    </div>
                                    <div>
                                      <p className="text-3xs text-slate-500">Distance</p>
                                      <p className="text-xs font-bold text-fuchsia-300">{stockBreakouts.pct_to_breakout > 0 ? '+' : ''}{stockBreakouts.pct_to_breakout}%</p>
                                    </div>
                                  </div>
                                )}
                                {stockBreakouts.patterns && stockBreakouts.patterns.length > 0 ? (
                                  <div className="space-y-1.5">
                                    {stockBreakouts.patterns.map((p: any, i: number) => (
                                      <div key={i} className="bg-slate-950/30 border border-slate-800/50 rounded-2xl p-2.5 text-3xs">
                                        <div className="flex items-center gap-1.5 mb-1">
                                          <span className={`px-1 py-0.5 rounded font-bold ${p.signal === 'BULLISH' ? 'bg-emerald-900/50 text-emerald-300' : p.signal === 'BEARISH' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>{p.signal}</span>
                                          <span className="font-bold text-slate-200">{p.label}</span>
                                          <span className={`ml-auto px-1 py-0.5 rounded font-bold ${p.status === 'BREAKOUT' ? 'bg-fuchsia-900/50 text-fuchsia-300' : p.status === 'READY' ? 'bg-emerald-900/40 text-emerald-300' : 'bg-slate-800 text-slate-400'}`}>{p.status}</span>
                                        </div>
                                        <div className="flex items-center gap-3 text-slate-400 mb-1">
                                          <span>Conf <span className="font-bold text-slate-200">{p.confidence}%</span></span>
                                          {p.breakout_level != null && <span>B/O <span className="font-bold text-slate-200">₹{p.breakout_level?.toFixed(1)}</span></span>}
                                          {p.stop != null && <span>Stop <span className="font-bold text-red-300">₹{p.stop?.toFixed(1)}</span></span>}
                                        </div>
                                        {p.advantage && <p className="text-emerald-400/80"><span className="text-slate-500">+ </span>{p.advantage}</p>}
                                        {p.disadvantage && <p className="text-red-400/70"><span className="text-slate-500">− </span>{p.disadvantage}</p>}
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <p className="text-3xs text-slate-500 italic text-center">No active breakout patterns detected.</p>
                                )}
                              </div>
                            )}

                            {/* Extra Technical Indicators */}
                            {extraIndicators?.indicators && (() => {
                              const ix = extraIndicators.indicators;
                              const tag = (v: string) => {
                                const bull = ['BULLISH', 'ACCUMULATION', 'UP', 'LONG', 'UPTREND', 'GREEN'].includes(v);
                                const bear = ['BEARISH', 'DISTRIBUTION', 'DOWN', 'SHORT', 'DOWNTREND', 'RED'].includes(v);
                                return <span className={`px-1 py-0.5 rounded font-bold ${bull ? 'bg-emerald-900/50 text-emerald-300' : bear ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>{v}</span>;
                              };
                              return (
                                <div className="space-y-2">
                                  <span className="text-2xs font-bold text-cyan-300">Extra Indicators</span>
                                  <div className="grid grid-cols-2 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-3xs">
                                    <div className="flex justify-between"><span className="text-slate-500">Parabolic SAR</span><span>₹{ix.parabolic_sar.value} {tag(ix.parabolic_sar.trend)}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">CMF (20)</span><span>{ix.cmf.value} {tag(ix.cmf.signal)}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">Aroon Osc</span><span className={`font-bold ${ix.aroon.oscillator >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{ix.aroon.oscillator}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">Vortex</span><span>{ix.vortex.vi_plus}/{ix.vortex.vi_minus} {tag(ix.vortex.signal)}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">TRIX (15)</span><span>{ix.trix.value} {tag(ix.trix.signal)}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">TTM Squeeze</span><span className={ix.ttm_squeeze.squeeze_on ? 'text-amber-300 font-bold' : 'text-slate-400'}>{ix.ttm_squeeze.squeeze_on ? 'ON' : 'OFF'} {tag(ix.ttm_squeeze.momentum_dir)}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">Donchian (20)</span><span className="text-slate-300">₹{ix.donchian.lower}–{ix.donchian.upper}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">Keltner (20)</span><span className="text-slate-300">₹{ix.keltner.lower}–{ix.keltner.upper}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">Heikin-Ashi</span><span>{tag(ix.heikin_ashi.color)} ×{ix.heikin_ashi.streak}</span></div>
                                    <div className="flex justify-between"><span className="text-slate-500">Chandelier</span><span>₹{ix.chandelier_exit.long_stop} {tag(ix.chandelier_exit.bias)}</span></div>
                                  </div>
                                  {ix.fibonacci?.levels && Object.keys(ix.fibonacci.levels).length > 0 && (
                                    <div className="bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-3xs">
                                      <div className="flex items-center justify-between mb-1.5">
                                        <span className="text-slate-400 font-bold">Fibonacci Retracement</span>
                                        <span className="text-slate-500">{ix.fibonacci.direction} · ₹{ix.fibonacci.swing_low}–{ix.fibonacci.swing_high}</span>
                                      </div>
                                      <div className="grid grid-cols-5 gap-1 text-center">
                                        {Object.entries(ix.fibonacci.levels).map(([k, v]: any) => (
                                          <div key={k}>
                                            <p className="text-slate-500">{k}</p>
                                            <p className="font-bold text-slate-200">₹{v}</p>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              );
                            })()}

                            {/* Fundamentals & Valuation */}
                            {fundamentals?.available && (() => {
                              const f = fundamentals;
                              const inr = (v: number) => {
                                if (v == null) return '—';
                                const a = Math.abs(v);
                                if (a >= 1e12) return `₹${(v / 1e12).toFixed(2)}T`;
                                if (a >= 1e9) return `₹${(v / 1e9).toFixed(2)}B`;
                                if (a >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`;
                                return `₹${v.toFixed(0)}`;
                              };
                              const fx = (v: any, s = '') => (v == null ? '—' : `${v}${s}`);
                              const dcf = f.dcf;
                              return (
                                <div className="space-y-2">
                                  <span className="text-2xs font-bold text-amber-300">Fundamentals & Valuation</span>
                                  <div className="grid grid-cols-3 gap-2 bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-3xs">
                                    <div className="flex flex-col"><span className="text-slate-500">P/E</span><span className="font-bold text-slate-200">{fx(f.pe)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">P/B</span><span className="font-bold text-slate-200">{fx(f.pb)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">EPS</span><span className="font-bold text-slate-200">{fx(f.eps)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">ROE</span><span className="font-bold text-emerald-300">{fx(f.roe, '%')}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">ROCE</span><span className="font-bold text-emerald-300">{fx(f.roce, '%')}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">D/E</span><span className="font-bold text-slate-200">{fx(f.debt_to_equity)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">Revenue</span><span className="font-bold text-slate-200">{inr(f.revenue)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">Net Income</span><span className="font-bold text-slate-200">{inr(f.net_income)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">Free Cash Flow</span><span className="font-bold text-slate-200">{inr(f.free_cash_flow)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">Mkt Cap</span><span className="font-bold text-slate-200">{inr(f.market_cap)}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">Rev Growth</span><span className={`font-bold ${(f.revenue_growth ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{fx(f.revenue_growth, '%')}</span></div>
                                    <div className="flex flex-col"><span className="text-slate-500">Div Yield</span><span className="font-bold text-slate-200">{fx(f.dividend_yield, '%')}</span></div>
                                  </div>
                                  {dcf && (
                                    <div className="bg-slate-950/30 border border-amber-800/30 p-3 rounded-2xl text-3xs">
                                      <div className="flex items-center justify-between mb-1.5">
                                        <span className="text-amber-300 font-bold">DCF Intrinsic Value</span>
                                        <span className={`px-1.5 py-0.5 rounded font-bold ${dcf.verdict === 'UNDERVALUED' ? 'bg-emerald-900/50 text-emerald-300' : dcf.verdict === 'OVERVALUED' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>{dcf.verdict.replace('_', ' ')}</span>
                                      </div>
                                      <div className="grid grid-cols-3 gap-2 text-center">
                                        <div><p className="text-slate-500">Fair Value</p><p className="font-bold text-amber-200">₹{dcf.intrinsic_value}</p></div>
                                        <div><p className="text-slate-500">Price</p><p className="font-bold text-slate-200">₹{dcf.current_price}</p></div>
                                        <div><p className="text-slate-500">Upside</p><p className={`font-bold ${dcf.upside_pct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{dcf.upside_pct > 0 ? '+' : ''}{dcf.upside_pct}%</p></div>
                                      </div>
                                      <p className="text-3xs text-slate-600 mt-1.5 text-center">FCF growth {dcf.growth_assumed}% · discount {dcf.discount_rate}%</p>
                                    </div>
                                  )}
                                  {correlation?.correlations?.length > 0 && (
                                    <div className="bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl text-3xs">
                                      <span className="text-slate-400 font-bold">Return Correlation (90d)</span>
                                      <div className="mt-1.5 space-y-1">
                                        {correlation.correlations.slice(0, 6).map((c: any) => (
                                          <div key={c.symbol} className="flex items-center justify-between">
                                            <span className="text-slate-400 truncate max-w-[60%]">{c.symbol}{c.type === 'benchmark' ? ' ★' : ''}</span>
                                            <div className="flex items-center gap-2">
                                              <div className="w-16 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                                <div className={`h-full ${c.correlation >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`} style={{ width: `${Math.abs(c.correlation) * 100}%` }} />
                                              </div>
                                              <span className={`font-bold w-9 text-right ${c.correlation >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{c.correlation}</span>
                                            </div>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                          </div>
                        )}

                      </div>
                    )}
                  </div>
                )}

                {/* 2. KNN SIMILARITY MATCHES TAB */}
                {activeTab === 'similarity' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div>
                        <span className="text-2xs font-bold text-slate-200">Historical Similarity Matches (K-Nearest Neighbors)</span>
                        <p className="text-3xs text-slate-500">Matching multi-variable patterns to find analog days in history.</p>
                      </div>
                      
                      {similarityData && (
                        <div className="flex gap-4 text-xs">
                          <div className="text-right">
                            <span className="block text-3xs text-slate-500">Analog Win Rate</span>
                            <span className="font-bold text-emerald-400">{similarityData.win_rate}%</span>
                          </div>
                          <div className="text-right">
                            <span className="block text-3xs text-slate-500">Exp. Outcome</span>
                            <span className={`font-bold ${similarityData.expected_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {similarityData.expected_return >= 0 ? '+' : ''}{similarityData.expected_return.toFixed(2)}%
                            </span>
                          </div>
                        </div>
                      )}
                    </div>

                    <table className="w-full text-left text-2xs border-collapse">
                      <thead>
                        <tr className="border-b border-slate-800 text-slate-500 font-semibold">
                          <th className="pb-1.5">Historical Date Match</th>
                          <th className="pb-1.5">Closing Price</th>
                          <th className="pb-1.5">Similarity Score</th>
                          <th className="pb-1.5">Next-Day Realized Return</th>
                        </tr>
                      </thead>
                      <tbody>
                        {similarityData?.matches && similarityData.matches.length > 0 ? (
                          similarityData.matches.map((m: any, idx: number) => (
                            <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950/20">
                              <td className="py-2 text-slate-350">{m.date}</td>
                              <td className="py-2 text-slate-400">₹{m.close.toFixed(2)}</td>
                              <td className="py-2 font-semibold text-slate-200">{m.similarity}% match</td>
                              <td className={`py-2 font-bold ${m.next_day_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {m.next_day_return >= 0 ? '+' : ''}{m.next_day_return}%
                              </td>
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td colSpan={4} className="py-4 text-center text-slate-500">No similarity matches found. Run sync to populate historical databases.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* FII/DII FLOWS TAB */}
                {activeTab === 'fii_dii' && (
                  <div className="space-y-4">
                    {fiiDiiData?.summary && (
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="bg-slate-950/40 border border-slate-800/60 p-4 rounded-2xl">
                          <span className="block text-3xs text-slate-400 uppercase font-semibold">FII 5D Net Cash Flow</span>
                          <span className={`text-lg font-bold ${fiiDiiData.summary.fii_net_5d >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {fiiDiiData.summary.fii_net_5d >= 0 ? '+' : ''}₹{fiiDiiData.summary.fii_net_5d.toLocaleString()} Cr
                          </span>
                        </div>
                        <div className="bg-slate-950/40 border border-slate-800/60 p-4 rounded-2xl">
                          <span className="block text-3xs text-slate-400 uppercase font-semibold">DII 5D Net Cash Flow</span>
                          <span className={`text-lg font-bold ${fiiDiiData.summary.dii_net_5d >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {fiiDiiData.summary.dii_net_5d >= 0 ? '+' : ''}₹{fiiDiiData.summary.dii_net_5d.toLocaleString()} Cr
                          </span>
                        </div>
                        <div className="bg-slate-950/40 border border-slate-800/60 p-4 rounded-2xl flex flex-col justify-center">
                          <span className="block text-3xs text-slate-400 uppercase font-semibold">Institutional Tone</span>
                          <span className="text-sm font-bold text-slate-200 mt-1">
                            <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                              fiiDiiData.summary.sentiment.includes('BULLISH') ? 'bg-emerald-950/40 text-emerald-400 border border-emerald-500/20' :
                              fiiDiiData.summary.sentiment.includes('BEARISH') ? 'bg-red-950/40 text-red-400 border border-red-500/20' :
                              'bg-slate-900 text-slate-450 border border-slate-700/30'
                            }`}>
                              {fiiDiiData.summary.sentiment.replace('_', ' ')}
                            </span>
                          </span>
                        </div>
                      </div>
                    )}

                    <div className="max-h-72 overflow-y-auto pr-1">
                      <table className="w-full text-left text-2xs border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-500 font-semibold">
                            <th className="pb-2">Date</th>
                            <th className="pb-2">FII Cash Net</th>
                            <th className="pb-2">DII Cash Net</th>
                            <th className="pb-2">Index Futures</th>
                            <th className="pb-2">Index Options</th>
                            <th className="pb-2">Stock Futures</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fiiDiiData?.activities && fiiDiiData.activities.length > 0 ? (
                            fiiDiiData.activities.map((a: any, idx: number) => (
                              <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950/20">
                                <td className="py-2 text-slate-350">{a.date}</td>
                                <td className={`py-2 font-semibold ${a.fii_cash >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                  {a.fii_cash >= 0 ? '+' : ''}{a.fii_cash.toFixed(1)} Cr
                                </td>
                                <td className={`py-2 font-semibold ${a.dii_cash >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                  {a.dii_cash >= 0 ? '+' : ''}{a.dii_cash.toFixed(1)} Cr
                                </td>
                                <td className={`py-2 ${a.fii_index_futures >= 0 ? 'text-emerald-450' : 'text-red-450'}`}>
                                  {a.fii_index_futures >= 0 ? '+' : ''}{a.fii_index_futures.toFixed(1)}
                                </td>
                                <td className={`py-2 ${a.fii_index_options >= 0 ? 'text-emerald-450' : 'text-red-450'}`}>
                                  {a.fii_index_options >= 0 ? '+' : ''}{a.fii_index_options.toFixed(1)}
                                </td>
                                <td className={`py-2 ${a.fii_stock_futures >= 0 ? 'text-emerald-450' : 'text-red-450'}`}>
                                  {a.fii_stock_futures >= 0 ? '+' : ''}{a.fii_stock_futures.toFixed(1)}
                                </td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={6} className="py-4 text-center text-slate-500">No FII/DII flow records found.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* PROMOTER INSIDER TRADES TAB */}
                {activeTab === 'insider_trades' && (
                  <div className="space-y-4">
                    {insiderTrades?.summary && (
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="bg-slate-950/40 border border-slate-800/60 p-4 rounded-2xl">
                          <span className="block text-3xs text-slate-400 uppercase font-semibold">Total Promoter Buy (90D)</span>
                          <span className="text-md font-bold text-emerald-400">
                            ₹{(insiderTrades.summary.total_bought_90d / 10000000).toFixed(2)} Cr
                          </span>
                        </div>
                        <div className="bg-slate-950/40 border border-slate-800/60 p-4 rounded-2xl">
                          <span className="block text-3xs text-slate-400 uppercase font-semibold">Total Promoter Sell (90D)</span>
                          <span className="text-md font-bold text-red-400">
                            ₹{(insiderTrades.summary.total_sold_90d / 10000000).toFixed(2)} Cr
                          </span>
                        </div>
                        <div className="bg-slate-950/40 border border-slate-800/60 p-4 rounded-2xl">
                          <span className="block text-3xs text-slate-400 uppercase font-semibold">Net Signal</span>
                          <span className={`text-xs font-bold inline-block px-2 py-0.5 rounded-full mt-1 ${
                            insiderTrades.summary.signal.includes('BULLISH') ? 'bg-emerald-950/40 text-emerald-400 border border-emerald-500/20' :
                            insiderTrades.summary.signal.includes('BEARISH') ? 'bg-red-950/40 text-red-400 border border-red-500/20' :
                            'bg-slate-900 text-slate-450 border border-slate-700/30'
                          }`}>
                            {insiderTrades.summary.signal.replace('_', ' ')}
                          </span>
                        </div>
                      </div>
                    )}

                    <div className="max-h-72 overflow-y-auto pr-1">
                      <table className="w-full text-left text-2xs border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-500 font-semibold">
                            <th className="pb-2">Date</th>
                            <th className="pb-2">Acquirer Name</th>
                            <th className="pb-2">Category</th>
                            <th className="pb-2">Type</th>
                            <th className="pb-2 text-right">Quantity</th>
                            <th className="pb-2 text-right">Value (INR)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {insiderTrades?.trades && insiderTrades.trades.length > 0 ? (
                            insiderTrades.trades.map((t: any, idx: number) => (
                              <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-950/20">
                                <td className="py-2 text-slate-450">{t.date}</td>
                                <td className="py-2 font-medium text-slate-200 truncate max-w-[150px]">{t.acquirer}</td>
                                <td className="py-2 text-slate-400">{t.category}</td>
                                <td className="py-2">
                                  <span className={`px-1.5 py-0.5 rounded text-3xs font-bold ${t.type === 'BUY' ? 'bg-emerald-950/40 text-emerald-400' : 'bg-red-950/40 text-red-400'}`}>
                                    {t.type}
                                  </span>
                                </td>
                                <td className="py-2 text-right text-slate-300 font-mono">{t.quantity.toLocaleString()}</td>
                                <td className="py-2 text-right font-bold text-slate-200">₹{(t.value / 100000).toFixed(2)} L</td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={6} className="py-4 text-center text-slate-500">No promoter transactions recorded.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* OPTIONS CHAIN TAB */}
                {activeTab === 'options_chain' && (
                  <div className="space-y-4">
                    {optionsChain && (
                      <div className="grid grid-cols-2 md:grid-cols-6 gap-4 bg-slate-950/30 border border-slate-800/50 p-3.5 rounded-2xl">
                        <div className="flex flex-col">
                          <span className="text-3xs text-slate-400 uppercase font-semibold">Underlying Close</span>
                          <span className="text-sm font-bold text-slate-200">₹{optionsChain.underlying_price}</span>
                        </div>
                        <div className="flex flex-col">
                          <span className="text-3xs text-slate-400 uppercase font-semibold">Put-Call Ratio (PCR)</span>
                          <span className={`text-sm font-bold ${optionsChain.pcr > 1.0 ? 'text-emerald-450' : optionsChain.pcr < 0.7 ? 'text-red-450' : 'text-yellow-450'}`}>
                            {optionsChain.pcr} ({optionsChain.pcr > 1.0 ? 'Bullish' : optionsChain.pcr < 0.7 ? 'Bearish' : 'Neutral'})
                          </span>
                        </div>
                        <div className="flex flex-col">
                          <span className="text-3xs text-slate-400 uppercase font-semibold">Max Pain Strike</span>
                          <span className="text-sm font-bold text-yellow-400">₹{optionsChain.max_pain}</span>
                        </div>
                        <div className="flex flex-col">
                          <span className="text-3xs text-slate-400 uppercase font-semibold">Total OI (C / P)</span>
                          <span className="text-sm font-semibold text-slate-350">
                            {(optionsChain.total_call_oi / 100000).toFixed(1)}L / {(optionsChain.total_put_oi / 100000).toFixed(1)}L
                          </span>
                        </div>
                        {optionsChain.base_iv != null && (
                          <div className="flex flex-col">
                            <span className="text-3xs text-slate-400 uppercase font-semibold">Base IV (30D HV)</span>
                            <span className="text-sm font-bold text-sky-300">{optionsChain.base_iv}%</span>
                          </div>
                        )}
                        {optionsChain.days_to_expiry != null && (
                          <div className="flex flex-col">
                            <span className="text-3xs text-slate-400 uppercase font-semibold">Days to Expiry</span>
                            <span className="text-sm font-bold text-slate-200">{optionsChain.days_to_expiry}d</span>
                          </div>
                        )}
                      </div>
                    )}

                    {/* ATM Black-Scholes Greeks */}
                    {optionsChain?.strikes && optionsChain.atm_strike != null && (() => {
                      const atmRow = optionsChain.strikes.find((s: any) => s.strike === optionsChain.atm_strike) || optionsChain.strikes[Math.floor(optionsChain.strikes.length / 2)];
                      if (!atmRow) return null;
                      const G = ({ label, c, p, fmt }: any) => (
                        <div className="flex flex-col items-center">
                          <span className="text-3xs text-slate-500 uppercase">{label}</span>
                          <span className="text-2xs font-bold text-emerald-400">{fmt(c)}</span>
                          <span className="text-2xs font-bold text-red-400">{fmt(p)}</span>
                        </div>
                      );
                      const n = (x: number) => (x >= 0 ? '+' : '') + x.toFixed(3);
                      return (
                        <div className="bg-slate-950/30 border border-slate-800/50 p-3 rounded-2xl space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-2xs font-bold text-sky-300">ATM Greeks @ ₹{optionsChain.atm_strike} (Black-Scholes)</span>
                            <span className="text-3xs text-slate-500"><span className="text-emerald-400">●</span> Call <span className="text-red-400 ml-1">●</span> Put</span>
                          </div>
                          <div className="grid grid-cols-6 gap-2 text-center">
                            <G label="Delta" c={atmRow.call.delta} p={atmRow.put.delta} fmt={n} />
                            <G label="Gamma" c={atmRow.call.gamma} p={atmRow.put.gamma} fmt={(x: number) => x.toFixed(5)} />
                            <G label="Theta/day" c={atmRow.call.theta} p={atmRow.put.theta} fmt={n} />
                            <G label="Vega/1%" c={atmRow.call.vega} p={atmRow.put.vega} fmt={(x: number) => x.toFixed(3)} />
                            <G label="Rho/1%" c={atmRow.call.rho} p={atmRow.put.rho} fmt={n} />
                            <G label="IV" c={atmRow.call.iv} p={atmRow.put.iv} fmt={(x: number) => x.toFixed(1) + '%'} />
                          </div>
                        </div>
                      );
                    })()}

                    <div className="max-h-80 overflow-y-auto pr-1">
                      <table className="w-full text-center text-3xs border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-500 font-semibold bg-slate-950/20">
                            <th colSpan={3} className="py-1 border-r border-slate-800 text-emerald-450">CALLS</th>
                            <th className="py-1"></th>
                            <th colSpan={3} className="py-1 border-l border-slate-800 text-red-450">PUTS</th>
                          </tr>
                          <tr className="border-b border-slate-800 text-slate-500 font-semibold">
                            <th className="py-1.5">OI</th>
                            <th className="py-1.5">OI Change</th>
                            <th className="py-1.5 border-r border-slate-800">LTP (Rs)</th>
                            <th className="py-1.5 bg-slate-900/40 text-slate-200">Strike Price</th>
                            <th className="py-1.5 border-l border-slate-800">LTP (Rs)</th>
                            <th className="py-1.5">OI Change</th>
                            <th className="py-1.5">OI</th>
                          </tr>
                        </thead>
                        <tbody>
                          {optionsChain?.strikes && optionsChain.strikes.length > 0 ? (
                            optionsChain.strikes.map((s: any, idx: number) => {
                              const isCallItm = s.strike < optionsChain.underlying_price;
                              const isPutItm = s.strike > optionsChain.underlying_price;
                              const isAtm = Math.abs(s.strike - optionsChain.underlying_price) <= (optionsChain.strikes[1].strike - optionsChain.strikes[0].strike) / 2.0;

                              return (
                                <tr key={idx} className="border-b border-slate-800/40 hover:bg-slate-900/10">
                                  {/* Call side */}
                                  <td className={`py-1.5 font-mono ${isCallItm ? 'bg-emerald-950/10' : ''}`}>{s.call.oi.toLocaleString()}</td>
                                  <td className={`py-1.5 font-mono ${isCallItm ? 'bg-emerald-950/10' : ''} ${s.call.oi_change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                    {s.call.oi_change >= 0 ? '+' : ''}{s.call.oi_change.toLocaleString()}
                                  </td>
                                  <td className={`py-1.5 font-bold border-r border-slate-800/80 ${isCallItm ? 'bg-emerald-950/15 text-emerald-300' : 'text-slate-350'}`}>
                                    ₹{s.call.ltp.toFixed(1)}
                                  </td>
                                  
                                  {/* Strike price */}
                                  <td className={`py-1.5 font-bold text-xs bg-slate-900/60 ${isAtm ? 'text-yellow-400 border-x border-yellow-500/30' : 'text-slate-200'}`}>
                                    {s.strike}
                                  </td>

                                  {/* Put side */}
                                  <td className={`py-1.5 font-bold border-l border-slate-800/80 ${isPutItm ? 'bg-red-950/15 text-red-300' : 'text-slate-350'}`}>
                                    ₹{s.put.ltp.toFixed(1)}
                                  </td>
                                  <td className={`py-1.5 font-mono ${isPutItm ? 'bg-red-950/10' : ''} ${s.put.oi_change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                    {s.put.oi_change >= 0 ? '+' : ''}{s.put.oi_change.toLocaleString()}
                                  </td>
                                  <td className={`py-1.5 font-mono ${isPutItm ? 'bg-red-950/10' : ''}`}>{s.put.oi.toLocaleString()}</td>
                                </tr>
                              );
                            })
                          ) : (
                            <tr>
                              <td colSpan={7} className="py-4 text-center text-slate-500">No option strikes available.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* 3. NEWS SENTIMENT TAB */}
                {activeTab === 'news' && (
                  <div className="space-y-3">
                    <span className="text-2xs font-bold text-slate-200">Stock News Headlines & Sentiment Score</span>
                    
                    <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                      {newsData.length > 0 ? (
                        newsData.map((n: any, idx: number) => (
                          <div key={idx} className="bg-slate-950/30 border border-slate-800/60 p-3 rounded-2xl flex flex-col gap-1.5 hover:border-slate-800 transition-all">
                            <div className="flex justify-between items-start gap-3">
                              <a 
                                href={n.url} 
                                target="_blank" 
                                rel="noreferrer" 
                                className="text-xs font-semibold text-slate-250 hover:text-emerald-450 hover:underline transition-all"
                              >
                                {n.title}
                              </a>
                              <span className={`text-3xs font-semibold px-2 py-0.5 rounded-md shrink-0 border ${
                                n.sentiment_class === 'POSITIVE' ? 'bg-emerald-950/20 text-emerald-400 border-emerald-500/25' :
                                n.sentiment_class === 'NEGATIVE' ? 'bg-red-950/20 text-red-400 border-red-500/25' :
                                'bg-slate-900 text-slate-400 border-slate-700/30'
                              }`}>
                                {n.sentiment_class} ({n.sentiment_score >= 0 ? '+' : ''}{n.sentiment_score.toFixed(2)})
                              </span>
                            </div>
                            <div className="flex gap-3 text-3xs text-slate-500">
                              <span>Source: {n.source}</span>
                              <span>•</span>
                              <span>Published: {new Date(n.published_at).toLocaleString()}</span>
                            </div>
                          </div>
                        ))
                      ) : (
                        <p className="text-2xs text-slate-500 py-6 text-center">No news articles found for this ticker.</p>
                      )}
                    </div>
                  </div>
                )}

                {/* 4. AI ANALYST TAB */}
                {activeTab === 'ai' && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div>
                        <span className="text-2xs font-bold text-slate-200">AI-Generated Analysis Report</span>
                        <p className="text-3xs text-slate-500">Synthesizes short/long term trends, indicators, and risk metrics.</p>
                      </div>
                      
                      {!aiReport && (
                        <button
                          onClick={handleLoadAiReport}
                          disabled={loadingAi}
                          className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-slate-950 text-2xs font-bold rounded-lg shadow-lg flex items-center gap-1.5 transition-all"
                        >
                          {loadingAi ? <RefreshCw className="animate-spin w-3.5 h-3.5" /> : <Cpu className="w-3.5 h-3.5" />}
                          Generate Report
                        </button>
                      )}
                    </div>

                    {loadingAi ? (
                      <div className="py-12 flex flex-col items-center justify-center gap-2">
                        <RefreshCw className="animate-spin text-emerald-500 w-8 h-8" />
                        <span className="text-2xs text-slate-400 font-medium animate-pulse">Running quantitative LLM synthesis...</span>
                      </div>
                    ) : (
                      <div className="bg-slate-950/20 border border-slate-800/50 rounded-2xl p-4 max-h-96 overflow-y-auto pr-2">
                        {aiReport ? (
                          renderMarkdown(aiReport)
                        ) : (
                          <div className="py-8 text-center text-slate-500 flex flex-col items-center justify-center gap-2">
                            <Shield className="w-10 h-10 text-slate-700" />
                            <p className="text-2xs">Click "Generate Report" to build a detailed data-driven analysis summary.</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

              </div>
            )}
          </div>
        </div>

        {/* RIGHT PANEL: STOCK SCREENER */}
        <div className="xl:col-span-1">
          {/* Market Pulse Panel */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-3 mb-6">
            <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
              <Activity className="text-sky-400 w-4 h-4" />
              <h2 className="text-sm font-bold">Market Pulse</h2>
              {marketBreadth?.sentiment && (
                <span className={`ml-auto px-2 py-0.5 rounded-lg text-2xs font-bold ${marketBreadth.sentiment === 'RISK_ON' ? 'bg-emerald-900/50 text-emerald-300' : marketBreadth.sentiment === 'RISK_OFF' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>{marketBreadth.sentiment.replace('_', ' ')}</span>
              )}
            </div>
            {marketBreadth ? (
              <>
                <div className="flex items-center gap-1 h-2 rounded-full overflow-hidden bg-slate-800">
                  <div className="h-full bg-emerald-500" style={{ width: `${marketBreadth.pct_advancing}%` }} />
                  <div className="h-full bg-red-500/80 flex-1" />
                </div>
                <div className="flex justify-between text-3xs">
                  <span className="text-emerald-400 font-bold">▲ {marketBreadth.advances} adv</span>
                  <span className="text-slate-500">A/D {marketBreadth.ad_ratio}</span>
                  <span className="text-red-400 font-bold">{marketBreadth.declines} dec ▼</span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-3xs">
                  <div className="bg-slate-950/40 rounded-xl p-2"><span className="text-slate-500">% &gt; EMA50</span><p className="font-bold text-slate-200">{marketBreadth.pct_above_ema50}%</p></div>
                  <div className="bg-slate-950/40 rounded-xl p-2"><span className="text-slate-500">% &gt; EMA200</span><p className="font-bold text-slate-200">{marketBreadth.pct_above_ema200}%</p></div>
                  <div className="bg-slate-950/40 rounded-xl p-2"><span className="text-slate-500">Avg RSI</span><p className="font-bold text-slate-200">{marketBreadth.avg_rsi ?? '—'}</p></div>
                  <div className="bg-slate-950/40 rounded-xl p-2"><span className="text-slate-500">52w Hi/Lo</span><p className="font-bold"><span className="text-emerald-300">{marketBreadth.new_highs_52w}</span><span className="text-slate-600"> / </span><span className="text-red-300">{marketBreadth.new_lows_52w}</span></p></div>
                </div>
              </>
            ) : (
              <p className="text-3xs text-slate-500 italic text-center py-2">Loading breadth…</p>
            )}
            {sectorPerf?.leaders?.length > 0 && (
              <div className="border-t border-slate-800 pt-2">
                <span className="text-2xs font-bold text-slate-300">Sector Rotation (20d)</span>
                <div className="mt-1.5 space-y-1">
                  {sectorPerf.leaders.slice(0, 3).map((s: any) => (
                    <div key={s.industry} className="flex items-center justify-between text-3xs">
                      <span className="text-slate-400 truncate max-w-[70%]">▲ {s.industry}</span>
                      <span className="font-bold text-emerald-300">+{s.return_20d}%</span>
                    </div>
                  ))}
                  {sectorPerf.laggards.slice(0, 3).map((s: any) => (
                    <div key={s.industry} className="flex items-center justify-between text-3xs">
                      <span className="text-slate-400 truncate max-w-[70%]">▼ {s.industry}</span>
                      <span className={`font-bold ${s.return_20d >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{s.return_20d > 0 ? '+' : ''}{s.return_20d}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Strategies — moved to a dedicated page */}
          <Link
            href="/strategies"
            className="group w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 hover:border-amber-600/50 rounded-2xl shadow-xl p-5 flex items-center gap-4 mb-6 transition-all"
          >
            <span className="flex-shrink-0 w-11 h-11 flex items-center justify-center rounded-xl bg-amber-500/15 border border-amber-500/30">
              <BarChart3 className="text-amber-400 w-5 h-5" />
            </span>
            <div className="flex-1 min-w-0">
              <h2 className="text-sm font-bold text-slate-100">Strategies &amp; Daily Picks</h2>
              <p className="text-3xs text-slate-500 mt-0.5">Top‑5 high‑probability picks, exit‑rule siblings (−6% stop, 2% trail, 3:20 PM exit) &amp; live scorecards — all on one page.</p>
            </div>
            <ArrowRight className="flex-shrink-0 w-4 h-4 text-slate-500 group-hover:text-amber-400 group-hover:translate-x-0.5 transition-all" />
          </Link>

          {/* Delta System — link to live data page */}
          <Link
            href="/delta"
            className="group w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 hover:border-violet-600/50 rounded-2xl shadow-xl p-5 flex items-center gap-4 mb-6 transition-all"
          >
            <span className="flex-shrink-0 w-11 h-11 flex items-center justify-center rounded-xl bg-violet-500/15 border border-violet-500/30">
              <Activity className="text-violet-400 w-5 h-5" />
            </span>
            <div className="flex-1 min-w-0">
              <h2 className="text-sm font-bold text-slate-100">Delta System · Live</h2>
              <p className="text-3xs text-slate-500 mt-0.5">Kite Connect · 400 symbols · Buy/Sell Delta · Cumulative Delta · VWAP · OBI · Prediction Score · Daily EOD results</p>
            </div>
            <ArrowRight className="flex-shrink-0 w-4 h-4 text-slate-500 group-hover:text-violet-400 group-hover:translate-x-0.5 transition-all" />
          </Link>

          {/* Breakout Scanner Panel */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-3 mb-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <Rocket className="text-fuchsia-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Breakout Scanner</h2>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setShowCatalog(v => !v)}
                  className="text-3xs text-slate-400 hover:text-fuchsia-300 transition-all"
                  title="Pattern guide"
                >
                  {showCatalog ? 'Hide guide' : 'Pattern guide'}
                </button>
                <button
                  onClick={fetchBreakoutReady}
                  disabled={breakoutLoading}
                  className="text-slate-400 hover:text-fuchsia-400 disabled:opacity-50 transition-all"
                  title="Rescan"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${breakoutLoading ? 'animate-spin text-fuchsia-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              Stocks coiled &amp; ready to break out, ranked by readiness (VCP, Darvas Box, squeeze, triangles, flags).
            </p>

            {/* Pattern name filter */}
            {breakoutReady.length > 0 && (() => {
              const patterns = Array.from(new Set(breakoutReady.map((b) => b.top_pattern).filter(Boolean))).sort();
              const matchCount = breakoutFilter === 'all'
                ? breakoutReady.length
                : breakoutReady.filter((b) => b.top_pattern === breakoutFilter).length;
              return (
                <div className="flex items-center gap-2">
                  <Filter className="text-fuchsia-400/70 w-3 h-3 flex-shrink-0" />
                  <select
                    value={breakoutFilter}
                    onChange={(e) => setBreakoutFilter(e.target.value)}
                    className="flex-1 bg-slate-950/60 border border-slate-800 rounded-lg px-2 py-1 text-3xs text-slate-200 focus:outline-none focus:border-fuchsia-500/50"
                  >
                    <option value="all">All patterns ({breakoutReady.length})</option>
                    {patterns.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                  <span className="text-3xs text-slate-500 flex-shrink-0">{matchCount} match{matchCount === 1 ? '' : 'es'}</span>
                </div>
              );
            })()}

            {/* Pattern guide (advantage / disadvantage) */}
            {showCatalog && (
              <div className="max-h-72 overflow-y-auto pr-1 space-y-1.5 bg-slate-950/40 border border-slate-800/50 rounded-2xl p-2">
                {catalog.map((p) => (
                  <div key={p.pattern} className="text-3xs border-b border-slate-800/50 pb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className={`px-1 py-0.5 rounded text-3xs font-bold ${p.signal === 'BULLISH' ? 'bg-emerald-900/50 text-emerald-300' : p.signal === 'BEARISH' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>{p.signal}</span>
                      <span className="font-bold text-slate-200">{p.label}</span>
                      <span className="text-slate-600 ml-auto uppercase">{p.category}</span>
                    </div>
                    <p className="text-emerald-400/80 mt-0.5"><span className="text-slate-500">+ </span>{p.advantage}</p>
                    <p className="text-red-400/70"><span className="text-slate-500">− </span>{p.disadvantage}</p>
                  </div>
                ))}
              </div>
            )}

            <div className="flex flex-col gap-1.5 max-h-[26rem] overflow-y-auto pr-1">
              {breakoutLoading ? (
                <div className="py-8 flex items-center justify-center">
                  <RefreshCw className="animate-spin text-fuchsia-500 w-5 h-5" />
                </div>
              ) : (
                breakoutReady
                  .filter((b) => breakoutFilter === 'all' || b.top_pattern === breakoutFilter)
                  .map((b) => (
                  <button
                    key={b.symbol}
                    onClick={() => setSelectedSymbol(b.symbol)}
                    className="flex items-center gap-2 text-2xs p-2 rounded-2xl hover:bg-slate-800/40 border border-transparent hover:border-slate-700/50 transition-all text-left"
                  >
                    <div className="flex flex-col items-center justify-center w-9 flex-shrink-0">
                      <span className={`text-sm font-extrabold ${b.readiness >= 80 ? 'text-fuchsia-300' : b.readiness >= 65 ? 'text-amber-300' : 'text-slate-300'}`}>{b.readiness}</span>
                      <span className="text-3xs text-slate-600">ready</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-slate-200">{b.symbol}</span>
                        <span className={`px-1 py-0.5 rounded text-3xs font-bold ${b.signal === 'BULLISH' ? 'bg-emerald-900/50 text-emerald-300' : b.signal === 'BEARISH' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>{b.signal}</span>
                      </div>
                      <span className="text-3xs text-fuchsia-300/90 truncate block font-semibold">{b.top_pattern}</span>
                      <span className="text-3xs text-slate-500 block">
                        B/O ₹{b.nearest_breakout != null ? b.nearest_breakout.toFixed(1) : '—'} · {b.confidence}% conf
                      </span>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <span className="font-bold text-slate-200 block">₹{b.price?.toFixed(1)}</span>
                      <span className={`text-3xs font-semibold ${b.status === 'BREAKOUT' ? 'text-fuchsia-300' : b.status === 'READY' ? 'text-emerald-400' : 'text-slate-400'}`}>{b.status}</span>
                    </div>
                    {b.pct_to_breakout != null && (
                      <div className="flex-shrink-0 w-12 text-right">
                        <span className="text-3xs text-slate-500 block">to b/o</span>
                        <span className={`text-3xs font-bold ${b.signal === 'BULLISH' ? 'text-emerald-300' : 'text-slate-300'}`}>{b.pct_to_breakout > 0 ? '+' : ''}{b.pct_to_breakout}%</span>
                      </div>
                    )}
                  </button>
                ))
              )}
              {!breakoutLoading && breakoutReady.length === 0 && (
                <p className="text-3xs text-slate-500 text-center py-4">No breakout setups detected right now.</p>
              )}
              {!breakoutLoading && breakoutReady.length > 0 && breakoutFilter !== 'all' &&
                breakoutReady.filter((b) => b.top_pattern === breakoutFilter).length === 0 && (
                <p className="text-3xs text-slate-500 text-center py-4">No stocks match this pattern right now.</p>
              )}
            </div>
          </div>

          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
              <Filter className="text-emerald-400 w-4 h-4" />
              <h2 className="text-sm font-bold">Stock Screener</h2>
            </div>

            {/* Filter Inputs */}
            <div className="space-y-4">
              {/* Max PE Slider */}
              <div>
                <div className="flex justify-between text-2xs text-slate-400 mb-1">
                  <span>Max P/E Ratio</span>
                  <span className="font-bold text-slate-200">{peFilter}</span>
                </div>
                <input
                  type="range"
                  min="5"
                  max="120"
                  value={peFilter}
                  onChange={(e) => setPeFilter(Number(e.target.value))}
                  className="w-full accent-emerald-500 bg-slate-800 h-1 rounded-lg"
                />
              </div>

              {/* Min ROE Slider */}
              <div>
                <div className="flex justify-between text-2xs text-slate-400 mb-1">
                  <span>Min ROE (%)</span>
                  <span className="font-bold text-slate-200">{roeMin}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="40"
                  value={roeMin}
                  onChange={(e) => setRoeMin(Number(e.target.value))}
                  className="w-full accent-emerald-500 bg-slate-800 h-1 rounded-lg"
                />
              </div>

              {/* RSI Range Sliders */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="flex justify-between text-2xs text-slate-400 mb-1">
                    <span>Min RSI</span>
                    <span className="font-bold text-slate-200">{rsiMin}</span>
                  </div>
                  <input
                    type="range"
                    min="10"
                    max="50"
                    value={rsiMin}
                    onChange={(e) => setRsiMin(Number(e.target.value))}
                    className="w-full accent-emerald-500 bg-slate-800 h-1 rounded-lg"
                  />
                </div>
                
                <div>
                  <div className="flex justify-between text-2xs text-slate-400 mb-1">
                    <span>Max RSI</span>
                    <span className="font-bold text-slate-200">{rsiMax}</span>
                  </div>
                  <input
                    type="range"
                    min="50"
                    max="90"
                    value={rsiMax}
                    onChange={(e) => setRsiMax(Number(e.target.value))}
                    className="w-full accent-emerald-500 bg-slate-800 h-1 rounded-lg"
                  />
                </div>
              </div>

              <button
                onClick={handleRunScreener}
                disabled={screenerLoading}
                className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-slate-950 text-xs font-bold rounded-xl shadow-lg transition-all flex items-center justify-center gap-1.5"
              >
                {screenerLoading ? <RefreshCw className="animate-spin w-4 h-4" /> : <Filter className="w-4 h-4" />}
                Scan Market
              </button>
            </div>

            {/* Screener Results table */}
            <div className="border-t border-slate-800 pt-3 flex flex-col gap-2">
              <span className="text-2xs font-bold text-slate-350">Matching Tickers ({screenerResults.length})</span>
              
              <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                {screenerLoading ? (
                  <div className="py-6 flex items-center justify-center">
                    <RefreshCw className="animate-spin text-emerald-500 w-5 h-5" />
                  </div>
                ) : (
                  screenerResults.map((stock) => (
                    <button
                      key={stock.symbol}
                      onClick={() => setSelectedSymbol(stock.symbol)}
                      className={`w-full text-left p-2 border rounded-xl text-xs flex justify-between items-center transition-all ${
                        stock.symbol === selectedSymbol 
                          ? 'bg-emerald-950/20 border-emerald-550/40' 
                          : 'bg-slate-950/30 border-slate-800 hover:border-slate-800'
                      }`}
                    >
                      <div>
                        <span className="font-bold block text-slate-200">{stock.symbol}</span>
                        <span className="text-3xs text-slate-500 truncate max-w-[120px] block">{stock.company_name}</span>
                      </div>
                      <div className="text-right">
                        <span className="font-bold text-slate-200 block">₹{stock.price.toFixed(1)}</span>
                        <span className={`text-3xs font-semibold flex items-center gap-0.5 justify-end ${stock.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {stock.change_pct >= 0 ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />}
                          {stock.change_pct.toFixed(2)}%
                        </span>
                      </div>
                    </button>
                  ))
                )}
                
                {!screenerLoading && screenerResults.length === 0 && (
                  <p className="text-3xs text-slate-500 text-center py-4">No stocks found matching the scan parameters.</p>
                )}
              </div>
            </div>

          </div>
        </div>

      </div>

      {/* FOOTER */}
      <footer className="mt-6 p-4 border-t border-slate-800/60 text-center text-3xs text-slate-500 flex flex-col sm:flex-row items-center justify-between gap-2">
        <span>© 2026 NSE Analytica Platform. All rights reserved.</span>
        <span className="flex items-center gap-1"><Shield className="w-3.5 h-3.5 text-emerald-500" /> Data feeds sourced via standardized yfinance historical endpoints.</span>
      </footer>
    </main>
  );
}

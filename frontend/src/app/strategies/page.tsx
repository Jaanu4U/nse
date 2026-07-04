'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useStockStore, API_BASE } from '../../store/useStockStore';
import {
  TrendingUp, TrendingDown, RefreshCw, Award, Zap, Clock,
  BarChart3, Shield, Target, Timer, ClipboardCheck, ArrowLeft
} from 'lucide-react';

export default function StrategiesPage() {
  const { selectedSymbol, setSelectedSymbol: _setSelectedSymbol } = useStockStore();
  const router = useRouter();

  // Selecting a pick navigates back to the dashboard with that stock active.
  const setSelectedSymbol = (symbol: string) => {
    _setSelectedSymbol(symbol);
    router.push('/');
  };

  // Top high-probability picks states
  const [topPicks, setTopPicks] = useState<any[]>([]);
  const [topPicksLoading, setTopPicksLoading] = useState(false);

  // +2% / +3% possibility scan states
  const [plusTargets, setPlusTargets] = useState<any>(null);
  const [plusTargetsLoading, setPlusTargetsLoading] = useState(false);
  const [plusLive, setPlusLive] = useState(false);

  // Strategy scorecard (Top-5 by P(+3%), realized next-day results)
  const [strategyScore, setStrategyScore] = useState<any>(null);
  const [strategyScoreLoading, setStrategyScoreLoading] = useState(false);
  const [selectedStrategyDate, setSelectedStrategyDate] = useState<string>('');
  const [strategyLive, setStrategyLive] = useState(false);

  // Sibling strategy: identical Top-5 selection, exited with a -6% disaster stop.
  const [stopScore, setStopScore] = useState<any>(null);
  const [stopScoreLoading, setStopScoreLoading] = useState(false);
  const [selectedStopDate, setSelectedStopDate] = useState<string>('');
  const [stopLive, setStopLive] = useState(false);

  // Sibling strategy: identical Top-5 selection, exited with a 2% trailing stop
  // armed after the pick first runs +2% (real 5-min-path backtest).
  const [trailScore, setTrailScore] = useState<any>(null);
  const [trailScoreLoading, setTrailScoreLoading] = useState(false);

  // Intraday "Strategy 3% · 3:20 PM" — live price as close, Top-5 by P(+3%)
  const [intradayStrategy, setIntradayStrategy] = useState<any>(null);
  const [intradayScore, setIntradayScore] = useState<any>(null);
  const [intradayScoreLoading, setIntradayScoreLoading] = useState(false);
  const [selectedIntradayDate, setSelectedIntradayDate] = useState<string>('');
  const [intradayLive, setIntradayLive] = useState(false);

  // Daily picks scorecard / archive states
  const [picksReport, setPicksReport] = useState<any>(null);
  const [picksReportLoading, setPicksReportLoading] = useState(false);
  const [picksDates, setPicksDates] = useState<string[]>([]);
  const [selectedPickDate, setSelectedPickDate] = useState<string>('');
  const [picksLive, setPicksLive] = useState(false);

  // Fetch top high-probability picks
  const fetchTopPicks = async () => {
    setTopPicksLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/top-picks?limit=5`);
      if (res.ok) {
        const data = await res.json();
        setTopPicks(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setTopPicksLoading(false);
    }
  };

  // Load top picks on mount
  useEffect(() => {
    fetchTopPicks();
  }, []);

  // Fetch the +2% / +3% possibility scan (counts + candidate lists)
  const fetchPlusTargets = async (refresh: boolean = false, silent: boolean = false) => {
    if (!silent) setPlusTargetsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/plus-targets?p2_min=40&p3_min=40${refresh ? '&refresh=true' : ''}`);
      if (res.ok) {
        const data = await res.json();
        setPlusTargets(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setPlusTargetsLoading(false);
    }
  };

  // Load +2% / +3% scan on mount. Inside market hours the live-polling effect below is
  // authoritative (fetches with the live overlay), so only do the plain fetch when the
  // market is closed to avoid a race that would wipe the live prices.
  useEffect(() => {
    if (!nseLiveWindow()) fetchPlusTargets();
  }, []);

  // Fetch the strategy scorecard (Top-5 by P(+3%) historical results)
  const fetchStrategyScore = async (refresh: boolean = false, silent: boolean = false) => {
    if (!silent) setStrategyScoreLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/strategy-scorecard?days=30&top_n=5${refresh ? '&refresh=true' : ''}`);
      if (res.ok) {
        const data = await res.json();
        setStrategyScore(data);
        if (data?.days?.length) {
          const preferred = data.days.find((d: any) => d.live)?.pick_date
            || data.days.find((d: any) => !d.pending)?.pick_date
            || data.days[0].pick_date;
          setSelectedStrategyDate((prev: string) => prev || preferred);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setStrategyScoreLoading(false);
    }
  };

  // Load strategy scorecard on mount. During market hours the live-polling effect is
  // authoritative (fetches with refresh so the in-flight day is marked live and becomes
  // the default selection); only do the plain fetch when the market is closed.
  useEffect(() => {
    if (!nseLiveWindow()) fetchStrategyScore();
  }, []);

  // Fetch the SIBLING scorecard: same Top-5, exited with a -6% disaster stop.
  const fetchStopScore = async (refresh: boolean = false, silent: boolean = false) => {
    if (!silent) setStopScoreLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/strategy-scorecard-stop?days=30&top_n=5${refresh ? '&refresh=true' : ''}`);
      if (res.ok) {
        const data = await res.json();
        setStopScore(data);
        if (data?.days?.length) {
          const preferred = data.days.find((d: any) => d.live)?.pick_date
            || data.days.find((d: any) => !d.pending)?.pick_date
            || data.days[0].pick_date;
          setSelectedStopDate((prev: string) => prev || preferred);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setStopScoreLoading(false);
    }
  };

  useEffect(() => {
    if (!nseLiveWindow()) fetchStopScore();
  }, []);

  // Fetch the SIBLING scorecard: same Top-5, exited with a 2% trailing stop (armed +2%).
  // Path-dependent, so served from the real 5-min backtest artifact (no live refresh).
  const fetchTrailScore = async (silent: boolean = false) => {
    if (!silent) setTrailScoreLoading(true);
    try {
      const res = await fetch(`${API_BASE}/screener/strategy-scorecard-trail?days=30&top_n=5`);
      if (res.ok) setTrailScore(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setTrailScoreLoading(false);
    }
  };

  useEffect(() => {
    fetchTrailScore();
  }, []);

  // Fetch the intraday "Strategy 3% · 3:20 PM" today picks + forward scorecard
  const fetchIntradayStrategy = async (refresh: boolean = false, silent: boolean = false) => {
    if (!silent) setIntradayScoreLoading(true);
    try {
      const [todayRes, scoreRes] = await Promise.all([
        fetch(`${API_BASE}/screener/intraday-strategy`),
        fetch(`${API_BASE}/screener/intraday-strategy/scorecard?days=30${refresh ? '&refresh=true' : ''}`),
      ]);
      if (todayRes.ok) setIntradayStrategy(await todayRes.json());
      if (scoreRes.ok) {
        const data = await scoreRes.json();
        setIntradayScore(data);
        if (data?.days?.length) {
          const preferred = data.days.find((d: any) => d.live)?.pick_date
            || data.days.find((d: any) => !d.pending)?.pick_date
            || data.days[0].pick_date;
          setSelectedIntradayDate((prev: string) => prev || preferred);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setIntradayScoreLoading(false);
    }
  };

  // Load intraday strategy on mount. During market hours the live-polling effect is
  // authoritative (refresh marks the in-flight day live -> becomes the default); only do
  // the plain fetch when the market is closed.
  useEffect(() => {
    if (nseLiveWindow()) {
      // Still fetch today's picks list (not the scorecard) so the hero card populates.
      fetch(`${API_BASE}/screener/intraday-strategy`).then(r => r.ok ? r.json() : null).then(d => { if (d) setIntradayStrategy(d); }).catch(() => {});
    } else {
      fetchIntradayStrategy();
    }
  }, []);

  // Live-poll both strategy scorecards during NSE hours so the in-flight day (the picks
  // being held / playing out today) tracks the running market, mirroring Picks Scorecard.
  const nseLiveWindow = () => {
    const fmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date());
    const get = (t: string) => fmt.find(p => p.type === t)?.value ?? '0';
    const minutes = parseInt(get('hour'), 10) * 60 + parseInt(get('minute'), 10);
    return minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 45;
  };

  // Today's NSE trading date (IST), e.g. "18 Jun 2026" — shown on cards so the current
  // day is never confused with the date the picks were generated on.
  const todayISTLabel = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric',
  }).format(new Date());

  useEffect(() => {
    const tick = () => {
      if (nseLiveWindow()) {
        setStrategyLive(true);
        fetchStrategyScore(true, true);
      } else {
        setStrategyLive(false);
      }
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const tick = () => {
      if (nseLiveWindow()) {
        setStopLive(true);
        fetchStopScore(true, true);
      } else {
        setStopLive(false);
      }
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const tick = () => {
      if (nseLiveWindow()) {
        setIntradayLive(true);
        fetchIntradayStrategy(true, true);
      } else {
        setIntradayLive(false);
      }
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, []);

  // Live-poll the Top-5 hold-to-close card so each pick shows its running price vs entry.
  useEffect(() => {
    const tick = () => {
      if (nseLiveWindow()) {
        setPlusLive(true);
        fetchPlusTargets(true, true);
      } else {
        setPlusLive(false);
      }
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, []);

  const fetchPicksReport = async (date?: string, refresh: boolean = false, silent: boolean = false) => {
    if (!silent) setPicksReportLoading(true);
    try {
      const params = new URLSearchParams();
      if (date) params.set('date', date);
      if (refresh) params.set('refresh', 'true');
      const res = await fetch(`${API_BASE}/screener/picks/report?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setPicksReport(data);
        if (data?.pick_date) setSelectedPickDate(data.pick_date);
      }
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setPicksReportLoading(false);
    }
  };

  // Load archived pick dates + most-recent report on mount
  useEffect(() => {
    fetch(`${API_BASE}/screener/picks/dates`)
      .then(r => r.ok ? r.json() : [])
      .then((d: string[]) => {
        setPicksDates(d || []);
        // Default to the most recent archived day ("yesterday's report")
        fetchPicksReport(d && d.length ? d[0] : undefined, false);
      })
      .catch(() => {});
  }, []);

  // Live-update the scorecard while *today's* picks are selected and the market is open.
  // The page otherwise only fetches once, so O/H/L and LTP would appear frozen. Poll a
  // silent refresh (no spinner) every 30s during NSE hours (09:15–15:45 IST) so the
  // running O/H/L, live close (LTP) and win/loss tally stay in sync with the market.
  useEffect(() => {
    if (!selectedPickDate) return;
    const istParts = () => {
      const fmt = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
      }).formatToParts(new Date());
      const get = (t: string) => fmt.find(p => p.type === t)?.value ?? '';
      return {
        date: `${get('year')}-${get('month')}-${get('day')}`,
        minutes: parseInt(get('hour'), 10) * 60 + parseInt(get('minute'), 10),
      };
    };
    const isLiveWindow = () => {
      const { date, minutes } = istParts();
      // Only today's board, only during/just-after the trading session (09:15–15:45 IST).
      return selectedPickDate === date && minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 45;
    };
    if (!isLiveWindow()) { setPicksLive(false); return; }
    setPicksLive(true);
    const id = setInterval(() => {
      if (isLiveWindow()) {
        fetchPicksReport(selectedPickDate, true, true);
      } else {
        setPicksLive(false);
      }
    }, 30000);
    return () => clearInterval(id);
  }, [selectedPickDate]);

  return (
    <main className="min-h-screen bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black text-slate-100 p-6 flex flex-col gap-6">
      {/* HEADER */}
      <header className="w-full max-w-7xl mx-auto flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <span className="flex-shrink-0 w-11 h-11 flex items-center justify-center rounded-xl bg-amber-500/15 border border-amber-500/30">
            <BarChart3 className="text-amber-400 w-5 h-5" />
          </span>
          <div className="min-w-0">
            <h1 className="text-lg font-extrabold tracking-tight text-slate-100">STRATEGIES</h1>
            <p className="text-3xs text-slate-500 truncate">Top‑5 picks, exit‑rule siblings &amp; live scorecards</p>
          </div>
        </div>
        <Link
          href="/"
          className="flex-shrink-0 flex items-center gap-1.5 text-3xs font-semibold text-slate-400 hover:text-amber-400 bg-slate-900/60 border border-slate-800 hover:border-amber-600/50 rounded-lg px-3 py-2 transition-all"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Dashboard
        </Link>
      </header>

      <div className="w-full max-w-7xl mx-auto flex flex-col gap-6">
        {/* Row: Top-5 High-Probability Picks + Picks Scorecard (strategy ↔ its scorecard) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Top 5 High-Probability Picks hero card */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <Award className="text-amber-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Top 5 High-Probability Picks</h2>
              </div>
              <button
                onClick={fetchTopPicks}
                disabled={topPicksLoading}
                className="text-slate-400 hover:text-emerald-400 disabled:opacity-50 transition-all"
                title="Refresh picks"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${topPicksLoading ? 'animate-spin text-emerald-500' : ''}`} />
              </button>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              Ranked by composite ML next-day return probability (XGBoost + LightGBM ensemble).
            </p>

            <div className="flex flex-col gap-1.5 max-h-[28rem] overflow-y-auto pr-1">
              {topPicksLoading ? (
                <div className="py-8 flex items-center justify-center">
                  <RefreshCw className="animate-spin text-emerald-500 w-5 h-5" />
                </div>
              ) : (
                topPicks.map((pick: any) => (
                  <button
                    key={pick.symbol}
                    onClick={() => setSelectedSymbol(pick.symbol)}
                    className={`w-full text-left p-2 border rounded-xl text-xs flex items-center gap-2.5 transition-all ${
                      pick.symbol === selectedSymbol
                        ? 'bg-emerald-950/20 border-emerald-550/40'
                        : 'bg-slate-950/30 border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <span className={`flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-lg text-3xs font-bold ${
                      pick.rank <= 3 ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' : 'bg-slate-800/60 text-slate-400'
                    }`}>
                      {pick.rank}
                    </span>
                    <div className="flex-1 min-w-0">
                      <span className="font-bold block text-slate-200">{pick.symbol}</span>
                      <span className="text-3xs text-slate-500 truncate block">{pick.company_name}</span>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <span className="font-bold text-slate-200 block">₹{pick.price.toFixed(1)}</span>
                      <span className={`text-3xs font-semibold flex items-center gap-0.5 justify-end ${pick.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {pick.change_pct >= 0 ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />}
                        {pick.change_pct.toFixed(2)}%
                      </span>
                    </div>
                    <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-14">
                      <span className="flex items-center gap-0.5 text-3xs font-bold text-emerald-300">
                        <Zap className="w-2.5 h-2.5" />{pick.confidence}%
                      </span>
                      <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                        <div className="h-full bg-emerald-500" style={{ width: `${Math.min(pick.confidence, 100)}%` }} />
                      </div>
                    </div>
                  </button>
                ))
              )}

              {!topPicksLoading && topPicks.length === 0 && (
                <p className="text-3xs text-slate-500 text-center py-4">No prediction data available yet.</p>
              )}
            </div>
          </div>

          {/* Daily Picks Scorecard / Archive Panel */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <ClipboardCheck className="text-sky-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Picks Scorecard</h2>
                {picksLive && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />LIVE
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {picksDates.length > 0 && (
                  <select
                    value={selectedPickDate}
                    onChange={(e) => { setSelectedPickDate(e.target.value); fetchPicksReport(e.target.value, false); }}
                    className="bg-slate-950/60 border border-slate-700 rounded-lg text-3xs text-slate-300 px-1.5 py-1 focus:outline-none focus:border-sky-500"
                    title="Archived pick date"
                  >
                    {picksDates.map((d) => <option key={d} value={d}>{d}</option>)}
                  </select>
                )}
                <button
                  onClick={() => fetchPicksReport(selectedPickDate || undefined, true)}
                  disabled={picksReportLoading}
                  className="text-slate-400 hover:text-sky-400 disabled:opacity-50 transition-all"
                  title="Refresh with today's live prices"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${picksReportLoading ? 'animate-spin text-sky-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              How yesterday&apos;s 25 picks worked out — entry price vs today&apos;s live O/H/L &amp; current price.
            </p>

            {picksReportLoading ? (
              <div className="py-8 flex items-center justify-center">
                <RefreshCw className="animate-spin text-sky-500 w-5 h-5" />
              </div>
            ) : !picksReport || !picksReport.picks || picksReport.picks.length === 0 ? (
              <p className="text-3xs text-slate-500 text-center py-4">
                No archived picks yet. Picks are snapshotted each trading morning and scored after 3:30 PM close.
              </p>
            ) : (
              <>
                {/* Summary scorecard */}
                {picksReport.summary && (
                  <div className="grid grid-cols-4 gap-1.5 text-3xs text-center">
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500">Win rate</span>
                      <p className="font-bold text-emerald-300">{picksReport.summary.win_rate != null ? `${picksReport.summary.win_rate}%` : '—'}</p>
                    </div>
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500">W / L</span>
                      <p className="font-bold"><span className="text-emerald-300">{picksReport.summary.wins}</span><span className="text-slate-600"> / </span><span className="text-red-300">{picksReport.summary.losses}</span></p>
                    </div>
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500">Avg move</span>
                      <p className={`font-bold ${(picksReport.summary.avg_change_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{picksReport.summary.avg_change_pct != null ? `${picksReport.summary.avg_change_pct > 0 ? '+' : ''}${picksReport.summary.avg_change_pct}%` : '—'}</p>
                    </div>
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500">Scored</span>
                      <p className="font-bold text-slate-200">{picksReport.summary.evaluated}/{picksReport.summary.total}</p>
                    </div>
                  </div>
                )}

                {/* Header row */}
                <div className="overflow-x-auto">
                  <div className="min-w-[34rem] flex flex-col gap-1">
                  <div className="flex items-center gap-2 px-2 text-3xs text-slate-500 font-semibold">
                  <span className="w-4 flex-shrink-0">#</span>
                  <span className="w-24 flex-shrink-0">Stock</span>
                  <span className="w-14 text-right flex-shrink-0">Entry</span>
                  <span className="w-32 text-right flex-shrink-0">O / H / L</span>
                  <span className="w-14 text-right flex-shrink-0" title="Open→High intraday gain: 1 star per 1%">O→H</span>
                  <span className="w-14 text-right flex-shrink-0">LTP</span>
                  <span className="w-12 text-right flex-shrink-0">Chg</span>
                </div>

                <div className="flex flex-col gap-1 max-h-[28rem] overflow-y-auto pr-1">
                  {picksReport.picks.map((p: any) => (
                    <button
                      key={p.symbol}
                      onClick={() => setSelectedSymbol(p.symbol)}
                      className={`w-full text-left p-2 border rounded-xl text-3xs flex items-center gap-2 transition-all ${
                        p.symbol === selectedSymbol ? 'bg-sky-950/20 border-sky-500/40' : 'bg-slate-950/30 border-slate-800 hover:border-slate-700'
                      }`}
                    >
                      <span className={`w-4 flex-shrink-0 font-bold ${
                        p.outcome === 'WIN' ? 'text-emerald-400' : p.outcome === 'LOSS' ? 'text-red-400' : 'text-slate-500'
                      }`}>{p.rank}</span>
                      <span className="w-24 flex-shrink-0 font-bold text-slate-200 truncate flex items-center gap-1">
                        {p.outcome === 'WIN' && <span className="text-emerald-400" title="Win">✓</span>}
                        {p.outcome === 'LOSS' && <span className="text-red-400" title="Loss">✗</span>}
                        <span className="truncate">{p.symbol}</span>
                      </span>
                      <span className="w-14 text-right flex-shrink-0 text-slate-400">₹{p.entry_price?.toFixed(2)}</span>
                      <span className="w-32 text-right flex-shrink-0 text-slate-500 tabular-nums">
                        {p.open != null ? `${p.open.toFixed(2)}/${p.high.toFixed(2)}/${p.low.toFixed(2)}` : '—'}
                      </span>
                      <span className="w-14 text-right flex-shrink-0 tabular-nums" title="Open→High intraday gain (1 star per 1%)">
                        {(() => {
                          // One star for each whole 1% the high ran above the open (max 5).
                          if (p.open == null || p.high == null || p.open <= 0) return <span className="text-slate-600">—</span>;
                          const stars = Math.min(5, Math.floor(((p.high - p.open) / p.open) * 100));
                          if (stars < 1) return <span className="text-slate-600">·</span>;
                          return <span className="text-amber-400">{'★'.repeat(stars)}</span>;
                        })()}
                      </span>
                      <span className="w-14 text-right flex-shrink-0 font-bold text-slate-200">{p.current != null ? `₹${p.current.toFixed(2)}` : '—'}</span>
                      <span className={`w-12 text-right flex-shrink-0 font-bold ${
                        p.change_pct == null ? 'text-slate-600' : p.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}>
                        {p.change_pct == null ? '—' : `${p.change_pct > 0 ? '+' : ''}${p.change_pct}%`}
                      </span>
                    </button>
                  ))}
                </div>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Row: Strategy Top-5 by P(+3%) × volatility + Strategy Scorecard (strategy ↔ its scorecard) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* +2% / +3% Possibility Scan Panel */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <Target className="text-sky-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy: Top‑5 by P(+3%) × volatility</h2>
                {(plusLive || plusTargets?.live) && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />LIVE
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-3xs font-semibold text-slate-400 bg-slate-800/60 px-1.5 py-0.5 rounded">Today · {todayISTLabel}</span>
                <button
                  onClick={() => fetchPlusTargets(true)}
                  disabled={plusTargetsLoading}
                  className="text-slate-400 hover:text-sky-400 disabled:opacity-50 transition-all"
                  title="Refresh scan"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${plusTargetsLoading ? 'animate-spin text-sky-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              Our strategy: each morning buy the 5 stocks ranked by a 50/50 blend of next‑day P(+3%) and volatility (ATR + range), then hold to the close
              {plusTargets?.as_of ? ` · picked on ${plusTargets.as_of}` : ''}
              {(plusLive || plusTargets?.live) ? (
                <span className="text-emerald-400 font-semibold">
                  {' · tracking live ' + new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' }).format(new Date())}
                </span>
              ) : ''}.
            </p>

            {plusTargetsLoading ? (
              <div className="py-8 flex items-center justify-center">
                <RefreshCw className="animate-spin text-sky-500 w-5 h-5" />
              </div>
            ) : plusTargets ? (
              <>
                {/* Strategy Top-5 by P(+3%) — the actual picks */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-3xs font-bold text-amber-300 uppercase tracking-wide">
                      Today&apos;s Top‑5 · P(+3%) × volatility
                    </span>
                    <span className="text-3xs text-slate-500">buy → hold to close</span>
                  </div>
                  <div className="flex flex-col gap-1">
                    {(plusTargets.plus3_top || []).slice(0, 5).map((c: any, i: number) => (
                      <button
                        key={`strat-${c.symbol}`}
                        onClick={() => setSelectedSymbol(c.symbol)}
                        className="w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/40 border-amber-900/40 hover:border-amber-600/60 transition-all"
                      >
                        <span className="w-4 text-center text-3xs font-bold text-amber-500/80 flex-shrink-0">{i + 1}</span>
                        <div className="flex-1 min-w-0">
                          <span className="font-bold block text-slate-100">{c.symbol}</span>
                          <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                        </div>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-20">
                          <span className="text-3xs text-slate-500">entry ₹{c.price.toFixed(1)}</span>
                          {c.live_price != null ? (
                            <span className={`text-3xs font-bold ${(c.live_change_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                              ₹{c.live_price.toFixed(1)} {(c.live_change_pct ?? 0) >= 0 ? '+' : ''}{c.live_change_pct}%
                            </span>
                          ) : (
                            <span className="text-3xs text-slate-600">—</span>
                          )}
                        </div>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-16">
                          <span className="text-3xs font-bold text-amber-300">P(+3%) {c.prob_plus_3}%</span>
                          <span className="text-3xs text-slate-500">P(+2%) {c.prob_plus_2}%</span>
                          <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                            <div className="h-full bg-amber-500" style={{ width: `${Math.min(c.prob_plus_3, 100)}%` }} />
                          </div>
                        </div>
                      </button>
                    ))}
                    {(plusTargets.plus3_top || []).length === 0 && (
                      <p className="text-3xs text-slate-500 text-center py-3">No prediction data available.</p>
                    )}
                  </div>
                  <p className="text-3xs text-slate-600 mt-0.5">
                    Backtested 30‑day OOS: portfolio +14.5% vs liquid‑universe +7.6% · ~30% of picks close ≥ +2% (1.5 of 5/day) · ~25% close ≥ +3%.
                  </p>
                </div>

                {/* Count cards — broader market context */}
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-slate-950/40 border border-slate-800 rounded-xl p-3 flex flex-col">
                    <span className="text-3xs text-slate-500">P(+2%) ≥ 30%</span>
                    <span className="text-xl font-extrabold text-sky-300">{plusTargets.counts.p2_30}</span>
                    <span className="text-3xs text-slate-600">stocks · meaningful shot</span>
                  </div>
                  <div className="bg-slate-950/40 border border-slate-800 rounded-xl p-3 flex flex-col">
                    <span className="text-3xs text-slate-500">P(+2%) ≥ 40%</span>
                    <span className="text-xl font-extrabold text-sky-300">{plusTargets.counts.p2_40}</span>
                    <span className="text-3xs text-slate-600">stocks · solid setup</span>
                  </div>
                  <div className="bg-emerald-950/20 border border-emerald-800/40 rounded-xl p-3 flex flex-col">
                    <span className="text-3xs text-emerald-500/80">P(+2%) ≥ 50%</span>
                    <span className="text-xl font-extrabold text-emerald-300">{plusTargets.counts.p2_50}</span>
                    <span className="text-3xs text-slate-600">stocks · strong</span>
                  </div>
                  <div className="bg-amber-950/20 border border-amber-800/40 rounded-xl p-3 flex flex-col">
                    <span className="text-3xs text-amber-500/80">P(+3%) ≥ 40%</span>
                    <span className="text-xl font-extrabold text-amber-300">{plusTargets.counts.p3_40 ?? 0}</span>
                    <span className="text-3xs text-slate-600">stocks · big-move</span>
                  </div>
                </div>

                {/* +2% >= 50% list */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-3xs font-bold text-emerald-300 uppercase tracking-wide">
                      {plusTargets.plus2_candidates.length > 0 ? '+2% · P ≥ 40%' : '+2% · strongest today'}
                    </span>
                    <span className="text-3xs text-slate-500">
                      {(plusTargets.plus2_candidates.length > 0 ? plusTargets.plus2_candidates : (plusTargets.plus2_top || [])).length} stocks
                    </span>
                  </div>
                  {plusTargets.plus2_candidates.length === 0 && (
                    <p className="text-3xs text-amber-500/80 -mt-0.5">No stock clears +2% at 40% today — showing the strongest candidates.</p>
                  )}
                  <div className="flex flex-col gap-1 max-h-56 overflow-y-auto pr-1">
                    {(plusTargets.plus2_candidates.length > 0 ? plusTargets.plus2_candidates : (plusTargets.plus2_top || [])).map((c: any) => (
                      <button
                        key={`p2-${c.symbol}`}
                        onClick={() => setSelectedSymbol(c.symbol)}
                        className="w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/30 border-slate-800 hover:border-emerald-700/50 transition-all"
                      >
                        <div className="flex-1 min-w-0">
                          <span className="font-bold block text-slate-200">{c.symbol}</span>
                          <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                        </div>
                        <span className="text-3xs text-slate-300 flex-shrink-0">₹{c.price.toFixed(1)}</span>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-14">
                          <span className="text-3xs font-bold text-emerald-300">{c.prob_plus_2}%</span>
                          <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                            <div className="h-full bg-emerald-500" style={{ width: `${Math.min(c.prob_plus_2, 100)}%` }} />
                          </div>
                        </div>
                      </button>
                    ))}
                    {(plusTargets.plus2_candidates.length === 0 && (plusTargets.plus2_top || []).length === 0) && (
                      <p className="text-3xs text-slate-500 text-center py-3">No prediction data available.</p>
                    )}
                  </div>
                </div>

                {/* +3% >= 50% list */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-3xs font-bold text-amber-300 uppercase tracking-wide">
                      {plusTargets.plus3_candidates.length > 0 ? '+3% · P ≥ 40%' : '+3% · strongest today'}
                    </span>
                    <span className="text-3xs text-slate-500">
                      {(plusTargets.plus3_candidates.length > 0 ? plusTargets.plus3_candidates : (plusTargets.plus3_top || [])).length} stocks
                    </span>
                  </div>
                  {plusTargets.plus3_candidates.length === 0 && (
                    <p className="text-3xs text-amber-500/80 -mt-0.5">No stock clears +3% at 40% today — showing the strongest candidates.</p>
                  )}
                  <div className="flex flex-col gap-1 max-h-56 overflow-y-auto pr-1">
                    {(plusTargets.plus3_candidates.length > 0 ? plusTargets.plus3_candidates : (plusTargets.plus3_top || [])).map((c: any) => (
                      <button
                        key={`p3-${c.symbol}`}
                        onClick={() => setSelectedSymbol(c.symbol)}
                        className="w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/30 border-slate-800 hover:border-amber-700/50 transition-all"
                      >
                        <div className="flex-1 min-w-0">
                          <span className="font-bold block text-slate-200">{c.symbol}</span>
                          <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                        </div>
                        <span className="text-3xs text-slate-300 flex-shrink-0">₹{c.price.toFixed(1)}</span>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-14">
                          <span className="text-3xs font-bold text-amber-300">{c.prob_plus_3}%</span>
                          <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                            <div className="h-full bg-amber-500" style={{ width: `${Math.min(c.prob_plus_3, 100)}%` }} />
                          </div>
                        </div>
                      </button>
                    ))}
                    {(plusTargets.plus3_candidates.length === 0 && (plusTargets.plus3_top || []).length === 0) && (
                      <p className="text-3xs text-slate-500 text-center py-3">No prediction data available.</p>
                    )}
                  </div>
                </div>

                <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
                  Strategy ranks by a 50/50 blend of P(+3%) and volatility (ATR + daily range) and holds to close — volatility‑tilting roughly doubled per‑pick return out‑of‑sample, while a fixed +2%/+3% profit‑target exit tested worse (it caps the fat‑tail winners). These are model probabilities, not guarantees; use the ranking, not the absolute number.
                </p>
              </>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-4">No prediction data available yet.</p>
            )}
          </div>

          {/* Strategy Scorecard — Top-5 by P(+3%) realized next-day results */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <Award className="text-amber-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy Scorecard</h2>
                {(strategyLive || strategyScore?.live) && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />LIVE
                  </span>
                )}
                {strategyScore?.source === 'backtest' && (
                  <span className="text-3xs font-bold text-amber-500/80 bg-amber-950/30 px-1.5 py-0.5 rounded">30‑day backtest</span>
                )}
                {strategyScore?.source === 'live+backtest' && (
                  <span className="text-3xs font-bold text-emerald-400/90 bg-emerald-950/30 px-1.5 py-0.5 rounded">{strategyScore.live_days} live + backtest</span>
                )}
                {strategyScore?.source === 'live' && (
                  <span className="text-3xs font-bold text-emerald-400/90 bg-emerald-950/30 px-1.5 py-0.5 rounded">live forward</span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {strategyScore?.days?.length > 0 && (
                  <select
                    value={selectedStrategyDate}
                    onChange={(e) => setSelectedStrategyDate(e.target.value)}
                    className="bg-slate-950/60 border border-slate-700 rounded-lg text-3xs text-slate-300 px-1.5 py-1 focus:outline-none focus:border-amber-500"
                    title="Backtest pick date"
                  >
                    {strategyScore.days.map((d: any) => (
                      <option key={d.pick_date} value={d.pick_date}>
                        {d.pick_date}{d.pending ? ' (today)' : ''}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  onClick={() => fetchStrategyScore(true)}
                  disabled={strategyScoreLoading}
                  className="text-slate-400 hover:text-amber-400 disabled:opacity-50 transition-all"
                  title="Refresh scorecard"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${strategyScoreLoading ? 'animate-spin text-amber-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              How the Top‑5 by P(+3%) × volatility actually played out — entry = pick‑day close, result = next session&apos;s close.
            </p>

            {strategyScoreLoading ? (
              <div className="py-8 flex items-center justify-center">
                <RefreshCw className="animate-spin text-amber-500 w-5 h-5" />
              </div>
            ) : strategyScore && strategyScore.days?.length ? (
              <>
                {/* Overall summary across graded days */}
                {strategyScore.overall && (
                  <div className="grid grid-cols-4 gap-1.5 text-3xs text-center">
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500 block">Win rate</span>
                      <span className="text-sm font-extrabold text-slate-100">{strategyScore.overall.win_rate}%</span>
                      <span className="text-slate-600 block">{strategyScore.overall.green}/{strategyScore.overall.scored}</span>
                    </div>
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500 block">Avg move</span>
                      <span className={`text-sm font-extrabold ${strategyScore.overall.avg_cc >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                        {strategyScore.overall.avg_cc >= 0 ? '+' : ''}{strategyScore.overall.avg_cc}%
                      </span>
                      <span className="text-slate-600 block">close→close</span>
                    </div>
                    <div className="bg-emerald-950/20 rounded-xl p-2">
                      <span className="text-emerald-500/80 block">≥ +2%</span>
                      <span className="text-sm font-extrabold text-emerald-300">{strategyScore.overall.hit2_rate}%</span>
                      <span className="text-slate-600 block">{strategyScore.overall.hit2}/{strategyScore.overall.scored}</span>
                    </div>
                    <div className="bg-amber-950/20 rounded-xl p-2">
                      <span className="text-amber-500/80 block">≥ +3%</span>
                      <span className="text-sm font-extrabold text-amber-300">{strategyScore.overall.hit3_rate}%</span>
                      <span className="text-slate-600 block">{strategyScore.overall.hit3}/{strategyScore.overall.scored}</span>
                    </div>
                  </div>
                )}

                {/* Selected-day full breakdown (entry vs next-session O/H/L/LTP) */}
                {(() => {
                  const day = strategyScore.days.find((d: any) => d.pick_date === selectedStrategyDate)
                    || strategyScore.days[0];
                  if (!day) return null;
                  return (
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between px-1">
                        <span className="text-3xs font-bold text-slate-300">
                          {day.pick_date}
                          <span className="text-slate-600"> → {day.pending ? 'pending' : day.result_date}</span>
                        </span>
                        {day.pending ? (
                          <span className="text-3xs font-bold text-sky-400 flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />TODAY
                          </span>
                        ) : day.summary ? (
                          <span className={`text-3xs font-bold ${day.summary.avg_cc >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                            {day.summary.avg_cc >= 0 ? '+' : ''}{day.summary.avg_cc}% · {day.summary.green}/5 green · {day.summary.hit2}×+2%
                          </span>
                        ) : null}
                      </div>

                      <div className="overflow-x-auto">
                        <div className="min-w-[34rem] flex flex-col gap-1">
                          {/* Header row */}
                          <div className="flex items-center gap-2 px-2 text-3xs text-slate-500 font-semibold">
                            <span className="w-4 flex-shrink-0">#</span>
                            <span className="w-24 flex-shrink-0">Stock</span>
                            <span className="w-12 text-right flex-shrink-0">Entry</span>
                            <span className="w-32 text-right flex-shrink-0">O / H / L</span>
                            <span className="w-14 text-right flex-shrink-0" title="Entry(close)→High intraday gain: 1 star per 1%">E→H</span>
                            <span className="w-14 text-right flex-shrink-0" title="Last/next close">LTP</span>
                            <span className="w-12 text-right flex-shrink-0" title="Close→close change">Chg</span>
                          </div>

                          {day.picks.map((p: any, i: number) => {
                            const win = p.outcome === 'WIN';
                            const loss = p.outcome === 'LOSS';
                            return (
                              <button
                                key={`${day.pick_date}-${p.symbol}`}
                                onClick={() => setSelectedSymbol(p.symbol)}
                                className={`w-full text-left p-2 border rounded-xl text-3xs flex items-center gap-2 transition-all ${
                                  p.symbol === selectedSymbol ? 'bg-amber-950/20 border-amber-500/40' : 'bg-slate-950/30 border-slate-800 hover:border-slate-700'
                                }`}
                              >
                                <span className={`w-4 flex-shrink-0 font-bold ${win ? 'text-emerald-400' : loss ? 'text-red-400' : 'text-slate-500'}`}>{i + 1}</span>
                                <span className="w-24 flex-shrink-0 font-bold text-slate-200 truncate flex items-center gap-1">
                                  {win && <span className="text-emerald-400" title="Win">✓</span>}
                                  {loss && <span className="text-red-400" title="Loss">✗</span>}
                                  <span className="truncate">{p.symbol}</span>
                                </span>
                                <span className="w-12 text-right flex-shrink-0 text-slate-400">₹{p.entry?.toFixed(2)}</span>
                                <span className="w-32 text-right flex-shrink-0 text-slate-500 tabular-nums">
                                  {p.next_open != null ? `${p.next_open.toFixed(2)}/${p.next_high.toFixed(2)}/${p.next_low.toFixed(2)}` : '—'}
                                </span>
                                <span className="w-14 text-right flex-shrink-0 tabular-nums" title="Entry(close)→High intraday gain (1 star per 1%)">
                                  {(() => {
                                    if (p.ch == null) return <span className="text-slate-600">—</span>;
                                    const stars = Math.min(5, Math.floor(p.ch));
                                    if (stars < 1) return <span className="text-slate-600">·</span>;
                                    return <span className="text-amber-400">{'★'.repeat(stars)}</span>;
                                  })()}
                                </span>
                                <span className="w-14 text-right flex-shrink-0 font-bold text-slate-200">{p.next_close != null ? `₹${p.next_close.toFixed(2)}` : '—'}</span>
                                <span className={`w-12 text-right flex-shrink-0 font-bold ${
                                  p.cc == null ? 'text-slate-600' : p.cc >= 0 ? 'text-emerald-400' : 'text-red-400'
                                }`}>
                                  {p.cc == null ? '—' : `${p.cc > 0 ? '+' : ''}${p.cc}%`}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
                  Walk‑forward out‑of‑sample: each day&apos;s Top‑5 by P(+3%) × volatility re‑ranked from models trained only on prior data. Entry = pick‑day close, LTP = next session&apos;s close (the strategy&apos;s exit). ✓ = closed up, ✗ = closed down. Past results don&apos;t guarantee future ones.
                </p>
              </>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-4">
                Not enough prediction history yet — the scorecard fills in as daily picks accumulate.
              </p>
            )}
          </div>
        </div>

        {/* Row: Strategy 3% · 3:20 PM + its forward scorecard (strategy ↔ its scorecard) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Strategy 3% · 3:20 PM — intraday run (live price as close), actionable before close */}
          <div className="w-full bg-gradient-to-br from-indigo-950/40 to-slate-900/40 backdrop-blur-md border border-indigo-800/50 rounded-2xl shadow-xl p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between border-b border-indigo-900/50 pb-2">
              <div className="flex items-center gap-2">
                <Clock className="text-indigo-300 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy 3% · 3:20 PM</h2>
                <span className="text-3xs font-bold text-indigo-300/80 bg-indigo-950/50 px-1.5 py-0.5 rounded">intraday</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-3xs font-semibold text-slate-400 bg-slate-800/60 px-1.5 py-0.5 rounded">Today · {todayISTLabel}</span>
                <button
                  onClick={() => fetchIntradayStrategy(true)}
                  disabled={intradayScoreLoading}
                  className="text-slate-400 hover:text-indigo-300 disabled:opacity-50 transition-all"
                  title="Refresh"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${intradayScoreLoading ? 'animate-spin text-indigo-400' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              At 3:20 PM the live price is taken as today&apos;s close and the model re‑ranks the Top‑5 by P(+3%) × volatility — so you can buy <span className="text-indigo-300 font-semibold">before the 3:30 close</span>, not the next day
              {intradayStrategy?.pick_date ? ` · ${intradayStrategy.pick_date}` : ''}.
            </p>

            {intradayStrategy && intradayStrategy.picks?.length ? (
              <div className="flex flex-col gap-1">
                <div className="flex items-center justify-between">
                  <span className="text-3xs font-bold text-indigo-300 uppercase tracking-wide">3:20 PM Top‑5 · P(+3%) × volatility</span>
                  <span className="text-3xs text-slate-500">buy near close</span>
                </div>
                {intradayStrategy.picks.slice(0, 5).map((c: any, i: number) => (
                  <button
                    key={`intra-${c.symbol}`}
                    onClick={() => setSelectedSymbol(c.symbol)}
                    className="w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/40 border-indigo-900/40 hover:border-indigo-500/60 transition-all"
                  >
                    <span className="w-4 text-center text-3xs font-bold text-indigo-300/80 flex-shrink-0">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <span className="font-bold block text-slate-100">{c.symbol}</span>
                      <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                    </div>
                    <span className="text-3xs text-slate-300 flex-shrink-0">₹{c.entry != null ? c.entry.toFixed(1) : '—'}</span>
                    <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-16">
                      <span className="text-3xs font-bold text-indigo-300">P(+3%) {c.prob_plus_3}%</span>
                      <span className="text-3xs text-slate-500">P(+2%) {c.prob_plus_2}%</span>
                      <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                        <div className="h-full bg-indigo-500" style={{ width: `${Math.min(c.prob_plus_3 || 0, 100)}%` }} />
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-4">
                No 3:20 PM snapshot yet — it&apos;s captured automatically each trading day at 3:20 PM IST.
              </p>
            )}
          </div>

          {/* Strategy 3% · 3:20 PM Scorecard — forward-tracked, graded vs next-day HIGH */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-indigo-900/50 rounded-2xl shadow-xl p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between border-b border-indigo-900/40 pb-2">
              <div className="flex items-center gap-2">
                <Clock className="text-indigo-300 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy 3% · 3:20 PM Scorecard</h2>
                {(intradayLive || intradayScore?.live) && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />LIVE
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {intradayScore?.days?.length > 0 && (
                  <select
                    value={selectedIntradayDate}
                    onChange={(e) => setSelectedIntradayDate(e.target.value)}
                    className="bg-slate-950/60 border border-slate-700 rounded-lg text-3xs text-slate-300 px-1.5 py-1 focus:outline-none focus:border-indigo-500"
                    title="3:20 PM pick date"
                  >
                    {intradayScore.days.map((d: any) => (
                      <option key={d.pick_date} value={d.pick_date}>
                        {d.pick_date}{d.pending ? ' (pending)' : ''}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  onClick={() => fetchIntradayStrategy(true)}
                  disabled={intradayScoreLoading}
                  className="text-slate-400 hover:text-indigo-300 disabled:opacity-50 transition-all"
                  title="Refresh scorecard"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${intradayScoreLoading ? 'animate-spin text-indigo-400' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              How the 3:20 PM Top‑5 played out — entry = 3:20 PM price, graded by how far the <span className="text-indigo-300 font-semibold">next day&apos;s HIGH</span> ran above entry (E→H). Built forward day‑by‑day (no historical intraday data to back‑test).
            </p>

            {intradayScoreLoading ? (
              <div className="py-8 flex items-center justify-center">
                <RefreshCw className="animate-spin text-indigo-400 w-5 h-5" />
              </div>
            ) : intradayScore && intradayScore.days?.length ? (
              <>
                {intradayScore.overall && (
                  <div className="grid grid-cols-4 gap-1.5 text-3xs text-center">
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500 block">Hit +2% high</span>
                      <span className="text-sm font-extrabold text-slate-100">{intradayScore.overall.win_rate}%</span>
                      <span className="text-slate-600 block">{intradayScore.overall.green}/{intradayScore.overall.scored}</span>
                    </div>
                    <div className="bg-slate-950/40 rounded-xl p-2">
                      <span className="text-slate-500 block">Avg E→H</span>
                      <span className={`text-sm font-extrabold ${intradayScore.overall.avg_ch >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                        {intradayScore.overall.avg_ch >= 0 ? '+' : ''}{intradayScore.overall.avg_ch}%
                      </span>
                      <span className="text-slate-600 block">entry→high</span>
                    </div>
                    <div className="bg-emerald-950/20 rounded-xl p-2">
                      <span className="text-emerald-500/80 block">≥ +2%</span>
                      <span className="text-sm font-extrabold text-emerald-300">{intradayScore.overall.hit2_rate}%</span>
                      <span className="text-slate-600 block">{intradayScore.overall.hit2}/{intradayScore.overall.scored}</span>
                    </div>
                    <div className="bg-amber-950/20 rounded-xl p-2">
                      <span className="text-amber-500/80 block">≥ +3%</span>
                      <span className="text-sm font-extrabold text-amber-300">{intradayScore.overall.hit3_rate}%</span>
                      <span className="text-slate-600 block">{intradayScore.overall.hit3}/{intradayScore.overall.scored}</span>
                    </div>
                  </div>
                )}

                {(() => {
                  const day = intradayScore.days.find((d: any) => d.pick_date === selectedIntradayDate)
                    || intradayScore.days[0];
                  if (!day) return null;
                  return (
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between px-1">
                        <span className="text-3xs font-bold text-slate-300">
                          {day.pick_date}
                          <span className="text-slate-600"> → {day.pending ? 'awaiting next session' : day.result_date}</span>
                        </span>
                        {day.pending ? (
                          <span className="text-3xs font-bold text-indigo-300 flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />PENDING
                          </span>
                        ) : day.summary ? (
                          <span className={`text-3xs font-bold ${day.summary.avg_ch >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                            avg E→H {day.summary.avg_ch >= 0 ? '+' : ''}{day.summary.avg_ch}% · {day.summary.hit2}/5 ≥+2%
                          </span>
                        ) : null}
                      </div>

                      <div className="overflow-x-auto">
                        <div className="min-w-[34rem] flex flex-col gap-1">
                          <div className="flex items-center gap-2 px-2 text-3xs text-slate-500 font-semibold">
                            <span className="w-4 flex-shrink-0">#</span>
                            <span className="w-24 flex-shrink-0">Stock</span>
                            <span className="w-12 text-right flex-shrink-0" title="3:20 PM entry">Entry</span>
                            <span className="w-32 text-right flex-shrink-0">O / H / L</span>
                            <span className="w-14 text-right flex-shrink-0" title="Entry→next High: 1 star per 1%">E→H</span>
                            <span className="w-14 text-right flex-shrink-0" title="Next close">LTP</span>
                            <span className="w-12 text-right flex-shrink-0" title="Entry→next close">Chg</span>
                          </div>

                          {day.picks.map((p: any, i: number) => {
                            const win = p.outcome === 'WIN';
                            const loss = p.outcome === 'LOSS';
                            return (
                              <button
                                key={`intra-sc-${day.pick_date}-${p.symbol}`}
                                onClick={() => setSelectedSymbol(p.symbol)}
                                className={`w-full text-left p-2 border rounded-xl text-3xs flex items-center gap-2 transition-all ${
                                  p.symbol === selectedSymbol ? 'bg-indigo-950/30 border-indigo-500/40' : 'bg-slate-950/30 border-slate-800 hover:border-slate-700'
                                }`}
                              >
                                <span className={`w-4 flex-shrink-0 font-bold ${win ? 'text-emerald-400' : loss ? 'text-red-400' : 'text-slate-500'}`}>{i + 1}</span>
                                <span className="w-24 flex-shrink-0 font-bold text-slate-200 truncate flex items-center gap-1">
                                  {win && <span className="text-emerald-400" title="Hit +2% high">✓</span>}
                                  {loss && <span className="text-red-400" title="Missed +2% high">✗</span>}
                                  <span className="truncate">{p.symbol}</span>
                                </span>
                                <span className="w-12 text-right flex-shrink-0 text-slate-400">₹{p.entry?.toFixed(2)}</span>
                                <span className="w-32 text-right flex-shrink-0 text-slate-500 tabular-nums">
                                  {p.next_open != null ? `${p.next_open.toFixed(2)}/${p.next_high.toFixed(2)}/${p.next_low.toFixed(2)}` : '—'}
                                </span>
                                <span className="w-14 text-right flex-shrink-0 tabular-nums" title="Entry→next High (1 star per 1%)">
                                  {(() => {
                                    if (p.ch == null) return <span className="text-slate-600">—</span>;
                                    const stars = Math.min(5, Math.floor(p.ch));
                                    if (stars < 1) return <span className="text-slate-600">·</span>;
                                    return <span className="text-amber-400">{'★'.repeat(stars)}</span>;
                                  })()}
                                </span>
                                <span className="w-14 text-right flex-shrink-0 font-bold text-slate-200">{p.next_close != null ? `₹${p.next_close.toFixed(2)}` : '—'}</span>
                                <span className={`w-12 text-right flex-shrink-0 font-bold ${
                                  p.cc == null ? 'text-slate-600' : p.cc >= 0 ? 'text-emerald-400' : 'text-red-400'
                                }`}>
                                  {p.cc == null ? '—' : `${p.cc > 0 ? '+' : ''}${p.cc}%`}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
                  Entry = 3:20 PM live price (treated as close). ✓ = next day&apos;s high reached ≥ +2% above entry, ✗ = it didn&apos;t. Forward‑tracked from today onward. Past results don&apos;t guarantee future ones.
                </p>
              </>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-4">
                No 3:20 PM picks graded yet — results appear the morning after the first snapshot.
              </p>
            )}
          </div>
        </div>

        {/* Row: −6% Disaster Stop + 2% Trailing Stop (side by side, stacks on mobile) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* SIBLING strategy hero card — same Top-5, exited with a -6% disaster stop */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-emerald-900/40 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <Shield className="text-emerald-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy: Top‑5 + −6% Disaster Stop</h2>
                {(stopLive || plusLive || plusTargets?.live) && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />LIVE
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-3xs font-semibold text-slate-400 bg-slate-800/60 px-1.5 py-0.5 rounded">Today · {todayISTLabel}</span>
                <button
                  onClick={() => { fetchPlusTargets(true); fetchStopScore(true); }}
                  disabled={stopScoreLoading}
                  className="text-slate-400 hover:text-emerald-400 disabled:opacity-50 transition-all"
                  title="Refresh"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${stopScoreLoading ? 'animate-spin text-emerald-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              <span className="text-emerald-300 font-semibold">Same Top‑5 picks</span> as the hold‑to‑close card, but each position is exited if it craters past <span className="text-emerald-300 font-semibold">−6% intraday</span> (gap‑aware) instead of held blindly to the close — pure tail insurance.
            </p>

            {/* Head-to-head comparison: hold-to-close vs +stop */}
            <div className="grid grid-cols-3 gap-1.5 text-3xs text-center">
              <div className="bg-slate-950/40 rounded-xl p-2 flex flex-col">
                <span className="text-slate-500 block mb-1">&nbsp;</span>
                <span className="text-slate-500 block h-5 leading-5">Win rate</span>
                <span className="text-slate-500 block h-5 leading-5">Avg / pick</span>
                <span className="text-slate-500 block h-5 leading-5">≥ +2%</span>
              </div>
              <div className="bg-slate-950/40 rounded-xl p-2 flex flex-col">
                <span className="text-amber-300/90 font-bold block mb-1">Hold‑to‑close</span>
                <span className="text-slate-200 font-bold block h-5 leading-5">{strategyScore?.overall ? `${strategyScore.overall.win_rate}%` : '—'}</span>
                <span className={`font-bold block h-5 leading-5 ${(strategyScore?.overall?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {strategyScore?.overall ? `${strategyScore.overall.avg_cc >= 0 ? '+' : ''}${strategyScore.overall.avg_cc}%` : '—'}
                </span>
                <span className="text-emerald-300 font-bold block h-5 leading-5">{strategyScore?.overall ? `${strategyScore.overall.hit2_rate}%` : '—'}</span>
              </div>
              <div className="bg-emerald-950/20 rounded-xl p-2 flex flex-col border border-emerald-900/40">
                <span className="text-emerald-300 font-bold block mb-1">+ −6% Stop</span>
                <span className="text-slate-200 font-bold block h-5 leading-5">{stopScore?.overall ? `${stopScore.overall.win_rate}%` : '—'}</span>
                <span className={`font-bold block h-5 leading-5 ${(stopScore?.overall?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {stopScore?.overall ? `${stopScore.overall.avg_cc >= 0 ? '+' : ''}${stopScore.overall.avg_cc}%` : '—'}
                </span>
                <span className="text-emerald-300 font-bold block h-5 leading-5">{stopScore?.overall ? `${stopScore.overall.hit2_rate}%` : '—'}</span>
              </div>
            </div>

            {/* Same Top-5 picks, with the stop level annotated */}
            {plusTargets?.plus3_top?.length ? (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-3xs font-bold text-emerald-300 uppercase tracking-wide">Today&apos;s Top‑5 · same picks</span>
                  <span className="text-3xs text-slate-500">buy → hold, stop −6%</span>
                </div>
                <div className="flex flex-col gap-1">
                  {(plusTargets.plus3_top || []).slice(0, 5).map((c: any, i: number) => {
                    const stopAt = c.price ? c.price * 0.94 : null;
                    const stopped = c.live_change_pct != null && c.live_change_pct <= -6;
                    return (
                      <button
                        key={`stopstrat-${c.symbol}`}
                        onClick={() => setSelectedSymbol(c.symbol)}
                        className={`w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/40 transition-all ${stopped ? 'border-red-700/60' : 'border-emerald-900/40 hover:border-emerald-600/60'}`}
                      >
                        <span className="w-4 text-center text-3xs font-bold text-emerald-500/80 flex-shrink-0">{i + 1}</span>
                        <div className="flex-1 min-w-0">
                          <span className="font-bold block text-slate-100">{c.symbol}</span>
                          <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                        </div>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-20">
                          <span className="text-3xs text-slate-500">entry ₹{c.price.toFixed(1)}</span>
                          {c.live_price != null ? (
                            <span className={`text-3xs font-bold ${(c.live_change_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                              ₹{c.live_price.toFixed(1)} {(c.live_change_pct ?? 0) >= 0 ? '+' : ''}{c.live_change_pct}%
                            </span>
                          ) : (
                            <span className="text-3xs text-slate-600">—</span>
                          )}
                        </div>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-16">
                          <span className={`text-3xs font-bold ${stopped ? 'text-red-300' : 'text-emerald-300'}`}>{stopped ? 'STOPPED' : 'stop ₹' + (stopAt ? stopAt.toFixed(1) : '—')}</span>
                          <span className="text-3xs text-slate-500">P(+3%) {c.prob_plus_3}%</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-3">No prediction data available.</p>
            )}

            <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
              Exit‑timing walk‑forward (30‑day &amp; 60‑day OOS): the loose −6% stop was the only exit that beat plain hold‑to‑close on <span className="text-emerald-300 font-semibold">both</span> windows while keeping win‑rate and the +2% hit‑rate identical — it just trims the rare crater. Tighter −3%/−4% stops tested worse.
            </p>
          </div>

          {/* SIBLING strategy hero card — same Top-5, exited with a 2% trailing stop (armed +2%) */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-violet-900/40 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <TrendingUp className="text-violet-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy: Top‑5 + 2% Trailing Stop</h2>
                {(plusLive || plusTargets?.live) && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-violet-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />LIVE
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-3xs font-semibold text-slate-400 bg-slate-800/60 px-1.5 py-0.5 rounded">Today · {todayISTLabel}</span>
                <button
                  onClick={() => { fetchPlusTargets(true); fetchTrailScore(); }}
                  disabled={trailScoreLoading}
                  className="text-slate-400 hover:text-violet-400 disabled:opacity-50 transition-all"
                  title="Refresh"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${trailScoreLoading ? 'animate-spin text-violet-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              <span className="text-violet-300 font-semibold">Same Top‑5 picks</span> as the hold‑to‑close card, but once a position first runs <span className="text-violet-300 font-semibold">+2%</span> it locks in by trailing a <span className="text-violet-300 font-semibold">2% stop</span> below its running peak — protecting gains while still letting winners run.
            </p>

            {/* Head-to-head comparison: hold-to-close vs +trailing (real 5-min backtest) */}
            <div className="grid grid-cols-3 gap-1.5 text-3xs text-center">
              <div className="bg-slate-950/40 rounded-xl p-2 flex flex-col">
                <span className="text-slate-500 block mb-1">&nbsp;</span>
                <span className="text-slate-500 block h-5 leading-5">Win rate</span>
                <span className="text-slate-500 block h-5 leading-5">Avg / pick</span>
                <span className="text-slate-500 block h-5 leading-5">≥ +2%</span>
              </div>
              <div className="bg-slate-950/40 rounded-xl p-2 flex flex-col">
                <span className="text-amber-300/90 font-bold block mb-1">Hold‑to‑close</span>
                <span className="text-slate-200 font-bold block h-5 leading-5">{trailScore?.baseline_overall ? `${trailScore.baseline_overall.win_rate}%` : '—'}</span>
                <span className={`font-bold block h-5 leading-5 ${(trailScore?.baseline_overall?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {trailScore?.baseline_overall ? `${trailScore.baseline_overall.avg_cc >= 0 ? '+' : ''}${trailScore.baseline_overall.avg_cc}%` : '—'}
                </span>
                <span className="text-emerald-300 font-bold block h-5 leading-5">{trailScore?.baseline_overall ? `${trailScore.baseline_overall.hit2_rate}%` : '—'}</span>
              </div>
              <div className="bg-violet-950/20 rounded-xl p-2 flex flex-col border border-violet-900/40">
                <span className="text-violet-300 font-bold block mb-1">+ 2% Trail</span>
                <span className="text-slate-200 font-bold block h-5 leading-5">{trailScore?.overall ? `${trailScore.overall.win_rate}%` : '—'}</span>
                <span className={`font-bold block h-5 leading-5 ${(trailScore?.overall?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {trailScore?.overall ? `${trailScore.overall.avg_cc >= 0 ? '+' : ''}${trailScore.overall.avg_cc}%` : '—'}
                </span>
                <span className="text-emerald-300 font-bold block h-5 leading-5">{trailScore?.overall ? `${trailScore.overall.hit2_rate}%` : '—'}</span>
              </div>
            </div>
            <p className="text-3xs text-slate-600 text-center -mt-1.5">30‑day OOS backtest on real 5‑minute paths · trailing lifts win‑rate {trailScore?.baseline_overall && trailScore?.overall ? `${trailScore.baseline_overall.win_rate}% → ${trailScore.overall.win_rate}%` : ''}</p>

            {/* Same Top-5 picks, with the live trailing status annotated */}
            {plusTargets?.plus3_top?.length ? (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-3xs font-bold text-violet-300 uppercase tracking-wide">Today&apos;s Top‑5 · same picks</span>
                  <span className="text-3xs text-slate-500">arm +2% → trail 2%</span>
                </div>
                <div className="flex flex-col gap-1">
                  {(plusTargets.plus3_top || []).slice(0, 5).map((c: any, i: number) => {
                    const peakPct = c.live_high_pct;
                    const armed = peakPct != null && peakPct >= 2;
                    const trailLevel = armed && c.live_high != null ? c.live_high * 0.98 : null;
                    const locked = armed && trailLevel != null && c.live_price != null && c.live_price <= trailLevel;
                    const status = locked ? 'LOCKED' : armed ? 'TRAILING' : 'WATCHING';
                    const statusColor = locked ? 'text-violet-300' : armed ? 'text-emerald-300' : 'text-slate-500';
                    return (
                      <button
                        key={`trailstrat-${c.symbol}`}
                        onClick={() => setSelectedSymbol(c.symbol)}
                        className={`w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/40 transition-all ${locked ? 'border-violet-700/60' : 'border-violet-900/40 hover:border-violet-600/60'}`}
                      >
                        <span className="w-4 text-center text-3xs font-bold text-violet-500/80 flex-shrink-0">{i + 1}</span>
                        <div className="flex-1 min-w-0">
                          <span className="font-bold block text-slate-100">{c.symbol}</span>
                          <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                        </div>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-20">
                          <span className="text-3xs text-slate-500">entry ₹{c.price.toFixed(1)}</span>
                          {c.live_price != null ? (
                            <span className={`text-3xs font-bold ${(c.live_change_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                              ₹{c.live_price.toFixed(1)} {(c.live_change_pct ?? 0) >= 0 ? '+' : ''}{c.live_change_pct}%
                            </span>
                          ) : (
                            <span className="text-3xs text-slate-600">—</span>
                          )}
                        </div>
                        <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-16">
                          <span className={`text-3xs font-bold ${statusColor}`}>{status}</span>
                          <span className="text-3xs text-slate-500">{armed && trailLevel != null ? 'trail ₹' + trailLevel.toFixed(1) : (peakPct != null ? `peak ${peakPct >= 0 ? '+' : ''}${peakPct}%` : 'P(+3%) ' + c.prob_plus_3 + '%')}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-3">No prediction data available.</p>
            )}

            <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
              Of the exit rules tested on real 5‑minute paths (30‑day &amp; 50‑day OOS), arming a 2% trail after +2% was the only trailing variant to beat hold‑to‑close on <span className="text-violet-300 font-semibold">both</span> windows — its edge is a far higher win‑rate (more green days), trading a little of the fat‑tail upside for consistency. Requires intraday execution; statuses above are live approximations from the running session high.
            </p>
          </div>
        </div>

        {/* Row: 3:20 PM Exit + Strategy Comparison (exit-rule sibling ↔ all-exits summary) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* SIBLING strategy hero card — same Top-5, exited at the 3:20 PM price (10 min before close) */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-sky-900/40 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <Timer className="text-sky-400 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy: Top‑5 + 3:20 PM Exit</h2>
                {(plusLive || plusTargets?.live) && (
                  <span className="flex items-center gap-1 text-3xs font-bold text-sky-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />LIVE
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-3xs font-semibold text-slate-400 bg-slate-800/60 px-1.5 py-0.5 rounded">Today · {todayISTLabel}</span>
                <button
                  onClick={() => { fetchPlusTargets(true); fetchTrailScore(); }}
                  disabled={trailScoreLoading}
                  className="text-slate-400 hover:text-sky-400 disabled:opacity-50 transition-all"
                  title="Refresh"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${trailScoreLoading ? 'animate-spin text-sky-500' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              <span className="text-sky-300 font-semibold">Same Top‑5 picks</span>, but instead of holding into the 3:30 closing auction you <span className="text-sky-300 font-semibold">sell at ~3:20 PM</span> — sidestepping last‑10‑minute volatility and settling before the auction.
            </p>

            {/* Head-to-head: hold-to-close vs 3:20 PM exit (real 5-min backtest) */}
            <div className="grid grid-cols-3 gap-1.5 text-3xs text-center">
              <div className="bg-slate-950/40 rounded-xl p-2 flex flex-col">
                <span className="text-slate-500 block mb-1">&nbsp;</span>
                <span className="text-slate-500 block h-5 leading-5">Win rate</span>
                <span className="text-slate-500 block h-5 leading-5">Avg / pick</span>
                <span className="text-slate-500 block h-5 leading-5">≥ +2%</span>
              </div>
              <div className="bg-slate-950/40 rounded-xl p-2 flex flex-col">
                <span className="text-amber-300/90 font-bold block mb-1">Hold‑to‑close</span>
                <span className="text-slate-200 font-bold block h-5 leading-5">{trailScore?.baseline_overall ? `${trailScore.baseline_overall.win_rate}%` : '—'}</span>
                <span className={`font-bold block h-5 leading-5 ${(trailScore?.baseline_overall?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {trailScore?.baseline_overall ? `${trailScore.baseline_overall.avg_cc >= 0 ? '+' : ''}${trailScore.baseline_overall.avg_cc}%` : '—'}
                </span>
                <span className="text-emerald-300 font-bold block h-5 leading-5">{trailScore?.baseline_overall ? `${trailScore.baseline_overall.hit2_rate}%` : '—'}</span>
              </div>
              <div className="bg-sky-950/20 rounded-xl p-2 flex flex-col border border-sky-900/40">
                <span className="text-sky-300 font-bold block mb-1">3:20 PM Exit</span>
                <span className="text-slate-200 font-bold block h-5 leading-5">{trailScore?.exit320_overall ? `${trailScore.exit320_overall.win_rate}%` : '—'}</span>
                <span className={`font-bold block h-5 leading-5 ${(trailScore?.exit320_overall?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {trailScore?.exit320_overall ? `${trailScore.exit320_overall.avg_cc >= 0 ? '+' : ''}${trailScore.exit320_overall.avg_cc}%` : '—'}
                </span>
                <span className="text-emerald-300 font-bold block h-5 leading-5">{trailScore?.exit320_overall ? `${trailScore.exit320_overall.hit2_rate}%` : '—'}</span>
              </div>
            </div>
            <p className="text-3xs text-slate-600 text-center -mt-1.5">30‑day OOS backtest on real 5‑minute paths · exit price = last 5‑min bar before 3:20 PM</p>

            {/* Same Top-5 picks with live price (your 3:20 sell approximated by current price) */}
            {plusTargets?.plus3_top?.length ? (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-3xs font-bold text-sky-300 uppercase tracking-wide">Today&apos;s Top‑5 · same picks</span>
                  <span className="text-3xs text-slate-500">sell ~3:20 PM</span>
                </div>
                <div className="flex flex-col gap-1">
                  {(plusTargets.plus3_top || []).slice(0, 5).map((c: any, i: number) => (
                    <button
                      key={`exit320-${c.symbol}`}
                      onClick={() => setSelectedSymbol(c.symbol)}
                      className="w-full text-left p-1.5 border rounded-lg text-xs flex items-center gap-2 bg-slate-950/40 border-sky-900/40 hover:border-sky-600/60 transition-all"
                    >
                      <span className="w-4 text-center text-3xs font-bold text-sky-500/80 flex-shrink-0">{i + 1}</span>
                      <div className="flex-1 min-w-0">
                        <span className="font-bold block text-slate-100">{c.symbol}</span>
                        <span className="text-3xs text-slate-500 truncate block">{c.company_name}</span>
                      </div>
                      <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-20">
                        <span className="text-3xs text-slate-500">entry ₹{c.price.toFixed(1)}</span>
                        {c.live_price != null ? (
                          <span className={`text-3xs font-bold ${(c.live_change_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                            ₹{c.live_price.toFixed(1)} {(c.live_change_pct ?? 0) >= 0 ? '+' : ''}{c.live_change_pct}%
                          </span>
                        ) : (
                          <span className="text-3xs text-slate-600">—</span>
                        )}
                      </div>
                      <div className="flex-shrink-0 flex flex-col items-end gap-0.5 w-16">
                        <span className="text-3xs font-bold text-sky-300">3:20 PM</span>
                        <span className="text-3xs text-slate-500">P(+3%) {c.prob_plus_3}%</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-3xs text-slate-500 text-center py-3">No prediction data available.</p>
            )}

            <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
              Honest result: on these picks the closing 10 minutes are, on aggregate, slightly <span className="text-sky-300 font-semibold">net‑positive</span>, so exiting at 3:20 PM is a touch behind hold‑to‑close ({trailScore?.baseline_overall && trailScore?.exit320_overall ? `${trailScore.exit320_overall.cum_pct}% vs ${trailScore.baseline_overall.cum_pct}% cum` : ''}). It is offered for those who prefer to be flat before the auction rather than as an edge.
            </p>
          </div>

          {/* Strategy Comparison hero card — all four exit rules side by side (same Top-5) */}
          <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-700/50 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2">
                <BarChart3 className="text-slate-300 w-4 h-4" />
                <h2 className="text-sm font-bold">Strategy Comparison · Top‑5 Exit Rules</h2>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-3xs font-bold text-slate-400 bg-slate-800/60 px-1.5 py-0.5 rounded">30‑day OOS</span>
                <button
                  onClick={() => fetchTrailScore()}
                  disabled={trailScoreLoading}
                  className="text-slate-400 hover:text-slate-200 disabled:opacity-50 transition-all"
                  title="Refresh"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${trailScoreLoading ? 'animate-spin text-slate-300' : ''}`} />
                </button>
              </div>
            </div>

            <p className="text-3xs text-slate-500 -mt-1">
              The identical Top‑5 selection, graded on <span className="text-slate-300 font-semibold">real 5‑minute paths</span> under four exit rules — so you can see exactly what each rule trades off.
            </p>

            {/* Comparison table */}
            <div className="overflow-hidden rounded-xl border border-slate-800/80">
              <table className="w-full text-3xs">
                <thead>
                  <tr className="bg-slate-950/60 text-slate-500">
                    <th className="text-left font-semibold py-1.5 px-2">Strategy</th>
                    <th className="text-right font-semibold py-1.5 px-2">Win</th>
                    <th className="text-right font-semibold py-1.5 px-2">Avg/pick</th>
                    <th className="text-right font-semibold py-1.5 px-2">≥ +2%</th>
                    <th className="text-right font-semibold py-1.5 px-2">Cum</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: 'Hold‑to‑close', o: trailScore?.baseline_overall, color: 'text-amber-300/90', note: 'baseline' },
                    { label: '+ 2% Trailing', o: trailScore?.overall, color: 'text-violet-300', note: 'best win‑rate' },
                    { label: '− 6% Disaster stop', o: trailScore?.stop_overall, color: 'text-emerald-300', note: 'best cum' },
                    { label: '3:20 PM Exit', o: trailScore?.exit320_overall, color: 'text-sky-300', note: 'flat by auction' },
                  ].map((row) => (
                    <tr key={row.label} className="border-t border-slate-800/60">
                      <td className="py-1.5 px-2">
                        <span className={`font-bold ${row.color}`}>{row.label}</span>
                        <span className="block text-slate-600 text-3xs">{row.note}</span>
                      </td>
                      <td className="text-right py-1.5 px-2 text-slate-200 font-bold">{row.o ? `${row.o.win_rate}%` : '—'}</td>
                      <td className={`text-right py-1.5 px-2 font-bold ${(row.o?.avg_cc ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                        {row.o ? `${row.o.avg_cc >= 0 ? '+' : ''}${row.o.avg_cc}%` : '—'}
                      </td>
                      <td className="text-right py-1.5 px-2 text-slate-300 font-bold">{row.o ? `${row.o.hit2_rate}%` : '—'}</td>
                      <td className={`text-right py-1.5 px-2 font-bold ${(row.o?.cum_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                        {row.o ? `${row.o.cum_pct >= 0 ? '+' : ''}${row.o.cum_pct}%` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="text-3xs text-slate-600 border-t border-slate-800/60 pt-2">
              All four hold the <span className="text-slate-300 font-semibold">same picks</span> — only the exit differs. <span className="text-violet-300 font-semibold">2% trailing</span> wins on consistency (highest win‑rate); the <span className="text-emerald-300 font-semibold">−6% stop</span> wins on total return by cutting only the rare disasters; <span className="text-sky-300 font-semibold">3:20 exit</span> ≈ hold‑to‑close. Cum = compounded daily mean across {trailScore?.overall ? trailScore.overall.scored / (trailScore?.top_n || 5) : '~'} sessions; backtest uses xgb for fast relative comparison.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}

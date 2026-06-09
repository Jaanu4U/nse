'use client';

import React, { useEffect, useState } from 'react';
import { useStockStore, API_BASE, Stock } from '../store/useStockStore';
import StockChart from '../components/StockChart';
import AuthPanel from '../components/AuthPanel';
import { 
  TrendingUp, TrendingDown, Search, Shield, RefreshCw, BarChart2, 
  Layers, Plus, Trash2, Filter, AlertTriangle, Cpu, Globe, Activity 
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
    watchlists, fetchWatchlists, selectedWatchlistId, createWatchlist, addToWatchlist, removeFromWatchlist,
    portfolios, fetchPortfolios, selectedPortfolioId, createPortfolio, addTransaction, portfolioDetail, fetchPortfolioDetail,
    token,
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

  // Screener states
  const [peFilter, setPeFilter] = useState<number>(50);
  const [rsiMin, setRsiMin] = useState<number>(30);
  const [rsiMax, setRsiMax] = useState<number>(70);
  const [roeMin, setRoeMin] = useState<number>(15);
  const [screenerResults, setScreenerResults] = useState<any[]>([]);
  const [screenerLoading, setScreenerLoading] = useState(false);

  // Portfolio Transaction Form states
  const [txSymbol, setTxSymbol] = useState(selectedSymbol);
  const [txType, setTxType] = useState('BUY');
  const [txQty, setTxQty] = useState(10);
  const [txPrice, setTxPrice] = useState(1500);
  const [txCharges, setTxCharges] = useState(20);
  const [txDate, setTxDate] = useState(new Date().toISOString().split('T')[0]);

  // Watchlist Creator states
  const [wlNameInput, setWlNameInput] = useState('');
  const [portNameInput, setPortNameInput] = useState('');

  // Fetch stocks list on mount
  useEffect(() => {
    fetchStocks();
  }, [fetchStocks]);

  // Sync auth data when token changes
  useEffect(() => {
    if (token) {
      fetchWatchlists();
      fetchPortfolios();
    }
  }, [token, fetchWatchlists, fetchPortfolios]);

  // Fetch data for selected Stock symbol
  const loadStockData = async (symbol: string) => {
    setLoadingAnalysis(true);
    setTxSymbol(symbol);
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

  // Filter stocks for autocomplete
  const filteredStocks = searchQuery 
    ? stocks.filter(s => 
        s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) || 
        s.company_name.toLowerCase().includes(searchQuery.toLowerCase())
      ).slice(0, 8)
    : [];

  // Active Watchlist
  const activeWatchlist = watchlists.find(w => w.id === selectedWatchlistId);

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

      {/* DASHBOARD CONTENT GRID */}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
        
        {/* LEFT PANEL: AUTH, WATCHLIST, PORTFOLIO */}
        <div className="xl:col-span-1 flex flex-col gap-6">
          <AuthPanel />

          {/* WATCHLIST SECTION */}
          {token && (
            <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <div className="flex items-center gap-2">
                  <Layers className="text-emerald-400 w-4 h-4" />
                  <h2 className="text-sm font-bold">Watchlists</h2>
                </div>
                
                {/* Watchlist Select dropdown */}
                <select
                  value={selectedWatchlistId || ""}
                  onChange={(e) => useStockStore.setState({ selectedWatchlistId: Number(e.target.value) })}
                  className="bg-slate-950 border border-slate-800 text-slate-300 text-2xs p-1 rounded focus:outline-none"
                >
                  {watchlists.map(w => (
                    <option key={w.id} value={w.id}>{w.name}</option>
                  ))}
                </select>
              </div>

              {/* Create new Watchlist */}
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="New watchlist name..."
                  value={wlNameInput}
                  onChange={(e) => setWlNameInput(e.target.value)}
                  className="flex-1 bg-slate-950/60 border border-slate-800 rounded-lg text-2xs px-2 py-1 text-slate-200 focus:outline-none"
                />
                <button
                  onClick={() => {
                    if (wlNameInput.trim()) {
                      createWatchlist(wlNameInput.trim());
                      setWlNameInput('');
                    }
                  }}
                  className="bg-emerald-600 hover:bg-emerald-500 text-slate-950 p-1.5 rounded-lg transition-all"
                >
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Active Watchlist item list */}
              {activeWatchlist ? (
                <div className="flex flex-col gap-2 max-h-40 overflow-y-auto">
                  {/* Dynamic add active symbol */}
                  <div className="flex items-center justify-between bg-slate-950/30 p-2 rounded-xl border border-slate-800/50">
                    <span className="text-2xs text-slate-400">Add current: <strong className="text-slate-200">{selectedSymbol}</strong></span>
                    <button
                      onClick={() => addToWatchlist(activeWatchlist.id, selectedSymbol)}
                      className="text-emerald-400 hover:text-emerald-300 text-2xs font-semibold px-2 py-0.5 border border-emerald-500/20 rounded-md"
                    >
                      Add
                    </button>
                  </div>
                  
                  {/* Display watchlist items */}
                  {/* In real database, watchlist items can be fetched. We'll simulate matching symbol items */}
                  {activeWatchlist.stocks && activeWatchlist.stocks.map((s: any) => (
                    <div key={s.id} className="flex items-center justify-between py-1 border-b border-slate-800/40 text-xs">
                      <button
                        onClick={() => setSelectedSymbol(s.symbol)}
                        className="font-bold text-slate-300 hover:text-emerald-400 text-left transition-all"
                      >
                        {s.symbol}
                      </button>
                      <button
                        onClick={() => removeFromWatchlist(activeWatchlist.id, s.symbol)}
                        className="text-slate-500 hover:text-red-400 transition-all"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-2xs text-slate-500 text-center">No watchlists found.</p>
              )}
            </div>
          )}

          {/* PORTFOLIO SECTION */}
          {token && (
            <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5 flex flex-col gap-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <div className="flex items-center gap-2">
                  <BarChart2 className="text-emerald-400 w-4 h-4" />
                  <h2 className="text-sm font-bold">Portfolios</h2>
                </div>
                
                <select
                  value={selectedPortfolioId || ""}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    useStockStore.setState({ selectedPortfolioId: id });
                    fetchPortfolioDetail(id);
                  }}
                  className="bg-slate-950 border border-slate-800 text-slate-300 text-2xs p-1 rounded focus:outline-none"
                >
                  {portfolios.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>

              {/* Create Portfolio */}
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="New portfolio name..."
                  value={portNameInput}
                  onChange={(e) => setPortNameInput(e.target.value)}
                  className="flex-1 bg-slate-950/60 border border-slate-800 rounded-lg text-2xs px-2 py-1 text-slate-200 focus:outline-none"
                />
                <button
                  onClick={() => {
                    if (portNameInput.trim()) {
                      createPortfolio(portNameInput.trim());
                      setPortNameInput('');
                    }
                  }}
                  className="bg-emerald-600 hover:bg-emerald-500 text-slate-950 p-1.5 rounded-lg transition-all"
                >
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Summary Dashboard */}
              {portfolioDetail?.summary && (
                <div className="grid grid-cols-2 gap-2 bg-slate-950/40 border border-slate-800/50 rounded-xl p-2.5">
                  <div className="flex flex-col">
                    <span className="text-3xs text-slate-500 uppercase font-semibold">Invested</span>
                    <span className="text-xs font-bold text-slate-200">₹{portfolioDetail.summary.total_invested}</span>
                  </div>
                  <div className="flex flex-col">
                    <span className="text-3xs text-slate-500 uppercase font-semibold">Value</span>
                    <span className="text-xs font-bold text-slate-200">₹{portfolioDetail.summary.total_current_value}</span>
                  </div>
                  <div className="flex flex-col col-span-2 border-t border-slate-800/50 pt-1 mt-1">
                    <span className="text-3xs text-slate-500 uppercase font-semibold">Unrealized P&L</span>
                    <span className={`text-xs font-bold flex items-center gap-1 ${portfolioDetail.summary.total_unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {portfolioDetail.summary.total_unrealized_pnl >= 0 ? '+' : ''}
                      ₹{portfolioDetail.summary.total_unrealized_pnl} ({portfolioDetail.summary.total_unrealized_pnl_pct}%)
                    </span>
                  </div>
                </div>
              )}

              {/* Add transaction form */}
              {selectedPortfolioId && (
                <form 
                  onSubmit={(e) => {
                    e.preventDefault();
                    addTransaction(selectedPortfolioId, txSymbol, txType, txQty, txPrice, txDate, txCharges);
                  }}
                  className="space-y-2.5 border-t border-slate-800 pt-3"
                >
                  <span className="block text-2xs font-bold text-slate-350">Add Trade (Record Ledger)</span>
                  
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      placeholder="Symbol"
                      value={txSymbol}
                      onChange={(e) => setTxSymbol(e.target.value.toUpperCase())}
                      className="bg-slate-950/60 border border-slate-800 rounded-lg text-2xs px-2 py-1 text-slate-200 focus:outline-none"
                    />
                    <select
                      value={txType}
                      onChange={(e) => setTxType(e.target.value)}
                      className="bg-slate-950 border border-slate-800 text-slate-300 text-2xs p-1 rounded focus:outline-none"
                    >
                      <option value="BUY">BUY</option>
                      <option value="SELL">SELL</option>
                    </select>
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="number"
                      placeholder="Qty"
                      value={txQty}
                      onChange={(e) => setTxQty(Number(e.target.value))}
                      className="bg-slate-950/60 border border-slate-800 rounded-lg text-2xs px-2 py-1 text-slate-200 focus:outline-none"
                    />
                    <input
                      type="number"
                      placeholder="Price"
                      value={txPrice}
                      onChange={(e) => setTxPrice(Number(e.target.value))}
                      className="bg-slate-950/60 border border-slate-800 rounded-lg text-2xs px-2 py-1 text-slate-200 focus:outline-none"
                    />
                  </div>

                  <button
                    type="submit"
                    className="w-full py-1 bg-emerald-600 hover:bg-emerald-500 text-slate-950 text-2xs font-bold rounded-lg transition-all shadow-md"
                  >
                    Add Trade
                  </button>
                </form>
              )}
            </div>
          )}
        </div>

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
                              
                              <p className="text-3xs text-slate-500 italic mt-2 text-center">Estimation uses calibrated XGBoost models based on 3-year feature runs.</p>
                            </div>
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
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 bg-slate-950/30 border border-slate-800/50 p-3.5 rounded-2xl">
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
                      </div>
                    )}

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

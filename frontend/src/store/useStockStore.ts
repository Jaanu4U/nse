import { create } from 'zustand';

export const API_BASE = "http://localhost:8000/api/v1";

export interface Stock {
  id: number;
  symbol: string;
  company_name: string;
  isin?: string;
  industry?: string;
}

export interface Watchlist {
  id: number;
  name: string;
  stocks?: Stock[];
}

export interface PortfolioSummary {
  total_invested: number;
  total_current_value: number;
  total_unrealized_pnl: number;
  total_unrealized_pnl_pct: number;
  total_realized_pnl: number;
  total_charges: number;
  net_pnl: number;
}

export interface Holding {
  stock_id: number;
  symbol: string;
  company_name: string;
  quantity: number;
  avg_buy_price: number;
  current_price: number;
  invested_value: number;
  current_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  realized_pnl: number;
  allocation_pct: number;
}

export interface PortfolioDetail {
  summary: PortfolioSummary;
  holdings: Holding[];
}

interface StockState {
  selectedSymbol: string;
  activeTab: string;
  stocks: Stock[];
  watchlists: Watchlist[];
  portfolios: any[];
  selectedWatchlistId: number | null;
  selectedPortfolioId: number | null;
  portfolioDetail: PortfolioDetail | null;
  token: string | null;
  email: string | null;
  fiiDiiData: any | null;
  insiderTrades: any | null;
  optionsChain: any | null;
  
  // Actions
  setSelectedSymbol: (symbol: string) => void;
  setActiveTab: (tab: string) => void;
  setToken: (token: string | null, email: string | null) => void;
  fetchStocks: () => Promise<void>;
  fetchWatchlists: () => Promise<void>;
  fetchPortfolios: () => Promise<void>;
  fetchPortfolioDetail: (id: number) => Promise<void>;
  createWatchlist: (name: string) => Promise<void>;
  addToWatchlist: (watchlistId: number, symbol: string) => Promise<void>;
  removeFromWatchlist: (watchlistId: number, symbol: string) => Promise<void>;
  createPortfolio: (name: string) => Promise<void>;
  addTransaction: (portfolioId: number, symbol: string, txType: string, qty: number, price: number, date: string, charges?: number) => Promise<void>;
  fetchFiiDiiData: (limit?: number) => Promise<void>;
  fetchInsiderTrades: (symbol: string) => Promise<void>;
  fetchOptionsChain: (symbol: string) => Promise<void>;
}

export const useStockStore = create<StockState>((set, get) => ({
  selectedSymbol: "RELIANCE",
  activeTab: "chart",
  stocks: [],
  watchlists: [],
  portfolios: [],
  selectedWatchlistId: null,
  selectedPortfolioId: null,
  portfolioDetail: null,
  token: typeof window !== 'undefined' ? localStorage.getItem('token') : null,
  email: typeof window !== 'undefined' ? localStorage.getItem('email') : null,
  fiiDiiData: null,
  insiderTrades: null,
  optionsChain: null,

  setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol.toUpperCase() }),
  setActiveTab: (tab) => set({ activeTab: tab }),
  
  setToken: (token, email) => {
    if (token && email) {
      localStorage.setItem('token', token);
      localStorage.setItem('email', email);
      set({ token, email });
    } else {
      localStorage.removeItem('token');
      localStorage.removeItem('email');
      set({ token: null, email: null, watchlists: [], portfolios: [], portfolioDetail: null });
    }
  },

  fetchStocks: async () => {
    try {
      const res = await fetch(`${API_BASE}/stocks/`);
      if (res.ok) {
        const data = await res.json();
        set({ stocks: data });
      }
    } catch (e) {
      console.error("Failed to fetch stock master:", e);
    }
  },

  fetchWatchlists: async () => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/watchlists`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        set({ 
          watchlists: data,
          selectedWatchlistId: get().selectedWatchlistId || (data.length > 0 ? data[0].id : null)
        });
      }
    } catch (e) {
      console.error("Failed to fetch watchlists:", e);
    }
  },

  fetchPortfolios: async () => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/portfolios`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        set({ 
          portfolios: data,
          selectedPortfolioId: get().selectedPortfolioId || (data.length > 0 ? data[0].id : null)
        });
        if (data.length > 0) {
          get().fetchPortfolioDetail(get().selectedPortfolioId || data[0].id);
        }
      }
    } catch (e) {
      console.error("Failed to fetch portfolios:", e);
    }
  },

  fetchPortfolioDetail: async (id: number) => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/portfolios/${id}`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        set({ portfolioDetail: data, selectedPortfolioId: id });
      }
    } catch (e) {
      console.error("Failed to fetch portfolio detail:", e);
    }
  },

  createWatchlist: async (name: string) => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/watchlists`, {
        method: "POST",
        headers: { 
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ name })
      });
      if (res.ok) {
        await get().fetchWatchlists();
      }
    } catch (e) {
      console.error("Failed to create watchlist:", e);
    }
  },

  addToWatchlist: async (watchlistId: number, symbol: string) => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/watchlists/${watchlistId}/items/${symbol}`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        await get().fetchWatchlists();
      }
    } catch (e) {
      console.error("Failed to add stock to watchlist:", e);
    }
  },

  removeFromWatchlist: async (watchlistId: number, symbol: string) => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/watchlists/${watchlistId}/items/${symbol}`, {
        method: "DELETE",
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        await get().fetchWatchlists();
      }
    } catch (e) {
      console.error("Failed to remove stock from watchlist:", e);
    }
  },

  createPortfolio: async (name: string) => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/portfolios`, {
        method: "POST",
        headers: { 
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ name })
      });
      if (res.ok) {
        await get().fetchPortfolios();
      }
    } catch (e) {
      console.error("Failed to create portfolio:", e);
    }
  },

  addTransaction: async (portfolioId, symbol, txType, qty, price, date, charges = 0.0) => {
    const token = get().token;
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/portfolios/${portfolioId}/transactions`, {
        method: "POST",
        headers: { 
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          symbol: symbol.toUpperCase(),
          transaction_type: txType.toUpperCase(),
          quantity: qty,
          price: price,
          transaction_date: date,
          charges: charges
        })
      });
      if (res.ok) {
        await get().fetchPortfolioDetail(portfolioId);
      }
    } catch (e) {
      console.error("Failed to add transaction:", e);
    }
  },

  fetchFiiDiiData: async (limit = 30) => {
    try {
      const res = await fetch(`${API_BASE}/fii-dii/activity?limit=${limit}`);
      if (res.ok) {
        const data = await res.json();
        set({ fiiDiiData: data });
      }
    } catch (e) {
      console.error("Failed to fetch FII/DII activity:", e);
    }
  },

  fetchInsiderTrades: async (symbol) => {
    try {
      const res = await fetch(`${API_BASE}/stocks/${symbol}/insider-trades`);
      if (res.ok) {
        const data = await res.json();
        set({ insiderTrades: data });
      }
    } catch (e) {
      console.error("Failed to fetch insider trades:", e);
    }
  },

  fetchOptionsChain: async (symbol) => {
    try {
      const res = await fetch(`${API_BASE}/stocks/${symbol}/options-chain`);
      if (res.ok) {
        const data = await res.json();
        set({ optionsChain: data });
      }
    } catch (e) {
      console.error("Failed to fetch options chain:", e);
    }
  }
}));

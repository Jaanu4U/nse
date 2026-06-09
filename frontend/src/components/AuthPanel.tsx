'use client';

import React, { useState } from 'react';
import { useStockStore, API_BASE } from '../store/useStockStore';

export default function AuthPanel() {
  const { setToken, token, email } = useStockStore();
  const [isLogin, setIsLogin] = useState(true);
  const [emailInput, setEmailInput] = useState('');
  const [passwordInput, setPasswordInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      if (isLogin) {
        // Build URL encoded form data for OAuth2PasswordRequestForm
        const formData = new URLSearchParams();
        formData.append('username', emailInput);
        formData.append('password', passwordInput);

        const res = await fetch(`${API_BASE}/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: formData.toString()
        });

        const data = await res.json();
        if (res.ok) {
          setToken(data.access_token, emailInput);
        } else {
          setError(data.detail || "Authentication failed");
        }
      } else {
        const res = await fetch(`${API_BASE}/auth/signup`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: emailInput, password: passwordInput })
        });

        const data = await res.json();
        if (res.ok) {
          // Auto login after sign up
          const formData = new URLSearchParams();
          formData.append('username', emailInput);
          formData.append('password', passwordInput);

          const loginRes = await fetch(`${API_BASE}/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: formData.toString()
          });
          const loginData = await loginRes.json();
          if (loginRes.ok) {
            setToken(loginData.access_token, emailInput);
          }
        } else {
          setError(data.detail || "Registration failed");
        }
      }
    } catch (err: any) {
      setError("Network or server connection error.");
    } finally {
      setLoading(false);
    }
  };

  if (token) {
    return (
      <div className="flex items-center justify-between p-4 bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl">
        <div className="flex flex-col">
          <span className="text-xs text-slate-400 font-medium">Logged in as</span>
          <span className="text-sm text-emerald-400 font-semibold truncate max-w-[150px]">{email}</span>
        </div>
        <button
          onClick={() => setToken(null, null)}
          className="px-3 py-1.5 bg-red-950/40 border border-red-800/50 hover:bg-red-900/40 text-red-300 text-xs font-semibold rounded-lg transition-all"
        >
          Sign Out
        </button>
      </div>
    );
  }

  return (
    <div className="w-full bg-slate-900/40 backdrop-blur-md border border-slate-800/80 rounded-2xl shadow-xl p-5">
      <div className="flex border-b border-slate-800 mb-4">
        <button
          onClick={() => { setIsLogin(true); setError(null); }}
          className={`flex-1 pb-2 text-sm font-semibold transition-all ${isLogin ? 'text-emerald-400 border-b-2 border-emerald-400' : 'text-slate-400 hover:text-slate-200'}`}
        >
          Login
        </button>
        <button
          onClick={() => { setIsLogin(false); setError(null); }}
          className={`flex-1 pb-2 text-sm font-semibold transition-all ${!isLogin ? 'text-emerald-400 border-b-2 border-emerald-400' : 'text-slate-400 hover:text-slate-200'}`}
        >
          Sign Up
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        {error && (
          <div className="p-2.5 bg-red-950/30 border border-red-800/30 rounded-lg text-center">
            <span className="text-red-400 text-xs font-medium">{error}</span>
          </div>
        )}

        <div>
          <label className="block text-xs text-slate-400 font-semibold mb-1">Email Address</label>
          <input
            type="email"
            value={emailInput}
            onChange={(e) => setEmailInput(e.target.value)}
            required
            className="w-full px-3 py-2 bg-slate-950/50 border border-slate-850 rounded-xl text-slate-200 text-sm focus:outline-none focus:border-emerald-500/50 transition-all"
            placeholder="name@email.com"
          />
        </div>

        <div>
          <label className="block text-xs text-slate-400 font-semibold mb-1">Password</label>
          <input
            type="password"
            value={passwordInput}
            onChange={(e) => setPasswordInput(e.target.value)}
            required
            className="w-full px-3 py-2 bg-slate-950/50 border border-slate-850 rounded-xl text-slate-200 text-sm focus:outline-none focus:border-emerald-500/50 transition-all"
            placeholder="••••••••"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-slate-950 text-sm font-semibold rounded-xl shadow-lg transition-all disabled:opacity-50 flex items-center justify-center gap-2"
        >
          {loading ? (
            <div className="w-4 h-4 border-2 border-slate-950 border-t-transparent rounded-full animate-spin"></div>
          ) : (
            isLogin ? "Log In" : "Create Account"
          )}
        </button>
      </form>
    </div>
  );
}

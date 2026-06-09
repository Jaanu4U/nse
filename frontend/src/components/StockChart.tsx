'use client';

import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickSeries, HistogramSeries, LineSeries } from 'lightweight-charts';
import { API_BASE } from '../store/useStockStore';

interface StockChartProps {
  symbol: string;
  showEma20: boolean;
  showEma50: boolean;
  showEma200: boolean;
  showBb: boolean;
  showVwap: boolean;
  showSrLevels: boolean;
}

export default function StockChart({
  symbol,
  showEma20,
  showEma50,
  showEma200,
  showBb,
  showVwap,
  showSrLevels
}: StockChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    setLoading(true);
    setError(null);

    // Create container element
    const container = chartContainerRef.current;
    
    // Create Chart
    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: 'rgba(15, 23, 42, 0.6)' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.5)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.5)' },
      },
      crosshair: {
        mode: 1, // Normal crosshair
        vertLine: { color: '#64748b', labelBackgroundColor: '#1e293b' },
        horzLine: { color: '#64748b', labelBackgroundColor: '#1e293b' },
      },
      rightPriceScale: {
        borderColor: 'rgba(51, 65, 85, 0.5)',
      },
      timeScale: {
        borderColor: 'rgba(51, 65, 85, 0.5)',
        timeVisible: true,
      },
      width: container.clientWidth,
      height: 450,
    });

    chartRef.current = chart;

    // Add Series
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#26a69a',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '', // overlay
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8, // volume at the bottom 20%
        bottom: 0,
      },
    });

    // Subscriptions and references for overlays
    let ema20Series: ISeriesApi<'Line'> | null = null;
    let ema50Series: ISeriesApi<'Line'> | null = null;
    let ema200Series: ISeriesApi<'Line'> | null = null;
    let bbUpperSeries: ISeriesApi<'Line'> | null = null;
    let bbLowerSeries: ISeriesApi<'Line'> | null = null;
    let vwapSeries: ISeriesApi<'Line'> | null = null;

    // Load data
    Promise.all([
      fetch(`${API_BASE}/stocks/${symbol}/chart/daily`),
      fetch(`${API_BASE}/stocks/${symbol}/analysis`),
      fetch(`${API_BASE}/stocks/${symbol}/news`) // to trigger background refresh
    ])
      .then(async ([chartRes, analysisRes]) => {
        if (!chartRes.ok) throw new Error("Failed to load chart data");
        const chartData = await chartRes.json();
        
        if (chartData.length === 0) {
          setError("No price history available");
          setLoading(false);
          return;
        }

        candlestickSeries.setData(chartData);

        // Format volume data
        const volData = chartData.map((d: any) => ({
          time: d.time,
          value: d.volume,
          color: d.close >= d.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)',
        }));
        volumeSeries.setData(volData);

        // Load technical indicator overlays if active
        // Let's call indicators API
        const indRes = await fetch(`${API_BASE}/stocks/${symbol}/analysis`);
        const analysisData = await analysisRes.json();

        // Fetch direct indicator series from Backend
        const indicatorsRes = await fetch(`${API_BASE}/stocks/${symbol}/chart/daily`); // reuse details or call custom endpoint if desired
        // We will fetch technical analysis details to overlay EMA/BB/S&R
        
        // S&R Level overlays
        if (showSrLevels && analysisData.supports && analysisData.resistances) {
          analysisData.supports.slice(0, 3).forEach((s: any) => {
            candlestickSeries.createPriceLine({
              price: s.price,
              color: '#22c55e',
              lineWidth: 1,
              lineStyle: 2, // dashed
              axisLabelVisible: true,
              title: `SUP (Conf: ${s.confidence})`,
            });
          });

          analysisData.resistances.slice(0, 3).forEach((r: any) => {
            candlestickSeries.createPriceLine({
              price: r.price,
              color: '#ef4444',
              lineWidth: 1,
              lineStyle: 2, // dashed
              axisLabelVisible: true,
              title: `RES (Conf: ${r.confidence})`,
            });
          });
        }

        // Standard Indicator overlay values can also be dynamically calculated on client
        // or fetched from a dedicated technical indicators API.
        // Let's compute simple overlays client-side for absolute reliability!
        const prices = chartData.map((d: any) => d.close);
        const times = chartData.map((d: any) => d.time);

        // Helper to compute EMA
        const computeEMA = (data: number[], period: number) => {
          const k = 2 / (period + 1);
          let emaArray = [];
          let ema = data[0]; // Seed
          emaArray.push(ema);
          for (let i = 1; i < data.length; i++) {
            ema = data[i] * k + ema * (1 - k);
            emaArray.push(ema);
          }
          return emaArray;
        };

        if (showEma20) {
          const ema20Val = computeEMA(prices, 20);
          ema20Series = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2, title: 'EMA 20' });
          ema20Series.setData(times.map((t: string, idx: number) => ({ time: t, value: ema20Val[idx] })));
        }

        if (showEma50) {
          const ema50Val = computeEMA(prices, 50);
          ema50Series = chart.addSeries(LineSeries, { color: '#eab308', lineWidth: 2, title: 'EMA 50' });
          ema50Series.setData(times.map((t: string, idx: number) => ({ time: t, value: ema50Val[idx] })));
        }

        if (showEma200) {
          const ema200Val = computeEMA(prices, 200);
          ema200Series = chart.addSeries(LineSeries, { color: '#ec4899', lineWidth: 2, title: 'EMA 200' });
          ema200Series.setData(times.map((t: string, idx: number) => ({ time: t, value: ema200Val[idx] })));
        }

        // Simple BB bands client-side helper
        if (showBb) {
          const period = 20;
          let upperBand = [];
          let lowerBand = [];
          for (let i = 0; i < prices.length; i++) {
            if (i < period - 1) {
              upperBand.push(null);
              lowerBand.push(null);
              continue;
            }
            const slice = prices.slice(i - period + 1, i + 1);
            const sum = slice.reduce((a: number, b: number) => a + b, 0);
            const mean = sum / period;
            const variance = slice.reduce((a: number, b: number) => a + Math.pow(b - mean, 2), 0) / period;
            const stdDev = Math.sqrt(variance);
            upperBand.push(mean + 2 * stdDev);
            lowerBand.push(mean - 2 * stdDev);
          }
          
          bbUpperSeries = chart.addSeries(LineSeries, { color: 'rgba(100, 116, 139, 0.6)', lineWidth: 1, lineStyle: 1 });
          bbUpperSeries.setData(times.map((t: string, idx: number) => ({ time: t, value: upperBand[idx] || 0 })).filter((d: any) => d.value > 0));
          
          bbLowerSeries = chart.addSeries(LineSeries, { color: 'rgba(100, 116, 139, 0.6)', lineWidth: 1, lineStyle: 1 });
          bbLowerSeries.setData(times.map((t: string, idx: number) => ({ time: t, value: lowerBand[idx] || 0 })).filter((d: any) => d.value > 0));
        }

        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setError("Error fetching chart: " + err.message);
        setLoading(false);
      });

    // Resize handler
    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [symbol, showEma20, showEma50, showEma200, showBb, showVwap, showSrLevels]);

  return (
    <div className="relative w-full h-[450px] bg-slate-900/40 backdrop-blur-md rounded-2xl border border-slate-800/80 shadow-2xl p-4 overflow-hidden">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-950/70 z-10 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3">
            <div className="w-10 h-10 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin"></div>
            <p className="text-slate-400 text-sm font-medium">Syncing chart data...</p>
          </div>
        </div>
      )}
      
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-950/80 z-10 p-6">
          <div className="bg-red-950/40 border border-red-800/50 rounded-xl p-4 text-center max-w-sm">
            <p className="text-red-400 text-sm font-semibold mb-2">Error Displaying Chart</p>
            <p className="text-slate-300 text-xs">{error}</p>
          </div>
        </div>
      )}
      
      <div ref={chartContainerRef} className="w-full h-full" />
    </div>
  );
}

import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart2,
  Brain,
  CheckCircle2,
  FlaskConical,
  Globe,
  LayoutGrid,
  RefreshCw,
  Search,
  ShieldCheck,
  Wallet,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type {
  AccountInfo,
  BacktestRun,
  OrderItem,
  PositionItem,
  QuoteItem,
  RecommendationItem,
  SocialFeedItem,
  TechnicalSummary,
  TradingViewTechnicals,
} from './types';

type TabKey = 'screener' | 'technicals' | 'portfolio' | 'advisor' | 'social' | 'backtest';

const API_BASE = (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_API_URL?.replace(/\/$/, '') || '';

const PIE_COLORS = ['#22c55e', '#38bdf8', '#a855f7', '#f59e0b', '#ec4899', '#14b8a6'];

function heatmapColor(chg: number): string {
  if (chg >= 2.5) return 'bg-[#15803d] border-[#22c55e]/50 text-white';
  if (chg >= 0.5) return 'bg-[#166534] border-[#22c55e]/30 text-emerald-50';
  if (chg > -0.5) return 'bg-[#1f2937] border-[#374151] text-slate-200';
  if (chg > -2.5) return 'bg-[#7f1d1d] border-[#ef4444]/30 text-rose-50';
  return 'bg-[#991b1b] border-[#ef4444]/50 text-white';
}

function verdictPill(verdict?: string): string {
  if (!verdict) return 'bg-slate-800 text-slate-300 border-slate-700';
  if (verdict.includes('BUY')) return 'bg-emerald-950/90 text-emerald-300 border-emerald-700/60';
  if (verdict.includes('SELL')) return 'bg-rose-950/90 text-rose-300 border-rose-700/60';
  return 'bg-slate-800 text-slate-300 border-slate-700';
}

function ratingColor(rating: string): string {
  const r = rating.toLowerCase();
  if (r.includes('buy')) return 'text-sky-400';
  if (r.includes('sell')) return 'text-rose-400';
  return 'text-slate-300';
}

function GaugeMeter({ title, summary }: { title: string; summary: TechnicalSummary }) {
  // Map score [-1, +1] to angle [-75deg, +75deg]
  const angle = Math.max(-75, Math.min(75, summary.score * 75));
  return (
    <div className="border border-[#30363d] rounded bg-[#161b22] p-4 flex flex-col items-center">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-300 mb-2">{title}</div>
      <svg viewBox="0 0 200 115" className="w-48 h-28">
        {/* 5 colored arc segments: Strong Sell -> Strong Buy */}
        <path d="M 20 100 A 80 80 0 0 1 43 43" fill="none" stroke="#ef4444" strokeWidth="10" strokeLinecap="round" />
        <path d="M 47 39 A 80 80 0 0 1 82 22" fill="none" stroke="#f97316" strokeWidth="10" />
        <path d="M 86 21 A 80 80 0 0 1 114 21" fill="none" stroke="#64748b" strokeWidth="10" />
        <path d="M 118 22 A 80 80 0 0 1 153 39" fill="none" stroke="#38bdf8" strokeWidth="10" />
        <path d="M 157 43 A 80 80 0 0 1 180 100" fill="none" stroke="#22c55e" strokeWidth="10" strokeLinecap="round" />
        {/* Needle */}
        <g transform={`translate(100, 100) rotate(${angle})`}>
          <line x1="0" y1="0" x2="0" y2="-66" stroke="#e6edf3" strokeWidth="3" strokeLinecap="round" />
          <circle cx="0" cy="0" r="5" fill="#e6edf3" />
        </g>
      </svg>
      <div className={`text-sm font-bold font-mono -mt-1 ${ratingColor(summary.rating)}`}>
        {summary.rating}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-4 text-center font-mono text-xs w-full">
        <div>
          <div className="text-rose-400 font-bold">{summary.sell}</div>
          <div className="text-[10px] text-slate-400">Sell</div>
        </div>
        <div>
          <div className="text-slate-300 font-bold">{summary.neutral}</div>
          <div className="text-[10px] text-slate-400">Neutral</div>
        </div>
        <div>
          <div className="text-sky-400 font-bold">{summary.buy}</div>
          <div className="text-[10px] text-slate-400">Buy</div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<TabKey>('screener');
  const [wsConnected, setWsConnected] = useState(false);
  const [lastTickAt, setLastTickAt] = useState<string>('');
  const [moomooMode, setMoomooMode] = useState('CHECKING...');
  const [executionMode, setExecutionMode] = useState<'SEMI_AUTO' | 'FULL_AUTO'>('SEMI_AUTO');
  const [circuitBreaker, setCircuitBreaker] = useState(false);

  const [quotes, setQuotes] = useState<QuoteItem[]>([]);
  const [movers, setMovers] = useState<QuoteItem[]>([]);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [navHistory, setNavHistory] = useState<Array<{ ts: string; nav: number }>>([]);
  const [recommendations, setRecommendations] = useState<RecommendationItem[]>([]);
  const [socialItems, setSocialItems] = useState<SocialFeedItem[]>([]);
  const [backtests, setBacktests] = useState<BacktestRun[]>([]);

  const [searchTicker, setSearchTicker] = useState('US.MU');
  const [techTicker, setTechTicker] = useState('US.MU');
  const [techTimeframe, setTechTimeframe] = useState<'1H' | '1D' | '1W'>('1D');
  const [technicalsData, setTechnicalsData] = useState<TradingViewTechnicals | null>(null);
  const [socialFilter, setSocialFilter] = useState('');
  const [btTicker, setBtTicker] = useState('US.NVDA');
  const [btStrategy, setBtStrategy] = useState('rsi_macd_momentum');
  const [btBenchmark, setBtBenchmark] = useState('US.SPY');
  const [btDays, setBtDays] = useState(730);

  const [manualTicker, setManualTicker] = useState('US.AAPL');
  const [manualSide, setManualSide] = useState<'BUY' | 'SELL'>('BUY');
  const [manualQty, setManualQty] = useState(5);
  const [manualSL, setManualSL] = useState(215);

  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [closingTickers, setClosingTickers] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);

  const showToast = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 5000);
  };

  const fetchAll = async () => {
    try {
      const [hRes, qRes, mRes, pRes, rRes, sRes, bRes] = await Promise.all([
        fetch(`${API_BASE}/health`).then((r) => r.json()),
        fetch(`${API_BASE}/api/quotes`).then((r) => r.json()),
        fetch(`${API_BASE}/api/movers?limit=12`).then((r) => r.json()),
        fetch(`${API_BASE}/api/portfolio`).then((r) => r.json()),
        fetch(`${API_BASE}/api/recommendations`).then((r) => r.json()),
        fetch(`${API_BASE}/api/social?limit=80`).then((r) => r.json()),
        fetch(`${API_BASE}/api/backtests`).then((r) => r.json()),
      ]);
      setMoomooMode(hRes.moomoo_mode || 'OFFLINE');
      setExecutionMode(hRes.execution_mode || 'SEMI_AUTO');
      setQuotes(qRes.quotes || []);
      setMovers(mRes.movers || []);
      setAccount(pRes.account || null);
      setPositions(pRes.positions || []);
      setOrders(pRes.orders || []);
      setNavHistory(pRes.nav_history || []);
      setCircuitBreaker(Boolean(pRes.circuit_breaker_tripped));
      setRecommendations(rRes.recommendations || []);
      setSocialItems(sRes.items || []);
      setBacktests(bRes.backtests || []);
    } catch {
      setMoomooMode('BACKEND_OFFLINE');
    }
  };

  useEffect(() => {
    fetchAll();
    const wsBase = API_BASE
      ? API_BASE.replace(/^http/, 'ws')
      : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;
    const ws = new WebSocket(`${wsBase}/ws/stream`);
    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'tick_update') {
          setLastTickAt(new Date().toLocaleTimeString());
          if (msg.account) {
            setAccount(msg.account);
            setNavHistory((prev) => [
              ...prev.slice(-199),
              { ts: new Date().toISOString(), nav: Number(msg.account.nav) },
            ]);
          }
          if (msg.positions) setPositions(msg.positions);
          if (msg.quotes) {
            setQuotes((prev) => {
              const prevMap = new Map(prev.map((p) => [p.symbol, p]));
              return msg.quotes.map((q: QuoteItem) => ({
                ...prevMap.get(q.symbol),
                ...q,
              }));
            });
          }
          if (msg.execution_mode) setExecutionMode(msg.execution_mode);
          setCircuitBreaker(Boolean(msg.circuit_breaker));
        }
      } catch {
        // ignore malformed frame
      }
    };
    return () => ws.close();
  }, []);

  const toggleMode = async (mode: 'SEMI_AUTO' | 'FULL_AUTO') => {
    const res = await fetch(`${API_BASE}/api/settings/mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    if (res.ok) {
      setExecutionMode(mode);
      showToast('ok', `Execution mode switched to ${mode}`);
    }
  };

  const runResearch = async (ticker: string) => {
    setBusyAction(`research:${ticker}`);
    try {
      const res = await fetch(`${API_BASE}/api/research/${encodeURIComponent(ticker)}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Research failed');
      showToast('ok', `${data.recommendation.ticker}: ${data.recommendation.verdict} (${data.recommendation.confidence}%)`);
      await fetchAll();
      setTab('advisor');
    } catch (e: unknown) {
      showToast('err', e instanceof Error ? e.message : 'Research failed');
    } finally {
      setBusyAction(null);
    }
  };

  const loadTechnicals = async (ticker: string, tf: '1H' | '1D' | '1W' = techTimeframe) => {
    setBusyAction(`tech:${ticker}`);
    try {
      const res = await fetch(`${API_BASE}/api/technicals/${encodeURIComponent(ticker)}?timeframe=${tf}`);
      const ctype = res.headers.get('content-type') || '';
      if (!ctype.includes('application/json')) {
        throw new Error('Backend API unreachable (set VITE_API_URL in Vercel or use http://127.0.0.1:8000)');
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load technicals');
      setTechnicalsData(data);
    } catch (e: unknown) {
      showToast('err', e instanceof Error ? e.message : 'Failed to load technicals');
    } finally {
      setBusyAction(null);
    }
  };

  useEffect(() => {
    if (tab === 'technicals') {
      loadTechnicals(techTicker, techTimeframe);
    }
  }, [tab, techTicker, techTimeframe]);

  const scanTopMovers = async () => {
    setBusyAction('scan_movers');
    try {
      const res = await fetch(`${API_BASE}/api/scan-movers?top_n=8`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Mover scan failed');
      showToast('ok', `Scanned ${data.scanned_count} top high-moving stocks`);
      await fetchAll();
      setTab('advisor');
    } catch (e: unknown) {
      showToast('err', e instanceof Error ? e.message : 'Mover scan failed');
    } finally {
      setBusyAction(null);
    }
  };

  const approveRec = async (recId: string) => {
    setBusyAction(`exec:${recId}`);
    try {
      const res = await fetch(`${API_BASE}/api/recommendations/${recId}/execute`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Execution rejected');
      showToast('ok', `Order ${data.order.order_id} (${data.order.side} ${data.order.qty} ${data.order.ticker}) -> ${data.order.status}`);
      await fetchAll();
    } catch (e: unknown) {
      showToast('err', e instanceof Error ? e.message : 'Order rejected');
    } finally {
      setBusyAction(null);
    }
  };

  const submitManualOrder = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusyAction('manual_order');
    try {
      const res = await fetch(`${API_BASE}/api/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: manualTicker,
          side: manualSide,
          qty: manualQty,
          stop_loss: manualSide === 'BUY' ? manualSL : null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Order rejected');
      showToast('ok', `Order ${data.order.order_id} -> ${data.order.status}`);
      await fetchAll();
    } catch (err: unknown) {
      showToast('err', err instanceof Error ? err.message : 'Order failed');
    } finally {
      setBusyAction(null);
    }
  };

  const closePosition = async (pos: PositionItem) => {
    setClosingTickers((prev) => ({ ...prev, [pos.ticker]: true }));
    try {
      const side = pos.qty < 0 ? 'BUY' : 'SELL';
      const qty = Math.abs(pos.qty);
      const res = await fetch(`${API_BASE}/api/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: pos.ticker,
          side,
          qty,
          stop_loss: null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Close failed');
      showToast('ok', `Closed ${pos.symbol} (${side} ${qty}) — P&L booked in SIMULATE`);
      await fetchAll();
    } catch (err: unknown) {
      showToast('err', err instanceof Error ? err.message : 'Close failed');
    } finally {
      setClosingTickers((prev) => {
        const next = { ...prev };
        delete next[pos.ticker];
        return next;
      });
    }
  };

  const closeAllPositions = async () => {
    setBusyAction('close_all');
    try {
      const res = await fetch(`${API_BASE}/api/positions/close-all`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Close all failed');
      showToast('ok', `Submitted closing orders for ${data.closed_count} position(s) in SIMULATE`);
      await fetchAll();
    } catch (err: unknown) {
      showToast('err', err instanceof Error ? err.message : 'Close all failed');
    } finally {
      setBusyAction(null);
    }
  };

  const cancelOrder = async (orderId: string) => {
    setBusyAction(`cancel:${orderId}`);
    try {
      const res = await fetch(`${API_BASE}/api/orders/${encodeURIComponent(orderId)}/cancel`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Cancel failed');
      showToast('ok', `Cancelled order ${orderId}`);
      await fetchAll();
    } catch (err: unknown) {
      showToast('err', err instanceof Error ? err.message : 'Cancel failed');
    } finally {
      setBusyAction(null);
    }
  };

  const cancelAllOrders = async () => {
    setBusyAction('cancel_all');
    try {
      const res = await fetch(`${API_BASE}/api/orders/cancel-all`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Cancel all failed');
      showToast('ok', `Cancelled ${data.cancelled_count} open order(s)`);
      await fetchAll();
    } catch (err: unknown) {
      showToast('err', err instanceof Error ? err.message : 'Cancel all failed');
    } finally {
      setBusyAction(null);
    }
  };

  const runBacktest = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusyAction('backtest');
    try {
      const res = await fetch(`${API_BASE}/api/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: btTicker,
          strategy: btStrategy,
          benchmark: btBenchmark,
          lookback_days: btDays,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Backtest failed');
      showToast(data.passed_gatekeeper ? 'ok' : 'err', data.gatekeeper_reason);
      await fetchAll();
    } catch (err: unknown) {
      showToast('err', err instanceof Error ? err.message : 'Backtest failed');
    } finally {
      setBusyAction(null);
    }
  };

  const filteredSocial = useMemo(() => {
    const q = socialFilter.trim().toUpperCase();
    if (!q) return socialItems;
    return socialItems.filter(
      (it) => it.ticker.toUpperCase().includes(q) || it.platform.toUpperCase().includes(q)
    );
  }, [socialItems, socialFilter]);

  const openOrders = useMemo(
    () => orders.filter((o) => ['OPEN', 'SUBMITTED', 'UNSUBMITTED'].includes(String(o.status).toUpperCase())),
    [orders]
  );

  const latestBacktest = backtests[0] || null;

  return (
    <div className="min-h-screen bg-[#0d1117] text-[#e6edf3] flex flex-col">
      {/* Finviz-style Top Bar */}
      <header className="border-b border-[#30363d] bg-[#161b22] px-4 py-2 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-base font-bold tracking-tight text-emerald-400 font-mono">
            QUANTVIZ<span className="text-slate-400 font-normal">.SIM</span>
          </span>
          <span className="inline-flex items-center gap-1.5 rounded border border-emerald-700/60 bg-emerald-950/60 px-2 py-0.5 text-[11px] font-mono text-emerald-300">
            <ShieldCheck className="w-3.5 h-3.5" /> TrdEnv.SIMULATE
          </span>
          <span className="inline-flex items-center gap-1.5 rounded border border-[#30363d] bg-[#0d1117] px-2 py-0.5 text-[11px] font-mono text-slate-300">
            <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
            {moomooMode}
          </span>
          {circuitBreaker && (
            <span className="inline-flex items-center gap-1 rounded border border-rose-600 bg-rose-950 px-2 py-0.5 text-[11px] font-semibold text-rose-200">
              <AlertTriangle className="w-3.5 h-3.5" /> CIRCUIT BREAKER (-2%)
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center bg-[#0d1117] border border-[#30363d] rounded overflow-hidden">
            <input
              value={searchTicker}
              onChange={(e) => setSearchTicker(e.target.value.toUpperCase())}
              placeholder="US.NVDA"
              className="bg-transparent px-2.5 py-1 text-xs font-mono w-28 focus:outline-none"
            />
            <button
              onClick={() => runResearch(searchTicker)}
              disabled={busyAction !== null}
              className="bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white px-2.5 py-1 text-xs font-medium flex items-center gap-1"
            >
              <Search className="w-3.5 h-3.5" />
              {busyAction === `research:${searchTicker}` ? 'Scanning...' : 'AI Scan'}
            </button>
          </div>

          <button
            onClick={scanTopMovers}
            disabled={busyAction !== null}
            className="bg-sky-700 hover:bg-sky-600 disabled:opacity-50 text-white px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap"
          >
            {busyAction === 'scan_movers' ? 'Scanning Movers...' : 'Scan Top 8 Movers'}
          </button>

          <div className="flex rounded border border-[#30363d] overflow-hidden text-[11px] font-mono">
            {(['SEMI_AUTO', 'FULL_AUTO'] as const).map((m) => (
              <button
                key={m}
                onClick={() => toggleMode(m)}
                className={`px-2.5 py-1 ${
                  executionMode === m
                    ? 'bg-sky-600 text-white font-semibold'
                    : 'bg-[#0d1117] text-slate-400 hover:text-white'
                }`}
              >
                {m}
              </button>
            ))}
          </div>

          <button
            onClick={fetchAll}
            className="p-1.5 rounded border border-[#30363d] bg-[#0d1117] hover:bg-[#21262d] text-slate-300"
            title="Refresh all data"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </header>

      {/* Live Scrolling Ticker Tape */}
      <div className="border-b border-[#30363d] bg-[#0d1117] px-3 py-1.5 flex items-center gap-3 overflow-hidden text-xs font-mono">
        <div className="shrink-0 flex items-center gap-1.5 pr-3 border-r border-[#30363d]">
          <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
          <span className="text-[11px] font-bold text-emerald-400">LIVE</span>
          {lastTickAt && <span className="text-[10px] text-slate-500">{lastTickAt}</span>}
        </div>
        <div className="overflow-hidden flex-1">
          <div className="ticker-marquee gap-6 whitespace-nowrap">
            {[...quotes, ...quotes].map((q, idx) => (
              <div key={`${q.ticker}-${idx}`} className="flex items-center gap-1.5">
                <span className="font-bold text-slate-100">{q.symbol}</span>
                <span className="text-slate-200">${q.price.toFixed(2)}</span>
                <span className={q.change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                  {q.change_pct >= 0 ? '+' : ''}
                  {q.change_pct.toFixed(2)}%
                </span>
                <span className={`px-1.5 py-0.2 rounded border text-[10px] ${verdictPill(q.ai_verdict)}`}>
                  {q.ai_verdict || 'HOLD'}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Finviz-style Navigation Tabs */}
      <nav className="border-b border-[#30363d] bg-[#161b22] px-4 flex items-center gap-1 overflow-x-auto">
        {[
          { key: 'screener', label: 'Screener & Heatmap', icon: LayoutGrid },
          { key: 'technicals', label: 'Technicals (TV)', icon: BarChart2 },
          { key: 'portfolio', label: 'Paper Portfolio', icon: Wallet },
          { key: 'advisor', label: `AI Decision Desk (${recommendations.length})`, icon: Brain },
          { key: 'social', label: `Social & News (${socialItems.length})`, icon: Globe },
          { key: 'backtest', label: 'Backtest Lab & Blotter', icon: FlaskConical },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key as TabKey)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors whitespace-nowrap ${
              tab === key
                ? 'border-emerald-400 text-emerald-300 bg-[#0d1117]/60'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </nav>

      {toast && (
        <div
          className={`mx-4 mt-3 px-3 py-2 rounded border text-xs font-mono flex items-center gap-2 ${
            toast.kind === 'ok'
              ? 'bg-emerald-950/80 border-emerald-700 text-emerald-200'
              : 'bg-rose-950/80 border-rose-700 text-rose-200'
          }`}
        >
          {toast.kind === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
          {toast.msg}
        </div>
      )}

      <main className="flex-1 p-4 space-y-4 max-w-[1600px] w-full mx-auto">
        {/* TAB 1: FINVIZ SCREENER & HEATMAP */}
        {tab === 'screener' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
            <section className="lg:col-span-5 border border-[#30363d] rounded bg-[#161b22] p-3">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                  Watchlist Market Heatmap
                </h2>
                <span className="text-[11px] text-slate-400 font-mono">24h % Change</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
                {quotes.map((q) => (
                  <button
                    key={q.ticker}
                    onClick={() => runResearch(q.ticker)}
                    className={`border rounded p-2.5 text-left transition-transform hover:scale-[1.02] ${heatmapColor(
                      q.change_pct
                    )}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold font-mono text-sm">{q.symbol}</span>
                      <span className="text-[10px] font-mono opacity-85">{q.ai_verdict || 'HOLD'}</span>
                    </div>
                    <div className="text-base font-bold font-mono mt-1">
                      {q.change_pct >= 0 ? '+' : ''}
                      {q.change_pct.toFixed(2)}%
                    </div>
                    <div className="text-[11px] font-mono opacity-80">${q.price.toFixed(2)}</div>
                  </button>
                ))}
              </div>
            </section>

            <section className="lg:col-span-7 border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
              <div className="px-3 py-2 border-b border-[#30363d] flex items-center justify-between">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                  Finviz-Style Quant Screener
                </h2>
                <span className="text-[11px] text-slate-400 font-mono">{quotes.length} symbols</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse font-mono text-xs">
                  <thead>
                    <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                      <th className="py-2 px-3">Ticker</th>
                      <th className="py-2 px-3 text-right">Price</th>
                      <th className="py-2 px-3 text-right">Change</th>
                      <th className="py-2 px-3 text-right">Volume</th>
                      <th className="py-2 px-3 text-right">Social</th>
                      <th className="py-2 px-3 text-center">AI Signal</th>
                      <th className="py-2 px-3 text-right">Conf</th>
                      <th className="py-2 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {quotes.map((q) => (
                      <tr
                        key={q.ticker}
                        className="border-b border-[#21262d] hover:bg-[#1f242c] transition-colors"
                      >
                        <td className="py-1.5 px-3 font-bold text-sky-400">
                          <button
                            onClick={() => {
                              setTechTicker(q.ticker);
                              setTab('technicals');
                            }}
                            className="hover:underline"
                            title="Open TradingView Technicals"
                          >
                            {q.symbol}
                          </button>
                        </td>
                        <td className="py-1.5 px-3 text-right">${q.price.toFixed(2)}</td>
                        <td
                          className={`py-1.5 px-3 text-right font-semibold ${
                            q.change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {q.change_pct >= 0 ? '+' : ''}
                          {q.change_pct.toFixed(2)}%
                        </td>
                        <td className="py-1.5 px-3 text-right text-slate-400">
                          {(q.volume / 1_000_000).toFixed(2)}M
                        </td>
                        <td className="py-1.5 px-3 text-right">
                          {(q.social_score ?? 0).toFixed(2)}
                        </td>
                        <td className="py-1.5 px-3 text-center">
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] ${verdictPill(q.ai_verdict)}`}>
                            {q.ai_verdict || 'HOLD'}
                          </span>
                        </td>
                        <td className="py-1.5 px-3 text-right">{(q.ai_confidence ?? 50).toFixed(0)}%</td>
                        <td className="py-1.5 px-3 text-right">
                          <button
                            onClick={() => runResearch(q.ticker)}
                            disabled={busyAction !== null}
                            className="px-2 py-0.5 rounded bg-[#21262d] hover:bg-[#30363d] text-slate-200 text-[11px]"
                          >
                            {busyAction === `research:${q.ticker}` ? '...' : 'Scan'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="lg:col-span-12 border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
              <div className="px-3 py-2 border-b border-[#30363d] flex items-center justify-between">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                  Top High-Moving Stocks (S&P 500 Momentum Scanner — Ranked by |% Change|)
                </h2>
                <button
                  onClick={scanTopMovers}
                  disabled={busyAction !== null}
                  className="px-2.5 py-1 rounded bg-sky-700 hover:bg-sky-600 disabled:opacity-50 text-white text-[11px] font-semibold"
                >
                  {busyAction === 'scan_movers' ? 'Scanning...' : 'AI Scan Top 5 Movers'}
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse font-mono text-xs">
                  <thead>
                    <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                      <th className="py-2 px-3">Ticker</th>
                      <th className="py-2 px-3 text-right">Price</th>
                      <th className="py-2 px-3 text-right">Change %</th>
                      <th className="py-2 px-3 text-right">Volume</th>
                      <th className="py-2 px-3 text-center">AI Signal</th>
                      <th className="py-2 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movers.map((m) => (
                      <tr key={m.ticker} className="border-b border-[#21262d] hover:bg-[#1f242c]">
                        <td className="py-1.5 px-3 font-bold text-sky-400">{m.symbol}</td>
                        <td className="py-1.5 px-3 text-right">${m.price.toFixed(2)}</td>
                        <td
                          className={`py-1.5 px-3 text-right font-semibold ${
                            m.change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {m.change_pct >= 0 ? '+' : ''}
                          {m.change_pct.toFixed(2)}%
                        </td>
                        <td className="py-1.5 px-3 text-right text-slate-400">
                          {(m.volume / 1_000_000).toFixed(2)}M
                        </td>
                        <td className="py-1.5 px-3 text-center">
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] ${verdictPill(m.ai_verdict)}`}>
                            {m.ai_verdict || 'HOLD'}
                          </span>
                        </td>
                        <td className="py-1.5 px-3 text-right">
                          <button
                            onClick={() => runResearch(m.ticker)}
                            disabled={busyAction !== null}
                            className="px-2 py-0.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-[11px]"
                          >
                            {busyAction === `research:${m.ticker}` ? '...' : 'AI Scan'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}

        {/* TAB 1B: TRADINGVIEW TECHNICALS VIEW */}
        {tab === 'technicals' && (
          <div className="space-y-4">
            <div className="border border-[#30363d] rounded bg-[#161b22] p-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <input
                  value={techTicker}
                  onChange={(e) => setTechTicker(e.target.value.toUpperCase())}
                  placeholder="US.MU"
                  className="bg-[#0d1117] border border-[#30363d] rounded px-2.5 py-1 text-xs font-mono w-28"
                />
                <div className="flex flex-wrap gap-1">
                  {['US.MU', 'US.NVDA', 'US.AMD', 'US.AVGO', 'US.AAPL', 'US.MSFT', 'US.TSLA', 'US.META', 'US.PLTR', 'US.SPY', 'US.QQQ'].map((sym) => (
                    <button
                      key={sym}
                      onClick={() => setTechTicker(sym)}
                      className={`px-2 py-0.5 rounded text-[11px] font-mono border ${
                        techTicker === sym
                          ? 'bg-sky-700 border-sky-500 text-white'
                          : 'bg-[#0d1117] border-[#30363d] text-slate-400 hover:text-white'
                      }`}
                    >
                      {sym.replace('US.', '')}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-2">
                {(['1H', '1D', '1W'] as const).map((tf) => (
                  <button
                    key={tf}
                    onClick={() => setTechTimeframe(tf)}
                    className={`px-2.5 py-1 rounded text-xs font-mono border ${
                      techTimeframe === tf
                        ? 'bg-emerald-700 border-emerald-500 text-white font-semibold'
                        : 'bg-[#0d1117] border-[#30363d] text-slate-400 hover:text-white'
                    }`}
                  >
                    {tf}
                  </button>
                ))}
                <button
                  onClick={() => runResearch(techTicker)}
                  disabled={busyAction !== null}
                  className="px-3 py-1 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-semibold"
                >
                  AI Scan {techTicker.replace('US.', '')}
                </button>
              </div>
            </div>

            {technicalsData ? (
              <>
                <div className="flex items-baseline gap-3 px-1 font-mono">
                  <span className="text-lg font-bold text-slate-100">{technicalsData.ticker}</span>
                  <span className="text-base font-semibold text-sky-400">${technicalsData.price.toFixed(2)}</span>
                  <span className="text-xs text-slate-400">Timeframe: {technicalsData.timeframe}</span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <GaugeMeter title="Oscillators" summary={technicalsData.oscillators_summary} />
                  <GaugeMeter title="Summary" summary={technicalsData.overall_summary} />
                  <GaugeMeter title="Moving Averages" summary={technicalsData.moving_averages_summary} />
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <div className="border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
                    <div className="px-3 py-2 border-b border-[#30363d] text-xs font-semibold uppercase text-slate-300">
                      Oscillators ({technicalsData.oscillators.length})
                    </div>
                    <table className="w-full text-left font-mono text-xs">
                      <thead>
                        <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                          <th className="py-2 px-3">Name</th>
                          <th className="py-2 px-3 text-right">Value</th>
                          <th className="py-2 px-3 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {technicalsData.oscillators.map((row) => (
                          <tr key={row.name} className="border-b border-[#21262d]">
                            <td className="py-1.5 px-3 font-sans text-slate-200">{row.name}</td>
                            <td className="py-1.5 px-3 text-right text-slate-300">{row.value.toFixed(2)}</td>
                            <td
                              className={`py-1.5 px-3 text-right font-semibold ${
                                row.action === 'Buy'
                                  ? 'text-sky-400'
                                  : row.action === 'Sell'
                                  ? 'text-rose-400'
                                  : 'text-slate-400'
                              }`}
                            >
                              {row.action}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
                    <div className="px-3 py-2 border-b border-[#30363d] text-xs font-semibold uppercase text-slate-300">
                      Moving Averages ({technicalsData.moving_averages.length})
                    </div>
                    <table className="w-full text-left font-mono text-xs">
                      <thead>
                        <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                          <th className="py-2 px-3">Name</th>
                          <th className="py-2 px-3 text-right">Value</th>
                          <th className="py-2 px-3 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {technicalsData.moving_averages.map((row) => (
                          <tr key={row.name} className="border-b border-[#21262d]">
                            <td className="py-1.5 px-3 font-sans text-slate-200">{row.name}</td>
                            <td className="py-1.5 px-3 text-right text-slate-300">${row.value.toFixed(2)}</td>
                            <td
                              className={`py-1.5 px-3 text-right font-semibold ${
                                row.action === 'Buy'
                                  ? 'text-sky-400'
                                  : row.action === 'Sell'
                                  ? 'text-rose-400'
                                  : 'text-slate-400'
                              }`}
                            >
                              {row.action}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            ) : (
              <div className="border border-[#30363d] rounded bg-[#161b22] p-8 text-center text-slate-400 text-xs font-mono">
                Loading technical indicators for {techTicker}...
              </div>
            )}
          </div>
        )}

        {/* TAB 2: PAPER PORTFOLIO */}
        {tab === 'portfolio' && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: 'Total Equity (NAV)', val: `$${(account?.nav ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
                {
                  label: `Margin Cash (BP: $${(account?.buying_power ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })})`,
                  val: `$${(account?.cash ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
                },
                {
                  label: 'Day P&L',
                  val: `$${(account?.day_pnl ?? 0).toFixed(2)} (${(account?.day_pnl_pct ?? 0).toFixed(2)}%)`,
                  color: (account?.day_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400',
                },
                {
                  label: 'Unrealized / Realized P&L',
                  val: `$${(account?.unrealized_pnl ?? 0).toFixed(2)} / $${(account?.realized_pnl ?? 0).toFixed(2)}`,
                  color: (account?.day_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400',
                },
              ].map((kpi) => (
                <div key={kpi.label} className="border border-[#30363d] rounded bg-[#161b22] p-3">
                  <div className="text-[11px] uppercase text-slate-400">{kpi.label}</div>
                  <div className={`text-lg font-bold font-mono mt-1 ${kpi.color || 'text-slate-100'}`}>
                    {kpi.val}
                  </div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-2 border border-[#30363d] rounded bg-[#161b22] p-3">
                <h3 className="text-xs font-semibold uppercase text-slate-300 mb-2 flex items-center gap-1.5">
                  <Activity className="w-3.5 h-3.5 text-sky-400" /> Paper Equity Curve
                </h3>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={navHistory}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                      <XAxis dataKey="ts" hide />
                      <YAxis domain={['auto', 'auto']} stroke="#8b949e" fontSize={11} />
                      <Tooltip contentStyle={{ backgroundColor: '#161b22', borderColor: '#30363d' }} />
                      <Area type="monotone" dataKey="nav" stroke="#38bdf8" fill="#38bdf8" fillOpacity={0.18} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="border border-[#30363d] rounded bg-[#161b22] p-3">
                <h3 className="text-xs font-semibold uppercase text-slate-300 mb-2">Asset Allocation</h3>
                {positions.length > 0 ? (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={positions.map((p) => ({ ...p, abs_value: Math.abs(p.market_value) }))}
                          dataKey="abs_value"
                          nameKey="symbol"
                          innerRadius={45}
                          outerRadius={78}
                        >
                          {positions.map((_, i) => (
                            <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip contentStyle={{ backgroundColor: '#161b22', borderColor: '#30363d' }} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <div className="h-64 flex items-center justify-center text-slate-400 font-mono text-xs">
                    100% Cash (No open positions)
                  </div>
                )}
              </div>
            </div>

            {openOrders.length > 0 && (
              <div className="border border-amber-700/50 rounded bg-[#161b22] overflow-hidden">
                <div className="px-3 py-2 border-b border-[#30363d] flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase text-amber-300">
                    Open / Queued Orders ({openOrders.length}) — Waiting for Market / Fill
                  </span>
                  <button
                    onClick={cancelAllOrders}
                    disabled={busyAction !== null}
                    className="px-2.5 py-1 rounded bg-rose-700 hover:bg-rose-600 disabled:opacity-50 text-white text-[11px] font-semibold"
                  >
                    {busyAction === 'cancel_all' ? 'Cancelling...' : `Cancel All (${openOrders.length})`}
                  </button>
                </div>
                <table className="w-full text-left font-mono text-xs">
                  <thead>
                    <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                      <th className="py-2 px-3">Order ID</th>
                      <th className="py-2 px-3">Ticker</th>
                      <th className="py-2 px-3">Side</th>
                      <th className="py-2 px-3 text-right">Qty</th>
                      <th className="py-2 px-3 text-right">Limit Price</th>
                      <th className="py-2 px-3">Status</th>
                      <th className="py-2 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openOrders.map((o) => (
                      <tr key={o.order_id} className="border-b border-[#21262d]">
                        <td className="py-1.5 px-3 text-slate-300">{o.order_id}</td>
                        <td className="py-1.5 px-3 font-bold text-sky-400">{o.ticker}</td>
                        <td className={`py-1.5 px-3 font-bold ${o.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {o.side}
                        </td>
                        <td className="py-1.5 px-3 text-right">{o.qty}</td>
                        <td className="py-1.5 px-3 text-right">${o.price.toFixed(2)}</td>
                        <td className="py-1.5 px-3 text-amber-300">{o.status}</td>
                        <td className="py-1.5 px-3 text-right">
                          <button
                            onClick={() => cancelOrder(o.order_id)}
                            disabled={busyAction !== null}
                            className="px-2 py-0.5 rounded bg-rose-700 hover:bg-rose-600 disabled:opacity-50 text-white text-[11px] font-semibold"
                          >
                            {busyAction === `cancel:${o.order_id}` ? '...' : 'Cancel'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-2 border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
                <div className="px-3 py-2 border-b border-[#30363d] flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase text-slate-300">Live Paper Holdings</span>
                  {positions.length > 0 && (
                    <button
                      onClick={closeAllPositions}
                      disabled={busyAction !== null}
                      className="px-2.5 py-1 rounded bg-amber-700 hover:bg-amber-600 disabled:opacity-50 text-white text-[11px] font-semibold"
                    >
                      {busyAction === 'close_all' ? 'Closing All...' : `Cover & Book All (${positions.length})`}
                    </button>
                  )}
                </div>
                <table className="w-full text-left font-mono text-xs">
                  <thead>
                    <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                      <th className="py-2 px-3">Ticker</th>
                      <th className="py-2 px-3 text-right">Qty</th>
                      <th className="py-2 px-3 text-right">Avg Cost</th>
                      <th className="py-2 px-3 text-right">Last</th>
                      <th className="py-2 px-3 text-right">Weight</th>
                      <th className="py-2 px-3 text-right">Unrealized P&L</th>
                      <th className="py-2 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="py-6 text-center text-slate-500">
                          No open paper positions.
                        </td>
                      </tr>
                    ) : (
                      positions.map((p) => (
                        <tr key={p.ticker} className="border-b border-[#21262d]">
                          <td className="py-2 px-3 font-bold text-sky-400">{p.symbol}</td>
                          <td className="py-2 px-3 text-right">{p.qty}</td>
                          <td className="py-2 px-3 text-right">${p.avg_cost.toFixed(2)}</td>
                          <td className="py-2 px-3 text-right">${p.current_price.toFixed(2)}</td>
                          <td className="py-2 px-3 text-right">{p.weight_pct.toFixed(1)}%</td>
                          <td
                            className={`py-2 px-3 text-right font-semibold ${
                              p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                            }`}
                          >
                            ${p.unrealized_pnl.toFixed(2)} ({p.unrealized_pnl_pct.toFixed(2)}%)
                          </td>
                          <td className="py-2 px-3 text-right">
                            <button
                              onClick={() => closePosition(p)}
                              disabled={Boolean(closingTickers[p.ticker]) || busyAction === 'close_all'}
                              className="px-2 py-0.5 rounded bg-amber-700 hover:bg-amber-600 disabled:opacity-50 text-white text-[11px] font-semibold"
                            >
                              {closingTickers[p.ticker] ? '...' : p.qty < 0 ? 'Cover & Book' : 'Close & Book'}
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <form onSubmit={submitManualOrder} className="border border-[#30363d] rounded bg-[#161b22] p-3 space-y-2.5">
                <h3 className="text-xs font-semibold uppercase text-slate-300">
                  Paper Order Ticket (SIMULATE)
                </h3>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[11px] text-slate-400">Ticker</label>
                    <input
                      value={manualTicker}
                      onChange={(e) => setManualTicker(e.target.value.toUpperCase())}
                      className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-400">Side</label>
                    <select
                      value={manualSide}
                      onChange={(e) => setManualSide(e.target.value as 'BUY' | 'SELL')}
                      className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                    >
                      <option value="BUY">BUY</option>
                      <option value="SELL">SELL</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-400">Quantity</label>
                    <input
                      type="number"
                      min={1}
                      value={manualQty}
                      onChange={(e) => setManualQty(Number(e.target.value))}
                      className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-400">Stop-Loss ($)</label>
                    <input
                      type="number"
                      step="0.5"
                      value={manualSL}
                      onChange={(e) => setManualSL(Number(e.target.value))}
                      className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                    />
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={busyAction !== null}
                  className="w-full py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-semibold"
                >
                  Submit Paper Order
                </button>
              </form>
            </div>
          </div>
        )}

        {/* TAB 3: AI DECISION DESK */}
        {tab === 'advisor' && (
          <div className="space-y-3">
            {recommendations.length === 0 ? (
              <div className="border border-[#30363d] rounded bg-[#161b22] p-6 text-center text-slate-400 text-xs">
                Click <b>AI Scan</b> in the top bar or click any tile in the Heatmap to generate a structured Buy/Hold/Sell recommendation.
              </div>
            ) : (
              recommendations.map((rec) => (
                <div key={rec.rec_id} className="border border-[#30363d] rounded bg-[#161b22] p-4 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-3">
                      <span className="text-base font-bold font-mono text-sky-400">{rec.ticker}</span>
                      <span className={`px-2 py-0.5 rounded border text-xs font-bold ${verdictPill(rec.verdict)}`}>
                        {rec.verdict}
                      </span>
                      <span className="text-xs font-mono text-slate-300">
                        Confidence: <b>{rec.confidence.toFixed(1)}%</b>
                      </span>
                      <span className="text-[11px] font-mono text-slate-500">[{rec.status}]</span>
                    </div>
                    {rec.status === 'PENDING' && rec.verdict !== 'HOLD' && (() => {
                      const isSell = rec.verdict.includes('SELL');
                      const hasLong = positions.some((p) => p.ticker === rec.ticker && p.qty > 0);
                      const label = !isSell
                        ? '1-Click Approve BUY (SIMULATE)'
                        : hasLong
                        ? '1-Click Close Long (SIMULATE)'
                        : '1-Click Short Sell (SIMULATE)';
                      return (
                        <button
                          onClick={() => approveRec(rec.rec_id)}
                          disabled={busyAction !== null}
                          className={`px-3 py-1 rounded text-white text-xs font-semibold ${
                            isSell && !hasLong
                              ? 'bg-rose-700 hover:bg-rose-600'
                              : 'bg-emerald-700 hover:bg-emerald-600'
                          }`}
                        >
                          {label}
                        </button>
                      );
                    })()}
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs font-mono">
                    <div className="bg-[#0d1117] border border-[#21262d] rounded p-2">
                      <div className="text-[10px] text-slate-400">TECH SCORE</div>
                      <div className="font-bold">{rec.technical_score.toFixed(2)}</div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#21262d] rounded p-2">
                      <div className="text-[10px] text-slate-400">CATALYST</div>
                      <div className="font-bold">{rec.catalyst_score.toFixed(2)}</div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#21262d] rounded p-2">
                      <div className="text-[10px] text-slate-400">SOCIAL</div>
                      <div className="font-bold">{rec.social_score.toFixed(2)}</div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#21262d] rounded p-2">
                      <div className="text-[10px] text-slate-400">ENTRY ZONE</div>
                      <div className="font-bold">${rec.entry_low.toFixed(2)}-${rec.entry_high.toFixed(2)}</div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#21262d] rounded p-2">
                      <div className="text-[10px] text-slate-400">SL / TP1 (R:R)</div>
                      <div className="font-bold">
                        ${rec.stop_loss.toFixed(2)} / ${rec.take_profit_1.toFixed(2)} ({rec.risk_reward}R)
                      </div>
                    </div>
                  </div>

                  <ul className="list-disc list-inside space-y-1 text-xs text-slate-300">
                    {rec.thesis.map((b, idx) => (
                      <li key={idx}>{b}</li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        )}

        {/* TAB 4: SOCIAL & NEWS FEED */}
        {tab === 'social' && (
          <div className="border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
            <div className="px-3 py-2 border-b border-[#30363d] flex items-center justify-between gap-2">
              <h2 className="text-xs font-semibold uppercase text-slate-300">
                Login-Free Social & News Intelligence Feed
              </h2>
              <input
                value={socialFilter}
                onChange={(e) => setSocialFilter(e.target.value)}
                placeholder="Filter ticker or platform..."
                className="bg-[#0d1117] border border-[#30363d] rounded px-2.5 py-1 text-xs font-mono w-56"
              />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                    <th className="py-2 px-3">Platform</th>
                    <th className="py-2 px-3">Ticker</th>
                    <th className="py-2 px-3 text-right">Sentiment</th>
                    <th className="py-2 px-3">Headline / Post</th>
                    <th className="py-2 px-3 text-right">Link</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredSocial.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-6 text-center text-slate-500">
                        No social/news items cached yet. Run AI Scan for any ticker.
                      </td>
                    </tr>
                  ) : (
                    filteredSocial.map((it) => (
                      <tr key={it.item_hash} className="border-b border-[#21262d] hover:bg-[#1f242c]">
                        <td className="py-1.5 px-3 text-slate-400">{it.platform}</td>
                        <td className="py-1.5 px-3 font-bold text-sky-400">{it.ticker}</td>
                        <td
                          className={`py-1.5 px-3 text-right font-semibold ${
                            it.sentiment_score > 0
                              ? 'text-emerald-400'
                              : it.sentiment_score < 0
                              ? 'text-rose-400'
                              : 'text-slate-400'
                          }`}
                        >
                          {it.sentiment_score >= 0 ? '+' : ''}
                          {it.sentiment_score.toFixed(2)}
                        </td>
                        <td className="py-1.5 px-3 font-sans text-slate-200 max-w-xl truncate">{it.title}</td>
                        <td className="py-1.5 px-3 text-right">
                          {it.url && (
                            <a
                              href={it.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-sky-400 hover:underline"
                            >
                              Open
                            </a>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 5: BACKTEST LAB & BLOTTER */}
        {tab === 'backtest' && (
          <div className="space-y-4">
            <form
              onSubmit={runBacktest}
              className="border border-[#30363d] rounded bg-[#161b22] p-3 grid grid-cols-2 md:grid-cols-5 gap-3 items-end"
            >
              <div>
                <label className="text-[11px] text-slate-400">Ticker</label>
                <input
                  value={btTicker}
                  onChange={(e) => setBtTicker(e.target.value.toUpperCase())}
                  className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-400">Strategy</label>
                <select
                  value={btStrategy}
                  onChange={(e) => setBtStrategy(e.target.value)}
                  className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                >
                  <option value="rsi_macd_momentum">RSI + MACD Momentum</option>
                  <option value="bb_mean_reversion">BB Mean Reversion</option>
                  <option value="ema_trend_following">EMA 20/50 Trend</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] text-slate-400">Benchmark</label>
                <input
                  value={btBenchmark}
                  onChange={(e) => setBtBenchmark(e.target.value.toUpperCase())}
                  className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-400">Lookback (Days)</label>
                <input
                  type="number"
                  min={120}
                  max={1825}
                  value={btDays}
                  onChange={(e) => setBtDays(Number(e.target.value))}
                  className="w-full mt-0.5 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs font-mono"
                />
              </div>
              <button
                type="submit"
                disabled={busyAction !== null}
                className="py-1.5 px-3 rounded bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold"
              >
                {busyAction === 'backtest' ? 'Running...' : 'Run Backtest'}
              </button>
            </form>

            {latestBacktest && (
              <div className="border border-[#30363d] rounded bg-[#161b22] p-3 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-mono font-bold text-slate-200">
                    {latestBacktest.ticker} — {latestBacktest.strategy}
                  </span>
                  <span
                    className={`px-2 py-0.5 rounded border text-[11px] font-mono ${
                      latestBacktest.passed_gatekeeper
                        ? 'bg-emerald-950 border-emerald-700 text-emerald-300'
                        : 'bg-rose-950 border-rose-700 text-rose-300'
                    }`}
                  >
                    {latestBacktest.gatekeeper_reason}
                  </span>
                </div>

                <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs font-mono">
                  <div className="bg-[#0d1117] p-2 rounded border border-[#21262d]">
                    <div className="text-[10px] text-slate-400">CAGR</div>
                    <div className="font-bold">{latestBacktest.cagr.toFixed(2)}%</div>
                  </div>
                  <div className="bg-[#0d1117] p-2 rounded border border-[#21262d]">
                    <div className="text-[10px] text-slate-400">SHARPE</div>
                    <div className="font-bold">{latestBacktest.sharpe.toFixed(2)}</div>
                  </div>
                  <div className="bg-[#0d1117] p-2 rounded border border-[#21262d]">
                    <div className="text-[10px] text-slate-400">SORTINO</div>
                    <div className="font-bold">{latestBacktest.sortino.toFixed(2)}</div>
                  </div>
                  <div className="bg-[#0d1117] p-2 rounded border border-[#21262d]">
                    <div className="text-[10px] text-slate-400">MAX DD</div>
                    <div className="font-bold text-rose-400">{latestBacktest.max_drawdown.toFixed(2)}%</div>
                  </div>
                  <div className="bg-[#0d1117] p-2 rounded border border-[#21262d]">
                    <div className="text-[10px] text-slate-400">WIN RATE</div>
                    <div className="font-bold">{latestBacktest.win_rate.toFixed(1)}%</div>
                  </div>
                  <div className="bg-[#0d1117] p-2 rounded border border-[#21262d]">
                    <div className="text-[10px] text-slate-400">PROFIT FACTOR</div>
                    <div className="font-bold">{latestBacktest.profit_factor.toFixed(2)}</div>
                  </div>
                </div>

                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={latestBacktest.equity_curve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                      <XAxis dataKey="date" hide />
                      <YAxis domain={['auto', 'auto']} stroke="#8b949e" fontSize={11} />
                      <Tooltip contentStyle={{ backgroundColor: '#161b22', borderColor: '#30363d' }} />
                      <Line type="monotone" dataKey="strategy_equity" stroke="#22c55e" dot={false} name="Strategy" />
                      <Line type="monotone" dataKey="benchmark_equity" stroke="#94a3b8" strokeDasharray="4 4" dot={false} name="Benchmark" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            <div className="border border-[#30363d] rounded bg-[#161b22] overflow-hidden">
              <div className="px-3 py-2 border-b border-[#30363d] text-xs font-semibold uppercase text-slate-300">
                Simulated Order Execution Blotter (TrdEnv.SIMULATE)
              </div>
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="bg-[#0d1117] text-slate-400 border-b border-[#30363d]">
                    <th className="py-2 px-3">Order ID</th>
                    <th className="py-2 px-3">Ticker</th>
                    <th className="py-2 px-3">Side</th>
                    <th className="py-2 px-3 text-right">Qty</th>
                    <th className="py-2 px-3 text-right">Price</th>
                    <th className="py-2 px-3">Status</th>
                    <th className="py-2 px-3">Env</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-6 text-center text-slate-500">
                        No simulated orders yet.
                      </td>
                    </tr>
                  ) : (
                    orders.map((o) => (
                      <tr key={o.order_id} className="border-b border-[#21262d]">
                        <td className="py-1.5 px-3 text-slate-300">{o.order_id}</td>
                        <td className="py-1.5 px-3 font-bold text-sky-400">{o.ticker}</td>
                        <td className={`py-1.5 px-3 font-bold ${o.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {o.side}
                        </td>
                        <td className="py-1.5 px-3 text-right">{o.qty}</td>
                        <td className="py-1.5 px-3 text-right">${o.price.toFixed(2)}</td>
                        <td className="py-1.5 px-3">{o.status}</td>
                        <td className="py-1.5 px-3 text-emerald-400">{o.trd_env}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export interface QuoteItem {
  ticker: string;
  symbol: string;
  price: number;
  prev_close: number;
  change_pct: number;
  volume: number;
  high: number;
  low: number;
  ai_verdict?: string;
  ai_confidence?: number;
  social_score?: number;
  source?: string;
}

export interface AccountInfo {
  trd_env: string;
  mode: string;
  nav: number;
  cash: number;
  buying_power: number;
  unrealized_pnl: number;
  realized_pnl: number;
  day_pnl: number;
  day_pnl_pct: number;
}

export interface PositionItem {
  ticker: string;
  symbol: string;
  qty: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  weight_pct: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss?: number | null;
  take_profit?: number | null;
}

export interface OrderItem {
  order_id: string;
  ts: string;
  ticker: string;
  side: string;
  qty: number;
  price: number;
  order_type: string;
  status: string;
  trd_env: string;
  stop_loss?: number | null;
  take_profit?: number | null;
}

export interface RecommendationItem {
  rec_id: string;
  ts?: string;
  ticker: string;
  verdict: 'STRONG BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG SELL';
  confidence: number;
  technical_score: number;
  catalyst_score: number;
  social_score: number;
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  risk_reward: number;
  thesis: string[];
  status: string;
}

export interface SocialFeedItem {
  item_hash: string;
  ts: string;
  platform: string;
  ticker: string;
  author: string;
  title: string;
  content: string;
  url: string;
  engagement: number;
  sentiment_score: number;
}

export interface BacktestRun {
  run_id: string;
  ts?: string;
  ticker: string;
  strategy: string;
  cagr: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  passed_gatekeeper: boolean;
  gatekeeper_reason: string;
  equity_curve: Array<{
    date: string;
    strategy_equity: number;
    benchmark_equity: number;
  }>;
}

export interface TechnicalIndicatorRow {
  name: string;
  value: number;
  action: 'Buy' | 'Sell' | 'Neutral';
}

export interface TechnicalSummary {
  buy: number;
  neutral: number;
  sell: number;
  score: number;
  rating: string;
}

export interface TradingViewTechnicals {
  ticker: string;
  symbol: string;
  timeframe: string;
  price: number;
  quote?: QuoteItem | null;
  oscillators: TechnicalIndicatorRow[];
  moving_averages: TechnicalIndicatorRow[];
  oscillators_summary: TechnicalSummary;
  moving_averages_summary: TechnicalSummary;
  overall_summary: TechnicalSummary;
}

export type NavSection = 'opportunities' | 'positions' | 'backtest'
export type ServiceStatus = 'healthy' | 'degraded' | 'down'
export type Environment = 'live' | 'paper'
export type OpportunityStage = 'new' | 'paper' | 'approved' | 'rejected' | 'executed'
export type PositionStatus = 'open' | 'review' | 'closed'
export type NoteContext = 'decision' | 'risk' | 'operations'
export type UserRole = 'trader' | 'analyst' | 'operator'
export type LogLevel = 'info' | 'warning' | 'error'
export type AttachmentType = 'link' | 'file'
export type OpportunitySide = 'YES' | 'NO'
export type AlertTone = 'neutral' | 'warning' | 'critical'

export interface UserAccount {
  id: string
  name: string
  email: string
  password: string
  role: UserRole
  createdAt: string
}

export interface SessionUser {
  id: string
  name: string
  email: string
  role: UserRole
}

export interface DecisionNote {
  id: string
  author: string
  createdAt: string
  context: NoteContext
  text: string
}

export interface ResearchAttachment {
  id: string
  type: AttachmentType
  title: string
  url?: string
  fileName?: string
  sizeLabel?: string
  uploadedBy: string
  uploadedAt: string
}

export interface OpportunityOutcome {
  name: string
  tokenId?: string
  price: number | null
  bestBid?: number | null
  bestAsk?: number | null
  spreadBps?: number | null
  depth?: number
  midpoint?: number | null
}

export interface Opportunity {
  id: string
  question: string
  slug?: string
  category: string
  marketType: string
  statusLabel: string
  currentStage: OpportunityStage
  discoveredAt: string
  lastUpdatedAt: string
  resolutionDate: string | null
  timeHorizon: string
  liquidity: number
  volume24h: number
  volume: number
  marketDepth: number
  spreadBps: number | null
  urgencyScore: number
  yesPrice: number | null
  noPrice: number | null
  bestBid: number | null
  bestAsk: number | null
  defaultStake: number
  maxStake: number
  entryPriceMin: number | null
  entryPriceMax: number | null
  recommendedOutcome: OpportunitySide | null
  expectedReturn: number | null
  confidence: number | null
  signalStrength: number | null
  strategyAvailable: boolean
  strategySummary: string | null
  thesis: string | null
  invalidation: string | null
  riskFlags: string[]
  tags: string[]
  tokenIds: Partial<Record<OpportunitySide, string>>
  outcomes: OpportunityOutcome[]
  priceHistory: number[]
  notes: DecisionNote[]
  attachments: ResearchAttachment[]
  reviewer?: string
  reviewedAt?: string
  description?: string | null
  eventTitle?: string | null
  eventSlug?: string | null
  defaultTokenId?: string | null
}

export interface Position {
  id: string
  tokenId: string
  marketId: string
  environment: Environment
  question: string
  category: string
  marketType: string
  side: OpportunitySide
  outcome: string
  shares: number
  stake: number
  entryPrice: number
  currentPrice: number
  liquidationValue: number
  unrealizedPnl: number
  status: PositionStatus
  openedAt: string
  updatedAt: string
  modelView: string
  thesisAtEntry: string
  exitGuidance: string
  relatedStrategy: string
  notes: DecisionNote[]
  tags: string[]
  priceHistory: number[]
}

export interface ServiceHealth {
  id: string
  name: string
  description: string
  status: ServiceStatus
  latencyMs: number
  lastHeartbeatAt: string | null
  owner: string
  critical: boolean
}

export interface LogEvent {
  id: string
  timestamp: string
  level: LogLevel
  source: string
  message: string
  user?: string
}

export interface AlertItem {
  id: string
  tone: AlertTone
  title: string
  description: string
}

export interface PortfolioSummary {
  environment: Environment
  available: boolean
  totalReturnImmediate: number | null
  openExposure: number
  availableCapital: number | null
  activePositions: number
  pendingApprovals: number
  realizedPnl: number | null
  unrealizedPnl: number | null
  dailyPnl: number | null
  liquidationValue: number
}

export interface FreshnessEntry {
  name: string
  available: boolean
  stale: boolean
  updatedAt: string | null
  ageSeconds: number | null
  error: string | null
}

export interface BackendHealthSummary {
  status: ServiceStatus
  paperExecutionAvailable: boolean
  liveExecutionAvailable: boolean
  liveHoldingsAvailable: boolean
}

export interface OverviewPayload {
  generatedAt: string
  lastRefreshAt: string
  backendHealthSummary: BackendHealthSummary
  dataFreshness: {
    opportunities: FreshnessEntry
    portfolio: FreshnessEntry
    positions: FreshnessEntry
  }
  paperSummary: PortfolioSummary
  liveSummary: PortfolioSummary
  paperPositionsCount: number
  pendingOpportunityCount: number
  alerts: AlertItem[]
  services: ServiceHealth[]
}

export interface OpportunitiesPayload {
  generatedAt: string
  freshness: FreshnessEntry
  paperExecutionAvailable: boolean
  liveExecutionAvailable: boolean
  items: Opportunity[]
  total: number
}

export interface PositionsPayload {
  generatedAt: string
  environment: Environment
  available: boolean
  message?: string
  freshness?: FreshnessEntry
  items: Position[]
}

export interface PortfolioPayload {
  generatedAt: string
  environment: Environment
  available: boolean
  message?: string
  freshness?: FreshnessEntry
  cash_balance: number | null
  total_position_value: number
  total_equity: number | null
  total_realized_pnl: number | null
  total_unrealized_pnl: number | null
  total_return_immediate: number | null
  open_exposure: number
  available_capital: number | null
  active_positions: number
  pending_approvals: number
  daily_pnl: number | null
  liquidation_value: number
}

export interface OrderbookSnapshot {
  token_id: string
  market_id: string
  best_bid: number | null
  best_ask: number | null
  spread: number | null
  midpoint: number | null
  updatedAt?: number | null
  bids: Array<{ price: number; size: number }>
  asks: Array<{ price: number; size: number }>
  error?: string
}

export interface ScoredOpportunity {
  market_id: string
  category: string
  question: string
  market_url: string | null
  side: OpportunitySide
  score: number
  score_pct: number
  edge_pct: number
  confidence: number
  confidence_pct: number
  p_model_yes: number
  p_market_yes: number
  liquidity_score: number
  spread_bps: number
  rationale_tags: string[]
  hours_to_resolution: number | null
  ai_commentary: string | null
}

export interface TradePayload {
  environment: Environment
  token_id: string
  market_id: string
  question: string
  outcome: string
  side: 'BUY' | 'SELL'
  size: number
  price?: number
  opportunityId?: string
}

// ── Backtest types ──

export interface BacktestMetrics {
  total_return_pct: number
  total_return_usd: number
  sharpe_ratio: number | null
  max_drawdown_pct: number
  max_drawdown_usd: number
  win_rate: number
  profit_factor: number | null
  avg_trade_pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  avg_win: number
  avg_loss: number
  best_trade_pnl: number
  worst_trade_pnl: number
  total_fees_paid: number
}

export interface BacktestTrade {
  timestamp: number
  token_id: string
  market_id: string
  market_question: string
  outcome: string
  side: string
  price: number
  shares: number
  cost: number
  fee: number
  reason: string
}

export interface EquityPoint {
  timestamp: number
  cash: number
  position_value: number
  total_equity: number
}

export interface BacktestResult {
  backtest_id: string
  strategy_name: string
  starting_cash: number
  ending_cash: number
  ending_equity: number
  fee_bps: number
  fidelity: number
  markets: string[]
  strategy_params: Record<string, number | string>
  metrics: BacktestMetrics
  trades: BacktestTrade[]
  equity_curve: EquityPoint[]
}

export interface StrategyInfo {
  name: string
  description: string
  params: Record<string, number | string>
}

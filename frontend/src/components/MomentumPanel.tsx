import { useState, useEffect, useRef, useMemo } from 'react';
import { Play, Square, Terminal, TrendingUp, DollarSign, BarChart3, Target, Shield, ChevronDown, ChevronUp, Activity, Percent, Search, ArrowUpDown, CheckCircle2, XCircle, Globe, BookOpen, Info } from 'lucide-react';
import axios from 'axios';
import { XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine, Area, AreaChart, Legend, Bar, BarChart } from 'recharts';

const API = 'http://localhost:8001/api/pipeline';

// ═══════════════════════════════════════════════════
//  Types
// ═══════════════════════════════════════════════════

interface PipelineStatus {
    name: string;
    status: 'idle' | 'running' | 'finished';
    pid?: number;
    exitCode?: number;
    logs: string[];
}

// ── Backtest types ──
interface MomentumMetrics {
    total_return?: number;
    cagr?: number;
    annualized_volatility?: number;
    mdd?: number;
    mdd_duration_days?: number;
    mdd_recovery_days?: number;
    sharpe_ratio?: number;
    sortino_ratio?: number;
    calmar_ratio?: number;
    profit_factor?: number;
    daily_win_rate?: number;
    monthly_win_rate?: number;
    total_trades?: number;
    total_commission?: number;
    total_slippage?: number;
    total_friction?: number;
    total_turnover?: number;
    best_day?: number;
    worst_day?: number;
    best_month?: number;
    worst_month?: number;
    start_date?: string;
    end_date?: string;
    total_trading_days?: number;
    total_years?: number;
    final_equity?: number;
    [key: string]: unknown;
}

interface MomentumResult {
    timestamp: string;
    config: {
        initial_capital: number;
        top_n: number;
        weight_method: string;
        commission: number;
        slippage: number;
        warmup_days: number;
        min_trading_value: number;
    };
    metrics: MomentumMetrics;
    equity_curve: Record<string, number>;
    trade_summary: Record<string, number>;
    regime_summary: { BULL: number; BEAR: number };
    elapsed_sec: number;
}

// ── Global backtest types ──
interface GlobalAllocation {
    date: string;
    weights: Record<string, number>;
    regimes: Record<string, string>;
    bull_count: number;
    bear_count: number;
}

interface GlobalMomentumResult {
    timestamp: string;
    config: {
        initial_capital: number;
        top_n: number;
        weight_method: string;
        commission: number;
        slippage: number;
        warmup_days: number;
        min_trading_value: number;
        global_mode: boolean;
        portfolio_preset: string;
        preset_label: string;
        risk_level: number;
        strategic_weights: Record<string, string>;
    };
    metrics: MomentumMetrics & {
        benchmark_cagr?: number;
        benchmark_mdd?: number;
        benchmark_total_return?: number;
    };
    equity_curve: Record<string, number>;
    benchmark_equity: Record<string, number>;
    trade_summary: Record<string, number>;
    regime_summary: { BULL: number; BEAR: number };
    global_allocation: GlobalAllocation[];
    regime_by_class: Record<string, string>;
    elapsed_sec: number;
}

const PRESET_INFO: Record<string, { emoji: string; label: string; risk: number; desc: string; detail: string; weights: Record<string, number> }> = {
    growth: {
        emoji: '🚀', label: '성장형', risk: 5,
        desc: '주식 55% + 대체 25%, 고수익 최우선',
        detail: '선진국주식의 비중이 절반 이상. 대체투자(리츠·원자재·금)와 함께 수익성을 극대화하는 공격적 배분. 채권 비중 최소화.',
        weights: { equity: 0.55, alternative: 0.25, foreign_bond: 0.15, domestic_bond: 0.00, cash: 0.05 },
    },
    growth_seeking: {
        emoji: '📈', label: '성장추구형', risk: 4,
        desc: '주식 50% 과반, 적극 자산 증식',
        detail: '선진국 주식이 과반 이상. 해외채권과 대체투자를 보조적으로 편입하여 적극적인 자산 증식을 추구.',
        weights: { equity: 0.50, alternative: 0.15, foreign_bond: 0.20, domestic_bond: 0.05, cash: 0.10 },
    },
    balanced: {
        emoji: '⚖️', label: '위험중립형', risk: 3,
        desc: '위험:안전 5:5 균형 배분',
        detail: '선진국주식이 가장 많으나, 해외채권 비중이 커지며 수익과 위험의 균형을 맞춤. 모든 자산군에 고루 분산.',
        weights: { equity: 0.35, alternative: 0.15, foreign_bond: 0.30, domestic_bond: 0.10, cash: 0.10 },
    },
    stability_seeking: {
        emoji: '🛡️', label: '안정추구형', risk: 2,
        desc: '채권 60% 중심, 시중금리+α',
        detail: '채권 중심 유지. 선진국 주식·대체투자를 일부 편입하여 시중 금리 이상의 추가 수익을 추구. 안정성 우선.',
        weights: { equity: 0.20, alternative: 0.10, foreign_bond: 0.35, domestic_bond: 0.25, cash: 0.10 },
    },
    stable: {
        emoji: '🏦', label: '안정형', risk: 1,
        desc: '채권 75%, 원금 보존 최우선',
        detail: '해외채권이 절반 이상. 채권 위주로 구성하여 안정성을 최우선. 최소한의 주식 비중으로 인플레이션 방어.',
        weights: { equity: 0.10, alternative: 0.05, foreign_bond: 0.50, domestic_bond: 0.25, cash: 0.10 },
    },
};

// ── 자산군 레지스트리 (ETF 13종) ──
interface AssetClassMeta {
    ticker: string;
    label: string;
    category: string;
    categoryLabel: string;
    safeHaven: boolean;
    description: string;
}

const ASSET_CLASS_REGISTRY: AssetClassMeta[] = [
    // 주식
    { ticker: 'SPY', label: '미국 대형주', category: 'equity', categoryLabel: '주식', safeHaven: false, description: 'S&P 500 추종. 미국 시가총액 상위 500개 대형주에 분산 투자하는 대표 ETF' },
    { ticker: 'IWM', label: '미국 소형주', category: 'equity', categoryLabel: '주식', safeHaven: false, description: 'Russell 2000 추종. 미국 소형주 2,000종목에 투자하여 성장 잠재력 확보' },
    { ticker: 'EFA', label: '선진국 (미국 제외)', category: 'equity', categoryLabel: '주식', safeHaven: false, description: 'MSCI EAFE 추종. 유럽·일본·호주 등 선진국 대형주에 분산 투자' },
    { ticker: 'EEM', label: '신흥국', category: 'equity', categoryLabel: '주식', safeHaven: false, description: 'MSCI Emerging Markets 추종. 중국·인도·브라질 등 신흥국 주식에 투자' },
    { ticker: 'EWY', label: '한국 주식', category: 'equity', categoryLabel: '주식', safeHaven: false, description: 'MSCI Korea 추종. 삼성전자·SK하이닉스 등 한국 대표 기업에 투자. 개별종목 Top-N 로직 연동' },
    // 채권
    { ticker: 'AGG', label: '미국 채권 (종합)', category: 'bond', categoryLabel: '채권', safeHaven: true, description: 'Bloomberg US Aggregate Bond 추종. 미국 투자등급 채권 종합 지수. 안전자산 ★' },
    { ticker: 'IEF', label: '미국 국채 (중기)', category: 'bond', categoryLabel: '채권', safeHaven: true, description: '미국 7-10년 만기 국채 ETF. 금리 변동 중간 수준, 안정적 이자 수익. 안전자산 ★' },
    { ticker: 'TLT', label: '미국 국채 (장기)', category: 'bond', categoryLabel: '채권', safeHaven: false, description: '미국 20년+ 장기 국채. 금리 리스크가 커서 대피처로는 부적합, 금리 하락 시 높은 수익' },
    { ticker: 'TIP', label: '물가연동채', category: 'bond', categoryLabel: '채권', safeHaven: false, description: '미국 물가연동국채(TIPS) ETF. 인플레이션 상승 시 원금이 조정되어 실질 구매력 보호' },
    // 실물자산
    { ticker: 'VNQ', label: '글로벌 리츠', category: 'real_asset', categoryLabel: '실물자산', safeHaven: false, description: 'Vanguard 미국 리츠 ETF. 부동산투자회사에 투자하여 임대·배당 수익 확보' },
    { ticker: 'DBC', label: '원자재', category: 'real_asset', categoryLabel: '실물자산', safeHaven: false, description: 'Invesco DB Commodity 추종. 원유·천연가스·금속·농산물 등 광범위한 원자재 바스켓' },
    { ticker: 'GLD', label: '금', category: 'real_asset', categoryLabel: '실물자산', safeHaven: true, description: '금 현물 가격 추종 ETF. 전통적 안전자산, 인플레이션·지정학적 리스크 헤지. 안전자산 ★' },
    // 현금등가
    { ticker: 'SHY', label: '단기 국채 (현금등가)', category: 'cash', categoryLabel: '현금등가', safeHaven: true, description: '미국 1-3년 단기 국채. 변동성 최소, 최종 안전 대피처. BEAR 국면 시 자금 이동 대상. 안전자산 ★' },
];

const TICKER_LABEL_MAP: Record<string, string> = Object.fromEntries(
    ASSET_CLASS_REGISTRY.map(a => [a.ticker, a.label])
);

const CATEGORY_WEIGHT_LABELS: Record<string, string> = {
    equity: '주식',
    alternative: '대체투자',
    foreign_bond: '해외채권',
    domestic_bond: '국내채권',
    cash: '현금등가',
};

// ── Screener types ──
interface ScreenedStock {
    rank: number;
    stk_cd: string;
    stk_nm: string;
    close: number;
    ret_3m: number;
    ret_6m: number;
    ret_12m: number;
    score: number;
    abs_pass: boolean;
    weight: number;
}

interface UniverseStock {
    stk_cd: string;
    stk_nm: string;
    close: number;
    score: number;
    ret_12m: number;
    passed: boolean;
    reason: string;
}

interface ScreenerResult {
    timestamp: string;
    ref_date: string;
    regime: string;
    kospi: number | null;
    kospi_sma200: number | null;
    config: {
        top_n: number;
        weight_method: string;
        min_trading_value: number;
    };
    summary: {
        total_stocks: number;
        universe_size: number;
        abs_momentum_pass: number;
        selected_count: number;
        data_start: string;
        data_end: string;
        error?: string;
    };
    passed_stocks: ScreenedStock[];
    all_universe: UniverseStock[];
    elapsed_sec: number;
}

// ═══════════════════════════════════════════════════
//  Formatters
// ═══════════════════════════════════════════════════

const formatKRW = (n: number) => {
    if (n >= 100_000_000) return (n / 100_000_000).toFixed(2) + '억';
    if (n >= 10_000) return (n / 10_000).toFixed(0) + '만';
    return n.toLocaleString();
};

const formatPct = (n: number | undefined, digits = 2) => {
    if (n === undefined || n === null) return '-';
    return (n * 100).toFixed(digits) + '%';
};

const formatRatio = (n: number | undefined, digits = 2) => {
    if (n === undefined || n === null) return '-';
    return n.toFixed(digits);
};

// ═══════════════════════════════════════════════════
//  Sub-components
// ═══════════════════════════════════════════════════

function StatusBadge({ s }: { s: PipelineStatus }) {
    const colors = {
        idle: 'bg-gray-100 text-gray-600',
        running: 'bg-amber-100 text-amber-600 animate-pulse',
        finished: s.exitCode === 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700',
    };
    const labels = { idle: '대기', running: '실행 중', finished: s.exitCode === 0 ? '완료' : '오류' };
    return (
        <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${colors[s.status]}`}>
            {s.status === 'running' && <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />}
            {labels[s.status]}
        </span>
    );
}

function StatCard({ label, value, color, icon }: {
    label: string; value: string; color: 'emerald' | 'red' | 'amber' | 'blue'; icon: React.ReactNode;
}) {
    const colorMap = {
        emerald: { bg: 'bg-emerald-50', border: 'border-emerald-100', text: 'text-emerald-700', icon: 'text-emerald-500' },
        red: { bg: 'bg-red-50', border: 'border-red-100', text: 'text-red-700', icon: 'text-red-500' },
        amber: { bg: 'bg-amber-50', border: 'border-amber-100', text: 'text-amber-700', icon: 'text-amber-500' },
        blue: { bg: 'bg-blue-50', border: 'border-blue-100', text: 'text-blue-700', icon: 'text-blue-500' },
    };
    const c = colorMap[color];
    return (
        <div className={`${c.bg} rounded-xl border ${c.border} p-4`}>
            <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-gray-500">{label}</span>
                <span className={c.icon}>{icon}</span>
            </div>
            <div className={`text-2xl font-bold ${c.text}`}>{value}</div>
        </div>
    );
}

function MetricItem({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-lg bg-gray-50 border border-gray-100 p-3">
            <div className="text-xs text-gray-500">{label}</div>
            <div className="text-sm font-bold text-gray-800 mt-1">{value}</div>
        </div>
    );
}

function RegimeBadge({ regime }: { regime: string }) {
    const isBull = regime === 'BULL';
    return (
        <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-bold ${isBull ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
            <span className={`w-2 h-2 rounded-full ${isBull ? 'bg-emerald-500' : 'bg-red-500'}`} />
            {isBull ? 'BULL' : 'BEAR'} 국면
        </span>
    );
}

// ═══════════════════════════════════════════════════
//  Asset Class Registry Reference Panel
// ═══════════════════════════════════════════════════

function AssetClassReferencePanel() {
    const [isOpen, setIsOpen] = useState(false);

    const categories = [
        { key: 'equity', label: '주식 (Equity)', color: 'blue', emoji: '📊' },
        { key: 'bond', label: '채권 (Bond)', color: 'emerald', emoji: '🏛️' },
        { key: 'real_asset', label: '실물자산 (Real Asset)', color: 'amber', emoji: '🏗️' },
        { key: 'cash', label: '현금등가 (Cash)', color: 'gray', emoji: '💵' },
    ];

    const colorMap: Record<string, { header: string; row: string; border: string }> = {
        blue:    { header: 'bg-blue-50 text-blue-800', row: 'hover:bg-blue-50/40', border: 'border-blue-200' },
        emerald: { header: 'bg-emerald-50 text-emerald-800', row: 'hover:bg-emerald-50/40', border: 'border-emerald-200' },
        amber:   { header: 'bg-amber-50 text-amber-800', row: 'hover:bg-amber-50/40', border: 'border-amber-200' },
        gray:    { header: 'bg-gray-100 text-gray-700', row: 'hover:bg-gray-50', border: 'border-gray-200' },
    };

    return (
        <div className="mb-6 rounded-xl border border-gray-200 bg-white overflow-hidden">
            <button
                className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-gray-50 transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <div className="flex items-center gap-2.5">
                    <BookOpen className="w-4 h-4 text-indigo-500" />
                    <span className="text-sm font-bold text-gray-700">자산군 레지스트리 · ETF 13종 상세 안내</span>
                    <span className="text-xs text-gray-400 font-normal ml-1">4개 카테고리 · 안전자산 4종 포함</span>
                </div>
                {isOpen ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
            </button>
            {isOpen && (
                <div className="px-5 pb-5 space-y-4 border-t border-gray-100 pt-4">
                    {/* Overall Summary */}
                    <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-4">
                        <div className="flex items-start gap-2">
                            <Info className="w-4 h-4 text-indigo-500 mt-0.5 shrink-0" />
                            <div className="text-xs text-gray-600 leading-relaxed space-y-1">
                                <p>
                                    <strong>글로벌 듀얼 모멘텀 전략</strong>은 4개 카테고리(주식·채권·실물자산·현금등가)의
                                    <strong> 13개 글로벌 ETF</strong>를 대상으로 <strong>상대 모멘텀</strong>(자산간 비교)과
                                    <strong> 절대 모멘텀</strong>(12개월 수익률 &gt; 0%)을 결합하여 자산을 배분합니다.
                                </p>
                                <p>
                                    각 ETF는 <strong>SMA200 국면 필터</strong>로 BULL/BEAR를 판단하며,
                                    BEAR 국면 자산의 비중은 <strong>안전자산(★ 표시)</strong>으로 자동 이전됩니다.
                                    벤치마크는 전통적 <strong>60/40 포트폴리오</strong>(SPY 60% + AGG 40%)입니다.
                                </p>
                            </div>
                        </div>
                    </div>

                    {/* Category Tables */}
                    {categories.map(cat => {
                        const assets = ASSET_CLASS_REGISTRY.filter(a => a.category === cat.key);
                        const cm = colorMap[cat.color];
                        return (
                            <div key={cat.key} className={`rounded-lg border ${cm.border} overflow-hidden`}>
                                <div className={`px-4 py-2 ${cm.header} flex items-center gap-2`}>
                                    <span>{cat.emoji}</span>
                                    <span className="text-xs font-bold">{cat.label}</span>
                                    <span className="text-xs opacity-60 ml-auto">{assets.length}종</span>
                                </div>
                                <table className="w-full text-xs">
                                    <thead>
                                        <tr className="border-b border-gray-100 bg-gray-50/50">
                                            <th className="px-4 py-2 text-left font-semibold text-gray-500 w-16">티커</th>
                                            <th className="px-4 py-2 text-left font-semibold text-gray-500 w-36">자산명</th>
                                            <th className="px-4 py-2 text-left font-semibold text-gray-500">설명</th>
                                            <th className="px-4 py-2 text-center font-semibold text-gray-500 w-16">대피처</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {assets.map(asset => (
                                            <tr key={asset.ticker} className={`border-b border-gray-50 ${cm.row}`}>
                                                <td className="px-4 py-2.5 font-mono font-bold text-gray-800">{asset.ticker}</td>
                                                <td className="px-4 py-2.5 font-medium text-gray-700">{asset.label}</td>
                                                <td className="px-4 py-2.5 text-gray-600 leading-relaxed">{asset.description}</td>
                                                <td className="px-4 py-2.5 text-center">
                                                    {asset.safeHaven ? (
                                                        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-emerald-100 text-emerald-600" title="안전자산 (Safe Haven)">★</span>
                                                    ) : (
                                                        <span className="text-gray-300">—</span>
                                                    )}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        );
                    })}

                    {/* Category → Ticker Mapping for Presets */}
                    <div className="rounded-lg border border-purple-200 overflow-hidden">
                        <div className="px-4 py-2 bg-purple-50 text-purple-800 flex items-center gap-2">
                            <span>🔗</span>
                            <span className="text-xs font-bold">프리셋 카테고리 → ETF 매핑</span>
                        </div>
                        <div className="p-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                            {[
                                { cat: '주식 (equity)', tickers: ['SPY', 'IWM', 'EFA', 'EEM', 'EWY'] },
                                { cat: '대체투자 (alternative)', tickers: ['VNQ', 'DBC', 'GLD'] },
                                { cat: '해외채권 (foreign_bond)', tickers: ['AGG', 'IEF', 'TLT', 'TIP'] },
                                { cat: '국내채권 (domestic_bond)', tickers: ['SHY'] },
                                { cat: '현금 (cash)', tickers: ['SHY'] },
                            ].map(m => (
                                <div key={m.cat} className="bg-white rounded-lg border border-purple-100 p-3">
                                    <div className="text-xs font-bold text-purple-700 mb-1.5">{m.cat}</div>
                                    <div className="flex flex-wrap gap-1">
                                        {m.tickers.map(t => (
                                            <span key={t} className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-purple-50 text-purple-800 text-xs">
                                                <span className="font-bold">{t}</span>
                                                <span className="text-purple-400">{TICKER_LABEL_MAP[t]}</span>
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Log Viewer (shared)
// ═══════════════════════════════════════════════════

function LogViewer({ status, label }: { status: PipelineStatus; label: string }) {
    const [showLogs, setShowLogs] = useState(false);
    const logRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (status.status === 'running' && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [status.logs, status.status]);

    return (
        <div className="bg-gray-900 rounded-xl shadow-2xl border border-gray-800 overflow-hidden">
            <button
                className="w-full flex items-center justify-between px-5 py-3 bg-gray-800/50 border-b border-gray-800 hover:bg-gray-800/80 transition-colors"
                onClick={() => setShowLogs(!showLogs)}
            >
                <div className="flex items-center gap-2">
                    <Terminal className="w-4 h-4 text-amber-400" />
                    <span className="text-sm font-bold text-gray-300">
                        Execution Logs <span className="text-amber-400 text-xs ml-1">[{label}]</span>
                    </span>
                </div>
                <div className="flex items-center gap-3">
                    <span className="text-[10px] text-gray-500 font-mono">
                        {status.pid ? `PID: ${status.pid}` : 'IDLE'} | {status.logs.length} lines
                    </span>
                    {showLogs ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
                </div>
            </button>
            {showLogs && (
                <div
                    ref={logRef}
                    className="p-6 font-mono text-sm text-gray-300 h-[400px] overflow-y-auto leading-relaxed scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent"
                >
                    {status.logs.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-gray-600 italic">
                            <Terminal className="w-12 h-12 mb-2 opacity-20" />
                            실행 로그가 여기에 표시됩니다.
                        </div>
                    ) : (
                        status.logs.map((line, i) => (
                            <div key={i} className="mb-0.5 border-l-2 border-transparent hover:border-amber-500/30 hover:bg-white/5 px-2 transition-all">
                                <span className="text-gray-600 inline-block w-8 select-none">{i + 1}</span>
                                {line}
                            </div>
                        ))
                    )}
                </div>
            )}
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Strategy Info Card (shared)
// ═══════════════════════════════════════════════════

function StrategyInfoCard() {
    return (
        <div className="mb-6 rounded-xl border p-4 bg-amber-50/50 border-amber-100">
            <div className="flex items-center gap-2 mb-3">
                <Shield className="w-4 h-4 text-amber-500" />
                <span className="text-sm font-bold text-amber-700">전략 파라미터</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">모멘텀 윈도우</div>
                    <div className="font-bold text-gray-800 mt-0.5">3 / 6 / 12개월</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">국면 필터</div>
                    <div className="font-bold text-gray-800 mt-0.5">KOSPI vs SMA200</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">리밸런싱 주기</div>
                    <div className="font-bold text-gray-800 mt-0.5">월말 (Monthly)</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">절대 모멘텀</div>
                    <div className="font-bold text-gray-800 mt-0.5">12M {'>'} 0%</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">ADTV 임계값</div>
                    <div className="font-bold text-gray-800 mt-0.5">50억 원</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">슬리피지 모델</div>
                    <div className="font-bold text-gray-800 mt-0.5">방향성 0.2%</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">매매 방식</div>
                    <div className="font-bold text-gray-800 mt-0.5">Netting (차액)</div>
                </div>
                <div className="bg-white rounded-lg p-2.5 border border-amber-100">
                    <div className="text-gray-500">수수료</div>
                    <div className="font-bold text-gray-800 mt-0.5">0.015% (편도)</div>
                </div>
            </div>
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Screener Tab
// ═══════════════════════════════════════════════════

function ScreenerTab() {
    const [status, setStatus] = useState<PipelineStatus>({ name: 'momentum-screener', status: 'idle', logs: [] });
    const [result, setResult] = useState<ScreenerResult | null>(null);
    const [topN, setTopN] = useState(20);
    const [weightMethod, setWeightMethod] = useState<'inverse_volatility' | 'equal_weight'>('inverse_volatility');
    const [sortKey, setSortKey] = useState<'rank' | 'score' | 'ret_3m' | 'ret_6m' | 'ret_12m' | 'weight' | 'close'>('rank');
    const [sortAsc, setSortAsc] = useState(true);
    const [showUniverse, setShowUniverse] = useState(false);

    const isRunning = status.status === 'running';

    // Status polling
    useEffect(() => {
        const fetchFn = () => {
            axios.get(`${API}/status/momentum-screener`)
                .then(r => setStatus(r.data))
                .catch(() => { });
        };
        fetchFn();
        const id = setInterval(fetchFn, 3000);
        return () => clearInterval(id);
    }, []);

    // Auto-load results
    useEffect(() => {
        if (status.status === 'finished' && status.exitCode === 0) loadResults();
    }, [status.status, status.exitCode]);

    useEffect(() => { loadResults(); }, []);

    const loadResults = () => {
        axios.get(`${API}/momentum-screener/result`)
            .then(r => {
                if (r.data.status === 'ok' && r.data.data) setResult(r.data.data);
            })
            .catch(() => { });
    };

    const startScreener = () => {
        axios.post(`${API}/momentum-screener`, {
            top_n: topN,
            weight_method: weightMethod,
        }).catch(err => alert('실행 실패: ' + err.message));
    };

    const stopScreener = () => {
        axios.post(`${API}/stop`, { name: 'momentum-screener' }).catch(() => { });
    };

    // Sorted stocks
    const sortedStocks = useMemo(() => {
        if (!result?.passed_stocks) return [];
        const arr = [...result.passed_stocks];
        arr.sort((a, b) => {
            const va = a[sortKey] ?? 0;
            const vb = b[sortKey] ?? 0;
            return sortAsc ? (va as number) - (vb as number) : (vb as number) - (va as number);
        });
        return arr;
    }, [result, sortKey, sortAsc]);

    const handleSort = (key: typeof sortKey) => {
        if (sortKey === key) setSortAsc(!sortAsc);
        else { setSortKey(key); setSortAsc(key === 'rank'); }
    };

    const SortHeader = ({ label, k, className = '' }: { label: string; k: typeof sortKey; className?: string }) => (
        <th
            className={`px-3 py-3 text-xs font-semibold text-gray-500 cursor-pointer hover:text-gray-800 transition-colors select-none ${className}`}
            onClick={() => handleSort(k)}
        >
            <div className="flex items-center gap-1 justify-end">
                {label}
                <ArrowUpDown className={`w-3 h-3 ${sortKey === k ? 'text-amber-500' : 'text-gray-300'}`} />
            </div>
        </th>
    );

    const summary = result?.summary;

    return (
        <div className="space-y-6">
            {/* Controls */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center">
                            <Search className="w-6 h-6 text-amber-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">듀얼 모멘텀 스크리너</h2>
                            <p className="text-sm text-gray-500">현시점 기준 3/6/12개월 듀얼 모멘텀 Top-N 종목 스크리닝</p>
                        </div>
                    </div>
                    <StatusBadge s={status} />
                </div>

                <StrategyInfoCard />

                {/* Parameters */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <Target className="w-4 h-4 text-gray-400" />
                            Top-N 편입 종목
                        </label>
                        <input
                            type="number" min={5} max={50} step={5}
                            className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-amber-500 outline-none transition-all"
                            value={topN}
                            onChange={e => setTopN(Number(e.target.value))}
                            disabled={isRunning}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <BarChart3 className="w-4 h-4 text-gray-400" />
                            가중치 배분
                        </label>
                        <select
                            className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-amber-500 outline-none transition-all bg-white"
                            value={weightMethod}
                            onChange={e => setWeightMethod(e.target.value as 'inverse_volatility' | 'equal_weight')}
                            disabled={isRunning}
                        >
                            <option value="inverse_volatility">변동성 역가중 (IV)</option>
                            <option value="equal_weight">동일 비중 (EW)</option>
                        </select>
                    </div>
                    <div className="flex items-end">
                        <div className="flex gap-3 w-full">
                            <button
                                className="flex-1 flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl font-bold text-white bg-amber-600 hover:bg-amber-700 disabled:opacity-50 transition-all shadow-lg shadow-amber-200 active:scale-[0.98]"
                                onClick={startScreener}
                                disabled={isRunning}
                            >
                                <Play className="w-4 h-4" />
                                스크리닝 시작
                            </button>
                            <button
                                className="flex items-center justify-center px-4 py-2.5 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 transition-all active:scale-[0.98]"
                                onClick={stopScreener}
                                disabled={!isRunning}
                            >
                                <Square className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* ──────── Results ──────── */}
            {result && (
                <>
                    {/* Summary Cards */}
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                        <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
                            <div className="text-xs text-gray-500 mb-1">국면</div>
                            {result.regime ? <RegimeBadge regime={result.regime} /> : <span className="text-gray-400">-</span>}
                        </div>
                        <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
                            <div className="text-xs text-gray-500 mb-1">KOSPI / SMA200</div>
                            <div className="text-lg font-bold text-gray-800">
                                {result.kospi ? result.kospi.toLocaleString() : '-'}
                                <span className="text-gray-400 text-sm font-normal"> / </span>
                                {result.kospi_sma200 ? result.kospi_sma200.toLocaleString() : '-'}
                            </div>
                        </div>
                        <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
                            <div className="text-xs text-gray-500 mb-1">유니버스 (ADTV≥50억)</div>
                            <div className="text-lg font-bold text-gray-800">{summary?.universe_size ?? '-'}
                                <span className="text-sm font-normal text-gray-400"> / {summary?.total_stocks ?? '-'} 종목</span>
                            </div>
                        </div>
                        <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
                            <div className="text-xs text-gray-500 mb-1">절대 모멘텀 통과</div>
                            <div className="text-lg font-bold text-emerald-700">{summary?.abs_momentum_pass ?? '-'}
                                <span className="text-sm font-normal text-gray-400"> 종목</span>
                            </div>
                        </div>
                        <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
                            <div className="text-xs text-gray-500 mb-1">최종 편입</div>
                            <div className="text-lg font-bold text-amber-700">{summary?.selected_count ?? '-'}
                                <span className="text-sm font-normal text-gray-400"> 종목</span>
                            </div>
                        </div>
                    </div>

                    {/* BEAR Warning */}
                    {result.regime === 'BEAR' && (
                        <div className="bg-red-50 border border-red-200 rounded-xl p-5 flex items-start gap-3">
                            <Shield className="w-6 h-6 text-red-500 mt-0.5 flex-shrink-0" />
                            <div>
                                <h3 className="text-red-800 font-bold">BEAR 국면 감지 -- 전액 현금화 권고</h3>
                                <p className="text-red-600 text-sm mt-1">
                                    KOSPI({result.kospi?.toLocaleString()})가 SMA200({result.kospi_sma200?.toLocaleString()}) 아래입니다.
                                    듀얼 모멘텀 전략에 따라 모든 주식 비중을 0%로 설정하고 전액 현금을 보유하는 것을 권장합니다.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Top-N Table */}
                    {sortedStocks.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                            <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <TrendingUp className="w-5 h-5 text-amber-500" />
                                    <h3 className="text-lg font-bold text-gray-800">
                                        모멘텀 Top-{result.config.top_n} 편입 종목
                                    </h3>
                                    <span className="text-xs text-gray-400 ml-2">
                                        기준일: {result.ref_date} | {result.config.weight_method === 'inverse_volatility' ? '변동성 역가중' : '동일 비중'}
                                    </span>
                                </div>
                            </div>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="bg-gray-50 border-b border-gray-100">
                                            <SortHeader label="Rank" k="rank" className="text-left pl-6" />
                                            <th className="px-3 py-3 text-xs font-semibold text-gray-500 text-left">종목코드</th>
                                            <th className="px-3 py-3 text-xs font-semibold text-gray-500 text-left">종목명</th>
                                            <SortHeader label="종가" k="close" />
                                            <SortHeader label="3M" k="ret_3m" />
                                            <SortHeader label="6M" k="ret_6m" />
                                            <SortHeader label="12M" k="ret_12m" />
                                            <SortHeader label="Score" k="score" />
                                            <SortHeader label="비중(%)" k="weight" />
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sortedStocks.map((stk, i) => (
                                            <tr key={stk.stk_cd} className={`border-b border-gray-50 hover:bg-amber-50/30 transition-colors ${i < 5 ? 'bg-amber-50/10' : ''}`}>
                                                <td className="px-3 py-2.5 pl-6 font-bold text-amber-600">{stk.rank}</td>
                                                <td className="px-3 py-2.5 font-mono text-gray-600 text-xs">{stk.stk_cd}</td>
                                                <td className="px-3 py-2.5 font-semibold text-gray-800">{stk.stk_nm}</td>
                                                <td className="px-3 py-2.5 text-right font-mono text-gray-700">{stk.close.toLocaleString()}</td>
                                                <td className={`px-3 py-2.5 text-right font-mono ${stk.ret_3m >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                                    {stk.ret_3m >= 0 ? '+' : ''}{stk.ret_3m.toFixed(1)}%
                                                </td>
                                                <td className={`px-3 py-2.5 text-right font-mono ${stk.ret_6m >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                                    {stk.ret_6m >= 0 ? '+' : ''}{stk.ret_6m.toFixed(1)}%
                                                </td>
                                                <td className={`px-3 py-2.5 text-right font-mono ${stk.ret_12m >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                                    {stk.ret_12m >= 0 ? '+' : ''}{stk.ret_12m.toFixed(1)}%
                                                </td>
                                                <td className={`px-3 py-2.5 text-right font-bold ${stk.score >= 0 ? 'text-amber-700' : 'text-red-600'}`}>
                                                    {stk.score >= 0 ? '+' : ''}{stk.score.toFixed(1)}%
                                                </td>
                                                <td className="px-3 py-2.5 text-right font-bold text-blue-700">
                                                    {stk.weight.toFixed(2)}%
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 text-xs text-gray-400 flex justify-between">
                                <span>{result.timestamp} | {result.elapsed_sec}초 소요</span>
                                <span>데이터 기간: {summary?.data_start} ~ {summary?.data_end}</span>
                            </div>
                        </div>
                    )}

                    {/* All Universe (collapsible) */}
                    {result.all_universe && result.all_universe.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                            <button
                                className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                                onClick={() => setShowUniverse(!showUniverse)}
                            >
                                <div className="flex items-center gap-2">
                                    <BarChart3 className="w-5 h-5 text-gray-400" />
                                    <span className="text-sm font-bold text-gray-700">
                                        전체 유니버스 ({result.all_universe.length}종목)
                                    </span>
                                    <span className="text-xs text-gray-400">
                                        통과 {result.all_universe.filter(s => s.passed).length} / 탈락 {result.all_universe.filter(s => !s.passed).length}
                                    </span>
                                </div>
                                {showUniverse ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                            </button>
                            {showUniverse && (
                                <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                                    <table className="w-full text-sm">
                                        <thead className="sticky top-0 bg-gray-50">
                                            <tr className="border-b border-gray-100">
                                                <th className="px-4 py-2 text-xs font-semibold text-gray-500 text-left">상태</th>
                                                <th className="px-3 py-2 text-xs font-semibold text-gray-500 text-left">코드</th>
                                                <th className="px-3 py-2 text-xs font-semibold text-gray-500 text-left">종목명</th>
                                                <th className="px-3 py-2 text-xs font-semibold text-gray-500 text-right">종가</th>
                                                <th className="px-3 py-2 text-xs font-semibold text-gray-500 text-right">12M</th>
                                                <th className="px-3 py-2 text-xs font-semibold text-gray-500 text-right">Score</th>
                                                <th className="px-3 py-2 text-xs font-semibold text-gray-500 text-left">사유</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {result.all_universe.map(stk => (
                                                <tr key={stk.stk_cd} className={`border-b border-gray-50 text-xs ${stk.passed ? 'bg-amber-50/20' : 'opacity-60'}`}>
                                                    <td className="px-4 py-1.5">
                                                        {stk.passed
                                                            ? <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                                                            : <XCircle className="w-4 h-4 text-red-400" />}
                                                    </td>
                                                    <td className="px-3 py-1.5 font-mono text-gray-600">{stk.stk_cd}</td>
                                                    <td className="px-3 py-1.5 text-gray-800">{stk.stk_nm}</td>
                                                    <td className="px-3 py-1.5 text-right font-mono text-gray-600">{(stk.close ?? 0).toLocaleString()}</td>
                                                    <td className={`px-3 py-1.5 text-right font-mono ${(stk.ret_12m ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                                                        {(stk.ret_12m ?? 0) >= 0 ? '+' : ''}{(stk.ret_12m ?? 0).toFixed(1)}%
                                                    </td>
                                                    <td className="px-3 py-1.5 text-right font-mono text-gray-700">{(stk.score ?? 0).toFixed(1)}%</td>
                                                    <td className="px-3 py-1.5 text-gray-400 truncate max-w-[200px]">{stk.reason}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}
                </>
            )}

            {/* Log Viewer */}
            <LogViewer status={status} label="Screener" />
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Backtest Tab
// ═══════════════════════════════════════════════════

function BacktestTab() {
    const [status, setStatus] = useState<PipelineStatus>({ name: 'momentum-backtest', status: 'idle', logs: [] });
    const [result, setResult] = useState<MomentumResult | null>(null);
    const [capital, setCapital] = useState(100_000_000);
    const [topN, setTopN] = useState(20);
    const [weightMethod, setWeightMethod] = useState<'inverse_volatility' | 'equal_weight'>('inverse_volatility');
    const [months, setMonths] = useState(12);
    const [fullPeriod, setFullPeriod] = useState(false);
    const [showChart, setShowChart] = useState(true);

    const isRunning = status.status === 'running';

    // Status polling
    useEffect(() => {
        const fetchStatus = () => {
            axios.get(`${API}/status/momentum-backtest`)
                .then(r => setStatus(r.data))
                .catch(() => { });
        };
        fetchStatus();
        const id = setInterval(fetchStatus, 3000);
        return () => clearInterval(id);
    }, []);

    useEffect(() => {
        if (status.status === 'finished' && status.exitCode === 0) loadResults();
    }, [status.status, status.exitCode]);

    useEffect(() => { loadResults(); }, []);

    const loadResults = () => {
        axios.get(`${API}/momentum-backtest/result`)
            .then(r => { if (r.data.status === 'ok' && r.data.data) setResult(r.data.data); })
            .catch(() => { });
    };

    const startBacktest = () => {
        axios.post(`${API}/momentum-backtest`, {
            capital, top_n: topN, weight_method: weightMethod, months, full: fullPeriod,
        }).catch(err => alert('실행 실패: ' + err.message));
    };

    const stopBacktest = () => {
        axios.post(`${API}/stop`, { name: 'momentum-backtest' }).catch(() => { });
    };

    const chartData = useMemo(() => {
        if (!result?.equity_curve) return [];
        const entries = Object.entries(result.equity_curve).sort(([a], [b]) => a.localeCompare(b));
        if (entries.length === 0) return [];
        const firstYear = entries[0][0].slice(0, 4);
        const lastYear = entries[entries.length - 1][0].slice(0, 4);
        const multiYear = firstYear !== lastYear;
        return entries.map(([date, value]) => ({
            date,
            value: Math.round(value),
            // 1년 이내: MM-DD, 복수 연도: YY-MM-DD
            displayDate: multiYear ? date.slice(2) : date.slice(5),
        }));
    }, [result]);

    const drawdownData = useMemo(() => {
        if (chartData.length === 0) return [];
        let peak = chartData[0].value;
        return chartData.map(d => {
            if (d.value > peak) peak = d.value;
            const dd = peak > 0 ? ((d.value - peak) / peak) * 100 : 0;
            return { date: d.date, displayDate: d.displayDate, drawdown: Math.round(dd * 100) / 100 };
        });
    }, [chartData]);

    const metrics = result?.metrics ?? {};

    return (
        <div className="space-y-6">
            {/* Controls */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center">
                            <TrendingUp className="w-6 h-6 text-amber-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">중장기 듀얼 모멘텀 백테스터</h2>
                            <p className="text-sm text-gray-500">3/6/12개월 복합 모멘텀 + KOSPI SMA200 국면 필터 + 월말 리밸런싱</p>
                        </div>
                    </div>
                    <StatusBadge s={status} />
                </div>

                <StrategyInfoCard />

                {/* Parameters */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <DollarSign className="w-4 h-4 text-gray-400" />
                            초기 자본금
                        </label>
                        <div className="relative">
                            <input
                                type="number"
                                className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-amber-500 outline-none transition-all"
                                value={capital} onChange={e => setCapital(Number(e.target.value))} disabled={isRunning}
                            />
                            <span className="absolute right-4 top-3 text-gray-400 text-sm">원</span>
                        </div>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <Target className="w-4 h-4 text-gray-400" />
                            Top-N 편입 종목
                        </label>
                        <input
                            type="number" min={5} max={50} step={5}
                            className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-amber-500 outline-none transition-all"
                            value={topN} onChange={e => setTopN(Number(e.target.value))} disabled={isRunning}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <BarChart3 className="w-4 h-4 text-gray-400" />
                            가중치 배분
                        </label>
                        <select
                            className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-amber-500 outline-none transition-all bg-white"
                            value={weightMethod}
                            onChange={e => setWeightMethod(e.target.value as 'inverse_volatility' | 'equal_weight')}
                            disabled={isRunning}
                        >
                            <option value="inverse_volatility">변동성 역가중 (IV)</option>
                            <option value="equal_weight">동일 비중 (EW)</option>
                        </select>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <Activity className="w-4 h-4 text-gray-400" />
                            백테스트 기간
                        </label>
                        <div className="flex gap-2">
                            {!fullPeriod && (
                                <div className="relative flex-1">
                                    <input
                                        type="number" min={3} max={120}
                                        className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-amber-500 outline-none transition-all"
                                        value={months} onChange={e => setMonths(Number(e.target.value))} disabled={isRunning}
                                    />
                                    <span className="absolute right-3 top-3 text-gray-400 text-sm">개월</span>
                                </div>
                            )}
                            <button
                                className={`px-3 py-2.5 rounded-lg text-xs font-bold transition-all border ${fullPeriod
                                    ? 'bg-amber-100 text-amber-800 border-amber-300 ring-1 ring-amber-200'
                                    : 'bg-white text-gray-600 border-gray-200 hover:border-amber-300'}`}
                                onClick={() => setFullPeriod(!fullPeriod)} disabled={isRunning}
                            >
                                {fullPeriod ? '전체' : 'ALL'}
                            </button>
                        </div>
                    </div>
                </div>

                {/* Action Buttons */}
                <div className="flex gap-3">
                    <button
                        className="flex-1 flex items-center justify-center gap-2 px-6 py-3 rounded-xl font-bold text-white bg-amber-600 hover:bg-amber-700 disabled:opacity-50 transition-all shadow-lg shadow-amber-200 active:scale-[0.98]"
                        onClick={startBacktest} disabled={isRunning}
                    >
                        <Play className="w-5 h-5" />
                        듀얼 모멘텀 백테스팅 시작
                    </button>
                    <button
                        className="flex items-center justify-center px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 transition-all active:scale-[0.98]"
                        onClick={stopBacktest} disabled={!isRunning}
                    >
                        <Square className="w-5 h-5" /> 중단
                    </button>
                </div>
            </div>

            {/* Results */}
            {result && (
                <>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <StatCard label="총 수익률" value={formatPct(metrics.total_return)}
                            color={(metrics.total_return ?? 0) >= 0 ? 'emerald' : 'red'}
                            icon={<TrendingUp className="w-5 h-5" />} />
                        <StatCard label="CAGR" value={formatPct(metrics.cagr)}
                            color={(metrics.cagr ?? 0) >= 0 ? 'emerald' : 'red'}
                            icon={<Percent className="w-5 h-5" />} />
                        <StatCard label="MDD" value={formatPct(metrics.mdd)} color="red"
                            icon={<Activity className="w-5 h-5" />} />
                        <StatCard label="Sharpe Ratio" value={formatRatio(metrics.sharpe_ratio)}
                            color={(metrics.sharpe_ratio ?? 0) >= 1 ? 'emerald' : 'amber'}
                            icon={<BarChart3 className="w-5 h-5" />} />
                    </div>

                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                        <h3 className="text-lg font-bold text-gray-800 mb-4 flex items-center gap-2">
                            <BarChart3 className="w-5 h-5 text-amber-500" />
                            상세 성과 지표
                        </h3>
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                            <MetricItem label="Sortino Ratio" value={formatRatio(metrics.sortino_ratio)} />
                            <MetricItem label="Calmar Ratio" value={formatRatio(metrics.calmar_ratio)} />
                            <MetricItem label="Profit Factor" value={formatRatio(metrics.profit_factor)} />
                            <MetricItem label="일간 승률" value={formatPct(metrics.daily_win_rate)} />
                            <MetricItem label="월간 승률" value={formatPct(metrics.monthly_win_rate)} />
                            <MetricItem label="연환산 변동성" value={formatPct(metrics.annualized_volatility)} />
                            <MetricItem label="MDD 지속" value={metrics.mdd_duration_days !== undefined ? `${metrics.mdd_duration_days}일` : '-'} />
                            <MetricItem label="Best Day" value={formatPct(metrics.best_day)} />
                            <MetricItem label="Worst Day" value={formatPct(metrics.worst_day)} />
                            <MetricItem label="최종 자산" value={metrics.final_equity ? formatKRW(metrics.final_equity) + '원' : '-'} />
                        </div>

                        <div className="mt-6 pt-4 border-t border-gray-100 grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <h4 className="text-sm font-bold text-gray-700 mb-3">거래 요약</h4>
                                <div className="flex flex-wrap gap-2">
                                    {Object.entries(result.trade_summary).map(([action, cnt]) => (
                                        <span key={action} className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-gray-50 text-sm border border-gray-100">
                                            <span className="font-bold text-gray-800">{action}</span>
                                            <span className="text-gray-500">{cnt}회</span>
                                        </span>
                                    ))}
                                    {metrics.total_friction !== undefined && (
                                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-50 text-sm border border-red-100">
                                            <span className="font-bold text-red-700">마찰비용</span>
                                            <span className="text-red-600">{formatKRW(metrics.total_friction as number)}원</span>
                                        </span>
                                    )}
                                </div>
                            </div>
                            <div>
                                <h4 className="text-sm font-bold text-gray-700 mb-3">국면 이력</h4>
                                <div className="flex gap-3">
                                    <div className="flex-1 rounded-lg bg-emerald-50 border border-emerald-100 p-3 text-center">
                                        <div className="text-2xl font-bold text-emerald-700">{result.regime_summary.BULL}</div>
                                        <div className="text-xs text-emerald-600 mt-1">BULL 국면</div>
                                    </div>
                                    <div className="flex-1 rounded-lg bg-red-50 border border-red-100 p-3 text-center">
                                        <div className="text-2xl font-bold text-red-700">{result.regime_summary.BEAR}</div>
                                        <div className="text-xs text-red-600 mt-1">BEAR 국면</div>
                                    </div>
                                    <div className="flex-1 rounded-lg bg-gray-50 border border-gray-100 p-3 text-center">
                                        <div className="text-2xl font-bold text-gray-700">
                                            {result.regime_summary.BULL + result.regime_summary.BEAR}
                                        </div>
                                        <div className="text-xs text-gray-500 mt-1">총 리밸런싱</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="mt-4 pt-4 border-t border-gray-100">
                            <div className="flex items-center justify-between text-xs text-gray-400">
                                <span>
                                    {result.config.weight_method === 'inverse_volatility' ? '변동성 역가중' : '동일 비중'} |
                                    Top-{result.config.top_n} |
                                    초기자본 {formatKRW(result.config.initial_capital)}원
                                </span>
                                <span>{result.timestamp} | {result.elapsed_sec}초 소요</span>
                            </div>
                        </div>
                    </div>

                    {/* Equity Curve */}
                    {chartData.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                            <button
                                className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                                onClick={() => setShowChart(!showChart)}
                            >
                                <div className="flex items-center gap-2">
                                    <Activity className="w-5 h-5 text-amber-500" />
                                    <span className="text-lg font-bold text-gray-800">자산 가치 곡선 (Equity Curve)</span>
                                </div>
                                {showChart ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                            </button>
                            {showChart && (
                                <div className="px-6 pb-6">
                                    <ResponsiveContainer width="100%" height={320}>
                                        <AreaChart data={chartData}>
                                            <defs>
                                                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
                                                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                                                </linearGradient>
                                            </defs>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                            <XAxis dataKey="displayDate" tick={{ fontSize: 11, fill: '#94a3b8' }}
                                                interval={Math.max(1, Math.floor(chartData.length / 10))} />
                                            <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }}
                                                tickFormatter={(v: number) => formatKRW(v)} width={72} />
                                            <Tooltip
                                                formatter={(val: unknown) => [formatKRW(Number(val)) + '원', '자산가치']}
                                                labelFormatter={(label: unknown) => `날짜: ${label}`}
                                                contentStyle={{ borderRadius: '8px', border: '1px solid #e2e8f0', fontSize: 12 }}
                                            />
                                            <ReferenceLine y={result.config.initial_capital} stroke="#94a3b8" strokeDasharray="5 5"
                                                label={{ value: '초기자본', position: 'left', fill: '#94a3b8', fontSize: 10 }} />
                                            <Area type="monotone" dataKey="value" stroke="#f59e0b" strokeWidth={2} fill="url(#equityGrad)" />
                                        </AreaChart>
                                    </ResponsiveContainer>

                                    <div className="mt-4 pt-4 border-t border-gray-100">
                                        <h4 className="text-sm font-bold text-gray-600 mb-2">낙폭 (Drawdown)</h4>
                                        <ResponsiveContainer width="100%" height={140}>
                                            <AreaChart data={drawdownData}>
                                                <defs>
                                                    <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                                                        <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                                                        <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                                                    </linearGradient>
                                                </defs>
                                                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                                <XAxis dataKey="displayDate" tick={{ fontSize: 10, fill: '#94a3b8' }}
                                                    interval={Math.max(1, Math.floor(drawdownData.length / 10))} />
                                                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }}
                                                    tickFormatter={(v: number) => v.toFixed(1) + '%'} width={52} />
                                                <Tooltip
                                                    formatter={(val: unknown) => [Number(val).toFixed(2) + '%', '낙폭']}
                                                    labelFormatter={(label: unknown) => `날짜: ${label}`}
                                                    contentStyle={{ borderRadius: '8px', border: '1px solid #fecaca', fontSize: 12 }}
                                                />
                                                <ReferenceLine y={0} stroke="#94a3b8" />
                                                <Area type="monotone" dataKey="drawdown" stroke="#ef4444" strokeWidth={1.5} fill="url(#ddGrad)" />
                                            </AreaChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </>
            )}

            {/* Log Viewer */}
            <LogViewer status={status} label="Backtest" />
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Global Backtest Tab
// ═══════════════════════════════════════════════════

function GlobalBacktestTab() {
    const [status, setStatus] = useState<PipelineStatus>({ name: 'global-momentum-backtest', status: 'idle', logs: [] });
    const [result, setResult] = useState<GlobalMomentumResult | null>(null);
    const [capital, setCapital] = useState(100_000_000);
    const [preset, setPreset] = useState('balanced');
    const [months, setMonths] = useState(12);
    const [fullPeriod, setFullPeriod] = useState(false);
    const [showChart, setShowChart] = useState(true);
    const [showAllocation, setShowAllocation] = useState(false);

    const isRunning = status.status === 'running';

    // Status polling
    useEffect(() => {
        const fetchStatus = () => {
            axios.get(`${API}/status/global-momentum-backtest`)
                .then(r => setStatus(r.data))
                .catch(() => { });
        };
        fetchStatus();
        const id = setInterval(fetchStatus, 3000);
        return () => clearInterval(id);
    }, []);

    useEffect(() => {
        if (status.status === 'finished' && status.exitCode === 0) loadResults();
    }, [status.status, status.exitCode]);

    useEffect(() => { loadResults(); }, []);

    const loadResults = () => {
        axios.get(`${API}/global-momentum-backtest/result`)
            .then(r => { if (r.data.status === 'ok' && r.data.data) setResult(r.data.data); })
            .catch(() => { });
    };

    const startBacktest = () => {
        axios.post(`${API}/global-momentum-backtest`, {
            capital, portfolio_preset: preset, months, full: fullPeriod,
        }).catch(err => alert('실행 실패: ' + err.message));
    };

    const stopBacktest = () => {
        axios.post(`${API}/stop`, { name: 'global-momentum-backtest' }).catch(() => { });
    };

    // Equity + Benchmark chart data
    const chartData = useMemo(() => {
        if (!result?.equity_curve) return [];
        const entries = Object.entries(result.equity_curve).sort(([a], [b]) => a.localeCompare(b));
        if (entries.length === 0) return [];
        const bm = result.benchmark_equity ?? {};
        const firstYear = entries[0][0].slice(0, 4);
        const lastYear = entries[entries.length - 1][0].slice(0, 4);
        const multiYear = firstYear !== lastYear;
        return entries.map(([date, value]) => ({
            date,
            value: Math.round(value),
            benchmark: Math.round(bm[date] ?? 0),
            displayDate: multiYear ? date.slice(2) : date.slice(5),
        }));
    }, [result]);

    // Drawdown data
    const drawdownData = useMemo(() => {
        if (chartData.length === 0) return [];
        let peak = chartData[0].value;
        let bmPeak = chartData[0].benchmark || chartData[0].value;
        return chartData.map(d => {
            if (d.value > peak) peak = d.value;
            if (d.benchmark > bmPeak) bmPeak = d.benchmark;
            const dd = peak > 0 ? ((d.value - peak) / peak) * 100 : 0;
            const bmDd = bmPeak > 0 ? ((d.benchmark - bmPeak) / bmPeak) * 100 : 0;
            return {
                date: d.date, displayDate: d.displayDate,
                drawdown: Math.round(dd * 100) / 100,
                bmDrawdown: Math.round(bmDd * 100) / 100,
            };
        });
    }, [chartData]);

    // Asset allocation bar chart data (last rebalancing)
    const allocationData = useMemo(() => {
        if (!result?.global_allocation?.length) return [];
        const last = result.global_allocation[result.global_allocation.length - 1];
        return Object.entries(last.weights)
            .filter(([, w]) => w > 0)
            .sort(([, a], [, b]) => b - a)
            .map(([ticker, w]) => ({
                ticker,
                weight: Math.round(w * 1000) / 10,
                regime: last.regimes?.[ticker] ?? '?',
            }));
    }, [result]);

    const metrics = result?.metrics ?? {};
    const presetMeta = PRESET_INFO[result?.config?.portfolio_preset ?? preset] ?? PRESET_INFO.balanced;

    return (
        <div className="space-y-6">
            {/* Controls */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-indigo-100 flex items-center justify-center">
                            <Globe className="w-6 h-6 text-indigo-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">글로벌 멀티에셋 듀얼 모멘텀</h2>
                            <p className="text-sm text-gray-500">ETF 13종 · 6개 자산군 · 프리셋 기반 전략적 자산배분 + 모멘텀</p>
                        </div>
                    </div>
                    <StatusBadge s={status} />
                </div>

                {/* Strategy Info */}
                <div className="mb-6 rounded-xl border p-4 bg-indigo-50/50 border-indigo-100">
                    <div className="flex items-center gap-2 mb-3">
                        <Shield className="w-4 h-4 text-indigo-500" />
                        <span className="text-sm font-bold text-indigo-700">글로벌 전략 파라미터</span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">자산군</div>
                            <div className="font-bold text-gray-800 mt-0.5">6종 (ETF 13개)</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">국면 필터</div>
                            <div className="font-bold text-gray-800 mt-0.5">SMA200 vs Price</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">리밸런싱</div>
                            <div className="font-bold text-gray-800 mt-0.5">월말 (Monthly)</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">안전자산</div>
                            <div className="font-bold text-gray-800 mt-0.5">SHY (단기국채)</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">벤치마크</div>
                            <div className="font-bold text-gray-800 mt-0.5">60/40 (SPY+AGG)</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">비용 모델</div>
                            <div className="font-bold text-gray-800 mt-0.5">0.07% + 0.05%</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">절대 모멘텀</div>
                            <div className="font-bold text-gray-800 mt-0.5">12M {'>'} 0%</div>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-indigo-100">
                            <div className="text-gray-500">한국 ETF</div>
                            <div className="font-bold text-gray-800 mt-0.5">EWY (한국 주식)</div>
                        </div>
                    </div>
                </div>

                {/* Preset Selection */}
                <div className="mb-6">
                    <label className="block text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
                        <Target className="w-4 h-4 text-gray-400" />
                        포트폴리오 프리셋 선택
                    </label>
                    <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
                        {Object.entries(PRESET_INFO).map(([key, info]) => (
                            <button
                                key={key}
                                className={`relative p-4 rounded-xl border-2 transition-all text-left ${preset === key
                                    ? 'border-indigo-500 bg-indigo-50 shadow-md shadow-indigo-100'
                                    : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-indigo-50/30'}`}
                                onClick={() => setPreset(key)}
                                disabled={isRunning}
                            >
                                <div className="text-2xl mb-1">{info.emoji}</div>
                                <div className="text-sm font-bold text-gray-800">{info.label}</div>
                                <div className="text-xs text-gray-500 mt-0.5">{info.desc}</div>
                                <div className="mt-2 flex gap-0.5">
                                    {[1, 2, 3, 4, 5].map(i => (
                                        <div
                                            key={i}
                                            className={`h-1.5 flex-1 rounded-full ${i <= info.risk
                                                ? (info.risk >= 4 ? 'bg-red-400' : info.risk >= 3 ? 'bg-amber-400' : 'bg-emerald-400')
                                                : 'bg-gray-200'}`}
                                        />
                                    ))}
                                </div>
                                {preset === key && (
                                    <div className="absolute top-2 right-2">
                                        <CheckCircle2 className="w-5 h-5 text-indigo-500" />
                                    </div>
                                )}
                            </button>
                        ))}
                    </div>

                    {/* Selected Preset Detail */}
                    {PRESET_INFO[preset] && (
                        <div className="mt-4 rounded-xl border border-indigo-100 bg-gradient-to-r from-indigo-50/50 to-purple-50/50 p-4">
                            <div className="flex items-start gap-3">
                                <div className="text-3xl">{PRESET_INFO[preset].emoji}</div>
                                <div className="flex-1">
                                    <div className="flex items-center gap-2 mb-1">
                                        <span className="text-sm font-bold text-gray-800">{PRESET_INFO[preset].label}</span>
                                        <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium">
                                            위험도 {PRESET_INFO[preset].risk}/5
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-600 leading-relaxed mb-3">{PRESET_INFO[preset].detail}</p>
                                    <div className="space-y-1.5">
                                        {Object.entries(PRESET_INFO[preset].weights).map(([cat, w]) => (
                                            <div key={cat} className="flex items-center gap-2">
                                                <span className="text-xs text-gray-500 w-16 text-right shrink-0">
                                                    {CATEGORY_WEIGHT_LABELS[cat] ?? cat}
                                                </span>
                                                <div className="flex-1 h-3 bg-gray-100 rounded-full overflow-hidden">
                                                    <div
                                                        className={`h-full rounded-full transition-all ${
                                                            cat === 'equity' ? 'bg-blue-400'
                                                            : cat === 'alternative' ? 'bg-amber-400'
                                                            : cat === 'foreign_bond' ? 'bg-emerald-400'
                                                            : cat === 'domestic_bond' ? 'bg-teal-400'
                                                            : 'bg-gray-400'
                                                        }`}
                                                        style={{ width: `${w * 100}%` }}
                                                    />
                                                </div>
                                                <span className="text-xs font-bold text-gray-700 w-10 text-right">
                                                    {(w * 100).toFixed(0)}%
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Asset Class Registry Reference */}
                <AssetClassReferencePanel />

                {/* Parameters */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <DollarSign className="w-4 h-4 text-gray-400" />
                            초기 자본금
                        </label>
                        <div className="relative">
                            <input
                                type="number"
                                className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                value={capital} onChange={e => setCapital(Number(e.target.value))} disabled={isRunning}
                            />
                            <span className="absolute right-4 top-3 text-gray-400 text-sm">원</span>
                        </div>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <Activity className="w-4 h-4 text-gray-400" />
                            백테스트 기간
                        </label>
                        <div className="flex gap-2">
                            {!fullPeriod && (
                                <div className="relative flex-1">
                                    <input
                                        type="number" min={3} max={120}
                                        className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                        value={months} onChange={e => setMonths(Number(e.target.value))} disabled={isRunning}
                                    />
                                    <span className="absolute right-3 top-3 text-gray-400 text-sm">개월</span>
                                </div>
                            )}
                            <button
                                className={`px-3 py-2.5 rounded-lg text-xs font-bold transition-all border ${fullPeriod
                                    ? 'bg-indigo-100 text-indigo-800 border-indigo-300 ring-1 ring-indigo-200'
                                    : 'bg-white text-gray-600 border-gray-200 hover:border-indigo-300'}`}
                                onClick={() => setFullPeriod(!fullPeriod)} disabled={isRunning}
                            >
                                {fullPeriod ? '전체' : 'ALL'}
                            </button>
                        </div>
                    </div>
                    <div className="flex items-end">
                        <div className="flex gap-3 w-full">
                            <button
                                className="flex-1 flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 transition-all shadow-lg shadow-indigo-200 active:scale-[0.98]"
                                onClick={startBacktest} disabled={isRunning}
                            >
                                <Play className="w-4 h-4" />
                                글로벌 백테스트 시작
                            </button>
                            <button
                                className="flex items-center justify-center px-4 py-2.5 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 transition-all active:scale-[0.98]"
                                onClick={stopBacktest} disabled={!isRunning}
                            >
                                <Square className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* ──────── Results ──────── */}
            {result && (
                <>
                    {/* Preset Header */}
                    <div className="bg-gradient-to-r from-indigo-50 to-purple-50 rounded-xl border border-indigo-100 p-5 flex items-center gap-4">
                        <div className="text-4xl">{presetMeta.emoji}</div>
                        <div>
                            <h3 className="text-lg font-bold text-gray-800">
                                {result.config.preset_label ?? presetMeta.label}
                                <span className="ml-2 text-sm font-normal text-gray-500">
                                    (위험도 {result.config.risk_level ?? presetMeta.risk}/5)
                                </span>
                            </h3>
                            <p className="text-xs text-gray-500 mt-0.5 mb-2">{presetMeta.detail}</p>
                            <div className="flex flex-wrap gap-2">
                                {Object.entries(result.config.strategic_weights ?? {}).map(([cls, w]) => (
                                    <span key={cls} className="text-xs bg-white px-2 py-0.5 rounded border border-indigo-100 text-gray-600">
                                        {CATEGORY_WEIGHT_LABELS[cls] ?? cls}: <span className="font-bold">{w}</span>
                                    </span>
                                ))}
                            </div>
                        </div>
                    </div>

                    {/* Stat Cards */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <StatCard label="총 수익률" value={formatPct(metrics.total_return)}
                            color={(metrics.total_return ?? 0) >= 0 ? 'emerald' : 'red'}
                            icon={<TrendingUp className="w-5 h-5" />} />
                        <StatCard label="CAGR" value={formatPct(metrics.cagr)}
                            color={(metrics.cagr ?? 0) >= 0 ? 'emerald' : 'red'}
                            icon={<Percent className="w-5 h-5" />} />
                        <StatCard label="MDD" value={formatPct(metrics.mdd)} color="red"
                            icon={<Activity className="w-5 h-5" />} />
                        <StatCard label="Sharpe Ratio" value={formatRatio(metrics.sharpe_ratio)}
                            color={(metrics.sharpe_ratio ?? 0) >= 1 ? 'emerald' : 'amber'}
                            icon={<BarChart3 className="w-5 h-5" />} />
                    </div>

                    {/* Benchmark Comparison */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
                            <div className="text-xs text-gray-500 mb-2">벤치마크 (60/40)</div>
                            <div className="flex items-baseline gap-2">
                                <span className="text-lg font-bold text-gray-600">
                                    CAGR {formatPct(metrics.benchmark_cagr)}
                                </span>
                                <span className="text-sm text-gray-400">
                                    MDD {formatPct(metrics.benchmark_mdd)}
                                </span>
                            </div>
                        </div>
                        <div className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
                            <div className="text-xs text-gray-500 mb-2">초과 수익 (Alpha)</div>
                            <div className={`text-lg font-bold ${((metrics.cagr ?? 0) - (metrics.benchmark_cagr ?? 0)) >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>
                                {((metrics.cagr ?? 0) - (metrics.benchmark_cagr ?? 0)) >= 0 ? '+' : ''}
                                {(((metrics.cagr ?? 0) - (metrics.benchmark_cagr ?? 0)) * 100).toFixed(2)}%p
                            </div>
                        </div>
                        <div className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
                            <div className="text-xs text-gray-500 mb-2">최종 자산</div>
                            <div className="text-lg font-bold text-gray-800">
                                {metrics.final_equity ? formatKRW(metrics.final_equity) + '원' : '-'}
                            </div>
                        </div>
                    </div>

                    {/* Detailed Metrics */}
                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                        <h3 className="text-lg font-bold text-gray-800 mb-4 flex items-center gap-2">
                            <BarChart3 className="w-5 h-5 text-indigo-500" />
                            상세 성과 지표
                        </h3>
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                            <MetricItem label="Sortino Ratio" value={formatRatio(metrics.sortino_ratio)} />
                            <MetricItem label="Calmar Ratio" value={formatRatio(metrics.calmar_ratio)} />
                            <MetricItem label="Profit Factor" value={formatRatio(metrics.profit_factor)} />
                            <MetricItem label="일간 승률" value={formatPct(metrics.daily_win_rate)} />
                            <MetricItem label="월간 승률" value={formatPct(metrics.monthly_win_rate)} />
                            <MetricItem label="연환산 변동성" value={formatPct(metrics.annualized_volatility)} />
                            <MetricItem label="MDD 지속" value={metrics.mdd_duration_days !== undefined ? `${metrics.mdd_duration_days}일` : '-'} />
                            <MetricItem label="Best Day" value={formatPct(metrics.best_day)} />
                            <MetricItem label="Worst Day" value={formatPct(metrics.worst_day)} />
                            <MetricItem label="거래 횟수" value={metrics.total_trades !== undefined ? `${metrics.total_trades}회` : '-'} />
                        </div>

                        {/* 거래 & 국면 */}
                        <div className="mt-6 pt-4 border-t border-gray-100 grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <h4 className="text-sm font-bold text-gray-700 mb-3">거래 요약</h4>
                                <div className="flex flex-wrap gap-2">
                                    {Object.entries(result.trade_summary).map(([action, cnt]) => (
                                        <span key={action} className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-gray-50 text-sm border border-gray-100">
                                            <span className="font-bold text-gray-800">{action}</span>
                                            <span className="text-gray-500">{cnt}회</span>
                                        </span>
                                    ))}
                                    {metrics.total_friction !== undefined && (
                                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-50 text-sm border border-red-100">
                                            <span className="font-bold text-red-700">마찰비용</span>
                                            <span className="text-red-600">{formatKRW(metrics.total_friction as number)}원</span>
                                        </span>
                                    )}
                                </div>
                            </div>
                            <div>
                                <h4 className="text-sm font-bold text-gray-700 mb-3">리밸런싱 이력</h4>
                                <div className="text-xs text-gray-500">
                                    총 {result.global_allocation?.length ?? 0}회 리밸런싱
                                </div>
                            </div>
                        </div>

                        <div className="mt-4 pt-4 border-t border-gray-100">
                            <div className="flex items-center justify-between text-xs text-gray-400">
                                <span>
                                    {result.config.preset_label} |
                                    초기자본 {formatKRW(result.config.initial_capital)}원
                                </span>
                                <span>{result.timestamp} | {result.elapsed_sec}초 소요</span>
                            </div>
                        </div>
                    </div>

                    {/* ETF Regime Badges */}
                    {result.regime_by_class && Object.keys(result.regime_by_class).length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                            <h3 className="text-sm font-bold text-gray-700 mb-2 flex items-center gap-2">
                                <Shield className="w-4 h-4 text-indigo-500" />
                                자산별 국면 현황 (최근)
                            </h3>
                            <p className="text-xs text-gray-400 mb-4">SMA200 기준 — 현재가 {'>'} SMA200이면 BULL, 아니면 BEAR. BEAR 자산의 비중은 안전자산으로 이전됩니다.</p>
                            <div className="flex flex-wrap gap-2">
                                {Object.entries(result.regime_by_class).map(([ticker, regime]) => (
                                    <span
                                        key={ticker}
                                        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold ${regime === 'BULL'
                                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                            : 'bg-red-50 text-red-700 border border-red-200'}`}
                                        title={TICKER_LABEL_MAP[ticker] ?? ticker}
                                    >
                                        <span className={`w-1.5 h-1.5 rounded-full ${regime === 'BULL' ? 'bg-emerald-500' : 'bg-red-500'}`} />
                                        {ticker}
                                        <span className="font-normal opacity-70">{TICKER_LABEL_MAP[ticker] ? `(${TICKER_LABEL_MAP[ticker]})` : ''}</span>
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Asset Allocation Bar Chart */}
                    {allocationData.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                            <div className="px-6 py-4 border-b border-gray-100 flex items-center gap-2">
                                <Target className="w-5 h-5 text-indigo-500" />
                                <h3 className="text-lg font-bold text-gray-800">자산 배분 현황 (최근 리밸런싱)</h3>
                            </div>
                            <div className="px-6 pb-6 pt-4">
                                <ResponsiveContainer width="100%" height={280}>
                                    <BarChart data={allocationData} layout="vertical">
                                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                        <XAxis type="number" tick={{ fontSize: 11, fill: '#94a3b8' }}
                                            tickFormatter={(v: number) => v.toFixed(1) + '%'} />
                                        <YAxis type="category" dataKey="ticker" tick={{ fontSize: 12, fill: '#4b5563', fontWeight: 600 }} width={100}
                                            tickFormatter={(ticker: string) => `${ticker} ${TICKER_LABEL_MAP[ticker] ?? ''}`} />
                                        <Tooltip
                                            formatter={(val: unknown) => [Number(val).toFixed(1) + '%', '비중']}
                                            labelFormatter={(ticker: unknown) => `${ticker} — ${TICKER_LABEL_MAP[String(ticker)] ?? ''}`}
                                            contentStyle={{ borderRadius: '8px', border: '1px solid #e2e8f0', fontSize: 12 }}
                                        />
                                        <Bar dataKey="weight" fill="#6366f1" radius={[0, 4, 4, 0]} barSize={20} />
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        </div>
                    )}

                    {/* Equity Curve with Benchmark Overlay */}
                    {chartData.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                            <button
                                className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                                onClick={() => setShowChart(!showChart)}
                            >
                                <div className="flex items-center gap-2">
                                    <Activity className="w-5 h-5 text-indigo-500" />
                                    <span className="text-lg font-bold text-gray-800">자산 가치 곡선 vs 벤치마크 (60/40)</span>
                                </div>
                                {showChart ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                            </button>
                            {showChart && (
                                <div className="px-6 pb-6">
                                    <ResponsiveContainer width="100%" height={320}>
                                        <AreaChart data={chartData}>
                                            <defs>
                                                <linearGradient id="globalEquityGrad" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                                                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                                                </linearGradient>
                                                <linearGradient id="benchmarkGrad" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="5%" stopColor="#94a3b8" stopOpacity={0.15} />
                                                    <stop offset="95%" stopColor="#94a3b8" stopOpacity={0} />
                                                </linearGradient>
                                            </defs>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                            <XAxis dataKey="displayDate" tick={{ fontSize: 11, fill: '#94a3b8' }}
                                                interval={Math.max(1, Math.floor(chartData.length / 10))} />
                                            <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }}
                                                tickFormatter={(v: number) => formatKRW(v)} width={72} />
                                            <Tooltip
                                                formatter={(val: unknown, name?: string) => [
                                                    formatKRW(Number(val)) + '원',
                                                    name === 'value' ? '전략' : '벤치마크'
                                                ]}
                                                labelFormatter={(label: unknown) => `날짜: ${label}`}
                                                contentStyle={{ borderRadius: '8px', border: '1px solid #e2e8f0', fontSize: 12 }}
                                            />
                                            <Legend formatter={(value: string) => value === 'value' ? '전략' : '벤치마크 (60/40)'} />
                                            <ReferenceLine y={result.config.initial_capital} stroke="#94a3b8" strokeDasharray="5 5"
                                                label={{ value: '초기자본', position: 'left', fill: '#94a3b8', fontSize: 10 }} />
                                            <Area type="monotone" dataKey="benchmark" stroke="#94a3b8" strokeWidth={1.5}
                                                fill="url(#benchmarkGrad)" strokeDasharray="4 2" />
                                            <Area type="monotone" dataKey="value" stroke="#6366f1" strokeWidth={2}
                                                fill="url(#globalEquityGrad)" />
                                        </AreaChart>
                                    </ResponsiveContainer>

                                    {/* Drawdown */}
                                    <div className="mt-4 pt-4 border-t border-gray-100">
                                        <h4 className="text-sm font-bold text-gray-600 mb-2">낙폭 (Drawdown)</h4>
                                        <ResponsiveContainer width="100%" height={140}>
                                            <AreaChart data={drawdownData}>
                                                <defs>
                                                    <linearGradient id="globalDdGrad" x1="0" y1="0" x2="0" y2="1">
                                                        <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                                                        <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                                                    </linearGradient>
                                                </defs>
                                                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                                <XAxis dataKey="displayDate" tick={{ fontSize: 10, fill: '#94a3b8' }}
                                                    interval={Math.max(1, Math.floor(drawdownData.length / 10))} />
                                                <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }}
                                                    tickFormatter={(v: number) => v.toFixed(1) + '%'} width={52} />
                                                <Tooltip
                                                    formatter={(val: unknown, name?: string) => [
                                                        Number(val).toFixed(2) + '%',
                                                        name === 'drawdown' ? '전략 낙폭' : '벤치마크 낙폭'
                                                    ]}
                                                    labelFormatter={(label: unknown) => `날짜: ${label}`}
                                                    contentStyle={{ borderRadius: '8px', border: '1px solid #fecaca', fontSize: 12 }}
                                                />
                                                <ReferenceLine y={0} stroke="#94a3b8" />
                                                <Area type="monotone" dataKey="bmDrawdown" stroke="#94a3b8" strokeWidth={1}
                                                    fill="none" strokeDasharray="3 3" />
                                                <Area type="monotone" dataKey="drawdown" stroke="#ef4444" strokeWidth={1.5}
                                                    fill="url(#globalDdGrad)" />
                                            </AreaChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Allocation History Table */}
                    {result.global_allocation && result.global_allocation.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                            <button
                                className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                                onClick={() => setShowAllocation(!showAllocation)}
                            >
                                <div className="flex items-center gap-2">
                                    <BarChart3 className="w-5 h-5 text-indigo-500" />
                                    <span className="text-sm font-bold text-gray-700">
                                        리밸런싱 이력 ({result.global_allocation.length}회)
                                    </span>
                                </div>
                                {showAllocation ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                            </button>
                            {showAllocation && (
                                <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                                    <table className="w-full text-xs">
                                        <thead className="sticky top-0 bg-gray-50">
                                            <tr className="border-b border-gray-200">
                                                <th className="px-3 py-2 text-left font-semibold text-gray-500">#</th>
                                                <th className="px-3 py-2 text-left font-semibold text-gray-500">날짜</th>
                                                <th className="px-3 py-2 text-center font-semibold text-gray-500">BULL</th>
                                                <th className="px-3 py-2 text-center font-semibold text-gray-500">BEAR</th>
                                                <th className="px-3 py-2 text-left font-semibold text-gray-500">배분 (상위 5)</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {result.global_allocation.map((alloc, i) => {
                                                const top5 = Object.entries(alloc.weights)
                                                    .sort(([, a], [, b]) => b - a)
                                                    .slice(0, 5);
                                                return (
                                                    <tr key={i} className="border-b border-gray-50 hover:bg-indigo-50/20">
                                                        <td className="px-3 py-2 font-mono text-gray-400">{i + 1}</td>
                                                        <td className="px-3 py-2 font-mono text-gray-700">{alloc.date}</td>
                                                        <td className="px-3 py-2 text-center">
                                                            <span className="inline-flex items-center px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 font-bold">
                                                                {alloc.bull_count}
                                                            </span>
                                                        </td>
                                                        <td className="px-3 py-2 text-center">
                                                            <span className="inline-flex items-center px-2 py-0.5 rounded bg-red-50 text-red-700 font-bold">
                                                                {alloc.bear_count}
                                                            </span>
                                                        </td>
                                                        <td className="px-3 py-2">
                                                            <div className="flex flex-wrap gap-1">
                                                                {top5.map(([ticker, w]) => (
                                                                    <span key={ticker} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-gray-100 text-gray-700"
                                                                        title={TICKER_LABEL_MAP[ticker] ?? ticker}>
                                                                        <span className="font-bold">{ticker}</span>
                                                                        <span className="text-gray-300 text-[10px]">{TICKER_LABEL_MAP[ticker] ?? ''}</span>
                                                                        <span className="text-gray-400">{(w * 100).toFixed(1)}%</span>
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}
                </>
            )}

            {/* Log Viewer */}
            <LogViewer status={status} label="Global Backtest" />
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Main Panel (Dual Tab)
// ═══════════════════════════════════════════════════

export default function MomentumPanel() {
    const [activeMode, setActiveMode] = useState<'screener' | 'backtest' | 'global'>('screener');

    return (
        <div className="space-y-6">
            {/* Mode Toggle */}
            <div className="flex gap-2 bg-white rounded-xl shadow-sm border border-gray-100 p-2">
                <button
                    className={`flex-1 flex items-center justify-center gap-2 px-6 py-3 rounded-lg font-bold text-sm transition-all ${activeMode === 'screener'
                        ? 'bg-amber-100 text-amber-800 shadow-sm'
                        : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
                    onClick={() => setActiveMode('screener')}
                >
                    <Search className="w-4 h-4" />
                    모멘텀 스크리너
                </button>
                <button
                    className={`flex-1 flex items-center justify-center gap-2 px-6 py-3 rounded-lg font-bold text-sm transition-all ${activeMode === 'backtest'
                        ? 'bg-amber-100 text-amber-800 shadow-sm'
                        : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
                    onClick={() => setActiveMode('backtest')}
                >
                    <TrendingUp className="w-4 h-4" />
                    모멘텀 백테스트
                </button>
                <button
                    className={`flex-1 flex items-center justify-center gap-2 px-6 py-3 rounded-lg font-bold text-sm transition-all ${activeMode === 'global'
                        ? 'bg-indigo-100 text-indigo-800 shadow-sm'
                        : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
                    onClick={() => setActiveMode('global')}
                >
                    <Globe className="w-4 h-4" />
                    글로벌 멀티에셋
                </button>
            </div>

            {/* Tab Content */}
            {activeMode === 'screener' && <ScreenerTab />}
            {activeMode === 'backtest' && <BacktestTab />}
            {activeMode === 'global' && <GlobalBacktestTab />}
        </div>
    );
}

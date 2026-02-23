import { useState, useEffect, useRef } from 'react';
import { Play, Square, Terminal, Filter, RefreshCw, ChevronDown, ChevronUp, CheckCircle2, XCircle, Clock, X, Target, TrendingUp, TrendingDown, Shield, BarChart3 } from 'lucide-react';
import axios from 'axios';

const API = 'http://localhost:8001/api/pipeline';

interface ScreenedStock {
    stk_cd: string;
    stk_nm: string;
    theme_nm: string;
    close: number;
    daily_return: number;
    sma10: number;
    ema20: number;
    sma20: number;
    disparity20: number;
    adtv20: number;   // 억 단위
    rvol: number;
    atr5: number;
    market_cap: number;  // 억 단위
    // 풀백 전용 필드
    vcr?: number;
    frl?: number;
    surge_return?: number;
    surge_rvol?: number;
    disparity_5?: number;
}

interface FilterResult {
    stk_cd: string;
    stk_nm: string;
    theme_nm: string;
    passed: boolean;
    reason: string;
    close: number;
    daily_return: number;
}

interface ScreenerData {
    timestamp: string;
    total_themes: number;
    total_candidates: number;
    total_passed: number;
    passed_stocks: ScreenedStock[];
    all_filter_results: FilterResult[];
}

interface PipelineStatus {
    name: string;
    status: 'idle' | 'running' | 'finished';
    pid?: number;
    exitCode?: number;
    logs: string[];
}

export default function ScreenerPanel() {
    const [status, setStatus] = useState<PipelineStatus>({ name: 'alpha-screener', status: 'idle', logs: [] });
    const [data, setData] = useState<ScreenerData | null>(null);
    const [topN, setTopN] = useState(30);
    const [strategy, setStrategy] = useState<'swing' | 'pullback'>('swing');
    const [showLogs, setShowLogs] = useState(false);
    const [showRejected, setShowRejected] = useState(false);
    const [sortField, setSortField] = useState<'daily_return' | 'rvol' | 'disparity20' | 'adtv20'>('daily_return');
    const [sortAsc, setSortAsc] = useState(false);
    const logRef = useRef<HTMLDivElement>(null);
    const [selectedStock, setSelectedStock] = useState<ScreenedStock | null>(null);

    // 전략 변경 시 선택 해제
    useEffect(() => { setSelectedStock(null); }, [strategy]);

    // 상태 폴링
    useEffect(() => {
        const fetchStatus = () => {
            axios.get(`${API}/status/alpha-screener`)
                .then(r => setStatus(r.data))
                .catch(() => { });
        };
        fetchStatus();
        const id = setInterval(fetchStatus, 3000);
        return () => clearInterval(id);
    }, []);

    // 결과 자동 로드 (완료 시)
    useEffect(() => {
        if (status.status === 'finished' && status.exitCode === 0) {
            loadResults();
        }
    }, [status.status]);

    // 초기 로드
    useEffect(() => { loadResults(); }, []);

    // 로그 자동 스크롤
    useEffect(() => {
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [status.logs, showLogs]);

    const loadResults = () => {
        axios.get(`${API}/screener/result`)
            .then(r => {
                if (r.data.status === 'ok' && r.data.data) {
                    setData(r.data.data);
                }
            })
            .catch(() => { });
    };

    const startScreener = () => {
        axios.post(`${API}/screener`, { top_n: topN, strategy })
            .catch(err => alert('실행 실패: ' + err.message));
    };

    const stopScreener = () => {
        axios.post(`${API}/stop`, { name: 'alpha-screener' })
            .catch(() => { });
    };

    const isRunning = status.status === 'running';

    const sortedStocks = data?.passed_stocks
        ? [...data.passed_stocks].sort((a, b) => {
            const diff = (a[sortField] || 0) - (b[sortField] || 0);
            return sortAsc ? diff : -diff;
        })
        : [];

    const handleSort = (field: typeof sortField) => {
        if (sortField === field) {
            setSortAsc(!sortAsc);
        } else {
            setSortField(field);
            setSortAsc(false);
        }
    };

    const SortIcon = ({ field }: { field: typeof sortField }) => {
        if (sortField !== field) return null;
        return sortAsc ? <ChevronUp className="w-3 h-3 inline" /> : <ChevronDown className="w-3 h-3 inline" />;
    };

    const rejectedStocks = data?.all_filter_results?.filter(r => !r.passed) || [];

    const getStrategyInfo = (stk: ScreenedStock) => {
        const close = stk.close;
        const atr = stk.atr5 || close * 0.02;
        const capital = 10_000_000;
        const riskAmt = capital * 0.015;
        const slotCap = capital / 10;

        if (strategy === 'pullback') {
            const entryEst = Math.round(close * 1.001725);
            const target = Math.round(entryEst + atr * 1.5);
            const stop = Math.round(entryEst - atr * 1.2);
            const breakeven = Math.round(entryEst * 1.00345);
            const stopDist = atr * 2.5;
            const rawShares = stopDist > 0 ? Math.floor(riskAmt / stopDist) : 0;
            const shares = Math.floor(Math.min(rawShares * entryEst, slotCap) / entryEst);
            return {
                entryEst, target, stop, breakeven,
                targetPct: ((target / entryEst) - 1) * 100,
                stopPct: ((stop / entryEst) - 1) * 100,
                riskAmt, stopDist, shares,
                amount: shares * entryEst,
                rr: 1.25, atr,
            };
        }
        const entryEst = close;
        const stop = Math.round(entryEst - atr * 2.5);
        const stopDist = atr * 2.5;
        const rawShares = stopDist > 0 ? Math.floor(riskAmt / stopDist) : 0;
        const shares = Math.floor(Math.min(rawShares * entryEst, slotCap) / entryEst);
        return {
            entryEst, target: 0, stop, breakeven: 0,
            targetPct: 0,
            stopPct: ((stop / entryEst) - 1) * 100,
            riskAmt, stopDist, shares,
            amount: shares * entryEst,
            rr: 0, atr,
        };
    };

    return (
        <div className="space-y-6">
            {/* Controls */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-emerald-100 flex items-center justify-center">
                            <Filter className="w-6 h-6 text-emerald-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">
                                {strategy === 'pullback' ? '스윙-풀백 스크리너' : '알파 필터 스크리너'}
                            </h2>
                            <p className="text-sm text-gray-500">
                                {strategy === 'pullback'
                                    ? '급등(Surge) → VCR(거래량감소) → FRL(피보나치) → 이격도 | 슬리피지·거리기반 매도 반영'
                                    : '유동성 → RVOL → 모멘텀 → 이격도 4단계 필터링'}
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {isRunning && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700 animate-pulse">
                                <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
                                스크리닝 중
                            </span>
                        )}
                        {status.status === 'finished' && status.exitCode === 0 && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700">
                                완료
                            </span>
                        )}
                    </div>
                </div>

                {/* Filter Criteria Summary */}
                <div className="mb-6 bg-gray-50 rounded-xl border border-gray-100 p-4">
                    {strategy === 'pullback' ? (
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">① 유동성/급등</div>
                                <div className="font-bold text-gray-800 mt-0.5">RVOL≥3, 수익률≥10%</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">② 기간</div>
                                <div className="font-bold text-gray-800 mt-0.5">최근 5일 이내 급등</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">③ VCR(거래량)</div>
                                <div className="font-bold text-gray-800 mt-0.5">당일/급등일 ≤ 35%</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">④ FRL(피보나치)</div>
                                <div className="font-bold text-gray-800 mt-0.5">0.382 ~ 0.618</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">⑤ Disparity</div>
                                <div className="font-bold text-gray-800 mt-0.5">-2% ~ +2% (5일 EMA)</div>
                            </div>
                        </div>
                    ) : (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">① 유동성</div>
                                <div className="font-bold text-gray-800 mt-0.5">ADTV ≥ 50억, 시총 ≥ 3000억</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">② RVOL</div>
                                <div className="font-bold text-gray-800 mt-0.5">당일 거래대금 / 20일 ADTV ≥ 1.5</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">③ 모멘텀</div>
                                <div className="font-bold text-gray-800 mt-0.5">종가 {'>'} SMA10 & EMA20, 수익률 ≥ 4%</div>
                            </div>
                            <div className="bg-white rounded-lg p-2.5 border border-gray-100">
                                <div className="text-gray-500">④ 이격도</div>
                                <div className="font-bold text-gray-800 mt-0.5">100 {'<'} (종가/SMA20×100) ≤ 120</div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Controls Row */}
                <div className="flex flex-wrap items-end gap-4">
                    <div className="flex-shrink-0">
                        <label className="block text-sm font-medium text-gray-700 mb-1">전략</label>
                        <select
                            className="bg-white border border-gray-200 rounded-lg px-3 py-2.5 focus:ring-2 focus:ring-emerald-500 outline-none text-sm"
                            value={strategy}
                            onChange={e => setStrategy(e.target.value as any)}
                            disabled={isRunning}
                        >
                            <option value="swing">스윙-상따</option>
                            <option value="pullback">스윙-풀백</option>
                        </select>
                    </div>
                    <div className="flex-shrink-0">
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                            {strategy === 'pullback' ? '거래량 Top-N' : '상위 테마 수'}
                        </label>
                        <input
                            type="number"
                            min="5"
                            max={strategy === 'pullback' ? 500 : 100}
                            className="w-24 border border-gray-200 rounded-lg px-3 py-2.5 focus:ring-2 focus:ring-emerald-500 outline-none"
                            value={topN}
                            onChange={e => setTopN(Number(e.target.value))}
                            disabled={isRunning}
                        />
                    </div>
                    <button
                        className="flex items-center gap-2 px-6 py-2.5 rounded-xl font-bold text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 transition-all shadow-lg shadow-emerald-200 active:scale-[0.98]"
                        onClick={startScreener}
                        disabled={isRunning}
                    >
                        <Play className="w-5 h-5" /> 스크리닝 시작
                    </button>
                    <button
                        className="flex items-center gap-2 px-4 py-2.5 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 transition-all active:scale-[0.98]"
                        onClick={stopScreener}
                        disabled={!isRunning}
                    >
                        <Square className="w-4 h-4" /> 중단
                    </button>
                    <button
                        className="flex items-center gap-2 px-4 py-2.5 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 transition-all active:scale-[0.98]"
                        onClick={loadResults}
                    >
                        <RefreshCw className="w-4 h-4" /> 새로고침
                    </button>
                </div>
            </div>

            {/* Summary Stats */}
            {data && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 text-center">
                        <div className="text-2xl font-bold text-emerald-600">{data.total_passed}</div>
                        <div className="text-sm text-gray-500 mt-1">통과 종목</div>
                    </div>
                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 text-center">
                        <div className="text-2xl font-bold text-gray-800">{data.total_candidates}</div>
                        <div className="text-sm text-gray-500 mt-1">전체 후보</div>
                    </div>
                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 text-center">
                        <div className="text-2xl font-bold text-blue-600">
                            {strategy === 'pullback' ? data.total_candidates : data.total_themes}
                        </div>
                        <div className="text-sm text-gray-500 mt-1">
                            {strategy === 'pullback' ? '유니버스(Vol)' : '조회 테마'}
                        </div>
                    </div>
                    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 text-center">
                        <div className="text-2xl font-bold text-gray-400">
                            {data.total_candidates > 0
                                ? ((data.total_passed / data.total_candidates) * 100).toFixed(1)
                                : 0}%
                        </div>
                        <div className="text-sm text-gray-500 mt-1">통과율</div>
                    </div>
                </div>
            )}

            {/* Results Table */}
            {data && data.passed_stocks.length > 0 && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                    <div className="p-4 border-b border-gray-100 bg-emerald-50/50 flex items-center justify-between">
                        <h3 className="text-sm font-bold text-emerald-800 flex items-center gap-2">
                            <CheckCircle2 className="w-4 h-4" />
                            필터 통과 종목 ({data.total_passed}건)
                        </h3>
                        <div className="text-xs text-gray-500">
                            <Clock className="w-3 h-3 inline mr-1" />
                            {new Date(data.timestamp).toLocaleString('ko-KR')}
                        </div>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-gray-50 border-b text-xs text-gray-500 uppercase">
                                    <th className="px-3 py-2 text-left">#</th>
                                    <th className="px-3 py-2 text-left">종목</th>
                                    {strategy !== 'pullback' && <th className="px-3 py-2 text-left">테마</th>}
                                    <th className="px-3 py-2 text-right">종가</th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('daily_return')}>
                                        {strategy === 'pullback' ? '급등 수익률' : '수익률'} <SortIcon field="daily_return" />
                                    </th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('rvol')}>
                                        {strategy === 'pullback' ? 'Surge RVOL' : 'RVOL'} <SortIcon field="rvol" />
                                    </th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('disparity20')}>
                                        {strategy === 'pullback' ? 'Disp(5EMA)' : '이격도'} <SortIcon field="disparity20" />
                                    </th>
                                    {strategy !== 'pullback' && <th className="px-3 py-2 text-right">SMA10</th>}
                                    {strategy !== 'pullback' && <th className="px-3 py-2 text-right">EMA20</th>}
                                    {strategy === 'pullback' && <th className="px-3 py-2 text-right">VCR</th>}
                                    {strategy === 'pullback' && <th className="px-3 py-2 text-right">FRL</th>}
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('adtv20')}>
                                        ADTV(억) <SortIcon field="adtv20" />
                                    </th>
                                    <th className="px-3 py-2 text-right">ATR({strategy === 'pullback' ? '14' : '5'})</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-50">
                                {sortedStocks.map((stk, i) => (
                                    <tr key={stk.stk_cd}
                                        className={`hover:bg-emerald-50/30 transition-colors cursor-pointer ${selectedStock?.stk_cd === stk.stk_cd ? 'bg-emerald-100/60 ring-1 ring-inset ring-emerald-300' : ''}`}
                                        onClick={() => setSelectedStock(selectedStock?.stk_cd === stk.stk_cd ? null : stk)}
                                    >
                                        <td className="px-3 py-2.5 text-gray-400 text-xs">{i + 1}</td>
                                        <td className="px-3 py-2.5">
                                            <div className="font-medium text-gray-800">{stk.stk_nm}</div>
                                            <div className="text-[10px] text-gray-400">{stk.stk_cd}</div>
                                        </td>
                                        {strategy !== 'pullback' && (
                                            <td className="px-3 py-2.5">
                                                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-100">
                                                    {stk.theme_nm}
                                                </span>
                                            </td>
                                        )}
                                        <td className="px-3 py-2.5 text-right font-mono text-gray-700">
                                            {stk.close.toLocaleString()}
                                        </td>
                                        <td className="px-3 py-2.5 text-right font-bold">
                                            <span className={stk.daily_return >= 0 ? 'text-red-600' : 'text-blue-600'}>
                                                {stk.daily_return >= 0 ? '+' : ''}{stk.daily_return}%
                                            </span>
                                        </td>
                                        <td className="px-3 py-2.5 text-right">
                                            <span className={`font-bold ${stk.rvol >= 3 ? 'text-orange-600' : 'text-gray-700'}`}>
                                                {stk.rvol.toFixed(1)}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2.5 text-right font-mono text-gray-700">
                                            {stk.disparity20.toFixed(1)}
                                        </td>
                                        {strategy !== 'pullback' && (
                                            <td className="px-3 py-2.5 text-right font-mono text-gray-500 text-xs">
                                                {stk.sma10.toLocaleString()}
                                            </td>
                                        )}
                                        {strategy !== 'pullback' && (
                                            <td className="px-3 py-2.5 text-right font-mono text-gray-500 text-xs">
                                                {stk.ema20.toLocaleString()}
                                            </td>
                                        )}
                                        {strategy === 'pullback' && (
                                            <td className={`px-3 py-2.5 text-right font-mono text-xs font-bold ${(stk.vcr ?? stk.sma20) <= 0.35 ? 'text-emerald-600' : 'text-orange-600'}`}>
                                                {(stk.vcr ?? stk.sma20).toFixed(2)}
                                            </td>
                                        )}
                                        {strategy === 'pullback' && (
                                            <td className={`px-3 py-2.5 text-right font-mono text-xs font-bold ${(stk.frl ?? stk.market_cap) >= 0.382 && (stk.frl ?? stk.market_cap) <= 0.618 ? 'text-emerald-600' : 'text-orange-600'}`}>
                                                {(stk.frl ?? stk.market_cap).toFixed(3)}
                                            </td>
                                        )}
                                        <td className="px-3 py-2.5 text-right font-mono text-gray-500 text-xs">
                                            {stk.adtv20.toLocaleString()}
                                        </td>
                                        <td className="px-3 py-2.5 text-right font-mono text-gray-500 text-xs">
                                            {stk.atr5.toLocaleString()}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Strategy Explanation Panel */}
            {selectedStock && (() => {
                const s = getStrategyInfo(selectedStock);
                const stk = selectedStock;
                if (strategy === 'pullback') {
                    return (
                        <div className="bg-white rounded-xl shadow-sm border-2 border-emerald-200 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
                            <div className="px-5 py-4 bg-gradient-to-r from-emerald-50 to-blue-50 border-b border-emerald-100 flex items-center justify-between">
                                <div>
                                    <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                                        <Target className="w-5 h-5 text-emerald-600" />
                                        {stk.stk_nm} <span className="text-sm font-normal text-gray-400">({stk.stk_cd})</span>
                                    </h3>
                                    <p className="text-xs text-gray-500 mt-0.5">풀백 백테스트 기준 매수/매도 전략 · 종가 {stk.close.toLocaleString()}원</p>
                                </div>
                                <button onClick={() => setSelectedStock(null)} className="p-1.5 rounded-lg hover:bg-gray-200 transition-colors">
                                    <X className="w-5 h-5 text-gray-400" />
                                </button>
                            </div>

                            <div className="p-5 grid grid-cols-1 md:grid-cols-3 gap-5">
                                {/* 매수 전략 */}
                                <div className="rounded-xl border border-blue-100 bg-blue-50/30 p-4">
                                    <h4 className="text-sm font-bold text-blue-800 flex items-center gap-1.5 mb-3">
                                        <TrendingUp className="w-4 h-4" /> 매수 전략
                                    </h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-gray-500">매수 시점</span><span className="font-semibold text-gray-800">필터 통과 익일 시가</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">갭하락 방어</span><span className="font-semibold text-red-600">시가/전종가 {'<'} 98% → 포기</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">슬리피지</span><span className="font-semibold text-gray-800">+10bp (0.1%)</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">마찰비용(편도)</span><span className="font-semibold text-gray-800">0.1725%</span></div>
                                        <hr className="border-blue-100" />
                                        <div className="flex justify-between"><span className="text-gray-500">예상 매수가</span><span className="font-bold text-blue-700">{s.entryEst.toLocaleString()}원</span></div>
                                    </div>
                                </div>

                                {/* 매도 전략 */}
                                <div className="rounded-xl border border-amber-100 bg-amber-50/30 p-4">
                                    <h4 className="text-sm font-bold text-amber-800 flex items-center gap-1.5 mb-3">
                                        <TrendingDown className="w-4 h-4" /> 매도 전략
                                    </h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-gray-500">① 1차 익절</span>
                                            <span className="font-semibold text-emerald-700">{s.target.toLocaleString()}원 (+{s.targetPct.toFixed(1)}%)</span>
                                        </div>
                                        <div className="pl-3 text-gray-400">진입가 + ATR(14)×1.5 도달 시 <b className="text-gray-600">50% 매도</b></div>
                                        <div className="flex justify-between"><span className="text-gray-500">② 하드 스톱</span>
                                            <span className="font-semibold text-red-600">{s.stop.toLocaleString()}원 ({s.stopPct.toFixed(1)}%)</span>
                                        </div>
                                        <div className="pl-3 text-gray-400">진입가 − ATR(14)×1.2 이탈 시 <b className="text-gray-600">전량 손절</b></div>
                                        <div className="flex justify-between"><span className="text-gray-500">③ 본절가 스톱</span>
                                            <span className="font-semibold text-yellow-700">{s.breakeven.toLocaleString()}원 (+0.35%)</span>
                                        </div>
                                        <div className="pl-3 text-gray-400">1차 익절 후 스톱 상향 (마찰비용 흡수)</div>
                                        <div className="flex justify-between"><span className="text-gray-500">④ 만기 청산</span>
                                            <span className="font-semibold text-gray-700">7영업일</span>
                                        </div>
                                        <div className="pl-3 text-gray-400">보유일 초과 시 종가로 전량 청산</div>
                                    </div>
                                </div>

                                {/* 포지션 사이징 */}
                                <div className="rounded-xl border border-purple-100 bg-purple-50/30 p-4">
                                    <h4 className="text-sm font-bold text-purple-800 flex items-center gap-1.5 mb-3">
                                        <Shield className="w-4 h-4" /> 포지션 사이징
                                    </h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-gray-500">기준 자본금</span><span className="font-semibold text-gray-800">1,000만원</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">ATR(14)</span><span className="font-semibold text-gray-800">{s.atr.toLocaleString()}원</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">1회 리스크</span><span className="font-semibold text-gray-800">{s.riskAmt.toLocaleString()}원 (1.5%)</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">스톱 거리(사이징)</span><span className="font-semibold text-gray-800">{Math.round(s.stopDist).toLocaleString()}원 (ATR×2.5)</span></div>
                                        <hr className="border-purple-100" />
                                        <div className="flex justify-between"><span className="text-gray-500">투입 수량</span><span className="font-bold text-purple-700">{s.shares}주</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">투입 금액</span><span className="font-bold text-purple-700">{Math.round(s.amount).toLocaleString()}원</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">최대 동시 보유</span><span className="font-semibold text-gray-800">10 슬롯</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">손익비(R:R)</span><span className="font-bold text-emerald-700">{s.rr.toFixed(2)} : 1</span></div>
                                    </div>
                                </div>
                            </div>

                            {/* 가격 레벨 시각화 */}
                            <div className="px-5 pb-5">
                                <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                                    <h4 className="text-xs font-bold text-gray-600 mb-3 flex items-center gap-1.5">
                                        <BarChart3 className="w-3.5 h-3.5" /> 가격 레벨
                                    </h4>
                                    <div className="space-y-2">
                                        {[
                                            { label: '1차 익절', price: s.target, pct: s.targetPct, color: 'bg-emerald-500', textColor: 'text-emerald-700' },
                                            { label: '본절가', price: s.breakeven, pct: 0.345, color: 'bg-yellow-500', textColor: 'text-yellow-700' },
                                            { label: '매수가', price: s.entryEst, pct: 0, color: 'bg-blue-500', textColor: 'text-blue-700' },
                                            { label: '하드 스톱', price: s.stop, pct: s.stopPct, color: 'bg-red-500', textColor: 'text-red-700' },
                                        ].map(level => {
                                            const range = s.target - s.stop;
                                            const width = range > 0 ? ((level.price - s.stop) / range) * 100 : 50;
                                            return (
                                                <div key={level.label} className="flex items-center gap-3 text-xs">
                                                    <span className={`w-16 text-right font-medium ${level.textColor}`}>{level.label}</span>
                                                    <div className="flex-1 bg-gray-200 rounded-full h-3 overflow-hidden">
                                                        <div className={`${level.color} h-full rounded-full transition-all`} style={{ width: `${Math.max(2, Math.min(width, 100))}%` }} />
                                                    </div>
                                                    <span className="w-24 text-right font-mono font-semibold text-gray-700">{level.price.toLocaleString()}원</span>
                                                    <span className={`w-16 text-right font-mono text-xs ${level.pct >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                                        {level.pct >= 0 ? '+' : ''}{level.pct.toFixed(1)}%
                                                    </span>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            </div>
                        </div>
                    );
                } else {
                    return (
                        <div className="bg-white rounded-xl shadow-sm border-2 border-emerald-200 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
                            <div className="px-5 py-4 bg-gradient-to-r from-emerald-50 to-blue-50 border-b border-emerald-100 flex items-center justify-between">
                                <div>
                                    <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                                        <Target className="w-5 h-5 text-emerald-600" />
                                        {stk.stk_nm} <span className="text-sm font-normal text-gray-400">({stk.stk_cd})</span>
                                    </h3>
                                    <p className="text-xs text-gray-500 mt-0.5">스윙-상따 백테스트 기준 매수/매도 전략 · 종가 {stk.close.toLocaleString()}원</p>
                                </div>
                                <button onClick={() => setSelectedStock(null)} className="p-1.5 rounded-lg hover:bg-gray-200 transition-colors">
                                    <X className="w-5 h-5 text-gray-400" />
                                </button>
                            </div>

                            <div className="p-5 grid grid-cols-1 md:grid-cols-3 gap-5">
                                {/* 매수 전략 */}
                                <div className="rounded-xl border border-blue-100 bg-blue-50/30 p-4">
                                    <h4 className="text-sm font-bold text-blue-800 flex items-center gap-1.5 mb-3">
                                        <TrendingUp className="w-4 h-4" /> 매수 전략
                                    </h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-gray-500">매수 방식</span><span className="font-semibold text-gray-800">Pseudo-VWAP 분할</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">매수 시간대</span><span className="font-semibold text-gray-800">14:30 ~ 15:20</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">분할 구간</span><span className="font-semibold text-gray-800">5구간 (10분 단위)</span></div>
                                        <hr className="border-blue-100" />
                                        <div className="text-gray-400 leading-relaxed">10%→12%→18%→25%→35% 비율로 시간대별 분할 매수 (VWAP 추종)</div>
                                    </div>
                                </div>

                                {/* 매도 전략 */}
                                <div className="rounded-xl border border-amber-100 bg-amber-50/30 p-4">
                                    <h4 className="text-sm font-bold text-amber-800 flex items-center gap-1.5 mb-3">
                                        <TrendingDown className="w-4 h-4" /> 매도 전략
                                    </h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-gray-500">① 트레일링 스톱</span>
                                            <span className="font-semibold text-red-600">{s.stop.toLocaleString()}원 ({s.stopPct.toFixed(1)}%)</span>
                                        </div>
                                        <div className="pl-3 text-gray-400">종가 − ATR(5)×2.5 래칫 방식 <b className="text-gray-600">(상승만 갱신)</b></div>
                                        <div className="flex justify-between"><span className="text-gray-500">② 만기 청산</span>
                                            <span className="font-semibold text-gray-700">5영업일</span>
                                        </div>
                                        <div className="pl-3 text-gray-400">보유일 초과 시 종가로 전량 청산</div>
                                        <hr className="border-amber-100" />
                                        <div className="text-gray-400 leading-relaxed">고정 목표가 없음. 트레일링 스톱이 수익 구간에서 자동 상향하여 이익 보호</div>
                                    </div>
                                </div>

                                {/* 포지션 사이징 */}
                                <div className="rounded-xl border border-purple-100 bg-purple-50/30 p-4">
                                    <h4 className="text-sm font-bold text-purple-800 flex items-center gap-1.5 mb-3">
                                        <Shield className="w-4 h-4" /> 포지션 사이징
                                    </h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-gray-500">기준 자본금</span><span className="font-semibold text-gray-800">1,000만원</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">ATR(5)</span><span className="font-semibold text-gray-800">{s.atr.toLocaleString()}원</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">1회 리스크</span><span className="font-semibold text-gray-800">{s.riskAmt.toLocaleString()}원 (1.5%)</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">스톱 거리</span><span className="font-semibold text-gray-800">{Math.round(s.stopDist).toLocaleString()}원 (ATR×2.5)</span></div>
                                        <hr className="border-purple-100" />
                                        <div className="flex justify-between"><span className="text-gray-500">투입 수량</span><span className="font-bold text-purple-700">{s.shares}주</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">투입 금액</span><span className="font-bold text-purple-700">{Math.round(s.amount).toLocaleString()}원</span></div>
                                        <div className="flex justify-between"><span className="text-gray-500">최대 동시 보유</span><span className="font-semibold text-gray-800">10 슬롯</span></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    );
                }
            })()}

            {/* Rejected Stocks (Collapsible) */}
            {data && rejectedStocks.length > 0 && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                    <button
                        className="w-full p-4 flex items-center justify-between text-left hover:bg-gray-50 transition-colors"
                        onClick={() => setShowRejected(!showRejected)}
                    >
                        <h3 className="text-sm font-bold text-gray-600 flex items-center gap-2">
                            <XCircle className="w-4 h-4 text-red-400" />
                            필터 탈락 종목 ({rejectedStocks.length}건)
                        </h3>
                        {showRejected ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
                    </button>
                    {showRejected && (
                        <div className="border-t border-gray-100 overflow-x-auto max-h-[400px] overflow-y-auto">
                            <table className="w-full text-xs">
                                <thead className="sticky top-0 bg-gray-50">
                                    <tr className="border-b text-gray-500">
                                        <th className="px-3 py-2 text-left">종목</th>
                                        {strategy !== 'pullback' && <th className="px-3 py-2 text-left">테마</th>}
                                        <th className="px-3 py-2 text-right">종가</th>
                                        <th className="px-3 py-2 text-right">수익률</th>
                                        <th className="px-3 py-2 text-left">탈락 사유</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-50">
                                    {rejectedStocks.map(stk => (
                                        <tr key={stk.stk_cd} className="hover:bg-red-50/30 text-gray-500">
                                            <td className="px-3 py-1.5">{stk.stk_nm} <span className="text-gray-300">({stk.stk_cd})</span></td>
                                            {strategy !== 'pullback' && <td className="px-3 py-1.5">{stk.theme_nm}</td>}
                                            <td className="px-3 py-1.5 text-right font-mono">{(stk.close || 0).toLocaleString()}</td>
                                            <td className="px-3 py-1.5 text-right font-mono">
                                                {stk.daily_return ? `${stk.daily_return >= 0 ? '+' : ''}${stk.daily_return}%` : '-'}
                                            </td>
                                            <td className="px-3 py-1.5">
                                                <span className="text-red-500">{stk.reason}</span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Empty State */}
            {!data && status.status === 'idle' && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-12 text-center text-gray-400">
                    <Filter className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p>스크리닝을 시작하면 결과가 여기에 표시됩니다.</p>
                </div>
            )}

            {/* Log Viewer */}
            <div className="bg-gray-900 rounded-xl shadow-2xl border border-gray-800 overflow-hidden">
                <button
                    className="w-full flex items-center justify-between px-5 py-3 bg-gray-800/50 border-b border-gray-800 hover:bg-gray-800/70 transition-colors"
                    onClick={() => setShowLogs(!showLogs)}
                >
                    <div className="flex items-center gap-2">
                        <Terminal className="w-4 h-4 text-emerald-400" />
                        <span className="text-sm font-bold text-gray-300">Screener Logs</span>
                    </div>
                    <div className="flex items-center gap-3">
                        <span className="text-[10px] text-gray-500 font-mono">
                            {status.pid ? `PID: ${status.pid}` : 'IDLE'}
                        </span>
                        {showLogs ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
                    </div>
                </button>
                {showLogs && (
                    <div
                        ref={logRef}
                        className="p-4 font-mono text-xs text-gray-300 h-[300px] overflow-y-auto leading-relaxed scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent"
                    >
                        {status.logs.length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-full text-gray-600 italic">
                                실행 로그가 여기에 표시됩니다.
                            </div>
                        ) : (
                            status.logs.map((line, i) => (
                                <div key={i} className="mb-0.5 hover:bg-white/5 px-2">
                                    <span className="text-gray-600 inline-block w-6 select-none">{i + 1}</span>
                                    {line}
                                </div>
                            ))
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

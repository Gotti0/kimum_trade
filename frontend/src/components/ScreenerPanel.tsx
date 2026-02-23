import { useState, useEffect, useRef } from 'react';
import { Play, Square, Terminal, Filter, RefreshCw, ChevronDown, ChevronUp, CheckCircle2, XCircle, Clock } from 'lucide-react';
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
    const [showLogs, setShowLogs] = useState(false);
    const [showRejected, setShowRejected] = useState(false);
    const [sortField, setSortField] = useState<'daily_return' | 'rvol' | 'disparity20' | 'adtv20'>('daily_return');
    const [sortAsc, setSortAsc] = useState(false);
    const logRef = useRef<HTMLDivElement>(null);

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
        axios.post(`${API}/screener`, { top_n: topN })
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
                            <h2 className="text-xl font-bold text-gray-800">알파 필터 스크리너</h2>
                            <p className="text-sm text-gray-500">유동성 → RVOL → 모멘텀 → 이격도 4단계 필터링</p>
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
                </div>

                {/* Controls Row */}
                <div className="flex items-end gap-4">
                    <div className="flex-shrink-0">
                        <label className="block text-sm font-medium text-gray-700 mb-1">상위 테마 수</label>
                        <input
                            type="number"
                            min="5"
                            max="100"
                            className="w-24 border border-gray-200 rounded-lg px-3 py-2.5 focus:ring-2 focus:ring-emerald-500 outline-none"
                            value={topN}
                            onChange={e => setTopN(Number(e.target.value))}
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
                        <div className="text-2xl font-bold text-blue-600">{data.total_themes}</div>
                        <div className="text-sm text-gray-500 mt-1">조회 테마</div>
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
                                    <th className="px-3 py-2 text-left">테마</th>
                                    <th className="px-3 py-2 text-right">종가</th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('daily_return')}>
                                        수익률 <SortIcon field="daily_return" />
                                    </th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('rvol')}>
                                        RVOL <SortIcon field="rvol" />
                                    </th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('disparity20')}>
                                        이격도 <SortIcon field="disparity20" />
                                    </th>
                                    <th className="px-3 py-2 text-right">SMA10</th>
                                    <th className="px-3 py-2 text-right">EMA20</th>
                                    <th className="px-3 py-2 text-right cursor-pointer select-none hover:text-emerald-600" onClick={() => handleSort('adtv20')}>
                                        ADTV(억) <SortIcon field="adtv20" />
                                    </th>
                                    <th className="px-3 py-2 text-right">ATR(5)</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-50">
                                {sortedStocks.map((stk, i) => (
                                    <tr key={stk.stk_cd} className="hover:bg-emerald-50/30 transition-colors">
                                        <td className="px-3 py-2.5 text-gray-400 text-xs">{i + 1}</td>
                                        <td className="px-3 py-2.5">
                                            <div className="font-medium text-gray-800">{stk.stk_nm}</div>
                                            <div className="text-[10px] text-gray-400">{stk.stk_cd}</div>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-100">
                                                {stk.theme_nm}
                                            </span>
                                        </td>
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
                                        <td className="px-3 py-2.5 text-right font-mono text-gray-500 text-xs">
                                            {stk.sma10.toLocaleString()}
                                        </td>
                                        <td className="px-3 py-2.5 text-right font-mono text-gray-500 text-xs">
                                            {stk.ema20.toLocaleString()}
                                        </td>
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
                                        <th className="px-3 py-2 text-left">테마</th>
                                        <th className="px-3 py-2 text-right">종가</th>
                                        <th className="px-3 py-2 text-right">수익률</th>
                                        <th className="px-3 py-2 text-left">탈락 사유</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-50">
                                    {rejectedStocks.map(stk => (
                                        <tr key={stk.stk_cd} className="hover:bg-red-50/30 text-gray-500">
                                            <td className="px-3 py-1.5">{stk.stk_nm} <span className="text-gray-300">({stk.stk_cd})</span></td>
                                            <td className="px-3 py-1.5">{stk.theme_nm}</td>
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

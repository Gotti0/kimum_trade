import { useState, useEffect, useRef } from 'react';
import { Play, Square, Terminal, TrendingUp, DollarSign, Calendar } from 'lucide-react';
import axios from 'axios';

const API = 'http://localhost:8001/api/pipeline';

interface PipelineStatus {
    name: string;
    status: 'idle' | 'running' | 'finished';
    pid?: number;
    exitCode?: number;
    logs: string[];
}

export default function BacktestPanel() {
    const [status, setStatus] = useState<PipelineStatus>({ name: 'kiwoom-backtest', status: 'idle', logs: [] });
    const [days, setDays] = useState(10);
    const [capital, setCapital] = useState(10000000);
    const logRef = useRef<HTMLDivElement>(null);

    // Status polling
    useEffect(() => {
        const fetchStatus = () => {
            axios.get(`${API}/status/kiwoom-backtest`)
                .then(r => setStatus(r.data))
                .catch(() => { });
        };
        fetchStatus();
        const id = setInterval(fetchStatus, 3000);
        return () => clearInterval(id);
    }, []);

    // Auto-scroll logs
    useEffect(() => {
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [status.logs]);

    const startBacktest = () => {
        axios.post(`${API}/kiwoom-backtest`, { days, capital })
            .catch(err => alert('실행 실패: ' + err.message));
    };

    const stopBacktest = () => {
        axios.post(`${API}/stop`, { name: 'kiwoom-backtest' })
            .catch(() => { });
    };

    const StatusBadge = ({ s }: { s: PipelineStatus }) => {
        const colors = {
            idle: 'bg-gray-100 text-gray-600',
            running: 'bg-blue-100 text-blue-700 animate-pulse',
            finished: s.exitCode === 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700',
        };
        const labels = { idle: '대기', running: '실행 중', finished: s.exitCode === 0 ? '완료' : '오류' };
        return (
            <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${colors[s.status]}`}>
                {s.status === 'running' && <span className="w-1.5 h-1.5 bg-blue-500 rounded-full" />}
                {labels[s.status]}
            </span>
        );
    };

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center">
                            <TrendingUp className="w-6 h-6 text-blue-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">Kiwoom Theme Backtester</h2>
                            <p className="text-sm text-gray-500">테마 선발 및 매매 전략 성과 분석</p>
                        </div>
                    </div>
                    <StatusBadge s={status} />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <Calendar className="w-4 h-4 text-gray-400" />
                            백테스팅 기간 (최근 N 영업일)
                        </label>
                        <div className="relative">
                            <input
                                type="number"
                                min="1"
                                max="100"
                                className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                                value={days}
                                onChange={e => setDays(Number(e.target.value))}
                            />
                            <span className="absolute right-4 top-3 text-gray-400 text-sm">일</span>
                        </div>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                            <DollarSign className="w-4 h-4 text-gray-400" />
                            초기 자본금
                        </label>
                        <div className="relative">
                            <input
                                type="number"
                                className="w-full border border-gray-200 rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                                value={capital}
                                onChange={e => setCapital(Number(e.target.value))}
                            />
                            <span className="absolute right-4 top-3 text-gray-400 text-sm">원</span>
                        </div>
                    </div>
                </div>

                <div className="flex gap-3">
                    <button
                        className="flex-1 flex items-center justify-center gap-2 px-6 py-3 rounded-xl font-bold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-all shadow-lg shadow-blue-200 active:scale-[0.98]"
                        onClick={startBacktest}
                        disabled={status.status === 'running'}
                    >
                        <Play className="w-5 h-5" /> 백테스팅 시작
                    </button>
                    <button
                        className="flex items-center justify-center px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 transition-all active:scale-[0.98]"
                        onClick={stopBacktest}
                        disabled={status.status !== 'running'}
                    >
                        <Square className="w-5 h-5" /> 중단
                    </button>
                </div>
            </div>

            {/* Log Viewer */}
            <div className="bg-gray-900 rounded-xl shadow-2xl border border-gray-800 overflow-hidden">
                <div className="flex items-center justify-between px-5 py-3 bg-gray-800/50 border-b border-gray-800">
                    <div className="flex items-center gap-2">
                        <Terminal className="w-4 h-4 text-blue-400" />
                        <span className="text-sm font-bold text-gray-300">Execution Logs</span>
                    </div>
                    <div className="text-[10px] text-gray-500 font-mono">
                        {status.pid ? `PID: ${status.pid}` : 'IDLE'}
                    </div>
                </div>
                <div
                    ref={logRef}
                    className="p-6 font-mono text-sm text-gray-300 h-[450px] overflow-y-auto leading-relaxed scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent"
                >
                    {status.logs.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-gray-600 italic">
                            <Terminal className="w-12 h-12 mb-2 opacity-20" />
                            실행 로그가 여기에 표시됩니다.
                        </div>
                    ) : (
                        status.logs.map((line, i) => (
                            <div key={i} className="mb-0.5 border-l-2 border-transparent hover:border-blue-500/30 hover:bg-white/5 px-2 transition-all">
                                <span className="text-gray-600 inline-block w-8 select-none">{i + 1}</span>
                                {line}
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
}

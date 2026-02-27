import { useState, useEffect, useRef } from 'react';
import { Play, Square, Terminal, TrendingUp, DollarSign, Calendar, Zap, Shield, BarChart3, SlidersHorizontal } from 'lucide-react';
import axios from 'axios';

const API = 'http://localhost:8001/api/pipeline';

type Strategy = 'phoenix' | 'swing' | 'pullback';

interface PipelineStatus {
    name: string;
    status: 'idle' | 'running' | 'finished';
    pid?: number;
    exitCode?: number;
    logs: string[];
}

const STRATEGY_CONFIG = {
    phoenix: {
        label: '피닉스 전략',
        desc: '선택한 타겟 종목(MD) 시초가 진입 및 시간대별·상한가 청산',
        color: 'blue',
        icon: TrendingUp,
        pipelineName: 'kiwoom-backtest',
    },
    swing: {
        label: '3~5일 스윙',
        desc: 'ATR 트레일링 스톱 + 알파 필터 (신규 전략)',
        color: 'violet',
        icon: BarChart3,
        pipelineName: 'kiwoom-backtest',
    },
    pullback: {
        label: '스윙-풀백',
        desc: '거래량 Top-N 유니버스 + 슬리피지 반영 (미래편향 제거)',
        color: 'emerald',
        icon: Shield,
        pipelineName: 'kiwoom-backtest',
    }
} as const;

export default function BacktestPanel() {
    const [strategy, setStrategy] = useState<Strategy>('phoenix');
    const [mode, setMode] = useState<'daily' | 'minute'>('daily');
    const [status, setStatus] = useState<PipelineStatus>({ name: 'kiwoom-backtest', status: 'idle', logs: [] });

    // 타겟 파일 선택 로직 추가
    const [mdFiles, setMdFiles] = useState<string[]>([]);
    const [selectedMdFile, setSelectedMdFile] = useState<string>('object_excel_daishin_filled.md');
    const [days, setDays] = useState(10);
    const [capital, setCapital] = useState(10000000);
    const [volumeTopN, setVolumeTopN] = useState(100);
    const [slippageBps, setSlippageBps] = useState(10);
    const [stopSlippageBps, setStopSlippageBps] = useState(20);
    const logRef = useRef<HTMLDivElement>(null);

    const cfg = STRATEGY_CONFIG[strategy];

    // Status polling 및 MD 파일 로드
    useEffect(() => {
        const fetchStatus = () => {
            axios.get(`${API}/status/${cfg.pipelineName}`)
                .then(r => setStatus(r.data))
                .catch(() => { });
        };
        fetchStatus();
        const id = setInterval(fetchStatus, 3000);

        // 마크다운 파일 목록 불러오기 (최초 1회 + 전략 변경 시 확인)
        axios.get(`${API}/md-files`)
            .then(r => {
                if (r.data && r.data.files) {
                    setMdFiles(r.data.files);
                    if (!r.data.files.includes(selectedMdFile) && r.data.files.length > 0) {
                        setSelectedMdFile(r.data.files[0]);
                    }
                }
            })
            .catch(() => { });

        return () => clearInterval(id);
    }, [strategy]);

    // Auto-scroll logs (only while running)
    useEffect(() => {
        if (status.status === 'running' && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [status.logs, status.status]);

    const startBacktest = () => {
        const payload: Record<string, unknown> = {
            days, capital,
            strategy: strategy === 'phoenix' ? 'legacy' : strategy, // 백엔드 라우터에서는 'legacy'로 받음
            mode: (strategy === 'swing' || strategy === 'pullback') ? mode : 'daily',
        };
        if (strategy === 'phoenix' && selectedMdFile) {
            payload.target_file = selectedMdFile;
        }
        if (strategy === 'pullback') {
            payload.volume_top_n = volumeTopN;
            payload.slippage_bps = slippageBps;
            payload.stop_slippage_bps = stopSlippageBps;
        }
        axios.post(`${API}/kiwoom-backtest`, payload)
            .catch(err => alert('실행 실패: ' + err.message));
    };

    const stopBacktest = () => {
        axios.post(`${API}/stop`, { name: cfg.pipelineName })
            .catch(() => { });
    };

    const isRunning = status.status === 'running';
    const accentMap = {
        blue: {
            bg: 'bg-blue-100', text: 'text-blue-600', btn: 'bg-blue-600 hover:bg-blue-700 shadow-blue-200',
            border: 'border-blue-200', ring: 'focus:ring-blue-500', selectBg: 'bg-blue-50 border-blue-300 ring-1 ring-blue-200',
        },
        violet: {
            bg: 'bg-violet-100', text: 'text-violet-600', btn: 'bg-violet-600 hover:bg-violet-700 shadow-violet-200',
            border: 'border-violet-200', ring: 'focus:ring-violet-500', selectBg: 'bg-violet-50 border-violet-300 ring-1 ring-violet-200',
        },
        emerald: {
            bg: 'bg-emerald-100', text: 'text-emerald-600', btn: 'bg-emerald-600 hover:bg-emerald-700 shadow-emerald-200',
            border: 'border-emerald-200', ring: 'focus:ring-emerald-500', selectBg: 'bg-emerald-50 border-emerald-300 ring-1 ring-emerald-200',
        },
    };
    const accent = accentMap[cfg.color];
    const Icon = cfg.icon;

    const StatusBadge = ({ s }: { s: PipelineStatus }) => {
        const colors = {
            idle: 'bg-gray-100 text-gray-600',
            running: `${accent.bg} ${accent.text} animate-pulse`,
            finished: s.exitCode === 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700',
        };
        const labels = { idle: '대기', running: '실행 중', finished: s.exitCode === 0 ? '완료' : '오류' };
        return (
            <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${colors[s.status]}`}>
                {s.status === 'running' && <span className={`w-1.5 h-1.5 rounded-full ${accent.bg.replace('100', '500')}`} />}
                {labels[s.status]}
            </span>
        );
    };

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                {/* Header */}
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-lg ${accent.bg} flex items-center justify-center transition-colors`}>
                            <Icon className={`w-6 h-6 ${accent.text}`} />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">Kiwoom Theme Backtester</h2>
                            <p className="text-sm text-gray-500">{cfg.desc}</p>
                        </div>
                    </div>
                    <StatusBadge s={status} />
                </div>

                {/* Strategy Toggle */}
                <div className="mb-6">
                    <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                        <Zap className="w-4 h-4 text-gray-400" />
                        전략 선택
                    </label>
                    <div className="grid grid-cols-2 gap-3">
                        {(Object.keys(STRATEGY_CONFIG) as Strategy[]).map(key => {
                            const c = STRATEGY_CONFIG[key];
                            const selected = strategy === key;
                            const StratIcon = c.icon;
                            return (
                                <button
                                    key={key}
                                    onClick={() => setStrategy(key)}
                                    disabled={isRunning}
                                    className={`
                                        relative flex items-center gap-3 px-4 py-3 rounded-xl border transition-all text-left
                                        ${selected
                                            ? accentMap[c.color].selectBg
                                            : 'bg-white border-gray-200 hover:border-gray-300'
                                        }
                                        disabled:opacity-60 disabled:cursor-not-allowed
                                    `}
                                >
                                    <StratIcon className={`w-5 h-5 ${selected ? accentMap[c.color].text : 'text-gray-400'}`} />
                                    <div>
                                        <div className={`text-sm font-bold ${selected ? accentMap[c.color].text : 'text-gray-700'}`}>
                                            {c.label}
                                        </div>
                                        <div className="text-xs text-gray-500 mt-0.5">{c.desc}</div>
                                    </div>
                                    {selected && (
                                        <div className={`absolute top-2 right-2 w-2 h-2 rounded-full ${accentMap[c.color].bg.replace('50', '500')}`} />
                                    )}
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* Phoenix Strategy File Selector */}
                {strategy === 'phoenix' && (
                    <div className="mb-6 rounded-xl border p-4 bg-blue-50/50 border-blue-100">
                        <div className="flex items-center justify-between mb-2">
                            <label className="text-sm font-bold text-blue-800 flex items-center gap-2">
                                <TrendingUp className="w-4 h-4 text-blue-500" />
                                매매 대상 종목 파일 (.md)
                            </label>
                        </div>
                        <select
                            value={selectedMdFile}
                            onChange={(e) => setSelectedMdFile(e.target.value)}
                            disabled={isRunning || mdFiles.length === 0}
                            className={`w-full border border-blue-200 rounded-lg px-4 py-3 bg-white text-sm focus:ring-2 focus:ring-blue-500 outline-none transition-all disabled:opacity-50`}
                        >
                            {mdFiles.length === 0 ? (
                                <option value="">MD 파일 로딩 중이거나 없습니다...</option>
                            ) : (
                                mdFiles.map(file => (
                                    <option key={file} value={file}>{file}</option>
                                ))
                            )}
                        </select>
                        <p className="mt-2 text-xs text-blue-600 font-medium">
                            선택한 파일의 "날자" 컬럼을 기록일로 인식하여 다음 영업일 매매에 사용합니다.
                        </p>
                    </div>
                )}
                {/* Swing Strategy Info Card */}
                {(strategy === 'swing' || strategy === 'pullback') && (
                    <div className={`mb-6 rounded-xl border p-4 ${strategy === 'pullback' ? 'bg-emerald-50/50 border-emerald-100' : 'bg-violet-50/50 border-violet-100'}`}>
                        <div className="flex items-center gap-2 mb-3">
                            <Shield className={`w-4 h-4 ${strategy === 'pullback' ? 'text-emerald-500' : 'text-violet-500'}`} />
                            <span className={`text-sm font-bold ${strategy === 'pullback' ? 'text-emerald-700' : 'text-violet-700'}`}>
                                {strategy === 'pullback' ? '풀백 전략 파라미터' : '스윙 전략 파라미터'}
                            </span>
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                            <div className={`bg-white rounded-lg p-2.5 border ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                                <div className="text-gray-500">ATR 기간</div>
                                <div className="font-bold text-gray-800 mt-0.5">{strategy === 'pullback' ? '14일' : '5일'}</div>
                            </div>
                            <div className={`bg-white rounded-lg p-2.5 border ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                                <div className="text-gray-500">{strategy === 'pullback' ? '스톱 / 익절' : '스톱 승수'}</div>
                                <div className="font-bold text-gray-800 mt-0.5">{strategy === 'pullback' ? '×1.2 / ×1.5' : '×2.5'}</div>
                            </div>
                            <div className={`bg-white rounded-lg p-2.5 border ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                                <div className="text-gray-500">최대 보유</div>
                                <div className="font-bold text-gray-800 mt-0.5">{strategy === 'pullback' ? '7일' : '5일'}</div>
                            </div>
                            <div className={`bg-white rounded-lg p-2.5 border ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                                <div className="text-gray-500">마찰 비용</div>
                                <div className="font-bold text-gray-800 mt-0.5">0.345%</div>
                            </div>
                            <div className={`bg-white rounded-lg p-2.5 border ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                                <div className="text-gray-500">슬롯 수</div>
                                <div className="font-bold text-gray-800 mt-0.5">10개</div>
                            </div>
                            <div className={`bg-white rounded-lg p-2.5 border ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                                <div className="text-gray-500">RPT</div>
                                <div className="font-bold text-gray-800 mt-0.5">1.5%</div>
                            </div>
                            {strategy === 'pullback' ? (
                                <>
                                    <div className={`bg-white rounded-lg p-2.5 border border-emerald-100`}>
                                        <div className="text-gray-500">매수·익절 슬리피지</div>
                                        <div className="font-bold text-gray-800 mt-0.5">{slippageBps} bp ({(slippageBps / 100).toFixed(2)}%)</div>
                                    </div>
                                    <div className={`bg-white rounded-lg p-2.5 border border-emerald-100`}>
                                        <div className="text-gray-500">손절 슬리피지</div>
                                        <div className="font-bold text-gray-800 mt-0.5">{stopSlippageBps} bp ({(stopSlippageBps / 100).toFixed(2)}%)</div>
                                    </div>
                                </>
                            ) : (
                                <>
                                    <div className={`bg-white rounded-lg p-2.5 border border-violet-100`}>
                                        <div className="text-gray-500">RVOL 허들</div>
                                        <div className="font-bold text-gray-800 mt-0.5">≥2.5</div>
                                    </div>
                                    <div className={`bg-white rounded-lg p-2.5 border border-violet-100`}>
                                        <div className="text-gray-500">이격도 캡</div>
                                        <div className="font-bold text-gray-800 mt-0.5">100~112</div>
                                    </div>
                                </>
                            )}
                        </div>

                        {/* Mode Selection for Swing/Pullback */}
                        <div className={`mt-4 pt-4 border-t flex items-center justify-between ${strategy === 'pullback' ? 'border-emerald-100' : 'border-violet-100'}`}>
                            <div className={`text-sm font-medium ${strategy === 'pullback' ? 'text-emerald-800' : 'text-violet-800'}`}>데이터 모드</div>
                            <div className={`flex bg-white rounded-lg border overflow-hidden divide-x ${strategy === 'pullback' ? 'border-emerald-200 divide-emerald-100' : 'border-violet-200 divide-violet-100'}`}>
                                <button
                                    onClick={() => setMode('daily')}
                                    className={`px-4 py-2 text-xs font-bold transition-colors ${mode === 'daily' ? (strategy === 'pullback' ? 'bg-emerald-100 text-emerald-800' : 'bg-violet-100 text-violet-800') : `text-gray-500 ${strategy === 'pullback' ? 'hover:bg-emerald-50' : 'hover:bg-violet-50'}`}`}
                                >
                                    일봉 전용 (장기)
                                </button>
                                <button
                                    onClick={() => setMode('minute')}
                                    className={`px-4 py-2 text-xs font-bold transition-colors ${mode === 'minute' ? (strategy === 'pullback' ? 'bg-emerald-100 text-emerald-800' : 'bg-violet-100 text-violet-800') : `text-gray-500 ${strategy === 'pullback' ? 'hover:bg-emerald-50' : 'hover:bg-violet-50'}`}`}
                                >
                                    분봉 기반 (~60일)
                                </button>
                            </div>
                        </div>

                        {/* Pullback-only: 슬리피지 & 유니버스 설정 */}
                        {strategy === 'pullback' && (
                            <div className="mt-4 pt-4 border-t border-emerald-100">
                                <div className="flex items-center gap-2 mb-3">
                                    <SlidersHorizontal className="w-4 h-4 text-emerald-500" />
                                    <span className="text-sm font-bold text-emerald-700">마찰 비용 & 유니버스 설정</span>
                                </div>
                                <div className="grid grid-cols-3 gap-3">
                                    <div>
                                        <label className="block text-xs text-gray-500 mb-1">유니버스 Top-N</label>
                                        <input
                                            type="number" min={10} max={500} step={10}
                                            className="w-full border border-emerald-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                                            value={volumeTopN}
                                            onChange={e => setVolumeTopN(Number(e.target.value))}
                                            disabled={isRunning}
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-xs text-gray-500 mb-1">매수·익절 슬리피지 (bp)</label>
                                        <input
                                            type="number" min={0} max={100} step={5}
                                            className="w-full border border-emerald-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                                            value={slippageBps}
                                            onChange={e => setSlippageBps(Number(e.target.value))}
                                            disabled={isRunning}
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-xs text-gray-500 mb-1">손절 슬리피지 (bp)</label>
                                        <input
                                            type="number" min={0} max={100} step={5}
                                            className="w-full border border-emerald-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                                            value={stopSlippageBps}
                                            onChange={e => setStopSlippageBps(Number(e.target.value))}
                                            disabled={isRunning}
                                        />
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Parameters */}
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
                                className={`w-full border border-gray-200 rounded-lg px-4 py-2.5 ${accent.ring} focus:ring-2 outline-none transition-all`}
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
                                className={`w-full border border-gray-200 rounded-lg px-4 py-2.5 ${accent.ring} focus:ring-2 outline-none transition-all`}
                                value={capital}
                                onChange={e => setCapital(Number(e.target.value))}
                            />
                            <span className="absolute right-4 top-3 text-gray-400 text-sm">원</span>
                        </div>
                    </div>
                </div>

                {/* Action Buttons */}
                <div className="flex gap-3">
                    <button
                        className={`flex-1 flex items-center justify-center gap-2 px-6 py-3 rounded-xl font-bold text-white ${accent.btn} disabled:opacity-50 transition-all shadow-lg active:scale-[0.98]`}
                        onClick={startBacktest}
                        disabled={isRunning}
                    >
                        <Play className="w-5 h-5" />
                        {strategy === 'swing' ? '스윙 백테스팅 시작' : strategy === 'pullback' ? '스윙-풀백 백테스팅 시작' : '백테스팅 시작'}
                    </button>
                    <button
                        className="flex items-center justify-center px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50 transition-all active:scale-[0.98]"
                        onClick={stopBacktest}
                        disabled={!isRunning}
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
                        <span className="text-sm font-bold text-gray-300">
                            Execution Logs {(strategy === 'swing' || strategy === 'pullback') && <span className={`${strategy === 'pullback' ? 'text-emerald-400' : 'text-violet-400'} text-xs ml-1`}>[{strategy === 'pullback' ? 'Pullback' : 'Swing'}]</span>}
                        </span>
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
                        status.logs.slice(-1000).map((line, i) => {
                            const originalIndex = Math.max(0, status.logs.length - 1000) + i;
                            return (
                                <div key={originalIndex} className="mb-0.5 border-l-2 border-transparent hover:border-blue-500/30 hover:bg-white/5 px-2 transition-all">
                                    <span className="text-gray-600 inline-block w-8 select-none">{originalIndex + 1}</span>
                                    {line}
                                </div>
                            );
                        })
                    )}
                </div>
            </div>
        </div>
    );
}


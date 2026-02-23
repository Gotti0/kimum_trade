import { useState, useEffect, useRef, useCallback } from 'react';
import { Server, FileSpreadsheet, BarChart3, Play, Square, ChevronDown, Terminal, Wifi, WifiOff } from 'lucide-react';
import axios from 'axios';

const API = 'http://localhost:8001/api/pipeline';

interface PipelineStatus {
    name: string;
    status: 'idle' | 'running' | 'finished';
    pid?: number;
    exitCode?: number;
    logs: string[];
}

export default function PipelinePanel() {
    const [pipelines, setPipelines] = useState<PipelineStatus[]>([]);
    const [bridgeConnected, setBridgeConnected] = useState<boolean | null>(null);
    const [excelFiles, setExcelFiles] = useState<string[]>([]);
    const [selectedFile, setSelectedFile] = useState('');
    const [stockCode, setStockCode] = useState('A005930');
    const [activeLog, setActiveLog] = useState<string | null>(null);
    const logRef = useRef<HTMLDivElement>(null);

    // Status polling (pipelines + bridge health)
    useEffect(() => {
        const fetchAll = () => {
            axios.get(`${API}/status`).then(r => setPipelines(r.data.pipelines)).catch(() => { });
            axios.get(`${API}/bridge-server/health`)
                .then(r => setBridgeConnected(r.data.status === 'connected'))
                .catch(() => setBridgeConnected(false));
        };
        fetchAll();
        const id = setInterval(fetchAll, 3000);
        return () => clearInterval(id);
    }, []);

    // Load excel files once
    useEffect(() => {
        axios.get(`${API}/excel-files`)
            .then(r => {
                setExcelFiles(r.data.files || []);
                if (r.data.files?.length) setSelectedFile(r.data.files[0]);
            })
            .catch(() => { });
    }, []);

    // Auto-scroll logs (only while running)
    const activeStatus = pipelines.find(p => p.name === activeLog);
    useEffect(() => {
        if (activeStatus?.status === 'running' && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [pipelines, activeLog, activeStatus?.status]);

    const getStatus = useCallback(
        (name: string) => pipelines.find(p => p.name === name) || { name, status: 'idle' as const, logs: [] },
        [pipelines]
    );

    const startExcelFill = () =>
        selectedFile && axios.post(`${API}/excel-fill`, { filename: selectedFile }).catch(() => { });
    const startFetchChart = () =>
        stockCode && axios.post(`${API}/fetch-chart`, { stock_code: stockCode }).catch(() => { });
    const stopPipeline = (name: string) =>
        axios.post(`${API}/stop`, { name }).catch(() => { });

    const StatusBadge = ({ s }: { s: PipelineStatus }) => {
        const colors = {
            idle: 'bg-gray-100 text-gray-600',
            running: 'bg-emerald-100 text-emerald-700 animate-pulse',
            finished: s.exitCode === 0 ? 'bg-blue-100 text-blue-700' : 'bg-red-100 text-red-700',
        };
        const labels = { idle: '대기', running: '실행 중', finished: s.exitCode === 0 ? '완료' : '오류' };
        return (
            <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${colors[s.status]}`}>
                {s.status === 'running' && <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />}
                {labels[s.status]}
            </span>
        );
    };

    const excelStatus = getStatus('excel-fill');
    const fetchStatus = getStatus('fetch-chart');
    const activePipeline = activeLog ? getStatus(activeLog) : null;

    return (
        <div className="space-y-6">
            {/* Bridge Server Monitor Banner */}
            <div className={`rounded-xl border-2 p-4 flex items-center justify-between transition-all ${bridgeConnected === null ? 'border-gray-200 bg-gray-50' :
                    bridgeConnected ? 'border-emerald-200 bg-emerald-50' : 'border-orange-200 bg-orange-50'
                }`}>
                <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${bridgeConnected ? 'bg-emerald-100' : 'bg-orange-100'
                        }`}>
                        {bridgeConnected
                            ? <Wifi className="w-5 h-5 text-emerald-600" />
                            : <WifiOff className="w-5 h-5 text-orange-500" />
                        }
                    </div>
                    <div>
                        <h3 className="font-semibold text-gray-800 text-sm flex items-center gap-2">
                            Daishin Bridge Server
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${bridgeConnected === null ? 'bg-gray-200 text-gray-500' :
                                    bridgeConnected ? 'bg-emerald-200 text-emerald-700' : 'bg-orange-200 text-orange-700'
                                }`}>
                                {bridgeConnected === null ? '확인 중...'
                                    : bridgeConnected
                                        ? <><span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" /> 연결됨</>
                                        : '연결 안 됨'
                                }
                            </span>
                        </h3>
                        <p className="text-xs text-gray-500 mt-0.5">
                            {bridgeConnected
                                ? 'localhost:8000에서 정상 작동 중입니다.'
                                : '관리자 권한으로 run_daishin_excel_fill.bat 또는 run_daishin_pipeline.bat을 실행해 주세요.'
                            }
                        </p>
                    </div>
                </div>
                <Server className={`w-6 h-6 ${bridgeConnected ? 'text-emerald-400' : 'text-orange-300'}`} />
            </div>

            {/* Pipeline Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">

                {/* Excel Fill */}
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 flex flex-col">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                            <div className="w-9 h-9 rounded-lg bg-emerald-100 flex items-center justify-center">
                                <FileSpreadsheet className="w-5 h-5 text-emerald-600" />
                            </div>
                            <div>
                                <h3 className="font-semibold text-gray-800 text-sm">Excel Fill</h3>
                                <p className="text-xs text-gray-400">분봉 데이터 채우기</p>
                            </div>
                        </div>
                        <StatusBadge s={excelStatus} />
                    </div>
                    <div className="mb-3">
                        <label className="block text-xs font-medium text-gray-500 mb-1">대상 파일</label>
                        <div className="relative">
                            <select
                                className="w-full appearance-none border border-gray-200 rounded-lg text-sm pl-3 pr-8 py-2 bg-white focus:ring-2 focus:ring-emerald-400 focus:border-emerald-400 outline-none transition-all"
                                value={selectedFile}
                                onChange={e => setSelectedFile(e.target.value)}
                            >
                                {excelFiles.length === 0 && <option value="">파일 없음</option>}
                                {excelFiles.map(f => <option key={f} value={f}>{f}</option>)}
                            </select>
                            <ChevronDown className="absolute right-2.5 top-2.5 w-4 h-4 text-gray-400 pointer-events-none" />
                        </div>
                    </div>
                    <div className="flex gap-2 mt-auto">
                        <button
                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                            onClick={startExcelFill}
                            disabled={excelStatus.status === 'running' || !selectedFile || !bridgeConnected}
                            title={!bridgeConnected ? 'Bridge Server가 연결되어야 합니다' : ''}
                        >
                            <Play className="w-3.5 h-3.5" /> 실행
                        </button>
                        <button
                            className="flex items-center justify-center px-2 py-2 rounded-lg text-sm bg-gray-50 text-gray-500 hover:bg-gray-100 disabled:opacity-40 transition-all"
                            onClick={() => stopPipeline('excel-fill')}
                            disabled={excelStatus.status !== 'running'}
                        >
                            <Square className="w-3.5 h-3.5" />
                        </button>
                        <button
                            className="flex items-center justify-center px-2 py-2 rounded-lg text-sm bg-gray-50 text-gray-500 hover:bg-gray-100 transition-all"
                            onClick={() => setActiveLog(activeLog === 'excel-fill' ? null : 'excel-fill')}
                            title="로그 보기"
                        >
                            <Terminal className="w-4 h-4" />
                        </button>
                    </div>
                </div>

                {/* Fetch Chart */}
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 flex flex-col">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                            <div className="w-9 h-9 rounded-lg bg-sky-100 flex items-center justify-center">
                                <BarChart3 className="w-5 h-5 text-sky-600" />
                            </div>
                            <div>
                                <h3 className="font-semibold text-gray-800 text-sm">Data Fetch</h3>
                                <p className="text-xs text-gray-400">분봉 차트 수집</p>
                            </div>
                        </div>
                        <StatusBadge s={fetchStatus} />
                    </div>
                    <div className="mb-3">
                        <label className="block text-xs font-medium text-gray-500 mb-1">종목코드</label>
                        <input
                            type="text"
                            className="w-full border border-gray-200 rounded-lg text-sm px-3 py-2 focus:ring-2 focus:ring-sky-400 focus:border-sky-400 outline-none uppercase transition-all"
                            value={stockCode}
                            onChange={e => setStockCode(e.target.value.toUpperCase())}
                            placeholder="A005930"
                        />
                    </div>
                    <div className="flex gap-2 mt-auto">
                        <button
                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                            onClick={startFetchChart}
                            disabled={fetchStatus.status === 'running' || !stockCode || !bridgeConnected}
                            title={!bridgeConnected ? 'Bridge Server가 연결되어야 합니다' : ''}
                        >
                            <Play className="w-3.5 h-3.5" /> 실행
                        </button>
                        <button
                            className="flex items-center justify-center px-2 py-2 rounded-lg text-sm bg-gray-50 text-gray-500 hover:bg-gray-100 disabled:opacity-40 transition-all"
                            onClick={() => stopPipeline('fetch-chart')}
                            disabled={fetchStatus.status !== 'running'}
                        >
                            <Square className="w-3.5 h-3.5" />
                        </button>
                        <button
                            className="flex items-center justify-center px-2 py-2 rounded-lg text-sm bg-gray-50 text-gray-500 hover:bg-gray-100 transition-all"
                            onClick={() => setActiveLog(activeLog === 'fetch-chart' ? null : 'fetch-chart')}
                            title="로그 보기"
                        >
                            <Terminal className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </div>

            {/* Log Viewer */}
            {activePipeline && (
                <div className="bg-gray-900 rounded-xl shadow-lg border border-gray-700 overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-2.5 bg-gray-800 border-b border-gray-700">
                        <div className="flex items-center gap-2">
                            <Terminal className="w-4 h-4 text-gray-400" />
                            <span className="text-sm font-medium text-gray-300">{activeLog}</span>
                            <StatusBadge s={activePipeline} />
                        </div>
                        <button
                            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                            onClick={() => setActiveLog(null)}
                        >
                            닫기 ✕
                        </button>
                    </div>
                    <div
                        ref={logRef}
                        className="p-4 font-mono text-xs text-green-400 max-h-80 overflow-y-auto leading-relaxed whitespace-pre-wrap"
                    >
                        {activePipeline.logs.length === 0
                            ? <span className="text-gray-600">로그 대기 중...</span>
                            : activePipeline.logs.map((line, i) => <div key={i}>{line}</div>)
                        }
                    </div>
                </div>
            )}
        </div>
    );
}


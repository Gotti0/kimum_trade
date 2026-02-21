import { useState, useEffect, useRef, useCallback } from 'react';
import { Server, FileSpreadsheet, BarChart3, Play, Square, ChevronDown, Terminal } from 'lucide-react';
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
    const [excelFiles, setExcelFiles] = useState<string[]>([]);
    const [selectedFile, setSelectedFile] = useState('');
    const [stockCode, setStockCode] = useState('A005930');
    const [activeLog, setActiveLog] = useState<string | null>(null);
    const logRef = useRef<HTMLDivElement>(null);

    // Status polling
    useEffect(() => {
        const fetchStatus = () =>
            axios.get(`${API}/status`).then(r => setPipelines(r.data.pipelines)).catch(() => { });
        fetchStatus();
        const id = setInterval(fetchStatus, 2000);
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

    // Auto-scroll logs
    useEffect(() => {
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [pipelines, activeLog]);

    const getStatus = useCallback(
        (name: string) => pipelines.find(p => p.name === name) || { name, status: 'idle' as const, logs: [] },
        [pipelines]
    );

    const startBridge = () => axios.post(`${API}/bridge-server/start`).catch(() => { });
    const stopBridge = () => axios.post(`${API}/bridge-server/stop`).catch(() => { });
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

    const bridgeStatus = getStatus('bridge-server');
    const excelStatus = getStatus('excel-fill');
    const fetchStatus = getStatus('fetch-chart');

    const activePipeline = activeLog ? getStatus(activeLog) : null;

    return (
        <div className="space-y-6">
            {/* Pipeline Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">

                {/* Bridge Server */}
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 flex flex-col">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                            <div className="w-9 h-9 rounded-lg bg-violet-100 flex items-center justify-center">
                                <Server className="w-5 h-5 text-violet-600" />
                            </div>
                            <div>
                                <h3 className="font-semibold text-gray-800 text-sm">Bridge Server</h3>
                                <p className="text-xs text-gray-400">Daishin 32-bit API</p>
                            </div>
                        </div>
                        <StatusBadge s={bridgeStatus} />
                    </div>
                    <p className="text-xs text-gray-500 mb-4 flex-1">
                        Daishin COM 객체를 사용하는 32-bit 브릿지 서버입니다. Excel Fill 및 Data Fetch 실행 전에 먼저 시작해야 합니다.
                    </p>
                    <div className="flex gap-2">
                        <button
                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                            onClick={startBridge}
                            disabled={bridgeStatus.status === 'running'}
                        >
                            <Play className="w-3.5 h-3.5" /> 시작
                        </button>
                        <button
                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                            onClick={stopBridge}
                            disabled={bridgeStatus.status !== 'running'}
                        >
                            <Square className="w-3.5 h-3.5" /> 중지
                        </button>
                        <button
                            className="flex items-center justify-center px-2 py-2 rounded-lg text-sm bg-gray-50 text-gray-500 hover:bg-gray-100 transition-all"
                            onClick={() => setActiveLog(activeLog === 'bridge-server' ? null : 'bridge-server')}
                            title="로그 보기"
                        >
                            <Terminal className="w-4 h-4" />
                        </button>
                    </div>
                </div>

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
                            disabled={excelStatus.status === 'running' || !selectedFile}
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
                            disabled={fetchStatus.status === 'running' || !stockCode}
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

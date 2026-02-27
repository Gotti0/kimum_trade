import { useState, useEffect } from 'react';
import { Plus, Trash2, Save, Bot, Search, TrendingUp, TrendingDown, Activity, Settings } from 'lucide-react';
import axios from 'axios';

const API_BASE = 'http://localhost:8001/api';

interface AutoTradeTarget {
    stk_cd: string;
    stk_nm: string;
    buy_amount: number;
}

interface AutoTradeHistory {
    date: string;
    time: string;
    stk_cd: string;
    stk_nm: string;
    buy_price: number;
    sell_price: number;
    qty: number;
    profit_amount: number;
    profit_rate: number;
    sell_reason: string;
}

interface AutoTradeConfig {
    buy_time: string;
    evaluate_time: string;
    force_close_time: string;
    trailing_drop_rate: number;
}

export default function AutoTradePanel() {
    const [targets, setTargets] = useState<AutoTradeTarget[]>([]);
    const [history, setHistory] = useState<AutoTradeHistory[]>([]);
    const [config, setConfig] = useState<AutoTradeConfig>({
        buy_time: '0900',
        evaluate_time: '0914',
        force_close_time: '1520',
        trailing_drop_rate: 0.08
    });
    const [newCode, setNewCode] = useState('');
    const [newName, setNewName] = useState('');
    const [newAmount, setNewAmount] = useState<number>(1000000); // 기본 100만원
    const [isSaving, setIsSaving] = useState(false);
    const [message, setMessage] = useState<{ text: string, type: 'success' | 'error' } | null>(null);

    // 컴포넌트 마운트 시 기존 타겟 및 로컬스토리지 설정 로드
    useEffect(() => {
        // Load settings from localStorage first
        const savedConfig = localStorage.getItem('autoTradeConfig');
        if (savedConfig) {
            try {
                const parsed = JSON.parse(savedConfig);
                setConfig(parsed);
                // Sync with backend on load
                axios.post(`${API_BASE}/auto-trade/config`, parsed)
                    .catch(e => console.error("Initial config sync failed:", e));
            } catch (e) {
                console.error("Failed to parse config from localStorage:", e);
            }
        } else {
            // If nothing in localStorage, fetch from backend and save
            axios.get(`${API_BASE}/auto-trade/config`)
                .then(res => {
                    if (res.data) {
                        setConfig(res.data);
                        localStorage.setItem('autoTradeConfig', JSON.stringify(res.data));
                    }
                })
                .catch(err => console.error("Config 로드 실패:", err));
        }

        axios.get(`${API_BASE}/auto-trade/targets`)
            .then(res => {
                if (Array.isArray(res.data)) {
                    setTargets(res.data);
                }
            })
            .catch(err => console.error("타겟 로드 실패:", err));

        axios.get(`${API_BASE}/auto-trade/history`)
            .then(res => {
                if (Array.isArray(res.data)) {
                    setHistory(res.data);
                }
            })
            .catch(err => console.error("히스토리 로드 실패:", err));
    }, []);

    const totalProfit = history.reduce((sum, h) => sum + h.profit_amount, 0);
    const winTrades = history.filter(h => h.profit_amount > 0).length;
    const winRate = history.length > 0 ? (winTrades / history.length) * 100 : 0;

    const showMessage = (text: string, type: 'success' | 'error') => {
        setMessage({ text, type });
        setTimeout(() => setMessage(null), 3000);
    };

    const handleAddTarget = () => {
        if (!newCode.trim() || !newName.trim() || newAmount <= 0) {
            showMessage("종목코드, 종목명, 매수금액을 올바르게 입력해주세요.", "error");
            return;
        }

        // 중복 체크
        if (targets.some(t => t.stk_cd === newCode.trim())) {
            showMessage("이미 등록된 종목입니다.", "error");
            return;
        }

        const newTarget: AutoTradeTarget = {
            stk_cd: newCode.trim(),
            stk_nm: newName.trim(),
            buy_amount: Number(newAmount)
        };

        setTargets([...targets, newTarget]);
        setNewCode('');
        setNewName('');
    };

    const handleRemoveTarget = (code: string) => {
        setTargets(targets.filter(t => t.stk_cd !== code));
    };

    const handleSaveTargets = () => {
        setIsSaving(true);
        axios.post(`${API_BASE}/auto-trade/targets`, targets)
            .then(() => {
                showMessage("타겟 종목이 성공적으로 저장되었습니다.", "success");
            })
            .catch(err => {
                showMessage(`저장 실패: ${err.message}`, "error");
            })
            .finally(() => setIsSaving(false));
    };

    const handleConfigChange = (key: keyof AutoTradeConfig, value: string | number) => {
        const newConfig = { ...config, [key]: value };
        setConfig(newConfig);
    };

    const handleSaveConfig = () => {
        localStorage.setItem('autoTradeConfig', JSON.stringify(config));
        axios.post(`${API_BASE}/auto-trade/config`, config)
            .then(() => showMessage("환경 설정이 성공적으로 저장되었습니다.", "success"))
            .catch(err => showMessage(`설정 저장 실패: ${err.message}`, "error"));
    };

    const formatCurrency = (val: number) => {
        return val.toLocaleString() + '원';
    };

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                {/* Header */}
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-indigo-100 flex items-center justify-center">
                            <Bot className="w-6 h-6 text-indigo-600" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-800">모의투자 자동매매 (피닉스)</h2>
                            <p className="text-sm text-gray-500">익일 매수할 타겟 종목과 매수 금액을 지정합니다.</p>
                        </div>
                    </div>
                    {message && (
                        <div className={`px-4 py-2 rounded-lg text-sm font-medium ${message.type === 'success' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                            {message.text}
                        </div>
                    )}
                </div>

                {/* Add Target Form */}
                <div className="bg-gray-50 p-4 rounded-xl border border-gray-200 mb-6">
                    <h3 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-2">
                        <Plus className="w-4 h-4 text-indigo-500" /> 신규 종목 추가
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">종목 코드 (6자리)</label>
                            <input
                                type="text"
                                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                                placeholder="예: 005930"
                                value={newCode}
                                onChange={e => setNewCode(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">종목명</label>
                            <input
                                type="text"
                                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                                placeholder="예: 삼성전자"
                                value={newName}
                                onChange={e => setNewName(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">매수 금액 (원)</label>
                            <input
                                type="number"
                                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                                value={newAmount}
                                step="100000"
                                min="100000"
                                onChange={e => setNewAmount(Number(e.target.value))}
                            />
                        </div>
                        <div className="flex items-end">
                            <button
                                onClick={handleAddTarget}
                                className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2 rounded-lg transition-colors shadow-sm flex justify-center items-center gap-2 text-sm"
                            >
                                <Plus className="w-4 h-4" /> 추가
                            </button>
                        </div>
                    </div>
                </div>

                {/* Targets List */}
                <div className="border border-gray-200 rounded-xl overflow-hidden mb-6">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="bg-gray-50 border-b border-gray-200 text-sm font-medium text-gray-600">
                                <th className="p-4">종목코드</th>
                                <th className="p-4">종목명</th>
                                <th className="p-4 text-right">매수 금액</th>
                                <th className="p-4 text-center">관리</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                            {targets.length === 0 ? (
                                <tr>
                                    <td colSpan={4} className="p-8 text-center text-gray-500 text-sm">
                                        등록된 매매 타겟이 없습니다.
                                    </td>
                                </tr>
                            ) : (
                                targets.map(target => (
                                    <tr key={target.stk_cd} className="hover:bg-gray-50 transition-colors">
                                        <td className="p-4 text-gray-700 font-mono text-sm">{target.stk_cd}</td>
                                        <td className="p-4 font-bold text-gray-800">{target.stk_nm}</td>
                                        <td className="p-4 text-right text-indigo-600 font-medium">{formatCurrency(target.buy_amount)}</td>
                                        <td className="p-4 text-center">
                                            <button
                                                onClick={() => handleRemoveTarget(target.stk_cd)}
                                                className="text-red-500 hover:text-red-700 p-1.5 rounded-md hover:bg-red-50 transition-colors"
                                                title="삭제"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                        {targets.length > 0 && (
                            <tfoot>
                                <tr className="bg-indigo-50/50 border-t border-indigo-100">
                                    <td colSpan={2} className="p-4 font-bold text-indigo-800 text-right">총 매수 예정 금액</td>
                                    <td className="p-4 text-right font-bold text-indigo-700">
                                        {formatCurrency(targets.reduce((sum, t) => sum + t.buy_amount, 0))}
                                    </td>
                                    <td></td>
                                </tr>
                            </tfoot>
                        )}
                    </table>
                </div>

                {/* Save Button */}
                <div className="flex justify-end border-t border-gray-100 pt-6">
                    <button
                        onClick={handleSaveTargets}
                        disabled={isSaving}
                        className="bg-gray-900 hover:bg-black text-white px-6 py-2.5 rounded-lg font-bold flex items-center gap-2 shadow-lg transition-all active:scale-95 disabled:opacity-50"
                    >
                        <Save className="w-4 h-4" /> {isSaving ? "저장 중..." : "타겟 리스트 서버에 저장"}
                    </button>
                </div>
            </div>

            {/* Config Panel */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-lg bg-orange-100 flex items-center justify-center">
                        <Settings className="w-6 h-6 text-orange-600" />
                    </div>
                    <div>
                        <h2 className="text-xl font-bold text-gray-800">자동매매 환경 설정</h2>
                        <p className="text-sm text-gray-500">봇의 매수/매도 시간 및 트레일링 스톱 비율을 커스터마이징합니다.</p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">매수 진입 시간</label>
                        <input type="text" value={config.buy_time} onChange={(e) => handleConfigChange('buy_time', e.target.value)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-orange-500 outline-none" placeholder="HHMM (예: 0900)" />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">수익률 평가 시간</label>
                        <input type="text" value={config.evaluate_time} onChange={(e) => handleConfigChange('evaluate_time', e.target.value)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-orange-500 outline-none" placeholder="HHMM (예: 0914)" />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">장 마감 청산 시간</label>
                        <input type="text" value={config.force_close_time} onChange={(e) => handleConfigChange('force_close_time', e.target.value)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-orange-500 outline-none" placeholder="HHMM (예: 1520)" />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">트레일링 스톱 비율 (%)</label>
                        <input type="number" step="1" value={Math.round(config.trailing_drop_rate * 100)} onChange={(e) => handleConfigChange('trailing_drop_rate', Number(e.target.value) / 100)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-orange-500 outline-none" placeholder="퍼센트 기준 (예: 8)" />
                    </div>
                </div>

                <div className="flex justify-end border-t border-gray-100 pt-4">
                    <button onClick={handleSaveConfig} className="bg-orange-600 hover:bg-orange-700 text-white px-5 py-2 rounded-lg font-medium flex items-center gap-2 shadow-sm transition-colors text-sm">
                        <Save className="w-4 h-4" /> 설정 저장 (동기화)
                    </button>
                </div>
            </div>

            {/* Instruction Card */}
            <div className="bg-blue-50/50 border border-blue-100 rounded-xl p-5">
                <h3 className="font-bold text-blue-800 mb-2 flex items-center gap-2">
                    <Search className="w-4 h-4 text-blue-600" /> 이용 안내
                </h3>
                <ul className="text-sm text-blue-700 space-y-1.5 list-disc pl-5">
                    <li>여기에 등록된 종목은 다음 영업일 <b>오전 9시 정각</b>에 자동 시장가 매수됩니다.</li>
                    <li>매수 금액을 기준으로 매수 수량을 계산하여 주문합니다.</li>
                    <li>목표 시간대 및 트레일링 스톱 로직은 백그라운드 봇(<code className="bg-blue-100 px-1 rounded text-xs">auto_trader.py</code>)에 의해 매일 자동으로 작동합니다.</li>
                    <li>봇이 실행 중인지 터미널에서 반드시 확인해주세요.</li>
                </ul>
            </div>

            {/* History Dashboard */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
                        <Activity className="w-6 h-6 text-green-600" />
                    </div>
                    <div>
                        <h2 className="text-xl font-bold text-gray-800">누적 수익률 대시보드</h2>
                        <p className="text-sm text-gray-500">자동매매 봇이 체결한 매매 내역 및 수익률을 확인합니다.</p>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                    <div className="bg-gray-50 rounded-xl p-4 border border-gray-100 shadow-sm">
                        <div className="text-sm text-gray-500 mb-1 font-medium">총 누적 수익금</div>
                        <div className={`text-2xl font-bold flex items-center gap-2 ${totalProfit > 0 ? 'text-red-500' : totalProfit < 0 ? 'text-blue-500' : 'text-gray-800'}`}>
                            {totalProfit > 0 ? <TrendingUp className="w-5 h-5" /> : totalProfit < 0 ? <TrendingDown className="w-5 h-5" /> : null}
                            {totalProfit > 0 ? '+' : ''}{formatCurrency(totalProfit)}
                        </div>
                    </div>
                    <div className="bg-gray-50 rounded-xl p-4 border border-gray-100 shadow-sm">
                        <div className="text-sm text-gray-500 mb-1 font-medium">매매 승률 (승/총 매매)</div>
                        <div className="text-2xl font-bold text-gray-800">
                            {winRate.toFixed(1)}% <span className="text-sm font-normal text-gray-500 ml-1">({winTrades}/{history.length}건)</span>
                        </div>
                    </div>
                    <div className="bg-gray-50 rounded-xl p-4 border border-gray-100 shadow-sm">
                        <div className="text-sm text-gray-500 mb-1 font-medium">평균 수익금 (건당)</div>
                        <div className="text-2xl font-bold text-gray-800">
                            {history.length > 0 ? formatCurrency(Math.round(totalProfit / history.length)) : '0원'}
                        </div>
                    </div>
                </div>

                {/* History Table */}
                <div className="border border-gray-200 rounded-xl overflow-hidden">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="bg-gray-50 border-b border-gray-200 text-sm font-medium text-gray-600">
                                <th className="p-4">일시</th>
                                <th className="p-4">종목명</th>
                                <th className="p-4 text-right">매수/매도가</th>
                                <th className="p-4 text-right">수량</th>
                                <th className="p-4 text-right">수익금(수익률)</th>
                                <th className="p-4 text-center">매도 사유</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                            {history.length === 0 ? (
                                <tr>
                                    <td colSpan={6} className="p-8 text-center text-gray-500 text-sm">
                                        체결된 매매 내역이 없습니다.
                                    </td>
                                </tr>
                            ) : (
                                [...history].reverse().slice(0, 10).map((h, i) => (
                                    <tr key={i} className="hover:bg-gray-50 transition-colors">
                                        <td className="p-4 text-sm text-gray-600">
                                            <div className="font-medium text-gray-800">{h.date}</div>
                                            <div className="text-xs">{h.time}</div>
                                        </td>
                                        <td className="p-4">
                                            <div className="font-bold text-gray-800">{h.stk_nm}</div>
                                            <div className="text-xs text-gray-500 font-mono">{h.stk_cd}</div>
                                        </td>
                                        <td className="p-4 text-right text-sm">
                                            <div className="text-gray-500">매수: {h.buy_price.toLocaleString()}원</div>
                                            <div className="font-medium">매도: {h.sell_price.toLocaleString()}원</div>
                                        </td>
                                        <td className="p-4 text-right font-medium text-gray-700">{h.qty.toLocaleString()}주</td>
                                        <td className="p-4 text-right">
                                            <div className={`font-bold ${h.profit_amount > 0 ? 'text-red-500' : h.profit_amount < 0 ? 'text-blue-500' : 'text-gray-600'}`}>
                                                {h.profit_amount > 0 ? '+' : ''}{h.profit_amount.toLocaleString()}원
                                            </div>
                                            <div className={`text-xs ${h.profit_rate > 0 ? 'text-red-500' : h.profit_rate < 0 ? 'text-blue-500' : 'text-gray-500'}`}>
                                                {h.profit_rate > 0 ? '+' : ''}{(h.profit_rate * 100).toFixed(2)}%
                                            </div>
                                        </td>
                                        <td className="p-4 text-center">
                                            <span className="bg-gray-100 text-gray-600 text-xs px-2 py-1 rounded-full font-medium">
                                                {h.sell_reason === 'Trailing Stop' ? '트레일링 스톱' : h.sell_reason === 'Target Time' ? '목표 시간달성' : '종가 청산'}
                                            </span>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
                {history.length > 10 && (
                    <div className="text-center mt-4 text-sm text-gray-500">
                        최근 10건의 거래 내역만 표시됩니다.
                    </div>
                )}
            </div>
        </div>
    );
}

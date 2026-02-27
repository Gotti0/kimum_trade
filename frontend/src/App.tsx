import { useState, useRef, useEffect, useMemo } from 'react';
import { UploadCloud, File, AlertCircle, Settings, Calculator, MessageSquare, Copy, CheckCircle2, ClipboardPaste, PieChart as PieChartIcon, Zap, TrendingUp, Filter, Flame, BarChart3, Briefcase, Search, Bot } from 'lucide-react';
import { parseMiraeAssetCSV, parseMiraeAssetText } from './utils/csvParser';
import type { SimulationResult, StockPosition } from './types';
import axios from 'axios';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';
import PipelinePanel from './components/PipelinePanel';
import BacktestPanel from './components/BacktestPanel';
import ScreenerPanel from './components/ScreenerPanel';
import MomentumPanel from './components/MomentumPanel';
import PortfolioComparePanel from './components/PortfolioComparePanel';
import AutoTradePanel from './components/AutoTradePanel';

// 포트폴리오 유형별 색상
const ASSET_COLORS: Record<string, string> = {
  '주식': '#10b981',
  '해외주식': '#6366f1',
  'ETF': '#8b5cf6',
  'ETN': '#14b8a6',
  '채권': '#f59e0b',
  'RP': '#64748b',
  '금': '#eab308',
  '기타': '#94a3b8',
};

function PortfolioChart({ results, usdToKrw = 1400 }: { results: SimulationResult[]; usdToKrw?: number }) {
  const chartData = useMemo(() => {
    const byType: Record<string, number> = {};
    results.forEach(r => {
      const type = r.assetType || '기타';
      const rawEval = r.evalAmount || r.averagePrice * r.quantity;
      const evalKRW = r.currency === 'USD' ? rawEval * usdToKrw : rawEval;
      byType[type] = (byType[type] || 0) + evalKRW;
    });
    return Object.entries(byType)
      .map(([name, value]) => ({ name, value: Math.round(value) }))
      .sort((a, b) => b.value - a.value);
  }, [results, usdToKrw]);

  const total = chartData.reduce((s, d) => s + d.value, 0);

  const formatKRW = (n: number) => {
    if (n >= 100000000) return (n / 100000000).toFixed(1) + '억';
    if (n >= 10000) return (n / 10000).toFixed(0) + '만';
    return n.toLocaleString();
  };

  const renderLabel = ({ name, percent }: any) =>
    `${name} ${(percent * 100).toFixed(1)}%`;

  if (results.length === 0) {
    return (
      <div className="bg-white rounded-xl shadow p-12 text-center text-gray-400">
        CSV 파일을 먼저 업로드하세요.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* 원 그래프 */}
      <div className="bg-white rounded-xl shadow p-6">
        <h3 className="text-lg font-semibold text-gray-800 mb-4">자산 유형별 비중</h3>
        <ResponsiveContainer width="100%" height={380}>
          <PieChart>
            <Pie
              data={chartData}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={140}
              innerRadius={60}
              paddingAngle={2}
              label={renderLabel}
              labelLine={{ stroke: '#94a3b8', strokeWidth: 1 }}
            >
              {chartData.map((entry, i) => (
                <Cell key={i} fill={ASSET_COLORS[entry.name] || ASSET_COLORS['기타']} />
              ))}
            </Pie>
            <Tooltip formatter={(val: any) => formatKRW(Number(val)) + '원'} />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* 요약 테이블 */}
      <div className="bg-white rounded-xl shadow p-6">
        <h3 className="text-lg font-semibold text-gray-800 mb-4">유형별 상세</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-gray-500">
              <th className="text-left py-2">유형</th>
              <th className="text-right py-2">평가금액</th>
              <th className="text-right py-2">비중</th>
              <th className="text-right py-2">종목수</th>
            </tr>
          </thead>
          <tbody>
            {chartData.map((d, i) => (
              <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-3 flex items-center gap-2">
                  <span className="w-3 h-3 rounded-full inline-block" style={{ backgroundColor: ASSET_COLORS[d.name] || ASSET_COLORS['기타'] }} />
                  {d.name}
                </td>
                <td className="text-right py-3 font-medium">{formatKRW(d.value)}원</td>
                <td className="text-right py-3 text-blue-600 font-medium">{total > 0 ? ((d.value / total) * 100).toFixed(1) : 0}%</td>
                <td className="text-right py-3 text-gray-500">{results.filter(r => (r.assetType || '기타') === d.name).length}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 font-bold text-gray-800">
              <td className="py-3">합계</td>
              <td className="text-right py-3">{formatKRW(total)}원</td>
              <td className="text-right py-3">100%</td>
              <td className="text-right py-3">{results.length}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function App() {
  const [capital, setCapital] = useState<number>(10000000);
  const [riskPercentage, setRiskPercentage] = useState<number>(3);
  const [atrMultiplier, setAtrMultiplier] = useState<number>(2.0);
  const [results, setResults] = useState<SimulationResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'my-portfolio' | 'stock-analysis' | 'momentum' | 'pipeline' | 'auto-trade'>('my-portfolio');
  const [portfolioSubTab, setPortfolioSubTab] = useState<'dashboard' | 'analysis' | 'prompt' | 'compare'>('dashboard');
  const [analysisSubTab, setAnalysisSubTab] = useState<'screener' | 'backtest'>('screener');
  const [promptCopied, setPromptCopied] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [stockMap, setStockMap] = useState<Record<string, string>>({});
  const [usdToKrw, setUsdToKrw] = useState(1400);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // 앱 시작 시 stock_map.json 로드
  useEffect(() => {
    axios.get('http://localhost:8001/api/stock-map')
      .then(res => setStockMap(res.data || {}))
      .catch(() => console.warn('stock-map 로드 실패, 빈 맵으로 진행'));
  }, []);

  const applyParsedPositions = async (parsedPositions: StockPosition[]) => {
    // Initialize results: 시뮬레이션 대상은 pending, 비대상은 excluded
    const initialResults: SimulationResult[] = parsedPositions.map(pos => ({
      ...pos,
      status: pos.isSimTarget ? 'pending' : 'excluded' as const
    }));
    setResults(initialResults);

    // 평가금액 합산으로 총 자본금 자동 갱신 (달러 자산은 환율 반영)
    const hasUsd = parsedPositions.some(pos => pos.currency === 'USD');
    let fetchedRate = 1400; // fallback
    if (hasUsd) {
      try {
        const res = await fetch('https://api.frankfurter.app/latest?from=USD&to=KRW');
        const data = await res.json();
        fetchedRate = data.rates?.KRW || 1400;
      } catch {
        console.warn('환율 API 호출 실패, 기본값 1400원 적용');
      }
    }
    setUsdToKrw(fetchedRate);

    const totalEval = parsedPositions.reduce((sum, pos) => {
      const evalAmt = pos.evalAmount || 0;
      return sum + (pos.currency === 'USD' ? evalAmt * fetchedRate : evalAmt);
    }, 0);

    if (totalEval > 0) {
      setCapital(Math.round(totalEval));
    }
  };

  const processFile = async (file: File) => {
    setError(null);
    try {
      const parsedPositions = await parseMiraeAssetCSV(file, stockMap);

      if (parsedPositions.length === 0) {
        setError('유효한 주식/해외주식 데이터가 없거나 파일 스키마가 일치하지 않습니다.');
        return;
      }

      applyParsedPositions(parsedPositions);

    } catch (err: any) {
      setError(err.message || '파일 처리 중 오류가 발생했습니다.');
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      await processFile(file);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);

    const file = e.dataTransfer.files?.[0];
    if (file && file.name.toLowerCase().endsWith('.csv')) {
      await processFile(file);
    } else if (file) {
      setError('CSV 파일만 업로드 가능합니다.');
    }
  };

  const handleClipboardPaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!text || text.trim() === '') {
        setError('클립보드에 텍스트가 없습니다.');
        return;
      }
      setError(null);
      const parsedPositions = await parseMiraeAssetText(text, stockMap);

      if (parsedPositions.length === 0) {
        setError('유효한 주식/해외주식 데이터가 없거나 파일 스키마가 일치하지 않습니다.');
        return;
      }

      applyParsedPositions(parsedPositions);
    } catch (err: any) {
      setError(err.message || '클립보드 데이터 처리 중 오류가 발생했습니다. 브라우저의 클립보드 접근 권한을 확인해주세요.');
    }
  };

  const handleSimulate = async () => {
    if (results.length === 0) {
      setError('먼저 CSV 파일을 업로드해주세요.');
      return;
    }

    setIsLoading(true);
    setError(null);

    // 이 예제에서는 백엔드 API 엔드포인트를 호출하는 형태로 구조가 잡혀있습니다.
    // 아직 백엔드가 완성되지 않았으므로 임시로 mock delay 및 로직을 구현합니다.
    try {

      // 시뮬레이션 대상 종목만 백엔드에 전달
      const simTargets = results.filter(r => r.isSimTarget);
      const excludedItems = results.filter(r => !r.isSimTarget);

      if (simTargets.length === 0) {
        setError('시뮬레이션 대상 종목(주식/해외주식)이 없습니다.');
        setIsLoading(false);
        return;
      }

      const response = await axios.post('http://localhost:8001/api/simulate', {
        capital,
        riskPercentage,
        atrMultiplier,
        positions: simTargets
      });

      // 시뮬레이션 결과와 비대상 종목을 합쳐서 표시 (원본 메타데이터 보존)
      const simulatedResults = response.data.data.map((r: any, idx: number) => ({
        ...simTargets[idx],  // 원본의 assetType, evalAmount 등 보존
        ...r,                // 백엔드 계산 결과 덮어쓰기
        isSimTarget: true,
      }));
      setResults([...simulatedResults, ...excludedItems]);

    } catch (err: any) {
      setError('서버 시뮬레이션 요청에 실패했습니다: ' + (err.message || ''));
    } finally {
      setIsLoading(false);
    }
  };

  const formatCurrency = (val?: number, currency: 'KRW' | 'USD' = 'KRW') => {
    if (val === undefined) return '-';
    if (currency === 'USD') {
      return '$' + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    return Math.round(val).toLocaleString() + '원';
  };

  const formatPercentage = (val?: number, base?: number) => {
    if (val === undefined || base === undefined || base === 0) return '-';
    return ((val / base) * 100).toFixed(2) + '%';
  };

  const formatKoreanCurrency = (value: number) => {
    if (!value || value === 0) return '0원';

    const eok = Math.floor(value / 100000000);
    const man = Math.floor((value % 100000000) / 10000);
    const rest = Math.floor(value % 10000);

    let result = '';
    if (eok > 0) result += `${eok.toLocaleString()}억 `;
    if (man > 0) result += `${man.toLocaleString()}만 `;
    if (rest > 0 || (eok === 0 && man === 0)) result += `${rest.toLocaleString()}`;

    return result.trim() + '원';
  };

  const generateAIPrompt = () => {
    if (results.length === 0) return 'CSV 데이터를 먼저 업로드해주세요.';

    // 시뮬레이션 대상 종목
    const simResults = results.filter(r => r.isSimTarget);
    const calculatedSim = simResults.filter(r => r.status === 'calculated');
    const simData = calculatedSim.length > 0 ? calculatedSim : simResults;

    const simSummary = simData.map(r =>
      `- [${r.assetType || '주식'}] ${r.name} (${r.currency}): 보유 ${r.quantity}주, 평균단가 ${formatCurrency(r.averagePrice, r.currency)}` +
      (r.atr ? `, ATR ${formatCurrency(r.atr, r.currency)}, 손절가 ${formatCurrency(r.stopLossPrice, r.currency)}, 예상손실 ${formatCurrency(r.riskAmount, 'KRW')}` : '')
    ).join('\n');

    // 비시뮬레이션 대상 종목
    const excludedResults = results.filter(r => !r.isSimTarget);
    const excludedSummary = excludedResults.length > 0
      ? excludedResults.map(r =>
        `- [${r.assetType || '기타'}] ${r.name} (${r.currency}): 평가금액 ${formatCurrency(r.evalAmount, r.currency)}`
      ).join('\n')
      : '(없음)';

    return `당신은 CFA 자격을 보유한 포트폴리오 리스크 관리 전문가입니다.
아래의 내 전체 포트폴리오 데이터를 분석하고, 최상의 관리 전략을 도출해주세요.

[계좌 기본 설정]
- 총 자본금 (전체 자산 합산): ${formatKoreanCurrency(capital)}
- 계좌 허용 최대 리스크 비율: ${riskPercentage}%
- ATR 배수 (손절가 계산): ${atrMultiplier}×

[시뮬레이션 대상 종목 (주식/해외주식/ETF)]
${simSummary}

[비시뮬레이션 대상 자산 (채권/RP/펀드 등)]
${excludedSummary}

[분석 요청]
1. 위험자산(주식+ETF) 포지션별 리스크가 허용 한도(${riskPercentage}%)를 초과하지 않는지 평가해 주세요.
2. 각 종목의 ATR 기반 손절가가 합리적인 폭인지 코멘트해 주세요.
3. 포트폴리오 전체에서 위험자산과 안전자산(채권/RP)의 비중 배분이 적절한지 평가해 주세요.
4. 리스크를 줄이기 위해 비중 조절이 필요한 종목이 있다면 우선순위와 함께 제안해 주세요.`;
  };

  const copyToClipboard = () => {
    navigator.clipboard.writeText(generateAIPrompt());
    setPromptCopied(true);
    setTimeout(() => setPromptCopied(false), 2000);
  };

  return (
    <div className="min-h-screen bg-gray-50 p-8 font-sans">
      <div className="max-w-6xl mx-auto space-y-6">

        {/* Header */}
        <div>
          <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-2">
            <Calculator className="w-8 h-8 text-blue-600" />
            손절매 시뮬레이터
          </h1>
          <p className="text-gray-500 mt-2">미래에셋 잔고 CSV를 업로드하고 ATR 기반 최적의 손절 라인을 계산하세요.</p>
        </div>

        {/* Main Tabs */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-1.5 mb-6">
          <div className="flex flex-wrap gap-1.5">
            {([
              { key: 'my-portfolio' as const, label: '내 포트폴리오', icon: <Briefcase className="w-4 h-4" />, color: 'blue' },
              { key: 'stock-analysis' as const, label: '종목 분석', icon: <Search className="w-4 h-4" />, color: 'emerald' },
              { key: 'momentum' as const, label: '모멘텀', icon: <Flame className="w-4 h-4" />, color: 'amber' },
              { key: 'pipeline' as const, label: '파이프라인', icon: <Zap className="w-4 h-4" />, color: 'violet' },
              { key: 'auto-trade' as const, label: '자동매매', icon: <Bot className="w-4 h-4" />, color: 'indigo' },
            ] as const).map(tab => {
              const isActive = activeTab === tab.key;
              const colorMap: Record<string, { active: string; hover: string }> = {
                blue: { active: 'bg-blue-600 text-white shadow-md', hover: 'hover:bg-blue-50 text-gray-600' },
                emerald: { active: 'bg-emerald-600 text-white shadow-md', hover: 'hover:bg-emerald-50 text-gray-600' },
                amber: { active: 'bg-amber-500 text-white shadow-md', hover: 'hover:bg-amber-50 text-gray-600' },
                violet: { active: 'bg-violet-600 text-white shadow-md', hover: 'hover:bg-violet-50 text-gray-600' },
                indigo: { active: 'bg-indigo-600 text-white shadow-md', hover: 'hover:bg-indigo-50 text-gray-600' },
              };
              const colors = colorMap[tab.color] || colorMap.blue;
              return (
                <button
                  key={tab.key}
                  className={`py-2.5 px-5 rounded-lg font-medium text-sm transition-all flex items-center gap-2 whitespace-nowrap ${isActive ? colors.active : colors.hover
                    }`}
                  onClick={() => setActiveTab(tab.key)}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Tab Content: 내 포트폴리오 */}
        <div className={activeTab === 'my-portfolio' ? 'space-y-6 block' : 'hidden'}>
          {/* Sub Tabs */}
          <div className="flex gap-1 border-b border-gray-200 pb-0">
            {([
              { key: 'dashboard' as const, label: '대시보드', icon: <Calculator className="w-3.5 h-3.5" /> },
              { key: 'analysis' as const, label: '자산 분석', icon: <PieChartIcon className="w-3.5 h-3.5" /> },
              { key: 'prompt' as const, label: 'AI 프롬프트', icon: <MessageSquare className="w-3.5 h-3.5" /> },
              { key: 'compare' as const, label: '비교분석', icon: <BarChart3 className="w-3.5 h-3.5" /> },
            ] as const).map(sub => (
              <button
                key={sub.key}
                className={`py-2 px-4 text-sm font-medium transition-colors border-b-2 flex items-center gap-1.5 -mb-px ${portfolioSubTab === sub.key
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                onClick={() => setPortfolioSubTab(sub.key)}
              >
                {sub.icon}
                {sub.label}
              </button>
            ))}
          </div>

          {/* Sub: 대시보드 */}
          <div className={portfolioSubTab === 'dashboard' ? 'space-y-6' : 'hidden'}>
            {/* Top Controls Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

              {/* Settings Panel */}
              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                <div className="flex items-center gap-2 mb-4">
                  <Settings className="w-5 h-5 text-gray-600" />
                  <h2 className="text-lg font-semibold text-gray-800">계좌 기본 설정</h2>
                </div>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">총 자본금 (Total Capital)</label>
                    <div className="relative">
                      <input
                        type="number"
                        className="w-full border border-gray-300 rounded-lg pl-4 pr-12 py-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={capital}
                        onChange={(e) => setCapital(Number(e.target.value))}
                      />
                      <span className="absolute right-4 top-2.5 text-gray-500">원</span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1.5 ml-1 text-right">
                      {formatKoreanCurrency(capital)}
                    </p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">허용 손실 비율 (Risk %)</label>
                    <div className="relative">
                      <input
                        type="number"
                        step="0.1"
                        className="w-full border border-gray-300 rounded-lg pl-4 pr-12 py-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={riskPercentage}
                        onChange={(e) => setRiskPercentage(Number(e.target.value))}
                      />
                      <span className="absolute right-4 top-2.5 text-gray-500">%</span>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">ATR 배수 (Stop-loss Multiplier)</label>
                    <div className="relative">
                      <input
                        type="number"
                        step="0.5"
                        min="1"
                        max="5"
                        className="w-full border border-gray-300 rounded-lg pl-4 pr-12 py-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={atrMultiplier}
                        onChange={(e) => setAtrMultiplier(Number(e.target.value))}
                      />
                      <span className="absolute right-4 top-2.5 text-gray-500">×</span>
                    </div>
                    <p className="text-xs text-gray-400 mt-1 ml-1">손절가 = 평균단가 − ({atrMultiplier} × ATR)</p>
                  </div>
                </div>
              </div>

              {/* Upload Panel */}
              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex flex-col justify-center">
                <input
                  type="file"
                  accept=".csv"
                  className="hidden"
                  ref={fileInputRef}
                  onChange={handleFileUpload}
                />

                <div className="flex gap-4 h-full pt-4">
                  <div
                    className={`flex-1 border-2 border-dashed rounded-xl p-4 text-center cursor-pointer transition-all flex flex-col items-center justify-center gap-2 ${isDragging ? 'bg-blue-100 border-blue-500' : 'border-gray-300 hover:bg-blue-50 hover:border-blue-400'
                      }`}
                    onClick={() => fileInputRef.current?.click()}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                  >
                    <UploadCloud className={`w-8 h-8 ${isDragging ? 'text-blue-500' : 'text-gray-400'}`} />
                    <div>
                      <p className="font-medium text-gray-700 text-sm">업로드</p>
                      <p className="text-xs text-gray-500 mt-1">
                        {isDragging ? '여기에 놓으세요' : '파일 선택 또는 드래그'}
                      </p>
                    </div>
                  </div>
                  <div
                    className="flex-1 border-2 border-dashed border-gray-300 rounded-xl p-4 text-center cursor-pointer hover:bg-green-50 hover:border-green-400 transition-all flex flex-col items-center justify-center gap-2"
                    onClick={handleClipboardPaste}
                  >
                    <ClipboardPaste className="w-8 h-8 text-green-500" />
                    <div>
                      <p className="font-medium text-gray-700 text-sm">붙여넣기</p>
                      <p className="text-xs text-gray-500 mt-1">클립보드 복사본</p>
                    </div>
                  </div>
                </div>
                <p className="text-xs text-center text-gray-500 mt-4">미래에셋 잔고 CSV 파일 또는 복사한 텍스트를 입력하세요.</p>
              </div>
            </div>

            {/* Error Alert */}
            {error && (
              <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-md flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-500 mt-0.5" />
                <p className="text-red-700">{error}</p>
              </div>
            )}

            {/* Results Area */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
                <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                  <File className="w-5 h-5 text-gray-600" />
                  보유 종목 ({results.length}건 · 시뮬레이션 대상 {results.filter(r => r.isSimTarget).length}건)
                </h2>
                <button
                  onClick={handleSimulate}
                  disabled={isLoading || results.length === 0}
                  className={`px - 6 py - 2 rounded - lg font - medium text - white transition - all shadow - sm
                ${isLoading || results.length === 0
                      ? 'bg-blue-300 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700 active:scale-95'
                    } `}
                >
                  {isLoading ? '계산 중...' : '손절가/ATR 계산 실행'}
                </button>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200 text-sm font-medium text-gray-600 uppercase tracking-wider">
                      <th className="p-4">종목명</th>
                      <th className="p-4 text-center">유형</th>
                      <th className="p-4 text-right">보유수량</th>
                      <th className="p-4 text-right">평균매수단가</th>
                      <th className="p-4 text-right bg-blue-50/50">변동성 (ATR)</th>
                      <th className="p-4 text-right bg-red-50/50">추천 손절가</th>
                      <th className="p-4 text-right bg-red-50/50">예상 손실액</th>
                      <th className="p-4 text-center">상태</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {results.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="p-8 text-center text-gray-500">
                          업로드된 데이터가 없습니다. CSV 파일을 추가해주세요.
                        </td>
                      </tr>
                    ) : (
                      results.map((row, idx) => (
                        <tr key={idx} className={`transition-colors ${row.isSimTarget ? 'hover:bg-gray-50' : 'bg-gray-50/70 text-gray-400 italic'}`}>
                          <td className={`p-4 font-medium ${row.isSimTarget ? 'text-gray-800' : 'text-gray-500'}`}>
                            {row.name}
                            {row.currency === 'USD' && row.isSimTarget && (
                              <div className="mt-1 flex items-center gap-1">
                                <input
                                  type="text"
                                  placeholder="티커 입력 (예: EQIX)"
                                  className={`w-28 px-2 py-0.5 text-xs border rounded uppercase placeholder:normal-case focus:ring-1 outline-none ${row.ticker
                                    ? 'border-indigo-200 bg-indigo-50/50 text-indigo-700 focus:ring-indigo-400'
                                    : 'border-orange-300 bg-orange-50/50 text-orange-700 focus:ring-orange-400'
                                    }`}
                                  value={row.ticker || ''}
                                  onChange={(e) => {
                                    const updated = [...results];
                                    updated[idx] = { ...updated[idx], ticker: e.target.value.toUpperCase() };
                                    setResults(updated);
                                  }}
                                  onBlur={(e) => {
                                    const val = e.target.value.trim();
                                    if (val) {
                                      axios.post('http://localhost:8001/api/stock-map/update', {
                                        name: row.name, ticker: val
                                      }).then(() => {
                                        setStockMap(prev => ({ ...prev, [row.name]: val }));
                                      }).catch(() => console.warn('티커 저장 실패'));
                                    }
                                  }}
                                />
                                {!row.ticker && <span className="text-[10px] text-orange-400">⚠ 필수</span>}
                                {row.ticker && <span className="text-[10px] text-green-500">✓ 저장됨</span>}
                              </div>
                            )}
                          </td>
                          <td className="p-4 text-center">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${row.assetType === '해외주식' ? 'bg-indigo-100 text-indigo-700' :
                              row.assetType === '주식' ? 'bg-emerald-100 text-emerald-700' :
                                'bg-gray-100 text-gray-600'
                              }`}>{row.assetType || '-'}</span>
                          </td>
                          <td className="p-4 text-right text-gray-600">{row.quantity.toLocaleString()}{row.isSimTarget ? '주' : ''}</td>
                          <td className="p-4 text-right text-gray-600">{row.isSimTarget ? formatCurrency(row.averagePrice, row.currency) : formatCurrency(row.evalAmount, row.currency)}</td>
                          <td className="p-4 text-right font-medium text-blue-600 bg-blue-50/10">
                            {row.isSimTarget ? formatCurrency(row.atr, row.currency) : '-'}
                            {row.isSimTarget && row.atr && <span className="text-xs text-blue-400 block">{formatPercentage(row.atr, row.averagePrice)}</span>}
                          </td>
                          <td className="p-4 text-right font-bold text-red-600 bg-red-50/10">
                            {row.isSimTarget ? formatCurrency(row.stopLossPrice, row.currency) : '-'}
                            {row.isSimTarget && row.stopLossPrice && <span className="text-xs text-red-400 block">{formatPercentage(row.stopLossPrice - row.averagePrice, row.averagePrice)}</span>}
                          </td>
                          <td className="p-4 text-right text-red-500 bg-red-50/10">
                            {row.isSimTarget ? formatCurrency(row.riskAmount, 'KRW') : '-'}
                            {row.isSimTarget && row.riskAmount && (
                              <>
                                <span className="text-xs text-red-400 block">전체계좌 {formatPercentage(row.riskAmount, capital)}</span>
                                <span className="text-xs text-orange-500 block">위험자산 {formatPercentage(row.riskAmount, results.filter(r => r.isSimTarget).reduce((s, r) => s + (r.evalAmount || r.averagePrice * r.quantity), 0))}</span>
                              </>
                            )}
                          </td>
                          <td className="p-4 text-center">
                            {row.status === 'pending' && <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-800">대기중</span>}
                            {row.status === 'calculated' && <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800">완료</span>}
                            {row.status === 'error' && <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800" title={row.errorMessage}>오류</span>}
                            {row.status === 'excluded' && <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700">비대상</span>}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Sub: 자산 분석 */}
          {portfolioSubTab === 'analysis' && (
            <PortfolioChart results={results} usdToKrw={usdToKrw} />
          )}

          {/* Sub: AI 프롬프트 */}
          {portfolioSubTab === 'prompt' && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
                <div>
                  <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                    <MessageSquare className="w-5 h-5 text-gray-600" />
                    포트폴리오 분석용 AI 프롬프트
                  </h2>
                  <p className="text-sm text-gray-500 mt-1">업로드/계산된 데이터를 바탕으로 ChatGPT, Claude 등을 위한 프롬프트를 자동 생성합니다.</p>
                </div>
                <button
                  onClick={copyToClipboard}
                  disabled={results.length === 0}
                  className={`flex items-center gap-2 px-5 py-2 rounded-lg font-medium text-white transition-all shadow-sm
                  ${results.length === 0
                      ? 'bg-blue-300 cursor-not-allowed'
                      : promptCopied ? 'bg-green-600 hover:bg-green-700' : 'bg-blue-600 hover:bg-blue-700 active:scale-95'}`}
                >
                  {promptCopied ? (
                    <><CheckCircle2 className="w-4 h-4" /> 복사 완료!</>
                  ) : (
                    <><Copy className="w-4 h-4" /> 프롬프트 복사</>
                  )}
                </button>
              </div>

              <div className="p-6 bg-gray-800 text-gray-100 font-mono text-sm leading-relaxed whitespace-pre-wrap overflow-y-auto max-h-[500px]">
                {generateAIPrompt()}
              </div>
            </div>
          )}

          {/* Sub: 비교분석 */}
          {portfolioSubTab === 'compare' && (
            <PortfolioComparePanel
              positions={results.map(r => ({
                name: r.name,
                quantity: r.quantity,
                averagePrice: r.averagePrice,
                currency: r.currency,
                evalAmount: r.evalAmount,
                assetType: r.assetType,
                isSimTarget: r.isSimTarget,
                ticker: r.ticker,
              }))}
              capital={capital}
              usdToKrw={usdToKrw}
              stockMap={stockMap}
            />
          )}
        </div>

        {/* Tab Content: 종목 분석 */}
        {activeTab === 'stock-analysis' && (
          <div className="space-y-6">
            {/* Sub Tabs */}
            <div className="flex gap-1 border-b border-gray-200 pb-0">
              {([
                { key: 'screener' as const, label: '스크리너', icon: <Filter className="w-3.5 h-3.5" /> },
                { key: 'backtest' as const, label: '백테스트', icon: <TrendingUp className="w-3.5 h-3.5" /> },
              ] as const).map(sub => (
                <button
                  key={sub.key}
                  className={`py-2 px-4 text-sm font-medium transition-colors border-b-2 flex items-center gap-1.5 -mb-px ${analysisSubTab === sub.key
                      ? 'border-emerald-600 text-emerald-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                    }`}
                  onClick={() => setAnalysisSubTab(sub.key)}
                >
                  {sub.icon}
                  {sub.label}
                </button>
              ))}
            </div>

            {analysisSubTab === 'screener' && <ScreenerPanel />}
            {analysisSubTab === 'backtest' && <BacktestPanel />}
          </div>
        )}

        {/* Tab Content: 모멘텀 */}
        {activeTab === 'momentum' && (
          <MomentumPanel />
        )}

        {/* Tab Content: 파이프라인 */}
        {activeTab === 'pipeline' && (
          <PipelinePanel />
        )}

        {/* Tab Content: 자동매매 */}
        {activeTab === 'auto-trade' && (
          <AutoTradePanel />
        )}

      </div>
    </div>
  )
}

export default App

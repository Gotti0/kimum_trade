/**
 * PortfolioComparePanel — 포트폴리오 비교분석 통합 대시보드
 *
 * CSV 보유 포트폴리오와 스크리너 타겟을 비교하여
 * 정량 GAP 시각화 + AI 프롬프트 생성(클립보드 복사) 기능을 제공합니다.
 *
 * 구조:
 *   5-1. 스크리너 결과 로드 (국내 + 글로벌)
 *   5-2. GAP 계산 (useMemo)
 *   5-3. 국내 GAP 섹션  — 매칭/과잉/미보유 테이블 + 섹터 차트
 *   5-4. 글로벌 GAP 섹션 — ETF 매칭 + 카테고리 배분 괴리
 *   5-5. 프롬프트 패널   — 3개 탭 + 전체 복사
 */

import { useState, useEffect, useMemo } from 'react';
import {
    BarChart3, Copy, CheckCircle2, AlertCircle, ChevronDown, ChevronUp,
    RefreshCw, ArrowUpDown, ArrowUp, ArrowDown, Minus, Info, Globe, Flag,
} from 'lucide-react';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
    ReferenceLine, Legend,
} from 'recharts';

import type {
    StockPosition,
    ScreenerResult,
    GlobalScreenerResult,
    PortfolioGap,
    CombinedGapResult,
    MatchedStock,
    MissingTarget,
    CategoryGap,
} from '../types';
import { analyzeKrGap, analyzeGlobalGap, combinedGap, gapSummary } from '../utils/gapAnalyzer';
import { PROMPT_TABS } from '../utils/promptBuilder';

// ═══════════════════════════════════════════════════
//  Constants
// ═══════════════════════════════════════════════════

const API = 'http://localhost:8001/api/pipeline';

const ACTION_STYLES = {
    hold: { label: '유지', bg: 'bg-gray-100', text: 'text-gray-600', icon: Minus },
    increase: { label: '▲ 확대', bg: 'bg-blue-100', text: 'text-blue-700', icon: ArrowUp },
    decrease: { label: '▼ 축소', bg: 'bg-red-100', text: 'text-red-700', icon: ArrowDown },
} as const;

// ═══════════════════════════════════════════════════
//  Formatters
// ═══════════════════════════════════════════════════

const formatPct = (n: number, digits = 2) => `${(n * 100).toFixed(digits)}%`;

const formatKRW = (n: number) => {
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1_0000_0000) return `${sign}${(abs / 1_0000_0000).toFixed(1)}억`;
    if (abs >= 1_0000) return `${sign}${(abs / 1_0000).toFixed(0)}만`;
    return `${sign}${abs.toLocaleString()}`;
};

const formatScore = (s: number | undefined) => (s != null ? s.toFixed(2) : '-');

// ═══════════════════════════════════════════════════
//  Props
// ═══════════════════════════════════════════════════

interface PortfolioComparePanelProps {
    positions: StockPosition[];
    capital: number;
    usdToKrw: number;
    stockMap: Record<string, string>;
}

// ═══════════════════════════════════════════════════
//  Sub-components
// ═══════════════════════════════════════════════════

/** 요약 카드 */
function SummaryCard({ label, value, sub, color = 'blue' }: {
    label: string; value: string | number; sub?: string; color?: string;
}) {
    const colorMap: Record<string, string> = {
        blue: 'bg-blue-50 border-blue-200 text-blue-700',
        green: 'bg-green-50 border-green-200 text-green-700',
        red: 'bg-red-50 border-red-200 text-red-700',
        amber: 'bg-amber-50 border-amber-200 text-amber-700',
        gray: 'bg-gray-50 border-gray-200 text-gray-600',
    };
    return (
        <div className={`rounded-lg border p-4 ${colorMap[color] ?? colorMap.blue}`}>
            <div className="text-xs font-medium opacity-70">{label}</div>
            <div className="text-2xl font-bold mt-1">{value}</div>
            {sub && <div className="text-xs mt-1 opacity-60">{sub}</div>}
        </div>
    );
}

/** 접기/펼치기 섹션 */
function CollapsibleSection({ title, icon, badge, defaultOpen = true, children }: {
    title: string; icon: React.ReactNode; badge?: string | number; defaultOpen?: boolean; children: React.ReactNode;
}) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="border border-gray-200 rounded-lg overflow-hidden">
            <button
                className="w-full flex items-center justify-between p-4 bg-gray-50 hover:bg-gray-100 transition-colors"
                onClick={() => setOpen(!open)}
            >
                <div className="flex items-center gap-2 font-semibold text-gray-800">
                    {icon}
                    {title}
                    {badge !== undefined && (
                        <span className="ml-2 px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-medium">{badge}</span>
                    )}
                </div>
                {open ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
            </button>
            {open && <div className="p-4">{children}</div>}
        </div>
    );
}

/** 매칭 종목 테이블 */
function MatchedTable({ matched, mode }: { matched: MatchedStock[]; mode: 'kr' | 'global' }) {
    const [sortKey, setSortKey] = useState<'weightGap' | 'momentumScore' | 'name'>('weightGap');
    const [sortAsc, setSortAsc] = useState(false);

    const toggleSort = (key: typeof sortKey) => {
        if (sortKey === key) setSortAsc(!sortAsc);
        else { setSortKey(key); setSortAsc(false); }
    };

    const sorted = useMemo(() => {
        const arr = [...matched];
        arr.sort((a, b) => {
            let cmp = 0;
            if (sortKey === 'weightGap') cmp = Math.abs(b.weightGap) - Math.abs(a.weightGap);
            else if (sortKey === 'momentumScore') cmp = (b.momentumScore ?? 0) - (a.momentumScore ?? 0);
            else cmp = a.name.localeCompare(b.name);
            return sortAsc ? -cmp : cmp;
        });
        return arr;
    }, [matched, sortKey, sortAsc]);

    if (matched.length === 0) {
        return <p className="text-gray-400 text-sm text-center py-6">매칭된 종목이 없습니다.</p>;
    }

    const SortHeader = ({ label, field }: { label: string; field: typeof sortKey }) => (
        <th
            className="text-left py-2 px-3 cursor-pointer select-none hover:text-blue-600 transition-colors"
            onClick={() => toggleSort(field)}
        >
            <span className="inline-flex items-center gap-1">
                {label}
                {sortKey === field && <ArrowUpDown className="w-3 h-3" />}
            </span>
        </th>
    );

    return (
        <div className="overflow-x-auto">
            <table className="w-full text-sm">
                <thead>
                    <tr className="border-b text-gray-500 text-xs">
                        <SortHeader label={mode === 'kr' ? '종목명' : 'ETF명'} field="name" />
                        <th className="text-left py-2 px-3">{mode === 'kr' ? '섹터' : '카테고리'}</th>
                        <th className="text-right py-2 px-3">보유비중</th>
                        <th className="text-right py-2 px-3">타겟비중</th>
                        <SortHeader label="괴리" field="weightGap" />
                        <SortHeader label="스코어" field="momentumScore" />
                        <th className="text-center py-2 px-3">조치</th>
                        <th className="text-right py-2 px-3">조정금액</th>
                    </tr>
                </thead>
                <tbody>
                    {sorted.map((m) => {
                        const style = ACTION_STYLES[m.action];
                        return (
                            <tr key={m.ticker} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
                                <td className="py-2.5 px-3 font-medium text-gray-800">{m.name}</td>
                                <td className="py-2.5 px-3">
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-600">
                                        {m.sector ?? '미분류'}
                                    </span>
                                </td>
                                <td className="py-2.5 px-3 text-right">{formatPct(m.actualWeight)}</td>
                                <td className="py-2.5 px-3 text-right">{formatPct(m.targetWeight)}</td>
                                <td className={`py-2.5 px-3 text-right font-medium ${m.weightGap > 0 ? 'text-red-600' : m.weightGap < 0 ? 'text-blue-600' : 'text-gray-500'}`}>
                                    {m.weightGap > 0 ? '+' : ''}{formatPct(m.weightGap)}
                                </td>
                                <td className="py-2.5 px-3 text-right text-gray-600">{formatScore(m.momentumScore)}</td>
                                <td className="py-2.5 px-3 text-center">
                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${style.bg} ${style.text}`}>
                                        {style.label}
                                    </span>
                                </td>
                                <td className="py-2.5 px-3 text-right text-gray-600">{formatKRW(m.adjustAmount)}원</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

/** 과잉보유 종목 리스트 */
function OverHoldingsList({ holdings }: { holdings: StockPosition[] }) {
    if (holdings.length === 0) {
        return <p className="text-gray-400 text-sm text-center py-4">과잉보유 종목이 없습니다.</p>;
    }
    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {holdings.map((p, i) => (
                <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-red-50 border border-red-100">
                    <div>
                        <span className="font-medium text-red-800 text-sm">{p.name}</span>
                        <span className="ml-2 text-xs text-red-500">{p.currency}</span>
                    </div>
                    {p.evalAmount != null && (
                        <span className="text-xs text-red-600 font-medium">{formatKRW(p.evalAmount)}원</span>
                    )}
                </div>
            ))}
        </div>
    );
}

/** 미보유 타겟 리스트 */
function MissingTargetsList({ targets }: { targets: MissingTarget[] }) {
    if (targets.length === 0) {
        return <p className="text-gray-400 text-sm text-center py-4">모든 타겟 종목을 보유 중입니다.</p>;
    }
    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {targets.map((t, i) => (
                <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-blue-50 border border-blue-100">
                    <div>
                        <span className="font-medium text-blue-800 text-sm">{t.name}</span>
                        {t.sector && (
                            <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] bg-blue-100 text-blue-600">{t.sector}</span>
                        )}
                    </div>
                    <div className="text-right">
                        <div className="text-xs text-blue-600 font-medium">{formatPct(t.weight)}</div>
                        {t.score != null && <div className="text-[10px] text-blue-400">스코어 {formatScore(t.score)}</div>}
                    </div>
                </div>
            ))}
        </div>
    );
}

/** 카테고리 배분 괴리 차트 */
function CategoryGapChart({ gaps, label }: { gaps: Record<string, CategoryGap>; label: string }) {
    const data = useMemo(() =>
        Object.entries(gaps)
            .map(([name, g]) => ({
                name,
                actual: +(g.actual * 100).toFixed(2),
                target: +(g.target * 100).toFixed(2),
                gap: +(g.gap * 100).toFixed(2),
            }))
            .sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap)),
        [gaps],
    );

    if (data.length === 0) {
        return <p className="text-gray-400 text-sm text-center py-4">데이터가 없습니다.</p>;
    }

    return (
        <div className="space-y-4">
            <h4 className="text-sm font-semibold text-gray-700">{label} 배분 괴리</h4>
            <ResponsiveContainer width="100%" height={Math.max(200, data.length * 40)}>
                <BarChart data={data} layout="vertical" margin={{ left: 80, right: 30 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" unit="%" tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={75} />
                    <Tooltip
                        formatter={(val, name) => {
                            const v = typeof val === 'number' ? val : Number(val);
                            return [`${v.toFixed(2)}%`, name === 'actual' ? '실제' : '타겟'];
                        }}
                        contentStyle={{ fontSize: 12 }}
                    />
                    <Legend
                        formatter={(value: string) => (value === 'actual' ? '실제비중' : '타겟비중')}
                        wrapperStyle={{ fontSize: 12 }}
                    />
                    <ReferenceLine x={0} stroke="#94a3b8" />
                    <Bar dataKey="actual" fill="#3b82f6" radius={[0, 4, 4, 0]} barSize={14} />
                    <Bar dataKey="target" fill="#10b981" radius={[0, 4, 4, 0]} barSize={14} />
                </BarChart>
            </ResponsiveContainer>

            {/* 괴리 요약 테이블 */}
            <div className="overflow-x-auto">
                <table className="w-full text-xs">
                    <thead>
                        <tr className="border-b text-gray-500">
                            <th className="text-left py-1.5 px-2">카테고리</th>
                            <th className="text-right py-1.5 px-2">실제</th>
                            <th className="text-right py-1.5 px-2">타겟</th>
                            <th className="text-right py-1.5 px-2">괴리</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.map((d) => (
                            <tr key={d.name} className="border-b border-gray-50">
                                <td className="py-1.5 px-2 font-medium text-gray-700">{d.name}</td>
                                <td className="py-1.5 px-2 text-right">{d.actual.toFixed(2)}%</td>
                                <td className="py-1.5 px-2 text-right">{d.target.toFixed(2)}%</td>
                                <td className={`py-1.5 px-2 text-right font-medium ${d.gap > 0 ? 'text-red-600' : d.gap < 0 ? 'text-blue-600' : 'text-gray-500'}`}>
                                    {d.gap > 0 ? '+' : ''}{d.gap.toFixed(2)}%
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

/** 개별 GAP 섹션 (국내 or 글로벌) */
function GapSection({ gap }: { gap: PortfolioGap }) {
    const summary = useMemo(() => gapSummary(gap), [gap]);
    const isKr = gap.mode === 'kr';
    const flagIcon = isKr
        ? <Flag className="w-4 h-4 text-red-500" />
        : <Globe className="w-4 h-4 text-blue-500" />;
    const sectionLabel = isKr ? '🇰🇷 국내 듀얼모멘텀 비교' : '🌍 글로벌 멀티에셋 비교';
    const catLabel = isKr ? 'WICS 섹터' : '자산군 카테고리';

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                    {flagIcon} {sectionLabel}
                </h3>
                <span className="text-xs text-gray-400">타겟: {gap.targetLabel}</span>
            </div>

            {/* 요약 카드 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                <SummaryCard label="매칭 종목" value={summary.totalMatched} color="blue" />
                <SummaryCard label="과잉보유" value={summary.totalOver} color="red" />
                <SummaryCard label="미보유 타겟" value={summary.totalMissing} color="amber" />
                <SummaryCard label="유지" value={summary.holdCount} color="gray" />
                <SummaryCard label="비중 확대" value={summary.increaseCount} color="blue" />
                <SummaryCard label="비중 축소" value={summary.decreaseCount} color="red" />
            </div>

            {/* 매칭 종목 테이블 */}
            <CollapsibleSection
                title={`매칭 종목 (보유 ∩ 타겟)`}
                icon={<BarChart3 className="w-4 h-4" />}
                badge={summary.totalMatched}
            >
                <MatchedTable matched={gap.matched} mode={gap.mode} />
            </CollapsibleSection>

            {/* 과잉보유 */}
            <CollapsibleSection
                title="과잉보유 (보유 O / 타겟 X)"
                icon={<ArrowDown className="w-4 h-4 text-red-500" />}
                badge={summary.totalOver}
                defaultOpen={summary.totalOver > 0}
            >
                <OverHoldingsList holdings={gap.overHoldings} />
            </CollapsibleSection>

            {/* 미보유 타겟 */}
            <CollapsibleSection
                title="미보유 타겟 (보유 X / 타겟 O)"
                icon={<ArrowUp className="w-4 h-4 text-blue-500" />}
                badge={summary.totalMissing}
                defaultOpen={summary.totalMissing > 0}
            >
                <MissingTargetsList targets={gap.missingTargets} />
            </CollapsibleSection>

            {/* 카테고리 배분 괴리 차트 */}
            <CollapsibleSection
                title={`${catLabel} 배분 괴리`}
                icon={<BarChart3 className="w-4 h-4 text-amber-500" />}
                badge={Object.keys(gap.categoryGaps).length}
            >
                <CategoryGapChart gaps={gap.categoryGaps} label={catLabel} />
            </CollapsibleSection>
        </div>
    );
}

/** 프롬프트 패널 */
function PromptPanel({ gapResult }: { gapResult: CombinedGapResult }) {
    const [activePromptTab, setActivePromptTab] = useState<'gap' | 'semantic' | 'action' | 'full'>('gap');
    const [copied, setCopied] = useState(false);

    const activeTabMeta = useMemo(() =>
        PROMPT_TABS.find((t) => t.key === activePromptTab) ?? PROMPT_TABS[0],
        [activePromptTab],
    );

    const promptText = useMemo(() => activeTabMeta.builder(gapResult), [activeTabMeta, gapResult]);
    const promptCharCount = promptText.length;
    const isPromptLong = promptCharCount > 12_000;

    const handleCopy = () => {
        navigator.clipboard.writeText(promptText);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="border border-gray-200 rounded-lg overflow-hidden">
            {/* 헤더 */}
            <div className="flex items-center justify-between p-4 bg-gray-50 border-b border-gray-200">
                <div>
                    <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                        📋 AI 비교분석 프롬프트
                    </h3>
                    <p className="text-xs text-gray-500 mt-1">
                        GAP 데이터가 자동 주입된 프롬프트를 ChatGPT/Claude에 붙여넣어 분석하세요.
                    </p>
                </div>
                <button
                    onClick={handleCopy}
                    className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-white text-sm transition-all shadow-sm ${
                        copied
                            ? 'bg-green-600 hover:bg-green-700'
                            : 'bg-blue-600 hover:bg-blue-700 active:scale-95'
                    }`}
                >
                    {copied ? (
                        <><CheckCircle2 className="w-4 h-4" /> 복사 완료!</>
                    ) : (
                        <><Copy className="w-4 h-4" /> 프롬프트 복사</>
                    )}
                </button>
            </div>

            {/* 프롬프트 탭 */}
            <div className="flex border-b border-gray-200 bg-white">
                {PROMPT_TABS.map((tab) => (
                    <button
                        key={tab.key}
                        className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
                            activePromptTab === tab.key
                                ? 'border-blue-600 text-blue-600'
                                : 'border-transparent text-gray-500 hover:text-gray-700'
                        }`}
                        onClick={() => setActivePromptTab(tab.key)}
                        title={tab.description}
                    >
                        <span>{tab.icon}</span>
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* 프롬프트 설명 + 문자수 */}
            <div className="px-4 py-2 bg-blue-50 border-b border-blue-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Info className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
                    <span className="text-xs text-blue-600">{activeTabMeta.description}</span>
                </div>
                <span className={`text-xs font-mono ${isPromptLong ? 'text-amber-600 font-semibold' : 'text-gray-400'}`}>
                    {promptCharCount.toLocaleString()}자
                    {isPromptLong && ' ⚠️ 프롬프트가 깁니다'}
                </span>
            </div>

            {/* 프롬프트 미리보기 */}
            <div className="p-4 bg-gray-800 text-gray-100 font-mono text-xs leading-relaxed whitespace-pre-wrap overflow-y-auto max-h-[500px]">
                {promptText}
            </div>
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  Main Component
// ═══════════════════════════════════════════════════

export default function PortfolioComparePanel({ positions, capital, usdToKrw, stockMap }: PortfolioComparePanelProps) {
    // ── 스크리너 결과 상태 ──
    const [krScreener, setKrScreener] = useState<ScreenerResult | null>(null);
    const [globalScreener, setGlobalScreener] = useState<GlobalScreenerResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    // ── 스크리너 결과 로드 ──
    const fetchScreenerResults = async () => {
        setLoading(true);
        setLoadError(null);

        try {
            // 백엔드 API는 { status, data } 래퍼로 감싸서 반환
            type ApiWrapper<T> = { status: string; data: T | null };

            const results = await Promise.allSettled([
                axios.get<ApiWrapper<ScreenerResult>>(`${API}/momentum-screener/result`),
                axios.get<ApiWrapper<GlobalScreenerResult>>(`${API}/global-screener/result`),
            ]);

            const [krRes, globalRes] = results;

            // 래퍼 내부의 .data 에서 실제 스크리너 결과를 꺼냄
            const krData = krRes.status === 'fulfilled' ? krRes.value.data?.data : null;
            const globalData = globalRes.status === 'fulfilled' ? globalRes.value.data?.data : null;

            if (krData?.passed_stocks) {
                setKrScreener(krData);
            }
            if (globalData?.kr_portfolio) {
                setGlobalScreener(globalData);
            }

            // 에러 세분화: 서버 미실행 vs 스크리너 미실행 vs 데이터 없음
            if (krRes.status === 'rejected' && globalRes.status === 'rejected') {
                const isNetworkError = [krRes.reason, globalRes.reason].some(
                    (e) => e?.code === 'ERR_NETWORK' || e?.message?.includes('Network Error'),
                );
                setLoadError(
                    isNetworkError
                        ? '백엔드 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해 주세요.'
                        : '스크리너 결과를 불러올 수 없습니다. 스크리너를 먼저 실행해 주세요.',
                );
            } else {
                // 한쪽만 로드된 경우 안내
                const partialErrors: string[] = [];
                if (krRes.status === 'rejected') {
                    partialErrors.push('국내 스크리너 결과 없음');
                } else if (!krData?.passed_stocks || krData.passed_stocks.length === 0) {
                    partialErrors.push('국내 스크리너 통과 종목 0건');
                }
                if (globalRes.status === 'rejected') {
                    partialErrors.push('글로벌 스크리너 결과 없음');
                } else if (!globalData?.kr_portfolio || globalData.kr_portfolio.length === 0) {
                    partialErrors.push('글로벌 스크리너 포트폴리오 0건');
                }
                if (partialErrors.length > 0) {
                    setLoadError(`일부 데이터 제한: ${partialErrors.join(', ')}. 해당 섹션은 비활성됩니다.`);
                }
            }
        } catch (err) {
            setLoadError('예상치 못한 오류가 발생했습니다. 새로고침을 시도해 주세요.');
        }

        setLoading(false);
    };

    useEffect(() => {
        fetchScreenerResults();
    }, []);

    // ── GAP 계산 (useMemo) ──
    const gapResult: CombinedGapResult | null = useMemo(() => {
        if (positions.length === 0) return null;
        if (!krScreener && !globalScreener) return null;

        // capital/usdToKrw 방어
        const safeCapital = capital > 0 ? capital : 1;
        const safeUsd = usdToKrw > 0 ? usdToKrw : 1300;

        const kr = krScreener
            ? analyzeKrGap(positions, krScreener, safeCapital, stockMap)
            : undefined;

        const global = globalScreener
            ? analyzeGlobalGap(positions, globalScreener, safeCapital, safeUsd, stockMap)
            : undefined;

        return combinedGap(kr, global, safeCapital, safeUsd);
    }, [positions, krScreener, globalScreener, capital, usdToKrw, stockMap]);

    // ── 경고 상태 계산 ──
    const warnings = useMemo(() => {
        const msgs: string[] = [];
        if (capital <= 0) msgs.push('자본금이 0원이며, 비중 계산이 정확하지 않을 수 있습니다.');
        if (usdToKrw <= 0) msgs.push('USD/KRW 환율이 설정되지 않았습니다. 기본값(1,300원)이 적용됩니다.');
        if (Object.keys(stockMap).length === 0) msgs.push('종목코드 매핑(stock_map)이 비어있어 종목명 기반으로만 매칭합니다. 정확도가 떨어질 수 있습니다.');

        // 통화 혼합 검사 — KRW+USD 모두 있는 경우 안내
        const currencies = new Set(positions.map((p) => p.currency));
        if (currencies.has('KRW') && currencies.has('USD') && usdToKrw > 0) {
            msgs.push(`통화 혼합 포트폴리오 — USD 자산은 ${usdToKrw.toLocaleString()}원/$ 환율로 KRW 환산하여 계산합니다.`);
        }

        // 매칭 0건 검사
        if (gapResult) {
            const krMatched = gapResult.kr?.matched.length ?? 0;
            const glMatched = gapResult.global?.matched.length ?? 0;
            if (gapResult.kr && krMatched === 0) {
                msgs.push('국내 스크리너와 일치하는 종목이 없습니다. CSV 종목명과 스크리너 종목명을 확인해 주세요.');
            }
            if (gapResult.global && glMatched === 0) {
                msgs.push('글로벌 스크리너와 일치하는 ETF가 없습니다. CSV에 해외 ETF가 포함되어 있는지 확인해 주세요.');
            }
        }

        return msgs;
    }, [capital, usdToKrw, stockMap, positions, gapResult]);

    // ── CSV 미업로드 상태 ──
    if (positions.length === 0) {
        return (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-12 text-center">
                <AlertCircle className="w-12 h-12 text-gray-300 mx-auto mb-4" />
                <h3 className="text-lg font-semibold text-gray-600 mb-2">CSV 데이터가 필요합니다</h3>
                <p className="text-sm text-gray-400">
                    시뮬레이터 탭에서 미래에셋 잔고 CSV를 먼저 업로드해 주세요.<br />
                    업로드한 보유 포트폴리오와 스크리너 타겟을 자동으로 비교 분석합니다.
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* 헤더 */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between">
                    <div>
                        <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                            <BarChart3 className="w-6 h-6 text-blue-600" />
                            포트폴리오 비교분석
                        </h2>
                        <p className="text-sm text-gray-500 mt-1">
                            보유 포트폴리오 ({positions.length}종목, {formatKRW(capital)}원) ↔ 스크리너 타겟 GAP 분석
                        </p>
                    </div>
                    <button
                        onClick={fetchScreenerResults}
                        disabled={loading}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 transition-colors disabled:opacity-50"
                    >
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                        새로고침
                    </button>
                </div>

                {/* 스크리너 상태 배지 */}
                <div className="flex gap-3 mt-4">
                    <StatusBadge
                        label="국내 스크리너"
                        loaded={!!krScreener}
                        detail={krScreener ? `${krScreener.passed_stocks?.length ?? 0}종목 통과 (${krScreener.regime})` : undefined}
                    />
                    <StatusBadge
                        label="글로벌 스크리너"
                        loaded={!!globalScreener}
                        detail={globalScreener ? `${globalScreener.preset?.label ?? ''} / ${globalScreener.kr_portfolio?.length ?? 0} ETF` : undefined}
                    />
                </div>

                {loadError && (
                    <div className="mt-4 flex items-center gap-2 p-3 rounded-lg bg-amber-50 border border-amber-200">
                        <AlertCircle className="w-4 h-4 text-amber-500 flex-shrink-0" />
                        <span className="text-sm text-amber-700">{loadError}</span>
                    </div>
                )}

                {/* 경고 메시지 */}
                {warnings.length > 0 && (
                    <div className="mt-4 space-y-2">
                        {warnings.map((msg, i) => (
                            <div key={i} className="flex items-start gap-2 p-2.5 rounded-lg bg-yellow-50 border border-yellow-200">
                                <Info className="w-3.5 h-3.5 text-yellow-600 flex-shrink-0 mt-0.5" />
                                <span className="text-xs text-yellow-700">{msg}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* 로딩 */}
            {loading && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-12 text-center">
                    <RefreshCw className="w-8 h-8 text-blue-400 animate-spin mx-auto mb-3" />
                    <p className="text-sm text-gray-500">스크리너 결과 불러오는 중…</p>
                </div>
            )}

            {/* GAP 분석 없음 */}
            {!loading && !gapResult && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-12 text-center">
                    <AlertCircle className="w-12 h-12 text-gray-300 mx-auto mb-4" />
                    <h3 className="text-lg font-semibold text-gray-600 mb-2">비교 분석을 수행할 수 없습니다</h3>
                    <p className="text-sm text-gray-400">
                        국내 또는 글로벌 스크리너를 먼저 실행한 뒤 새로고침 버튼을 눌러 주세요.
                    </p>
                </div>
            )}

            {/* 국내 GAP 섹션 */}
            {gapResult?.kr && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                    <GapSection gap={gapResult.kr} />
                </div>
            )}

            {/* 글로벌 GAP 섹션 */}
            {gapResult?.global && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                    <GapSection gap={gapResult.global} />
                </div>
            )}

            {/* 프롬프트 패널 */}
            {gapResult && (
                <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
                    <PromptPanel gapResult={gapResult} />
                </div>
            )}
        </div>
    );
}

// ═══════════════════════════════════════════════════
//  작은 유틸 컴포넌트
// ═══════════════════════════════════════════════════

function StatusBadge({ label, loaded, detail }: { label: string; loaded: boolean; detail?: string }) {
    return (
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium ${
            loaded ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-gray-100 text-gray-500 border border-gray-200'
        }`}>
            <span className={`w-2 h-2 rounded-full ${loaded ? 'bg-green-500' : 'bg-gray-400'}`} />
            {label}
            {detail && <span className="opacity-60">— {detail}</span>}
        </div>
    );
}

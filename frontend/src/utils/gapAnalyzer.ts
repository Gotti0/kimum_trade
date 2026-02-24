/**
 * 정량 GAP 분석 엔진
 *
 * CSV 포트폴리오(StockPosition[]) ↔ 스크리너 타겟을 비교하여
 * 종목매칭, 가중치 괴리, 섹터/카테고리 배분 차이를 계산합니다.
 *
 * 순수함수로 구현 — React 외부에서도 테스트 가능.
 *
 * 의존성: types/index.ts, utils/sectorMap.ts
 */

import type {
    StockPosition,
    ScreenedStock,
    ScreenerResult,
    GlobalScreenerKrEtf,
    GlobalEtfDetail,
    GlobalScreenerResult,
    MatchedStock,
    MissingTarget,
    CategoryGap,
    PortfolioGap,
    CombinedGapResult,
} from '../types';
import { getKrSector, getGlobalCategory } from './sectorMap';

// ═══════════════════════════════════════════════════
//  유틸리티 헬퍼
// ═══════════════════════════════════════════════════

/** 종목명 비교용 정규화: 공백·괄호·특수문자 제거, 소문자 변환 */
function normalizeName(name: string): string {
    return name
        .replace(/\s+/g, '')
        .replace(/[\(\)\[\]【】「」]/g, '')
        .replace(/[^\w가-힣]/g, '')
        .toLowerCase();
}

/** 0 나누기 방지 — 자본금이 0이면 1 반환 */
function safeDivisor(value: number): number {
    return value > 0 ? value : 1;
}

/** 안전한 환율 — 0 이하/NaN이면 기본값 1300 반환 */
function safeExchangeRate(rate: number): number {
    return Number.isFinite(rate) && rate > 0 ? rate : 1300;
}

/** 안전한 비중 — NaN/Infinity 방지 */
function safeWeight(value: number): number {
    return Number.isFinite(value) ? value : 0;
}

/**
 * 비중 차이에 따른 리밸런싱 액션 결정
 * weightGap = actualWeight - targetWeight
 *   > +0.5%p → decrease (비중 과다)
 *   < -0.5%p → increase (비중 부족)
 *   else     → hold     (허용 범위 내)
 */
function decideAction(weightGap: number): 'hold' | 'increase' | 'decrease' {
    const TOLERANCE = 0.005; // 0.5%p 허용 오차
    if (weightGap > TOLERANCE) return 'decrease';
    if (weightGap < -TOLERANCE) return 'increase';
    return 'hold';
}

// ═══════════════════════════════════════════════════
//  1. 국내 스크리너 매칭
// ═══════════════════════════════════════════════════

/**
 * CSV 포지션 1개를 국내 스크리너 통과종목과 매칭합니다.
 *
 * 매칭 우선순위:
 *   1차: 종목코드 완전일치 (stockMap에서 얻은 코드 ↔ stk_cd)
 *   2차: 종목명 완전일치 (name ↔ stk_nm)
 *   3차: 종목명 부분일치 (정규화 후 contains)
 */
function matchKrPosition(
    position: StockPosition,
    passedStocks: ScreenedStock[],
    stockMap: Record<string, string>,
): ScreenedStock | null {
    // 1차: 종목코드 완전일치
    const posCode = stockMap[position.name] ?? '';
    if (posCode && /^\d{6}$/.test(posCode)) {
        const byCode = passedStocks.find((s) => s.stk_cd === posCode);
        if (byCode) return byCode;
    }

    // 2차: 종목명 완전일치
    const byName = passedStocks.find((s) => s.stk_nm === position.name);
    if (byName) return byName;

    // 3차: 종목명 부분일치 (정규화 후)
    const normPos = normalizeName(position.name);
    if (normPos.length < 2) return null; // 너무 짧으면 오매칭 방지

    for (const stk of passedStocks) {
        const normStk = normalizeName(stk.stk_nm);
        if (normStk.length < 2) continue;
        if (normPos.includes(normStk) || normStk.includes(normPos)) {
            return stk;
        }
    }

    return null;
}

// ═══════════════════════════════════════════════════
//  2. 글로벌 스크리너 매칭
// ═══════════════════════════════════════════════════

interface GlobalMatchResult {
    kr: GlobalScreenerKrEtf;
    detail: GlobalEtfDetail;
}

/**
 * CSV 포지션 1개를 글로벌 스크리너 KR ETF 포트폴리오와 매칭합니다.
 *
 * 매칭 우선순위:
 *   1차: 해외 티커 완전일치 (position.ticker ↔ global_ticker)
 *   2차: 종목명 완전일치 (name ↔ kr_name)
 *   3차: 종목명 부분일치 (정규화 후 contains)
 */
function matchGlobalPosition(
    position: StockPosition,
    krPortfolio: GlobalScreenerKrEtf[],
    globalDetails: GlobalEtfDetail[],
    stockMap: Record<string, string>,
): GlobalMatchResult | null {
    const detailMap = new Map(globalDetails.map((g) => [g.global_ticker, g]));

    // 헬퍼: kr ETF를 GlobalMatchResult로 변환
    const toResult = (kr: GlobalScreenerKrEtf): GlobalMatchResult | null => {
        const detail = detailMap.get(kr.global_ticker);
        return detail ? { kr, detail } : null;
    };

    // 1차: 해외 티커 완전일치 (e.g. position.ticker === 'SPY')
    if (position.ticker) {
        const upperTicker = position.ticker.toUpperCase();
        const krByGlobal = krPortfolio.find(
            (k) => k.global_ticker.toUpperCase() === upperTicker,
        );
        if (krByGlobal) {
            const result = toResult(krByGlobal);
            if (result) return result;
        }
    }

    // stockMap에서 코드를 얻어 kr_code와 비교
    const posCode = stockMap[position.name] ?? '';
    if (posCode) {
        const krByCode = krPortfolio.find((k) => k.kr_code === posCode);
        if (krByCode) {
            const result = toResult(krByCode);
            if (result) return result;
        }
    }

    // 2차: 종목명 완전일치
    const krByName = krPortfolio.find((k) => k.kr_name === position.name);
    if (krByName) {
        const result = toResult(krByName);
        if (result) return result;
    }

    // 3차: 종목명 부분일치 (정규화 후)
    const normPos = normalizeName(position.name);
    if (normPos.length < 2) return null;

    for (const kr of krPortfolio) {
        const normKr = normalizeName(kr.kr_name);
        if (normKr.length < 2) continue;
        if (normPos.includes(normKr) || normKr.includes(normPos)) {
            const result = toResult(kr);
            if (result) return result;
        }
    }

    return null;
}

// ═══════════════════════════════════════════════════
//  3. analyzeKrGap — 국내 듀얼모멘텀 GAP 분석
// ═══════════════════════════════════════════════════

/**
 * CSV 포트폴리오를 국내 듀얼모멘텀 스크리너 결과와 비교하여
 * 종목매칭, 가중치 괴리, WICS 섹터 배분 차이를 계산합니다.
 *
 * @param positions     CSV 파싱된 전체 포지션 (KRW+USD 모두 포함)
 * @param screener      국내 스크리너 결과
 * @param totalCapital  총 자본금 (KRW)
 * @param stockMap      종목명 → 종목코드 매핑 (stock_map.json)
 */
export function analyzeKrGap(
    positions: StockPosition[],
    screener: ScreenerResult,
    totalCapital: number,
    stockMap: Record<string, string> = {},
): PortfolioGap {
    const capital = safeDivisor(totalCapital);
    const passed = screener.passed_stocks ?? [];

    // 엣지케이스: 스크리너 통과 종목이 없는 경우 빈 결과 반환
    if (passed.length === 0) {
        return {
            mode: 'kr',
            targetLabel: `국내 듀얼모멘텀 Top${screener.config?.top_n ?? 0}`,
            matched: [],
            overHoldings: positions.filter((p) => p.currency === 'KRW'),
            missingTargets: [],
            categoryGaps: {},
        };
    }

    // KRW 포지션만 대상 (해외주식은 글로벌 GAP에서 처리)
    const krPositions = positions.filter((p) => p.currency === 'KRW');

    const matched: MatchedStock[] = [];
    const matchedPosNames = new Set<string>();
    const matchedStkCds = new Set<string>();

    // ── 종목 매칭 ──
    for (const pos of krPositions) {
        const target = matchKrPosition(pos, passed, stockMap);
        if (!target) continue;

        // 중복 매칭 방지 (1개 스크리너 종목에 여러 포지션이 매칭되는 경우)
        if (matchedStkCds.has(target.stk_cd)) continue;

        const evalKrw = pos.evalAmount ?? 0;
        const actualWeight = safeWeight(evalKrw / capital);
        const targetWeight = safeWeight(target.weight / 100); // weight는 퍼센트 (5.0 = 5%)
        const weightGap = actualWeight - targetWeight;

        matched.push({
            ticker: target.stk_cd,
            name: target.stk_nm,
            sector: getKrSector(target.stk_cd),
            actualWeight,
            targetWeight,
            weightGap,
            momentumScore: target.score,
            action: decideAction(weightGap),
            adjustAmount: Math.abs(weightGap) * capital,
        });

        matchedPosNames.add(pos.name);
        matchedStkCds.add(target.stk_cd);
    }

    // ── 과잉보유: 보유 중이지만 타겟에 없는 KR 종목 ──
    const overHoldings = krPositions.filter((p) => !matchedPosNames.has(p.name));

    // ── 미보유 타겟: 스크리너에 있지만 보유하지 않은 종목 ──
    const missingTargets: MissingTarget[] = passed
        .filter((s) => !matchedStkCds.has(s.stk_cd))
        .map((s) => ({
            ticker: s.stk_cd,
            name: s.stk_nm,
            weight: s.weight / 100,
            score: s.score,
            sector: getKrSector(s.stk_cd),
        }));

    // ── WICS 섹터별 배분 괴리 ──
    const categoryGaps = computeKrSectorGaps(krPositions, passed, capital, stockMap);

    return {
        mode: 'kr',
        targetLabel: `국내 듀얼모멘텀 Top${screener.config.top_n}`,
        matched,
        overHoldings,
        missingTargets,
        categoryGaps,
    };
}

/**
 * 국내 WICS 섹터별 실제비중 vs 타겟비중 괴리를 계산합니다.
 */
function computeKrSectorGaps(
    krPositions: StockPosition[],
    passedStocks: ScreenedStock[],
    _totalKrCapital: number,
    stockMap: Record<string, string>,
): Record<string, CategoryGap> {
    // 실제 보유 섹터 분포 (평가금액 기준)
    const totalKrEval = safeDivisor(
        krPositions.reduce((sum, p) => sum + (p.evalAmount ?? 0), 0),
    );
    const sectorActual: Record<string, number> = {};
    for (const pos of krPositions) {
        const code = stockMap[pos.name] ?? '';
        const sector = code ? getKrSector(code) : '미분류';
        sectorActual[sector] =
            (sectorActual[sector] ?? 0) + (pos.evalAmount ?? 0) / totalKrEval;
    }

    // 타겟 섹터 분포 (weight% 합산)
    const sectorTarget: Record<string, number> = {};
    for (const stk of passedStocks) {
        const sector = getKrSector(stk.stk_cd);
        sectorTarget[sector] = (sectorTarget[sector] ?? 0) + stk.weight / 100;
    }

    // 모든 섹터 합집합
    const allSectors = new Set([
        ...Object.keys(sectorActual),
        ...Object.keys(sectorTarget),
    ]);

    const gaps: Record<string, CategoryGap> = {};
    for (const sector of allSectors) {
        const actual = sectorActual[sector] ?? 0;
        const target = sectorTarget[sector] ?? 0;
        gaps[sector] = { actual, target, gap: actual - target };
    }

    return gaps;
}

// ═══════════════════════════════════════════════════
//  4. analyzeGlobalGap — 글로벌 멀티에셋 GAP 분석
// ═══════════════════════════════════════════════════

/**
 * CSV 포트폴리오를 글로벌 멀티에셋 스크리너 결과와 비교하여
 * ETF 매칭, 가중치 괴리, 자산군(주식/채권/대체/현금) 배분 차이를 계산합니다.
 *
 * @param positions      CSV 파싱된 전체 포지션
 * @param globalResult   글로벌 스크리너 결과
 * @param totalCapital   총 자본금 (KRW)
 * @param usdToKrw       USD/KRW 환율
 * @param stockMap       종목명 → 종목코드/티커 매핑
 */
export function analyzeGlobalGap(
    positions: StockPosition[],
    globalResult: GlobalScreenerResult,
    totalCapital: number,
    usdToKrw: number,
    stockMap: Record<string, string> = {},
): PortfolioGap {
    const capital = safeDivisor(totalCapital);
    const safeUsdKrw = safeExchangeRate(usdToKrw);
    const krPortfolio = globalResult.kr_portfolio ?? [];
    const globalDetails = globalResult.global_etf_details ?? [];

    // 엣지케이스: 글로벌 포트폴리오가 비어있는 경우 빈 결과 반환
    if (krPortfolio.length === 0) {
        const presetLabel = globalResult.preset?.label ?? '글로벌 멀티에셋';
        return {
            mode: 'global',
            targetLabel: presetLabel,
            matched: [],
            overHoldings: positions.filter(
                (p) => p.currency === 'USD' || p.assetType === 'ETF' || p.assetType === 'ETN' || p.assetType === '해외주식',
            ),
            missingTargets: [],
            categoryGaps: {},
        };
    }

    const matched: MatchedStock[] = [];
    const matchedPosNames = new Set<string>();
    const matchedGlobalTickers = new Set<string>();

    // ── 종목 매칭 ──
    for (const pos of positions) {
        const match = matchGlobalPosition(pos, krPortfolio, globalDetails, stockMap);
        if (!match) continue;

        // 중복 매칭 방지
        if (matchedGlobalTickers.has(match.kr.global_ticker)) continue;

        // 평가금액을 KRW로 통일 (안전한 환율 적용)
        const evalKrw =
            pos.currency === 'USD'
                ? (pos.evalAmount ?? 0) * safeUsdKrw
                : (pos.evalAmount ?? 0);

        const actualWeight = safeWeight(evalKrw / capital);
        const targetWeight = safeWeight(match.kr.weight_pct / 100); // weight_pct는 퍼센트
        const weightGap = actualWeight - targetWeight;

        matched.push({
            ticker: match.kr.global_ticker,
            name: match.kr.kr_name,
            sector: getGlobalCategory(match.kr.global_ticker),
            actualWeight,
            targetWeight,
            weightGap,
            momentumScore: match.detail.score ?? 0,
            action: decideAction(weightGap),
            adjustAmount: Math.abs(weightGap) * capital,
        });

        matchedPosNames.add(pos.name);
        matchedGlobalTickers.add(match.kr.global_ticker);
    }

    // ── 과잉보유: 보유 중이지만 글로벌 타겟에 없는 ETF/해외주식 ──
    // (국내 개별주식은 제외 — KR GAP에서 분석)
    const overHoldings = positions.filter(
        (p) =>
            !matchedPosNames.has(p.name) &&
            (p.currency === 'USD' ||
                p.assetType === 'ETF' ||
                p.assetType === 'ETN' ||
                p.assetType === '해외주식'),
    );

    // ── 미보유 타겟: 타겟에 있지만 보유하지 않은 ETF ──
    const missingTargets: MissingTarget[] = krPortfolio
        .filter(
            (k) =>
                !matchedGlobalTickers.has(k.global_ticker) && k.weight_pct > 0,
        )
        .map((k) => {
            const detail = globalDetails.find(
                (g) => g.global_ticker === k.global_ticker,
            );
            return {
                ticker: k.global_ticker,
                name: k.kr_name,
                weight: k.weight_pct / 100,
                score: detail?.score,
                sector: getGlobalCategory(k.global_ticker),
            };
        });

    // ── 자산군 카테고리 배분 괴리 ──
    const categoryGaps = computeGlobalCategoryGaps(
        matched,
        krPortfolio,
        globalDetails,
    );

    // 프리셋 라벨!
    const presetLabel = globalResult.preset?.label ?? '글로벌 멀티에셋';

    return {
        mode: 'global',
        targetLabel: presetLabel,
        matched,
        overHoldings,
        missingTargets,
        categoryGaps,
    };
}

/**
 * 글로벌 자산군(주식/채권/대체/현금) 카테고리별 실제비중 vs 타겟비중 괴리를 계산합니다.
 */
function computeGlobalCategoryGaps(
    matched: MatchedStock[],
    krPortfolio: GlobalScreenerKrEtf[],
    _globalDetails: GlobalEtfDetail[],
): Record<string, CategoryGap> {
    // 실제 카테고리 배분 (매칭된 종목의 actualWeight 합산)
    const catActual: Record<string, number> = {};
    for (const m of matched) {
        const cat = m.sector ?? '미분류';
        catActual[cat] = (catActual[cat] ?? 0) + m.actualWeight;
    }

    // 타겟 카테고리 배분 (kr_portfolio의 weight_pct 합산을 카테고리별로)
    const catTarget: Record<string, number> = {};
    for (const kr of krPortfolio) {
        if (kr.weight_pct <= 0) continue;
        const cat = getGlobalCategory(kr.global_ticker);
        catTarget[cat] = (catTarget[cat] ?? 0) + kr.weight_pct / 100;
    }

    // 모든 카테고리 합집합
    const allCats = new Set([
        ...Object.keys(catActual),
        ...Object.keys(catTarget),
    ]);

    const gaps: Record<string, CategoryGap> = {};
    for (const cat of allCats) {
        const actual = catActual[cat] ?? 0;
        const target = catTarget[cat] ?? 0;
        gaps[cat] = { actual, target, gap: actual - target };
    }

    return gaps;
}

// ═══════════════════════════════════════════════════
//  5. combinedGap — 국내 + 글로벌 통합
// ═══════════════════════════════════════════════════

/**
 * 국내/글로벌 GAP 분석 결과를 하나의 CombinedGapResult로 합칩니다.
 * 둘 중 하나만 있어도 OK — undefined는 해당 섹션 미표시.
 */
export function combinedGap(
    kr: PortfolioGap | undefined,
    global: PortfolioGap | undefined,
    totalCapital: number,
    usdToKrw: number,
): CombinedGapResult {
    return { kr, global, totalCapital, usdToKrw };
}

// ═══════════════════════════════════════════════════
//  6. 보조 통계 유틸리티
// ═══════════════════════════════════════════════════

/**
 * PortfolioGap에서 주요 통계 지표를 추출합니다 (UI 요약 카드용).
 */
export function gapSummary(gap: PortfolioGap) {
    const totalMatched = gap.matched.length;
    const totalOver = gap.overHoldings.length;
    const totalMissing = gap.missingTargets.length;

    const holdCount = gap.matched.filter((m) => m.action === 'hold').length;
    const increaseCount = gap.matched.filter(
        (m) => m.action === 'increase',
    ).length;
    const decreaseCount = gap.matched.filter(
        (m) => m.action === 'decrease',
    ).length;

    // 가중치 괴리 절대값 합 (괴리도 지표)
    const totalAbsGap = gap.matched.reduce(
        (sum, m) => sum + Math.abs(m.weightGap),
        0,
    );

    // 가장 큰 괴리 (양수=과다, 음수=부족)
    const maxOverweight =
        gap.matched.length > 0
            ? gap.matched.reduce((max, m) =>
                  m.weightGap > max.weightGap ? m : max,
              )
            : null;
    const maxUnderweight =
        gap.matched.length > 0
            ? gap.matched.reduce((min, m) =>
                  m.weightGap < min.weightGap ? m : min,
              )
            : null;

    // 카테고리 괴리 중 가장 큰 것
    const worstCategoryGap = Object.entries(gap.categoryGaps)
        .sort(([, a], [, b]) => Math.abs(b.gap) - Math.abs(a.gap))
        .at(0);

    return {
        totalMatched,
        totalOver,
        totalMissing,
        holdCount,
        increaseCount,
        decreaseCount,
        totalAbsGap,
        maxOverweight,
        maxUnderweight,
        worstCategoryGap: worstCategoryGap
            ? { sector: worstCategoryGap[0], ...worstCategoryGap[1] }
            : null,
    };
}

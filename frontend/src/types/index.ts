export interface StockPosition {
    name: string;
    quantity: number;
    averagePrice: number;
    currency: 'KRW' | 'USD';
    evalAmount?: number;
    assetType?: string;
    isSimTarget: boolean;
    ticker?: string;
}

export interface CSVRow {
    유형: string;
    종목명: string;
    종목구분: string;
    보유량: string;
    주문가능: string;
    평균단가: string;
    매입금액: string;
    현재가: string;
    평가금액: string;
    평가손익: string;
    수익률: string;
}

export interface SimulationResult {
    code?: string;
    name: string;
    quantity: number;
    averagePrice: number;
    currency: 'KRW' | 'USD';
    evalAmount?: number;
    assetType?: string;
    isSimTarget: boolean;
    ticker?: string;
    currentPrice?: number;
    atr?: number;
    stopLossPrice?: number;
    riskAmount?: number;
    status: 'pending' | 'calculated' | 'error' | 'excluded';
    errorMessage?: string;
}

// ═══════════════════════════════════════════════════
//  Screener Result Types (MomentumPanel 과 공유)
// ═══════════════════════════════════════════════════

/** 국내 듀얼모멘텀 스크리너 — 통과 종목 */
export interface ScreenedStock {
    rank: number;
    stk_cd: string;
    stk_nm: string;
    close: number;
    ret_3m: number;
    ret_6m: number;
    ret_12m: number;
    score: number;
    abs_pass: boolean;
    weight: number;
}

/** 국내 듀얼모멘텀 스크리너 — 유니버스 종목 */
export interface UniverseStock {
    stk_cd: string;
    stk_nm: string;
    close: number;
    score: number;
    ret_12m: number;
    passed: boolean;
    reason: string;
}

/** 국내 듀얼모멘텀 스크리너 결과 */
export interface ScreenerResult {
    timestamp: string;
    ref_date: string;
    regime: string;
    kospi: number | null;
    kospi_sma200: number | null;
    config: {
        top_n: number;
        weight_method: string;
        min_trading_value: number;
    };
    summary: {
        total_stocks: number;
        universe_size: number;
        abs_momentum_pass: number;
        selected_count: number;
        data_start: string;
        data_end: string;
        error?: string;
    };
    passed_stocks: ScreenedStock[];
    all_universe: UniverseStock[];
    elapsed_sec: number;
}

/** 글로벌 스크리너 — KR ETF 매핑 */
export interface GlobalScreenerKrEtf {
    kr_code: string;
    kr_name: string;
    global_ticker: string;
    category: string;
    hedged: boolean;
    weight_pct: number;
    alloc_krw: number;
    kr_price: number;
    shares: number;
    actual_alloc: number;
    description: string;
}

/** 글로벌 스크리너 — ETF 상세 */
export interface GlobalEtfDetail {
    global_ticker: string;
    global_label: string;
    global_price_usd: number;
    regime: string;
    weight_pct: number;
    kr_code: string;
    kr_name: string;
    kr_category: string;
    kr_hedged: boolean;
    kr_description: string;
    ret_3m?: number;
    ret_6m?: number;
    ret_12m?: number;
    score?: number;
    abs_pass?: boolean;
}

/** 글로벌 멀티에셋 스크리너 결과 */
export interface GlobalScreenerResult {
    timestamp: string;
    ref_date: string;
    preset: {
        key: string;
        label: string;
        icon: string;
        risk_level: number;
        desc: string;
    };
    config: {
        weight_method: string;
        initial_capital: number;
        warmup_days: number;
    };
    usdkrw_rate: number;
    regime_summary: {
        n_bull: number;
        n_bear: number;
        total: number;
        regimes: Record<string, string>;
    };
    strategic_weights: Record<string, string>;
    category_actual: Record<string, number>;
    global_etf_details: GlobalEtfDetail[];
    kr_portfolio: GlobalScreenerKrEtf[];
    benchmark_kr: Array<{
        kr_code: string;
        kr_name: string;
        global_ticker: string;
        weight_pct: number;
        alloc_krw: number;
        kr_price: number;
        shares: number;
    }>;
    summary: {
        total_etfs: number;
        invested_etfs: number;
        total_alloc_krw: number;
        remaining_cash: number;
        utilization_pct: number;
        data_start: string;
        data_end: string;
        error?: string;
    };
    elapsed_sec: number;
}

// ═══════════════════════════════════════════════════
//  Portfolio Compare Types (GAP 분석)
// ═══════════════════════════════════════════════════

/** GAP 분석 — 매칭된 종목 */
export interface MatchedStock {
    ticker: string;
    name: string;
    sector?: string;               // WICS 업종 (국내) | 자산군 카테고리 (글로벌)
    actualWeight: number;           // 실제 보유 비중 (0~1)
    targetWeight: number;           // 타겟 비중 (0~1)
    weightGap: number;              // actual - target
    momentumScore: number;
    action: 'hold' | 'increase' | 'decrease';
    adjustAmount: number;           // KRW 기준 조정 필요 금액
}

/** GAP 분석 — 타겟에만 있는 미보유 종목 */
export interface MissingTarget {
    ticker: string;
    name: string;
    weight: number;                 // 타겟 비중
    score?: number;                 // 모멘텀 스코어
    sector?: string;                // 섹터/카테고리
}

/** 카테고리별 배분 괴리 */
export interface CategoryGap {
    actual: number;                 // 실제 비중 (0~1)
    target: number;                 // 타겟 비중 (0~1)
    gap: number;                    // actual - target
}

/** 국내/글로벌 공통 GAP 분석 결과 */
export interface PortfolioGap {
    mode: 'kr' | 'global';
    targetLabel: string;            // 예: "국내 듀얼모멘텀 Top20" | "글로벌 위험중립형"
    matched: MatchedStock[];
    overHoldings: StockPosition[];  // 현재만 보유 (타겟에 없음)
    missingTargets: MissingTarget[];
    categoryGaps: Record<string, CategoryGap>;
}

/** 국내 + 글로벌 통합 비교 결과 */
export interface CombinedGapResult {
    kr?: PortfolioGap;              // 국내 스크리너 비교 (결과 없으면 undefined)
    global?: PortfolioGap;          // 글로벌 스크리너 비교 (결과 없으면 undefined)
    totalCapital: number;           // 총 자본금 (KRW)
    usdToKrw: number;              // USD/KRW 환율
}

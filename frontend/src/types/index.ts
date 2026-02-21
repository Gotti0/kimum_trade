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

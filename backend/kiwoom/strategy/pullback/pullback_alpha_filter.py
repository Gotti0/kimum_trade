"""
PullbackAlphaFilter: 스윙-풀백 전략을 위한 2단계 눌림목 필터링 엔진.

필터 파이프라인:
  Phase 1. Surge Detection (워치리스트 등록)
    - 유동성 허들: 20일 ADTV >= 50억
    - 급등 조건: 최근 5영업일 이내 RVOL >= 3.0 AND 일일수익률 >= 10% 인 날이 존재
  Phase 2. Pullback Confirmation (매수 후보 승격)
    - VCR: 당일 거래량 / 급등일 거래량 <= 0.35
    - FRL: 0.382 <= (급등일 고가 - 당일 종가) / (급등일 고가 - 급등 전일 종가) <= 0.618
    - Disparity: 당일 종가와 5일 EMA의 이격도가 -2.0% ~ +2.0% 이내
"""

import logging
from typing import Optional

from backend.kiwoom.strategy.phoenix.alpha_filter import compute_adtv, compute_rvol, compute_ema, estimate_market_cap

logger = logging.getLogger(__name__)

# 파라미터 기본값
ADTV_THRESHOLD = 50 * 100_000_000  # 50억
MARKET_CAP_THRESHOLD = 300 * 100_000_000  # 300억 (선택)
SURGE_RVOL_THRESHOLD = 3.0
SURGE_RETURN_THRESHOLD = 10.0
SURGE_LOOKBACK_DAYS = 5

VCR_THRESHOLD = 0.35
FRL_LOWER = 0.382
FRL_UPPER = 0.618
DISPARITY_LOWER = -2.0
DISPARITY_UPPER = 2.0


def compute_pullback_indicators(daily_bars: list[dict], current_idx: int = -1) -> dict:
    if current_idx < 0:
        current_idx = len(daily_bars) + current_idx
        
    if current_idx < 20:
        return {'valid': False, 'reason': '데이터 부족 (최소 20일 필요)'}
        
    bars_up_to_current = daily_bars[:current_idx + 1]
    current_bar = bars_up_to_current[-1]
    
    # 1. ADTV & RVOL (최근 21일 어치 데이터 필요, 어제까지의 ADTV)
    adtv20 = compute_adtv(bars_up_to_current[:-1], period=20)
    if adtv20 is None or adtv20 == 0:
        return {'valid': False, 'reason': 'ADTV 계산 불가'}
        
    # 당일 거래대금 / 20일 ADTV
    # trde_amt 우선, 없으면 종가*거래량
    current_close = float(current_bar.get('cur_prc', 0))
    current_vol = float(current_bar.get('trde_qty', 0))
    current_trde_amt = float(current_bar.get('trde_amt', current_close * current_vol))
    rvol = current_trde_amt / adtv20
    
    # 2. 5일 EMA 계산
    closes = [float(b.get('cur_prc', 0)) for b in bars_up_to_current]
    ema5 = compute_ema(closes, period=5)
    disparity_5 = ((current_close / ema5) - 1) * 100 if ema5 else 0
    
    # 3. Surge Detection (최근 5일 이내 급등일 찾기)
    surge_day_idx = -1
    for i in range(current_idx - 1, max(0, current_idx - 1 - SURGE_LOOKBACK_DAYS), -1):
        bar = daily_bars[i]
        prev_bar = daily_bars[i-1]
        
        c_close = float(bar.get('cur_prc', 0))
        p_close = float(prev_bar.get('cur_prc', 0))
        daily_ret = ((c_close - p_close) / p_close) * 100 if p_close > 0 else 0
        
        b_adtv = compute_adtv(daily_bars[:i], period=20)
        b_trde_amt = float(bar.get('trde_amt', c_close * float(bar.get('trde_qty', 0))))
        b_rvol = b_trde_amt / b_adtv if b_adtv and b_adtv > 0 else 0
        
        if daily_ret >= SURGE_RETURN_THRESHOLD and b_rvol >= SURGE_RVOL_THRESHOLD:
            surge_day_idx = i
            break
            
    if surge_day_idx == -1:
        return {'valid': False, 'reason': '최근 5일 내 급등일 없음'}
        
    surge_bar = daily_bars[surge_day_idx]
    surge_prev_bar = daily_bars[surge_day_idx - 1]
    
    surge_high = float(surge_bar.get('high_pric', surge_bar.get('cur_prc', 0)))
    surge_prev_close = float(surge_prev_bar.get('cur_prc', 0))
    surge_vol = float(surge_bar.get('trde_qty', 0))
    
    # VCR 계산
    vcr = current_vol / surge_vol if surge_vol > 0 else 999.0
    
    # FRL 계산
    frl = 0.0
    if surge_high - surge_prev_close > 0:
        frl = (surge_high - current_close) / (surge_high - surge_prev_close)
        
    return {
        'valid': True,
        'adtv20': adtv20,
        'vcr': vcr,
        'frl': frl,
        'disparity_5': disparity_5,
        'surge_day_idx': surge_day_idx,
        'surge_return': ((float(surge_bar.get('cur_prc', 0)) - surge_prev_close) / surge_prev_close) * 100,
        'surge_rvol': float(surge_bar.get('trde_amt', float(surge_bar.get('cur_prc', 0)) * surge_vol)) / compute_adtv(daily_bars[:surge_day_idx], period=20) if compute_adtv(daily_bars[:surge_day_idx], period=20) else 0
    }

class PullbackAlphaFilter:
    def __init__(
        self,
        adtv_threshold: float = ADTV_THRESHOLD,
        surge_rvol_threshold: float = SURGE_RVOL_THRESHOLD,
        surge_return_threshold: float = SURGE_RETURN_THRESHOLD,
        vcr_threshold: float = VCR_THRESHOLD,
        frl_lower: float = FRL_LOWER,
        frl_upper: float = FRL_UPPER,
        disparity_lower: float = DISPARITY_LOWER,
        disparity_upper: float = DISPARITY_UPPER,
    ):
        self.adtv_threshold = adtv_threshold
        self.surge_rvol_threshold = surge_rvol_threshold
        self.surge_return_threshold = surge_return_threshold
        self.vcr_threshold = vcr_threshold
        self.frl_lower = frl_lower
        self.frl_upper = frl_upper
        self.disparity_lower = disparity_lower
        self.disparity_upper = disparity_upper

    def apply_all_filters(self, indicators: dict) -> tuple[bool, list[str]]:
        reasons = []
        
        if not indicators.get('valid', False):
            return False, [indicators.get('reason', '지표 계산 실패')]
            
        # 1. 유동성
        if indicators['adtv20'] < self.adtv_threshold:
            reasons.append(f"ADTV 부족 (기준: {self.adtv_threshold/1e8}억, 현재: {indicators['adtv20']/1e8:.2f}억)")
            return False, reasons
            
        # 2. VCR (거래량 감소 비율)
        if indicators['vcr'] > self.vcr_threshold:
            reasons.append(f"거래량 미감소 (VCR: {indicators['vcr']:.2f} > {self.vcr_threshold})")
            return False, reasons
            
        # 3. FRL (피보나치 되돌림)
        if not (self.frl_lower <= indicators['frl'] <= self.frl_upper):
            reasons.append(f"되돌림 이탈 (FRL: {indicators['frl']:.3f}, 허용: {self.frl_lower}~{self.frl_upper})")
            return False, reasons
            
        # 4. Disparity (5일선 이격)
        if not (self.disparity_lower <= indicators['disparity_5'] <= self.disparity_upper):
            reasons.append(f"이격도 이탈 (Disparity: {indicators['disparity_5']:.2f}%, 허용: {self.disparity_lower}%~{self.disparity_upper}%)")
            return False, reasons
            
        reasons.append(f"통과 (VCR: {indicators['vcr']:.2f}, FRL: {indicators['frl']:.2f}, Disp: {indicators['disparity_5']:.2f}%)")
        return True, reasons

    def screen_universe(
        self,
        candidates: list[dict],
        daily_bars_by_stock: dict[str, list[dict]],
    ) -> list[dict]:
        """후보 종목 리스트에 필터를 적용하여 통과 종목만 반환합니다."""
        passed_stocks = []
        
        for stock in candidates:
            stk_cd = stock['stk_cd']
            stk_nm = stock['stk_nm']
            daily_bars = daily_bars_by_stock.get(stk_cd, [])
            
            if not daily_bars or len(daily_bars) < 30:
                logger.debug(f"[{stk_nm}] 데이터 부족 필터 탈락")
                continue
                
            indicators = compute_pullback_indicators(daily_bars, current_idx=-1)
            is_passed, reasons = self.apply_all_filters(indicators)
            
            if is_passed:
                stock_copy = stock.copy()
                stock_copy['pullback_indicators'] = indicators
                passed_stocks.append(stock_copy)
                logger.info(f"[Pullback 통과] {stk_nm}({stk_cd}): {reasons[-1]}")
            else:
                logger.debug(f"[Pullback 탈락] {stk_nm}({stk_cd}): {reasons[-1]}")
                
        return passed_stocks

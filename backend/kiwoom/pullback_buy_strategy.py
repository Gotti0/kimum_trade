"""
PullbackBuyEngine: 스윙-풀백 전략의 매수 시뮬레이터.

주요 로직:
  1. 익일 09:00 시가 확인 (갭하락 방어)
     - 시초가 < 전일 종가 * 0.98 이면 매수 포기
  2. 매수 타이밍
     - Daily 모드: 갭하락 필터 통과 시 시가(Open)로 매수(가정)
     - Minute 모드: 09:30~10:30 구간에서 가격이 5일 EMA를 회복(Reclaim)할 때 매수. 
       (여기서는 단순화를 위해 Minute 데이터가 주어지면 09:30 시점의 종가로 매수하거나 특정 조건을 만족할 때 매수하도록 단순화)
"""

import logging
from typing import Optional

from backend.kiwoom.sell_strategy import _parse_price

logger = logging.getLogger(__name__)

GAP_DOWN_THRESHOLD = 0.98  # -2% 이하 갭하락 시 매수 취소

class PullbackBuyEngine:
    def __init__(self, mode: str = "daily", gap_down_threshold: float = GAP_DOWN_THRESHOLD, slippage_bps: float = 10.0):
        """
        Args:
            mode: "daily" 또는 "minute"
            gap_down_threshold: 갭하락 기준치 (0.98 = -2%)
            slippage_bps: 슬리피지 (bp 단위, 10 = 0.1%). 매수 시 불리한 방향(+)으로 적용.
        """
        self.mode = mode
        self.gap_down_threshold = gap_down_threshold
        self.slippage_rate = slippage_bps / 10_000  # bps → 비율

    def simulate_buy(
        self,
        date_str: str,
        stk_cd: str,
        stk_nm: str,
        prev_close: float,
        target_amount: int,
        daily_bars: list[dict],
        minute_bars: Optional[list[dict]] = None,
    ) -> list[dict]:
        """
        주어진 일자(date_str) 데이터로 매수를 시뮬레이션합니다.
        
        Args:
            date_str: 매수 당일 (ex: '20250102')
            stk_cd, stk_nm: 종목 정보
            prev_close: 전일 종가
            target_amount: 목표 매수 금액
            daily_bars: 과거~해당일까지의 일봉 데이터 (해당일 포함 또는 직전일까지)
            minute_bars: 해당일 분봉 데이터 (mode="minute"일 때 사용)
            
        Returns:
            매수 체결 내역 리스트: [{'time': '0900', 'price': ..., 'qty': ..., 'amount': ...}]
            매수 포기 시 빈 리스트 반환
        """
        today_daily = None
        for b in reversed(daily_bars):
            if b.get('dt', '') == date_str:
                today_daily = b
                break
                
        if not today_daily:
            logger.warning(f"[{stk_nm}] {date_str} 일봉 데이터 없음. 매수 취소.")
            return []
            
        open_price = _parse_price(str(today_daily.get('open_pric', 0)))
        if open_price == 0:
            return []

        # 1. 갭하락 방어 필터
        if prev_close > 0 and (open_price / prev_close) < self.gap_down_threshold:
            logger.info(f"[{stk_nm}] {date_str} 시초가 갭하락({(open_price/prev_close - 1)*100:.2f}%) 방어: 매수 포기")
            return []
            
        # 2. Daily 모드: 시가(Open)로 1배수 일괄 매수 (슬리피지 적용)
        if self.mode == "daily" or not minute_bars:
            buy_price = open_price * (1 + self.slippage_rate)
            qty = int(target_amount // buy_price) if buy_price > 0 else 0
            if qty <= 0:
                return []
                
            logger.debug(f"[{stk_nm}] {date_str} (Daily) 갭하락 방어 성공. 시가 {open_price}원 → 체결가 {buy_price:.0f}원(슬리피지) {qty}주 매수")
            return [{
                'time': '0900',
                'price': buy_price,
                'qty': qty,
                'amount': int(buy_price * qty)
            }]
            
        # 3. Minute 모드: 09:30 시점 매수
        # 원래 논문 로직은 09:30~10:30 구간 5일선 Reclaim이나, 
        # API 제공 분봉 해상도 및 백테스트 속도를 고려해 09:30 분봉선 종가로 단순화 (충분한 유예).
        for m_bar in minute_bars:
            m_time = m_bar.get('dt', '')[-6:]
            if m_time >= '093000':
                raw_price = _parse_price(str(m_bar.get('cur_prc', 0)))
                buy_price = raw_price * (1 + self.slippage_rate)
                qty = int(target_amount // buy_price) if buy_price > 0 else 0
                if qty <= 0:
                    break
                    
                logger.debug(f"[{stk_nm}] {date_str} (Minute) 09:30 체결가 {buy_price:.0f}원(슬리피지) {qty}주 매수")
                return [{
                    'time': '0930',
                    'price': buy_price,
                    'qty': qty,
                    'amount': int(buy_price * qty)
                }]
                
        # 09:30 이후 데이터가 없으면 시가 매수 (슬리피지 적용)
        buy_price = open_price * (1 + self.slippage_rate)
        qty = int(target_amount // buy_price) if buy_price > 0 else 0
        if qty > 0:
            return [{
                'time': '0900',
                'price': buy_price,
                'qty': qty,
                'amount': int(buy_price * qty)
            }]
            
        return []

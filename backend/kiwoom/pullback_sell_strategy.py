"""
PullbackSellEngine: 스윙-풀백 전략의 비대칭 손익비 달성을 위한 매도 시뮬레이터.

주요 로직 (일봉/분봉 동시 적용 가능하도록 추상화):
  1. 목표가(1차 익절): 진입가 + (ATR * 1.5)
     - 도달 시 보유 물량의 50% 매도
     - 매도 후 남은 물량의 하드 스톱을 본절가(진입가 * 1.00345)로 상향 조정
  2. 스톱로스(손절가): 진입가 - (ATR * 1.2)
     - 이탈 시 전량 손절
  3. 만기 청산: 보유 7영업일 경과 시 종가로 전량 청산
"""

import logging
import math
from typing import Optional

from backend.kiwoom.sell_strategy import _parse_price

logger = logging.getLogger(__name__)

# 마찰 비용 상수 (왕복 0.345%)
FRICTION_COST = 0.00345

class PullbackSellEngine:
    def __init__(
        self,
        atr_period: int = 14,
        stop_atr_multiplier: float = 1.2,
        profit_atr_multiplier: float = 1.5,
        max_holding_days: int = 7,
        slippage_bps: float = 10.0,
        stop_slippage_bps: float = 20.0,
    ):
        self.atr_period = atr_period
        self.stop_atr_multiplier = stop_atr_multiplier
        self.profit_atr_multiplier = profit_atr_multiplier
        self.max_holding_days = max_holding_days
        self.slippage_rate = slippage_bps / 10_000        # 일반 매도 슬리피지 (bps → 비율)
        self.stop_slippage_rate = stop_slippage_bps / 10_000  # 손절 시 슬리피지 (급락 시 호가 부족 반영)

    def evaluate_sell(
        self,
        stk_cd: str,
        stk_nm: str,
        position: dict,
        date_str: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        minute_bars: Optional[list[dict]] = None
    ) -> list[dict]:
        """
        주어진 일자(date_str)의 데이터를 바탕으로 매도 여부를 평가하여 체결 내역 리스트를 반환합니다.
        
        Args:
            stk_cd, stk_nm: 종목 정보
            position: 기준 포지션 정보 dict. 
                예: {'entry_price': 10000, 'qty': 100, 'atr': 500, 'is_partially_sold': False, 'days_held': 2}
            date_str: 오늘 일자
            open_price, high, low, close: 당일 OHLC
            minute_bars: 분봉 데이터 (필수 아님)
            
        Returns:
            체결 내역 리스트: [{'time': '0930', 'price': ..., 'qty': ..., 'amount': ..., 'reason': '손절'}]
            매도 발생하지 않으면 빈 리스트 반환.
        """
        sells = []
        
        entry_price = position.get('entry_price', 0)
        current_qty = position.get('qty', 0)
        atr = position.get('atr', entry_price * 0.02)
        is_partially_sold = position.get('is_partially_sold', False)
        days_held = position.get('days_held', 0)
        
        # 아직 1주도 없거나, 진입 당일(days_held == 0)일 경우 당일엔 매도 안 함(스윙이므로)
        if current_qty <= 0:
            return []
            
        # 목표가와 손절가 설정
        take_profit_price = entry_price + (atr * self.profit_atr_multiplier)
        
        if is_partially_sold:
            # 1차 익절 통과 상태: 본절가 스톱 (마찰비용 감안 +0.345% 위)
            stop_price = entry_price * (1 + 0.00345)
        else:
            # 초기 스톱: ATR 기준 하드 스톱
            stop_price = entry_price - (atr * self.stop_atr_multiplier)
            
        # Minute 모드: 분봉 해상도로 목표가/손절가 정확한 터치 시간 계산
        if minute_bars:
            for m_bar in minute_bars:
                m_time = m_bar.get('dt', 'X152000')[-6:]
                m_high = _parse_price(str(m_bar.get('high_pric', 0)))
                m_low = _parse_price(str(m_bar.get('low_pric', 0)))
                m_close = _parse_price(str(m_bar.get('cur_prc', 0)))
                
                # 1. 1차 익절 확인 (아직 절반 매도 전일 때)
                if not position.get('is_partially_sold', False) and m_high >= take_profit_price:
                    sell_qty = current_qty // 2
                    fill_price = take_profit_price * (1 - self.slippage_rate)
                    if sell_qty > 0:
                        sells.append({
                            'time': m_time,
                            'price': fill_price,
                            'qty': sell_qty,
                            'amount': int(fill_price * sell_qty),
                            'reason': '1차_익절(50%)'
                        })
                        current_qty -= sell_qty
                        position['is_partially_sold'] = True
                        position['qty'] = current_qty
                        # 본절가 상향으로 즉각 스톱 조정
                        stop_price = entry_price * (1 + 0.00345)
                        logger.info(f"[{date_str} {m_time}] {stk_nm}({stk_cd}) 1차 익절 완료: {sell_qty}주 (수익률: {(take_profit_price/entry_price - 1)*100:.2f}%)")
                
                # 남은 물량이 없으면 종료
                if current_qty <= 0:
                    break
                    
                # 2. 손절/본절가 확인 (손절 시 더 큰 슬리피지 적용)
                if m_low <= stop_price:
                    fill_price = stop_price * (1 - self.stop_slippage_rate)
                    sells.append({
                        'time': m_time,
                        'price': fill_price,
                        'qty': current_qty,
                        'amount': int(fill_price * current_qty),
                        'reason': '본절가_청산' if position.get('is_partially_sold', False) else 'ATR_하드스톱'
                    })
                    logger.info(f"[{date_str} {m_time}] {stk_nm}({stk_cd}) 스톱 터치: 전량 매도 (수익률: {(stop_price/entry_price - 1)*100:.2f}%)")
                    current_qty = 0
                    break
                    
            # 장 종료 후에도 남은 물량이 있고, 만기일 도달 시 종가 청산
            if current_qty > 0 and days_held >= self.max_holding_days:
                sells.append({
                    'time': '1520',
                    'price': close,
                    'qty': current_qty,
                    'amount': int(close * current_qty),
                    'reason': '만기_청산(7일)'
                })
                logger.info(f"[{date_str} 15:20] {stk_nm}({stk_cd}) 만기 도달(7일): 종가 전량 매도 (수익률: {(close/entry_price - 1)*100:.2f}%)")
                current_qty = 0
                
        # Daily 모드: 일봉 OHLC만으로 판단 (단순 백테스트용)
        else:
            # 1. 시가에서 스톱 갭하락 터치 시 (시가 자체가 이미 불리하므로 슬리피지 추가 적용)
            if open_price <= stop_price:
                fill_price = open_price * (1 - self.stop_slippage_rate)
                sells.append({
                    'time': '0900',
                    'price': fill_price,
                    'qty': current_qty,
                    'amount': int(fill_price * current_qty),
                    'reason': '시가_스톱터치(갭하락)'
                })
                return sells

            # 2. 익절/손절 동시 터치 시, 시가로부터의 거리로 순서 추정
            #    - 시가→목표가 거리 vs 시가→손절가 거리 비교
            #    - 동일 거리일 경우 보수적으로 손절 우선 (worst-case)
            tp_hit = not is_partially_sold and high >= take_profit_price
            sl_hit = low <= stop_price

            if tp_hit and sl_hit:
                dist_to_tp = abs(take_profit_price - open_price)
                dist_to_sl = abs(open_price - stop_price)
                tp_first = dist_to_tp < dist_to_sl  # 동일 거리 시 손절 우선
            elif tp_hit:
                tp_first = True
            else:
                tp_first = False

            if tp_first and tp_hit:
                # 익절 먼저 실행 (슬리피지 적용)
                sell_qty = current_qty // 2
                tp_fill = take_profit_price * (1 - self.slippage_rate)
                if sell_qty > 0:
                    sells.append({
                        'time': '1200',
                        'price': tp_fill,
                        'qty': sell_qty,
                        'amount': int(tp_fill * sell_qty),
                        'reason': '1차_익절(50%)'
                    })
                    current_qty -= sell_qty
                    position['is_partially_sold'] = True
                    position['qty'] = current_qty
                    stop_price = entry_price * (1 + 0.00345)

                if current_qty <= 0:
                    return sells

                # 익절 후 남은 물량에 대해 손절 확인 (손절 슬리피지 적용)
                if sl_hit and low <= stop_price:
                    sl_fill = stop_price * (1 - self.stop_slippage_rate)
                    sells.append({
                        'time': '1400',
                        'price': sl_fill,
                        'qty': current_qty,
                        'amount': int(sl_fill * current_qty),
                        'reason': '본절가_청산'
                    })
                    current_qty = 0
                    return sells
            elif sl_hit:
                # 손절 먼저 실행 → 전량 청산 (익절 불가, 손절 슬리피지 적용)
                sl_fill = stop_price * (1 - self.stop_slippage_rate)
                sells.append({
                    'time': '1200',
                    'price': sl_fill,
                    'qty': current_qty,
                    'amount': int(sl_fill * current_qty),
                    'reason': '본절가_청산' if is_partially_sold else 'ATR_하드스톱'
                })
                current_qty = 0
                return sells

            # 3. 만기 청산
            if current_qty > 0 and days_held >= self.max_holding_days:
                sells.append({
                    'time': '1520',
                    'price': close,
                    'qty': current_qty,
                    'amount': int(close * current_qty),
                    'reason': '만기_청산(7일)'
                })
                
        return sells

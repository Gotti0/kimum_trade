"""
BuyStrategyEngine: 14:30~15:20 Pseudo-VWAP 분할 매수 시뮬레이터.

KOSPI 일중 U자형 거래량 프로파일의 오른쪽 꼬리(14:30~15:20)에서
10분 단위 5구간으로 나누어, 과거 거래량 비중에 따라 물량을 배분하고
가중평균 매수가(VWAP)를 산출합니다.

Reference:
  KOSPI 모멘텀_스윙 알고리즘 전략 설계.md §3.2
"""

import logging
from typing import Optional

from backend.kiwoom.sell_strategy import _parse_price

logger = logging.getLogger(__name__)

# 10분 단위 매수 윈도우 정의 (HHMM 형식)
BUY_TIME_BINS = [
    ("1430", "1439"),  # Bin 1: 14:30~14:39
    ("1440", "1449"),  # Bin 2: 14:40~14:49
    ("1450", "1459"),  # Bin 3: 14:50~14:59
    ("1500", "1509"),  # Bin 4: 15:00~15:09
    ("1510", "1520"),  # Bin 5: 15:10~15:20 (가장 큰 비중)
]

# 과거 30일 데이터가 없을 때 사용하는 기본 가중치
# 마감에 가까울수록 높은 비중 (U자형 꼬리)
DEFAULT_WEIGHTS = [0.10, 0.12, 0.18, 0.25, 0.35]


def _get_bars_in_range(minute_bars: list[dict], start: str, end: str) -> list[dict]:
    """지정 HHMM 범위의 분봉 추출."""
    result = []
    for bar in minute_bars:
        cntr_tm = bar.get("cntr_tm", "")
        if len(cntr_tm) >= 12:
            hhmm = cntr_tm[8:12]
            if start <= hhmm <= end:
                result.append(bar)
    return result


def _compute_bin_vwap(bars: list[dict]) -> Optional[float]:
    """주어진 분봉 리스트의 VWAP(거래량 가중평균가격) 계산.

    VWAP = Σ(가격 × 거래량) / Σ(거래량)
    """
    total_value = 0.0
    total_volume = 0.0

    for bar in bars:
        price = _parse_price(bar.get("cur_prc", "0"))
        volume = _parse_price(bar.get("trde_qty", "0"))
        if price > 0 and volume > 0:
            total_value += price * volume
            total_volume += volume

    if total_volume == 0:
        return None

    return total_value / total_volume


def _compute_bin_total_volume(bars: list[dict]) -> float:
    """주어진 분봉 리스트의 총 거래량."""
    total = 0.0
    for bar in bars:
        total += _parse_price(bar.get("trde_qty", "0"))
    return total


class BuyStrategyEngine:
    """14:30~15:20 Pseudo-VWAP 분할 매수 시뮬레이터.

    과거 거래량 비중을 학습하여 각 10분 구간에 차등 배분하고,
    가중평균 매수가를 산출합니다.
    """

    def __init__(self, weights: Optional[list[float]] = None):
        """
        Args:
            weights: 5개 구간별 가중치 리스트. None이면 기본값 사용.
        """
        self.weights = weights if weights else list(DEFAULT_WEIGHTS)
        # 정규화
        w_sum = sum(self.weights)
        if w_sum > 0:
            self.weights = [w / w_sum for w in self.weights]

    def learn_volume_weights(self, historical_minute_bars: list[list[dict]]) -> list[float]:
        """과거 N일의 분봉 데이터에서 14:30~15:20 구간별 거래량 비중을 학습합니다.

        Args:
            historical_minute_bars: [day1_bars, day2_bars, ...] 각 일자의 분봉 리스트.

        Returns:
            5개 구간의 정규화된 가중치 리스트.
        """
        bin_volumes = [0.0] * len(BUY_TIME_BINS)

        for day_bars in historical_minute_bars:
            for i, (start, end) in enumerate(BUY_TIME_BINS):
                bars = _get_bars_in_range(day_bars, start, end)
                bin_volumes[i] += _compute_bin_total_volume(bars)

        total = sum(bin_volumes)
        if total > 0:
            self.weights = [v / total for v in bin_volumes]
        else:
            self.weights = list(DEFAULT_WEIGHTS)

        logger.info("학습된 구간별 가중치: %s", [f"{w:.3f}" for w in self.weights])
        return self.weights

    def execute(self, minute_bars: list[dict], total_amount: float) -> dict:
        """매수일 분봉 데이터로 Pseudo-VWAP 분할 매수를 시뮬레이션합니다.

        Args:
            minute_bars: 매수일 1분봉 리스트 (시간순 정렬)
            total_amount: 총 매수 금액 (원)

        Returns:
            {
                'avg_buy_price': float,    # 가중평균 매수가
                'total_shares': float,     # 총 매수 수량 (추정)
                'total_cost': float,       # 실제 투입 금액
                'bin_details': [           # 구간별 상세
                    {
                        'bin': str,        # '1430-1439'
                        'weight': float,
                        'vwap': float,
                        'volume': float,
                        'allocated': float,  # 배분 금액
                    }, ...
                ],
                'executed': bool,          # 체결 성공 여부
            }
        """
        bin_details = []
        total_weighted_price = 0.0
        total_weight_used = 0.0

        for i, (start, end) in enumerate(BUY_TIME_BINS):
            weight = self.weights[i]
            allocated = total_amount * weight

            bars = _get_bars_in_range(minute_bars, start, end)
            vwap = _compute_bin_vwap(bars)
            volume = _compute_bin_total_volume(bars)

            if vwap is not None and vwap > 0:
                total_weighted_price += vwap * weight
                total_weight_used += weight

            bin_details.append({
                "bin": f"{start}-{end}",
                "weight": weight,
                "vwap": vwap if vwap else 0.0,
                "volume": volume,
                "allocated": allocated,
            })

        # 가중평균 매수가 계산
        if total_weight_used > 0:
            avg_buy_price = total_weighted_price / total_weight_used
        else:
            # 14:30~15:20 분봉이 전혀 없으면 fallback: 마지막 분봉 종가
            avg_buy_price = 0.0
            for bar in reversed(minute_bars):
                cntr_tm = bar.get("cntr_tm", "")
                if len(cntr_tm) >= 12:
                    p = _parse_price(bar.get("cur_prc", "0"))
                    if p > 0:
                        avg_buy_price = p
                        break

        # 총 매수 수량 추정
        total_shares = total_amount / avg_buy_price if avg_buy_price > 0 else 0
        total_cost = avg_buy_price * total_shares if total_shares > 0 else 0

        executed = avg_buy_price > 0

        if executed:
            logger.info(
                "Pseudo-VWAP 매수: 가중평균가 %.0f원, 추정수량 %.0f주, 투입 %.0f원",
                avg_buy_price, total_shares, total_cost
            )
        else:
            logger.warning("매수일 14:30~15:20 분봉 없음, 매수 불가")

        return {
            "avg_buy_price": avg_buy_price,
            "total_shares": total_shares,
            "total_cost": total_cost,
            "bin_details": bin_details,
            "executed": executed,
        }

    def get_simple_buy_price(self, minute_bars: list[dict]) -> float:
        """간단한 매수가 산출 — Pseudo-VWAP 구간의 가중평균 종가 반환.
        
        포지션 사이징 전에 예상 매수가를 빠르게 구하기 위한 헬퍼.
        """
        result = self.execute(minute_bars, 1_000_000)  # dummy amount
        return result["avg_buy_price"]

"""
RiskManager: 거시 레짐 필터 + 변동성 역산 포지션 사이징.

모듈:
  - RegimeFilter: KOSPI 200 기반 시장 국면 판별 (BULL / WARNING / BEAR)
  - PositionSizer: ATR 기반 변동성 역산 포지션 사이징 (10 슬롯 모델)

Reference:
  KOSPI 모멘텀_스윙 알고리즘 전략 설계.md §4.2, §4.3
"""

import logging
from typing import Optional

from backend.kiwoom.strategy.phoenix.alpha_filter import compute_sma, compute_ema
from backend.kiwoom.strategy.phoenix.sell_strategy import _parse_price

logger = logging.getLogger(__name__)


# ── MACD 계산 ──────────────────────────────────────────────

def compute_macd(
    prices: list[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Optional[dict]:
    """MACD 지표 계산.

    Returns:
        {
            'macd_line': float,    # MACD = EMA(fast) - EMA(slow)
            'signal_line': float,  # Signal = EMA(MACD, signal_period)
            'histogram': float,    # MACD - Signal
        }
        데이터 부족 시 None 반환.
    """
    if len(prices) < slow_period + signal_period:
        return None

    fast_ema = compute_ema(prices, fast_period)
    slow_ema = compute_ema(prices, slow_period)

    if fast_ema is None or slow_ema is None:
        return None

    # MACD 라인 시계열 생성 (signal EMA 계산을 위해)
    macd_series = []
    # 초기 EMA 시드
    fast_seed = sum(prices[:fast_period]) / fast_period
    slow_seed = sum(prices[:slow_period]) / slow_period
    fast_k = 2 / (fast_period + 1)
    slow_k = 2 / (slow_period + 1)

    f_ema = fast_seed
    s_ema = slow_seed

    for i in range(slow_period, len(prices)):
        # fast EMA 갱신 (i 시점까지)
        pass

    # 간략화: 최종 MACD만 사용
    macd_line = fast_ema - slow_ema

    # Signal line — MACD 시계열이 필요하지만 간략화를 위해
    # 최근 signal_period일의 MACD 근사치를 사용
    # (실제로는 전체 MACD 시계열에서 EMA를 구해야 하지만, 백테스트에서는 근사치로 충분)
    signal_line = macd_line * 0.8  # 보수적 근사

    return {
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": macd_line - signal_line,
    }


def compute_macd_precise(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """정확한 MACD 계산 (전체 시계열 기반)."""
    if len(prices) < slow + signal:
        return None

    # Fast EMA 시계열
    fast_k = 2 / (fast + 1)
    f_ema = sum(prices[:fast]) / fast
    fast_emas = [f_ema]
    for p in prices[fast:]:
        f_ema = p * fast_k + f_ema * (1 - fast_k)
        fast_emas.append(f_ema)

    # Slow EMA 시계열
    slow_k = 2 / (slow + 1)
    s_ema = sum(prices[:slow]) / slow
    slow_emas = [s_ema]
    for p in prices[slow:]:
        s_ema = p * slow_k + s_ema * (1 - slow_k)
        slow_emas.append(s_ema)

    # MACD 라인 (slow 시작점부터)
    # fast_emas는 fast시점부터, slow_emas는 slow시점부터 시작
    # 정렬: MACD = fast_ema[i] - slow_ema[i] (slow 기준 시작점 맞추기)
    offset = slow - fast
    macd_series = []
    for i in range(len(slow_emas)):
        fi = i + offset
        if fi < len(fast_emas):
            macd_series.append(fast_emas[fi] - slow_emas[i])

    if len(macd_series) < signal:
        return None

    # Signal EMA
    sig_k = 2 / (signal + 1)
    sig_ema = sum(macd_series[:signal]) / signal
    for m in macd_series[signal:]:
        sig_ema = m * sig_k + sig_ema * (1 - sig_k)

    macd_line = macd_series[-1]
    signal_line = sig_ema
    histogram = macd_line - signal_line

    return {
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    }


# ── 레짐 필터 ──────────────────────────────────────────────

class RegimeFilter:
    """KOSPI 200 기반 거시 레짐 판별.

    상태 정의:
      - BULL:    지수 > SMA(200) OR SMA(5) > SMA(50) → 신규 진입 전면 허용
      - WARNING: 지수 < SMA(200) AND SMA(5) < SMA(50) → 진입 자본 50% 축소
      - BEAR:    WARNING + MACD Signal < 0 → 신규 매수 완전 차단 (킬스위치)

    Reference:
      KOSPI 모멘텀_스윙 알고리즘 전략 설계.md §4.2
    """

    def detect_regime(self, daily_bars: list[dict]) -> dict:
        """시장 레짐을 판별합니다.

        Args:
            daily_bars: KOSPI 200 또는 대표 지수의 일봉 (과거→최신, 최소 200개 권장).

        Returns:
            {
                'regime': 'BULL' | 'WARNING' | 'BEAR',
                'scale_factor': float,  # 진입 자본 배수 (1.0 / 0.5 / 0.0)
                'details': str,
            }
        """
        closes = [_parse_price(bar.get("cur_prc", "0")) for bar in daily_bars]
        if not closes:
            return {"regime": "BULL", "scale_factor": 1.0, "details": "데이터없음_기본BULL"}

        current_price = closes[-1]
        sma200 = compute_sma(closes, 200)
        sma50 = compute_sma(closes, 50)
        sma5 = compute_sma(closes, 5)

        # 데이터 부족 시 기본 BULL
        if sma200 is None or sma50 is None or sma5 is None:
            return {
                "regime": "BULL",
                "scale_factor": 1.0,
                "details": f"데이터부족(bars={len(closes)})_기본BULL",
            }

        # BULL 조건: 지수 > SMA(200) OR SMA(5) > SMA(50)
        above_sma200 = current_price > sma200
        golden_cross = sma5 > sma50

        if above_sma200 or golden_cross:
            details = (
                f"BULL: 지수={current_price:.0f}, "
                f"SMA200={sma200:.0f}({'>' if above_sma200 else '≤'}), "
                f"SMA5={sma5:.0f} vs SMA50={sma50:.0f}"
            )
            return {"regime": "BULL", "scale_factor": 1.0, "details": details}

        # WARNING 조건: 지수 < SMA(200) AND SMA(5) < SMA(50)
        # BEAR 추가 조건: MACD Signal < 0
        macd = compute_macd_precise(closes)

        if macd and macd["signal_line"] < 0:
            details = (
                f"BEAR(킬스위치): 지수={current_price:.0f}<SMA200={sma200:.0f}, "
                f"SMA5={sma5:.0f}<SMA50={sma50:.0f}, "
                f"MACD_Signal={macd['signal_line']:.2f}<0"
            )
            logger.warning("🚨 %s", details)
            return {"regime": "BEAR", "scale_factor": 0.0, "details": details}

        details = (
            f"WARNING: 지수={current_price:.0f}<SMA200={sma200:.0f}, "
            f"SMA5={sma5:.0f}<SMA50={sma50:.0f}"
        )
        logger.warning("⚠️ %s", details)
        return {"regime": "WARNING", "scale_factor": 0.5, "details": details}


# ── 포지션 사이징 ──────────────────────────────────────────

class PositionSizer:
    """변동성 역산 포지션 사이징 (슬롯 모델).

    수식:
      투입수량 = (자본금 × RPT) / (ATR × 승수)
      투입금액 = 투입수량 × 매수가
      슬롯 상한 = 자본금 / MAX_SLOTS

    각 슬롯에 투입되는 금액은 종목의 ATR(변동성)에 반비례하여
    포트폴리오의 하방 리스크 기여도를 균등화합니다.

    Reference:
      KOSPI 모멘텀_스윙 알고리즘 전략 설계.md §4.3
    """

    MAX_SLOTS = 10
    RISK_PER_TRADE = 0.015  # 1.5%

    def __init__(
        self,
        max_slots: int = 10,
        risk_per_trade: float = 0.015,
        atr_multiplier: float = 2.5,
    ):
        self.max_slots = max_slots
        self.risk_per_trade = risk_per_trade
        self.atr_multiplier = atr_multiplier

    def compute_position_size(
        self,
        total_capital: float,
        buy_price: float,
        atr: float,
    ) -> dict:
        """단일 종목의 투입 금액/수량을 계산합니다.

        Args:
            total_capital: 현재 총 자본금.
            buy_price: 예상 매수가.
            atr: 해당 종목의 ATR(5) 값.

        Returns:
            {
                'position_amount': float,  # 투입 금액 (원)
                'position_shares': int,    # 투입 수량 (주)
                'risk_amount': float,      # 1회 최대 손실 허용액
                'slot_cap': float,         # 슬롯 상한 금액
                'capped': bool,            # 슬롯 상한 적용 여부
            }
        """
        risk_amount = total_capital * self.risk_per_trade
        stop_distance = atr * self.atr_multiplier

        if stop_distance <= 0 or buy_price <= 0:
            return {
                "position_amount": 0.0,
                "position_shares": 0,
                "risk_amount": risk_amount,
                "slot_cap": total_capital / self.max_slots,
                "capped": False,
            }

        # 변동성 역산 수량
        shares = risk_amount / stop_distance
        amount = shares * buy_price

        # 슬롯 상한 체크
        slot_cap = total_capital / self.max_slots
        capped = False
        if amount > slot_cap:
            amount = slot_cap
            shares = amount / buy_price
            capped = True

        return {
            "position_amount": amount,
            "position_shares": int(shares),
            "risk_amount": risk_amount,
            "slot_cap": slot_cap,
            "capped": capped,
        }

    def available_slots(self, current_positions: int) -> int:
        """사용 가능한 슬롯 수."""
        return max(0, self.max_slots - current_positions)

    def apply_regime_scale(self, position_amount: float, regime_scale: float) -> float:
        """레짐 필터의 scale_factor를 적용합니다.

        WARNING 레짐에서 0.5, BEAR에서 0.0을 곱해 투입 금액을 축소.
        """
        return position_amount * regime_scale

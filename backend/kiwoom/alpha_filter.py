"""
AlphaFilter: 스윙 전략을 위한 4단계 유니버스 필터링 엔진.

필터 파이프라인:
  1. 유동성 허들  — 20일 ADTV ≥ 500억, 시총 ≥ 3000억
  2. RVOL 허들   — 당일 거래대금 / 20일 ADTV ≥ 2.5
  3. 모멘텀 허들  — 종가 > SMA(10) AND 종가 > EMA(20) AND 일일 수익률 ≥ +4%
  4. 이격도 캡    — 100 < (종가/SMA(20)×100) ≤ 112

Reference:
  KOSPI 모멘텀_스윙 알고리즘 전략 설계.md §2
"""

import logging
from typing import Optional

from backend.kiwoom.sell_strategy import _parse_price

logger = logging.getLogger(__name__)

# ── 상수 ────────────────────────────────────────────────────
ADTV_THRESHOLD = 500_0000_0000       # 500억 원
MARKET_CAP_THRESHOLD = 3000_0000_0000  # 3,000억 원
RVOL_THRESHOLD = 2.5
DAILY_RETURN_THRESHOLD = 4.0          # +4%
DISPARITY_LOWER = 100.0
DISPARITY_UPPER = 112.0

SMA_SHORT_PERIOD = 10   # 단기 SMA
EMA_PERIOD = 20          # 지수이동평균
SMA_LONG_PERIOD = 20     # 장기 SMA (이격도 기준)


# ── 기술적 지표 계산 유틸리티 ─────────────────────────────────

def compute_sma(prices: list[float], period: int) -> Optional[float]:
    """단순이동평균(SMA) 계산.
    prices 는 과거→최신 정렬.  마지막 `period`개의 평균값 반환.
    """
    if len(prices) < period:
        return None
    window = prices[-period:]
    return sum(window) / period


def compute_ema(prices: list[float], period: int) -> Optional[float]:
    """지수이동평균(EMA) 계산.
    prices 는 과거→최신 정렬.  최소 `period`개 필요.
    """
    if len(prices) < period:
        return None
    # 초기 SMA를 시드로 사용
    ema = sum(prices[:period]) / period
    k = 2 / (period + 1)
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def compute_adtv(daily_bars: list[dict], period: int = 20) -> Optional[float]:
    """평균 일일 거래대금(ADTV) 계산.
    daily_bars: 과거→최신 정렬된 일봉 리스트.
    거래대금 = 종가 × 거래량  (trde_amt 필드가 있으면 우선 사용).
    """
    if len(daily_bars) < period:
        return None

    window = daily_bars[-period:]
    trade_values = []
    for bar in window:
        # trde_amt(거래대금) 필드가 있으면 우선 사용
        amt = bar.get("trde_amt")
        if amt is not None:
            val = _parse_price(str(amt))
        else:
            # 없으면 종가 × 거래량으로 추정
            close = _parse_price(bar.get("cur_prc", "0"))
            vol = _parse_price(bar.get("trde_qty", "0"))
            val = close * vol
        trade_values.append(val)

    return sum(trade_values) / len(trade_values) if trade_values else None


def compute_rvol(daily_bars: list[dict], period: int = 20) -> Optional[float]:
    """상대거래대금(RVOL) 계산.
    당일 거래대금 / 과거 period일 ADTV.
    """
    if len(daily_bars) < period + 1:
        return None

    adtv = compute_adtv(daily_bars[:-1], period)
    if not adtv or adtv == 0:
        return None

    today = daily_bars[-1]
    amt = today.get("trde_amt")
    if amt is not None:
        today_val = _parse_price(str(amt))
    else:
        close = _parse_price(today.get("cur_prc", "0"))
        vol = _parse_price(today.get("trde_qty", "0"))
        today_val = close * vol

    return today_val / adtv


def compute_disparity(close: float, sma: float) -> float:
    """이격도 = (종가 / SMA) × 100."""
    if sma == 0:
        return 0.0
    return (close / sma) * 100


def compute_daily_return(daily_bars: list[dict]) -> Optional[float]:
    """당일 수익률 (%) = (당일 종가 - 전일 종가) / 전일 종가 × 100."""
    if len(daily_bars) < 2:
        return None
    prev_close = _parse_price(daily_bars[-2].get("cur_prc", "0"))
    curr_close = _parse_price(daily_bars[-1].get("cur_prc", "0"))
    if prev_close == 0:
        return None
    return (curr_close - prev_close) / prev_close * 100


def estimate_market_cap(daily_bars: list[dict]) -> Optional[float]:
    """시가총액 추정.
    API에서 mkt_cap 필드가 없으면 None 반환.
    """
    if not daily_bars:
        return None
    latest = daily_bars[-1]
    mkt_cap = latest.get("mkt_cap")
    if mkt_cap is not None:
        return _parse_price(str(mkt_cap))
    # stk_info에서 제공될 수 있음 — 호출자가 별도로 주입
    return None


# ── 통합 지표 계산 ──────────────────────────────────────────

def compute_all_indicators(daily_bars: list[dict], market_cap: Optional[float] = None) -> dict:
    """일봉 데이터에서 필터링에 필요한 모든 기술적 지표를 산출합니다.

    Args:
        daily_bars: 과거→최신 정렬된 일봉 리스트 (최소 21개 필요)
        market_cap: 시가총액 (외부에서 주입, None이면 일봉에서 추정 시도)

    Returns:
        {
            'close': float,
            'daily_return': float | None,
            'sma10': float | None,
            'ema20': float | None,
            'sma20': float | None,
            'disparity20': float,
            'adtv20': float | None,
            'rvol': float | None,
            'market_cap': float | None,
        }
    """
    closes = [_parse_price(bar.get("cur_prc", "0")) for bar in daily_bars]

    result = {
        "close": closes[-1] if closes else 0.0,
        "daily_return": compute_daily_return(daily_bars),
        "sma10": compute_sma(closes, SMA_SHORT_PERIOD),
        "ema20": compute_ema(closes, EMA_PERIOD),
        "sma20": compute_sma(closes, SMA_LONG_PERIOD),
        "adtv20": compute_adtv(daily_bars, 20),
        "rvol": compute_rvol(daily_bars, 20),
        "market_cap": market_cap if market_cap else estimate_market_cap(daily_bars),
    }

    sma20 = result["sma20"]
    result["disparity20"] = compute_disparity(result["close"], sma20) if sma20 else 0.0

    return result


# ── AlphaFilter 클래스 ──────────────────────────────────────

class AlphaFilter:
    """4단계 유니버스 필터링 엔진.

    각 필터는 독립적으로 on/off 가능하며, 탈락 사유를 로그로 남깁니다.
    """

    def __init__(
        self,
        adtv_threshold: float = ADTV_THRESHOLD,
        market_cap_threshold: float = MARKET_CAP_THRESHOLD,
        rvol_threshold: float = RVOL_THRESHOLD,
        daily_return_threshold: float = DAILY_RETURN_THRESHOLD,
        disparity_lower: float = DISPARITY_LOWER,
        disparity_upper: float = DISPARITY_UPPER,
    ):
        self.adtv_threshold = adtv_threshold
        self.market_cap_threshold = market_cap_threshold
        self.rvol_threshold = rvol_threshold
        self.daily_return_threshold = daily_return_threshold
        self.disparity_lower = disparity_lower
        self.disparity_upper = disparity_upper

    def check_liquidity(self, indicators: dict) -> tuple[bool, str]:
        """Step 1: 유동성 허들."""
        adtv = indicators.get("adtv20")
        mkt_cap = indicators.get("market_cap")

        if adtv is not None and adtv < self.adtv_threshold:
            return False, f"ADTV부족({adtv/1e8:.0f}억 < {self.adtv_threshold/1e8:.0f}억)"

        if mkt_cap is not None and mkt_cap < self.market_cap_threshold:
            return False, f"시총부족({mkt_cap/1e8:.0f}억 < {self.market_cap_threshold/1e8:.0f}억)"

        if adtv is None and mkt_cap is None:
            return False, "유동성데이터없음"

        return True, "유동성OK"

    def check_rvol(self, indicators: dict) -> tuple[bool, str]:
        """Step 2: 상대거래대금 허들."""
        rvol = indicators.get("rvol")
        if rvol is None:
            return False, "RVOL계산불가"
        if rvol < self.rvol_threshold:
            return False, f"RVOL부족({rvol:.1f} < {self.rvol_threshold})"
        return True, f"RVOL={rvol:.1f}"

    def check_momentum(self, indicators: dict) -> tuple[bool, str]:
        """Step 3: 이동평균 돌파 + 수익률 허들."""
        close = indicators.get("close", 0)
        sma10 = indicators.get("sma10")
        ema20 = indicators.get("ema20")
        daily_ret = indicators.get("daily_return")

        if sma10 is None or ema20 is None or daily_ret is None:
            return False, "모멘텀지표부족"

        reasons = []
        if close <= sma10:
            reasons.append(f"종가({close:.0f})≤SMA10({sma10:.0f})")
        if close <= ema20:
            reasons.append(f"종가({close:.0f})≤EMA20({ema20:.0f})")
        if daily_ret < self.daily_return_threshold:
            reasons.append(f"수익률({daily_ret:.1f}%)<{self.daily_return_threshold}%")

        if reasons:
            return False, "모멘텀탈락: " + ", ".join(reasons)

        return True, f"모멘텀OK(ret={daily_ret:.1f}%)"

    def check_disparity(self, indicators: dict) -> tuple[bool, str]:
        """Step 4: 이격도 역추세 상단 필터."""
        disp = indicators.get("disparity20", 0)

        if disp <= self.disparity_lower:
            return False, f"이격도하한({disp:.1f}≤{self.disparity_lower})"
        if disp > self.disparity_upper:
            return False, f"이격도상한({disp:.1f}>{self.disparity_upper})"

        return True, f"이격도OK({disp:.1f})"

    def apply_all_filters(self, indicators: dict) -> tuple[bool, list[str]]:
        """4단계 필터를 순차 적용합니다.

        Returns:
            (passed, reasons): 통과 여부와 각 단계의 결과 메시지 리스트.
        """
        filters = [
            ("유동성", self.check_liquidity),
            ("RVOL", self.check_rvol),
            ("모멘텀", self.check_momentum),
            ("이격도", self.check_disparity),
        ]

        reasons = []
        for name, check_fn in filters:
            passed, reason = check_fn(indicators)
            reasons.append(f"[{name}] {reason}")
            if not passed:
                return False, reasons

        return True, reasons

    def screen_universe(
        self,
        candidates: list[dict],
        daily_bars_by_stock: dict[str, list[dict]],
        market_caps: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        """후보 종목 리스트에 4단계 필터를 적용하여 통과 종목만 반환합니다.

        Args:
            candidates: [{'stk_cd': '005930', 'stk_nm': '삼성전자', ...}, ...]
            daily_bars_by_stock: {stk_cd: [일봉 리스트]}
            market_caps: {stk_cd: 시가총액}  (optional)

        Returns:
            필터 통과한 종목 리스트 (원본 dict에 'indicators' 키 추가).
        """
        if market_caps is None:
            market_caps = {}

        passed_stocks = []

        for stock in candidates:
            stk_cd = stock.get("stk_cd", "")
            stk_nm = stock.get("stk_nm", "?")

            bars = daily_bars_by_stock.get(stk_cd, [])
            if len(bars) < SMA_LONG_PERIOD + 1:
                logger.debug("[%s %s] 일봉 부족 (%d개), 건너뜀", stk_cd, stk_nm, len(bars))
                continue

            mkt_cap = market_caps.get(stk_cd)
            indicators = compute_all_indicators(bars, market_cap=mkt_cap)

            passed, reasons = self.apply_all_filters(indicators)

            if passed:
                logger.info("[%s %s] 필터 통과: %s", stk_cd, stk_nm, " | ".join(reasons))
                stock_with_indicators = {**stock, "indicators": indicators}
                passed_stocks.append(stock_with_indicators)
            else:
                logger.debug("[%s %s] 필터 탈락: %s", stk_cd, stk_nm, reasons[-1])

        logger.info("유니버스 스크리닝 결과: %d/%d 종목 통과", len(passed_stocks), len(candidates))
        return passed_stocks

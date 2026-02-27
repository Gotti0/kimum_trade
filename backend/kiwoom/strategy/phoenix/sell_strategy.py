"""
SellStrategyEngine: 매수_매도_pseudocode.md의 매도 전략을 분봉 데이터에 적용합니다.

핵심 로직:
  1. 09:00 시가 확인
  2. 09:01~09:15 상한가 도달 시 홀드 + 트레일링 스톱(-8%)
  3. 09:14 수익률 기반 매도 시간대 결정
  4. 해당 시간대의 분봉 종가로 매도 체결
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_price(raw: str) -> float:
    """키움 API 가격 문자열(예: '+12000', '-5000')을 양수 float로 변환."""
    if not raw:
        return 0.0
    # 부호(+, -)와 콤마를 제거하고 절대값으로 변환
    clean = str(raw).replace("+", "").replace("-", "").replace(",", "")
    try:
        return float(clean)
    except ValueError:
        return 0.0


def _get_bar_at(minute_bars: list[dict], target_time: str) -> Optional[dict]:
    """특정 시각(HHMM)에 가장 가까운 분봉을 반환합니다.

    Args:
        minute_bars: 분봉 리스트 [{cntr_tm: 'YYYYMMDDHHMMSS', cur_prc, ...}, ...]
        target_time: 'HHMM' 형식 (예: '0914')
    """
    for bar in minute_bars:
        cntr_tm = bar.get("cntr_tm", "")
        # cntr_tm 형식: YYYYMMDDHHMMSS → HHMM = [8:12]
        if len(cntr_tm) >= 12:
            hhmm = cntr_tm[8:12]
            if hhmm == target_time:
                return bar
    return None


def _get_bars_in_range(minute_bars: list[dict], start_hhmm: str, end_hhmm: str) -> list[dict]:
    """특정 시간 범위의 분봉들을 반환합니다.

    Args:
        start_hhmm: 시작 시각 (HHMM)
        end_hhmm: 종료 시각 (HHMM)
    """
    result = []
    for bar in minute_bars:
        cntr_tm = bar.get("cntr_tm", "")
        if len(cntr_tm) >= 12:
            hhmm = cntr_tm[8:12]
            if start_hhmm <= hhmm <= end_hhmm:
                result.append(bar)
    return result


class SellStrategyEngine:
    """매도 전략을 분봉 데이터에 적용하여 매도 결과를 산출합니다."""

    def execute(
        self,
        minute_bars: list[dict],
        buy_price: float,
        upper_limit_price: float,
    ) -> dict:
        """분봉 데이터에 매도 전략을 적용합니다.

        Args:
            minute_bars: 매도일 1분봉 리스트 (시간순 정렬)
            buy_price: 매수가
            upper_limit_price: 상한가 (ka10007에서 조회)

        Returns:
            {
                'sell_price': float,      # 매도 체결가
                'sell_time': str,         # 매도 시각 (HHMM)
                'sell_reason': str,       # 매도 사유
                'return_rate': float,     # 수익률 (%)
                'open_price': float,      # 시가
                'hit_upper_limit': bool,  # 상한가 도달 여부
            }
        """
        if not minute_bars:
            return self._make_result(buy_price, buy_price, "0900", "분봉데이터없음", False)

        # ── 1단계: 시가 확인 ────────────────────────────────
        open_bar = _get_bar_at(minute_bars, "0901")
        if not open_bar:
            # 0901이 없으면 가장 이른 09시대 분봉 사용
            for bar in minute_bars:
                cntr_tm = bar.get("cntr_tm", "")
                if len(cntr_tm) >= 12 and cntr_tm[8:10] == "09":
                    open_bar = bar
                    break

        if not open_bar:
            return self._make_result(buy_price, buy_price, "0900", "시가분봉없음", False)

        open_price = _parse_price(open_bar.get("cur_prc", "0"))

        # ── 2단계: 상한가 체크 (09:01 ~ 09:15) ──────────────
        hit_upper_limit = False
        early_bars = _get_bars_in_range(minute_bars, "0901", "0915")

        for bar in early_bars:
            bar_price = _parse_price(bar.get("high_pric", "0"))
            if bar_price >= upper_limit_price and upper_limit_price > 0:
                hit_upper_limit = True
                break

        if hit_upper_limit:
            # 상한가 도달 → 홀드, 트레일링 스톱 -8%
            return self._handle_upper_limit(
                minute_bars, buy_price, open_price, upper_limit_price
            )

        # ── 3단계: 09:14 수익률 산정 ────────────────────────
        bar_0914 = _get_bar_at(minute_bars, "0914")
        if not bar_0914:
            # 0914이 없으면 가장 가까운 분봉 사용
            bar_0914 = _get_bar_at(minute_bars, "0913") or _get_bar_at(minute_bars, "0915")

        if not bar_0914:
            # 14분 봉도 없으면 기본 시간대(09:24~09:27)로 매도
            return self._sell_in_range(
                minute_bars, buy_price, open_price, "0924", "0927",
                "14분봉없음_기본매도", hit_upper_limit
            )

        price_at_0914 = _parse_price(bar_0914.get("cur_prc", "0"))

        if open_price == 0:
            return_rate = 0.0
        else:
            return_rate = (price_at_0914 - open_price) / open_price * 100

        logger.info("09:14 수익률: %.2f%% (시가: %.0f, 14분가: %.0f)",
                     return_rate, open_price, price_at_0914)

        # ── 4단계: 수익률 구간별 매도 시간대 결정 ─────────────
        if return_rate <= -9:
            sell_start, sell_end = "0924", "0927"
        elif return_rate <= -4:
            sell_start, sell_end = "0921", "0922"
        elif return_rate <= -0.1:
            sell_start, sell_end = "0919", "0920"
        elif return_rate <= 4:
            sell_start, sell_end = "0924", "0927"
        elif return_rate <= 9:
            sell_start, sell_end = "0920", "0924"
        else:  # > 9%
            sell_start, sell_end = "0917", "0919"

        reason = f"14분수익률({return_rate:.1f}%)→{sell_start}~{sell_end}매도"

        return self._sell_in_range(
            minute_bars, buy_price, open_price, sell_start, sell_end,
            reason, hit_upper_limit
        )

    def _handle_upper_limit(
        self, minute_bars: list, buy_price: float,
        open_price: float, upper_limit_price: float
    ) -> dict:
        """상한가 도달 시: 홀드 후 -8% 하락 시 매도, 아니면 종가 매도."""
        trailing_stop = upper_limit_price * 0.92

        # 09:15 이후 분봉에서 trailing stop 체크
        post_bars = _get_bars_in_range(minute_bars, "0916", "1530")
        for bar in post_bars:
            low = _parse_price(bar.get("low_pric", "0"))
            if low <= trailing_stop and low > 0:
                sell_price = trailing_stop
                sell_time = bar.get("cntr_tm", "")[8:12] if len(bar.get("cntr_tm", "")) >= 12 else "????"
                return self._make_result(
                    buy_price, sell_price, sell_time,
                    f"상한가도달→트레일링스톱({trailing_stop:.0f})", True, open_price
                )

        # trailing stop에 안 걸리면 마지막 분봉(종가)에서 매도
        last_bar = minute_bars[-1] if minute_bars else None
        if last_bar:
            sell_price = _parse_price(last_bar.get("cur_prc", "0"))
            sell_time = last_bar.get("cntr_tm", "")[8:12] if len(last_bar.get("cntr_tm", "")) >= 12 else "1530"
        else:
            sell_price = upper_limit_price
            sell_time = "1530"

        return self._make_result(
            buy_price, sell_price, sell_time,
            "상한가도달→종가매도", True, open_price
        )

    def _sell_in_range(
        self, minute_bars: list, buy_price: float, open_price: float,
        sell_start: str, sell_end: str, reason: str, hit_upper_limit: bool
    ) -> dict:
        """지정 시간 범위에서 매도합니다. 범위 내 마지막 분봉 종가를 체결가로."""
        sell_bars = _get_bars_in_range(minute_bars, sell_start, sell_end)

        if sell_bars:
            # 매도 시간대의 마지막 분봉 종가를 체결가로 사용
            sell_bar = sell_bars[-1]
            sell_price = _parse_price(sell_bar.get("cur_prc", "0"))
            sell_time = sell_bar.get("cntr_tm", "")[8:12] if len(sell_bar.get("cntr_tm", "")) >= 12 else sell_end
        else:
            # 해당 시간대 분봉이 없으면 종가 사용
            logger.warning("매도 시간대 %s~%s 분봉 없음, 종가 사용", sell_start, sell_end)
            last_bar = minute_bars[-1] if minute_bars else None
            sell_price = _parse_price(last_bar.get("cur_prc", "0")) if last_bar else buy_price
            sell_time = "1530"
            reason += "(시간대분봉없음→종가)"

        return self._make_result(buy_price, sell_price, sell_time, reason, hit_upper_limit, open_price)

    @staticmethod
    def _make_result(
        buy_price: float, sell_price: float, sell_time: str,
        reason: str, hit_upper_limit: bool, open_price: float = 0.0
    ) -> dict:
        if buy_price > 0:
            return_rate = (sell_price - buy_price) / buy_price * 100
        else:
            return_rate = 0.0

        return {
            "sell_price": sell_price,
            "sell_time": sell_time,
            "sell_reason": reason,
            "return_rate": return_rate,
            "open_price": open_price,
            "hit_upper_limit": hit_upper_limit,
        }


# ══════════════════════════════════════════════════════════════
#  SwingSellStrategyEngine: 3~5일 스윙용 ATR 트레일링 스톱 매도 전략
# ══════════════════════════════════════════════════════════════

def compute_atr(daily_bars: list[dict], period: int = 5) -> Optional[float]:
    """Modified ATR(Average True Range) 계산.

    ATR = SMA(True Range, period)
    True Range = max(High - Low, |High - Prev Close|, |Low - Prev Close|)

    Args:
        daily_bars: 과거→최신 정렬된 일봉 리스트 (최소 period+1개).
        period: ATR 기간 (기본 5일).

    Returns:
        ATR 값 (float) 또는 데이터 부족 시 None.
    """
    if len(daily_bars) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(daily_bars)):
        high = _parse_price(daily_bars[i].get("high_pric", "0"))
        low = _parse_price(daily_bars[i].get("low_pric", "0"))
        prev_close = _parse_price(daily_bars[i - 1].get("cur_prc", "0"))

        if high == 0 or low == 0 or prev_close == 0:
            continue

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # SMA 평활화를 적용한 Modified ATR
    recent_trs = true_ranges[-period:]
    return sum(recent_trs) / len(recent_trs)


class SwingSellStrategyEngine:
    """3~5일 스윙 전략용 ATR 트레일링 스톱 매도 엔진.

    핵심 로직:
      1. 진입일 기준 ATR(5) 계산
      2. 매일 종가 기준 스톱 라인 = 종가 - ATR × 승수
      3. 스톱 라인은 상승만 허용 (래칫 메커니즘)
      4. 스톱 터치 시 해당 분봉 종가로 매도
      5. 최대 보유일(max_hold_days) 초과 시 종가 강제 청산

    Reference:
      KOSPI 모멘텀_스윙 알고리즘 전략 설계.md §4.1
    """

    ATR_PERIOD = 5
    ATR_MULTIPLIER = 2.5
    MAX_HOLD_DAYS = 5

    def __init__(
        self,
        atr_period: int = 5,
        atr_multiplier: float = 2.5,
        max_hold_days: int = 5,
    ):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_days = max_hold_days

    def execute_multi_day(
        self,
        daily_bars: list[dict],
        minute_bars_by_date: dict[str, list[dict]],
        buy_price: float,
        entry_date: str,
        holding_dates: list[str],
    ) -> dict:
        """여러 날에 걸쳐 ATR 트레일링 스톱을 추적합니다.

        Args:
            daily_bars: 진입 이전~보유 기간까지의 일봉 (과거→최신).
            minute_bars_by_date: {YYYYMMDD: [분봉 리스트]} — 보유 기간 중 분봉.
            buy_price: 매수 평균 단가.
            entry_date: 진입일 (YYYYMMDD).
            holding_dates: 보유 기간의 영업일 리스트 [진입 다음날, ...].

        Returns:
            {
                'sell_price': float,
                'sell_date': str,
                'sell_time': str,      # HHMM or 'CLOSE'
                'sell_reason': str,
                'return_rate': float,  # %
                'hold_days': int,
                'atr': float,
                'max_stop_line': float,
                'daily_stops': [{date, close, stop_line}, ...],
            }
        """
        # ATR 계산 — 진입일까지의 일봉으로
        entry_idx = -1
        for i, bar in enumerate(daily_bars):
            if bar.get("dt") == entry_date:
                entry_idx = i
                break

        if entry_idx < 0:
            entry_idx = len(daily_bars) - 1

        # 진입일까지의 일봉으로 ATR 계산
        bars_up_to_entry = daily_bars[:entry_idx + 1]
        atr = compute_atr(bars_up_to_entry, self.atr_period)

        if atr is None or atr == 0:
            # ATR 계산 불가 시 buy_price 기준 고정 스톱 (-5%)
            atr = buy_price * 0.02  # fallback
            logger.warning("ATR 계산 불가, fallback ATR=%.0f 사용", atr)

        stop_distance = atr * self.atr_multiplier
        stop_line = buy_price - stop_distance
        max_stop_line = stop_line
        daily_stops = []

        logger.info(
            "스윙 매도 시작: 매수가=%.0f, ATR=%.0f, 스톱간격=%.0f, 초기스톱=%.0f",
            buy_price, atr, stop_distance, stop_line,
        )

        # 보유 기간 순회
        for day_num, hold_date in enumerate(holding_dates, start=1):
            # 최대 보유일 초과 → 강제 청산
            if day_num > self.max_hold_days:
                # 이전 날의 마지막 종가로 청산
                prev_date = holding_dates[day_num - 2] if day_num >= 2 else entry_date
                prev_bars = minute_bars_by_date.get(prev_date, [])
                sell_price = buy_price  # fallback
                if prev_bars:
                    sell_price = _parse_price(prev_bars[-1].get("cur_prc", "0"))

                return self._make_swing_result(
                    buy_price, sell_price, prev_date, "CLOSE",
                    f"최대보유일({self.max_hold_days}일)초과_강제청산",
                    day_num - 1, atr, max_stop_line, daily_stops,
                )

            # 해당 일의 분봉 가져오기
            day_minute_bars = minute_bars_by_date.get(hold_date, [])

            # 장중 스톱 체크: 저가가 스톱 라인 이하인 분봉 탐색
            for bar in day_minute_bars:
                low = _parse_price(bar.get("low_pric", "0"))
                if low > 0 and low <= stop_line:
                    # 스톱 터치! 해당 분봉 종가로 매도
                    sell_price = _parse_price(bar.get("cur_prc", "0"))
                    if sell_price <= 0:
                        sell_price = stop_line
                    sell_time = bar.get("cntr_tm", "")[8:12] if len(bar.get("cntr_tm", "")) >= 12 else "????"

                    return self._make_swing_result(
                        buy_price, sell_price, hold_date, sell_time,
                        f"ATR트레일링스톱(스톱={stop_line:.0f},저가={low:.0f})",
                        day_num, atr, max_stop_line, daily_stops,
                    )

            # 스톱에 안 걸린 경우 → 일봉 종가 기준 스톱 라인 갱신
            # 해당 날의 일봉에서 종가 찾기
            day_close = 0.0
            for bar in daily_bars:
                if bar.get("dt") == hold_date:
                    day_close = _parse_price(bar.get("cur_prc", "0"))
                    break

            if day_close == 0 and day_minute_bars:
                day_close = _parse_price(day_minute_bars[-1].get("cur_prc", "0"))

            if day_close > 0:
                new_stop = day_close - stop_distance
                # 래칫: 스톱 라인은 상승만 허용
                if new_stop > stop_line:
                    stop_line = new_stop
                    max_stop_line = max(max_stop_line, stop_line)
                    logger.debug(
                        "Day %d (%s): 스톱 상향 → %.0f (종가=%.0f)",
                        day_num, hold_date, stop_line, day_close,
                    )

            daily_stops.append({
                "date": hold_date,
                "close": day_close,
                "stop_line": stop_line,
            })

        # 모든 보유일을 스톱 없이 통과 → 마지막 날 종가 매도
        last_date = holding_dates[-1] if holding_dates else entry_date
        last_bars = minute_bars_by_date.get(last_date, [])
        sell_price = buy_price  # fallback
        if last_bars:
            sell_price = _parse_price(last_bars[-1].get("cur_prc", "0"))
        elif daily_stops:
            sell_price = daily_stops[-1]["close"]

        return self._make_swing_result(
            buy_price, sell_price, last_date, "CLOSE",
            f"보유만기({len(holding_dates)}일)_종가청산",
            len(holding_dates), atr, max_stop_line, daily_stops,
        )

    @staticmethod
    def _make_swing_result(
        buy_price: float,
        sell_price: float,
        sell_date: str,
        sell_time: str,
        reason: str,
        hold_days: int,
        atr: float,
        max_stop_line: float,
        daily_stops: list[dict],
    ) -> dict:
        if buy_price > 0:
            return_rate = (sell_price - buy_price) / buy_price * 100
        else:
            return_rate = 0.0

        return {
            "sell_price": sell_price,
            "sell_date": sell_date,
            "sell_time": sell_time,
            "sell_reason": reason,
            "return_rate": return_rate,
            "hold_days": hold_days,
            "atr": atr,
            "max_stop_line": max_stop_line,
            "daily_stops": daily_stops,
        }

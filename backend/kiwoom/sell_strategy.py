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

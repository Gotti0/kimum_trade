"""
MomentumScorer: 듀얼 모멘텀 평가 및 유니버스 필터링 모듈.

역할:
  1. 동적 유니버스 필터링 — ADTV ≥ 50억 원 종목만 편입
  2. 상대 모멘텀(Relative Momentum) — 3/6/12개월 수익률 합산 스코어
  3. 절대 모멘텀(Absolute Momentum) — 12개월 수익률 < 0% 종목 제거
  4. 최종 Top-N 종목 선정

설계 문서 참조:
  §3.1 동적 유니버스 필터링
  §3.2 듀얼 모멘텀 스코어링 로직
"""

import logging
from typing import List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── 모멘텀 윈도우 상수 (영업일 기준) ───────────────────
#   1개월 ≈ 21 영업일
#   3개월 = 63일,  6개월 = 126일,  12개월 = 252일
LOOKBACK_3M = 63
LOOKBACK_6M = 126
LOOKBACK_12M = 252


class MomentumScorer:
    """동적 유니버스 생성 및 듀얼 모멘텀 스코어링을 수행합니다.

    3개월·6개월·12개월 수익률의 산술 평균을 상대 모멘텀 스코어로 산출하고,
    절대 모멘텀 필터(12개월 수익률 ≥ 무위험수익률)를 적용하여
    최종 Top-N 종목을 선정합니다.

    사용 예시::

        scorer = MomentumScorer(top_n=20)
        selected = scorer.select_assets(hist_prices, hist_trading_value)
    """

    def __init__(
        self,
        top_n: int = 20,
        min_trading_value: float = 5e9,
        risk_free_rate: float = 0.0,
    ):
        """
        Args:
            top_n: 최종 선정할 상위 종목 수 (기본 20).
            min_trading_value: 동적 유니버스 편입 기준 ADTV (기본 50억 원).
                               DataHandler에서 이미 MA(20).shift(1) 적용된 값 사용.
            risk_free_rate: 절대 모멘텀 필터 기준 수익률.
                           0.0이면 12개월 수익률이 음수인 종목만 제거.
        """
        self.top_n = top_n
        self.min_trading_value = min_trading_value
        self.risk_free_rate = risk_free_rate

    # ═══════════════════════════════════════════════════
    #  내부 헬퍼
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _calculate_returns(
        prices: pd.DataFrame, periods: int
    ) -> pd.Series:
        """지정된 영업일(periods) 동안의 누적 수익률을 벡터 연산으로 계산합니다.

        (현재 종가 / N일 전 종가) - 1.0

        Args:
            prices: 종가 DataFrame (행=영업일, 열=종목코드).
                    최소 (periods + 1)개 행이 있어야 유효.
            periods: 룩백 일수.

        Returns:
            종목별 수익률 Series. 데이터 부족 시 NaN.
        """
        if len(prices) <= periods:
            return pd.Series(dtype=float, index=prices.columns)

        current = prices.iloc[-1]
        past = prices.iloc[-(periods + 1)]

        # 0으로 나누기 방지
        with np.errstate(divide="ignore", invalid="ignore"):
            ret = (current / past) - 1.0

        return ret

    @staticmethod
    def _calculate_returns_all(
        prices: pd.DataFrame, periods: int
    ) -> pd.DataFrame:
        """모든 시점에서의 N일 수익률을 한 번에 계산합니다 (벡터화).

        Args:
            prices: 종가 DataFrame.
            periods: 룩백 일수.

        Returns:
            동일 shape의 DataFrame (각 행 = 해당 시점 기준 N일 수익률).
        """
        past = prices.shift(periods)
        with np.errstate(divide="ignore", invalid="ignore"):
            ret = (prices / past) - 1.0
        return ret

    # ═══════════════════════════════════════════════════
    #  핵심 API
    # ═══════════════════════════════════════════════════

    def select_assets(
        self,
        hist_prices: pd.DataFrame,
        hist_trading_value: pd.DataFrame,
    ) -> List[str]:
        """모멘텀 투자 로직에 따른 최종 타겟 종목 코드를 추출합니다.

        Args:
            hist_prices: 기준일까지의 종가 DataFrame (미래 참조 차단 완료).
            hist_trading_value: 기준일까지의 거래대금 MA(20) DataFrame
                                (shift(1) 적용 완료).

        Returns:
            상위 top_n 종목의 코드 리스트. 조건 미달 시 빈 리스트.
        """
        # ── 1단계: 동적 유니버스 필터링 ──
        # 가장 최근 행의 ADTV가 min_trading_value 이상인 종목만 추출
        if hist_trading_value.empty:
            logger.warning("거래대금 데이터가 비어있습니다.")
            return []

        latest_tv = hist_trading_value.iloc[-1]
        universe = latest_tv[latest_tv >= self.min_trading_value].index.tolist()

        if not universe:
            logger.warning(
                "ADTV ≥ %.0f억 조건을 충족하는 종목이 없습니다.",
                self.min_trading_value / 1e8,
            )
            return []

        logger.info(
            "동적 유니버스: ADTV ≥ %.0f억 → %d 종목 편입",
            self.min_trading_value / 1e8,
            len(universe),
        )

        # 유니버스 종목만의 가격 서브셋
        univ_prices = hist_prices[universe]

        # ── 2단계: 상대 모멘텀 계산 ──
        # 3개월(63일), 6개월(126일), 12개월(252일) 수익률
        ret_3m = self._calculate_returns(univ_prices, LOOKBACK_3M)
        ret_6m = self._calculate_returns(univ_prices, LOOKBACK_6M)
        ret_12m = self._calculate_returns(univ_prices, LOOKBACK_12M)

        # 세 기간 수익률의 산술 평균 = 상대 모멘텀 스코어
        # S_i = (R_3m + R_6m + R_12m) / 3
        momentum_score = (ret_3m + ret_6m + ret_12m) / 3.0

        # ── 3단계: 절대 모멘텀 필터 ──
        # 12개월 수익률이 무위험 수익률(기본 0%) 미만인 종목 제거
        abs_filter_mask = ret_12m < self.risk_free_rate
        n_filtered = abs_filter_mask.sum()
        momentum_score[abs_filter_mask] = np.nan

        if n_filtered > 0:
            logger.info(
                "절대 모멘텀 필터: 12M 수익률 < %.1f%% → %d 종목 제거",
                self.risk_free_rate * 100,
                n_filtered,
            )

        # ── 4단계: Top-N 선정 ──
        # NaN 제거 후 스코어 내림차순 정렬
        valid_scores = momentum_score.dropna().sort_values(ascending=False)
        top_assets = valid_scores.head(self.top_n).index.tolist()

        logger.info(
            "듀얼 모멘텀 Top-%d 선정 완료 (%d 후보 중 %d 선정)",
            self.top_n,
            len(valid_scores),
            len(top_assets),
        )

        # 디버그: 상위 5개 종목 스코어 로깅
        for i, code in enumerate(top_assets[:5]):
            logger.debug(
                "  #%d %s: Score=%.4f (3M=%.2f%%, 6M=%.2f%%, 12M=%.2f%%)",
                i + 1,
                code,
                valid_scores[code],
                ret_3m.get(code, 0) * 100,
                ret_6m.get(code, 0) * 100,
                ret_12m.get(code, 0) * 100,
            )

        return top_assets

    def score_universe(
        self,
        hist_prices: pd.DataFrame,
        hist_trading_value: pd.DataFrame,
    ) -> pd.DataFrame:
        """유니버스 내 전 종목의 상세 스코어링 결과를 반환합니다.

        select_assets()와 동일한 로직이지만, Top-N 절삭 없이 전체 스코어와
        개별 수익률을 DataFrame으로 반환하여 분석·디버깅에 활용합니다.

        Args:
            hist_prices: 기준일까지의 종가 DataFrame.
            hist_trading_value: 기준일까지의 거래대금 DataFrame.

        Returns:
            DataFrame with columns:
                - ret_3m:  3개월 수익률
                - ret_6m:  6개월 수익률
                - ret_12m: 12개월 수익률
                - score:   상대 모멘텀 스코어
                - abs_pass: 절대 모멘텀 통과 여부
                - rank:    스코어 순위
            인덱스 = 종목코드. 유니버스 편입 종목만 포함.
        """
        if hist_trading_value.empty:
            return pd.DataFrame()

        latest_tv = hist_trading_value.iloc[-1]
        universe = latest_tv[latest_tv >= self.min_trading_value].index.tolist()

        if not universe:
            return pd.DataFrame()

        univ_prices = hist_prices[universe]

        ret_3m = self._calculate_returns(univ_prices, LOOKBACK_3M)
        ret_6m = self._calculate_returns(univ_prices, LOOKBACK_6M)
        ret_12m = self._calculate_returns(univ_prices, LOOKBACK_12M)

        score = (ret_3m + ret_6m + ret_12m) / 3.0
        abs_pass = ret_12m >= self.risk_free_rate

        result = pd.DataFrame(
            {
                "ret_3m": ret_3m,
                "ret_6m": ret_6m,
                "ret_12m": ret_12m,
                "score": score,
                "abs_pass": abs_pass,
            },
            index=universe,
        )

        # 절대 모멘텀 통과 종목만 순위 부여
        valid = result.loc[result["abs_pass"], "score"].rank(ascending=False)
        result["rank"] = np.nan
        result.loc[valid.index, "rank"] = valid

        result.sort_values("rank", inplace=True)
        return result


# ═══════════════════════════════════════════════════════
#  테스트/검증용 CLI
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    from backend.kiwoom.momentum_data_handler import MomentumDataHandler

    # 1. 데이터 로드
    handler = MomentumDataHandler(finder=None)
    n = handler.load_from_cache()

    if n == 0:
        logger.error("캐시에 일봉 데이터가 없습니다.")
        sys.exit(1)

    handler.build_dataframes()

    # 2. 마지막 영업일 기준 스코어링
    dates = handler.get_available_dates()
    last_date = dates[-1]

    hist_prices, hist_tv, kospi_val, kospi_sma = handler.get_data_up_to(last_date)

    scorer = MomentumScorer(top_n=20, min_trading_value=5e9)

    # 2-1. 전체 스코어링 결과
    score_df = scorer.score_universe(hist_prices, hist_tv)

    print(f"\n{'='*70}")
    print(f"  듀얼 모멘텀 스코어링 결과 ({last_date.date()})")
    print(f"{'='*70}")
    print(f"  유니버스 종목:  {len(score_df)}개")
    print(f"  절대 모멘텀 통과: {score_df['abs_pass'].sum()}개")
    print()

    # 2-2. Top-20 출력
    top20 = score_df.head(20)
    print(f"  {'Rank':>4}  {'Code':<8}  {'3M':>8}  {'6M':>8}  {'12M':>8}  {'Score':>8}")
    print(f"  {'-'*4}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")

    for _, row in top20.iterrows():
        rank = int(row["rank"]) if pd.notna(row["rank"]) else "-"
        code = row.name
        print(
            f"  {rank:>4}  {code:<8}  "
            f"{row['ret_3m']*100:>+7.1f}%  "
            f"{row['ret_6m']*100:>+7.1f}%  "
            f"{row['ret_12m']*100:>+7.1f}%  "
            f"{row['score']*100:>+7.1f}%"
        )

    print()

    # 2-3. select_assets 결과
    selected = scorer.select_assets(hist_prices, hist_tv)
    print(f"  select_assets() → {len(selected)}개 종목: {selected[:5]}...")
    print(f"{'='*70}")

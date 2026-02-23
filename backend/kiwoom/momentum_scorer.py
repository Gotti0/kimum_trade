"""
MomentumScorer: 듀얼 모멘텀 평가 및 유니버스 필터링 모듈.

역할:
  1. 동적 유니버스 필터링 — ADTV ≥ 50억 원 종목만 편입
  2. 상대 모멘텀(Relative Momentum) — 3/6/12개월 수익률 합산 스코어
  3. 절대 모멘텀(Absolute Momentum) — 12개월 수익률 < 0% 종목 제거
  4. 최종 Top-N 종목 선정
  5. [확장] 글로벌 자산군(Asset Class) 모멘텀 스코어링 (Layer 1)
  6. [확장] 프리셋 기반 전략적 배분 + 전술적 모멘텀 조정 (select_global_assets)

설계 문서 참조:
  §3.1 동적 유니버스 필터링
  §3.2 듀얼 모멘텀 스코어링 로직
  §2-4 2계층 모멘텀 스코어링
"""

import logging
from typing import List, Optional, Tuple, Dict

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

    # ═══════════════════════════════════════════════════
    #  Layer 1: 글로벌 자산군(Asset Class) 모멘텀 스코어링
    # ═══════════════════════════════════════════════════

    def score_asset_classes(
        self,
        global_prices: pd.DataFrame,
    ) -> pd.DataFrame:
        """각 글로벌 ETF(자산군)의 3/6/12M 수익률 + 복합 스코어 + 절대모멘텀 통과 여부.

        Args:
            global_prices: 기준일까지의 글로벌 ETF 종가 DataFrame.
                           열 = 티커(SPY, AGG, …), 행 = 날짜.

        Returns:
            DataFrame(index=ticker, columns=[ret_3m, ret_6m, ret_12m, score, abs_pass, rank])
        """
        if global_prices.empty or len(global_prices) < LOOKBACK_3M + 1:
            logger.warning("글로벌 가격 데이터가 부족합니다 (%d행).", len(global_prices))
            return pd.DataFrame()

        ret_3m = self._calculate_returns(global_prices, LOOKBACK_3M)
        ret_6m = self._calculate_returns(global_prices, LOOKBACK_6M)
        ret_12m = self._calculate_returns(global_prices, LOOKBACK_12M)

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
            index=global_prices.columns,
        )

        # 절대 모멘텀 통과 종목만 순위 부여
        valid = result.loc[result["abs_pass"], "score"].rank(ascending=False)
        result["rank"] = np.nan
        result.loc[valid.index, "rank"] = valid

        result.sort_values("score", ascending=False, inplace=True)

        logger.info(
            "자산군 모멘텀 스코어링 완료: %d 티커, 절대모멘텀 통과 %d개",
            len(result),
            int(result["abs_pass"].sum()),
        )

        return result

    def select_global_assets(
        self,
        global_prices: pd.DataFrame,
        kr_prices: pd.DataFrame,
        kr_trading_value: pd.DataFrame,
        preset: Optional[dict] = None,
    ) -> Tuple[Dict[str, float], List[str]]:
        """프리셋의 전략적 배분을 기반으로 모멘텀 스코어링 + 국내 Top-N 선정.

        3-Layer 로직:
          Layer 0: 프리셋의 카테고리별 기본 비중 로드
          Layer 1: 카테고리 내 ETF들의 모멘텀 스코어로 비중 분할
                   + 절대 모멘텀 탈락 ETF 비중 → SHY로 이전
          Layer 2: kr_equity(EWY) 비중 → 국내 개별종목 Top-N으로 세분화

        Args:
            global_prices: 기준일까지의 글로벌 ETF 종가 DataFrame.
            kr_prices: 기준일까지의 국내 종목 종가 DataFrame.
            kr_trading_value: 기준일까지의 국내 거래대금 DataFrame.
            preset: 포트폴리오 프리셋 dict (PORTFOLIO_PRESETS 값).
                    None이면 balanced 프리셋 사용.

        Returns:
            (asset_weights, kr_top_n_codes)
            - asset_weights: {티커: 비중} 예) {"SPY": 0.18, "SHY": 0.15, ...}
            - kr_top_n_codes: 국내 개별종목 Top-N 코드 리스트
        """
        from backend.kiwoom.momentum_asset_classes import (
            CATEGORY_TO_TICKERS,
            CASH_TICKER,
            get_preset,
        )

        # ── Layer 0: 전략적 배분 로드 ──
        if preset is None:
            preset = get_preset("balanced")

        strategic_weights = preset["weights"]
        logger.info(
            "프리셋 '%s' 전략적 배분: %s",
            preset.get("label", "?"),
            {k: f"{v:.0%}" for k, v in strategic_weights.items()},
        )

        # ── Layer 1: 자산군 모멘텀 스코어링 ──
        ac_scores = self.score_asset_classes(global_prices)

        # 티커별 최종 비중 집계
        asset_weights: Dict[str, float] = {}
        shy_overflow = 0.0  # 절대 모멘텀 탈락분을 SHY로 이전할 누적치

        for category, cat_weight in strategic_weights.items():
            if cat_weight <= 0:
                continue

            tickers_in_cat = CATEGORY_TO_TICKERS.get(category, [])
            if not tickers_in_cat:
                continue

            # 카테고리 내 ETF 중 global_prices에 존재하는 것만 필터
            available = [t for t in tickers_in_cat if t in ac_scores.index]
            if not available:
                # 데이터가 없는 카테고리 → 전량 SHY
                shy_overflow += cat_weight
                continue

            # 카테고리 내 ETF별 상대 모멘텀 스코어 추출
            cat_scores = ac_scores.loc[available]

            # 절대 모멘텀 통과 ETF만
            passed = cat_scores[cat_scores["abs_pass"]]
            failed = cat_scores[~cat_scores["abs_pass"]]

            # 탈락 ETF 비중 → SHY
            if len(available) > 0:
                failed_share = len(failed) / len(available)
                shy_overflow += cat_weight * failed_share
                remaining_weight = cat_weight * (1 - failed_share)
            else:
                remaining_weight = 0.0

            if passed.empty or remaining_weight <= 0:
                continue

            # 통과 ETF들 간 상대 모멘텀 비중 분할
            # 스코어가 모두 동일하거나 NaN이면 균등 배분
            scores_valid = passed["score"].fillna(0)
            score_min = scores_valid.min()

            # 스코어를 양수로 이동 (최솟값이 0이 되도록)
            shifted = scores_valid - score_min + 1e-8
            total_shifted = shifted.sum()

            if total_shifted <= 0:
                # 균등 배분
                for ticker in passed.index:
                    w = remaining_weight / len(passed)
                    asset_weights[ticker] = asset_weights.get(ticker, 0) + w
            else:
                for ticker in passed.index:
                    w = remaining_weight * (shifted[ticker] / total_shifted)
                    asset_weights[ticker] = asset_weights.get(ticker, 0) + w

        # SHY에 탈락분 합산
        if shy_overflow > 0:
            asset_weights[CASH_TICKER] = asset_weights.get(CASH_TICKER, 0) + shy_overflow
            logger.info(
                "절대 모멘텀 탈락분 → SHY 이전: %.2f%%",
                shy_overflow * 100,
            )

        # ── Layer 2: kr_equity(EWY) → 국내 개별종목 Top-N ──
        kr_top_n_codes: List[str] = []

        ewy_weight = asset_weights.get("EWY", 0)
        if ewy_weight > 0 and not kr_prices.empty and not kr_trading_value.empty:
            kr_top_n_codes = self.select_assets(kr_prices, kr_trading_value)
            logger.info(
                "Layer 2: EWY %.2f%% → 국내 Top-%d 종목으로 세분화",
                ewy_weight * 100,
                len(kr_top_n_codes),
            )

        # 비중 합계 정규화 (반올림 오차 보정)
        total = sum(asset_weights.values())
        if total > 0 and abs(total - 1.0) > 1e-6:
            asset_weights = {k: v / total for k, v in asset_weights.items()}

        logger.info(
            "최종 자산 배분 (%d 티커): %s",
            len(asset_weights),
            {k: f"{v:.2%}" for k, v in sorted(asset_weights.items(), key=lambda x: -x[1])},
        )

        return asset_weights, kr_top_n_codes


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

    global_only = "--global" in sys.argv

    handler = MomentumDataHandler(finder=None)
    scorer = MomentumScorer(top_n=20, min_trading_value=5e9)

    # ── 국내 종목 스코어링 ──
    if not global_only:
        n = handler.load_from_cache()
        if n == 0:
            logger.warning("캐시에 국내 일봉 데이터가 없습니다.")
        else:
            handler.build_dataframes()
            dates = handler.get_available_dates()
            last_date = dates[-1]
            hist_prices, hist_tv, kospi_val, kospi_sma = handler.get_data_up_to(last_date)

            score_df = scorer.score_universe(hist_prices, hist_tv)

            print(f"\n{'='*70}")
            print(f"  듀얼 모멘텀 스코어링 결과 ({last_date.date()})")
            print(f"{'='*70}")
            print(f"  유니버스 종목:  {len(score_df)}개")
            print(f"  절대 모멘텀 통과: {score_df['abs_pass'].sum()}개")
            print()

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
            selected = scorer.select_assets(hist_prices, hist_tv)
            print(f"  select_assets() → {len(selected)}개 종목: {selected[:5]}...")
            print(f"{'='*70}")

    # ── 글로벌 자산군 스코어링 ──
    print(f"\n{'='*70}")
    print(f"  글로벌 자산군 모멘텀 스코어링 (Layer 1 + Layer 2)")
    print(f"{'='*70}")

    try:
        handler.load_global_data()
        handler.build_global_dataframes()

        g_dates = handler._global_trading_days
        g_last = g_dates[-1]
        g_prices, g_sma = handler.get_global_data_up_to(g_last)

        # Layer 1: 자산군 스코어
        ac_scores = scorer.score_asset_classes(g_prices)
        print(f"\n  자산군 모멘텀 스코어 ({g_last.date()}):")
        print(f"  {'Ticker':<6}  {'3M':>8}  {'6M':>8}  {'12M':>8}  {'Score':>8}  {'AbsPass':>8}  {'Rank':>5}")
        print(f"  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*5}")

        for ticker, row in ac_scores.iterrows():
            rank = int(row["rank"]) if pd.notna(row["rank"]) else "-"
            abs_mark = "✓" if row["abs_pass"] else "✗"
            print(
                f"  {ticker:<6}  "
                f"{row['ret_3m']*100:>+7.1f}%  "
                f"{row['ret_6m']*100:>+7.1f}%  "
                f"{row['ret_12m']*100:>+7.1f}%  "
                f"{row['score']*100:>+7.1f}%  "
                f"{abs_mark:>8}  "
                f"{rank:>5}"
            )

        # Layer 1+2: 프리셋별 자산 배분
        from backend.kiwoom.momentum_asset_classes import PORTFOLIO_PRESETS, get_preset

        for preset_key in ["growth", "balanced", "stable"]:
            preset = get_preset(preset_key)
            print(f"\n  {'─'*60}")
            print(f"  프리셋: {preset['icon']} {preset['label']} (risk {preset['risk_level']})")
            print(f"  {'─'*60}")

            # 국내 데이터가 있으면 사용, 없으면 빈 DataFrame
            kr_p = handler.prices if not handler.prices.empty else pd.DataFrame()
            kr_tv = handler.trading_value if not handler.trading_value.empty else pd.DataFrame()

            weights, kr_codes = scorer.select_global_assets(
                g_prices, kr_p, kr_tv, preset=preset
            )

            print(f"\n  {'Ticker':<6}  {'Weight':>8}")
            print(f"  {'-'*6}  {'-'*8}")
            for t, w in sorted(weights.items(), key=lambda x: -x[1]):
                if w > 0:
                    bar = "█" * int(w * 50)
                    print(f"  {t:<6}  {w:>7.1%}  {bar}")

            total_w = sum(weights.values())
            print(f"\n  합계: {total_w:.4f}")

            if kr_codes:
                print(f"  국내 Top-N: {kr_codes[:5]}{'...' if len(kr_codes) > 5 else ''}")

    except Exception as e:
        logger.error("글로벌 스코어링 실패: %s", e)
        import traceback
        traceback.print_exc()

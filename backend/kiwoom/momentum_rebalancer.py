"""
MomentumRebalancer: 시장 국면 필터 + 가중치 배분 모듈.

역할 (설계 문서 §3.3, §4, §5-③, §2-5):
  1. 시장 국면 판단 — KOSPI vs SMA(200) 비교 → BULL / BEAR 분류
     - BULL (KOSPI ≥ SMA200): 모멘텀 스코어 기반 정상 편입
     - BEAR (KOSPI < SMA200): 모든 주식 비중 → 0%, 전액 현금화
  2. 가중치 배분 — 두 가지 방식 지원
     - equal_weight: 1/N 균등 배분 (기본 베이스라인)
     - inverse_volatility: 20일 변동성의 역수 기반 리스크 패리티
  3. MomentumScorer 결과 + 국면 필터를 결합하여
     PortfolioManager에 전달할 최종 target_weights를 확정
  4. [확장] 글로벌 자산군별 독립 국면 필터 (각 ETF vs 자체 SMA200)
  5. [확장] 프리셋 기반 전략적 배분 + 국면 필터 → 최종 통합 가중치

데이터 무결성:
  - DataHandler가 제공하는 current_kospi, kospi_sma200은
    이미 shift(1)이 적용된 전일 기준 값이므로 미래 참조 편향 없음
  - 변동성 산출 시에도 hist_prices (date까지 슬라이스)만 사용
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
#  상수 정의
# ═══════════════════════════════════════════════════

VOL_LOOKBACK = 20       # 역변동성 가중치 산출 시 참조할 영업일 수
ANNUALIZE_FACTOR = np.sqrt(252)  # 연율화 팩터


class MomentumRebalancer:
    """시장 국면 필터링 및 종목별 자본 배분 가중치를 산출하는 모듈.

    MomentumScorer가 선정한 상위 N개 종목을 받아,
    KOSPI 국면 필터(Bull/Bear)를 적용한 뒤
    동일 비중 또는 변동성 역가중 방식으로 최종 target_weights를 확정합니다.

    설계 문서 참조:
      §3.3  시장 국면 필터에 의한 거시적 리스크 관리
      §4    포트폴리오 자산 배분 메커니즘: 변동성 역가중 방식
      §5-③  Rebalancer

    사용 예시::

        reb = MomentumRebalancer(weight_method="inverse_volatility")
        target_weights, regime = reb.generate_target_weights(
            assets=["005930", "000660"],
            hist_prices=hist_prices,
            current_kospi=2650.0,
            kospi_sma=2700.0,
        )
        # regime == "BEAR" → target_weights 전부 0.0
    """

    # 지원하는 가중치 방식
    VALID_METHODS = {"equal_weight", "inverse_volatility"}

    def __init__(
        self,
        weight_method: str = "inverse_volatility",
        vol_lookback: int = VOL_LOOKBACK,
    ):
        """
        Args:
            weight_method: 가중치 배분 방식.
                ``"equal_weight"`` — 1/N 균등 배분.
                ``"inverse_volatility"`` — 변동성 역수 기반 리스크 패리티.
            vol_lookback: 역변동성 산출 시 사용할 과거 영업일 수 (기본 20).
        """
        if weight_method not in self.VALID_METHODS:
            raise ValueError(
                f"지원하지 않는 weight_method: '{weight_method}'. "
                f"허용값: {self.VALID_METHODS}"
            )
        self.weight_method = weight_method
        self.vol_lookback = vol_lookback

        # 리밸런싱 이력 기록
        self.rebalance_history: List[dict] = []

    # ═══════════════════════════════════════════════════
    #  1. 시장 국면 판별 (Market Regime Filter)
    # ═══════════════════════════════════════════════════

    @staticmethod
    def detect_regime(
        current_kospi: float, kospi_sma200: float
    ) -> Tuple[str, float]:
        """KOSPI 종가 vs SMA(200) 비교로 시장 국면을 판별합니다.

        설계 문서 §3.3:
          KOSPI ≥ SMA(200) → BULL (scale_factor = 1.0)
          KOSPI < SMA(200) → BEAR (scale_factor = 0.0, 전액 현금화)

        DataHandler가 제공하는 값에는 이미 shift(1)이 적용되어 있어
        당일 장중 노이즈가 반영되지 않은 보수적 판단입니다.

        Args:
            current_kospi: 벤치마크(KOSPI) 현재 종가 (전일 기준).
            kospi_sma200: KOSPI 200일 이동평균 (전일 기준).

        Returns:
            (regime, scale_factor) 튜플.
              regime: ``"BULL"`` 또는 ``"BEAR"``
              scale_factor: 투자 비중 승수 (1.0 또는 0.0)
        """
        # NaN 방어: 데이터 없으면 보수적으로 BULL 유지
        if pd.isna(current_kospi) or pd.isna(kospi_sma200):
            logger.warning(
                "KOSPI 또는 SMA(200) 값이 NaN입니다. 기본 BULL로 처리합니다."
            )
            return "BULL", 1.0

        if current_kospi >= kospi_sma200:
            return "BULL", 1.0
        else:
            return "BEAR", 0.0

    # ═══════════════════════════════════════════════════
    #  2. 가중치 배분 (Weight Allocation)
    # ═══════════════════════════════════════════════════

    def _equal_weight(self, assets: List[str]) -> Dict[str, float]:
        """1/N 균등 배분."""
        if not assets:
            return {}
        w = 1.0 / len(assets)
        return {a: w for a in assets}

    def _inverse_volatility_weight(
        self, hist_prices: pd.DataFrame, assets: List[str]
    ) -> Dict[str, float]:
        """최근 N일 변동성에 반비례하는 리스크 패리티 가중치를 산출합니다.

        설계 문서 §4:
          w_i = (1/σ_i) / Σ(1/σ_j)
          σ_i = std(일간수익률, N일) × √252  (연율화)

        변동성이 0이거나 데이터 부족 종목은 제외 후 정규화합니다.
        모든 종목이 부적격이면 동일 비중으로 fallback합니다.

        Args:
            hist_prices: date까지의 종가 DataFrame (columns = 종목코드).
            assets: 타겟 종목 리스트.

        Returns:
            {종목코드: 가중치} (합계 = 1.0)
        """
        if not assets:
            return {}

        # 존재하는 컬럼만 필터
        valid_assets = [a for a in assets if a in hist_prices.columns]
        if not valid_assets:
            return self._equal_weight(assets)

        # 최근 vol_lookback 일의 일간 수익률
        prices_slice = hist_prices[valid_assets].tail(self.vol_lookback + 1)
        daily_returns = prices_slice.pct_change().dropna(how="all")

        if daily_returns.empty or len(daily_returns) < 2:
            logger.warning(
                "변동성 산출을 위한 수익률 데이터 부족. 동일 비중 fallback."
            )
            return self._equal_weight(assets)

        # 연율화 변동성 계산
        vols = daily_returns.std() * ANNUALIZE_FACTOR

        # 변동성 0이거나 NaN인 종목 제거
        vols = vols.replace(0, np.nan).dropna()

        if vols.empty:
            logger.warning(
                "유효한 변동성 값이 없습니다. 동일 비중 fallback."
            )
            return self._equal_weight(assets)

        # 역수 계산 → 정규화
        inv_vols = 1.0 / vols
        weights_series = inv_vols / inv_vols.sum()

        weights = weights_series.to_dict()

        # 원래 assets에 있었으나 변동성 부적격으로 제거된 종목은 0.0
        for a in assets:
            if a not in weights:
                weights[a] = 0.0

        # 최종 정규화 (제거된 종목의 비중을 유효 종목에 재분배)
        total = sum(weights.values())
        if total > 0 and abs(total - 1.0) > 1e-9:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def compute_weights(
        self, assets: List[str], hist_prices: pd.DataFrame
    ) -> Dict[str, float]:
        """설정된 방식에 따라 가중치를 산출합니다.

        Args:
            assets: 투자 대상 종목 리스트.
            hist_prices: date까지의 종가 DataFrame.

        Returns:
            {종목코드: 가중치} (합계 ≈ 1.0)
        """
        if self.weight_method == "inverse_volatility":
            return self._inverse_volatility_weight(hist_prices, assets)
        else:
            return self._equal_weight(assets)

    # ═══════════════════════════════════════════════════
    #  3. 메인 인터페이스: generate_target_weights
    # ═══════════════════════════════════════════════════

    def generate_target_weights(
        self,
        assets: List[str],
        hist_prices: pd.DataFrame,
        current_kospi: float,
        kospi_sma: float,
    ) -> Tuple[Dict[str, float], str]:
        """국면 필터를 적용한 후 최종 포트폴리오 가중치를 확정합니다.

        워크플로우:
          1. KOSPI vs SMA(200) → 국면 판별
          2. BEAR → 모든 비중 0.0 (PortfolioManager가 전량 매도)
          3. BULL → 가중치 배분 (equal_weight / inverse_volatility)

        Args:
            assets: MomentumScorer가 선정한 상위 N개 종목 리스트.
            hist_prices: date까지의 종가 DataFrame.
            current_kospi: 벤치마크 현재 종가 (전일 기준, shift(1) 적용).
            kospi_sma: KOSPI 200일 이동평균 (전일 기준, shift(1) 적용).

        Returns:
            (target_weights, regime) 튜플.
              target_weights: {종목코드: 비중} — BEAR 시 모두 0.0
              regime: ``"BULL"`` 또는 ``"BEAR"``
        """
        # 1. 국면 판별
        regime, scale_factor = self.detect_regime(current_kospi, kospi_sma)

        # 2. BEAR → 전액 현금화
        if scale_factor == 0.0:
            logger.info(
                "[국면 필터] BEAR 감지 -- KOSPI %.2f < SMA200 %.2f -> 전액 현금화",
                current_kospi, kospi_sma,
            )
            target_weights = {a: 0.0 for a in assets}
            self._record_rebalance(regime, assets, target_weights, current_kospi, kospi_sma)
            return target_weights, regime

        # 3. BULL → 가중치 배분
        if not assets:
            logger.info("[국면 필터] BULL이나 편입 종목 없음 → 현금 유지")
            self._record_rebalance(regime, [], {}, current_kospi, kospi_sma)
            return {}, regime

        target_weights = self.compute_weights(assets, hist_prices)

        logger.info(
            "[국면 필터] BULL -- KOSPI %.2f >= SMA200 %.2f -> %d종목 %s 배분",
            current_kospi, kospi_sma, len(assets), self.weight_method,
        )

        self._record_rebalance(regime, assets, target_weights, current_kospi, kospi_sma)
        return target_weights, regime

    # ═══════════════════════════════════════════════════
    #  4. 이력 기록
    # ═══════════════════════════════════════════════════

    def _record_rebalance(
        self,
        regime: str,
        assets: List[str],
        weights: Dict[str, float],
        kospi: float,
        kospi_sma: float,
    ) -> None:
        """리밸런싱 이벤트를 내부 이력에 기록합니다."""
        self.rebalance_history.append({
            "regime": regime,
            "n_assets": len(assets),
            "kospi": kospi,
            "kospi_sma200": kospi_sma,
            "weight_method": self.weight_method,
            "weights": dict(weights),
        })

    def get_regime_history(self) -> pd.DataFrame:
        """전체 리밸런싱 국면 이력을 DataFrame으로 반환합니다."""
        if not self.rebalance_history:
            return pd.DataFrame()
        rows = [
            {k: v for k, v in rec.items() if k != "weights"}
            for rec in self.rebalance_history
        ]
        return pd.DataFrame(rows)

    def summary(self) -> str:
        """리밸런서 설정 및 누적 통계 문자열을 반환합니다."""
        n = len(self.rebalance_history)
        n_bull = sum(1 for r in self.rebalance_history if r["regime"] == "BULL")
        n_bear = n - n_bull

        lines = [
            "=" * 60,
            "  MomentumRebalancer 요약",
            "=" * 60,
            f"  가중치 방식:    {self.weight_method}",
            f"  변동성 참조일:  {self.vol_lookback}일",
            f"  총 리밸런싱:    {n}회",
            f"    BULL:         {n_bull}회",
            f"    BEAR:         {n_bear}회",
            f"  현금화 비율:    {n_bear / n * 100:.1f}%" if n > 0 else "  현금화 비율:    N/A",
            "=" * 60,
        ]
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════
    #  5. 글로벌 자산군별 독립 국면 필터
    # ═══════════════════════════════════════════════════

    @staticmethod
    def detect_global_regimes(
        global_prices: pd.DataFrame,
        global_sma200: pd.DataFrame,
    ) -> Dict[str, str]:
        """각 글로벌 ETF의 가격 vs 자체 SMA200 비교 → 개별 국면 판별.

        설계 문서 §2-5:
          SPY ≥ SMA200(SPY) → BULL  → 정상 비중
          EFA < SMA200(EFA) → BEAR  → 비중 → SHY로 이전

        Args:
            global_prices: 기준일까지의 글로벌 ETF 종가 DataFrame.
            global_sma200: 기준일까지의 SMA200 DataFrame (shift(1) 적용 완료).

        Returns:
            {ticker: "BULL" or "BEAR"}
        """
        regime_map: Dict[str, str] = {}

        if global_prices.empty or global_sma200.empty:
            return regime_map

        # 가장 최근 행 사용
        latest_prices = global_prices.iloc[-1]
        latest_sma = global_sma200.iloc[-1]

        for ticker in global_prices.columns:
            price = latest_prices.get(ticker)
            sma = latest_sma.get(ticker)

            # NaN 방어: 데이터 부족 시 보수적 BULL
            if pd.isna(price) or pd.isna(sma):
                regime_map[ticker] = "BULL"
                continue

            regime_map[ticker] = "BULL" if price >= sma else "BEAR"

        n_bull = sum(1 for v in regime_map.values() if v == "BULL")
        n_bear = len(regime_map) - n_bull
        logger.info(
            "글로벌 국면 판별: %d BULL / %d BEAR (총 %d 티커)",
            n_bull, n_bear, len(regime_map),
        )

        return regime_map

    def generate_global_target_weights(
        self,
        asset_class_weights: Dict[str, float],
        global_prices: pd.DataFrame,
        global_sma200: pd.DataFrame,
        kr_top_n_codes: List[str],
        kr_equity_ticker: str = "EWY",
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """Scorer의 자산 배분에 국면 필터를 적용하여 최종 통합 비중을 산출합니다.

        워크플로우:
          1. 자산군별 독립 국면 판별 (각 ETF vs 자체 SMA200)
          2. BEAR인 ETF → 비중을 SHY(현금등가)로 이전
          3. kr_equity(EWY) 비중 → 국내 개별종목으로 균등 분할
          4. 최종 target_weights 확정 + 정규화

        Args:
            asset_class_weights: Scorer의 모멘텀 기반 티커별 비중.
                예) {"SPY": 0.18, "EFA": 0.10, "SHY": 0.15, ...}
            global_prices: 기준일까지의 글로벌 ETF 종가 DataFrame.
            global_sma200: 기준일까지의 SMA200 DataFrame (shift(1) 적용).
            kr_top_n_codes: 국내 개별종목 Top-N 코드 리스트.
            kr_equity_ticker: 한국 자산군 벤치마크 티커 (기본 "EWY").

        Returns:
            (target_weights, regime_by_ticker)
            - target_weights: {티커/종목코드: 최종비중} (글로벌 + 국내 통합)
            - regime_by_ticker: {티커: "BULL"/"BEAR"}
        """
        from backend.kiwoom.momentum_asset_classes import CASH_TICKER

        # 1. 자산군별 독립 국면 판별
        regime_by_ticker = self.detect_global_regimes(global_prices, global_sma200)

        # 2. BEAR ETF 비중 → SHY 이전
        final_weights: Dict[str, float] = {}
        shy_overflow = 0.0

        for ticker, weight in asset_class_weights.items():
            if weight <= 0:
                continue

            regime = regime_by_ticker.get(ticker, "BULL")

            # SHY 자체는 항상 BULL 취급 (대피처)
            if ticker == CASH_TICKER:
                final_weights[ticker] = final_weights.get(ticker, 0) + weight
                continue

            if regime == "BEAR":
                shy_overflow += weight
                logger.info(
                    "  [국면 필터] %s BEAR → %.2f%% SHY로 이전",
                    ticker, weight * 100,
                )
            else:
                final_weights[ticker] = final_weights.get(ticker, 0) + weight

        # SHY에 탈락분 합산
        if shy_overflow > 0:
            final_weights[CASH_TICKER] = final_weights.get(CASH_TICKER, 0) + shy_overflow
            logger.info(
                "  [국면 필터] BEAR 이전 합계 → SHY +%.2f%%",
                shy_overflow * 100,
            )

        # 3. kr_equity(EWY) → 국내 개별종목 균등 분할
        ewy_weight = final_weights.pop(kr_equity_ticker, 0)
        if ewy_weight > 0 and kr_top_n_codes:
            per_stock = ewy_weight / len(kr_top_n_codes)
            for code in kr_top_n_codes:
                final_weights[code] = final_weights.get(code, 0) + per_stock
            logger.info(
                "  [EWY 분할] %.2f%% → %d 국내종목 (각 %.3f%%)",
                ewy_weight * 100, len(kr_top_n_codes), per_stock * 100,
            )
        elif ewy_weight > 0:
            # 국내 종목이 없으면 EWY ETF 자체로 유지
            final_weights[kr_equity_ticker] = ewy_weight

        # 4. 비중 정규화 (반올림 오차 보정)
        total = sum(final_weights.values())
        if total > 0 and abs(total - 1.0) > 1e-6:
            final_weights = {k: v / total for k, v in final_weights.items()}

        # 이력 기록
        n_bull = sum(1 for v in regime_by_ticker.values() if v == "BULL")
        n_bear = len(regime_by_ticker) - n_bull
        self.rebalance_history.append({
            "regime": f"GLOBAL({n_bull}B/{n_bear}R)",
            "n_assets": len(final_weights),
            "kospi": 0.0,
            "kospi_sma200": 0.0,
            "weight_method": "global_regime_filter",
            "weights": dict(final_weights),
            "regime_detail": dict(regime_by_ticker),
        })

        logger.info(
            "최종 global target_weights: %d 항목 (글로벌 + 국내)",
            len(final_weights),
        )

        return final_weights, regime_by_ticker


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
    from backend.kiwoom.momentum_scorer import MomentumScorer

    global_only = "--global" in sys.argv

    handler = MomentumDataHandler(finder=None)
    scorer = MomentumScorer(top_n=20, min_trading_value=5e9)

    # ── 국내 종목 리밸런서 테스트 ──
    if not global_only:
        n = handler.load_from_cache()
        if n == 0:
            logger.warning("캐시 데이터 없음.")
        else:
            handler.build_dataframes()
            reb_iv = MomentumRebalancer(weight_method="inverse_volatility")

            eom_dates = handler.get_month_end_dates()
            test_dates = eom_dates[-3:]

            print(f"\n{'='*80}")
            print(f"  MomentumRebalancer 국내 검증 — 최근 {len(test_dates)}개 월말")
            print(f"{'='*80}")

            for date in test_dates:
                hist_prices, hist_tv, kospi, kospi_sma = handler.get_data_up_to(date)
                selected = scorer.select_assets(hist_prices, hist_tv)
                weights_iv, regime_iv = reb_iv.generate_target_weights(
                    selected, hist_prices, kospi, kospi_sma
                )

                print(f"\n  {date.date()} [{regime_iv}] "
                      f"KOSPI={kospi:,.0f} SMA200={kospi_sma:,.0f} "
                      f"종목={len(selected)}")

                if regime_iv == "BULL":
                    top3 = sorted(weights_iv.items(), key=lambda x: -x[1])[:3]
                    for t, w in top3:
                        print(f"    {t}: {w:.4f}")

            print()
            print(reb_iv.summary())

    # ── 글로벌 리밸런서 테스트 ──
    print(f"\n{'='*80}")
    print(f"  글로벌 국면 필터 + 자산 배분 (Rebalancer 확장)")
    print(f"{'='*80}")

    try:
        handler.load_global_data()
        handler.build_global_dataframes()

        from backend.kiwoom.momentum_asset_classes import get_preset

        g_last = handler._global_trading_days[-1]
        g_prices, g_sma = handler.get_global_data_up_to(g_last)

        reb_global = MomentumRebalancer(weight_method="inverse_volatility")

        for preset_key in ["growth", "balanced", "stable"]:
            preset = get_preset(preset_key)
            print(f"\n  {'─'*65}")
            print(f"  프리셋: {preset['icon']} {preset['label']} (risk {preset['risk_level']})")
            print(f"  {'─'*65}")

            # Scorer: 모멘텀 기반 티커별 비중
            kr_p = handler.prices if not handler.prices.empty else pd.DataFrame()
            kr_tv = handler.trading_value if not handler.trading_value.empty else pd.DataFrame()
            ac_weights, kr_codes = scorer.select_global_assets(
                g_prices, kr_p, kr_tv, preset=preset
            )

            print(f"\n  [Scorer 결과] 모멘텀 배분:")
            for t, w in sorted(ac_weights.items(), key=lambda x: -x[1])[:5]:
                print(f"    {t:5s}: {w:6.2%}")

            # Rebalancer: 국면 필터 적용
            final_weights, regime_map = reb_global.generate_global_target_weights(
                ac_weights, g_prices, g_sma, kr_codes,
            )

            # 국면 표시
            print(f"\n  [국면 판별]:")
            for ticker in sorted(regime_map.keys()):
                regime = regime_map[ticker]
                mark = "✓" if regime == "BULL" else "✗"
                print(f"    {ticker:5s}: {regime}  {mark}")

            # 최종 비중
            print(f"\n  [최종 비중] (국면 필터 적용 후):")
            n_global = 0
            n_kr = 0
            for t, w in sorted(final_weights.items(), key=lambda x: -x[1]):
                if w > 0.001:
                    bar = "█" * int(w * 50)
                    is_kr = not t.isupper()
                    label = "[KR]" if is_kr else ""
                    print(f"    {t:8s}: {w:6.2%}  {bar} {label}")
                    if is_kr:
                        n_kr += 1
                    else:
                        n_global += 1

            total_w = sum(final_weights.values())
            print(f"\n    합계: {total_w:.4f}  (글로벌 {n_global} + 국내 {n_kr})")

        print()
        print(reb_global.summary())

    except Exception as e:
        logger.error("글로벌 리밸런서 실패: %s", e)
        import traceback
        traceback.print_exc()

    print(f"{'='*80}")

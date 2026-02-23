"""
MomentumRebalancer: 시장 국면 필터 + 가중치 배분 모듈.

역할 (설계 문서 §3.3, §4, §5-③):
  1. 시장 국면 판단 — KOSPI vs SMA(200) 비교 → BULL / BEAR 분류
     - BULL (KOSPI ≥ SMA200): 모멘텀 스코어 기반 정상 편입
     - BEAR (KOSPI < SMA200): 모든 주식 비중 → 0%, 전액 현금화
  2. 가중치 배분 — 두 가지 방식 지원
     - equal_weight: 1/N 균등 배분 (기본 베이스라인)
     - inverse_volatility: 20일 변동성의 역수 기반 리스크 패리티
  3. MomentumScorer 결과 + 국면 필터를 결합하여
     PortfolioManager에 전달할 최종 target_weights를 확정

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

    # ── 1. 데이터 로드 ──
    handler = MomentumDataHandler(finder=None)
    n = handler.load_from_cache()
    if n == 0:
        logger.error("캐시 데이터 없음.")
        sys.exit(1)
    handler.build_dataframes()

    # ── 2. 두 가중치 방식 비교 ──
    scorer = MomentumScorer(top_n=20, min_trading_value=5e9)
    reb_iv = MomentumRebalancer(weight_method="inverse_volatility")
    reb_ew = MomentumRebalancer(weight_method="equal_weight")

    eom_dates = handler.get_month_end_dates()
    # 최근 5개 월말에 대해 테스트
    test_dates = eom_dates[-5:]

    print(f"\n{'='*80}")
    print(f"  MomentumRebalancer 검증 — 최근 {len(test_dates)}개 월말")
    print(f"{'='*80}")

    for date in test_dates:
        hist_prices, hist_tv, kospi, kospi_sma = handler.get_data_up_to(date)

        # 종목 선정
        selected = scorer.select_assets(hist_prices, hist_tv)

        # 역변동성 가중치
        weights_iv, regime_iv = reb_iv.generate_target_weights(
            selected, hist_prices, kospi, kospi_sma
        )
        # 동일 비중
        weights_ew, regime_ew = reb_ew.generate_target_weights(
            selected, hist_prices, kospi, kospi_sma
        )

        print(f"\n  ┌─ {date.date()} ─────────────────────────────────")
        print(f"  │ KOSPI: {kospi:,.2f}  SMA200: {kospi_sma:,.2f}  "
              f"국면: {regime_iv}")
        print(f"  │ 선정 종목: {len(selected)}개")

        if regime_iv == "BEAR":
            print(f"  │ → BEAR: 전액 현금화 (모든 비중 = 0.0)")
        else:
            # 역변동성 vs 동일 비중 상위 5종목 비교
            sorted_iv = sorted(weights_iv.items(), key=lambda x: x[1], reverse=True)
            print(f"  │")
            print(f"  │ 가중치 배분 비교 (상위 5종목):")
            print(f"  │ {'종목':>8s}  {'역변동성':>10s}  {'동일비중':>10s}  {'차이':>10s}")
            print(f"  │ {'─'*8}  {'─'*10}  {'─'*10}  {'─'*10}")
            for ticker, w_iv in sorted_iv[:5]:
                w_ew = weights_ew.get(ticker, 0.0)
                diff = w_iv - w_ew
                print(f"  │ {ticker:>8s}  {w_iv:>9.4f}  {w_ew:>9.4f}  {diff:>+9.4f}")

            # 가중치 분산도 (Herfindahl Index)
            hhi_iv = sum(w ** 2 for w in weights_iv.values())
            hhi_ew = sum(w ** 2 for w in weights_ew.values())
            print(f"  │")
            print(f"  │ HHI 집중도: 역변동성 {hhi_iv:.4f} vs 동일비중 {hhi_ew:.4f}")

            # 가중치 범위
            ws = list(weights_iv.values())
            if ws:
                print(f"  │ 역변동성 가중치 범위: "
                      f"{min(ws):.4f} ~ {max(ws):.4f} "
                      f"(max/min = {max(ws)/min(ws):.2f}x)" if min(ws) > 0
                      else f"  │ 역변동성 가중치 범위: {min(ws):.4f} ~ {max(ws):.4f}")

        print(f"  └{'─'*50}")

    # ── 3. 요약 출력 ──
    print()
    print(reb_iv.summary())
    print(reb_ew.summary())

    # 국면 이력 통계
    regime_df = reb_iv.get_regime_history()
    if not regime_df.empty:
        bull_count = (regime_df["regime"] == "BULL").sum()
        bear_count = (regime_df["regime"] == "BEAR").sum()
        print(f"\n  전체 국면 통계: BULL {bull_count}회 / BEAR {bear_count}회")

    print(f"{'='*80}")

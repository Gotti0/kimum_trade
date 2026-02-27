"""
MomentumPerformanceAnalyzer: 통계적 성과 검증 및 핵심 지표 산출 모듈.

역할 (설계 문서 §5-⑤, §8):
  1. PortfolioManager가 기록한 일간 자산 가치(Equity Curve)를 넘겨받아
     핵심 수익성 및 리스크 지표(KPI)를 연산
  2. 산출 지표:
     - CAGR (연평균 복리 수익률)
     - MDD  (최대 낙폭)
     - Sharpe Ratio (샤프 지수, Rf=0%)
     - Sortino Ratio (소르티노 지수, 하방 리스크 기반)
     - Calmar Ratio (CAGR / |MDD|)
     - Win Rate (일간/월간 승률)
     - 연율화 변동성
     - Best/Worst 일간·월간 수익률
  3. 거래 비용 분석 (PortfolioManager cost_summary 통합)
  4. 국면별 성과 분석 (Rebalancer regime_history 통합)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
#  상수 정의
# ═══════════════════════════════════════════════════

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.0  # 무위험 이자율 (보수적 0% 가정)


class MomentumPerformanceAnalyzer:
    """백테스트 종료 후 성과 지표를 산출하는 사후 분석 모듈.

    PortfolioManager의 equity_curve를 받아 CAGR, MDD, Sharpe 등
    기관급 성과 지표를 계산합니다.

    사용 예시::

        analyzer = MomentumPerformanceAnalyzer(
            equity_curve=portfolio_manager.equity_curve,
            initial_capital=1e8,
        )
        metrics = analyzer.calculate_metrics()
        print(analyzer.report())
    """

    def __init__(
        self,
        equity_curve: Dict[pd.Timestamp, float],
        initial_capital: float = 1e8,
        cost_summary: Optional[dict] = None,
        rebalance_history: Optional[List[dict]] = None,
    ):
        """
        Args:
            equity_curve: {날짜: 포트폴리오 총 가치} 딕셔너리.
                          PortfolioManager.equity_curve 그대로 전달 가능.
            initial_capital: 초기 자본금 (CAGR·수익률 기준).
            cost_summary: PortfolioManager.get_cost_summary() 결과 (선택).
            rebalance_history: MomentumRebalancer.rebalance_history (선택).
        """
        self.initial_capital = initial_capital
        self.cost_summary = cost_summary or {}
        self.rebalance_history = rebalance_history or []

        # equity_curve → pandas Series (날짜 정렬)
        self.equity = pd.Series(equity_curve, dtype=float).sort_index()

        if self.equity.empty:
            logger.warning("equity_curve가 비어 있습니다.")

        # 캐시: 계산 결과 저장
        self._metrics: Optional[Dict[str, float]] = None

    # ═══════════════════════════════════════════════════
    #  1. 일간 수익률 시리즈
    # ═══════════════════════════════════════════════════

    @property
    def daily_returns(self) -> pd.Series:
        """일간 수익률 시리즈를 반환합니다 (첫 날 NaN 제거)."""
        return self.equity.pct_change().dropna()

    @property
    def monthly_returns(self) -> pd.Series:
        """월간 수익률 시리즈를 반환합니다."""
        monthly_equity = self.equity.resample("ME").last().dropna()
        return monthly_equity.pct_change().dropna()

    # ═══════════════════════════════════════════════════
    #  2. 핵심 성과 지표 산출
    # ═══════════════════════════════════════════════════

    def calculate_metrics(self) -> Dict[str, float]:
        """모든 핵심 성과 지표를 산출하여 딕셔너리로 반환합니다.

        설계 문서 §8의 4가지 핵심 지표 + 확장 지표를 포함합니다.

        Returns:
            {지표명: 값} 딕셔너리.
        """
        if self._metrics is not None:
            return self._metrics

        metrics: Dict[str, float] = {}

        if len(self.equity) < 2:
            logger.warning("equity 데이터 포인트 부족 (최소 2개 필요).")
            self._metrics = metrics
            return metrics

        # ── 기본 수익률 ──
        final_equity = self.equity.iloc[-1]
        total_return = (final_equity / self.initial_capital) - 1.0
        metrics["total_return_pct"] = total_return * 100

        # ── CAGR (연평균 복리 수익률) ──
        # §8: (Final / Initial) ** (1/Years) - 1
        n_days = (self.equity.index[-1] - self.equity.index[0]).days
        years = n_days / 365.25
        if years > 0 and final_equity > 0:
            cagr = (final_equity / self.initial_capital) ** (1.0 / years) - 1.0
        else:
            cagr = 0.0
        metrics["cagr_pct"] = cagr * 100

        # ── MDD (최대 낙폭) ──
        # §8: Drawdown = (Equity - CumMax) / CumMax → min
        cum_max = self.equity.cummax()
        drawdown = (self.equity - cum_max) / cum_max
        mdd = drawdown.min()
        metrics["mdd_pct"] = mdd * 100

        # MDD 기간 정보
        mdd_end_idx = drawdown.idxmin()
        mdd_start_idx = self.equity.loc[:mdd_end_idx].idxmax()
        # MDD 회복 시점
        recovery_mask = self.equity.loc[mdd_end_idx:] >= cum_max.loc[mdd_end_idx]
        if recovery_mask.any():
            mdd_recovery_idx = recovery_mask.idxmax()
            metrics["mdd_recovery_days"] = (mdd_recovery_idx - mdd_end_idx).days
        else:
            metrics["mdd_recovery_days"] = float("nan")  # 미회복

        metrics["mdd_duration_days"] = (mdd_end_idx - mdd_start_idx).days

        # ── 연율화 변동성 ──
        dr = self.daily_returns
        annual_vol = dr.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        metrics["annual_volatility_pct"] = annual_vol * 100

        # ── Sharpe Ratio ──
        # §8: (Mean(Return) / Std(Return)) × √252, Rf=0%
        mean_daily = dr.mean()
        std_daily = dr.std()
        if std_daily > 0:
            sharpe = (mean_daily - RISK_FREE_RATE / TRADING_DAYS_PER_YEAR) / std_daily
            sharpe *= np.sqrt(TRADING_DAYS_PER_YEAR)
        else:
            sharpe = 0.0
        metrics["sharpe_ratio"] = sharpe

        # ── Sortino Ratio (하방 리스크 기반) ──
        downside = dr[dr < 0]
        if len(downside) > 0:
            downside_std = downside.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
            if downside_std > 0:
                sortino = (mean_daily * TRADING_DAYS_PER_YEAR) / downside_std
            else:
                sortino = 0.0
        else:
            sortino = float("inf")  # 하락일 없음
        metrics["sortino_ratio"] = sortino

        # ── Calmar Ratio (CAGR / |MDD|) ──
        if abs(mdd) > 0:
            calmar = cagr / abs(mdd)
        else:
            calmar = float("inf") if cagr > 0 else 0.0
        metrics["calmar_ratio"] = calmar

        # ── Win Rate (일간 승률) ──
        # §8: len(Positive Days) / len(Total Days)
        if len(dr) > 0:
            win_days = (dr > 0).sum()
            metrics["daily_win_rate_pct"] = (win_days / len(dr)) * 100
        else:
            metrics["daily_win_rate_pct"] = 0.0

        # ── Win Rate (월간 승률) ──
        mr = self.monthly_returns
        if len(mr) > 0:
            win_months = (mr > 0).sum()
            metrics["monthly_win_rate_pct"] = (win_months / len(mr)) * 100
        else:
            metrics["monthly_win_rate_pct"] = 0.0

        # ── Best / Worst 일간 수익률 ──
        if len(dr) > 0:
            metrics["best_day_pct"] = dr.max() * 100
            metrics["worst_day_pct"] = dr.min() * 100
        else:
            metrics["best_day_pct"] = 0.0
            metrics["worst_day_pct"] = 0.0

        # ── Best / Worst 월간 수익률 ──
        if len(mr) > 0:
            metrics["best_month_pct"] = mr.max() * 100
            metrics["worst_month_pct"] = mr.min() * 100
        else:
            metrics["best_month_pct"] = 0.0
            metrics["worst_month_pct"] = 0.0

        # ── 기간 정보 ──
        metrics["total_trading_days"] = len(self.equity)
        metrics["total_years"] = years
        metrics["initial_capital"] = self.initial_capital
        metrics["final_equity"] = final_equity

        # ── Profit Factor (이익일 수익합 / 손실일 절대합) ──
        if len(dr) > 0:
            gross_profit = dr[dr > 0].sum()
            gross_loss = abs(dr[dr < 0].sum())
            if gross_loss > 0:
                metrics["profit_factor"] = gross_profit / gross_loss
            else:
                metrics["profit_factor"] = float("inf") if gross_profit > 0 else 0.0
        else:
            metrics["profit_factor"] = 0.0

        self._metrics = metrics
        return metrics

    # ═══════════════════════════════════════════════════
    #  3. Drawdown 시리즈
    # ═══════════════════════════════════════════════════

    def get_drawdown_series(self) -> pd.Series:
        """일별 Drawdown 비율 시리즈를 반환합니다."""
        cum_max = self.equity.cummax()
        return (self.equity - cum_max) / cum_max

    # ═══════════════════════════════════════════════════
    #  4. 연도별 수익률 분해
    # ═══════════════════════════════════════════════════

    def yearly_returns(self) -> pd.Series:
        """연도별 수익률을 산출합니다."""
        yearly_equity = self.equity.resample("YE").last().dropna()
        # 첫 해는 initial_capital 대비
        returns = yearly_equity.pct_change()
        if len(yearly_equity) > 0:
            returns.iloc[0] = (yearly_equity.iloc[0] / self.initial_capital) - 1.0
        return returns

    # ═══════════════════════════════════════════════════
    #  5. 국면별 성과 분석
    # ═══════════════════════════════════════════════════

    def regime_analysis(self) -> Optional[pd.DataFrame]:
        """리밸런싱 이력 기반 국면별 통계를 반환합니다.

        MomentumRebalancer.rebalance_history가 제공된 경우에만 동작합니다.
        """
        if not self.rebalance_history:
            return None

        bull_count = sum(1 for r in self.rebalance_history if r["regime"] == "BULL")
        bear_count = sum(1 for r in self.rebalance_history if r["regime"] == "BEAR")
        total = len(self.rebalance_history)

        rows = [
            {"국면": "BULL", "횟수": bull_count, "비율(%)": bull_count / total * 100 if total > 0 else 0},
            {"국면": "BEAR", "횟수": bear_count, "비율(%)": bear_count / total * 100 if total > 0 else 0},
            {"국면": "합계", "횟수": total, "비율(%)": 100.0},
        ]
        return pd.DataFrame(rows)

    # ═══════════════════════════════════════════════════
    #  6. 종합 리포트
    # ═══════════════════════════════════════════════════

    def report(self) -> str:
        """모든 성과 지표를 포맳팅한 종합 리포트 문자열을 반환합니다."""
        m = self.calculate_metrics()

        if not m:
            return "데이터 부족: 성과 분석 불가"

        lines = [
            "",
            "=" * 68,
            "  MomentumPerformanceAnalyzer — 백테스트 성과 리포트",
            "=" * 68,
            "",
            "  ┌─ 기간 정보 ─────────────────────────────────────",
            f"  │ 시작일:          {self.equity.index[0].date()}",
            f"  │ 종료일:          {self.equity.index[-1].date()}",
            f"  │ 총 영업일:       {m.get('total_trading_days', 0):,.0f}일",
            f"  │ 총 기간:         {m.get('total_years', 0):.2f}년",
            f"  │ 초기 자본금:     {m.get('initial_capital', 0):>18,.0f}원",
            f"  │ 최종 자산:       {m.get('final_equity', 0):>18,.0f}원",
            "  └──────────────────────────────────────────────────",
            "",
            "  ┌─ 수익성 지표 ───────────────────────────────────",
            f"  │ 총 수익률:       {m.get('total_return_pct', 0):>+12.2f}%",
            f"  │ CAGR:            {m.get('cagr_pct', 0):>+12.2f}%",
            f"  │ Best Day:        {m.get('best_day_pct', 0):>+12.2f}%",
            f"  │ Worst Day:       {m.get('worst_day_pct', 0):>+12.2f}%",
            f"  │ Best Month:      {m.get('best_month_pct', 0):>+12.2f}%",
            f"  │ Worst Month:     {m.get('worst_month_pct', 0):>+12.2f}%",
            "  └──────────────────────────────────────────────────",
            "",
            "  ┌─ 리스크 지표 ───────────────────────────────────",
            f"  │ MDD:             {m.get('mdd_pct', 0):>+12.2f}%",
            f"  │ MDD 지속기간:    {m.get('mdd_duration_days', 0):>9.0f}일",
        ]

        recovery = m.get("mdd_recovery_days", float("nan"))
        if pd.notna(recovery):
            lines.append(f"  │ MDD 회복기간:    {recovery:>9.0f}일")
        else:
            lines.append(f"  │ MDD 회복기간:         미회복")

        lines.extend([
            f"  │ 연율화 변동성:   {m.get('annual_volatility_pct', 0):>12.2f}%",
            "  └──────────────────────────────────────────────────",
            "",
            "  ┌─ 위험조정 수익률 ───────────────────────────────",
            f"  │ Sharpe Ratio:    {m.get('sharpe_ratio', 0):>12.4f}",
            f"  │ Sortino Ratio:   {m.get('sortino_ratio', 0):>12.4f}",
            f"  │ Calmar Ratio:    {m.get('calmar_ratio', 0):>12.4f}",
            f"  │ Profit Factor:   {m.get('profit_factor', 0):>12.4f}",
            "  └──────────────────────────────────────────────────",
            "",
            "  ┌─ 승률 ─────────────────────────────────────────",
            f"  │ 일간 승률:       {m.get('daily_win_rate_pct', 0):>12.2f}%",
            f"  │ 월간 승률:       {m.get('monthly_win_rate_pct', 0):>12.2f}%",
            "  └──────────────────────────────────────────────────",
        ])

        # 거래 비용 요약
        if self.cost_summary:
            cs = self.cost_summary
            lines.extend([
                "",
                "  ┌─ 거래 비용 ─────────────────────────────────────",
                f"  │ 총 거래 횟수:   {cs.get('total_trades', 0):>12d}",
                f"  │ 누적 수수료:    {cs.get('total_commission', 0):>15,.0f}원",
                f"  │ 누적 슬리피지:  {cs.get('total_slippage_cost', 0):>15,.0f}원",
                f"  │ 총 마찰비용:    {cs.get('total_friction', 0):>15,.0f}원",
                f"  │ 누적 회전율:    {cs.get('total_turnover', 0):>15,.0f}원",
                "  └──────────────────────────────────────────────────",
            ])

        # 국면 분석
        regime_df = self.regime_analysis()
        if regime_df is not None:
            lines.extend([
                "",
                "  ┌─ 국면 분석 ─────────────────────────────────────",
            ])
            for _, row in regime_df.iterrows():
                lines.append(
                    f"  │ {row['국면']:>6s}: {row['횟수']:>4d}회 ({row['비율(%)']:>5.1f}%)"
                )
            lines.append("  └──────────────────────────────────────────────────")

        # 연도별 수익률
        yr = self.yearly_returns()
        if len(yr) > 0:
            lines.extend([
                "",
                "  ┌─ 연도별 수익률 ─────────────────────────────────",
            ])
            for date, ret in yr.items():
                lines.append(f"  │ {date.year}:  {ret * 100:>+10.2f}%")
            lines.append("  └──────────────────────────────────────────────────")

        lines.extend([
            "",
            "=" * 68,
        ])

        return "\n".join(lines)

    def summary_dict(self) -> dict:
        """간결한 요약 딕셔너리를 반환합니다 (주요 지표만)."""
        m = self.calculate_metrics()
        return {
            "CAGR (%)": m.get("cagr_pct", 0),
            "총 수익률 (%)": m.get("total_return_pct", 0),
            "MDD (%)": m.get("mdd_pct", 0),
            "Sharpe Ratio": m.get("sharpe_ratio", 0),
            "Sortino Ratio": m.get("sortino_ratio", 0),
            "Calmar Ratio": m.get("calmar_ratio", 0),
            "일간 승률 (%)": m.get("daily_win_rate_pct", 0),
            "월간 승률 (%)": m.get("monthly_win_rate_pct", 0),
        }


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

    from backend.kiwoom.strategy.momentum.momentum_data_handler import MomentumDataHandler
    from backend.kiwoom.strategy.momentum.momentum_scorer import MomentumScorer
    from backend.kiwoom.strategy.momentum.momentum_rebalancer import MomentumRebalancer
    from backend.kiwoom.strategy.momentum.momentum_portfolio import MomentumPortfolioManager

    # ── 1. 데이터 로드 ──
    handler = MomentumDataHandler(finder=None)
    n = handler.load_from_cache()
    if n == 0:
        logger.error("캐시 데이터 없음.")
        sys.exit(1)
    handler.build_dataframes()

    # ── 2. 모듈 초기화 ──
    scorer = MomentumScorer(top_n=20, min_trading_value=5e9)
    rebalancer = MomentumRebalancer(weight_method="inverse_volatility")
    pm = MomentumPortfolioManager(
        initial_capital=1e8,
        commission=0.00015,
        slippage=0.002,
    )

    # ── 3. 미니 백테스트 (최근 6개 월말 사용) ──
    eom_dates = handler.get_month_end_dates()
    all_dates = handler.get_available_dates()

    # warmup: 252일 이상 확보
    backtest_window = handler.get_backtest_window(warmup_days=252)
    if backtest_window is None or len(backtest_window) == 0:
        logger.error("백테스트 윈도우 부족.")
        sys.exit(1)

    # 최근 6개 월말만 사용 (빠른 검증)
    test_eom = eom_dates[-6:]
    # 해당 기간의 일별 날짜 범위
    start_date = test_eom[0]
    end_date = all_dates[-1]
    daily_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]

    print(f"\n{'='*68}")
    print(f"  미니 백테스트: {start_date.date()} ~ {end_date.date()}")
    print(f"  영업일: {len(daily_dates)}일 | 리밸런싱: {len(test_eom)}회")
    print(f"{'='*68}")

    eom_set = set(test_eom)

    for date in daily_dates:
        hist_prices, hist_tv, kospi, kospi_sma = handler.get_data_up_to(date)
        current_prices = handler.get_current_prices(date)

        # 일별 기록
        pm.record_daily_equity(date, current_prices)

        # 월말 → 리밸런싱
        if date in eom_set:
            selected = scorer.select_assets(hist_prices, hist_tv)
            target_weights, regime = rebalancer.generate_target_weights(
                selected, hist_prices, kospi, kospi_sma
            )
            pm.execute_trades(target_weights, current_prices, date)
            pm.record_daily_equity(date, current_prices)  # 리밸런싱 후 재기록

    # ── 4. 성과 분석 ──
    analyzer = MomentumPerformanceAnalyzer(
        equity_curve=pm.equity_curve,
        initial_capital=pm.initial_capital,
        cost_summary=pm.get_cost_summary(),
        rebalance_history=rebalancer.rebalance_history,
    )

    # 종합 리포트 출력
    print(analyzer.report())

    # Drawdown 시리즈 요약
    dd = analyzer.get_drawdown_series()
    print(f"\n  Drawdown 통계:")
    print(f"    평균:  {dd.mean() * 100:>+8.2f}%")
    print(f"    중앙값: {dd.median() * 100:>+8.2f}%")
    print(f"    최악:  {dd.min() * 100:>+8.2f}%")

    # 연도별 수익률
    yr = analyzer.yearly_returns()
    if len(yr) > 1:
        print(f"\n  연도별 수익률 표준편차: {yr.std() * 100:.2f}%")

    print(f"\n{'='*68}")

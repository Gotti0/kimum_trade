"""
MomentumPortfolioManager: 현금/포지션 상태 머신 및 체결 시뮬레이션 모듈.

역할:
  1. 현금 잔고·종목별 보유 수량(Positions) 추적
  2. 방향성 슬리피지(Directional Slippage) — 매수 시 불리하게 높게, 매도 시 낮게
  3. 부분 상계 교체(Netting) — 기존 보유 종목은 차액만 매매 (불필요한 수수료 제거)
  4. 안전 현금망(Safety Cash Margin) — 수수료·슬리피지 포함 비용이 가용 현금 초과 시 차단
  5. 일별 포트폴리오 가치 기록(Equity Curve)
  6. 멀티마켓 비용 — 국내/글로벌 시장별 수수료·슬리피지 차등 적용
  7. 원화 통합 평가 — 글로벌 ETF(USD)는 환율 적용, 국내 종목은 KRW 원본

설계 문서 참조:
  §5 PortfolioManager (객체지향 아키텍처)
  §7 거래 비용 및 미시 구조의 정밀한 모델링
  §2-6 포트폴리오 매니저 확장 (멀티마켓 비용)
"""

import logging
from datetime import datetime
from typing import Dict, Optional, List

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── 시장별 비용 파라미터 (편도 기준) ────────────────────
COST_PARAMS = {
    "domestic": {"commission": 0.00015, "slippage": 0.002},   # 국내 주식
    "global":   {"commission": 0.0003,  "slippage": 0.001},   # 해외 ETF (스프레드 작음)
}


class MomentumPortfolioManager:
    """현금·포지션 상태 머신 및 체결 시뮬레이션 엔진.

    Rebalancer가 목표 가중치(Target Weights)를 전달하면,
    현재 포트폴리오 상태와 비교하여 매도/매수 주문을 순차적으로 실행합니다.
    슬리피지·수수료를 실시간으로 차감하며 현금 잔고를 엄격히 통제합니다.

    사용 예시::

        pm = MomentumPortfolioManager(initial_capital=1e8)
        pm.execute_trades(target_weights, current_prices, date)
        pm.record_daily_equity(date, current_prices)
    """

    def __init__(
        self,
        initial_capital: float = 1e8,
        commission: float = 0.00015,
        slippage: float = 0.002,
    ):
        """
        Args:
            initial_capital: 초기 자본금 (기본 1억 원).
            commission: 편도 거래 수수료 비율 (기본 0.015%).
            slippage: 편도 슬리피지 비율 (기본 0.2%).
                      매수 시 +slippage, 매도 시 -slippage 방향으로 불리하게 적용.
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, float] = {}  # {종목코드: 보유 수량(실수)}
        self.commission = commission
        self.slippage = slippage

        # 일별 자산 가치 기록
        self.equity_curve: Dict[pd.Timestamp, float] = {}

        # 거래 내역 로그
        self.trade_log: List[dict] = []

        # 통계 카운터
        self._total_commission_paid = 0.0
        self._total_slippage_cost = 0.0
        self._total_trades = 0
        self._total_turnover = 0.0  # 매매금액 누적합

    # ═══════════════════════════════════════════════════
    #  0. 멀티마켓 헬퍼
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _detect_market(ticker: str) -> str:
        """티커/종목코드 기반 시장 판별.

        국내 종목코드: 숫자로만 구성 (예: '005930')
        글로벌 ETF 티커: 영문 대문자 (예: 'SPY', 'AGG')
        """
        if ticker.isdigit():
            return "domestic"
        return "global"

    def _get_cost_params(self, ticker: str) -> tuple:
        """시장별 (commission, slippage)를 반환합니다."""
        market = self._detect_market(ticker)
        params = COST_PARAMS.get(market, COST_PARAMS["domestic"])
        return params["commission"], params["slippage"]

    def _to_krw(
        self, ticker: str, price: float, usdkrw_rate: float = 1.0,
    ) -> float:
        """가격을 원화로 환산. 글로벌 ETF는 USD×환율, 국내는 그대로."""
        if self._detect_market(ticker) == "global":
            return price * usdkrw_rate
        return price

    # ═══════════════════════════════════════════════════
    #  1. 포트폴리오 가치 평가
    # ═══════════════════════════════════════════════════

    def get_portfolio_value(
        self,
        current_prices: pd.Series,
        usdkrw_rate: float = 1.0,
    ) -> float:
        """현금 + 보유 주식의 시가평가(Mark-to-Market) 총합을 산출합니다.

        국내 종목 가격은 원화(KRW), 글로벌 ETF는 USD → 환율 적용하여 원화 환산.

        Args:
            current_prices: 종목코드/티커를 인덱스로 한 현재 종가 Series.
                            국내 종목은 KRW, 글로벌 ETF는 USD 단위.
            usdkrw_rate: USD/KRW 환율 (기본 1.0 — 국내 전용 모드).

        Returns:
            총 포트폴리오 가치 (현금 + 주식 평가액, KRW 기준).
        """
        stock_value = 0.0
        for ticker, shares in self.positions.items():
            price = current_prices.get(ticker, 0.0)
            if pd.notna(price) and price > 0:
                stock_value += shares * self._to_krw(ticker, price, usdkrw_rate)
        return self.cash + stock_value

    def get_position_value(
        self,
        ticker: str,
        current_prices: pd.Series,
        usdkrw_rate: float = 1.0,
    ) -> float:
        """특정 종목의 현재 평가액을 원화(KRW)로 반환합니다."""
        shares = self.positions.get(ticker, 0.0)
        price = current_prices.get(ticker, 0.0)
        if pd.notna(price) and price > 0:
            return shares * self._to_krw(ticker, price, usdkrw_rate)
        return 0.0

    # ═══════════════════════════════════════════════════
    #  2. 매매 실행 (핵심)
    # ═══════════════════════════════════════════════════

    def execute_trades(
        self,
        target_weights: Dict[str, float],
        current_prices: pd.Series,
        date: pd.Timestamp,
        usdkrw_rate: float = 1.0,
    ) -> None:
        """타겟 비중에 맞추어 Netting 기반 주문을 실행합니다.

        설계 문서 §7의 3가지 원칙 + 멀티마켓 비용:
          1. 방향성 슬리피지 — 매수: price×(1+s), 매도: price×(1-s)
          2. 안전 현금망 — cost = shares × exec_price × (1+commission)
          3. 부분 상계(Netting) — 비중 차이만 순매수/순매도
          4. 시장별 비용 차등 — COST_PARAMS로 국내/글로벌 수수료·슬리피지 분리

        실행 순서: 매도 → 매수 (현금을 먼저 확보한 후 매수)

        Args:
            target_weights: {종목코드또는티커: 목표비중(0~1)}. 합계 ≤ 1.0.
                            포함되지 않은 기존 보유 종목은 전량 매도.
            current_prices: 종목별 현재 시장 가격 (국내: KRW, 글로벌: USD).
            date: 거래 기준 날짜.
            usdkrw_rate: USD/KRW 환율 (기본 1.0 — 국내 전용 모드).
        """
        target_tickers = set(target_weights.keys())

        # ── Phase 1: 기존 보유 종목 중 타겟에 없는 것 → 전량 매도 ──
        tickers_to_liquidate = [
            t for t in list(self.positions.keys())
            if t not in target_tickers or target_weights.get(t, 0) == 0
        ]
        for ticker in tickers_to_liquidate:
            shares = self.positions.pop(ticker, 0.0)
            if shares <= 0:
                continue
            price = current_prices.get(ticker, 0.0)
            if not (pd.notna(price) and price > 0):
                continue

            comm, slip = self._get_cost_params(ticker)
            market = self._detect_market(ticker)
            krw_factor = usdkrw_rate if market == "global" else 1.0

            exec_price = price * (1 - slip)
            proceeds_krw = shares * exec_price * krw_factor
            fee = proceeds_krw * comm
            net_proceeds = proceeds_krw - fee
            self.cash += net_proceeds

            slippage_cost = shares * price * slip * krw_factor
            self._total_commission_paid += fee
            self._total_slippage_cost += slippage_cost
            self._total_trades += 1
            self._total_turnover += proceeds_krw

            self.trade_log.append({
                "date": date,
                "ticker": ticker,
                "action": "LIQUIDATE",
                "shares": -shares,
                "price": price,
                "exec_price": exec_price,
                "amount": -proceeds_krw,
                "fee": fee,
                "slippage": slippage_cost,
                "market": market,
                "currency": "USD" if market == "global" else "KRW",
            })

            logger.debug(
                "[%s] LIQUIDATE %s: %.2f shares @ %.0f (exec %.0f), "
                "proceeds=%.0f, fee=%.0f",
                date.date(), ticker, shares, price, exec_price,
                net_proceeds, fee,
            )

        # ── Phase 2: 매도 후 총 자산 재평가 → 비중 조절 ──
        total_value = self.get_portfolio_value(current_prices, usdkrw_rate)

        # Netting: 현재 보유 가치 vs 목표 가치의 차이(value_diff)만 거래
        sell_orders: List[tuple] = []   # (ticker, sell_value_krw, price, krw_factor)
        buy_orders: List[tuple] = []    # (ticker, buy_value_krw, price, krw_factor)

        for ticker, weight in target_weights.items():
            if weight <= 0:
                continue

            price = current_prices.get(ticker, 0.0)
            if not (pd.notna(price) and price > 0):
                continue

            market = self._detect_market(ticker)
            krw_factor = usdkrw_rate if market == "global" else 1.0
            krw_price = price * krw_factor

            target_value = total_value * weight          # KRW
            current_shares = self.positions.get(ticker, 0.0)
            current_value = current_shares * krw_price   # KRW
            value_diff = target_value - current_value    # KRW

            if value_diff > krw_price:
                # 순매수 필요
                buy_orders.append((ticker, value_diff, price, krw_factor))
            elif value_diff < -krw_price:
                # 순매도 필요
                sell_orders.append((ticker, abs(value_diff), price, krw_factor))
            # else: 차이가 1주 가격 미만 → 무시 (불필요한 소액 거래 방지)

        # ── Phase 2a: 순매도 먼저 실행 (현금 확보) ──
        for ticker, sell_value_krw, price, krw_factor in sell_orders:
            comm, slip = self._get_cost_params(ticker)
            market = self._detect_market(ticker)

            exec_price = price * (1 - slip)
            shares_to_sell = sell_value_krw / (exec_price * krw_factor)
            current_shares = self.positions.get(ticker, 0.0)
            shares_to_sell = min(shares_to_sell, current_shares)

            if shares_to_sell <= 0:
                continue

            proceeds_krw = shares_to_sell * exec_price * krw_factor
            fee = proceeds_krw * comm
            net_proceeds = proceeds_krw - fee
            self.cash += net_proceeds
            self.positions[ticker] = current_shares - shares_to_sell

            if self.positions[ticker] <= 0:
                del self.positions[ticker]

            slippage_cost = shares_to_sell * price * slip * krw_factor
            self._total_commission_paid += fee
            self._total_slippage_cost += slippage_cost
            self._total_trades += 1
            self._total_turnover += proceeds_krw

            self.trade_log.append({
                "date": date,
                "ticker": ticker,
                "action": "NET_SELL",
                "shares": -shares_to_sell,
                "price": price,
                "exec_price": exec_price,
                "amount": -proceeds_krw,
                "fee": fee,
                "slippage": slippage_cost,
                "market": market,
                "currency": "USD" if market == "global" else "KRW",
            })

            logger.debug(
                "[%s] NET_SELL %s: %.2f shares @ %.0f (exec %.0f)",
                date.date(), ticker, shares_to_sell, price, exec_price,
            )

        # ── Phase 2b: 순매수 실행 ──
        for ticker, buy_value_krw, price, krw_factor in buy_orders:
            comm, slip = self._get_cost_params(ticker)
            market = self._detect_market(ticker)

            exec_price = price * (1 + slip)
            shares_to_buy = buy_value_krw / (exec_price * krw_factor)

            # 안전 현금망: 수수료 포함 총 비용 산출 (KRW)
            total_cost = shares_to_buy * exec_price * krw_factor * (1 + comm)

            # 가용 현금 부족 시 매수 가능 수량으로 축소
            if total_cost > self.cash:
                if self.cash <= 0:
                    continue
                max_shares = self.cash / (exec_price * krw_factor * (1 + comm))
                shares_to_buy = max_shares

                if shares_to_buy <= 0:
                    continue

                total_cost = shares_to_buy * exec_price * krw_factor * (1 + comm)

            gross_amount_krw = shares_to_buy * exec_price * krw_factor
            fee = gross_amount_krw * comm
            self.cash -= (gross_amount_krw + fee)

            current_shares = self.positions.get(ticker, 0.0)
            self.positions[ticker] = current_shares + shares_to_buy

            slippage_cost = shares_to_buy * price * slip * krw_factor
            self._total_commission_paid += fee
            self._total_slippage_cost += slippage_cost
            self._total_trades += 1
            self._total_turnover += gross_amount_krw

            self.trade_log.append({
                "date": date,
                "ticker": ticker,
                "action": "NET_BUY",
                "shares": shares_to_buy,
                "price": price,
                "exec_price": exec_price,
                "amount": gross_amount_krw,
                "fee": fee,
                "slippage": slippage_cost,
                "market": market,
                "currency": "USD" if market == "global" else "KRW",
            })

            logger.debug(
                "[%s] NET_BUY %s: %.2f shares @ %.0f (exec %.0f), "
                "cost=%.0f, fee=%.0f",
                date.date(), ticker, shares_to_buy, price, exec_price,
                gross_amount_krw + fee, fee,
            )

    # ═══════════════════════════════════════════════════
    #  3. 일별 기록
    # ═══════════════════════════════════════════════════

    def record_daily_equity(
        self,
        date: pd.Timestamp,
        current_prices: pd.Series,
        usdkrw_rate: float = 1.0,
    ) -> float:
        """매 영업일 포트폴리오 가치를 기록합니다.

        Args:
            date: 기록 날짜.
            current_prices: 현재 종가 (국내: KRW, 글로벌: USD).
            usdkrw_rate: USD/KRW 환율 (기본 1.0).

        Returns:
            해당일 포트폴리오 총 가치 (KRW).
        """
        value = self.get_portfolio_value(current_prices, usdkrw_rate)
        self.equity_curve[date] = value
        return value

    # ═══════════════════════════════════════════════════
    #  4. 조회·분석 유틸리티
    # ═══════════════════════════════════════════════════

    def get_equity_series(self) -> pd.Series:
        """일별 포트폴리오 가치를 Pandas Series로 반환합니다."""
        return pd.Series(self.equity_curve, dtype=float)

    def get_positions_summary(
        self, current_prices: pd.Series, usdkrw_rate: float = 1.0,
    ) -> pd.DataFrame:
        """현재 보유 포지션 요약을 DataFrame으로 반환합니다."""
        if not self.positions:
            return pd.DataFrame(
                columns=["ticker", "shares", "price", "value", "weight", "market", "currency"]
            )

        total_value = self.get_portfolio_value(current_prices, usdkrw_rate)
        rows = []
        for ticker, shares in sorted(self.positions.items()):
            market = self._detect_market(ticker)
            price = current_prices.get(ticker, 0.0)
            krw_factor = usdkrw_rate if market == "global" else 1.0
            value = shares * price * krw_factor if pd.notna(price) else 0.0
            weight = value / total_value if total_value > 0 else 0.0
            rows.append({
                "ticker": ticker,
                "shares": shares,
                "price": price,
                "value": value,
                "weight": weight,
                "market": market,
                "currency": "USD" if market == "global" else "KRW",
            })

        return pd.DataFrame(rows)

    def get_trade_log_df(self) -> pd.DataFrame:
        """전체 거래 내역을 DataFrame으로 반환합니다."""
        if not self.trade_log:
            return pd.DataFrame()
        return pd.DataFrame(self.trade_log)

    def get_cost_summary(self) -> dict:
        """누적 거래 비용 요약을 반환합니다."""
        return {
            "total_trades": self._total_trades,
            "total_commission": self._total_commission_paid,
            "total_slippage_cost": self._total_slippage_cost,
            "total_friction": self._total_commission_paid + self._total_slippage_cost,
            "total_turnover": self._total_turnover,
            "avg_cost_per_trade": (
                (self._total_commission_paid + self._total_slippage_cost) / self._total_trades
                if self._total_trades > 0 else 0.0
            ),
        }

    def summary(
        self,
        current_prices: Optional[pd.Series] = None,
        usdkrw_rate: float = 1.0,
    ) -> str:
        """포트폴리오 현황 요약 문자열을 반환합니다."""
        total_value = (
            self.get_portfolio_value(current_prices, usdkrw_rate)
            if current_prices is not None
            else self.cash
        )
        pnl = total_value - self.initial_capital
        pnl_pct = pnl / self.initial_capital * 100

        costs = self.get_cost_summary()

        domestic_count = sum(
            1 for t in self.positions if self._detect_market(t) == "domestic"
        )
        global_count = sum(
            1 for t in self.positions if self._detect_market(t) == "global"
        )

        lines = [
            "=" * 60,
            "  MomentumPortfolioManager 현황",
            "=" * 60,
            f"  초기 자본금:    {self.initial_capital:>15,.0f}원",
            f"  현재 현금:      {self.cash:>15,.0f}원",
            f"  주식 평가액:    {(total_value - self.cash):>15,.0f}원",
            f"  총 자산:        {total_value:>15,.0f}원",
            f"  손익(P&L):      {pnl:>+15,.0f}원 ({pnl_pct:+.2f}%)",
            "-" * 60,
            f"  보유 종목 수:   {len(self.positions):>8d}  "
            f"(국내 {domestic_count} / 글로벌 {global_count})",
            f"  적용 환율:      {usdkrw_rate:>12,.2f} ₩/$",
            f"  총 거래 횟수:   {costs['total_trades']:>8d}",
            f"  누적 수수료:    {costs['total_commission']:>15,.0f}원",
            f"  누적 슬리피지:  {costs['total_slippage_cost']:>15,.0f}원",
            f"  누적 마찰비용:  {costs['total_friction']:>15,.0f}원",
            f"  누적 회전율:    {costs['total_turnover']:>15,.0f}원",
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

    global_mode = "--global" in sys.argv

    if global_mode:
        # ══════════════════════════════════════════════
        #  글로벌 멀티마켓 비용 테스트
        # ══════════════════════════════════════════════
        from backend.kiwoom.strategy.momentum.momentum_data_handler import MomentumDataHandler
        from backend.kiwoom.strategy.momentum.momentum_scorer import MomentumScorer
        from backend.kiwoom.strategy.momentum.momentum_rebalancer import MomentumRebalancer
        from backend.kiwoom.strategy.momentum.momentum_asset_classes import get_preset

        print(f"\n{'='*70}")
        print("  PortfolioManager 멀티마켓 비용 테스트 (글로벌 + 국내)")
        print(f"{'='*70}")

        # 1. 글로벌 데이터 로드
        handler = MomentumDataHandler(finder=None)
        n_global = handler.load_global_data()
        handler.build_global_dataframes()

        global_prices = handler.global_prices
        global_sma200 = handler.global_sma200
        latest_date = global_prices.index[-1]
        current_global = global_prices.iloc[-1]

        print(f"\n  글로벌 ETF {n_global}개 로드, 마지막 날짜: {latest_date.date()}")

        # 2. 환율 조회
        from backend.kiwoom.strategy.global_etf.global_data_fetcher import GlobalDataFetcher
        fetcher = GlobalDataFetcher()
        usdkrw = fetcher.fetch_usdkrw_rate()
        print(f"  USD/KRW 환율: {usdkrw:,.2f}")

        # 3. 시장별 비용 파라미터 출력
        print(f"\n  [시장별 비용 파라미터 (COST_PARAMS)]")
        for market_name, params in COST_PARAMS.items():
            print(f"    {market_name:>10}: 수수료 {params['commission']*100:.4f}% / "
                  f"슬리피지 {params['slippage']*100:.2f}%")

        # 4. 프리셋별 테스트
        for preset_name in ["growth", "balanced", "stable"]:
            preset = get_preset(preset_name)
            print(f"\n  {'─'*60}")
            print(f"  프리셋: {preset['icon']} {preset['label']} (risk {preset['risk_level']})")
            print(f"  {'─'*60}")

            # PM 초기화
            pm_test = MomentumPortfolioManager(initial_capital=1e8)

            # Scorer → 자산 배분
            scorer = MomentumScorer(top_n=20, min_trading_value=5e9)
            asset_weights, kr_top_n = scorer.select_global_assets(
                global_prices, pd.DataFrame(), pd.DataFrame(), preset=preset,
            )

            # Rebalancer → 국면 필터 적용
            rebalancer = MomentumRebalancer()
            final_weights, regimes = rebalancer.generate_global_target_weights(
                asset_weights, global_prices, global_sma200,
                kr_top_n_codes=kr_top_n,
            )

            # current_prices: 글로벌 ETF의 USD 가격 (Series)
            prices_series = current_global.copy()

            # 5. 매매 실행 (멀티마켓 비용 적용)
            pm_test.execute_trades(
                final_weights, prices_series, latest_date, usdkrw_rate=usdkrw,
            )
            pm_test.record_daily_equity(
                latest_date, prices_series, usdkrw_rate=usdkrw,
            )

            # 6. 결과 출력
            print(pm_test.summary(prices_series, usdkrw_rate=usdkrw))

            # 포지션 상세
            pos_df = pm_test.get_positions_summary(prices_series, usdkrw_rate=usdkrw)
            if not pos_df.empty:
                print(f"\n  [보유 포지션] (상위 10개)")
                top_pos = pos_df.nlargest(10, "value")
                for _, row in top_pos.iterrows():
                    bar = "█" * int(row["weight"] * 50)
                    curr_sym = "$" if row["currency"] == "USD" else "₩"
                    print(f"    {row['ticker']:>8s}  {row['weight']:6.2%}  "
                          f"{curr_sym}{row['price']:>10,.2f} × {row['shares']:>8.2f}주  "
                          f"= ₩{row['value']:>14,.0f}  {bar}")

            # 거래 로그 요약
            log_df = pm_test.get_trade_log_df()
            if not log_df.empty:
                print(f"\n  [거래 로그] ({len(log_df)}건)")
                if "market" in log_df.columns:
                    market_counts = log_df["market"].value_counts()
                    for mkt, cnt in market_counts.items():
                        print(f"    {mkt}: {cnt}건")

        print(f"\n{'='*70}")

    else:
        # ══════════════════════════════════════════════
        #  기존 국내 전용 테스트
        # ══════════════════════════════════════════════
        from backend.kiwoom.strategy.momentum.momentum_data_handler import MomentumDataHandler
        from backend.kiwoom.strategy.momentum.momentum_scorer import MomentumScorer

        # 1. 데이터 로드
        handler = MomentumDataHandler(finder=None)
        n = handler.load_from_cache()
        if n == 0:
            logger.error("캐시 데이터 없음.")
            sys.exit(1)

        handler.build_dataframes()

        # 2. 마지막 2개 월말 날짜로 리밸런싱 시뮬레이션
        eom_dates = handler.get_month_end_dates()
        if len(eom_dates) < 2:
            logger.error("월말 날짜 부족.")
            sys.exit(1)

        scorer = MomentumScorer(top_n=20, min_trading_value=5e9)
        pm = MomentumPortfolioManager(
            initial_capital=1e8,
            commission=0.00015,
            slippage=0.002,
        )

        # 2-1. 첫 번째 월말: 최초 매수
        date1 = eom_dates[-2]
        hist_prices1, hist_tv1, _, _ = handler.get_data_up_to(date1)
        current_prices1 = handler.get_current_prices(date1)

        selected1 = scorer.select_assets(hist_prices1, hist_tv1)
        weight1 = 1.0 / len(selected1) if selected1 else 0.0
        target_weights1 = {s: weight1 for s in selected1}

        print(f"\n{'='*70}")
        print(f"  리밸런싱 #1: {date1.date()} — {len(selected1)}개 종목 편입")
        print(f"{'='*70}")

        pm.execute_trades(target_weights1, current_prices1, date1)
        pm.record_daily_equity(date1, current_prices1)

        pos1 = pm.get_positions_summary(current_prices1)
        print(f"\n  보유 종목 수: {len(pos1)}")
        print(f"  현금 잔고:    {pm.cash:,.0f}원")
        print(f"  총 자산:      {pm.get_portfolio_value(current_prices1):,.0f}원")

        # 2-2. 두 번째 월말: 리밸런싱 (Netting 테스트)
        date2 = eom_dates[-1]
        hist_prices2, hist_tv2, _, _ = handler.get_data_up_to(date2)
        current_prices2 = handler.get_current_prices(date2)

        selected2 = scorer.select_assets(hist_prices2, hist_tv2)
        weight2 = 1.0 / len(selected2) if selected2 else 0.0
        target_weights2 = {s: weight2 for s in selected2}

        # 종목 변화 분석
        set1 = set(selected1)
        set2 = set(selected2)
        new_in = set2 - set1
        dropped = set1 - set2
        retained = set1 & set2

        print(f"\n{'='*70}")
        print(f"  리밸런싱 #2: {date2.date()} — Netting 테스트")
        print(f"{'='*70}")
        print(f"  유지: {len(retained)}개 | 신규 편입: {len(new_in)}개 | 퇴출: {len(dropped)}개")

        pm.execute_trades(target_weights2, current_prices2, date2)
        pm.record_daily_equity(date2, current_prices2)

        # 3. 결과 출력
        print(pm.summary(current_prices2))

        # 거래 로그 요약
        log_df = pm.get_trade_log_df()
        if not log_df.empty:
            action_counts = log_df["action"].value_counts()
            print("\n  거래 유형별 횟수:")
            for action, cnt in action_counts.items():
                print(f"    {action}: {cnt}회")

            print(f"\n  Netting 효과: "
                  f"순매도(NET_SELL) {action_counts.get('NET_SELL', 0)}회, "
                  f"순매수(NET_BUY) {action_counts.get('NET_BUY', 0)}회, "
                  f"전량청산(LIQUIDATE) {action_counts.get('LIQUIDATE', 0)}회")

        print(f"{'='*70}")

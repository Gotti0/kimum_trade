"""
MomentumBacktester: 중장기 듀얼 모멘텀 하이브리드 백테스팅 메인 엔진.

설계 문서 §6 — 메인 하이브리드 엔진 실행 컨트롤러

아키텍처:
  - 벡터화(Vectorized): 모멘텀 스코어링, SMA 산출, 변동성 계산
  - 이벤트 기반(Event-Driven): 월말 리밸런싱, 주문 체결, 포지션 관리

워크플로우:
  1. DataHandler — 캐시 로드, DataFrame 구축
  2. 일간 루프 — 매 영업일 포트폴리오 mark-to-market
  3. 월말 이벤트 — Scorer → Rebalancer → PortfolioManager 순차 실행
  4. 사후 분석 — PerformanceAnalyzer 종합 리포트

CLI 사용법:
  python -m backend.kiwoom.momentum_backtester
  python -m backend.kiwoom.momentum_backtester --capital 200000000
  python -m backend.kiwoom.momentum_backtester --weight equal_weight --top-n 30
  python -m backend.kiwoom.momentum_backtester --full  (전체 기간 백테스트)
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

import pandas as pd
import numpy as np

from backend.kiwoom.momentum_data_handler import MomentumDataHandler
from backend.kiwoom.momentum_scorer import MomentumScorer
from backend.kiwoom.momentum_rebalancer import MomentumRebalancer
from backend.kiwoom.momentum_portfolio import MomentumPortfolioManager
from backend.kiwoom.momentum_performance import MomentumPerformanceAnalyzer

logger = logging.getLogger(__name__)

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


class MomentumBacktester:
    """중장기 듀얼 모멘텀 하이브리드 백테스팅 엔진.

    설계 문서 §1~§9의 전체 파이프라인을 하나의 클래스로 통합합니다.
    5개 서브모듈(DataHandler, Scorer, Rebalancer, PortfolioManager,
    PerformanceAnalyzer)을 순차적으로 연결합니다.

    사용 예시::

        bt = MomentumBacktester(initial_capital=1e8)
        result = bt.run()
        print(result["report"])
    """

    def __init__(
        self,
        initial_capital: float = 1e8,
        top_n: int = 20,
        min_trading_value: float = 5e9,
        weight_method: str = "inverse_volatility",
        commission: float = 0.00015,
        slippage: float = 0.002,
        warmup_days: int = 252,
        risk_free_rate: float = 0.0,
    ):
        """
        Args:
            initial_capital: 초기 자본금 (기본 1억 원).
            top_n: 모멘텀 상위 편입 종목 수 (기본 20).
            min_trading_value: ADTV 필터 임계값 (기본 50억 원).
            weight_method: 가중치 배분 방식 (``"inverse_volatility"`` or ``"equal_weight"``).
            commission: 편도 거래 수수료 비율 (기본 0.015%).
            slippage: 편도 슬리피지 비율 (기본 0.2%).
            warmup_days: 모멘텀 산출 웜업 기간 (기본 252일 = 12개월).
            risk_free_rate: 절대 모멘텀 기준 무위험 수익률 (기본 0%).
        """
        self.initial_capital = initial_capital
        self.top_n = top_n
        self.min_trading_value = min_trading_value
        self.weight_method = weight_method
        self.commission = commission
        self.slippage = slippage
        self.warmup_days = warmup_days
        self.risk_free_rate = risk_free_rate

        # 서브모듈 (run()에서 초기화)
        self.handler: Optional[MomentumDataHandler] = None
        self.scorer: Optional[MomentumScorer] = None
        self.rebalancer: Optional[MomentumRebalancer] = None
        self.portfolio: Optional[MomentumPortfolioManager] = None
        self.analyzer: Optional[MomentumPerformanceAnalyzer] = None

    # ═══════════════════════════════════════════════════
    #  메인 백테스트 실행
    # ═══════════════════════════════════════════════════

    def run(
        self,
        full: bool = False,
        recent_months: int = 12,
    ) -> dict:
        """백테스트를 실행합니다.

        Args:
            full: True면 웜업 이후 전체 기간 백테스트.
                  False면 최근 N개월만 (빠른 검증용).
            recent_months: ``full=False``일 때 사용할 최근 월수 (기본 12).

        Returns:
            dict with keys:
              - ``"metrics"``: 성과 지표 딕셔너리
              - ``"report"``: 종합 리포트 문자열
              - ``"equity_curve"``: {날짜: 자산가치} 딕셔너리
              - ``"trade_log"``: 거래내역 DataFrame
              - ``"regime_history"``: 국면 이력 DataFrame
              - ``"elapsed_sec"``: 소요 시간 (초)
        """
        t0 = time.time()

        # ── 1. DataHandler 초기화 ──
        logger.info("=" * 68)
        logger.info("  중장기 듀얼 모멘텀 백테스터 시작")
        logger.info("=" * 68)
        logger.info("  초기자본: %s원 | Top-N: %d | 가중치: %s",
                     f"{self.initial_capital:,.0f}", self.top_n, self.weight_method)
        logger.info("  수수료: %.4f%% | 슬리피지: %.3f%% | 웜업: %d일",
                     self.commission * 100, self.slippage * 100, self.warmup_days)

        self.handler = MomentumDataHandler(finder=None)
        n_loaded = self.handler.load_from_cache()
        if n_loaded == 0:
            logger.error("캐시 데이터 없음. 먼저 일봉 데이터를 캐싱하세요.")
            return {"metrics": {}, "report": "데이터 없음", "equity_curve": {},
                    "trade_log": pd.DataFrame(), "regime_history": pd.DataFrame(),
                    "elapsed_sec": 0}

        self.handler.build_dataframes()

        # ── 2. 서브모듈 초기화 ──
        self.scorer = MomentumScorer(
            top_n=self.top_n,
            min_trading_value=self.min_trading_value,
            risk_free_rate=self.risk_free_rate,
        )
        self.rebalancer = MomentumRebalancer(
            weight_method=self.weight_method,
        )
        self.portfolio = MomentumPortfolioManager(
            initial_capital=self.initial_capital,
            commission=self.commission,
            slippage=self.slippage,
        )

        # ── 3. 백테스트 구간 결정 ──
        all_dates = self.handler.get_available_dates()
        eom_dates = self.handler.get_month_end_dates()
        backtest_window = self.handler.get_backtest_window(self.warmup_days)

        if len(backtest_window) == 0:
            logger.error("백테스트 윈도우 부족 (웜업 %d일 필요).", self.warmup_days)
            return {"metrics": {}, "report": "데이터 부족", "equity_curve": {},
                    "trade_log": pd.DataFrame(), "regime_history": pd.DataFrame(),
                    "elapsed_sec": 0}

        if full:
            # 전체 기간: 웜업 이후 첫 월말부터
            start_date = backtest_window[0]
        else:
            # 최근 N개월: 최근 N+1번째 월말부터 시작 (N회 리밸런싱 포함)
            n_eom_needed = recent_months + 1
            if len(eom_dates) >= n_eom_needed:
                start_date = eom_dates[-n_eom_needed]
            else:
                start_date = backtest_window[0]

        # 시작일이 웜업 이전이면 보정
        if start_date < backtest_window[0]:
            start_date = backtest_window[0]

        end_date = all_dates[-1]

        # 일별 순회 대상
        daily_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]

        # 리밸런싱 대상 월말
        active_eom = eom_dates[(eom_dates >= start_date) & (eom_dates <= end_date)]
        eom_set = set(active_eom)

        logger.info("  백테스트 구간: %s ~ %s", start_date.date(), end_date.date())
        logger.info("  영업일: %d일 | 리밸런싱: %d회", len(daily_dates), len(active_eom))
        logger.info("-" * 68)

        # ── 4. 하이브리드 이벤트 루프 ──
        rebal_count = 0
        progress_interval = max(1, len(daily_dates) // 10)  # 10% 단위 진행률

        for i, date in enumerate(daily_dates):
            # 현재가 조회
            current_prices = self.handler.get_current_prices(date)

            # 일별 mark-to-market
            self.portfolio.record_daily_equity(date, current_prices)

            # 월말 리밸런싱 이벤트
            if date in eom_set:
                rebal_count += 1
                self._execute_rebalance(date, current_prices, rebal_count, len(active_eom))

            # 진행률 로깅
            if (i + 1) % progress_interval == 0 or i == len(daily_dates) - 1:
                pct = (i + 1) / len(daily_dates) * 100
                pv = self.portfolio.get_portfolio_value(current_prices)
                logger.info(
                    "  [%5.1f%%] %s | 자산: %s원 | 종목: %d | 리밸런싱: %d/%d",
                    pct, date.date(),
                    f"{pv:,.0f}", len(self.portfolio.positions),
                    rebal_count, len(active_eom),
                )

        # ── 5. 사후 분석 ──
        elapsed = time.time() - t0

        self.analyzer = MomentumPerformanceAnalyzer(
            equity_curve=self.portfolio.equity_curve,
            initial_capital=self.initial_capital,
            cost_summary=self.portfolio.get_cost_summary(),
            rebalance_history=self.rebalancer.rebalance_history,
        )

        metrics = self.analyzer.calculate_metrics()
        report = self.analyzer.report()

        # 소요 시간 추가
        report += f"\n  소요 시간: {elapsed:.2f}초\n"

        logger.info("=" * 68)
        logger.info("  백테스트 완료 — %.2f초 소요", elapsed)
        logger.info("=" * 68)

        return {
            "metrics": metrics,
            "report": report,
            "equity_curve": dict(self.portfolio.equity_curve),
            "trade_log": self.portfolio.get_trade_log_df(),
            "regime_history": self.rebalancer.get_regime_history(),
            "elapsed_sec": elapsed,
        }

    # ═══════════════════════════════════════════════════
    #  리밸런싱 이벤트 처리
    # ═══════════════════════════════════════════════════

    def _execute_rebalance(
        self,
        date: pd.Timestamp,
        current_prices: pd.Series,
        rebal_idx: int,
        total_rebal: int,
    ) -> None:
        """단일 월말 리밸런싱 이벤트를 처리합니다.

        워크플로우:
          1. DataHandler → 편향 제거된 과거 데이터 슬라이스
          2. MomentumScorer → 상위 N개 종목 선정
          3. MomentumRebalancer → 국면 필터 + 가중치 배분
          4. MomentumPortfolioManager → 매매 실행 (Netting)
        """
        # 1. 데이터 슬라이스 (미래 참조 차단)
        hist_prices, hist_tv, kospi, kospi_sma = self.handler.get_data_up_to(date)

        # 2. 듀얼 모멘텀 스코어링
        selected = self.scorer.select_assets(hist_prices, hist_tv)

        # 3. 국면 필터 + 가중치 배분
        target_weights, regime = self.rebalancer.generate_target_weights(
            selected, hist_prices, kospi, kospi_sma,
        )

        # 4. 매매 실행
        pre_value = self.portfolio.get_portfolio_value(current_prices)
        self.portfolio.execute_trades(target_weights, current_prices, date)
        post_value = self.portfolio.get_portfolio_value(current_prices)

        # 리밸런싱 후 equity 재기록
        self.portfolio.record_daily_equity(date, current_prices)

        # 로깅
        n_selected = len(selected)
        n_nonzero = sum(1 for w in target_weights.values() if w > 0)

        logger.info(
            "  [리밸런싱 %d/%d] %s | 국면: %s | 편입: %d종목 | "
            "사전: %s → 사후: %s원",
            rebal_idx, total_rebal, date.date(), regime,
            n_nonzero,
            f"{pre_value:,.0f}", f"{post_value:,.0f}",
        )

    # ═══════════════════════════════════════════════════
    #  설정 요약
    # ═══════════════════════════════════════════════════

    def config_summary(self) -> str:
        """현재 설정을 문자열로 반환합니다."""
        lines = [
            "=" * 60,
            "  MomentumBacktester 설정",
            "=" * 60,
            f"  초기 자본금:     {self.initial_capital:>15,.0f}원",
            f"  Top-N:           {self.top_n:>8d}",
            f"  ADTV 임계값:     {self.min_trading_value:>15,.0f}원",
            f"  가중치 방식:     {self.weight_method:>15s}",
            f"  수수료:          {self.commission * 100:>12.4f}%",
            f"  슬리피지:        {self.slippage * 100:>12.3f}%",
            f"  웜업 기간:       {self.warmup_days:>8d}일",
            f"  무위험 수익률:   {self.risk_free_rate * 100:>12.2f}%",
            "=" * 60,
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  CLI 진입점
# ═══════════════════════════════════════════════════════

def main():
    """커맨드라인 인터페이스 진입점."""
    parser = argparse.ArgumentParser(
        description="중장기 듀얼 모멘텀 백테스터",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python -m backend.kiwoom.momentum_backtester
  python -m backend.kiwoom.momentum_backtester --full
  python -m backend.kiwoom.momentum_backtester --capital 200000000 --top-n 30
  python -m backend.kiwoom.momentum_backtester --weight equal_weight --months 24
        """,
    )

    parser.add_argument(
        "--capital",
        type=float,
        default=1e8,
        help="초기 자본금 (기본값: 100,000,000)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        dest="top_n",
        help="모멘텀 상위 편입 종목 수 (기본값: 20)",
    )
    parser.add_argument(
        "--min-tv",
        type=float,
        default=5e9,
        dest="min_trading_value",
        help="ADTV 필터 임계값 (기본값: 5,000,000,000 = 50억)",
    )
    parser.add_argument(
        "--weight",
        type=str,
        default="inverse_volatility",
        choices=["inverse_volatility", "equal_weight"],
        dest="weight_method",
        help="가중치 배분 방식 (기본값: inverse_volatility)",
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=0.00015,
        help="편도 거래 수수료 비율 (기본값: 0.00015 = 0.015%%)",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=0.002,
        help="편도 슬리피지 비율 (기본값: 0.002 = 0.2%%)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=252,
        dest="warmup_days",
        help="모멘텀 산출 웜업 기간 (기본값: 252 = 12개월)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="전체 기간 백테스트 (웜업 이후 전체)",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=12,
        dest="recent_months",
        help="최근 N개월 백테스트 (--full 미지정 시, 기본값: 12)",
    )
    parser.add_argument(
        "--risk-free",
        type=float,
        default=0.0,
        dest="risk_free_rate",
        help="절대 모멘텀 기준 무위험 수익률 (기본값: 0.0)",
    )
    parser.add_argument(
        "--log-file",
        action="store_true",
        dest="log_file",
        help="로그를 파일에도 저장 (logs/ 디렉토리)",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        dest="save_json",
        help="결과를 JSON 파일로 저장 (cache/momentum/latest_result.json)",
    )

    args = parser.parse_args()

    # ── 로깅 설정 ──
    handlers = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        log_dir = os.path.join(_project_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(
            log_dir,
            f"momentum_bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

    # ── 백테스터 생성 & 실행 ──
    bt = MomentumBacktester(
        initial_capital=args.capital,
        top_n=args.top_n,
        min_trading_value=args.min_trading_value,
        weight_method=args.weight_method,
        commission=args.commission,
        slippage=args.slippage,
        warmup_days=args.warmup_days,
        risk_free_rate=args.risk_free_rate,
    )

    # 설정 출력
    print(bt.config_summary())

    result = bt.run(
        full=args.full,
        recent_months=args.recent_months,
    )

    # 성과 리포트 출력
    print(result["report"])

    # 거래 요약
    trade_log = result["trade_log"]
    if not trade_log.empty:
        action_counts = trade_log["action"].value_counts()
        print("\n  거래 유형별 횟수:")
        for action, cnt in action_counts.items():
            print(f"    {action}: {cnt}회")

    # 국면 이력 요약
    regime_df = result["regime_history"]
    if not regime_df.empty and "regime" in regime_df.columns:
        bull_n = (regime_df["regime"] == "BULL").sum()
        bear_n = (regime_df["regime"] == "BEAR").sum()
        print(f"\n  국면 이력: BULL {bull_n}회 / BEAR {bear_n}회")

    # ── JSON 결과 저장 ──
    if args.save_json:
        _save_result_json(result, bt)

    return result


def _save_result_json(result: dict, bt: "MomentumBacktester") -> None:
    """백테스트 결과를 JSON 파일로 저장 (프론트엔드 연동용)."""
    import json

    out_dir = os.path.join(_project_root, "cache", "momentum")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "latest_result.json")

    # equity_curve를 직렬화 가능한 형태로 변환
    equity_serializable = {}
    for k, v in result.get("equity_curve", {}).items():
        date_str = k.strftime("%Y-%m-%d") if hasattr(k, "strftime") else str(k)
        equity_serializable[date_str] = round(float(v), 0)

    # 거래 로그 요약
    trade_summary = {}
    trade_log = result.get("trade_log", pd.DataFrame())
    if not trade_log.empty and "action" in trade_log.columns:
        for action, cnt in trade_log["action"].value_counts().items():
            trade_summary[action] = int(cnt)

    # 국면 이력 요약
    regime_summary = {"BULL": 0, "BEAR": 0}
    regime_df = result.get("regime_history", pd.DataFrame())
    if not regime_df.empty and "regime" in regime_df.columns:
        regime_summary["BULL"] = int((regime_df["regime"] == "BULL").sum())
        regime_summary["BEAR"] = int((regime_df["regime"] == "BEAR").sum())

    # metrics를 안전하게 직렬화
    metrics_safe = {}
    for k, v in result.get("metrics", {}).items():
        if isinstance(v, (int, float)):
            metrics_safe[k] = round(float(v), 6)
        elif isinstance(v, str):
            metrics_safe[k] = v
        else:
            metrics_safe[k] = str(v)

    output = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "initial_capital": bt.initial_capital,
            "top_n": bt.top_n,
            "weight_method": bt.weight_method,
            "commission": bt.commission,
            "slippage": bt.slippage,
            "warmup_days": bt.warmup_days,
            "min_trading_value": bt.min_trading_value,
        },
        "metrics": metrics_safe,
        "equity_curve": equity_serializable,
        "trade_summary": trade_summary,
        "regime_summary": regime_summary,
        "elapsed_sec": round(result.get("elapsed_sec", 0), 2),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  결과 JSON 저장: {out_path}")


if __name__ == "__main__":
    main()

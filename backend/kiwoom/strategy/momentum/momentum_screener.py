"""
momentum_screener.py: 듀얼 모멘텀 스크리너 독립 실행 스크립트.

모멘텀 백테스터(momentum_backtester.py)의 투자 전략을 그대로 적용하여
현시점 기준 Top-N 편입 종목을 스크리닝합니다.

전략 로직:
  1. DataHandler  -- cache/daily_charts 일봉 캐시 로드, DataFrame 구축
  2. MomentumScorer -- ADTV >= 50억 필터 + 3/6/12M 듀얼 모멘텀 스코어링
  3. MomentumRebalancer -- KOSPI vs SMA200 국면 필터 + 가중치 배분
  4. 결과 JSON 저장 (cache/screener/momentum_latest.json)

Usage:
    python -m backend.kiwoom.strategy.momentum.momentum_screener
    python -m backend.kiwoom.strategy.momentum.momentum_screener --top-n 30
    python -m backend.kiwoom.strategy.momentum.momentum_screener --weight equal_weight
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from backend.kiwoom.strategy.momentum.momentum_data_handler import MomentumDataHandler
from backend.kiwoom.strategy.momentum.momentum_scorer import MomentumScorer
from backend.kiwoom.strategy.momentum.momentum_rebalancer import MomentumRebalancer

logger = logging.getLogger(__name__)

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
RESULT_DIR = os.path.join(_project_root, "cache", "screener")
STOCK_MAP_FILE = os.path.join(_project_root, "cache", "stock_map.json")
os.makedirs(RESULT_DIR, exist_ok=True)


def _load_stock_map() -> dict[str, str]:
    """cache/stock_map.json에서 종목코드 -> 종목명 매핑을 로드합니다.

    stock_map.json은 { 종목명: 종목코드 } 형식이므로 역전하여 반환합니다.
    """
    if os.path.isfile(STOCK_MAP_FILE):
        try:
            with open(STOCK_MAP_FILE, "r", encoding="utf-8") as f:
                name_to_code = json.load(f)
                # { 종목명 -> 종목코드 } → { 종목코드 -> 종목명 } 역전
                return {code: name for name, code in name_to_code.items()}
        except Exception:
            pass
    return {}


def run_momentum_screener(
    top_n: int = 20,
    weight_method: str = "inverse_volatility",
    min_trading_value: float = 5e9,
    warmup_days: int = 252,
) -> dict:
    """듀얼 모멘텀 스크리너를 실행합니다.

    모멘텀 백테스터와 동일한 파이프라인을 사용합니다:
      DataHandler -> Scorer -> Rebalancer

    현재(마지막 영업일) 시점 기준으로:
    - 전체 스코어링 결과 (score_universe)
    - Top-N 선정 (select_assets)
    - 시장 국면 판별 (BULL/BEAR)
    - 가중치 배분 (inverse_volatility / equal_weight)

    Args:
        top_n: 편입 상위 종목 수.
        weight_method: 가중치 방식 ("inverse_volatility" 또는 "equal_weight").
        min_trading_value: ADTV 필터 임계값 (기본 50억 원).
        warmup_days: 모멘텀 산출 웜업 일수 (기본 252).

    Returns:
        스크리닝 결과 딕셔너리.
    """
    t0 = time.time()

    logger.info("=" * 60)
    logger.info("  듀얼 모멘텀 스크리너 시작")
    logger.info("  Top-N: %d | 가중치: %s | ADTV: %.0f억",
                top_n, weight_method, min_trading_value / 1e8)
    logger.info("=" * 60)

    # ── 1. DataHandler 초기화 ──
    handler = MomentumDataHandler(finder=None)
    n_loaded = handler.load_from_cache()
    if n_loaded == 0:
        logger.error("캐시 데이터 없음. 먼저 일봉 데이터를 캐싱하세요.")
        return _empty_result("데이터 없음")

    handler.build_dataframes()

    # 종목명 매핑 로드
    stock_map = _load_stock_map()

    # ── 2. 기준일 결정 (마지막 영업일) ──
    all_dates = handler.get_available_dates()
    if len(all_dates) == 0:
        logger.error("영업일 데이터 없음.")
        return _empty_result("영업일 없음")

    ref_date = all_dates[-1]
    logger.info("  기준일: %s", ref_date.date())

    # 웜업 확인
    bt_window = handler.get_backtest_window(warmup_days)
    if len(bt_window) == 0:
        logger.error("웜업 기간(%d일) 부족.", warmup_days)
        return _empty_result("웜업 기간 부족")

    # ── 3. 데이터 슬라이스 (미래 참조 차단) ──
    hist_prices, hist_tv, kospi_val, kospi_sma = handler.get_data_up_to(ref_date)
    current_prices = handler.get_current_prices(ref_date)

    # ── 4. MomentumScorer -- 전체 스코어링 ──
    scorer = MomentumScorer(
        top_n=top_n,
        min_trading_value=min_trading_value,
    )
    score_df = scorer.score_universe(hist_prices, hist_tv)

    if score_df.empty:
        logger.error("유니버스 스코어링 결과 없음.")
        return _empty_result("스코어링 결과 없음")

    # Top-N 선정
    selected = scorer.select_assets(hist_prices, hist_tv)

    # ── 5. MomentumRebalancer -- 국면 판별 + 가중치 ──
    rebalancer = MomentumRebalancer(weight_method=weight_method)
    target_weights, regime = rebalancer.generate_target_weights(
        selected, hist_prices, kospi_val, kospi_sma,
    )

    # ── 6. 결과 구성 ──
    elapsed = time.time() - t0

    # 종목별 상세 결과 리스트
    screened_stocks = []
    for code in selected:
        if code not in score_df.index:
            continue
        row = score_df.loc[code]
        price = float(current_prices.get(code, 0))

        screened_stocks.append({
            "rank": int(row["rank"]) if pd.notna(row.get("rank")) else 0,
            "stk_cd": code,
            "stk_nm": stock_map.get(code, code),
            "close": round(price, 0),
            "ret_3m": round(float(row.get("ret_3m", 0)) * 100, 2),
            "ret_6m": round(float(row.get("ret_6m", 0)) * 100, 2),
            "ret_12m": round(float(row.get("ret_12m", 0)) * 100, 2),
            "score": round(float(row.get("score", 0)) * 100, 2),
            "abs_pass": bool(row.get("abs_pass", False)),
            "weight": round(float(target_weights.get(code, 0)) * 100, 4),
        })

    # 스코어 순 정렬 (이미 rank로 정렬됨)
    screened_stocks.sort(key=lambda x: x["rank"])

    # 전체 유니버스 요약 (통과/탈락)
    all_universe = []
    for code in score_df.index:
        row = score_df.loc[code]
        is_selected = code in selected
        price = float(current_prices.get(code, 0))

        reason = ""
        if not row.get("abs_pass", False):
            reason = "절대 모멘텀 미달 (12M < 0%)"
        elif not is_selected and pd.notna(row.get("rank")):
            reason = f"순위 밖 (Rank {int(row['rank'])})"

        all_universe.append({
            "stk_cd": code,
            "stk_nm": stock_map.get(code, code),
            "close": round(price, 0),
            "score": round(float(row.get("score", 0)) * 100, 2),
            "ret_12m": round(float(row.get("ret_12m", 0)) * 100, 2),
            "passed": is_selected,
            "reason": reason,
        })

    # 데이터 기간 정보
    date_start, date_end = handler.get_date_range()

    result = {
        "timestamp": datetime.now().isoformat(),
        "ref_date": str(ref_date.date()),
        "regime": regime,
        "kospi": round(float(kospi_val), 2) if not np.isnan(kospi_val) else None,
        "kospi_sma200": round(float(kospi_sma), 2) if not np.isnan(kospi_sma) else None,
        "config": {
            "top_n": top_n,
            "weight_method": weight_method,
            "min_trading_value": min_trading_value,
        },
        "summary": {
            "total_stocks": handler.get_stock_count(),
            "universe_size": len(score_df),
            "abs_momentum_pass": int(score_df["abs_pass"].sum()),
            "selected_count": len(selected),
            "data_start": date_start,
            "data_end": date_end,
        },
        "passed_stocks": screened_stocks,
        "all_universe": all_universe,
        "elapsed_sec": round(elapsed, 2),
    }

    # ── 7. JSON 저장 ──
    result_file = os.path.join(RESULT_DIR, "momentum_latest.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ── 8. 로깅 출력 ──
    logger.info("-" * 60)
    logger.info("  국면: %s | KOSPI: %s | SMA200: %s",
                regime,
                f"{kospi_val:,.2f}" if not np.isnan(kospi_val) else "N/A",
                f"{kospi_sma:,.2f}" if not np.isnan(kospi_sma) else "N/A")
    logger.info("  유니버스: %d종목 | 절대모멘텀 통과: %d | 편입: %d",
                len(score_df), score_df["abs_pass"].sum(), len(selected))
    logger.info("-" * 60)

    if regime == "BEAR":
        logger.info("  [BEAR 국면] 전액 현금화 -- 편입 종목 가중치 모두 0%%")
    else:
        for stk in screened_stocks[:20]:
            logger.info(
                "  %2d. [%s] %-12s | 종가=%8.0f | 3M=%+6.1f%% | 6M=%+6.1f%% | "
                "12M=%+6.1f%% | Score=%+6.1f%% | W=%.2f%%",
                stk["rank"], stk["stk_cd"], stk["stk_nm"],
                stk["close"],
                stk["ret_3m"], stk["ret_6m"], stk["ret_12m"],
                stk["score"], stk["weight"],
            )

    logger.info("=" * 60)
    logger.info("  스크리닝 완료 -- %.2f초 소요", elapsed)
    logger.info("  결과 저장: %s", result_file)
    logger.info("=" * 60)

    return result


def _empty_result(reason: str) -> dict:
    """빈 결과 딕셔너리를 반환합니다."""
    return {
        "timestamp": datetime.now().isoformat(),
        "ref_date": None,
        "regime": None,
        "kospi": None,
        "kospi_sma200": None,
        "config": {},
        "summary": {
            "total_stocks": 0,
            "universe_size": 0,
            "abs_momentum_pass": 0,
            "selected_count": 0,
            "error": reason,
        },
        "passed_stocks": [],
        "all_universe": [],
        "elapsed_sec": 0,
    }


# ═══════════════════════════════════════════════════════
#  CLI 진입점
# ═══════════════════════════════════════════════════════

def main():
    """커맨드라인 인터페이스 진입점."""
    parser = argparse.ArgumentParser(
        description="듀얼 모멘텀 스크리너",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python -m backend.kiwoom.strategy.momentum.momentum_screener
  python -m backend.kiwoom.strategy.momentum.momentum_screener --top-n 30
  python -m backend.kiwoom.strategy.momentum.momentum_screener --weight equal_weight
        """,
    )

    parser.add_argument(
        "--top-n", type=int, default=20, dest="top_n",
        help="모멘텀 상위 편입 종목 수 (기본: 20)",
    )
    parser.add_argument(
        "--weight", type=str, default="inverse_volatility",
        choices=["inverse_volatility", "equal_weight"],
        dest="weight_method",
        help="가중치 배분 방식 (기본: inverse_volatility)",
    )
    parser.add_argument(
        "--min-tv", type=float, default=5e9, dest="min_trading_value",
        help="ADTV 필터 임계값 (기본: 50억)",
    )
    parser.add_argument(
        "--warmup", type=int, default=252, dest="warmup_days",
        help="모멘텀 산출 웜업 기간 (기본: 252 = 12개월)",
    )

    args = parser.parse_args()

    # 로깅 설정
    log_dir = os.path.join(_project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(log_dir,
                             f"momentum_screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                encoding="utf-8",
            ),
        ],
    )

    run_momentum_screener(
        top_n=args.top_n,
        weight_method=args.weight_method,
        min_trading_value=args.min_trading_value,
        warmup_days=args.warmup_days,
    )


if __name__ == "__main__":
    main()

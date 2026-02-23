"""
global_screener.py: 글로벌 멀티에셋 스크리너 — 국내 상장 ETF 근사 포트폴리오.

글로벌 백테스터(momentum_backtester._run_global)의 투자 전략을 그대로 적용하여
현시점 기준 포트폴리오를 국내 상장 ETF로 매핑합니다.

전략 로직:
  1. GlobalDataFetcher — 13개 글로벌 ETF(SPY, AGG 등) 데이터 수집 (yfinance)
  2. MomentumScorer.score_asset_classes — 3/6/12M 모멘텀 스코어링
  3. MomentumScorer.select_global_assets — 프리셋 기반 3-Layer 배분
  4. MomentumRebalancer.detect_global_regimes — 자산별 SMA200 국면 필터
  5. MomentumRebalancer.generate_global_target_weights — BEAR→SHY 이전
  6. 국내 상장 ETF 매핑 — 글로벌 티커 → KRX 상장 ETF 근사
  7. 결과 JSON 저장 (cache/screener/global_screener_latest.json)

Usage:
    python -m backend.kiwoom.global_screener
    python -m backend.kiwoom.global_screener --preset growth
    python -m backend.kiwoom.global_screener --preset stable
    python -m backend.kiwoom.global_screener --capital 100000000
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backend.kiwoom.momentum_data_handler import MomentumDataHandler
from backend.kiwoom.momentum_scorer import MomentumScorer
from backend.kiwoom.momentum_rebalancer import MomentumRebalancer
from backend.kiwoom.momentum_asset_classes import (
    ASSET_CLASSES,
    CATEGORY_TO_TICKERS,
    CASH_TICKER,
    BENCHMARK_WEIGHTS,
    get_preset,
    get_all_presets_summary,
)

logger = logging.getLogger(__name__)

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
RESULT_DIR = os.path.join(_project_root, "cache", "screener")
os.makedirs(RESULT_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════
#  국내 상장 ETF 매핑 레지스트리
# ══════════════════════════════════════════════════════

KR_ETF_MAPPING: Dict[str, Dict[str, Any]] = {
    # ── 주식 (equity) ──
    "SPY": {
        "kr_code": "360750",
        "kr_name": "TIGER 미국S&P500",
        "category": "equity",
        "category_label": "주식",
        "description": "S&P 500 추종. 미국 대형주 500종목 분산 투자",
        "currency": "KRW",
        "hedged": False,
    },
    "IWM": {
        "kr_code": "388800",
        "kr_name": "TIGER 미국러셀2000",
        "category": "equity",
        "category_label": "주식",
        "description": "Russell 2000 소형주 추종. 미국 소형 성장주 투자",
        "currency": "KRW",
        "hedged": False,
    },
    "EFA": {
        "kr_code": "251350",
        "kr_name": "KODEX 선진국MSCI World",
        "category": "equity",
        "category_label": "주식",
        "description": "MSCI World 추종. 미국 제외 선진국 대형주 분산",
        "currency": "KRW",
        "hedged": False,
    },
    "EEM": {
        "kr_code": "195980",
        "kr_name": "TIGER 차이나CSI300",
        "category": "equity",
        "category_label": "주식",
        "description": "CSI 300 추종. 중국 대형주 300종목 (신흥국 대표)",
        "currency": "KRW",
        "hedged": False,
    },
    "EWY": {
        "kr_code": "069500",
        "kr_name": "KODEX 200",
        "category": "equity",
        "category_label": "주식",
        "description": "KOSPI 200 추종. 한국 대표 대형주 200종목",
        "currency": "KRW",
        "hedged": False,
    },
    # ── 채권 (bond) ──
    "AGG": {
        "kr_code": "453850",
        "kr_name": "ACE 미국30년국채액티브(H)",
        "category": "bond",
        "category_label": "채권",
        "description": "미국 장기 국채 중심 투자등급 채권 (환헤지)",
        "currency": "KRW",
        "hedged": True,
    },
    "IEF": {
        "kr_code": "308620",
        "kr_name": "KODEX 미국채10년선물",
        "category": "bond",
        "category_label": "채권",
        "description": "미국 10년 만기 국채 선물 추종",
        "currency": "KRW",
        "hedged": False,
    },
    "TLT": {
        "kr_code": "304660",
        "kr_name": "KODEX 미국채울트라30년선물(H)",
        "category": "bond",
        "category_label": "채권",
        "description": "미국 30년+ 장기 국채 선물 추종 (환헤지)",
        "currency": "KRW",
        "hedged": True,
    },
    "TIP": {
        "kr_code": "458730",
        "kr_name": "TIGER 미국TIPS단기채액티브",
        "category": "bond",
        "category_label": "채권",
        "description": "미국 TIPS(물가연동채) 단기 채권 투자",
        "currency": "KRW",
        "hedged": False,
    },
    # ── 실물자산 / 대체투자 ──
    "VNQ": {
        "kr_code": "352560",
        "kr_name": "TIGER 미국MSCI리츠(합성 H)",
        "category": "alternative",
        "category_label": "대체투자",
        "description": "미국 리츠(REITs) 지수 추종. 부동산 임대·배당 수익 (환헤지)",
        "currency": "KRW",
        "hedged": True,
    },
    "DBC": {
        "kr_code": "261220",
        "kr_name": "KODEX WTI원유선물(H)",
        "category": "alternative",
        "category_label": "대체투자",
        "description": "WTI 원유 선물 추종 (환헤지). 원자재 대표 ETF",
        "currency": "KRW",
        "hedged": True,
    },
    "GLD": {
        "kr_code": "411060",
        "kr_name": "ACE KRX금현물",
        "category": "alternative",
        "category_label": "대체투자",
        "description": "KRX 금시장 현물 가격 추종. 실물 금 기반 전통적 안전자산",
        "currency": "KRW",
        "hedged": False,
    },
    # ── 현금등가 ──
    "SHY": {
        "kr_code": "329750",
        "kr_name": "TIGER 미국달러단기채권액티브",
        "category": "cash",
        "category_label": "현금등가",
        "description": "미국 단기 국채 투자. 변동성 최소, 최종 안전 대피처",
        "currency": "KRW",
        "hedged": False,
    },
}


def get_kr_etf_info(global_ticker: str) -> Dict[str, Any]:
    """글로벌 티커에 대응하는 국내 ETF 정보를 반환합니다."""
    return KR_ETF_MAPPING.get(global_ticker, {
        "kr_code": "N/A",
        "kr_name": f"[미매핑] {global_ticker}",
        "category": "unknown",
        "category_label": "미분류",
        "description": "대응 국내 ETF 없음",
        "currency": "KRW",
        "hedged": False,
    })


# ══════════════════════════════════════════════════════
#  국내 ETF 현재가 조회 (yfinance)
# ══════════════════════════════════════════════════════

def fetch_kr_etf_prices(kr_codes: List[str]) -> Dict[str, float]:
    """국내 ETF 현재가를 yfinance로 조회합니다.

    Args:
        kr_codes: 국내 ETF 종목코드 리스트 (예: ["360750", "069500"]).

    Returns:
        {종목코드: 현재가(KRW)} 딕셔너리.
    """
    prices: Dict[str, float] = {}

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 미설치. 국내 ETF 가격 조회 불가.")
        return prices

    for code in kr_codes:
        yf_ticker = f"{code}.KS"
        try:
            ticker = yf.Ticker(yf_ticker)
            hist = ticker.history(period="5d")
            if not hist.empty:
                prices[code] = float(hist["Close"].iloc[-1])
            else:
                logger.warning("국내 ETF %s (%s) 가격 데이터 없음.", code, yf_ticker)
        except Exception as e:
            logger.warning("국내 ETF %s 가격 조회 실패: %s", code, e)

    return prices


# ══════════════════════════════════════════════════════
#  메인 스크리너 실행 함수
# ══════════════════════════════════════════════════════

def run_global_screener(
    preset_name: str = "balanced",
    weight_method: str = "inverse_volatility",
    initial_capital: float = 1e8,
    warmup_days: int = 252,
) -> dict:
    """글로벌 멀티에셋 스크리너를 실행합니다.

    글로벌 백테스터와 동일한 파이프라인을 사용하여 현시점 기준
    포트폴리오를 산출하고 국내 상장 ETF로 매핑합니다.

    Args:
        preset_name: 포트폴리오 프리셋 이름.
        weight_method: 가중치 방식 ("inverse_volatility" 또는 "equal_weight").
        initial_capital: 투자 예정 자본금 (원). 각 ETF 매수 금액 산출용.
        warmup_days: 모멘텀 산출 웜업 일수 (기본 252).

    Returns:
        스크리닝 결과 딕셔너리.
    """
    t0 = time.time()

    preset = get_preset(preset_name)

    logger.info("=" * 68)
    logger.info("  글로벌 멀티에셋 스크리너 (국내 ETF 근사) 시작")
    logger.info("=" * 68)
    logger.info("  프리셋: %s %s (risk %d)",
                preset["icon"], preset["label"], preset["risk_level"])
    logger.info("  투자 자본: %s원 | 가중치: %s",
                f"{initial_capital:,.0f}", weight_method)

    # ── 1. GlobalDataFetcher → 글로벌 ETF 데이터 로드 ──
    handler = MomentumDataHandler(finder=None)
    n_global = handler.load_global_data()
    if n_global == 0:
        logger.error("글로벌 ETF 캐시 데이터 없음.")
        return _empty_result("글로벌 데이터 없음")

    handler.build_global_dataframes()

    global_prices = handler.global_prices
    global_sma200 = handler.global_sma200

    if global_prices.empty or len(global_prices) < warmup_days:
        logger.error("데이터 부족 (웜업 %d일 필요, %d일 보유).",
                     warmup_days, len(global_prices))
        return _empty_result("데이터 부족")

    # 환율 조회
    from backend.kiwoom.global_data_fetcher import GlobalDataFetcher
    fetcher = GlobalDataFetcher()
    usdkrw_rate = fetcher.fetch_usdkrw_rate()
    logger.info("  USD/KRW 환율: %s", f"{usdkrw_rate:,.2f}")

    # 기준일 (마지막 영업일)
    ref_date = global_prices.index[-1]
    logger.info("  기준일: %s", ref_date.date())

    # ── 2. MomentumScorer — 자산군 스코어링 + 배분 ──
    scorer = MomentumScorer(
        top_n=20,
        min_trading_value=5e9,
        risk_free_rate=0.0,
    )

    # 2a. 전체 자산군 모멘텀 스코어
    ac_scores = scorer.score_asset_classes(global_prices)

    # 2b. 프리셋 기반 자산 배분 (3-Layer)
    asset_weights, kr_top_n = scorer.select_global_assets(
        global_prices,
        pd.DataFrame(),   # 국내 개별종목 미사용
        pd.DataFrame(),
        preset=preset,
    )

    # ── 3. MomentumRebalancer — 국면 필터 ──
    rebalancer = MomentumRebalancer(weight_method=weight_method)

    final_weights, regime_by_ticker = rebalancer.generate_global_target_weights(
        asset_weights,
        global_prices,
        global_sma200,
        kr_top_n_codes=[],  # 국내 개별종목 미사용 (ETF로 직접 매핑)
    )

    # ── 4. EWY 비중 처리 ──
    # generate_global_target_weights에서 EWY가 남아있으면 그대로 유지
    # (국내 ETF 매핑에서 EWY → KODEX 200으로 자동 변환)

    # ── 5. 국내 ETF 매핑 + 현재가 조회 ──
    # 글로벌 티커 → 국내 ETF 코드 + 비중
    kr_code_to_weight: Dict[str, float] = {}
    kr_code_to_global: Dict[str, str] = {}  # 국내 코드 → 글로벌 티커 역매핑
    unmapped_tickers: List[str] = []

    for global_ticker, weight in final_weights.items():
        if weight <= 0:
            continue
        kr_info = get_kr_etf_info(global_ticker)
        kr_code = kr_info["kr_code"]
        if kr_code == "N/A":
            unmapped_tickers.append(global_ticker)
            continue
        kr_code_to_weight[kr_code] = kr_code_to_weight.get(kr_code, 0) + weight
        kr_code_to_global[kr_code] = global_ticker

    if unmapped_tickers:
        logger.warning("미매핑 글로벌 티커: %s", unmapped_tickers)

    # 국내 ETF 현재가 조회
    kr_codes_list = list(kr_code_to_weight.keys())
    kr_prices = fetch_kr_etf_prices(kr_codes_list)

    # ── 6. 결과 구성 ──
    elapsed = time.time() - t0

    # 글로벌 ETF 스코어 상세
    global_etf_details: List[Dict[str, Any]] = []
    for global_ticker in global_prices.columns:
        kr_info = get_kr_etf_info(global_ticker)
        regime = regime_by_ticker.get(global_ticker, "N/A")
        weight_pct = final_weights.get(global_ticker, 0) * 100

        # 모멘텀 스코어
        score_row = {}
        if not ac_scores.empty and global_ticker in ac_scores.index:
            row = ac_scores.loc[global_ticker]
            score_row = {
                "ret_3m": round(float(row.get("ret_3m", 0)) * 100, 2),
                "ret_6m": round(float(row.get("ret_6m", 0)) * 100, 2),
                "ret_12m": round(float(row.get("ret_12m", 0)) * 100, 2),
                "score": round(float(row.get("score", 0)) * 100, 2),
                "abs_pass": bool(row.get("abs_pass", False)),
            }

        # 글로벌 ETF 현재가 (USD)
        global_price_usd = 0.0
        if not global_prices.empty:
            global_price_usd = float(global_prices.iloc[-1].get(global_ticker, 0))

        # 자산군 메타정보
        asset_label = ""
        for _, info in ASSET_CLASSES.items():
            if info["ticker"] == global_ticker:
                asset_label = info["label"]
                break

        global_etf_details.append({
            "global_ticker": global_ticker,
            "global_label": asset_label,
            "global_price_usd": round(global_price_usd, 2),
            "regime": regime,
            "weight_pct": round(weight_pct, 2),
            "kr_code": kr_info["kr_code"],
            "kr_name": kr_info["kr_name"],
            "kr_category": kr_info["category_label"],
            "kr_hedged": kr_info.get("hedged", False),
            "kr_description": kr_info["description"],
            **score_row,
        })

    # 비중 기준 내림차순 정렬
    global_etf_details.sort(key=lambda x: -x["weight_pct"])

    # 국내 ETF 포트폴리오 (실제 매수용)
    kr_portfolio: List[Dict[str, Any]] = []
    total_alloc = 0.0

    for kr_code, weight in sorted(kr_code_to_weight.items(), key=lambda x: -x[1]):
        kr_info = get_kr_etf_info(kr_code_to_global.get(kr_code, ""))
        kr_price = kr_prices.get(kr_code, 0)
        alloc_krw = initial_capital * weight
        shares = int(alloc_krw / kr_price) if kr_price > 0 else 0
        actual_alloc = shares * kr_price

        kr_portfolio.append({
            "kr_code": kr_code,
            "kr_name": kr_info["kr_name"],
            "global_ticker": kr_code_to_global.get(kr_code, ""),
            "category": kr_info["category_label"],
            "hedged": kr_info.get("hedged", False),
            "weight_pct": round(weight * 100, 2),
            "alloc_krw": round(alloc_krw, 0),
            "kr_price": round(kr_price, 0),
            "shares": shares,
            "actual_alloc": round(actual_alloc, 0),
            "description": kr_info["description"],
        })
        total_alloc += actual_alloc

    # 국면 요약
    n_bull = sum(1 for v in regime_by_ticker.values() if v == "BULL")
    n_bear = sum(1 for v in regime_by_ticker.values() if v == "BEAR")

    # 벤치마크 60/40 국내 ETF
    benchmark_kr = []
    for bm_ticker, bm_weight in BENCHMARK_WEIGHTS.items():
        bm_kr_info = get_kr_etf_info(bm_ticker)
        bm_kr_price = kr_prices.get(bm_kr_info["kr_code"], 0)
        bm_alloc = initial_capital * bm_weight
        bm_shares = int(bm_alloc / bm_kr_price) if bm_kr_price > 0 else 0
        benchmark_kr.append({
            "kr_code": bm_kr_info["kr_code"],
            "kr_name": bm_kr_info["kr_name"],
            "global_ticker": bm_ticker,
            "weight_pct": round(bm_weight * 100, 1),
            "alloc_krw": round(bm_alloc, 0),
            "kr_price": round(bm_kr_price, 0),
            "shares": bm_shares,
        })

    # 전략적 배분 (프리셋 원본 vs 최종)
    strategic_weights = preset["weights"]
    strategic_summary = {
        cat: f"{w:.0%}"
        for cat, w in strategic_weights.items()
    }

    # 카테고리별 실제 배분 집계
    category_actual: Dict[str, float] = {}
    for global_ticker, weight in final_weights.items():
        if weight <= 0:
            continue
        kr_info = get_kr_etf_info(global_ticker)
        cat = kr_info.get("category", "unknown")
        category_actual[cat] = category_actual.get(cat, 0) + weight

    result = {
        "timestamp": datetime.now().isoformat(),
        "ref_date": str(ref_date.date()),
        "preset": {
            "key": preset_name,
            "label": preset["label"],
            "icon": preset["icon"],
            "risk_level": preset["risk_level"],
            "desc": preset.get("desc", ""),
        },
        "config": {
            "weight_method": weight_method,
            "initial_capital": initial_capital,
            "warmup_days": warmup_days,
        },
        "usdkrw_rate": round(usdkrw_rate, 2),
        "regime_summary": {
            "n_bull": n_bull,
            "n_bear": n_bear,
            "total": len(regime_by_ticker),
            "regimes": {k: v for k, v in regime_by_ticker.items()},
        },
        "strategic_weights": strategic_summary,
        "category_actual": {
            k: round(v * 100, 2) for k, v in category_actual.items()
        },
        "global_etf_details": global_etf_details,
        "kr_portfolio": kr_portfolio,
        "benchmark_kr": benchmark_kr,
        "summary": {
            "total_etfs": len(kr_portfolio),
            "invested_etfs": sum(1 for p in kr_portfolio if p["weight_pct"] > 0),
            "total_alloc_krw": round(total_alloc, 0),
            "remaining_cash": round(initial_capital - total_alloc, 0),
            "utilization_pct": round(total_alloc / initial_capital * 100, 2) if initial_capital > 0 else 0,
            "data_start": global_prices.index[0].strftime("%Y-%m-%d"),
            "data_end": ref_date.strftime("%Y-%m-%d"),
        },
        "elapsed_sec": round(elapsed, 2),
    }

    # ── 7. JSON 저장 ──
    result_file = os.path.join(RESULT_DIR, "global_screener_latest.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ── 8. 로깅 출력 ──
    logger.info("-" * 68)
    logger.info("  프리셋: %s %s (risk %d)", preset["icon"], preset["label"], preset["risk_level"])
    logger.info("  전략적 배분: %s", strategic_summary)
    logger.info("  국면: BULL %d / BEAR %d (총 %d 티커)", n_bull, n_bear, len(regime_by_ticker))
    logger.info("-" * 68)

    for etf in kr_portfolio:
        if etf["weight_pct"] > 0:
            regime_str = regime_by_ticker.get(etf["global_ticker"], "?")
            logger.info(
                "  [%s] %-30s | %s → %6s | W=%5.1f%% | %s원 × %d주 = %s원",
                regime_str,
                etf["kr_name"],
                etf["global_ticker"],
                etf["kr_code"],
                etf["weight_pct"],
                f"{etf['kr_price']:,.0f}" if etf["kr_price"] > 0 else "N/A",
                etf["shares"],
                f"{etf['actual_alloc']:,.0f}" if etf["actual_alloc"] > 0 else "N/A",
            )

    logger.info("-" * 68)
    logger.info("  투자 배분: %s원 / %s원 (활용률 %.1f%%)",
                f"{total_alloc:,.0f}",
                f"{initial_capital:,.0f}",
                total_alloc / initial_capital * 100 if initial_capital > 0 else 0)
    logger.info("  잔여 현금: %s원", f"{initial_capital - total_alloc:,.0f}")
    logger.info("=" * 68)
    logger.info("  글로벌 스크리닝 완료 — %.2f초 소요", elapsed)
    logger.info("  결과 저장: %s", result_file)
    logger.info("=" * 68)

    return result


def _empty_result(reason: str) -> dict:
    """빈 결과 딕셔너리를 반환합니다."""
    return {
        "timestamp": datetime.now().isoformat(),
        "ref_date": None,
        "preset": None,
        "config": {},
        "usdkrw_rate": None,
        "regime_summary": {"n_bull": 0, "n_bear": 0, "total": 0, "regimes": {}},
        "strategic_weights": {},
        "category_actual": {},
        "global_etf_details": [],
        "kr_portfolio": [],
        "benchmark_kr": [],
        "summary": {
            "total_etfs": 0,
            "invested_etfs": 0,
            "total_alloc_krw": 0,
            "remaining_cash": 0,
            "utilization_pct": 0,
            "error": reason,
        },
        "elapsed_sec": 0,
    }


# ══════════════════════════════════════════════════════
#  CLI 진입점
# ══════════════════════════════════════════════════════

def main():
    """커맨드라인 인터페이스 진입점."""
    parser = argparse.ArgumentParser(
        description="글로벌 멀티에셋 스크리너 (국내 ETF 근사 포트폴리오)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python -m backend.kiwoom.global_screener
  python -m backend.kiwoom.global_screener --preset growth
  python -m backend.kiwoom.global_screener --preset stable --capital 200000000
  python -m backend.kiwoom.global_screener --preset balanced --weight equal_weight

프리셋 목록:
  growth           🚀 성장형         (risk 5) — 주식 55%%, 대체 25%%
  growth_seeking   📈 성장추구형     (risk 4) — 주식 50%%, 해외채권 20%%
  balanced         ⚖️  위험중립형     (risk 3) — 주식 35%%, 해외채권 30%%
  stability_seeking🛡️ 안정추구형     (risk 2) — 채권 60%%, 주식 20%%
  stable           🏦 안정형         (risk 1) — 채권 75%%, 주식 10%%
        """,
    )

    parser.add_argument(
        "--preset", type=str, default="balanced",
        choices=["growth", "growth_seeking", "balanced", "stability_seeking", "stable"],
        help="글로벌 포트폴리오 프리셋 (기본: balanced)",
    )
    parser.add_argument(
        "--weight", type=str, default="inverse_volatility",
        choices=["inverse_volatility", "equal_weight"],
        dest="weight_method",
        help="가중치 배분 방식 (기본: inverse_volatility)",
    )
    parser.add_argument(
        "--capital", type=float, default=1e8,
        help="투자 예정 자본금 (원, 기본: 100,000,000)",
    )
    parser.add_argument(
        "--warmup", type=int, default=252, dest="warmup_days",
        help="모멘텀 산출 웜업 기간 (기본: 252 = 12개월)",
    )

    args = parser.parse_args()

    # stdout UTF-8 강제 (Windows cp949 대응)
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
                             f"global_screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                encoding="utf-8",
            ),
        ],
    )

    result = run_global_screener(
        preset_name=args.preset,
        weight_method=args.weight_method,
        initial_capital=args.capital,
        warmup_days=args.warmup_days,
    )

    # ── 요약 출력 ──
    print()
    if result.get("summary", {}).get("error"):
        print(f"  오류: {result['summary']['error']}")
        return

    p = result.get("preset", {})
    print(f"  {p.get('icon', '')} {p.get('label', '')} 포트폴리오 (risk {p.get('risk_level', '?')})")
    print(f"  기준일: {result.get('ref_date', '?')} | USD/KRW: {result.get('usdkrw_rate', '?')}")
    print()

    rs = result.get("regime_summary", {})
    print(f"  국면: BULL {rs.get('n_bull', 0)} / BEAR {rs.get('n_bear', 0)}")
    print()

    print(f"  {'국내 ETF':30s} {'글로벌':6s} {'국면':6s} {'비중':>6s} {'단가':>10s} {'수량':>6s} {'투자금':>14s}")
    print("  " + "-" * 88)

    for etf in result.get("kr_portfolio", []):
        if etf["weight_pct"] > 0:
            reg = rs.get("regimes", {}).get(etf["global_ticker"], "?")
            print(
                f"  {etf['kr_name']:30s} {etf['global_ticker']:6s} {reg:6s} "
                f"{etf['weight_pct']:5.1f}% {etf['kr_price']:>10,.0f} "
                f"{etf['shares']:>5d}주 {etf['actual_alloc']:>13,.0f}원"
            )

    summary = result.get("summary", {})
    print()
    print(f"  총 투자: {summary.get('total_alloc_krw', 0):,.0f}원 / {args.capital:,.0f}원 "
          f"(활용률 {summary.get('utilization_pct', 0):.1f}%)")
    print(f"  잔여 현금: {summary.get('remaining_cash', 0):,.0f}원")

    # 벤치마크 비교
    benchmark = result.get("benchmark_kr", [])
    if benchmark:
        print()
        print("  [벤치마크 60/40]")
        for bm in benchmark:
            print(f"  {bm['kr_name']:30s} {bm['global_ticker']:6s} "
                  f"{bm['weight_pct']:5.1f}% {bm['kr_price']:>10,.0f} "
                  f"{bm['shares']:>5d}주")


if __name__ == "__main__":
    main()

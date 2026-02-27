"""
alpha_screener.py: 알파 필터 스크리너 독립 실행 스크립트.

상위 테마에서 종목을 수집하고, 4단계 알파 필터를 적용하여
통과 종목을 JSON 파일로 저장합니다.

Usage:
    python -m backend.kiwoom.strategy.phoenix.alpha_screener [--top_n 30]
"""

import os
import sys
import json
import time
import logging
from datetime import datetime

from backend.kiwoom.strategy.phoenix.theme_finder import TopThemeFinder
from backend.kiwoom.strategy.phoenix.alpha_filter import AlphaFilter, compute_all_indicators
from backend.kiwoom.strategy.pullback.pullback_alpha_filter import PullbackAlphaFilter, compute_pullback_indicators
from backend.kiwoom.strategy.phoenix.sell_strategy import _parse_price, compute_atr

logger = logging.getLogger(__name__)

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_project_root, "cache", "daily_charts")
RESULT_DIR = os.path.join(_project_root, "cache", "screener")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

API_DELAY = 0.35


def _build_volume_universe(finder: TopThemeFinder, top_n: int = 100) -> list[dict]:
    """ka10030 API로 당일 거래량 상위 N개 종목을 조회합니다.

    백테스터의 거래량 기반 유니버스와 동일한 소스를 사용하여
    백테스트-라이브 간 일관성을 보장합니다.
    """
    logger.info("[1/3] 거래량 상위 %d 종목 수집 중 (ka10030)...", top_n)

    token = finder._get_token()
    url = f"{finder.domain}/api/dostk/rkinfo"
    headers = {
        "api-id": "ka10030",
        "authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
    }
    payload = {
        "mrkt_tp": "000",
        "sort_tp": "1",
        "mang_stk_incls": "1",
        "crd_tp": "0",
        "trde_qty_tp": "0",
        "pric_tp": "0",
        "trde_prica_tp": "0",
        "mrkt_open_tp": "0",
        "stex_tp": "1",
    }

    universe: list[dict] = []
    seen: set[str] = set()
    cont_yn = ""
    next_key = ""

    for _ in range(10):
        if cont_yn == "Y":
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        resp = finder._request_with_retry(url, headers, payload)
        data = resp.json()
        items = data.get("tdy_trde_qty_upper", [])

        for item in items:
            cd = item.get("stk_cd", "")
            nm = item.get("stk_nm", "")
            if cd and cd not in seen:
                seen.add(cd)
                universe.append({"stk_cd": cd, "stk_nm": nm})

        if len(universe) >= top_n:
            break

        cont_yn = resp.headers.get("cont-yn", "N")
        next_key = resp.headers.get("next-key", "")
        if cont_yn != "Y" or not items:
            break
        time.sleep(API_DELAY)

    universe = universe[:top_n]
    logger.info("  거래량 상위 %d 종목 수집 완료.", len(universe))
    return universe


def run_screener(top_n: int = 30, strategy: str = "swing") -> dict:
    """알파 필터 또는 스윙-풀백 스크리너를 실행합니다.

    Args:
        top_n: swing=상위 테마 수, pullback=거래량 Top-N 종목 수.
        strategy: "swing" 또는 "pullback"

    Returns:
        스크리닝 결과 딕셔너리.
    """
    finder = TopThemeFinder()
    if strategy == "pullback":
        alpha_filter = PullbackAlphaFilter()
    else:
        alpha_filter = AlphaFilter()

    logger.info("=" * 60)
    logger.info("  %s 스크리너 시작", "풀백(Pullback)" if strategy == "pullback" else "알파(Swing)")
    if strategy == "pullback":
        logger.info("  거래량 상위 %d 종목 유니버스", top_n)
    else:
        logger.info("  상위 %d개 테마 조회", top_n)
    logger.info("=" * 60)

    # 1. 유니버스 구축 (전략별 분기)
    themes: list[dict] = []
    all_candidates: list[dict] = []
    seen_codes: set[str] = set()
    theme_map: dict[str, str] = {}

    if strategy == "pullback":
        # 거래량 기반 유니버스 (백테스터와 동일한 소스)
        all_candidates = _build_volume_universe(finder, top_n)
    else:
        # 테마 기반 유니버스
        logger.info("[1/3] 상위 테마 종목 수집 중...")
        themes = finder.get_top_themes(days_ago=1, top_n=top_n)

        for theme in themes:
            cd = theme.get("thema_grp_cd")
            nm = theme.get("thema_nm", "?")
            if not cd:
                continue
            time.sleep(API_DELAY)
            stocks = finder.get_theme_stocks(cd, days_ago=1)
            for stk in stocks:
                stk_cd = stk.get("stk_cd", "")
                if stk_cd and stk_cd not in seen_codes:
                    seen_codes.add(stk_cd)
                    stk["theme_nm"] = nm
                    stk["thema_grp_cd"] = cd
                    all_candidates.append(stk)
                    theme_map[stk_cd] = nm

        logger.info("  수집 완료: %d개 테마, %d개 종목", len(themes), len(all_candidates))

    # 2. 일봉 데이터 수집
    logger.info("[2/3] 일봉 데이터 수집 중 (%d개 종목)...", len(all_candidates))
    daily_bars_map: dict[str, list[dict]] = {}
    today_str = datetime.now().strftime("%Y%m%d")

    for i, stk in enumerate(all_candidates):
        stk_cd = stk.get("stk_cd", "")
        if not stk_cd:
            continue

        cache_file = os.path.join(CACHE_DIR, f"{stk_cd}.json")
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                bars = json.load(f)
        else:
            time.sleep(API_DELAY)
            bars = finder.get_daily_chart(stk_cd, today_str)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(bars, f, ensure_ascii=False)

        if len(bars) >= 21:
            daily_bars_map[stk_cd] = bars

        if (i + 1) % 50 == 0:
            logger.info("  일봉 수집 진행: %d/%d", i + 1, len(all_candidates))

    logger.info("  일봉 수집 완료: %d개 종목 (21일 이상 데이터 보유)", len(daily_bars_map))

    # 3. 알파 필터 적용
    logger.info("[3/3] 4단계 알파 필터 적용 중...")
    passed_stocks = alpha_filter.screen_universe(all_candidates, daily_bars_map)

    # 결과 포맷팅
    screened_results: list[dict] = []
    
    if strategy == "pullback":
        for stk in passed_stocks:
            indicators = stk.get("pullback_indicators", {})
            stk_cd = stk.get("stk_cd", "")
            bars = daily_bars_map.get(stk_cd, [])
            atr = compute_atr(bars, 14)

            # pullback 지표 매핑을 기존 포맷과 유사하게 맞춰 UI 호환성을 유지합니다
            # vcr은 당일 거래량에 대한 지표이지만, 임시로 남는 필드에 표시
            screened_results.append({
                "stk_cd": stk_cd,
                "stk_nm": stk.get("stk_nm", "?"),
                "theme_nm": stk.get("theme_nm", ""),
                "close": float(bars[-1].get("cur_prc", 0)) if bars else 0,
                "daily_return": round(indicators.get("surge_return", 0) or 0, 2),
                "sma10": 0,
                "ema20": 0,
                "sma20": round(indicators.get("vcr", 0) or 0, 2),
                "disparity20": round(indicators.get("disparity_5", 0) or 0, 2),
                "adtv20": round((indicators.get("adtv20", 0) or 0) / 1e8, 1),
                "rvol": round(indicators.get("surge_rvol", 0) or 0, 2),
                "atr5": round(atr or 0, 0),
                "market_cap": round(indicators.get("frl", 0) or 0, 3),
                # 풀백 전용 명시 필드
                "vcr": round(indicators.get("vcr", 0) or 0, 2),
                "frl": round(indicators.get("frl", 0) or 0, 3),
                "surge_return": round(indicators.get("surge_return", 0) or 0, 2),
                "surge_rvol": round(indicators.get("surge_rvol", 0) or 0, 2),
                "disparity_5": round(indicators.get("disparity_5", 0) or 0, 2),
            })
    else:
        for stk in passed_stocks:
            indicators = stk.get("indicators", {})
            stk_cd = stk.get("stk_cd", "")
            bars = daily_bars_map.get(stk_cd, [])
            atr = compute_atr(bars, 5)

            screened_results.append({
                "stk_cd": stk_cd,
                "stk_nm": stk.get("stk_nm", "?"),
                "theme_nm": stk.get("theme_nm", ""),
                "close": indicators.get("close", 0),
                "daily_return": round(indicators.get("daily_return", 0) or 0, 2),
                "sma10": round(indicators.get("sma10", 0) or 0, 0),
                "ema20": round(indicators.get("ema20", 0) or 0, 0),
                "sma20": round(indicators.get("sma20", 0) or 0, 0),
                "disparity20": round(indicators.get("disparity20", 0) or 0, 2),
                "adtv20": round((indicators.get("adtv20", 0) or 0) / 1e8, 1),
                "rvol": round(indicators.get("rvol", 0) or 0, 2),
                "atr5": round(atr or 0, 0),
                "market_cap": round((indicators.get("market_cap", 0) or 0) / 1e8, 0),
            })

    # 수익률(또는 surge_return) 기준 내림차순 정렬
    screened_results.sort(key=lambda x: x.get("daily_return", 0), reverse=True)

    # 모든 후보의 필터 결과 (탈락 포함)
    all_filter_results: list[dict] = []
    for stk in all_candidates:
        stk_cd = stk.get("stk_cd", "")
        bars = daily_bars_map.get(stk_cd, [])
        if len(bars) < 21:
            all_filter_results.append({
                "stk_cd": stk_cd,
                "stk_nm": stk.get("stk_nm", "?"),
                "theme_nm": stk.get("theme_nm", ""),
                "passed": False,
                "reason": "일봉 데이터 부족",
            })
            continue

        if strategy == "pullback":
            indicators = compute_pullback_indicators(bars)
            passed, reasons = alpha_filter.apply_all_filters(indicators)
            all_filter_results.append({
                "stk_cd": stk_cd,
                "stk_nm": stk.get("stk_nm", "?"),
                "theme_nm": stk.get("theme_nm", ""),
                "passed": passed,
                "reason": reasons[-1] if reasons else "",
                "close": float(bars[-1].get("cur_prc", 0)) if bars else 0,
                "daily_return": round(indicators.get("surge_return", 0) or 0, 2),
            })
        else:
            indicators = compute_all_indicators(bars)
            passed, reasons = alpha_filter.apply_all_filters(indicators)
            all_filter_results.append({
                "stk_cd": stk_cd,
                "stk_nm": stk.get("stk_nm", "?"),
                "theme_nm": stk.get("theme_nm", ""),
                "passed": passed,
                "reason": reasons[-1] if reasons else "",
                "close": indicators.get("close", 0),
                "daily_return": round(indicators.get("daily_return", 0) or 0, 2),
            })

    result = {
        "timestamp": datetime.now().isoformat(),
        "total_themes": len(themes),
        "total_candidates": len(all_candidates),
        "total_passed": len(screened_results),
        "passed_stocks": screened_results,
        "all_filter_results": all_filter_results,
    }

    # JSON 파일 저장
    result_file = os.path.join(RESULT_DIR, "latest.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info("  스크리닝 완료: %d/%d 종목 통과", len(screened_results), len(all_candidates))
    logger.info("  결과 저장: %s", result_file)
    logger.info("=" * 60)

    # 통과 종목 요약 출력
    for i, stk in enumerate(screened_results, 1):
        logger.info(
            "  %2d. [%s] %-12s | 종가=%6.0f | 수익률=%+5.1f%% | RVOL=%.1f | 이격도=%.1f | ATR=%5.0f | 테마=%s",
            i, stk["stk_cd"], stk["stk_nm"],
            stk["close"], stk["daily_return"],
            stk["rvol"], stk["disparity20"],
            stk["atr5"], stk["theme_nm"],
        )

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="알파 필터 스크리너")
    parser.add_argument("--top_n", type=int, default=30, help="상위 테마 수 (기본: 30)")
    parser.add_argument("--strategy", type=str, default="swing", choices=["swing", "pullback"], help="스크리닝 전략 (swing 또는 pullback)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(_project_root, "logs",
                             f"screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                encoding="utf-8",
            ),
        ],
    )

    run_screener(top_n=args.top_n, strategy=args.strategy)

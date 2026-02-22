"""
ThemeBacktester: 테마 기반 백테스팅 엔진.

99일전부터 현재까지 매일:
  1. N+1일전 1등 테마 구성종목 조회
  2. 장마감 10분전(15:20) 매수
  3. 다음 영업일 매도 전략 실행
  4. 누적 수익률 계산
"""

import os
import json
import time
import sys
import logging
from datetime import datetime, timedelta

from backend.kiwoom.theme_finder import TopThemeFinder
from backend.kiwoom.sell_strategy import SellStrategyEngine, _parse_price

logger = logging.getLogger(__name__)

# 분봉 캐시 디렉토리
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_project_root, "cache", "minute_charts")
DAILY_CACHE_DIR = os.path.join(_project_root, "cache", "daily_charts")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)

# API 호출 간 딜레이 (초) — 레이트 리밋 방지
API_DELAY = 0.35


class ThemeBacktester:
    """테마 기반 백테스팅을 실행합니다."""

    def __init__(self, initial_capital: float = 10_000_000):
        self.finder = TopThemeFinder()
        self.sell_engine = SellStrategyEngine()
        self.initial_capital = initial_capital
        self._trading_days_cache: list[str] = []  # YYYYMMDD 형식
        self._universe_themes: list[dict] = []
        self._universe_stocks: dict[str, list[dict]] = {} # {theme_cd: [stocks]}
        self._daily_bars_cache: dict[str, list[dict]] = {} # {stk_cd: [bars]}

    # ── 개장일 판별 ────────────────────────────────────────

    def _load_trading_days(self):
        """일봉 데이터로 실제 개장일 목록을 구축합니다.
        코스피 대표종목(삼성전자 005930)의 최근 일봉을 조회하여 날짜 추출.
        """
        if self._trading_days_cache:
            return

        logger.info("개장일 목록 구축 중 (삼성전자 일봉 조회)...")
        token = self.finder._get_token()
        url = f"{self.finder.domain}/api/dostk/chart"
        headers = {
            "api-id": "ka10081",
            "authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
        }
        payload = {
            "stk_cd": "005930",
            "base_dt": datetime.now().strftime("%Y%m%d"),
            "upd_stkpc_tp": "1",
        }

        all_dates = []
        cont_yn = ""
        next_key = ""

        for _ in range(5):  # 최대 5회 연속조회 (~100일)
            if cont_yn == "Y":
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            resp = __import__('requests').post(
                url, headers=headers, json=payload,
                verify=__import__('certifi').where(), timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            chart = data.get("stk_dt_pole_chart_qry", [])
            for item in chart:
                dt = item.get("dt", "")
                if dt and len(dt) == 8:
                    all_dates.append(dt)

            if len(all_dates) >= 120:
                break

            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = resp.headers.get("next-key", "")
            if cont_yn != "Y":
                break

            time.sleep(API_DELAY)

        # 날짜 오름차순 정렬
        self._trading_days_cache = sorted(set(all_dates))
        logger.info("개장일 %d일 로드 완료 (%s ~ %s)",
                     len(self._trading_days_cache),
                     self._trading_days_cache[0] if self._trading_days_cache else "?",
                     self._trading_days_cache[-1] if self._trading_days_cache else "?")

    def _is_trading_day(self, dt_str: str) -> bool:
        """YYYYMMDD가 개장일인지 확인."""
        return dt_str in self._trading_days_cache

    def _get_trading_day_n_ago(self, n: int) -> str:
        """현재 기준 n 영업일 전 날짜 반환."""
        if not self._trading_days_cache:
            return ""
        idx = len(self._trading_days_cache) - n
        if idx < 0:
            return ""
        return self._trading_days_cache[idx]

    def _next_trading_day(self, dt_str: str) -> str:
        """dt_str 다음 영업일 반환."""
        try:
            idx = self._trading_days_cache.index(dt_str)
            if idx + 1 < len(self._trading_days_cache):
                return self._trading_days_cache[idx + 1]
        except ValueError:
            pass
        return ""

    # ── 분봉 캐시 ──────────────────────────────────────────

    def _get_minute_chart_cached(self, stk_cd: str, base_dt: str) -> list[dict]:
        """분봉 데이터를 캐시에서 가져오거나, 없으면 API 호출 후 캐싱."""
        cache_file = os.path.join(CACHE_DIR, f"{stk_cd}_{base_dt}.json")

        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

        logger.info("분봉 캐시 미스: %s/%s → API 호출", stk_cd, base_dt)
        time.sleep(API_DELAY)
        bars = self.finder.get_minute_chart(stk_cd, base_dt)

        # 캐시 저장
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(bars, f, ensure_ascii=False)

        return bars

    def _get_daily_chart_cached(self, stk_cd: str) -> list[dict]:
        """일봉 데이터를 캐시에서 가져오거나, 없으면 API 호출 후 캐싱."""
        if stk_cd in self._daily_bars_cache:
            return self._daily_bars_cache[stk_cd]

        cache_file = os.path.join(DAILY_CACHE_DIR, f"{stk_cd}.json")
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                bars = json.load(f)
                self._daily_bars_cache[stk_cd] = bars
                return bars

        logger.info("일봉 캐시 미스: %s → API 호출", stk_cd)
        time.sleep(API_DELAY)
        bars = self.finder.get_daily_chart(stk_cd, datetime.now().strftime("%Y%m%d"))

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(bars, f, ensure_ascii=False)

        self._daily_bars_cache[stk_cd] = bars
        return bars

    # ── 유니버스 구축 및 과거 순위 계산 ────────────────────

    def _build_history_universe(self):
        """현재 기준 상위 테마/종목 유니버스를 구축하고 일봉 데이터를 미리 수집합니다."""
        logger.info("백테스트 유니버스 구축 중 (상위 100개 테마)...")
        self._universe_themes = self.finder.get_top_themes(days_ago=1, top_n=100)

        for theme in self._universe_themes:
            cd = theme.get("thema_grp_cd")
            if not cd: continue
            time.sleep(API_DELAY)
            stocks = self.finder.get_theme_stocks(cd, days_ago=1)
            self._universe_stocks[cd] = stocks

            # 각 종목 일봉 캐싱
            for stk in stocks:
                stk_cd = stk.get("stk_cd")
                if stk_cd:
                    self._get_daily_chart_cached(stk_cd)

    def _get_historical_top_theme(self, target_date: str) -> dict:
        """특정 날짜(target_date) 기준의 1등 테마와 대장주를 일봉 데이터로 계산합니다."""
        results = []

        for theme in self._universe_themes:
            theme_cd = theme.get("thema_grp_cd")
            theme_nm = theme.get("thema_nm")
            stocks = self._universe_stocks.get(theme_cd, [])

            stock_returns = []
            for stk in stocks:
                stk_cd = stk.get("stk_cd")
                stk_nm = stk.get("stk_nm")
                bars = self._get_daily_chart_cached(stk_cd)

                # target_date의 성과 찾기
                # target_date 당일 등락률을 계산 (target_date 종가 vs 전일 종가)
                # 미래 참조 방지를 위해 target_date 이전 데이터만 사용
                target_idx = -1
                for i, bar in enumerate(bars):
                    if bar.get("dt") == target_date:
                        target_idx = i
                        break

                if target_idx > 0:
                    prev_close = _parse_price(bars[target_idx-1].get("cur_prc", "0"))
                    curr_close = _parse_price(bars[target_idx].get("cur_prc", "0"))
                    if prev_close > 0:
                        ret = (curr_close - prev_close) / prev_close * 100
                        stock_returns.append({
                            "stk_cd": stk_cd,
                            "stk_nm": stk_nm,
                            "ret": ret
                        })

            if stock_returns:
                # 테마 내 평균 수익률 계산
                avg_ret = sum(s["ret"] for s in stock_returns) / len(stock_returns)
                # 대장주 선정 (수익률 1위)
                stock_returns.sort(key=lambda x: x["ret"], reverse=True)
                results.append({
                    "theme": theme,
                    "avg_ret": avg_ret,
                    "top_stock": stock_returns[0]
                })

        if not results:
            return {}

        # 1등 테마 선정
        results.sort(key=lambda x: x["avg_ret"], reverse=True)
        top = results[0]

        return {
            "theme": top["theme"],
            "stocks": [top["top_stock"]]  # 대장주 1개만 반환
        }

    # ── 메인 백테스팅 루프 ─────────────────────────────────

    def run(self, start_days_ago: int = 99) -> dict:
        """백테스팅을 실행합니다.

        Args:
            start_days_ago: 시작일 (N 영업일 전, 최대 99)

        Returns:
            {
                'initial_capital': float,
                'final_capital': float,
                'total_return': float,  # 누적 수익률 (%)
                'trades': [{day, date, theme, stocks, buy_price, sell_price, return_rate, ...}],
                'summary': str,
            }
        """
        self._load_trading_days()

        cumulative_return = 1.0  # 복리 누적
        trades = []

        logger.info("=" * 70)
        logger.info("  백테스팅 시작: %d 영업일 전 → 현재", start_days_ago)
        logger.info("  초기 자본금: %s원", f"{self.initial_capital:,.0f}")
        logger.info("=" * 70)

        # 헤더 출력
        header = (
            f"{'Day':>4} | {'BuyDate':<10} | {'SellDate':<10} | "
            f"{'Theme':<20} | {'Stock':<10} | "
            f"{'BuyPrc':>10} | {'SellPrc':>10} | {'Return':>8} | {'Cumul':>8}"
        )
        logger.info(header)
        logger.info("-" * len(header))

        for day_offset in range(start_days_ago, 1, -1):
            buy_date = self._get_trading_day_n_ago(day_offset)
            if not buy_date:
                logger.info("Day -%d: 매수일 특정 불가, 건너뜀", day_offset)
                continue

            # 1단계: 과거 시점(buy_date) 기준 1등 테마 및 대장주 계산 (미래 참조 차단)
            try:
                if not self._universe_themes:
                    self._build_history_universe()
                
                theme_result = self._get_historical_top_theme(buy_date)
            except Exception as e:
                logger.warning("Day -%d: 테마 계산 실패 (%s), 건너뜀", day_offset, e)
                continue

            theme = theme_result.get("theme", {})
            stocks = theme_result.get("stocks", [])

            if not theme or not stocks:
                logger.info("Day -%d: 테마/종목 없음, 건너뜀", day_offset)
                continue

            theme_name = theme.get("thema_nm", "?")

            # 2단계: 매수일/매도일 특정
            sell_date = self._next_trading_day(buy_date)
            if not sell_date:
                logger.info("Day -%d: 매도일 특정 불가, 건너뜀", day_offset)
                continue

            # 3단계: 1위 종목 선정 결과 로그
            stk_top = stocks[0]
            logger.info("Day -%d: 테마 [%s] 내 대장주 [%s] 선정 (과거시점 수익률: %.2f%%)",
                         day_offset, theme_name, stk_top.get("stk_nm"), stk_top.get("ret", 0))

            # 4단계: 각 종목 처리 (현재 대장주 1개)
            day_returns = []
            for stk in stocks:
                stk_cd = stk.get("stk_cd", "")
                stk_nm = stk.get("stk_nm", "?")

                if not stk_cd:
                    continue

                try:
                    # 매수일 분봉 → 15:20 종가 = 매수가
                    buy_bars = self._get_minute_chart_cached(stk_cd, buy_date)
                    buy_bar = None
                    for bar in reversed(buy_bars):
                        cntr_tm = bar.get("cntr_tm", "")
                        if len(cntr_tm) >= 12:
                            hhmm = cntr_tm[8:12]
                            if hhmm <= "1520":
                                buy_bar = bar
                                break

                    if not buy_bar:
                        # 15:20 분봉이 없으면 마지막 분봉 사용
                        buy_bar = buy_bars[-1] if buy_bars else None

                    if not buy_bar:
                        logger.info("Day -%d [%s]: 매수일 분봉 없음, 건너뜀", day_offset, stk_nm)
                        continue

                    buy_price = _parse_price(buy_bar.get("cur_prc", "0"))
                    if buy_price <= 0:
                        continue

                    # 상한가 조회 (ka10007)
                    time.sleep(API_DELAY)
                    stock_info = self.finder.get_stock_info(stk_cd)
                    upper_limit_price = _parse_price(stock_info.get("upl_pric", "0"))

                    # 매도일 분봉
                    sell_bars = self._get_minute_chart_cached(stk_cd, sell_date)

                    # 매도 전략 실행
                    result = self.sell_engine.execute(sell_bars, buy_price, upper_limit_price)

                    day_returns.append(result["return_rate"] / 100)

                    trade_record = {
                        "day_offset": day_offset,
                        "buy_date": buy_date,
                        "sell_date": sell_date,
                        "theme": theme_name,
                        "stk_cd": stk_cd,
                        "stk_nm": stk_nm,
                        "buy_price": buy_price,
                        "sell_price": result["sell_price"],
                        "return_rate": result["return_rate"],
                        "sell_reason": result["sell_reason"],
                        "hit_upper_limit": result["hit_upper_limit"],
                    }
                    trades.append(trade_record)

                except Exception as e:
                    logger.warning("Day -%d [%s]: 처리 실패 (%s)", day_offset, stk_nm, e)
                    continue

            # 해당 일의 평균 수익률로 누적 수익률 갱신
            if day_returns:
                avg_daily_return = sum(day_returns) / len(day_returns)
                cumulative_return *= (1 + avg_daily_return)
                cumul_pct = (cumulative_return - 1) * 100

                logger.info(
                    f"{day_offset:>4} | {buy_date:<10} | {sell_date:<10} | "
                    f"{theme_name[:20]:<20} | {len(day_returns):>2} stocks | "
                    f"{avg_daily_return*100:>+7.2f}% | {cumul_pct:>+7.2f}%"
                )

        # ── 최종 결과 ──────────────────────────────────────
        final_capital = self.initial_capital * cumulative_return
        total_return = (cumulative_return - 1) * 100

        summary = (
            f"\n{'='*70}\n"
            f"  백테스팅 완료\n"
            f"  기간: {start_days_ago}영업일전 → 현재\n"
            f"  총 거래 횟수: {len(trades)}건\n"
            f"  초기 자본금: {self.initial_capital:>15,.0f}원\n"
            f"  최종 자본금: {final_capital:>15,.0f}원\n"
            f"  누적 수익률: {total_return:>+.2f}%\n"
            f"{'='*70}"
        )
        logger.info(summary)

        return {
            "initial_capital": self.initial_capital,
            "final_capital": final_capital,
            "total_return": total_return,
            "trade_count": len(trades),
            "trades": trades,
            "summary": summary,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="테마 기반 백테스팅 실행기")
    parser.add_argument(
        "days", 
        type=int, 
        nargs="?", 
        default=99, 
        help="시작일 (N영업일 전, 기본값: 99)"
    )
    parser.add_argument(
        "--capital", 
        type=float, 
        default=10_000_000, 
        help="초기 자본금 (기본값: 10,000,000)"
    )
    
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(_project_root, "logs",
                             f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                encoding="utf-8",
            ),
        ],
    )

    backtester = ThemeBacktester(initial_capital=args.capital)
    result = backtester.run(start_days_ago=args.days)

    print(result["summary"])

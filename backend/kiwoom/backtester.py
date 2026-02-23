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
from backend.kiwoom.pullback_backtester import PullbackBacktester

logger = logging.getLogger(__name__)

# 분봉 캐시 디렉토리
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_project_root, "cache", "minute_charts")
DAILY_CACHE_DIR = os.path.join(_project_root, "cache", "daily_charts")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)

# API 호출 간 딜레이 (초) — 레이트 리밋 방지
API_DELAY = 0.35

# 마찰 비용 상수 (왕복 0.345%)
FRICTION_COST = 0.00345


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

                    # 마찰 비용 적용 (왕복 0.345%)
                    sell_price_after_friction = result["sell_price"] * (1 - FRICTION_COST / 2)
                    buy_price_with_friction = buy_price * (1 + FRICTION_COST / 2)
                    
                    if buy_price_with_friction > 0:
                        ret_after_friction = (sell_price_after_friction - buy_price_with_friction) / buy_price_with_friction
                    else:
                        ret_after_friction = 0.0

                    day_returns.append(ret_after_friction)

                    trade_record = {
                        "day_offset": day_offset,
                        "buy_date": buy_date,
                        "sell_date": sell_date,
                        "theme": theme_name,
                        "stk_cd": stk_cd,
                        "stk_nm": stk_nm,
                        "buy_price": buy_price,
                        "sell_price": result["sell_price"],
                        "sell_price_after_friction": sell_price_after_friction,
                        "return_rate": ret_after_friction * 100,
                        "original_return_rate": result["return_rate"],
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
            f"  백테스팅 완료 [ThemeBacktester]\n"
            f"  기간: {start_days_ago}영업일전 → 현재\n"
            f"  마찰 비용: 왕복 {FRICTION_COST*100:.3f}%\n"
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


# ══════════════════════════════════════════════════════════════
#  SwingBacktester: 3~5일 스윙 전략 백테스터
# ══════════════════════════════════════════════════════════════

from backend.kiwoom.alpha_filter import AlphaFilter, compute_all_indicators
from backend.kiwoom.buy_strategy import BuyStrategyEngine
from backend.kiwoom.sell_strategy import SwingSellStrategyEngine, compute_atr
from backend.kiwoom.risk_manager import RegimeFilter, PositionSizer

from backend.kiwoom.risk_manager import RegimeFilter, PositionSizer


class SwingBacktester:
    """3~5일 스윙 전략 백테스터.

    전략 설계 문서 기반의 전체 파이프라인:
      1. 레짐 필터 (KOSPI200) → 시장 국면 판별
      2. 알파 필터 → 유니버스 스크리닝 (유동성/RVOL/모멘텀/이격도)
      3. Pseudo-VWAP 매수 → 14:30~15:20 분할 매수 시뮬레이션
      4. ATR 트레일링 스톱 매도 → 3~5일 보유 후 청산
      5. 변동성 역산 포지션 사이징 → 10 슬롯 모델
      6. 마찰 비용 차감 → 왕복 ~0.345%
    """

    def __init__(self, initial_capital: float = 10_000_000):
        self.finder = TopThemeFinder()
        self.alpha_filter = AlphaFilter()
        self.buy_engine = BuyStrategyEngine()
        self.sell_engine = SwingSellStrategyEngine()
        self.regime_filter = RegimeFilter()
        self.position_sizer = PositionSizer()

        self.initial_capital = initial_capital
        self._trading_days_cache: list[str] = []
        self._daily_bars_cache: dict[str, list[dict]] = {}

    # ── 개장일/캐시 (ThemeBacktester와 동일 로직 재사용) ─────

    def _load_trading_days(self):
        """삼성전자(005930) 일봉으로 개장일 목록 구축."""
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
        for _ in range(5):
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
        self._trading_days_cache = sorted(set(all_dates))
        logger.info("개장일 %d일 로드 완료", len(self._trading_days_cache))

    def _get_trading_day_n_ago(self, n: int) -> str:
        if not self._trading_days_cache:
            return ""
        idx = len(self._trading_days_cache) - n
        return self._trading_days_cache[idx] if idx >= 0 else ""

    def _next_trading_day(self, dt_str: str) -> str:
        try:
            idx = self._trading_days_cache.index(dt_str)
            if idx + 1 < len(self._trading_days_cache):
                return self._trading_days_cache[idx + 1]
        except ValueError:
            pass
        return ""

    def _get_n_trading_days_after(self, dt_str: str, n: int) -> list[str]:
        """dt_str 이후 n 영업일 리스트 반환."""
        try:
            idx = self._trading_days_cache.index(dt_str)
            return self._trading_days_cache[idx + 1: idx + 1 + n]
        except ValueError:
            return []

    def _get_daily_chart_cached(self, stk_cd: str) -> list[dict]:
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

    def _get_minute_chart_cached(self, stk_cd: str, base_dt: str) -> list[dict]:
        cache_file = os.path.join(CACHE_DIR, f"{stk_cd}_{base_dt}.json")
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        logger.info("분봉 캐시 미스: %s/%s → API 호출", stk_cd, base_dt)
        time.sleep(API_DELAY)
        bars = self.finder.get_minute_chart(stk_cd, base_dt)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(bars, f, ensure_ascii=False)
        return bars

    def _get_daily_bars_up_to(self, stk_cd: str, target_date: str) -> list[dict]:
        """target_date 이전(포함)의 일봉만 반환 (미래 참조 방지)."""
        all_bars = self._get_daily_chart_cached(stk_cd)
        result = []
        for bar in all_bars:
            result.append(bar)
            if bar.get("dt") == target_date:
                break
        return result

    def _get_daily_bar_for_date(self, stk_cd: str, target_date: str) -> dict:
        """특정 날짜의 일봉 1개를 반환합니다. 없으면 빈 dict."""
        all_bars = self._get_daily_chart_cached(stk_cd)
        for bar in all_bars:
            if bar.get("dt") == target_date:
                return bar
        return {}

    # ── 메인 백테스팅 루프 ─────────────────────────────────

    def run(self, start_days_ago: int = 60, use_daily_only: bool = True) -> dict:
        """스윙 백테스팅을 실행합니다.

        Args:
            start_days_ago: 시작일 (N 영업일 전)
            use_daily_only: True면 일봉만 사용 (장기 백테스팅 가능),
                            False면 기존 분봉 기반 로직 사용.

        Returns:
            {'initial_capital', 'final_capital', 'total_return',
             'trade_count', 'trades', 'portfolio_history', 'summary'}
        """
        self._load_trading_days()

        capital = self.initial_capital
        positions: list[dict] = []  # 현재 보유 포지션
        trades: list[dict] = []     # 완료된 거래
        portfolio_history: list[dict] = []  # 일별 포트폴리오 가치

        data_mode = "일봉 전용" if use_daily_only else "분봉 기반"
        logger.info("=" * 70)
        logger.info("  스윙 백테스팅 시작: %d 영업일 전 → 현재 [%s]", start_days_ago, data_mode)
        logger.info("  초기 자본금: %s원", f"{capital:,.0f}")
        logger.info("  전략: ATR(5)×2.5 트레일링 스톱, 최대 5일 보유")
        logger.info("=" * 70)

        # 테마 유니버스 1회 조회
        logger.info("상위 테마/종목 유니버스 구축 중...")
        themes = self.finder.get_top_themes(days_ago=1, top_n=50)
        all_candidate_stocks: list[dict] = []
        all_stk_codes: set[str] = set()

        for theme in themes:
            cd = theme.get("thema_grp_cd")
            if not cd:
                continue
            time.sleep(API_DELAY)
            stocks = self.finder.get_theme_stocks(cd, days_ago=1)
            for stk in stocks:
                stk_cd = stk.get("stk_cd", "")
                if stk_cd and stk_cd not in all_stk_codes:
                    all_stk_codes.add(stk_cd)
                    stk["theme_nm"] = theme.get("thema_nm", "")
                    all_candidate_stocks.append(stk)

        # 일봉 데이터 사전 수집
        logger.info("일봉 데이터 수집 중 (%d 종목)...", len(all_candidate_stocks))
        for stk in all_candidate_stocks:
            stk_cd = stk.get("stk_cd", "")
            if stk_cd:
                self._get_daily_chart_cached(stk_cd)

        # KOSPI 200 대용 — 삼성전자 일봉으로 레짐 판별
        kospi_bars = self._get_daily_chart_cached("005930")

        # ── 일별 루프 ──────────────────────────────────────
        for day_offset in range(start_days_ago, 5, -1):
            current_date = self._get_trading_day_n_ago(day_offset)
            if not current_date:
                continue

            # ── 1. 기존 포지션 관리 (ATR 스톱 체크 + 만기 청산) ──
            closed_positions = []
            for pos in positions:
                hold_dates = pos["holding_dates"]
                days_held = 0
                # 현재 날짜가 보유 기간에 포함되는지 확인
                if current_date in hold_dates:
                    days_held = hold_dates.index(current_date) + 1
                elif current_date > hold_dates[-1] if hold_dates else True:
                    days_held = len(hold_dates) + 1
                else:
                    continue

                stk_cd = pos["stk_cd"]

                # 스톱 라인 체크
                stop_line = pos["stop_line"]
                triggered = False
                sell_price = 0.0
                sell_time = "CLOSE"

                if use_daily_only:
                    # ── 일봉 전용 모드 ──
                    day_bar = self._get_daily_bar_for_date(stk_cd, current_date)
                    if day_bar:
                        low = _parse_price(day_bar.get("low_pric", "0"))
                        if low > 0 and low <= stop_line:
                            sell_price = stop_line  # 스톱가로 체결 가정
                            sell_time = "STOP"
                            triggered = True
                    day_close = _parse_price(day_bar.get("cur_prc", "0")) if day_bar else 0.0
                else:
                    # ── 기존 분봉 모드 ──
                    day_minute_bars = self._get_minute_chart_cached(stk_cd, current_date)
                    for bar in day_minute_bars:
                        low = _parse_price(bar.get("low_pric", "0"))
                        if low > 0 and low <= stop_line:
                            sell_price = _parse_price(bar.get("cur_prc", "0"))
                            if sell_price <= 0:
                                sell_price = stop_line
                            sell_time = bar.get("cntr_tm", "")[8:12] if len(bar.get("cntr_tm", "")) >= 12 else "????"
                            triggered = True
                            break
                    day_close = _parse_price(day_minute_bars[-1].get("cur_prc", "0")) if day_minute_bars else 0.0

                # 만기 청산
                force_close = days_held >= self.sell_engine.max_hold_days

                if triggered or force_close:
                    if not triggered:
                        # 종가 매도
                        if day_close > 0:
                            sell_price = day_close
                        else:
                            sell_price = pos["buy_price"]

                    # 마찰 비용 적용
                    sell_price_after_friction = sell_price * (1 - FRICTION_COST / 2)

                    ret = (sell_price_after_friction - pos["buy_price"]) / pos["buy_price"]
                    pnl = pos["position_amount"] * ret

                    capital += pos["position_amount"] + pnl

                    reason = f"ATR스톱(스톱={stop_line:.0f})" if triggered else f"만기청산({days_held}일)"
                    trade_record = {
                        "entry_date": pos["entry_date"],
                        "exit_date": current_date,
                        "exit_time": sell_time,
                        "stk_cd": stk_cd,
                        "stk_nm": pos.get("stk_nm", "?"),
                        "theme": pos.get("theme_nm", ""),
                        "buy_price": pos["buy_price"],
                        "sell_price": sell_price,
                        "sell_price_after_friction": sell_price_after_friction,
                        "return_rate": ret * 100,
                        "pnl": pnl,
                        "hold_days": days_held,
                        "reason": reason,
                        "atr": pos.get("atr", 0),
                    }
                    trades.append(trade_record)
                    closed_positions.append(pos)

                    logger.info(
                        "SELL [%s] %s: %.0f→%.0f (%.2f%%) %s (%d일)",
                        stk_cd, pos.get("stk_nm", "?"),
                        pos["buy_price"], sell_price,
                        ret * 100, reason, days_held,
                    )
                else:
                    # 스톱 라인 갱신 (래칫)
                    if day_close > 0:
                        new_stop = day_close - pos["stop_distance"]
                        if new_stop > stop_line:
                            pos["stop_line"] = new_stop

            for cp in closed_positions:
                positions.remove(cp)

            # ── 2. 레짐 필터 ─────────────────────────────────
            kospi_bars_up_to = [b for b in kospi_bars if b.get("dt", "") <= current_date]
            regime_result = self.regime_filter.detect_regime(kospi_bars_up_to)
            regime = regime_result["regime"]
            scale_factor = regime_result["scale_factor"]

            # ── 3. 신규 진입 ─────────────────────────────────
            available_slots = self.position_sizer.available_slots(len(positions))

            if available_slots > 0 and scale_factor > 0:
                # 알파 필터: 각 종목 일봉에서 당일까지의 지표 계산
                daily_bars_map = {}
                for stk in all_candidate_stocks:
                    stk_cd = stk.get("stk_cd", "")
                    if stk_cd:
                        bars = self._get_daily_bars_up_to(stk_cd, current_date)
                        if len(bars) >= 21:
                            daily_bars_map[stk_cd] = bars

                passed_stocks = self.alpha_filter.screen_universe(
                    all_candidate_stocks, daily_bars_map
                )

                # 이미 보유 중인 종목 제외
                held_codes = {p["stk_cd"] for p in positions}
                new_entries = [s for s in passed_stocks if s["stk_cd"] not in held_codes]

                # 슬롯 수만큼만 진입
                for stk in new_entries[:available_slots]:
                    stk_cd = stk["stk_cd"]
                    stk_nm = stk.get("stk_nm", "?")
                    indicators = stk.get("indicators", {})

                    # 매수가 산출
                    if use_daily_only:
                        # 일봉 종가 매수
                        entry_bar = self._get_daily_bar_for_date(stk_cd, current_date)
                        buy_price = _parse_price(entry_bar.get("cur_prc", "0")) if entry_bar else 0.0
                    else:
                        buy_bars = self._get_minute_chart_cached(stk_cd, current_date)
                        buy_result = self.buy_engine.execute(buy_bars, 1_000_000)  # dummy
                        buy_price = buy_result["avg_buy_price"]

                    if buy_price <= 0:
                        continue

                    # 매수가에 마찰 비용 적용
                    buy_price_with_friction = buy_price * (1 + FRICTION_COST / 2)

                    # ATR 계산 → 포지션 사이징
                    daily_bars = daily_bars_map.get(stk_cd, [])
                    atr = compute_atr(daily_bars, self.sell_engine.atr_period)
                    if not atr or atr == 0:
                        atr = buy_price * 0.02

                    sizing = self.position_sizer.compute_position_size(
                        capital, buy_price_with_friction, atr
                    )
                    position_amount = self.position_sizer.apply_regime_scale(
                        sizing["position_amount"], scale_factor
                    )

                    if position_amount <= 0 or position_amount > capital:
                        continue

                    # 보유 기간 산출
                    holding_dates = self._get_n_trading_days_after(
                        current_date, self.sell_engine.max_hold_days
                    )
                    if not holding_dates:
                        continue

                    stop_distance = atr * self.sell_engine.atr_multiplier
                    stop_line = buy_price_with_friction - stop_distance

                    capital -= position_amount

                    position = {
                        "stk_cd": stk_cd,
                        "stk_nm": stk_nm,
                        "theme_nm": stk.get("theme_nm", ""),
                        "entry_date": current_date,
                        "buy_price": buy_price_with_friction,
                        "position_amount": position_amount,
                        "atr": atr,
                        "stop_distance": stop_distance,
                        "stop_line": stop_line,
                        "holding_dates": holding_dates,
                    }
                    positions.append(position)

                    logger.info(
                        "BUY  [%s] %s: 매수가=%.0f, ATR=%.0f, 스톱=%.0f, 투입=%.0f원 (레짐=%s)",
                        stk_cd, stk_nm, buy_price_with_friction,
                        atr, stop_line, position_amount, regime,
                    )

                    available_slots -= 1
                    if available_slots <= 0:
                        break

            # ── 4. 일별 포트폴리오 가치 기록 ─────────────────
            position_value = sum(p["position_amount"] for p in positions)
            total_value = capital + position_value
            portfolio_history.append({
                "date": current_date,
                "capital": capital,
                "position_value": position_value,
                "total_value": total_value,
                "positions_count": len(positions),
                "regime": regime,
            })

        # ── 잔여 포지션 강제 청산 ──────────────────────────
        for pos in positions:
            capital += pos["position_amount"]
            trades.append({
                "entry_date": pos["entry_date"],
                "exit_date": "END",
                "stk_cd": pos["stk_cd"],
                "stk_nm": pos.get("stk_nm", "?"),
                "buy_price": pos["buy_price"],
                "sell_price": pos["buy_price"],
                "return_rate": 0.0,
                "pnl": 0.0,
                "hold_days": 0,
                "reason": "백테스트종료_강제청산",
            })

        final_capital = capital
        total_return = (final_capital - self.initial_capital) / self.initial_capital * 100

        # 통계
        win_trades = [t for t in trades if t.get("return_rate", 0) > 0]
        lose_trades = [t for t in trades if t.get("return_rate", 0) <= 0]
        win_rate = len(win_trades) / len(trades) * 100 if trades else 0
        avg_win = sum(t["return_rate"] for t in win_trades) / len(win_trades) if win_trades else 0
        avg_lose = sum(t["return_rate"] for t in lose_trades) / len(lose_trades) if lose_trades else 0

        summary = (
            f"\n{'='*70}\n"
            f"  스윙 백테스팅 완료\n"
            f"  기간: {start_days_ago}영업일전 → 현재\n"
            f"  총 거래: {len(trades)}건 (승: {len(win_trades)}, 패: {len(lose_trades)})\n"
            f"  승률: {win_rate:.1f}%\n"
            f"  평균 수익 거래: {avg_win:+.2f}% | 평균 손실 거래: {avg_lose:+.2f}%\n"
            f"  초기 자본금: {self.initial_capital:>15,.0f}원\n"
            f"  최종 자본금: {final_capital:>15,.0f}원\n"
            f"  누적 수익률: {total_return:>+.2f}%\n"
            f"  마찰 비용: 왕복 {FRICTION_COST*100:.3f}%\n"
            f"{'='*70}"
        )
        logger.info(summary)

        return {
            "initial_capital": self.initial_capital,
            "final_capital": final_capital,
            "total_return": total_return,
            "trade_count": len(trades),
            "win_rate": win_rate,
            "trades": trades,
            "portfolio_history": portfolio_history,
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
    parser.add_argument(
        "--strategy",
        type=str,
        default="legacy",
        choices=["legacy", "swing", "pullback"],
        help="전략 선택: legacy(기존 1일), swing(스윙 3~5일), pullback(스윙-풀백)"
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="daily",
        choices=["daily", "minute"],
        help="데이터 모드: daily(일봉 전용, 장기 가능), minute(분봉 기반, ~60일)"
    )
    parser.add_argument(
        "--volume-top-n",
        type=int,
        default=100,
        dest="volume_top_n",
        help="[pullback] 거래량 상위 N 종목 유니버스 (기본값: 100)"
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=10.0,
        dest="slippage_bps",
        help="[pullback] 매수·익절 슬리피지 bp (기본값: 10 = 0.1%%)"
    )
    parser.add_argument(
        "--stop-slippage-bps",
        type=float,
        default=20.0,
        dest="stop_slippage_bps",
        help="[pullback] 손절 슬리피지 bp (기본값: 20 = 0.2%%)"
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

    if args.strategy == "swing":
        backtester = SwingBacktester(initial_capital=args.capital)
        result = backtester.run(
            start_days_ago=args.days,
            use_daily_only=(args.mode == "daily"),
        )
    elif args.strategy == "pullback":
        backtester = PullbackBacktester(
            initial_capital=args.capital,
            volume_top_n=args.volume_top_n,
            slippage_bps=args.slippage_bps,
            stop_slippage_bps=args.stop_slippage_bps,
        )
        result = backtester.run(
            start_days_ago=args.days,
            use_daily_only=(args.mode == "daily"),
        )
    else:
        backtester = ThemeBacktester(initial_capital=args.capital)
        result = backtester.run(start_days_ago=args.days)
    print(result["summary"])
    print(f"\n  데이터 모드: {args.mode}")


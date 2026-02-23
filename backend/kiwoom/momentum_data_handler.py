"""
MomentumDataHandler: 중장기 듀얼 모멘텀 백테스팅용 데이터 전처리·무결성 관리 모듈.

역할:
  1. cache/daily_charts/*.json 일봉 캐시 일괄 로드
  2. Pandas DataFrame 기반 종가·거래량·거래대금 행렬 구축
  3. KOSPI 대용(삼성전자) SMA(200) 산출
  4. shift(1) 기반 미래 참조 편향(Look-ahead Bias) 원천 차단
  5. get_data_up_to(date) — 특정 시점까지의 데이터만 반환하는 샌드박스

설계 문서 참조: §2.2 미래 참조 편향의 원천 차단, §3.1 동적 유니버스 필터링
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Tuple, Optional

import pandas as pd
import numpy as np

from backend.kiwoom.theme_finder import TopThemeFinder
from backend.kiwoom.sell_strategy import _parse_price

logger = logging.getLogger(__name__)

# ── 캐시 경로 ──────────────────────────────────────────
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DAILY_CACHE_DIR = os.path.join(_project_root, "cache", "daily_charts")
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)

# API 호출 간 딜레이
API_DELAY = 0.35


class MomentumDataHandler:
    """
    모든 가격/거래량 데이터 및 벤치마크(KOSPI) 데이터를 캡슐화하는 클래스.

    미래 참조 편향(Look-ahead Bias) 방지를 위해 특정 시점(t)까지의
    데이터만 반환하는 인터페이스를 강제한다.

    Attributes:
        prices:          종목(열) × 날짜(행) 종가 DataFrame
        volumes:         종목(열) × 날짜(행) 거래량 DataFrame
        trading_value:   종목(열) × 날짜(행) 거래대금 DataFrame (MA20, shift(1) 적용)
        kospi:           벤치마크(삼성전자) 종가 Series
        kospi_sma200:    벤치마크 200일 SMA (shift(1) 적용)
    """

    # 벤치마크 종목코드 (KOSPI 200 대용)
    BENCHMARK_CODE = "005930"

    # 동적 유니버스 유동성 허들
    DEFAULT_ADTV_WINDOW = 20
    DEFAULT_MIN_TRADING_VALUE = 5e9  # 50억 원

    def __init__(
        self,
        finder: Optional[TopThemeFinder] = None,
        min_history_days: int = 500,
    ):
        """
        Args:
            finder: API 호출용 TopThemeFinder 인스턴스.
                    None이면 캐시 전용 모드(API 호출 없이 캐시만 사용).
            min_history_days: 종목당 최소 히스토리 일수.
                              252(모멘텀) + 200(SMA) = 452일 이상 권장.
        """
        self.finder = finder
        self.min_history_days = min_history_days

        # ── 원본 데이터 (list[dict] 형태, 종목별) ──
        self._raw_daily_charts: dict[str, list[dict]] = {}

        # ── Pandas DataFrame (구축 후 사용) ──
        self.prices: pd.DataFrame = pd.DataFrame()
        self.volumes: pd.DataFrame = pd.DataFrame()
        self.trading_value: pd.DataFrame = pd.DataFrame()
        self.kospi: pd.Series = pd.Series(dtype=float)
        self.kospi_sma200: pd.Series = pd.Series(dtype=float)

        # ── 메타데이터 ──
        self._trading_days: pd.DatetimeIndex = pd.DatetimeIndex([])
        self._month_end_dates: pd.DatetimeIndex = pd.DatetimeIndex([])

    # ═══════════════════════════════════════════════════
    #  1. 데이터 로드
    # ═══════════════════════════════════════════════════

    def load_from_cache(self, additional_codes: Optional[list[str]] = None) -> int:
        """cache/daily_charts 디렉토리의 모든 일봉 JSON을 메모리에 로드합니다.

        Args:
            additional_codes: 캐시에 없으면 API로 추가 수집할 종목코드 목록.
                              finder가 None이면 무시.

        Returns:
            로드된 종목 수
        """
        count = 0

        # 1) 캐시 디렉토리 전량 스캔
        if os.path.isdir(DAILY_CACHE_DIR):
            for fname in os.listdir(DAILY_CACHE_DIR):
                if not fname.endswith(".json"):
                    continue
                stk_cd = fname.replace(".json", "")
                if stk_cd in self._raw_daily_charts:
                    count += 1
                    continue
                cache_file = os.path.join(DAILY_CACHE_DIR, fname)
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        bars = json.load(f)
                    if bars:
                        self._raw_daily_charts[stk_cd] = bars
                        count += 1
                except Exception:
                    pass

        logger.info("캐시 일봉 %d 종목 메모리 로드 완료.", count)

        # 2) 벤치마크(삼전) 캐시 확인 — 없으면 API 호출
        if self.BENCHMARK_CODE not in self._raw_daily_charts:
            self._fetch_and_cache(self.BENCHMARK_CODE)

        # 3) 추가 종목 API 수집
        if additional_codes and self.finder:
            for code in additional_codes:
                if code not in self._raw_daily_charts:
                    self._fetch_and_cache(code)

        return len(self._raw_daily_charts)

    def _fetch_and_cache(self, stk_cd: str) -> list[dict]:
        """API로 일봉을 조회하여 캐시에 저장합니다."""
        if not self.finder:
            logger.warning("finder 미설정 — %s API 호출 불가.", stk_cd)
            return []

        logger.info("일봉 API 호출: %s", stk_cd)
        time.sleep(API_DELAY)
        bars = self.finder.get_daily_chart(stk_cd, datetime.now().strftime("%Y%m%d"))

        if bars:
            cache_file = os.path.join(DAILY_CACHE_DIR, f"{stk_cd}.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(bars, f, ensure_ascii=False)
            self._raw_daily_charts[stk_cd] = bars

        return bars

    # ═══════════════════════════════════════════════════
    #  2. DataFrame 구축
    # ═══════════════════════════════════════════════════

    def build_dataframes(self) -> None:
        """원본 JSON 데이터를 Pandas DataFrame으로 변환합니다.

        구축되는 DataFrame:
          - prices:  종가 (ffill 적용)
          - volumes: 거래량 (0 채움)
          - trading_value: 거래대금 MA(20), shift(1) 적용
          - kospi:   벤치마크 종가 (ffill)
          - kospi_sma200: 벤치마크 SMA(200), shift(1) 적용
        """
        if not self._raw_daily_charts:
            raise RuntimeError(
                "데이터가 로드되지 않았습니다. load_from_cache()를 먼저 호출하세요."
            )

        logger.info("DataFrame 구축 시작 (%d 종목)...", len(self._raw_daily_charts))

        # ── 2-1. 종목별 종가·거래량 Series 생성 ──
        price_series: dict[str, pd.Series] = {}
        volume_series: dict[str, pd.Series] = {}

        for stk_cd, bars in self._raw_daily_charts.items():
            dates = []
            closes = []
            vols = []

            for bar in bars:
                dt_str = bar.get("dt", "")
                if not dt_str or len(dt_str) != 8:
                    continue

                close = _parse_price(bar.get("cur_prc", "0"))
                vol = _parse_price(bar.get("trde_qty", "0"))

                if close <= 0:
                    continue

                try:
                    dt = pd.Timestamp(dt_str)
                except Exception:
                    continue

                dates.append(dt)
                closes.append(close)
                vols.append(vol)

            if len(dates) >= 20:  # 최소 20일 이상 데이터가 있어야 편입
                price_series[stk_cd] = pd.Series(closes, index=dates, name=stk_cd)
                volume_series[stk_cd] = pd.Series(vols, index=dates, name=stk_cd)

        if not price_series:
            raise RuntimeError("유효한 종목 데이터가 없습니다.")

        # ── 2-2. DataFrame 합병 (outer join) ──
        self.prices = pd.DataFrame(price_series)
        self.volumes = pd.DataFrame(volume_series)

        # 인덱스 정렬 (오래된 → 최신)
        self.prices.sort_index(inplace=True)
        self.volumes.sort_index(inplace=True)

        # 중복 인덱스 제거 (혹시 모를 데이터 이상)
        self.prices = self.prices[~self.prices.index.duplicated(keep="last")]
        self.volumes = self.volumes[~self.volumes.index.duplicated(keep="last")]

        # 인덱스 통일
        common_index = self.prices.index.union(self.volumes.index).sort_values()
        self.prices = self.prices.reindex(common_index)
        self.volumes = self.volumes.reindex(common_index)

        # ── 2-3. 결측치 처리 ──
        # 가격: 전방 채움(ffill) → 상장 전/거래 정지 구간 보간
        self.prices = self.prices.ffill()
        # 거래량: 결측 → 0
        self.volumes = self.volumes.fillna(0)

        # ── 2-4. 거래대금(Trading Value) 산출 ──
        #   거래대금 = 종가 × 거래량
        #   → 20일 이동평균
        #   → shift(1): t 시점 의사결정에 t-1일까지의 데이터만 사용
        raw_trading_value = self.prices * self.volumes
        self.trading_value = (
            raw_trading_value
            .rolling(window=self.DEFAULT_ADTV_WINDOW, min_periods=1)
            .mean()
            .shift(1)
        )

        # ── 2-5. 벤치마크(KOSPI 대용) ──
        if self.BENCHMARK_CODE in self.prices.columns:
            self.kospi = self.prices[self.BENCHMARK_CODE].copy()
            self.kospi = self.kospi.ffill()

            self.kospi_sma200 = (
                self.kospi
                .rolling(window=200, min_periods=200)
                .mean()
                .shift(1)
            )
        else:
            logger.warning(
                "벤치마크 종목 %s이(가) 데이터에 없습니다. "
                "국면 필터가 비활성화됩니다.",
                self.BENCHMARK_CODE,
            )
            self.kospi = pd.Series(dtype=float)
            self.kospi_sma200 = pd.Series(dtype=float)

        # ── 2-6. 영업일 인덱스 & 월말 리밸런싱 날짜 ──
        self._trading_days = self.prices.index
        self._compute_month_end_dates()

        logger.info(
            "DataFrame 구축 완료: %d 종목 × %d 영업일 (%s ~ %s)",
            len(self.prices.columns),
            len(self._trading_days),
            self._trading_days[0].strftime("%Y-%m-%d") if len(self._trading_days) else "?",
            self._trading_days[-1].strftime("%Y-%m-%d") if len(self._trading_days) else "?",
        )

    def _compute_month_end_dates(self) -> None:
        """매월 마지막 영업일 인덱스를 산출합니다."""
        if self._trading_days.empty:
            self._month_end_dates = pd.DatetimeIndex([])
            return

        # 실제 영업일 중 각 월의 마지막 날짜 추출
        sr = pd.Series(range(len(self._trading_days)), index=self._trading_days)
        eom = sr.resample("ME").last()  # 월말 리샘플
        eom_dates = eom.dropna().index

        # 실제 영업일과 교차 → 영업일에 속하는 월말만
        # resample('ME')가 반환하는 날짜는 달력상 월말이므로
        # 가장 가까운 이전 영업일을 찾아야 함
        actual_eom: list[pd.Timestamp] = []
        for eom_dt in eom_dates:
            # eom_dt 이하인 영업일 중 가장 마지막
            mask = self._trading_days <= eom_dt
            if mask.any():
                actual_eom.append(self._trading_days[mask][-1])

        self._month_end_dates = pd.DatetimeIndex(sorted(set(actual_eom)))
        logger.info("월말 리밸런싱 날짜 %d개 산출됨.", len(self._month_end_dates))

    # ═══════════════════════════════════════════════════
    #  3. 데이터 접근 인터페이스 (미래 참조 차단)
    # ═══════════════════════════════════════════════════

    def get_data_up_to(
        self, date: pd.Timestamp
    ) -> Tuple[pd.DataFrame, pd.DataFrame, float, float]:
        """특정 시점 date까지의 데이터 슬라이스를 반환합니다.

        이 메서드를 통해서만 데이터에 접근함으로써
        알고리즘의 미래 정보 접근을 원천 봉쇄합니다.

        Args:
            date: 기준 날짜 (pd.Timestamp 또는 호환 타입)

        Returns:
            (hist_prices, hist_trading_value, current_kospi, current_kospi_sma200)

            - hist_prices: date까지의 종가 DataFrame
            - hist_trading_value: date까지의 거래대금 MA(20) DataFrame (이미 shift(1) 적용)
            - current_kospi: date 기준 벤치마크 종가 (없으면 NaN)
            - current_kospi_sma200: date 기준 SMA(200) (없으면 NaN)
        """
        hist_prices = self.prices.loc[:date]
        hist_tv = self.trading_value.loc[:date]

        current_kospi = float("nan")
        current_kospi_sma = float("nan")

        if not self.kospi.empty and date in self.kospi.index:
            current_kospi = self.kospi.loc[date]
        elif not self.kospi.empty:
            # 정확한 날짜가 없으면 가장 가까운 이전 날짜 사용
            mask = self.kospi.index <= date
            if mask.any():
                current_kospi = self.kospi.loc[self.kospi.index[mask][-1]]

        if not self.kospi_sma200.empty and date in self.kospi_sma200.index:
            current_kospi_sma = self.kospi_sma200.loc[date]
        elif not self.kospi_sma200.empty:
            mask = self.kospi_sma200.index <= date
            if mask.any():
                current_kospi_sma = self.kospi_sma200.loc[
                    self.kospi_sma200.index[mask][-1]
                ]

        return hist_prices, hist_tv, current_kospi, current_kospi_sma

    def get_current_prices(self, date: pd.Timestamp) -> pd.Series:
        """특정 날짜의 종목별 종가를 반환합니다.

        정확히 해당 날짜 행이 없으면 가장 가까운 이전 영업일 종가를 사용합니다.

        Args:
            date: 기준 날짜

        Returns:
            종목코드를 인덱스로 한 종가 Series
        """
        if date in self.prices.index:
            return self.prices.loc[date]

        mask = self.prices.index <= date
        if mask.any():
            return self.prices.loc[self.prices.index[mask][-1]]

        return pd.Series(dtype=float)

    # ═══════════════════════════════════════════════════
    #  4. 유틸리티
    # ═══════════════════════════════════════════════════

    def get_available_dates(self) -> pd.DatetimeIndex:
        """백테스트가 순회할 전체 영업일 인덱스를 반환합니다."""
        return self._trading_days

    def get_month_end_dates(self) -> pd.DatetimeIndex:
        """월말 리밸런싱 트리거 날짜 목록을 반환합니다."""
        return self._month_end_dates

    def get_backtest_window(
        self, warmup_days: int = 252
    ) -> pd.DatetimeIndex:
        """모멘텀 지표 산출에 필요한 웜업 기간을 제외한 실제 백테스트 구간을 반환합니다.

        Args:
            warmup_days: 모멘텀 계산에 필요한 최소 과거 데이터 일수.
                         12개월 모멘텀 = 252일.

        Returns:
            웜업 이후의 영업일 인덱스
        """
        if len(self._trading_days) <= warmup_days:
            logger.warning(
                "데이터(%d일)가 웜업(%d일)보다 짧습니다.",
                len(self._trading_days),
                warmup_days,
            )
            return pd.DatetimeIndex([])

        return self._trading_days[warmup_days:]

    def get_stock_count(self) -> int:
        """로드된 종목 수를 반환합니다."""
        return len(self.prices.columns) if not self.prices.empty else 0

    def get_date_range(self) -> Tuple[Optional[str], Optional[str]]:
        """데이터의 시작일과 종료일을 (YYYY-MM-DD, YYYY-MM-DD) 형식으로 반환합니다."""
        if self._trading_days.empty:
            return None, None
        return (
            self._trading_days[0].strftime("%Y-%m-%d"),
            self._trading_days[-1].strftime("%Y-%m-%d"),
        )

    def summary(self) -> str:
        """데이터 현황 요약 문자열을 반환합니다."""
        start, end = self.get_date_range()
        n_stocks = self.get_stock_count()
        n_days = len(self._trading_days)
        n_eom = len(self._month_end_dates)

        has_benchmark = self.BENCHMARK_CODE in self.prices.columns
        sma200_valid = not self.kospi_sma200.empty and self.kospi_sma200.notna().sum() > 0

        lines = [
            "=" * 60,
            "  MomentumDataHandler 데이터 현황",
            "=" * 60,
            f"  종목 수:       {n_stocks:>8,d}",
            f"  영업일 수:     {n_days:>8,d}",
            f"  데이터 기간:   {start or 'N/A'} ~ {end or 'N/A'}",
            f"  월말 날짜 수:  {n_eom:>8,d}",
            f"  벤치마크:      {'✓' if has_benchmark else '✗'} ({self.BENCHMARK_CODE})",
            f"  SMA(200):      {'✓' if sma200_valid else '✗'} (shift(1) 적용)",
            "=" * 60,
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  테스트/검증용 CLI
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    handler = MomentumDataHandler(finder=None)  # 캐시 전용 모드
    n = handler.load_from_cache()

    if n == 0:
        logger.error("캐시에 일봉 데이터가 없습니다. 먼저 다른 백테스터를 실행하여 캐시를 구축하세요.")
    else:
        handler.build_dataframes()
        print(handler.summary())

        # 샘플: 마지막 영업일 기준 get_data_up_to 테스트
        dates = handler.get_available_dates()
        if len(dates) > 0:
            last_date = dates[-1]
            hist_prices, hist_tv, kospi_val, kospi_sma = handler.get_data_up_to(
                last_date
            )
            print(f"\n최근 영업일({last_date.date()}) 기준:")
            print(f"  hist_prices shape: {hist_prices.shape}")
            print(f"  hist_tv shape:     {hist_tv.shape}")
            print(f"  KOSPI 종가:        {kospi_val:,.0f}")
            print(f"  KOSPI SMA(200):    {kospi_sma:,.0f}")

            # ADTV 50억 이상 종목 수 확인
            latest_tv = hist_tv.iloc[-1] if not hist_tv.empty else pd.Series(dtype=float)
            eligible = latest_tv[latest_tv >= 5e9]
            print(f"  ADTV ≥ 50억 종목:  {len(eligible)}개")

            # 웜업 이후 실제 백테스트 가용일수
            bt_dates = handler.get_backtest_window(warmup_days=252)
            print(f"  백테스트 가용일수:  {len(bt_dates)}일")

            # 월말 리밸런싱 날짜 샘플
            eom = handler.get_month_end_dates()
            if len(eom) >= 3:
                print(f"  월말 날짜 샘플:    {eom[-3].date()}, {eom[-2].date()}, {eom[-1].date()}")

"""
글로벌 ETF 데이터 수집기 — Yahoo Finance API (yfinance)

설계 문서: docs/글로벌_듀얼_모멤텀_설계계획.md  §2-1

캐시 구조:  cache/global_charts/{TICKER}.json
환율 캐시:  cache/global_charts/USDKRW.json
캐시 정책:  마지막 수정 18 시간 이상이면 자동 re-fetch
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

# ── 경로 ────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]          # kimum_trade/
GLOBAL_CACHE_DIR = str(_PROJECT_ROOT / "cache" / "global_charts")

# ── 기본 유니버스 ───────────────────────────────────
DEFAULT_TICKERS: list[str] = [
    # 주식
    "SPY",   # 미국 대형주 (S&P 500)
    "IWM",   # 미국 소형주 (Russell 2000)
    "EFA",   # 선진국 ex-US (MSCI EAFE)
    "EEM",   # 신흥국 (MSCI EM)
    "EWY",   # 한국 (MSCI Korea)
    # 채권
    "AGG",   # 미국 종합채권
    "IEF",   # 미국 중기 국채 (7-10Y)
    "TLT",   # 미국 장기 국채 (20Y+)
    "TIP",   # 물가연동채 (TIPS)
    # 실물자산
    "VNQ",   # 글로벌 리츠
    "DBC",   # 원자재
    "GLD",   # 금
    # 현금등가
    "SHY",   # 단기 국채 (1-3Y)
]

# ── 재시도 설정 ─────────────────────────────────────
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # 초 단위, exponential backoff


class GlobalDataFetcher:
    """Yahoo Finance에서 글로벌 ETF 일봉 데이터를 다운로드·캐시합니다."""

    def __init__(
        self,
        tickers: list[str] | None = None,
        lookback_years: int = 5,
        cache_dir: str = GLOBAL_CACHE_DIR,
        max_cache_age_hours: int = 18,
    ):
        self.tickers = tickers or DEFAULT_TICKERS
        self.lookback_years = lookback_years
        self.cache_dir = cache_dir
        self.max_cache_age_hours = max_cache_age_hours

        os.makedirs(self.cache_dir, exist_ok=True)

    # ================================================================
    # Public API
    # ================================================================

    def fetch_all(self, force_refresh: bool = False) -> dict[str, list[dict]]:
        """전 티커를 다운로드하여 cache/global_charts/{TICKER}.json에 저장.

        Returns:
            { "SPY": [{dt, open, high, low, close, volume}, ...], ... }
        """
        result: dict[str, list[dict]] = {}
        for ticker in self.tickers:
            try:
                if not force_refresh and self.is_cache_fresh(ticker):
                    cached = self.load_from_cache(ticker)
                    if cached is not None:
                        result[ticker] = cached
                        logger.info(f"[GlobalDataFetcher] {ticker}: 캐시 로드 ({len(cached)}일)")
                        continue

                data = self.fetch_single(ticker)
                if data:
                    result[ticker] = data
                    logger.info(f"[GlobalDataFetcher] {ticker}: yfinance 다운로드 ({len(data)}일)")
                else:
                    # 다운로드 실패 시 기존 캐시라도 사용
                    cached = self.load_from_cache(ticker)
                    if cached:
                        result[ticker] = cached
                        logger.warning(f"[GlobalDataFetcher] {ticker}: 다운로드 실패, 기존 캐시 사용")
                    else:
                        logger.error(f"[GlobalDataFetcher] {ticker}: 데이터 없음 (다운로드 실패 & 캐시 없음)")
            except Exception as e:
                logger.error(f"[GlobalDataFetcher] {ticker}: 오류 발생 - {e}")
                cached = self.load_from_cache(ticker)
                if cached:
                    result[ticker] = cached
        return result

    def fetch_single(self, ticker: str) -> list[dict] | None:
        """단일 티커를 yfinance로 다운로드. 실패 시 None 반환.

        자동 재시도: 최대 3회 (exponential backoff).
        """
        end = datetime.now()
        start = end - timedelta(days=365 * self.lookback_years + 30)  # 여유 30일

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                df: pd.DataFrame = yf.download(
                    ticker,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    auto_adjust=True,
                    progress=False,
                )

                if df.empty:
                    logger.warning(f"[GlobalDataFetcher] {ticker}: 빈 DataFrame (시도 {attempt}/{MAX_RETRIES})")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_BASE_DELAY ** attempt)
                        continue
                    return None

                # DataFrame → list[dict] 변환
                records = self._dataframe_to_records(df, ticker)

                # 캐시 저장
                self._save_to_cache(ticker, records)
                return records

            except Exception as e:
                logger.warning(f"[GlobalDataFetcher] {ticker}: 다운로드 오류 (시도 {attempt}/{MAX_RETRIES}) - {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY ** attempt)

        return None

    def load_from_cache(self, ticker: str) -> list[dict] | None:
        """로컬 캐시 JSON이 있으면 로드. 없으면 None."""
        path = self._cache_path(ticker)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"[GlobalDataFetcher] {ticker}: 캐시 로드 실패 - {e}")
            return None

    def is_cache_fresh(self, ticker: str, max_age_hours: int | None = None) -> bool:
        """캐시 파일의 최종 수정 시각이 max_age_hours 이내인지 확인."""
        max_age = max_age_hours if max_age_hours is not None else self.max_cache_age_hours
        path = self._cache_path(ticker)
        if not os.path.exists(path):
            return False
        mtime = os.path.getmtime(path)
        age_hours = (time.time() - mtime) / 3600
        return age_hours < max_age

    # ── 환율 관련 ───────────────────────────────────

    def fetch_usdkrw_rate(self) -> float:
        """yfinance로 최신 USD/KRW 환율을 조회합니다."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ticker = yf.Ticker("USDKRW=X")
                data = ticker.history(period="5d")
                if data.empty:
                    logger.warning(f"[GlobalDataFetcher] USDKRW: 빈 데이터 (시도 {attempt}/{MAX_RETRIES})")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_BASE_DELAY ** attempt)
                        continue
                    return self._load_cached_fx_rate()

                rate = float(data["Close"].iloc[-1])
                self._save_cached_fx_rate(rate)
                logger.info(f"[GlobalDataFetcher] USD/KRW 환율: {rate:.2f}")
                return rate

            except Exception as e:
                logger.warning(f"[GlobalDataFetcher] USDKRW 조회 오류 (시도 {attempt}/{MAX_RETRIES}) - {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY ** attempt)

        return self._load_cached_fx_rate()

    def _load_cached_fx_rate(self) -> float:
        """cache/global_charts/USDKRW.json에서 마지막 환율 로드."""
        path = os.path.join(self.cache_dir, "USDKRW.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    rate = data.get("rate", 1350.0)
                    logger.info(f"[GlobalDataFetcher] USDKRW 캐시 로드: {rate:.2f}")
                    return rate
            except (json.JSONDecodeError, IOError):
                pass
        logger.warning("[GlobalDataFetcher] USDKRW 캐시 없음, 기본값 1350.0 사용")
        return 1350.0

    def _save_cached_fx_rate(self, rate: float) -> None:
        """환율을 캐시에 저장."""
        path = os.path.join(self.cache_dir, "USDKRW.json")
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"rate": rate, "updated": datetime.now().isoformat()},
                f,
                ensure_ascii=False,
            )

    # ================================================================
    # Private helpers
    # ================================================================

    def _cache_path(self, ticker: str) -> str:
        """캐시 파일 경로: cache/global_charts/{TICKER}.json"""
        return os.path.join(self.cache_dir, f"{ticker}.json")

    def _save_to_cache(self, ticker: str, records: list[dict]) -> None:
        """레코드 목록을 JSON 캐시에 저장."""
        path = self._cache_path(ticker)
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=1)

    @staticmethod
    def _dataframe_to_records(df: pd.DataFrame, ticker: str) -> list[dict]:
        """yfinance DataFrame을 [{dt, open, high, low, close, volume}, ...] 로 변환.

        yfinance >= 1.0 에서는 multi-level columns가 반환될 수 있으므로
        ticker 열 레벨을 제거합니다.
        """
        # multi-level columns 처리 (yfinance >= 1.0)
        if isinstance(df.columns, pd.MultiIndex):
            # ('Close', 'SPY') → 'Close' 형태로 flatten
            df = df.droplevel("Ticker", axis=1) if "Ticker" in df.columns.names else df
            # 여전히 MultiIndex이면 첫 번째 레벨만 사용
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        records: list[dict] = []
        for idx, row in df.iterrows():
            dt_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)

            # NaN 체크 — 결측치가 있는 행은 skip
            close_val = row.get("Close")
            if pd.isna(close_val):
                continue

            records.append({
                "dt": dt_str,
                "open": round(float(row.get("Open", 0)), 4),
                "high": round(float(row.get("High", 0)), 4),
                "low": round(float(row.get("Low", 0)), 4),
                "close": round(float(close_val), 4),
                "volume": int(row.get("Volume", 0)),
            })

        # 날짜 오름차순 정렬
        records.sort(key=lambda r: r["dt"])
        return records

    # ================================================================
    # 유틸리티
    # ================================================================

    def get_cache_summary(self) -> dict[str, dict[str, Any]]:
        """각 티커의 캐시 상태를 요약합니다.

        Returns:
            { "SPY": {"exists": True, "fresh": True, "records": 1260, "latest": "2026-02-21"}, ... }
        """
        summary: dict[str, dict[str, Any]] = {}
        for ticker in self.tickers:
            path = self._cache_path(ticker)
            exists = os.path.exists(path)
            fresh = self.is_cache_fresh(ticker) if exists else False
            records = 0
            latest = None
            if exists:
                cached = self.load_from_cache(ticker)
                if cached:
                    records = len(cached)
                    latest = cached[-1]["dt"] if cached else None
            summary[ticker] = {
                "exists": exists,
                "fresh": fresh,
                "records": records,
                "latest": latest,
            }
        return summary


# ================================================================
# CLI 지원 — 직접 실행 시 전체 데이터 수집
# ================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    fetcher = GlobalDataFetcher()

    print("=" * 60)
    print("글로벌 ETF 데이터 수집 시작")
    print(f"  티커: {fetcher.tickers}")
    print(f"  lookback: {fetcher.lookback_years}년")
    print(f"  캐시: {fetcher.cache_dir}")
    print("=" * 60)

    # 전체 데이터 수집
    result = fetcher.fetch_all(force_refresh=True)

    print(f"\n수집 완료: {len(result)}/{len(fetcher.tickers)} 티커")
    for ticker, data in result.items():
        if data:
            print(f"  {ticker}: {len(data)}일  ({data[0]['dt']} ~ {data[-1]['dt']})")
        else:
            print(f"  {ticker}: 데이터 없음")

    # 환율 조회
    print("\n환율 조회 중...")
    rate = fetcher.fetch_usdkrw_rate()
    print(f"  USD/KRW: {rate:.2f}")

    print("\n완료!")

"""
Yahoo Finance를 활용한 나스닥(NASDAQ Composite) 일봉 데이터 조회 모듈.
전일 나스닥 종가를 엑셀 데이터에 추가하기 위한 유틸리티.
"""
import os
import sys
import json
from datetime import datetime, timedelta

sys.path.append(os.getcwd())
from utils.config import get_logger

logger = get_logger("nasdaq_client", "nasdaq_client.log")

# 나스닥 종가 캐시 (메모리 내)
_nasdaq_cache = {}

# 로컬 파일 캐시 경로
NASDAQ_CACHE_DIR = os.path.join(os.getcwd(), "cache_nasdaq")


def fetch_nasdaq_close(target_date_int: int):
    """
    target_date_int: YYYYMMDD (예: 20260304)
    해당 날짜의 *전날* 나스닥(^IXIC) 종가를 반환.
    (한국 시간 D+1 매수일 기준, 전날 새벽에 마감된 미국 장의 종가)
    미국 휴장일이면 직전 거래일 종가를 반환.
    Returns: float or None
    """
    # 메모리 캐시 확인
    if target_date_int in _nasdaq_cache:
        return _nasdaq_cache[target_date_int]

    # 파일 캐시 확인
    if not os.path.exists(NASDAQ_CACHE_DIR):
        os.makedirs(NASDAQ_CACHE_DIR)

    cache_file = os.path.join(NASDAQ_CACHE_DIR, f"nasdaq_{target_date_int}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cached = json.load(f)
                close_val = cached.get("close")
                _nasdaq_cache[target_date_int] = close_val
                return close_val
        except Exception:
            pass

    # yfinance로 조회
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance 패키지가 설치되어 있지 않습니다. pip install yfinance 실행 필요.")
        return None

    import time as _time

    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds

    target_dt = datetime.strptime(str(target_date_int), "%Y%m%d")
    start_dt = target_dt - timedelta(days=10)
    end_dt = target_dt  # target_date 미포함 (전날까지)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ticker = yf.Ticker("^IXIC")
            hist = ticker.history(start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"))

            if hist.empty:
                logger.warning(f"나스닥 데이터 없음: {target_date_int}")
                break  # 데이터 자체가 없는 경우 재시도 불필요 → 인접 캐시 폴백

            # 가장 최근 거래일의 종가
            close_val = round(float(hist.iloc[-1]["Close"]), 2)

            # 캐시 저장
            _nasdaq_cache[target_date_int] = close_val
            try:
                with open(cache_file, 'w') as f:
                    json.dump({"date": target_date_int, "close": close_val}, f)
            except Exception as e:
                logger.warning(f"나스닥 캐시 저장 실패: {e}")

            logger.info(f"나스닥 종가 조회 완료: {target_date_int} → {close_val}")
            return close_val

        except Exception as e:
            logger.warning(f"나스닥 종가 조회 실패 (시도 {attempt}/{MAX_RETRIES}, {target_date_int}): {e}")
            if attempt < MAX_RETRIES:
                _time.sleep(RETRY_DELAY)

    # 모든 재시도 실패 → 인접 날짜 캐시 폴백 (±5일 범위)
    logger.info(f"나스닥 API {MAX_RETRIES}회 실패. 인접 캐시 폴백 시도: {target_date_int}")
    for delta in range(1, 6):
        for direction in [-1, 1]:  # 과거 우선 탐색
            nearby_dt = target_dt + timedelta(days=delta * direction)
            nearby_int = int(nearby_dt.strftime("%Y%m%d"))
            nearby_file = os.path.join(NASDAQ_CACHE_DIR, f"nasdaq_{nearby_int}.json")
            if os.path.exists(nearby_file):
                try:
                    with open(nearby_file, 'r') as f:
                        cached = json.load(f)
                        close_val = cached.get("close")
                        if close_val is not None:
                            logger.info(f"나스닥 폴백: {target_date_int} → 인접일 {nearby_int} 캐시 사용 ({close_val})")
                            _nasdaq_cache[target_date_int] = close_val
                            return close_val
                except Exception:
                    pass

    logger.warning(f"나스닥 종가 폴백도 실패 ({target_date_int}): 인접 캐시 없음")
    return None

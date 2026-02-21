import os
import json
import certifi
import urllib3
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# Mock 모드: 1이면 Mock 데이터, 0이면 키움 실제 API 호출
MOCK_MODE = os.getenv("USE_MOCK_KIWOOM", "1") == "1"
ACCESS_TOKEN = os.getenv("KIWOOM_ACCESS_TOKEN", "")
KIWOOM_DOMAIN = os.getenv("KIWOOM_DOMAIN", "https://api.kiwoom.com")

# stock_mapper.py가 생성하는 캐시 파일 경로
STOCK_MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "stock_map.json")

# Disable warnings for mock usage
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 종목 캐시를 메모리에 한 번만 로드
_stock_map_cache = None

def _load_stock_map() -> dict:
    """cache/stock_map.json을 메모리로 로드 (1회만)"""
    global _stock_map_cache
    if _stock_map_cache is not None:
        return _stock_map_cache

    if os.path.exists(STOCK_MAP_FILE):
        try:
            with open(STOCK_MAP_FILE, 'r', encoding='utf-8') as f:
                _stock_map_cache = json.load(f)
                print(f"[StockMapper] Loaded {len(_stock_map_cache)} stocks from cache")
                return _stock_map_cache
        except Exception as e:
            print(f"[StockMapper] Failed to load cache: {e}")

    _stock_map_cache = {}
    return _stock_map_cache


# 해외주식 하드코딩 매퍼 (캐시에 없는 해외 종목용)
FOREIGN_STOCK_DB = {
    "에퀴닉스(소수)": "EQIX",
    "에퀴닉스": "EQIX",
    "애플": "AAPL",
    "테슬라": "TSLA",
    "엔비디아": "NVDA",
    "마이크로소프트": "MSFT",
}


import sys
# stock_mapper.py를 import하기 위해 프로젝트 루트를 path에 추가
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _refresh_stock_map():
    """stock_mapper.py의 update 로직을 호출하여 캐시를 갱신"""
    global _stock_map_cache
    try:
        from utils.stock_mapper import get_access_token, update_stock_map
        print("[StockMapper] Cache miss — auto-refreshing stock map...")
        token = get_access_token()
        if token:
            update_stock_map(token)
            # 캐시 재로드
            _stock_map_cache = None
            return _load_stock_map()
        else:
            print("[StockMapper] Failed to get access token for auto-refresh")
    except Exception as e:
        print(f"[StockMapper] Auto-refresh failed: {e}")
    return {}


async def get_stock_code(name: str) -> str:
    """종목명 → 종목코드 매퍼.
    1순위: stock_mapper.py가 생성한 캐시 (cache/stock_map.json)
    2순위: 해외주식 하드코딩 DB
    3순위: 캐시 자동 갱신 후 재시도
    4순위: 이름 해시 기반 fallback
    """
    # 1. 캐시에서 조회 (국내 주식/ETF)
    stock_map = _load_stock_map()
    if name in stock_map:
        return stock_map[name]

    # 2. 해외주식 DB
    if name in FOREIGN_STOCK_DB:
        return FOREIGN_STOCK_DB[name]

    # 3. 캐시에 없으면 자동 갱신 후 재시도
    refreshed_map = _refresh_stock_map()
    if name in refreshed_map:
        return refreshed_map[name]

    # 4. fallback
    print(f"[StockMapper] Warning: '{name}' not found even after refresh")
    return str(hash(name))[-6:].zfill(6)


async def get_daily_ohlcv(code: str) -> pd.DataFrame:
    """일봉 OHLCV 데이터 조회.
    - 해외주식 (알파벳 티커): Yahoo Finance API
    - 국내주식/ETF (숫자 코드): 키움 REST API ka10081 또는 Mock
    """
    is_foreign = not code.isdigit()

    # ── 해외주식: Yahoo Finance ──
    if is_foreign:
        try:
            ticker = yf.Ticker(code)
            df = ticker.history(period="1mo")
            if df.empty:
                return pd.DataFrame()

            df = df.reset_index()
            df = df.rename(columns={
                'Date': 'date', 'Open': 'open', 'High': 'high',
                'Low': 'low', 'Close': 'close', 'Volume': 'volume'
            })
            return df[['date', 'open', 'high', 'low', 'close', 'volume']]
        except Exception as e:
            print(f"[yfinance] Error for {code}: {e}")
            return pd.DataFrame()

    # ── 국내: Mock 모드 ──
    if MOCK_MODE:
        return _generate_mock_ohlcv(code)

    # ── 국내: 키움 REST API 실 연동 ──
    return await _fetch_kiwoom_daily_chart(code)


async def _fetch_kiwoom_daily_chart(code: str) -> pd.DataFrame:
    """키움증권 REST API ka10081 (주식일봉차트조회) 실 호출.

    Request:
        POST {KIWOOM_DOMAIN}/api/dostk/chart
        Header: api-id=ka10081, authorization=Bearer {token}
        Body: stk_cd, base_dt (YYYYMMDD), upd_stkpc_tp (0|1)

    Response:
        stk_dt_pole_chart_qr: [{dt, open_pric, high_pric, low_pric, cur_prc, trde_qty}, ...]
    """
    url = f"{KIWOOM_DOMAIN}/api/dostk/chart"
    headers = {
        "api-id": "ka10081",
        "authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json;charset=UTF-8",
    }
    payload = {
        "stk_cd": code,
        "base_dt": datetime.now().strftime("%Y%m%d"),
        "upd_stkpc_tp": "1",  # 수정주가 적용
    }

    try:
        all_records = []
        cont_yn = ""
        next_key = ""

        # 연속조회 루프 (ATR(14) 계산에 최소 14 영업일 필요)
        for _ in range(3):
            if cont_yn == "Y":
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            response = requests.post(
                url, headers=headers, json=payload,
                verify=certifi.where(), timeout=10
            )
            response.raise_for_status()
            data = response.json()

            chart_list = data.get("stk_dt_pole_chart_qr", [])
            if not chart_list:
                break

            all_records.extend(chart_list)

            # 20일 이상 확보하면 충분
            if len(all_records) >= 20:
                break

            # 연속조회 가능 여부
            cont_yn = response.headers.get("cont-yn", "N")
            next_key = response.headers.get("next-key", "")
            if cont_yn != "Y":
                break

        if not all_records:
            print(f"[Kiwoom] No chart data returned for {code}")
            return pd.DataFrame()

        # OHLCV DataFrame 변환
        df = pd.DataFrame(all_records)
        df = df.rename(columns={
            'dt': 'date',
            'open_pric': 'open',
            'high_pric': 'high',
            'low_pric': 'low',
            'cur_prc': 'close',
            'trde_qty': 'volume',
        })

        # 문자열 → 숫자 (키움 응답에 부호 '+'/'-' 포함 가능)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace('+', '', regex=False),
                    errors='coerce'
                )

        # 날짜 변환 및 시간순 정렬
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
            df = df.sort_values('date').reset_index(drop=True)

        return df[['date', 'open', 'high', 'low', 'close', 'volume']]

    except requests.exceptions.RequestException as e:
        print(f"[Kiwoom] API request failed for {code}: {e}")
        return pd.DataFrame()
    except Exception as e:
        print(f"[Kiwoom] Unexpected error for {code}: {e}")
        return pd.DataFrame()


def _generate_mock_ohlcv(code: str) -> pd.DataFrame:
    """Mock OHLCV 데이터 생성기 (UI 테스트용)"""
    end_date = datetime.now()
    dates = pd.date_range(end=end_date, periods=30, freq='B')

    base_price = 10000 + (hash(code) % 90000)

    data = []
    curr_p = base_price
    for d in dates:
        change = curr_p * np.random.normal(0, 0.02)
        open_p = curr_p + np.random.normal(0, 0.005) * curr_p

        close_p = curr_p + change
        high_p = max(open_p, close_p) + abs(np.random.normal(0, 0.01) * curr_p)
        low_p = min(open_p, close_p) - abs(np.random.normal(0, 0.01) * curr_p)

        data.append({
            "date": d, "open": open_p, "high": high_p,
            "low": low_p, "close": close_p,
            "volume": int(np.random.uniform(10000, 1000000))
        })
        curr_p = close_p

    return pd.DataFrame(data)

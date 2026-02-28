import os
import sys
import json
import time
import requests
import certifi
from datetime import datetime

sys.path.append(os.getcwd())
from utils.config import get_logger

KIWOOM_CACHE_DIR = os.path.join(os.getcwd(), "cache_kiwoom")
logger = get_logger("kiwoom_api_client", "kiwoom_api_client.log")

try:
    from backend.kiwoom.auth import get_token as _get_token
except ImportError:
    logger.warning("Could not import backend.kiwoom.auth.get_token. Falling back to basic token reader.")
    def _get_token() -> str:
        token_path = os.path.join(os.getcwd(), "token.json")
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("access_token", "")
        except Exception as e:
            logger.error(f"Failed to load Kiwoom token: {e}")
            return ""

def _get_headers(api_id: str, token: str) -> dict:
    return {
        "api-id": api_id,
        "authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8"
    }

def fetch_kiwoom_minute_data(stk_cd: str, required_date_int: int = None, is_nxt: bool = False, base_date_int: int = None):
    """
    Fetch minute chart data via Kiwoom REST API (ka10080).
    stk_cd: 6-digit stock code (e.g., 'A005930' or '005930'). If Daishin prefixed 'A', we strip it.
    is_nxt: True if ATS_Nextrade == 'Y'
    required_date_int: YYYYMMDD integer. Stop fetching if we reach older dates.
    base_date_int: YYYYMMDD integer. The starting (newest) date to fetch backwards from.
    """
    # Clean stock code
    clean_cd = stk_cd.replace("A", "")
    
    # Apply NXT suffix if needed
    req_stk_cd = f"{clean_cd}_NX" if is_nxt else clean_cd
    
    if not os.path.exists(KIWOOM_CACHE_DIR):
        os.makedirs(KIWOOM_CACHE_DIR)
        
    cache_file = os.path.join(KIWOOM_CACHE_DIR, f"{req_stk_cd}_raw.json")
    cache_data = None
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
        except:
            cache_data = None
            
    if cache_data and len(cache_data) > 0:
        dates = [int(item['date']) for item in cache_data if 'date' in item]
        if not dates:
            cache_data = None
        else:
            first_cached_date = min(dates)   # 진짜 Oldest
            last_cached_date = max(dates)    # 진짜 Newest
        
        needed_oldest = required_date_int if required_date_int else first_cached_date
        
        today_int = int(datetime.now().strftime("%Y%m%d"))
        needed_newest = base_date_int if base_date_int else last_cached_date
        needed_newest = min(needed_newest, today_int)
        
        if first_cached_date <= needed_oldest and last_cached_date >= needed_newest:
            logger.info(f"Local Kiwoom cache for {req_stk_cd} satisfies {needed_oldest}-{needed_newest}. Skipping API call.")
            return cache_data

    logger.info(f"Downloading historical minute data for {req_stk_cd} from Kiwoom REST API...")
    
    token = _get_token()
    if not token:
        logger.error("No valid token found. Please authenticate Kiwoom API first (generate token.json).")
        return cache_data if cache_data else None

    # Default to open api domain if using mock, but historical data is better fetched from real API.
    # We will just honor the USE_MOCK_KIWOOM flag
    is_mock = os.environ.get("USE_MOCK_KIWOOM", "0") == "1"
    domain = "https://mockapi.kiwoom.com" if is_mock else "https://api.kiwoom.com"
    
    url = f"{domain}/api/dostk/chart"
    headers = _get_headers("ka10080", token)
    
    next_key = ""
    cont_yn = "N"
    prev_next_key = None
    prev_first_dt = None
    
    all_fetched = []
    
    def _safe_int(val):
        if not val: return 0
        if isinstance(val, str) and not val.strip(): return 0
        try: return int(val)
        except ValueError: return 0
        
    today_int = int(datetime.now().strftime("%Y%m%d"))
    needed_newest = min((base_date_int if base_date_int else today_int), today_int)
    needed_oldest = required_date_int if required_date_int else today_int

    is_backfilling = False
    if cache_data and len(cache_data) > 0:
        dates = [int(item['date']) for item in cache_data if 'date' in item]
        first_cached_date = min(dates)
        last_cached_date = max(dates)
        
        # [최적화] 기존에는 캐시의 최신 날짜(last_cached_date)부터 백필링을 시작해서
        # 수개월의 불필요한 데이터를 모두 다운받는 비효율이 있었습니다.
        # 이제는 오직 필요한 날짜(needed_newest)부터 필요한 과거(needed_oldest)까지만 딱 타겟팅해서 가져옵니다.
        initial_base_dt = needed_newest
        logger.info(f"Targeted fetch: Requesting specifically from {initial_base_dt} down to {needed_oldest}")
    else:
        first_cached_date = 99999999
        last_cached_date = 0
        initial_base_dt = needed_newest

    while True:
        payload = {
            "stk_cd": req_stk_cd,
            "tic_scope": "1",
            "upd_stkpc_tp": "1",
            "base_dt": str(initial_base_dt)
        }
        
        if cont_yn == "Y" and next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key
        else:
            headers.pop("cont-yn", None)
            headers.pop("next-key", None)
            
        try:
            resp = requests.post(url, headers=headers, json=payload, verify=certifi.where(), timeout=10)
            if resp.status_code != 200:
                logger.error(f"Kiwoom HTTP {resp.status_code}: {resp.text}")
                break
                
            res_json = resp.json()
            
            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = str(resp.headers.get("next-key", "")).strip()
            
            if prev_next_key is not None and prev_next_key == next_key:
                 logger.warning("Pagination loop detected (same next_key). Breaking out.")
                 break
            prev_next_key = next_key
            
            chart_list = res_json.get("stk_min_pole_chart_qry", [])
            
            if not chart_list:
                logger.info("No more chart records returned by Kiwoom.")
                break
                
            first_dt = chart_list[0].get("cntr_tm", "") if len(chart_list) > 0 else ""
            if prev_first_dt is not None and first_dt == prev_first_dt and first_dt != "":
                logger.warning(f"Pagination loop detected (first item {first_dt} did not change). Breaking out.")
                break
            prev_first_dt = first_dt
                
            reached_old_data = False
            items_this_page = 0
            
            for row in chart_list:
                cntr_tm_str = row.get("cntr_tm", "")
                if len(cntr_tm_str) >= 12:
                    d_str = cntr_tm_str[:8]
                    # Kiwoom format: YYYYMMDDHHMMSS -> time is HHMM
                    t_str = cntr_tm_str[8:12] 
                else:
                    d_str = payload["base_dt"]
                    t_str = "0000"
                    
                d_int = int(d_str)
                
                # Check date boundary
                if required_date_int is not None and d_int < required_date_int:
                    reached_old_data = True
                    break
                    
                item = {
                    "date": d_int,
                    "time": int(t_str),
                    "open": abs(_safe_int(row.get("open_pric", 0))),
                    "high": abs(_safe_int(row.get("high_pric", 0))),
                    "low": abs(_safe_int(row.get("low_pric", 0))),
                    "close": abs(_safe_int(row.get("cur_prc", 0))),
                    "volume": _safe_int(row.get("trde_qty", 0))
                }
                all_fetched.append(item)
                items_this_page += 1
                
            logger.info(f"Fetched {items_this_page} new minute records for {req_stk_cd}.")
            
            if reached_old_data:
                logger.info(f"Reached date boundary {required_date_int}. Stopping fetch.")
                break
                
            # [주의] Forward-filling(최신 데이터를 앞쪽으로 붙일 때)만 아래 조기종료 작동
            if cache_data and not is_backfilling and initial_base_dt > last_cached_date:
                if chart_list:
                    page_oldest_dt_str = chart_list[-1].get("cntr_tm", "")
                    if len(page_oldest_dt_str) >= 8:
                        page_oldest_date = int(page_oldest_dt_str[:8])
                        if page_oldest_date <= last_cached_date:
                            logger.info(f"Reached cached data boundary {last_cached_date}. Stopping fetch early.")
                            break
                
            if cont_yn != "Y" or not next_key:
                break
                
            time.sleep(0.5) # Anti-ban rate limit protection
            
        except Exception as e:
            logger.error(f"Kiwoom ka10080 Request Failed: {e}")
            break
            
    # Kiwoom returns descending (newest first). Let's reverse it to chronological.
    all_fetched.reverse()
    
    if cache_data:
        merged = cache_data + all_fetched
        seen = set()
        final_data = []
        # Keep latest duplicate if any
        for item in reversed(merged):
            key = (item['date'], item['time'])
            if key not in seen:
                seen.add(key)
                final_data.append(item)
        final_data.reverse()
    else:
        final_data = all_fetched
        
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(final_data, f)
    except Exception as e:
        logger.warning(f"Failed to write Kiwoom cache for {req_stk_cd}: {e}")
        
    return final_data

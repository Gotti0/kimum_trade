import os
import sys
import requests
import json
import logging

sys.path.append(os.getcwd())
from utils.config import DAISHIN_BRIDGE_URL, DAISHIN_CACHE_DIR, DAISHIN_MAX_MINUTE_COUNT, get_logger

logger = get_logger("daishin_api_client", "daishin_api_client.log")

def fetch_daishin_data(stk_cd, required_date_int=None):
    """Fetch raw JSON chart data from the Daishin 32-bit bridge server or local cache."""
    clean_cd = stk_cd.replace("A", "")
    if not os.path.exists(DAISHIN_CACHE_DIR):
        os.makedirs(DAISHIN_CACHE_DIR)
        
    cache_file = os.path.join(DAISHIN_CACHE_DIR, f"{clean_cd}_raw.json")
    cache_data = None
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load cache {cache_file}: {e}")
            cache_data = None
            
    since_date = None
    since_time = None
    
    # If Cache exists, find the latest date/time to do an incremental fetch
    if cache_data and len(cache_data) > 0:
        latest_record = cache_data[-1]  # Because we sorted chronologically when saving
        since_date = latest_record['date']
        since_time = latest_record['time']
        
        # [OPTIMIZATION] Skip API call if the local cache already satisfies the required date
        if required_date_int is not None and int(since_date) >= required_date_int:
            logger.info(f"Local cache for {stk_cd} satisfies required date {required_date_int} (Cache: {since_date}). Skipping Bridge Server hit.")
            return cache_data
            
        logger.info(f"Found cache for {stk_cd}. Fetching new data since {since_date} {since_time}")
            
    logger.info(f"Downloading historical minute data for {stk_cd} from Bridge Server...")
    try:
        req_params = {"stk_cd": stk_cd, "count": DAISHIN_MAX_MINUTE_COUNT}
        if since_date is not None:
            req_params["since_date"] = since_date
        if since_time is not None:
            req_params["since_time"] = since_time
            
        response = requests.get(DAISHIN_BRIDGE_URL, params=req_params, timeout=300)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                new_data = result.get("data", [])
                logger.info(f"Successfully received {len(new_data)} newly fetched records for {stk_cd}.")
                
                # Merge with cached data if it exists
                if cache_data:
                    merged_data = cache_data + new_data
                    
                    # Deduplicate based on date and time, keeping the latest one
                    seen = set()
                    final_data = []
                    # iterate from newest to oldest to keep the latest if duplicates exist
                    for item in reversed(merged_data):
                        key = (item['date'], item['time'])
                        if key not in seen:
                            seen.add(key)
                            final_data.append(item)
                    # reverse back to get chronological order
                    final_data.reverse()
                else:
                    final_data = new_data
                
                # Save merged data back to cache
                try:
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(final_data, f)
                except Exception as e:
                     logger.warning(f"Could not save cache file {cache_file}: {e}")
                     
                return final_data
            else:
                logger.error(f"API Error: {result.get('detail')}")
                if cache_data:
                    logger.warning("Returning old cached data due to API error")
                    return cache_data
                return None
        else:
            logger.error(f"HTTP Error {response.status_code}: {response.text}")
            if cache_data:
                logger.warning("Returning old cached data due to HTTP error")
                return cache_data
            return None
            
    except requests.exceptions.ConnectionError:
        logger.error("Connection Error: Is the 32-bit bridge_server.py running on port 8000?")
        if cache_data:
            logger.warning("Returning old cached data due to Connection error")
            return cache_data
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if cache_data:
            logger.warning("Returning old cached data due to Unexpected error")
            return cache_data
        return None

def fetch_daishin_info(stk_cd):
    """Fetch company metadata (Market Cap, Sector, ATS, Market) from the Daishin 32-bit bridge server or local cache."""
    clean_cd = stk_cd.replace("A", "")
    if not os.path.exists(DAISHIN_CACHE_DIR):
        os.makedirs(DAISHIN_CACHE_DIR)
        
    cache_file = os.path.join(DAISHIN_CACHE_DIR, f"{clean_cd}_info.json")
    cache_data = None
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                # For info, we just return the cache if it exists (assuming daily static)
                logger.info(f"Loaded info cache for {stk_cd}")
                return cache_data
        except Exception as e:
            logger.error(f"Failed to load info cache {cache_file}: {e}")
            cache_data = None
            
    logger.info(f"Downloading company info for {stk_cd} from Bridge Server...")
    try:
        req_params = {"stk_cd": stk_cd}
        info_url = DAISHIN_BRIDGE_URL.replace("/chart", "/info")
        response = requests.get(info_url, params=req_params, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                new_data = result.get("data", {})
                logger.info(f"Successfully received info for {stk_cd}.")
                
                # Save data to cache
                try:
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(new_data, f)
                except Exception as e:
                     logger.warning(f"Could not save info cache file {cache_file}: {e}")
                     
                return new_data
            else:
                logger.error(f"API Error for info: {result.get('detail')}")
                return None
        else:
            logger.error(f"HTTP Error {response.status_code}: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        logger.error("Connection Error: Is the 32-bit bridge_server.py running on port 8000?")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching info: {e}")
        return None

def fetch_daishin_info_batch(tickers: list):
    """
    Fetch company metadata (Market Cap, Sector, ATS, Market) for multiple stocks 
    from the Daishin 32-bit bridge server in one go (max 200).
    Saves the fetched info to local per-stock json caches for future use.
    Returns a dictionary: { "A005930": {...}, "A129920": {...} }
    """
    if not tickers:
        return {}
        
    if not os.path.exists(DAISHIN_CACHE_DIR):
        os.makedirs(DAISHIN_CACHE_DIR)
        
    logger.info(f"Downloading batch company info for {len(tickers)} stocks from Bridge Server...")
    
    try:
        info_batch_url = DAISHIN_BRIDGE_URL.replace("/chart", "/info_batch")
        payload = {"tickers": tickers}
        response = requests.post(info_batch_url, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                batch_data = result.get("data", {})
                logger.info(f"Successfully received batch info for {len(batch_data)} stocks.")
                
                # Save each entry to its own cache file for future individual calls
                for stk_cd, new_data in batch_data.items():
                    clean_cd = stk_cd.replace("A", "")
                    cache_file = os.path.join(DAISHIN_CACHE_DIR, f"{clean_cd}_info.json")
                    try:
                        with open(cache_file, 'w', encoding='utf-8') as f:
                            json.dump(new_data, f)
                    except Exception as e:
                         logger.warning(f"Could not save info cache file {cache_file} for {stk_cd}: {e}")
                         
                return batch_data
            else:
                logger.error(f"API Error for batch info: {result.get('detail')}")
                return {}
        else:
            logger.error(f"HTTP Error {response.status_code}: {response.text}")
            return {}
            
    except requests.exceptions.ConnectionError:
        logger.error("Connection Error: Is the 32-bit bridge_server.py running on port 8000?")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error fetching batch info: {e}")
        return {}

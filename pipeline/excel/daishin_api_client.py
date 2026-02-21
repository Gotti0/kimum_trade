import os
import sys
import requests
import json
import logging

sys.path.append(os.getcwd())
from utils.config import DAISHIN_BRIDGE_URL, DAISHIN_CACHE_DIR, DAISHIN_MAX_MINUTE_COUNT, get_logger

logger = get_logger("daishin_api_client", "daishin_api_client.log")

def fetch_daishin_data(stk_cd):
    """Fetch raw JSON chart data from the Daishin 32-bit bridge server or local cache."""
    if not os.path.exists(DAISHIN_CACHE_DIR):
        os.makedirs(DAISHIN_CACHE_DIR)
        
    cache_file = os.path.join(DAISHIN_CACHE_DIR, f"{stk_cd}_raw.json")
    if os.path.exists(cache_file):
        logger.info(f"Loading {stk_cd} data from local cache: {cache_file}")
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load cache {cache_file}: {e}")
            # Fall through to download if cache loading fails
            
    logger.info(f"Downloading historical minute data for {stk_cd} from Bridge Server...")
    try:
        response = requests.get(DAISHIN_BRIDGE_URL, params={"stk_cd": stk_cd, "count": DAISHIN_MAX_MINUTE_COUNT}, timeout=300)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                data = result.get("data", [])
                logger.info(f"Successfully received {len(data)} records for {stk_cd}.")
                
                # Save to cache
                try:
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f)
                except Exception as e:
                     logger.warning(f"Could not save cache file {cache_file}: {e}")
                     
                return data
            else:
                logger.error(f"API Error: {result.get('detail')}")
                return None
        else:
            logger.error(f"HTTP Error {response.status_code}: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        logger.error("Connection Error: Is the 32-bit bridge_server.py running on port 8000?")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None

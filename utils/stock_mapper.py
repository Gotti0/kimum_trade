import os
import requests
import json
import logging
import argparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

APP_KEY = os.getenv('appkey')
SECRET_KEY = os.getenv('secretkey')

# Constants
BASE_URL = "https://api.kiwoom.com"
CACHE_DIR = "cache"
STOCK_MAP_FILE = os.path.join(CACHE_DIR, "stock_map.json")

# Configure Logging
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_filename = os.path.join(LOG_DIR, "stock_mapper.log")

# Create logger
logger = logging.getLogger("stock_mapper")
logger.setLevel(logging.INFO)

# File handler
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

def get_access_token():
    url = f"{BASE_URL}/oauth2/token"
    headers = {
        "Content-Type": "application/json;charset=UTF-8"
    }
    data = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "secretkey": SECRET_KEY
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json().get('token')
        else:
            logger.error(f"Failed to get access token: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Exception during token generation: {e}")
        return None

def fetch_stock_list_for_market(token, market_type):
    """
    Fetches stock list for a given market type (e.g., "0" for KOSPI, "10" for KOSDAQ).
    Handles pagination using 'cont-yn' and 'next-key' headers.
    """
    url = f"{BASE_URL}/api/dostk/stkinfo"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "api-id": "ka10099"
    }
    
    params = {
        "mrkt_tp": market_type
    }
    
    all_stocks = []
    next_key = None
    
    while True:
        if next_key:
            headers["next-key"] = next_key
            
        try:
            response = requests.post(url, headers=headers, json=params)
            
            if response.status_code == 200:
                data = response.json()
                stock_list = data.get('list', [])
                all_stocks.extend(stock_list)
                
                logger.info(f"Fetched {len(stock_list)} stocks for market {market_type}. Total so far: {len(all_stocks)}")
                
                # Check pagination
                cont_yn = response.headers.get('cont-yn', 'N')
                if cont_yn == 'Y':
                    next_key = response.headers.get('next-key')
                    if not next_key:
                        logger.warning("Cont-yn is Y but next-key is missing. Stopping pagination.")
                        break
                else:
                    break
            else:
                logger.error(f"Failed to fetch stock list: {response.text}")
                break
                
        except Exception as e:
            logger.error(f"Exception during stock list fetch: {e}")
            break
            
    return all_stocks

def update_stock_map(token):
    """
    Fetches KOSPI and KOSDAQ lists and saves name -> code mapping to cache.
    """
    stock_map = {}
    
    # 0: KOSPI
    logger.info("Fetching KOSPI stock list...")
    kospi_list = fetch_stock_list_for_market(token, "0")
    for item in kospi_list:
        name = item.get('name')
        code = item.get('code')
        if name and code:
            stock_map[name] = code
            
    # 10: KOSDAQ
    logger.info("Fetching KOSDAQ stock list...")
    kosdaq_list = fetch_stock_list_for_market(token, "10")
    for item in kosdaq_list:
        name = item.get('name')
        code = item.get('code')
        if name and code:
            stock_map[name] = code # Overwrite if same name exists (unlikely between markets, but logic accepts it)

    logger.info(f"Total mapped stocks: {len(stock_map)}")
    
    # Save to cache
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        
    try:
        with open(STOCK_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(stock_map, f, indent=4, ensure_ascii=False)
        logger.info(f"Stock map saved to {STOCK_MAP_FILE}")
    except Exception as e:
        logger.error(f"Failed to save stock map: {e}")

def get_code_by_name(name):
    """
    Loads stock map from cache and returns code for the given name.
    """
    if not os.path.exists(STOCK_MAP_FILE):
        logger.error(f"Stock map file not found at {STOCK_MAP_FILE}. Please run with --update first.")
        return None
        
    try:
        with open(STOCK_MAP_FILE, 'r', encoding='utf-8') as f:
            stock_map = json.load(f)
            
        return stock_map.get(name)
    except Exception as e:
        logger.error(f"Failed to load stock map: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Map stock names to codes using Kiwoom API.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--update", action="store_true", help="Update the stock map cache from API")
    group.add_argument("--name", help="Get stock code for the given name")
    
    args = parser.parse_args()
    
    if args.update:
        if not APP_KEY or not SECRET_KEY:
            logger.error("APP_KEY or SECRET_KEY not found in .env")
            return
            
        logger.info("Getting access token...")
        token = get_access_token()
        if token:
            update_stock_map(token)
            
    elif args.name:
        code = get_code_by_name(args.name)
        if code:
            print(f"Stock Code for '{args.name}': {code}")
        else:
            print(f"Stock code not found for '{args.name}'. Try running --update to refresh the list.")

if __name__ == "__main__":
    main()

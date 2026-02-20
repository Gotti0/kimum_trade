import sys
import argparse
import os
import requests
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure Logging
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_filename = os.path.join(LOG_DIR, "fetch_samsung_chart.log")

# Create logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# File handler
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

APP_KEY = os.getenv('appkey')
SECRET_KEY = os.getenv('secretkey')

# Constants
BASE_URL = "https://api.kiwoom.com"  # Real trading server
CACHE_DIR = "cache"

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

def fetch_minute_chart(token, stk_cd, date):
    url = f"{BASE_URL}/api/dostk/chart" 
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "api-id": "ka10080"
    }
    
    params = {
        "stk_cd": stk_cd,
        "tic_scope": "1", # 1 minute
        "upd_stkpc_tp": "1", # Adjusted price
        "base_dt": date # YYYYMMDD
    }
    
    try:
        response = requests.post(url, headers=headers, json=params)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Failed to fetch chart data: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Exception during chart fetch: {e}")
        return None

def save_to_cache(data, filename):
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    filepath = os.path.join(CACHE_DIR, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Data saved to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save cache file {filepath}: {e}")


def get_stock_data(token, stk_cd, target_date):
    """
    Fetches minute chart data and extracts specific time points.
    Returns a dictionary with extracted data or None on failure.
    """
    logger.info(f"Fetching minute chart for {stk_cd} on {target_date}...")
    data = fetch_minute_chart(token, stk_cd, target_date)
    
    if data:
        # Save raw data
        save_to_cache(data, f"{stk_cd}_{target_date}_raw.json")
        
        try:
            chart_data = data.get('stk_min_pole_chart_qry', [])
            logger.info(f"Total records: {len(chart_data)}")
            
            # Sort by time to be sure
            chart_data.sort(key=lambda x: x['cntr_tm'])
            
            # Times to extract
            target_times = {
                "091700": "open_pric",
                "091800": "cur_prc",
                "091900": "cur_prc",
                "092000": "cur_prc"
            }
            
            extracted_data = {}
            
            for item in chart_data:
                time_str = item['cntr_tm'][-6:] # Extract HHMMSS
                
                # Special handling for 091700 to get both Open and Close
                if time_str == "091700":
                    open_val = item.get("open_pric")
                    close_val = item.get("cur_prc")
                    
                    label = f"{time_str[:2]}:{time_str[2:4]}"
                    
                    extracted_data["091700_OPEN"] = {
                        "time": label,
                        "type": "Open",
                        "value": open_val,
                        "raw_field": "open_pric"
                    }
                    extracted_data["091700_CLOSE"] = {
                        "time": label,
                        "type": "Close",
                        "value": close_val,
                        "raw_field": "cur_prc"
                    }
                    logger.info(f"Found {label} (Open): {open_val}, (Close): {close_val}")

                elif time_str in target_times:
                    field = target_times[time_str]
                    value = item.get(field)
                    
                    label = f"{time_str[:2]}:{time_str[2:4]}"
                    type_label = "Close" # All others are Close
                    
                    logger.info(f"Found {label} ({type_label}): {value}")
                    
                    extracted_data[time_str] = {
                        "time": label,
                        "type": type_label,
                        "value": value,
                        "raw_field": field
                    }
            
            # Save extracted data
            save_to_cache(extracted_data, f"{stk_cd}_{target_date}_extracted.json")
            return extracted_data
                
        except Exception as e:
            logger.error(f"Error processing data for {target_date}: {e}")
            return None
    return None

def process_date(token, stk_cd, target_date):
    # Wrapper for backward compatibility or direct usage, just calls get_stock_data
    get_stock_data(token, stk_cd, target_date)

def main():
    parser = argparse.ArgumentParser(description="Fetch minute chart data from Kiwoom API.")
    parser.add_argument("--code", required=True, help="Stock code (e.g., 005930)")
    parser.add_argument("start_date", help="Start date (YYYYMMDD)")
    parser.add_argument("end_date", nargs="?", help="End date (YYYYMMDD) [Optional]")
    
    args = parser.parse_args()
    
    stk_cd = args.code
    start_date_str = args.start_date
    end_date_str = args.end_date if args.end_date else start_date_str
    
    # Validate dates
    try:
        start_dt = datetime.strptime(start_date_str, "%Y%m%d")
        end_dt = datetime.strptime(end_date_str, "%Y%m%d")
    except ValueError:
        logger.error("Error: Dates must be in YYYYMMDD format.")
        return
        
    if start_dt > end_dt:
        logger.error("Error: Start date cannot be after end date.")
        return

    if not APP_KEY or not SECRET_KEY:
        logger.error("APP_KEY or SECRET_KEY not found in .env")
        return

    logger.info("Getting access token...")
    token = get_access_token()
    
    if not token:
        return

    current_dt = start_dt
    while current_dt <= end_dt:
        current_date_str = current_dt.strftime("%Y%m%d")
        process_date(token, stk_cd, current_date_str)
        current_dt += timedelta(days=1)


if __name__ == "__main__":
    main()

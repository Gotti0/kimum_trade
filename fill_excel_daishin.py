import pandas as pd
import os
import sys
import re
import logging
import requests
import json
from datetime import datetime

# Import stock mapper
sys.path.append(os.getcwd())
try:
    from stock_mapper import get_code_by_name
except ImportError as e:
    print(f"Error importing modules: {e}. Please ensure stock_mapper.py exists in the current directory.")
    sys.exit(1)

# Configure logging
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "fill_excel_daishin.log"),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger("fill_excel_daishin")
logger.addHandler(logging.StreamHandler())

# Constants
BRIDGE_URL = "http://localhost:8000/api/dostk/chart"
CACHE_DIR = "cache_daishin"
# Daishin returns maximum ~18.5k minutes per stock, taking ~10-15 seconds. Let's just request max to ensure we get 1-year ago data.
MAX_MINUTE_COUNT = 180000 

def parse_date(date_str, current_year):
    """
    Parses 'M.D.' or 'MM.DD.' string into int YYYYMMDD for comparison.
    Returns (yyyymmdd_int, updated_year, month_int)
    """
    if pd.isna(date_str):
        return None, current_year, None

    date_str = str(date_str).strip()
    match = re.match(r"(\d{1,2})\.(\d{1,2})\.?", date_str)
    
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        return int(f"{current_year}{month:02d}{day:02d}"), current_year, month
    return None, current_year, None

def clean_price(value):
    try:
        if pd.isna(value):
            return None
        if isinstance(value, str):
            return abs(int(value.strip()))
        return abs(int(value))
    except Exception:
        return value

def fetch_daishin_data(stk_cd):
    """Fetch raw JSON chart data from the Daishin 32-bit bridge server or local cache."""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        
    cache_file = os.path.join(CACHE_DIR, f"{stk_cd}_raw.json")
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
        response = requests.get(BRIDGE_URL, params={"stk_cd": stk_cd, "count": MAX_MINUTE_COUNT}, timeout=300)
        
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

def extract_time_points(minute_data, target_date_int):
    """
    Extracts the specific OHLC values for a given date from the massive minute payload.
    time is integer HHMM (e.g., 917, 918, 919, 920) or string "917"
    """
    extracted = {}
    
    # Target times to extract
    target_times = [917, 918, 919, 920]
    
    # Filter records for the specific date
    day_records = [row for row in minute_data if int(row['date']) == target_date_int]
    
    if not day_records:
         logger.warning(f"No data available for date {target_date_int}")
         return extracted
         
    for row in day_records:
        time_val = int(row['time'])
        
        if time_val == 917:
            extracted["시가"] = clean_price(row["open"])
            extracted["17분"] = clean_price(row["close"])
        elif time_val == 918:
            extracted["18분"] = clean_price(row["close"])
        elif time_val == 919:
            extracted["19분"] = clean_price(row["close"])
        elif time_val == 920:
            extracted["20분"] = clean_price(row["close"])
            
    return extracted

def fill_excel_data(input_file):
    if not os.path.exists(input_file):
        logger.error(f"Input file not found: {input_file}")
        return

    logger.info(f"Processing Excel File: {input_file}...")
    
    try:
        # Assuming header is at top row as per object_excel.xlsx
        df = pd.read_excel(input_file, header=0)
    except Exception as e:
        logger.error(f"Failed to read Excel: {e}")
        return

    # Handle typos or alternative names for date column
    for alt_name in ["날짜", "실제", "일자", "날짜 "]:
        if alt_name in df.columns and "날자" not in df.columns:
            df.rename(columns={alt_name: "날자"}, inplace=True)
            break
            
    required_cols = ["날자", "종목", "시가", "17분", "18분", "19분", "20분"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"Missing columns in Excel. Must contain: {required_cols}. Actually has: {df.columns.tolist()}")
        return

    # Cache downloaded stock data to avoid re-fetching the same stock for different days
    stock_data_cache = {}
    
    current_year = 2025 # Starting year from top of file
    prev_month = -1
    modified_count = 0

    for idx, row in df.iterrows():
        date_raw = row["날자"]
        stock_name = row["종목"]
        
        if pd.isna(date_raw) or pd.isna(stock_name):
            continue
            
        # Parse Date & Handle Year Rollback
        date_int, _, month = parse_date(date_raw, current_year)
        
        if prev_month != -1 and month > prev_month:
            # e.g., prev was 1 (Jan), now 12 (Dec) -> Year decreased
            current_year -= 1
            date_int, _, _ = parse_date(date_raw, current_year)
            logger.info(f"Year rollback triggered! Now processing year: {current_year} at row {idx}")
            
        prev_month = month
        
        if not date_int:
            continue

        # Check if we need to fill this row
        # (e.g., if everything is already filled, skip it)
        is_empty_row = pd.isna(row["시가"]) or pd.isna(row["17분"]) or pd.isna(row["20분"])
        
        if not is_empty_row:
             # Already has data
             continue

        # Get Stock Code
        # We mapped names via Kiwoom before. Wait, Daishin needs 'A' prefix for kospi/kosdaq
        raw_code = get_code_by_name(stock_name)
        if not raw_code:
            logger.warning(f"Stock map Code not found for '{stock_name}'. Skipping.")
            continue
            
        daishin_code = f"A{raw_code}"
        
        logger.info(f"Target Row {idx} [{date_int}] - {stock_name} ({daishin_code}) - Needs filling.")
        
        # 1. Fetch entire historical data (from cache or API)
        if daishin_code not in stock_data_cache:
             fetched_data = fetch_daishin_data(daishin_code)
             if fetched_data:
                 stock_data_cache[daishin_code] = fetched_data
             else:
                 stock_data_cache[daishin_code] = [] # Mark as failed/empty to avoid re-spamming API
                 
        minute_data = stock_data_cache[daishin_code]
        
        if not minute_data:
            logger.warning(f"No Data downloaded for {stock_name}. Cannot fill row {idx}.")
            continue
            
        # 2. Extract specific times for this date
        extracted = extract_time_points(minute_data, date_int)
        
        if not extracted:
             logger.warning(f"[{date_int}] OHLC data not found for {stock_name} around 09:17~09:20.")
             continue
             
        # 3. Update DataFrame
        if "시가" in extracted: df.at[idx, "시가"] = extracted["시가"]
        if "17분" in extracted: df.at[idx, "17분"] = extracted["17분"]
        if "18분" in extracted: df.at[idx, "18분"] = extracted["18분"]
        if "19분" in extracted: df.at[idx, "19분"] = extracted["19분"]
        if "20분" in extracted: df.at[idx, "20분"] = extracted["20분"]
            
        modified_count += 1
            
    # Save Results
    output_excel = os.path.splitext(input_file)[0] + "_daishin_filled.xlsx"
    output_md = os.path.splitext(input_file)[0] + "_daishin_filled.md"
    
    try:
        df.to_excel(output_excel, index=False)
        logger.info(f"Saved filled Excel to {output_excel}")
        
        # Also save MD (First 7 columns)
        df_subset = df.iloc[:, :7]
        markdown_table = df_subset.to_markdown(index=False)
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(markdown_table)
            
        logger.info(f"Saved filled Markdown to {output_md}")
        print(f"\n✅ Processing Complete! Filled {modified_count} rows.")
        print(f"📊 Result Excel: {output_excel}")
        print(f"📝 Result Markdown: {output_md}")
        
    except Exception as e:
        logger.error(f"Failed to save final outputs: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fill_excel_daishin.py <input_excel_file>")
        sys.exit(1)
        
    target_file = sys.argv[1]
    fill_excel_data(target_file)

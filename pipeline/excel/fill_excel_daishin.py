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
    from utils.stock_mapper import get_code_by_name
except ImportError as e:
    print(f"Error importing modules: {e}. Please ensure stock_mapper.py exists in the current directory.")
    sys.exit(1)

from utils.config import get_logger
from pipeline.excel.daishin_api_client import fetch_daishin_data, fetch_daishin_info, fetch_daishin_info_batch

logger = get_logger("fill_excel_daishin", "fill_excel_daishin.log")

def parse_date(date_str, current_year):
    """
    Parses 'M.D.', 'YY.M.D.', or 'YYYY.MM.DD.' string into int YYYYMMDD for comparison.
    Returns (yyyymmdd_int, updated_year, month_int)
    """
    if pd.isna(date_str):
        return None, current_year, None

    date_str = str(date_str).strip()
    match = re.match(r"(?:(\d{2}|\d{4})\.)?\s*(\d{1,2})\.\s*(\d{1,2})\.?", date_str)
    
    if match:
        year_str = match.group(1)
        month = int(match.group(2))
        day = int(match.group(3))
        
        year = current_year
        if year_str:
            if len(year_str) == 2:
                year = 2000 + int(year_str)
            else:
                year = int(year_str)
                
        return int(f"{year}{month:02d}{day:02d}"), year, month
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



def extract_time_points(minute_data, target_date_int):
    """
    Extracts the specific OHLC values for a given date from the massive minute payload.
    time is integer HHMM (e.g., 917, 918, 919, 920) or string "917"
    """
    extracted = {}
    
    # Target times to extract
    target_times = [904, 908, 911, 914, 915, 916, 917, 918, 919, 920, 921, 922, 923, 924, 925, 926, 929]
    
    # Filter records for the specific date
    day_records = [row for row in minute_data if int(row['date']) == target_date_int]
    
    if not day_records:
         return extracted
         
    # 시간 순으로 정렬하여 가장 첫 데이터의 시가를 당일 장개시 시가로 사용
    day_records = sorted(day_records, key=lambda x: int(x['time']))
    extracted["시작가"] = clean_price(day_records[0]["open"])

    for row in day_records:
        time_val = int(row['time'])
        
        if time_val == 904: extracted["4분종가"] = clean_price(row["close"])
        elif time_val == 908: extracted["8분종가"] = clean_price(row["close"])
        elif time_val == 911: extracted["11분종가"] = clean_price(row["close"])
        elif time_val == 914: extracted["14분종가"] = clean_price(row["close"])
        elif time_val == 915: extracted["15분종가"] = clean_price(row["close"])
        elif time_val == 916: extracted["16분종가"] = clean_price(row["close"])
        elif time_val == 917: extracted["17분종가"] = clean_price(row["close"])
        elif time_val == 918: extracted["18분종가"] = clean_price(row["close"])
        elif time_val == 919: extracted["19분종가"] = clean_price(row["close"])
        elif time_val == 920: extracted["20분종가"] = clean_price(row["close"])
        elif time_val == 921: extracted["21분종가"] = clean_price(row["close"])
        elif time_val == 922: extracted["22분종가"] = clean_price(row["close"])
        elif time_val == 923: extracted["23분종가"] = clean_price(row["close"])
        elif time_val == 924: extracted["24분종가"] = clean_price(row["close"])
        elif time_val == 925: extracted["25분종가"] = clean_price(row["close"])
        elif time_val == 926: extracted["26분종가"] = clean_price(row["close"])
        elif time_val == 929: extracted["29분종가"] = clean_price(row["close"])
            
    return extracted

def extract_daily_ohlc(minute_data, target_date_int):
    """
    Extracts the daily High, Low, Open, Close values for a given date from 1-minute data.
    """
    # Filter records for the specific date
    day_records = [row for row in minute_data if int(row['date']) == target_date_int]
    
    if not day_records:
         return None
         
    day_records = sorted(day_records, key=lambda x: int(x['time']))
    
    open_p = clean_price(day_records[0]["open"])
    close_p = clean_price(day_records[-1]["close"])
    
    high_p = max(clean_price(row["high"]) for row in day_records if clean_price(row["high"]) is not None)
    low_p = min(clean_price(row["low"]) for row in day_records if clean_price(row["low"]) is not None)
    
    return {
        "시가": open_p,
        "고가": high_p,
        "저가": low_p,
        "종가": close_p
    }

def fill_excel_data(input_file):
    if not os.path.exists(input_file):
        logger.error(f"Input file not found: {input_file}")
        return

    logger.info(f"Processing Excel File: {input_file}...")
    
    try:
        # Assuming header is at the second row as per new object_excel xlsx
        df = pd.read_excel(input_file, header=1)
    except Exception as e:
        logger.error(f"Failed to read Excel: {e}")
        return

    # Handle typos or alternative names for date column
    for alt_name in ["날짜", "실제", "일자", "날짜 "]:
        if alt_name in df.columns and "날자" not in df.columns:
            df.rename(columns={alt_name: "날자"}, inplace=True)
            break
            
    # Get required columns mapped safely
    # In pandas with duplicate names: '날자.1', '시가', '고가' etc.
    # To check easily, we don't strictly require ALL 51 columns but we shouldn't crash.
    required_cols = ["날자", "종목", "날자.1"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"Missing base columns in Excel. Must contain: {required_cols}")
        return

    # =============== [PHASE 3 OPTIMIZATION] PRE-FETCH COMPANY INFO ===============
    logger.info("Extracting unique stock codes for batch info pre-fetching...")
    unique_stocks = df["종목"].dropna().unique()
    unique_daishin_codes = []
    
    for stock_name in unique_stocks:
        raw_code = get_code_by_name(stock_name)
        if raw_code:
            unique_daishin_codes.append(f"A{raw_code}")
            
    # Deduplicate again just in case
    unique_daishin_codes = list(set(unique_daishin_codes))
    
    company_info_cache = {}
    CHUNK_SIZE = 200
    
    if unique_daishin_codes:
        logger.info(f"Initiating batch fetch for {len(unique_daishin_codes)} unique stocks in {len(unique_daishin_codes)//CHUNK_SIZE + 1} chunks.")
        
        for i in range(0, len(unique_daishin_codes), CHUNK_SIZE):
            chunk = unique_daishin_codes[i:i+CHUNK_SIZE]
            batch_result = fetch_daishin_info_batch(chunk)
            if batch_result:
                 company_info_cache.update(batch_result)
                 
        logger.info(f"Successfully pre-fetched company info for {len(company_info_cache)} stocks. Proceeding to main loop.")
    # =================================================================================

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
        date_int, parsed_year, month = parse_date(date_raw, current_year)
        
        if parsed_year != current_year:
            current_year = parsed_year
        elif prev_month != -1 and month and month > prev_month:
            # e.g., prev was 1 (Jan), now 12 (Dec) -> Year decreased
            current_year -= 1
            date_int, _, _ = parse_date(date_raw, current_year)
            logger.info(f"Year rollback triggered! Now processing year: {current_year} at row {idx}")
            
        prev_month = month
        
        if not date_int:
            continue
            
        # Extract target offset dates (날자.1 to 날자.5)
        dt_ints = {}
        for offset, col_name in [(1, "날자.1"), (2, "날자.2"), (3, "날자.3"), (4, "날자.4"), (5, "날자.5")]:
            if col_name in row and not pd.isna(row[col_name]):
                 # use the *current* context year for these lookaheads.
                 # (simplification: assume they are purely lookaheads within the same/next month)
                 dt_val, _, _ = parse_date(row[col_name], current_year)
                 dt_ints[offset] = dt_val

        # Get Stock Code
        raw_code = get_code_by_name(stock_name)
        if not raw_code:
            logger.warning(f"Stock map Code not found for '{stock_name}'. Skipping.")
            continue
            
        daishin_code = f"A{raw_code}"
        
        logger.info(f"Target Row {idx} [{date_int}] - {stock_name} ({daishin_code}) - Needs filling.")
        
        # 1-1. Fill Company Info (MarketCap, Sector, ATS, Market) for EVERY row
        info_data = company_info_cache.get(daishin_code)
        
        # Fallback to individual fetch if it failed in batch
        if not info_data:
            info_data = fetch_daishin_info(daishin_code)
            if info_data:
                company_info_cache[daishin_code] = info_data # 캐시에 저장하여 다음 동일 종목에서 재사용
                
        if info_data:
            if "(억원)" in df.columns:
                df.at[idx, "(억원)"] = info_data.get("MarketCap")
            if "업종" in df.columns:
                sector_val = info_data.get("Sector")
                if isinstance(sector_val, str):
                    # 불필요한 거래소 정보 접두어 제거 (예: '코스닥 기계' -> '기계')
                    sector_val = re.sub(r'^(?:코스피|코스닥|코넥스|KOSPI|KOSDAQ)\s*', '', sector_val).strip()
                df.at[idx, "업종"] = sector_val
            
            # Set MarketType (usually E column, 'Unnamed: 4')
            if "Unnamed: 4" in df.columns:
                df.at[idx, "Unnamed: 4"] = info_data.get("MarketType")
                
            # ATS (F column) might be missing a header or we add it explicitly.
            if "대체거래소" not in df.columns:
                df.insert(5, "대체거래소", "") # Insert at F (index 5)
            df.at[idx, "대체거래소"] = info_data.get("ATS_Nextrade")

        # 1-2. Fetch Minute Chart Data (from cache or API)
        if daishin_code not in stock_data_cache:
             # We pad the current target date by ~1 month (+100) to safely cover all D+1~D+5 lookaheads
             safe_required_date = date_int + 100
             fetched_data = fetch_daishin_data(daishin_code, required_date_int=safe_required_date)
             if fetched_data:
                 stock_data_cache[daishin_code] = fetched_data
             else:
                 stock_data_cache[daishin_code] = [] # Mark as failed/empty to avoid re-spamming API
                 
        minute_data = stock_data_cache[daishin_code]
        
        if not minute_data:
            logger.warning(f"No Data downloaded for {stock_name}. Cannot fill row {idx}.")
            continue
            
        # 2. Process NXT (which uses D+1 date)
        if 1 in dt_ints:
             nxt_data = extract_daily_ohlc(minute_data, dt_ints[1])
             if nxt_data:
                 if "종목.1" in df.columns: df.at[idx, "종목.1"] = daishin_code
                 if "시가" in df.columns: df.at[idx, "시가"] = nxt_data["시가"]
                 if "고가" in df.columns: df.at[idx, "고가"] = nxt_data["고가"]
                 if "종가" in df.columns: df.at[idx, "종가"] = nxt_data["종가"]
                 
        # 3. Process Minute Data and Daily Data for D+1 (날자.1)
        if 1 in dt_ints:
            extracted = extract_time_points(minute_data, dt_ints[1])
            if extracted:
                for k, v in extracted.items():
                    if k in df.columns:
                        df.at[idx, k] = v
                        
            # D+1 Daily data is mapped to "고가.1", "저가", "종가.1"
            d1_data = extract_daily_ohlc(minute_data, dt_ints[1])
            if d1_data:
                if "고가.1" in df.columns: df.at[idx, "고가.1"] = d1_data["고가"]
                if "저가" in df.columns: df.at[idx, "저가"] = d1_data["저가"]
                if "종가.1" in df.columns: df.at[idx, "종가.1"] = d1_data["종가"]
                
        # 4. Process D+2 (날자.2) -> 시가.1, 고가.2, 저가.1, 종가.2
        if 2 in dt_ints:
             d2_data = extract_daily_ohlc(minute_data, dt_ints[2])
             if d2_data:
                 if "시가.1" in df.columns: df.at[idx, "시가.1"] = d2_data["시가"]
                 if "고가.2" in df.columns: df.at[idx, "고가.2"] = d2_data["고가"]
                 if "저가.1" in df.columns: df.at[idx, "저가.1"] = d2_data["저가"]
                 if "종가.2" in df.columns: df.at[idx, "종가.2"] = d2_data["종가"]
                 
        # 5. Process D+3 (날자.3) -> 시가.2, 고가.3, 저가.2, 종가.3
        if 3 in dt_ints:
             d3_data = extract_daily_ohlc(minute_data, dt_ints[3])
             if d3_data:
                 if "시가.2" in df.columns: df.at[idx, "시가.2"] = d3_data["시가"]
                 if "고가.3" in df.columns: df.at[idx, "고가.3"] = d3_data["고가"]
                 if "저가.2" in df.columns: df.at[idx, "저가.2"] = d3_data["저가"]
                 if "종가.3" in df.columns: df.at[idx, "종가.3"] = d3_data["종가"]

        # 6. Process D+4 (날자.4) -> 시가.3, 고가.4, 저가.3, 종가.4
        if 4 in dt_ints:
             d4_data = extract_daily_ohlc(minute_data, dt_ints[4])
             if d4_data:
                 if "시가.3" in df.columns: df.at[idx, "시가.3"] = d4_data["시가"]
                 if "고가.4" in df.columns: df.at[idx, "고가.4"] = d4_data["고가"]
                 if "저가.3" in df.columns: df.at[idx, "저가.3"] = d4_data["저가"]
                 if "종가.4" in df.columns: df.at[idx, "종가.4"] = d4_data["종가"]
                 
        # 7. Process D+5 (날자.5) -> 시가.4, 고가.5, 저가.4, 종가.5
        if 5 in dt_ints:
             d5_data = extract_daily_ohlc(minute_data, dt_ints[5])
             if d5_data:
                 if "시가.4" in df.columns: df.at[idx, "시가.4"] = d5_data["시가"]
                 if "고가.5" in df.columns: df.at[idx, "고가.5"] = d5_data["고가"]
                 if "저가.4" in df.columns: df.at[idx, "저가.4"] = d5_data["저가"]
                 if "종가.5" in df.columns: df.at[idx, "종가.5"] = d5_data["종가"]
            
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

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
from pipeline.excel.kiwoom_api_client import fetch_kiwoom_minute_data
from pipeline.excel.nasdaq_client import fetch_nasdaq_close

logger = get_logger("fill_excel_kiwoom_hybrid", "fill_excel_kiwoom.log")

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

def add_minutes(time_int: int, minutes_to_add: int) -> int:
    """
    Adds or subtracts minutes from an HHMM integer format time.
    """
    hours = time_int // 100
    mins = time_int % 100
    
    total_mins = hours * 60 + mins + minutes_to_add
    
    new_hours = total_mins // 60
    new_mins = total_mins % 60
    
    return new_hours * 100 + new_mins

def detect_base_time(day_records):
    """
    Detects the starting time of the day by comparing early morning volume.
    We check the sum of volume from 09:00~09:05 vs 10:00~10:05 to account for
    delayed first-ticks (e.g. 09:02 first trade).
    """
    if not day_records:
        return 900, "9시 시작"
        
    vol_0900_window = 0
    vol_1000_window = 0
    
    for r in day_records:
        t = int(r['time'])
        if 900 <= t <= 905:
            vol_0900_window += int(r.get('volume', 0))
        elif 1000 <= t <= 1005:
            vol_1000_window += int(r.get('volume', 0))
            
    # 정규장(9시)이 정상적으로 열렸다면 09:00~09:05 거래량이 매우 큽니다.
    # 수능 등 10시 개장일인 경우에만 10:00~10:05 거래량이 9시 구간보다 압도적으로 큽니다.
    if vol_1000_window > (vol_0900_window * 5) and vol_1000_window > 1000:
        return 1000, "10시 시작"
        
    # 데이터가 비정상적으로 부족한 경우 시초 거래 시간을 확인합니다. (NXT 8시 제외)
    if vol_0900_window == 0 and vol_1000_window == 0:
        for r in day_records:
            t = int(r['time'])
            if t >= 900:  # 8시(NXT) 제외한 첫 정규 거래시간
                if t >= 1000 and t < 1100:
                    return 1000, "10시 시작"
                break
                
    return 900, "9시 시작"

def extract_time_points(minute_data, target_date_int, base_time: int):
    """
    Extracts the specific OHLC values for a given date relative to a base time.
    Extracts: 1, 2, 3, 4, 8, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 29, 30 mins after.
    Values are NOT forward filled; if data is missing, we leave it as None.
    """
    extracted = {}
    
    offsets = {
        1: "1분종가", 2: "2분종가", 3: "3분종가", 4: "4분종가",
        8: "8분종가", 11: "11분종가", 14: "14분종가", 15: "15분종가",
        16: "16분종가", 17: "17분종가", 18: "18분종가", 19: "19분종가",
        20: "20분종가", 21: "21분종가", 22: "22분종가", 23: "23분종가",
        24: "24분종가", 25: "25분종가", 26: "26분종가", 29: "29분종가",
        30: "30분종가"
    }
    
    day_records = [row for row in minute_data if int(row['date']) == target_date_int]
    if not day_records:
         return extracted
         
    day_records = sorted(day_records, key=lambda x: int(x['time']))
    
def extract_time_points(minute_data, target_date_int, base_time: int):
    """
    Extracts the specific OHLC values for a given date relative to a base time.
    Extracts: 1, 2, 3, 4, 8, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 29, 30 mins after.
    Values are NOT forward filled; if data is missing, we leave it as None.
    """
    extracted = {}
    
    offsets = {
        1: "1분종가", 2: "2분종가", 3: "3분종가", 4: "4분종가",
        8: "8분종가", 11: "11분종가", 14: "14분종가", 15: "15분종가",
        16: "16분종가", 17: "17분종가", 18: "18분종가", 19: "19분종가",
        20: "20분종가", 21: "21분종가", 22: "22분종가", 23: "23분종가",
        24: "24분종가", 25: "25분종가", 26: "26분종가", 29: "29분종가",
        30: "30분종가"
    }
    
    day_records = [row for row in minute_data if int(row['date']) == target_date_int]
    if not day_records:
         return extracted
         
    day_records = sorted(day_records, key=lambda x: int(x['time']))
    
    # Extract Open (first minute on or after base_time)
    valid_start_records = [r for r in day_records if int(r['time']) >= base_time]
    if valid_start_records:
        start_price = clean_price(valid_start_records[0]["open"])
    else:
        # Fallback to absolute first record
        start_price = clean_price(day_records[0]["open"])
        
    extracted["시작가"] = start_price

    for offset, col_name in offsets.items():
        target_time = add_minutes(base_time, offset)
        
        # Look for the exact minute match. (No fallback)
        exact_record = [r for r in day_records if int(r['time']) == target_time]
        
        if exact_record:
            extracted[col_name] = clean_price(exact_record[0]["close"])
        else:
            extracted[col_name] = None
            
    return extracted

def extract_daily_ohlc(minute_data, target_date_int):
    """
    Extracts the daily High, Low, Open, Close values for a given date from 1-minute data.
    """
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
        df = pd.read_excel(input_file, header=0)
    except Exception as e:
        logger.error(f"Failed to read Excel: {e}")
        return

    # Handle typos
    for alt_name in ["날짜", "실제", "일자", "날짜 "]:
        if alt_name in df.columns and "날자" not in df.columns:
            df.rename(columns={alt_name: "날자"}, inplace=True)
            break
            
    required_cols = ["날자", "종목", "날자.1"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"Missing base columns in Excel. Must contain: {required_cols}")
        return

    # Add "시작시간" column if not present
    if "시작시간" not in df.columns:
        # insert near the start (e.g., column index 2)
        df.insert(2, "시작시간", "")

    stock_data_cache = {}
    stock_range_cache = {}
    
    current_year = 2025
    prev_month = -1
    modified_count = 0

    for idx, row in df.iterrows():
        date_raw = row["날자"]
        stock_name = row["종목"]
        
        if pd.isna(date_raw) or pd.isna(stock_name):
            continue
            
        date_int, parsed_year, month = parse_date(date_raw, current_year)
        
        if parsed_year != current_year:
            current_year = parsed_year
        elif prev_month != -1 and month and month > prev_month:
            current_year -= 1
            date_int, _, _ = parse_date(date_raw, current_year)
            logger.info(f"Year rollback triggered! Now processing year: {current_year} at row {idx}")
            
        prev_month = month
        
        if not date_int:
            continue
            
        dt_ints = {}
        for offset, col_name in [(1, "날자.1"), (2, "날자.2"), (3, "날자.3"), (4, "날자.4"), (5, "날자.5")]:
            if col_name in row and not pd.isna(row[col_name]):
                 dt_val, _, _ = parse_date(row[col_name], current_year)
                 dt_ints[offset] = dt_val

        # Get Stock Code
        raw_code = get_code_by_name(stock_name)
        if not raw_code:
            logger.warning(f"Stock map Code not found for '{stock_name}'. Skipping.")
            continue
            
        # We fetch Kiwoom regular data for all. If ATS, we fetch it with _NX flag. 
        # But Daishin cache and info fetch has been removed. 
        # Since the user requested "Kiwoom unified source", we use clean_cd.
        clean_cd = raw_code
        logger.info(f"Target Row {idx} [{date_int}] - {stock_name} ({clean_cd}) - Needs filling.")
        
        dates_needed = [date_int] + [dt for dt in dt_ints.values() if dt]
        min_date = min(dates_needed)
        max_date = max(dates_needed)
        
        if clean_cd not in stock_range_cache:
            stock_range_cache[clean_cd] = {"min": 99999999, "max": 0}
            
        checked_min = stock_range_cache[clean_cd]["min"]
        checked_max = stock_range_cache[clean_cd]["max"]
        
        needs_fetch = False
        if clean_cd not in stock_data_cache or min_date < checked_min or max_date > checked_max:
            needs_fetch = True
            
        if needs_fetch:
            try:
                max_dt = datetime.strptime(str(max_date), "%Y%m%d")
                from datetime import timedelta
                padded_max_dt = max_dt + timedelta(days=7)
                safe_base_date = int(padded_max_dt.strftime("%Y%m%d"))
            except Exception:
                safe_base_date = max_date
                 
            # Query Kiwoom directly.
            # We first try to fetch with _NX flag as fallback if standard returns nothing.
            fetched_data = fetch_kiwoom_minute_data(clean_cd, required_date_int=min_date, is_nxt=False, base_date_int=safe_base_date)
            
            if not fetched_data:
                logger.info(f"Row {idx}: No data for standard {clean_cd}. Trying _NX fallback.")
                fetched_data = fetch_kiwoom_minute_data(clean_cd, required_date_int=min_date, is_nxt=True, base_date_int=safe_base_date)
                
            if fetched_data:
                stock_data_cache[clean_cd] = fetched_data
            else:
                stock_data_cache[clean_cd] = [] 
                 
            stock_range_cache[clean_cd]["min"] = min(stock_range_cache[clean_cd]["min"], min_date)
            stock_range_cache[clean_cd]["max"] = max(stock_range_cache[clean_cd]["max"], max_date)
                 
        minute_data = stock_data_cache[clean_cd]
        
        nasdaq_col = "나스닥종가%" if "나스닥종가%" in df.columns else "나스닥종가"
        if 1 in dt_ints:
            nasdaq_close = fetch_nasdaq_close(dt_ints[1])
            if nasdaq_close is not None:
                if nasdaq_col not in df.columns:
                    df[nasdaq_col] = None
                df.at[idx, nasdaq_col] = nasdaq_close

        if not minute_data:
            logger.warning(f"No Data downloaded for {stock_name}. Cannot fill row {idx}.")
            continue
            
        # Detect Base Time automatically based on D+1 logic or D-day logic
        detect_day_int = dt_ints[1] if 1 in dt_ints else date_int
        day_records = [r for r in minute_data if int(r['date']) == detect_day_int]
        base_time, time_label = detect_base_time(sorted(day_records, key=lambda x: int(x['time'])))
        
        # Write the detected time label
        df.at[idx, "시작시간"] = time_label

        if date_int:
             d0_data = extract_daily_ohlc(minute_data, date_int)
             if d0_data:
                 if "시가" in df.columns: df.at[idx, "시가"] = d0_data["시가"]
                 if "고가" in df.columns: df.at[idx, "고가"] = d0_data["고가"]
                 if "저가" in df.columns: df.at[idx, "저가"] = d0_data["저가"]
                 if "종가" in df.columns: df.at[idx, "종가"] = d0_data["종가"]

        if 1 in dt_ints:
             if "종목.1" in df.columns: df.at[idx, "종목.1"] = clean_cd
                 
        if 1 in dt_ints:
            extracted = extract_time_points(minute_data, dt_ints[1], base_time)
            if extracted:
                for k, v in extracted.items():
                    if k in df.columns:
                        df.at[idx, k] = v
                        
            d1_data = extract_daily_ohlc(minute_data, dt_ints[1])
            if d1_data:
                if "고가.1" in df.columns: df.at[idx, "고가.1"] = d1_data["고가"]
                if "저가" in df.columns: df.at[idx, "저가"] = d1_data["저가"]
                if "종가.1" in df.columns: df.at[idx, "종가.1"] = d1_data["종가"]
                
        if 2 in dt_ints:
             d2_data = extract_daily_ohlc(minute_data, dt_ints[2])
             if d2_data:
                 if "시가.1" in df.columns: df.at[idx, "시가.1"] = d2_data["시가"]
                 if "고가.2" in df.columns: df.at[idx, "고가.2"] = d2_data["고가"]
                 if "저가.1" in df.columns: df.at[idx, "저가.1"] = d2_data["저가"]
                 if "종가.2" in df.columns: df.at[idx, "종가.2"] = d2_data["종가"]
                 
        if 3 in dt_ints:
             d3_data = extract_daily_ohlc(minute_data, dt_ints[3])
             if d3_data:
                 if "시가.2" in df.columns: df.at[idx, "시가.2"] = d3_data["시가"]
                 if "고가.3" in df.columns: df.at[idx, "고가.3"] = d3_data["고가"]
                 if "저가.2" in df.columns: df.at[idx, "저가.2"] = d3_data["저가"]
                 if "종가.3" in df.columns: df.at[idx, "종가.3"] = d3_data["종가"]

        if 4 in dt_ints:
             d4_data = extract_daily_ohlc(minute_data, dt_ints[4])
             if d4_data:
                 if "시가.3" in df.columns: df.at[idx, "시가.3"] = d4_data["시가"]
                 if "고가.4" in df.columns: df.at[idx, "고가.4"] = d4_data["고가"]
                 if "저가.3" in df.columns: df.at[idx, "저가.3"] = d4_data["저가"]
                 if "종가.4" in df.columns: df.at[idx, "종가.4"] = d4_data["종가"]
                 
        if 5 in dt_ints:
             d5_data = extract_daily_ohlc(minute_data, dt_ints[5])
             if d5_data:
                 if "시가.4" in df.columns: df.at[idx, "시가.4"] = d5_data["시가"]
                 if "고가.5" in df.columns: df.at[idx, "고가.5"] = d5_data["고가"]
                 if "저가.4" in df.columns: df.at[idx, "저가.4"] = d5_data["저가"]
                 if "종가.5" in df.columns: df.at[idx, "종가.5"] = d5_data["종가"]
            
        modified_count += 1
            
    output_excel = os.path.splitext(input_file)[0] + "_kiwoom_filled.xlsx"
    output_md = os.path.splitext(input_file)[0] + "_kiwoom_filled.md"
    
    try:
        df.to_excel(output_excel, index=False)
        logger.info(f"Saved filled Excel to {output_excel}")
        
        markdown_table = df.to_markdown(index=False)
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

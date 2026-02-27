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
from pipeline.excel.kiwoom_api_client import fetch_kiwoom_minute_data

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
    Ex: add_minutes(900, -3) -> 857
    Ex: add_minutes(859, 2) -> 901
    """
    hours = time_int // 100
    mins = time_int % 100
    
    total_mins = hours * 60 + mins + minutes_to_add
    
    new_hours = total_mins // 60
    new_mins = total_mins % 60
    
    return new_hours * 100 + new_mins


def extract_time_points(minute_data, target_date_int, base_time: int):
    """
    Extracts the specific OHLC values for a given date relative to a base time.
    base_time: e.g., 800 (8:00), 900 (9:00), 1000 (10:00).
    Extracts: 1, 2, 3, 4, 8, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 29, 30 mins after.
    """
    extracted = {}
    
    # Offsets required: (mins_after)
    offsets = {
        1: "1분종가",
        2: "2분종가",
        3: "3분종가",
        4: "4분종가",
        8: "8분종가",
        11: "11분종가",
        14: "14분종가",
        15: "15분종가",
        16: "16분종가",
        17: "17분종가",
        18: "18분종가",
        19: "19분종가",
        20: "20분종가",
        21: "21분종가",
        22: "22분종가",
        23: "23분종가",
        24: "24분종가",
        25: "25분종가",
        26: "26분종가",
        29: "29분종가",
        30: "30분종가"
    }
    
    # Filter records for the specific date
    day_records = [row for row in minute_data if int(row['date']) == target_date_int]
    
    if not day_records:
         return extracted
         
    # 정렬
    day_records = sorted(day_records, key=lambda x: int(x['time']))
    
    # 시작가(Open) 할당 (해당일 전체 데이터 중 가장 첫 데이터의 시가 사용 - base_time 근접 데이터 우선, 아니면 안전하게 그냥 첫 데이터)
    # 조금 더 안전하게 base_time 이후의 첫 거래 데이터를 시가로 잡음 (VI 등으로 9시 2분 시작 시 대응)
    valid_start_records = [r for r in day_records if int(r['time']) >= base_time]
    if valid_start_records:
        extracted["시작가"] = clean_price(valid_start_records[0]["open"])
    else:
        extracted["시작가"] = clean_price(day_records[0]["open"]) # Fallback

    # 각 offset에 해당하는 종가 추출
    for offset, col_name in offsets.items():
        target_time = add_minutes(base_time, offset)
        
        # target_time 이하의 가장 최근 분봉 데이터 검색 (결측치 방어로 이전 분봉 종가 끌고오기)
        valid_rows = [r for r in day_records if int(r['time']) <= target_time]
        
        if valid_rows:
            # day_records는 이미 시간 오름차순 정렬이 되어 있으므로, 마지막 원소가 가장 가까운 과거 데이터
            extracted[col_name] = clean_price(valid_rows[-1]["close"])
        else:
            extracted[col_name] = None # 해당 시간 이전에 거래 데이터가 아예 없는 경우
            
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
        # Assuming header is at the top row (header=0)
        df = pd.read_excel(input_file, header=0)
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

        # NXT 종목 판별 (Y/N)
        ats_val = str(df.at[idx, "대체거래소"]).strip().upper()

        # 1-2. Fetch Minute Chart Data (from cache or API)
        if daishin_code not in stock_data_cache:
             # We determine the exact date range needed including D+1~D+5 lookaheads
             dates_needed = [date_int] + [dt for dt in dt_ints.values() if dt]
             min_date = min(dates_needed)
             max_date = max(dates_needed)
             
             # Pad max_date by ~7 days to ensure consecutive rows in the same spreadsheet are covered
             try:
                 max_dt = datetime.strptime(str(max_date), "%Y%m%d")
                 from datetime import timedelta
                 padded_max_dt = max_dt + timedelta(days=7)
                 safe_base_date = int(padded_max_dt.strftime("%Y%m%d"))
             except Exception:
                 safe_base_date = max_date
                 
             is_nxt = (ats_val == "Y")
             if is_nxt:
                 fetched_data = fetch_kiwoom_minute_data(daishin_code, required_date_int=min_date, is_nxt=is_nxt, base_date_int=safe_base_date)
             else:
                 fetched_data = fetch_daishin_data(daishin_code, required_date_int=min_date)
                 
             if fetched_data:
                 stock_data_cache[daishin_code] = fetched_data
             else:
                 stock_data_cache[daishin_code] = [] # Mark as failed/empty to avoid re-spamming API
                 
        minute_data = stock_data_cache[daishin_code]
        
        if not minute_data:
            logger.warning(f"No Data downloaded for {stock_name}. Cannot fill row {idx}.")
            continue
            
        # 2. Base Time 결정 로직
        # 10시 시작 검사: 보통 컬럼 6(G열)에 입력되므로 전체 row 값을 확인
        is_10_am_start = False
        for val in row.values:
            if isinstance(val, str) and "10시시작" in val.replace(" ", ""):
                is_10_am_start = True
                break
        
        base_time = 900 # 기본 KRX 시작시간 (09:00)
        
        if is_10_am_start:
            base_time = 1000 # 10:00 수능 등 지연개장
        elif ats_val == "Y":
            base_time = 800  # 08:00 NXT 오픈
            
        # 3. Fallback 로직 (과거 데이터 부족 대응)
        # NXT 종목('Y')이라 800을 설정했으나, 해당 날짜의 첫 분봉이 8시 50분 이후인 경우(KRX 시절 데이터)
        if base_time == 800 and 1 in dt_ints:
            day_records = [r for r in minute_data if int(r['date']) == dt_ints[1]]
            if day_records:
                first_time = min((int(r['time']) for r in day_records))
                if first_time >= 850:
                    logger.info(f"Row {idx}: ATS='Y' but first time for {dt_ints[1]} is {first_time}. Falling back to KRX base_time 900.")
                    base_time = 900

        # 4. Process D-day (A column date)
        if date_int:
             d0_data = extract_daily_ohlc(minute_data, date_int)
             if d0_data:
                 if "시가" in df.columns: df.at[idx, "시가"] = d0_data["시가"]
                 if "고가" in df.columns: df.at[idx, "고가"] = d0_data["고가"]
                 if "저가" in df.columns: df.at[idx, "저가"] = d0_data["저가"]
                 if "종가" in df.columns: df.at[idx, "종가"] = d0_data["종가"]

        # 4-1. Process NXT (which uses D+1 date, but only '종목.1' is strictly necessary here since OHLC is handled below)
        if 1 in dt_ints:
             if "종목.1" in df.columns: df.at[idx, "종목.1"] = daishin_code
                 
        # 5. Process Minute Data and Daily Data for D+1 (날자.1)
        if 1 in dt_ints:
            extracted = extract_time_points(minute_data, dt_ints[1], base_time)
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
    output_excel = os.path.splitext(input_file)[0] + "_kiwoom_filled.xlsx"
    output_md = os.path.splitext(input_file)[0] + "_kiwoom_filled.md"
    
    try:
        df.to_excel(output_excel, index=False)
        logger.info(f"Saved filled Excel to {output_excel}")
        
        # Also save MD
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

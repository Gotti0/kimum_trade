import pandas as pd
import os
import sys
import re
import logging
from datetime import datetime
from dotenv import load_dotenv

# Import our helper modules
# Ensure we can import from current directory
sys.path.append(os.getcwd())
try:
    from scripts.exploration.fetch_samsung_chart import get_stock_data, get_access_token
    from utils.stock_mapper import get_code_by_name
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)

# Configure logging
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "fill_excel_data.log"),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger("fill_excel")
logger.addHandler(logging.StreamHandler())

def parse_date(date_str, current_year):
    """
    Parses 'M.D.' or 'MM.DD.' string into YYYYMMDD.
    Returns (yyyymmdd_str, updated_year, month_int)
    """
    if pd.isna(date_str):
        return None, current_year, None

    date_str = str(date_str).strip()
    match = re.match(r"(\d{1,2})\.(\d{1,2})\.?", date_str)
    
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        
        # Simple logic for year transition (assuming descending date order in file)
        # If we jump from e.g., Jan (1) to Dec (12), it implies year changed backwards.
        # This logic is handled by the caller maintaining 'current_year'.
        
        # However, to help the caller, we return the month.
        return f"{current_year}{month:02d}{day:02d}", current_year, month
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

def fill_excel_data(input_file):
    if not os.path.exists(input_file):
        logger.error(f"Input file not found: {input_file}")
        return

    logger.info(f"Processing {input_file}...")
    
    # 1. Access Token
    token = get_access_token()
    if not token:
        logger.error("Failed to get access token. Exiting.")
        return

    # 2. Read Excel
    # We rely on header detection or assume row 0 based on previous checks
    # For object_excel.xlsx, we verified header is at row 0.
    # To be safe, let's use the logic: if header detection in convert_excel_to_md found row 0, we assume row 0.
    # We'll just try reading with header=0.
    try:
        df = pd.read_excel(input_file, header=0)
    except Exception as e:
        logger.error(f"Failed to read Excel: {e}")
        return

    # Check required columns
    required_cols = ["날자", "종목", "시가", "17분", "18분", "19분", "20분"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"Missing columns in Excel: {missing}")
        return

    # 3. Iterate and Fill
    current_year = 2026 # Initial assumption (Shifted +1 year as per user request to fit API data range)
    prev_month = -1
    
    modified_count = 0

    for idx, row in df.iterrows():
        date_raw = row["날자"]
        stock_name = row["종목"]
        
        if pd.isna(date_raw) or pd.isna(stock_name):
            continue
            
        # Parse Date
        date_str, _, month = parse_date(date_raw, current_year)
        
        # Year adjustment logic (Descending dates assumption)
        if prev_month != -1 and month > prev_month:
            # e.g., prev was 1 (Jan), now 12 (Dec) -> Year decreased
            current_year -= 1
            # Re-parse with new year
            date_str, _, _ = parse_date(date_raw, current_year)
            logger.info(f"Year changed to {current_year} at row {idx}")
            
        prev_month = month
        
        if not date_str:
            continue

        # Get Code
        stock_code = get_code_by_name(stock_name)
        if not stock_code:
            logger.warning(f"Sort Code not found for '{stock_name}'. Skipping.")
            continue
            
        # Check if data already exists? 
        # User asked to fill blank spaces. If "시가" is not null, maybe skip?
        # But let's overwrite to ensure accuracy or checks.
        # Use pd.notna(row['시가']) to check.
        # Let's try filling even if present, or maybe strictly if empty.
        # User said "빈칸을 채워야 하는데", so focus on blanks.
        
        if pd.notna(row["시가"]) and pd.notna(row["17분"]) and pd.notna(row["20분"]):
             logger.info(f"Row {idx} ({stock_name} {date_str}) already has data. Skipping fetch.")
             continue

        logger.info(f"Processing Row {idx}: {stock_name} ({stock_code}) on {date_str}")
        
        # Fetch Data
        data = get_stock_data(token, stock_code, date_str)
        
        if data:
            # Update DataFrame
            # Mapping:
            # 시가 -> 091700_OPEN
            # 17분 -> 091700_CLOSE
            # 18분 -> 091800 (Close)
            # 19분 -> 091900 (Close)
            # 20분 -> 092000 (Close)
            
            if "091700_OPEN" in data:
                df.at[idx, "시가"] = clean_price(data["091700_OPEN"]["value"])
            if "091700_CLOSE" in data:
                df.at[idx, "17분"] = clean_price(data["091700_CLOSE"]["value"])
            if "091800" in data:
                df.at[idx, "18분"] = clean_price(data["091800"]["value"])
            if "091900" in data:
                df.at[idx, "19분"] = clean_price(data["091900"]["value"])
            if "092000" in data:
                df.at[idx, "20분"] = clean_price(data["092000"]["value"])
                
            modified_count += 1
            
    # 4. Save Result
    output_excel = os.path.splitext(input_file)[0] + "_filled.xlsx"
    output_md = os.path.splitext(input_file)[0] + "_filled.md"
    
    try:
        df.to_excel(output_excel, index=False)
        logger.info(f"Saved filled Excel to {output_excel}")
        
        # Also save MD
        # Select first 7 columns for consistency with convert_excel_to_md
        df_subset = df.iloc[:, :7]
        markdown_table = df_subset.to_markdown(index=False)
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(markdown_table)
        logger.info(f"Saved filled Markdown to {output_md}")
        
        print(f"Done. Processed {modified_count} rows.")
        print(f"Excel: {output_excel}")
        print(f"Markdown: {output_md}")
        
    except Exception as e:
        logger.error(f"Failed to save output: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fill_excel_data.py <input_excel_file>")
        sys.exit(1)
        
    fill_excel_data(sys.argv[1])

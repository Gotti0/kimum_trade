import requests
import pandas as pd
import argparse
import sys
import os
import json
from datetime import datetime

# API Server URL (FastAPI bridge running in 32-bit venv)
BRIDGE_URL = "http://localhost:8000/api/dostk/chart"
CACHE_DIR = "cache_daishin"

def fetch_data_from_bridge(stk_cd, count):
    """Fetch raw JSON data from the 32-bit bridge server."""
    print(f"Requesting {count} minute records for {stk_cd} from Bridge Server...")
    
    try:
        response = requests.get(BRIDGE_URL, params={"stk_cd": stk_cd, "count": count})
        
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                data = result.get("data", [])
                print(f"Successfully received {len(data)} records.")
                return data
            else:
                print(f"API Error: {result.get('detail')}")
                return None
        else:
            print(f"HTTP Error {response.status_code}: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        print("Connection Error: Is the 32-bit bridge_server.py running on port 8000?")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

def process_to_dataframe(raw_data):
    """Convert raw JSON list to Pandas DataFrame and format it."""
    if not raw_data:
        return None
        
    df = pd.DataFrame(raw_data)
    
    # Daishin API returns dates as integer YYYYMMDD and time as HHMM
    # Convert them to string and then to datetime
    df['date_str'] = df['date'].astype(str)
    
    # Time returned by Daishin is sometimes inherently an integer like 900 (for 09:00)
    # So we pad it with leading zeros
    df['time_str'] = df['time'].astype(str).str.zfill(4)
    
    # Create combined datetime column
    df['datetime'] = pd.to_datetime(df['date_str'] + ' ' + df['time_str'], format='%Y%m%d %H%M')
    
    # Drop intermediate columns
    df = df.drop(columns=['date_str', 'time_str', 'date', 'time'])
    
    # Reorder columns
    df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']]
    
    # Set datetime as index (optional, but good for time-series analysis)
    df.set_index('datetime', inplace=True)
    
    return df

def save_dataframe(df, stk_cd):
    """Save the processed DataFrame locally."""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        
    today_str = datetime.now().strftime("%Y%m%d")
    
    # Save as CSV for easy viewing
    csv_path = os.path.join(CACHE_DIR, f"{stk_cd}_{today_str}_1m.csv")
    df.to_csv(csv_path)
    print(f"Saved CSV to: {csv_path}")
    
    # Save as Parquet for performance and size efficiency
    try:
        parquet_path = os.path.join(CACHE_DIR, f"{stk_cd}_{today_str}_1m.parquet")
        df.to_parquet(parquet_path)
        print(f"Saved Parquet to: {parquet_path}")
    except ModuleNotFoundError:
         print("fastparquet or pyarrow not installed. Skipping parquet save.")
         
    return csv_path

def main():
    parser = argparse.ArgumentParser(description="Fetch and process Daishin 1-minute chart data via Bridge Server.")
    parser.add_argument("--code", required=True, help="Stock code (e.g., A005930 or 005930)")
    parser.add_argument("--count", type=int, default=185000, help="Number of minute records to fetch (Daishin Max ~185,000)")
    
    args = parser.parse_args()
    
    # 1. Fetch
    raw_data = fetch_data_from_bridge(args.code, args.count)
    if not raw_data:
        sys.exit(1)
        
    # 2. Process
    df = process_to_dataframe(raw_data)
    if df is None or df.empty:
        print("No valid data to process.")
        sys.exit(1)
        
    print("\nData Preview:")
    print(df.head())
    print("...")
    print(df.tail())
    print("\nData Summary:")
    print(df.info())
    
    # 3. Save
    save_dataframe(df, args.code)

if __name__ == "__main__":
    main()

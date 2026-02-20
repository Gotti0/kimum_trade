import pandas as pd
import sys

def find_header_and_print(input_file):
    try:
        # Read first 20 rows without header
        df = pd.read_excel(input_file, header=None, nrows=20)
        
        print("Searching for header row...")
        header_index = -1
        
        for idx, row in df.iterrows():
            row_str = " ".join([str(x) for x in row.values if pd.notna(x)])
            print(f"Row {idx}: {row_str}")
            
            if "종목명" in row_str or "일자" in row_str or "종목코드" in row_str:
                header_index = idx
                print(f"\nPotential header found at row index: {header_index}")
                break
        
        if header_index != -1:
            # Read again with correct header
            df_correct = pd.read_excel(input_file, header=header_index, nrows=5)
            print("\nData with correct header:")
            print(df_correct.columns.tolist())
            print(df_correct.iloc[:, :7].head().to_string())
        else:
            print("\nCould not find header row with keywords '종목명', '일자', '종목코드'.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_excel.py <input_file>")
        sys.exit(1)
    find_header_and_print(sys.argv[1])

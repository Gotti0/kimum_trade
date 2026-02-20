import pandas as pd
import sys
import os

def find_header_row(file_path):
    """
    Finds the index of the header row by searching for specific keywords.
    """
    try:
        df = pd.read_excel(file_path, header=None, nrows=10)
        for idx, row in df.iterrows():
            row_values = [str(val).strip() for val in row.values if pd.notna(val)]
            if any(keyword in row_values for keyword in ["날짜", "종목", "종목명", "일자"]):
                return idx
    except Exception as e:
        print(f"Warning: Failed to detect header row: {e}")
    return 0 # Default to 0 if not found

def convert_excel_to_md(input_file, output_file=None):
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' not found.")
        return

    try:
        # Detect header row
        header_row_index = find_header_row(input_file)
        print(f"Detected header row at index: {header_row_index}")

        # Read Excel file with correct header
        df = pd.read_excel(input_file, header=header_row_index)
        
        # Select first 7 columns (index 0 to 6)
        df_subset = df.iloc[:, :7]
        
        # Convert to Markdown
        markdown_table = df_subset.to_markdown(index=False)
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(markdown_table)
            print(f"Successfully converted '{input_file}' to '{output_file}'.")
        else:
            print(markdown_table)
            
    except Exception as e:
        print(f"Error during conversion: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_excel_to_md.py <input_excel_file> [output_md_file]")
        sys.exit(1)
        
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(input_path)[0] + ".md"
    
    convert_excel_to_md(input_path, output_path)

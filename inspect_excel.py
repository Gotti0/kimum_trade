import pandas as pd

def inspect_details():
    file1 = r'docs\25년1월 ~ 24년2월  8차분석_수기.xlsx'
    
    with open('inspect_output2.txt', 'w', encoding='utf-8') as f:
        try:
            # Read first 30 rows without treating first row as header
            df1 = pd.read_excel(file1, header=None, nrows=30)
            f.write(f"File 1 first 30 rows:\n")
            f.write(df1.to_string() + "\n\n")
        except Exception as e:
            f.write(f"Error reading {file1}: {e}\n")

if __name__ == '__main__':
    inspect_details()

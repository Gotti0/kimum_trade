import pandas as pd
import numpy as np

def clean_dataframe(df):
    # 날자나 종목이 없는 행 제거
    df = df.dropna(subset=['날자', '종목'])
    # 날자가 문자열일 경우, '복리', '계' 등의 불필요한 행 제거
    df = df[~df['날자'].astype(str).str.contains('복리|계|총합', na=False)]
    
    # 숫자 데이터 정제 (문자를 숫자로 변환, 오류시 NaN)
    cols_to_numeric = ['시가', '17분', '18분', '19분', '20분']
    for col in cols_to_numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    
    # 0.0 이나 NaN이 있을 수 있으므로 반올림 등 처리
    return df

def compare_excels():
    file_manual = r'docs\25년1월 ~ 24년2월  8차분석_수기.xlsx'
    file_auto = r'docs\object_excel_daishin_filled.xlsx'
    
    print("Loading data...")
    # 수기 파일은 10번째 줄(index 9)이 헤더
    df_manual = pd.read_excel(file_manual, header=9)
    df_auto = pd.read_excel(file_auto)
    
    # 컬럼 이름 공백 제거
    df_manual.columns = df_manual.columns.str.strip()
    df_auto.columns = df_auto.columns.str.strip()
    
    df_manual = clean_dataframe(df_manual)
    df_auto = clean_dataframe(df_auto)
    
    # 비교를 위해 키 컬럼의 공백 제거 및 문자열로 통일
    df_manual['날자'] = df_manual['날자'].astype(str).str.strip()
    df_manual['종목'] = df_manual['종목'].astype(str).str.strip()

    df_auto['날자'] = df_auto['날자'].astype(str).str.strip()
    df_auto['종목'] = df_auto['종목'].astype(str).str.strip()
    
    # Merge
    merged = pd.merge(df_manual, df_auto, on=['날자', '종목'], suffixes=('_수기', '_자동'), how='outer', indicator=True)
    
    report_lines = []
    report_lines.append("# 데이터 정합성 보고서")
    report_lines.append(f"- **수기 데이터 파일**: `{file_manual}`")
    report_lines.append(f"- **자동 채움 데이터 파일**: `{file_auto}`\n")
    
    only_manual = merged[merged['_merge'] == 'left_only']
    only_auto = merged[merged['_merge'] == 'right_only']
    both = merged[merged['_merge'] == 'both']
    
    report_lines.append("## 1. 개요")
    report_lines.append(f"- 수기 데이터 건수: {len(df_manual)}건")
    report_lines.append(f"- 자동 데이터 건수: {len(df_auto)}건")
    report_lines.append(f"- 공통 데이터 건수: {len(both)}건")
    report_lines.append(f"- 수기 데이터에만 있는 건수: {len(only_manual)}건")
    report_lines.append(f"- 자동 데이터에만 있는 건수: {len(only_auto)}건\n")
    
    # 값 비교
    columns_to_compare = ['시가', '17분', '18분', '19분', '20분']
    mismatches = []
    
    for idx, row in both.iterrows():
        mismatch_cols = []
        for col in columns_to_compare:
            val_manual = row[f"{col}_수기"]
            val_auto = row[f"{col}_자동"]
            
            # 둘다 NaN이면 동일한 것으로 간주
            if pd.isna(val_manual) and pd.isna(val_auto):
                continue
            
            # 둘중 하나만 NaN이거나 값이 다르면 불일치
            if pd.isna(val_manual) or pd.isna(val_auto) or not np.isclose(val_manual, val_auto, atol=1.0):
                mismatch_cols.append({
                    'column': col,
                    'manual': val_manual,
                    'auto': val_auto
                })
                
        if mismatch_cols:
            mismatches.append({
                'date': row['날자'],
                'stock': row['종목'],
                'details': mismatch_cols
            })
            
    report_lines.append("## 2. 데이터 값 불일치 내역")
    report_lines.append(f"공통 데이터 {len(both)}건 중, 값이 불일치하는 건수는 **{len(mismatches)}건** 입니다.\n")
    
    if mismatches:
        report_lines.append("| 날자 | 종목 | 불일치 컬럼 | 수기 데이터 | 자동 데이터 |")
        report_lines.append("|---|---|---|---|---|")
        for m in mismatches:
            date = m['date']
            stock = m['stock']
            for d in m['details']:
                col = d['column']
                val_m = d['manual']
                val_a = d['auto']
                report_lines.append(f"| {date} | {stock} | {col} | {val_m} | {val_a} |")
    else:
        report_lines.append("모든 공통 데이터의 갑이 완벽하게 일치합니다.\n")
        
    report_lines.append("\n## 3. 누락 데이터 내역")
    report_lines.append("### 수기 데이터에만 존재하는 항목 (자동 데이터에 누락)")
    if len(only_manual) > 0:
        for idx, row in only_manual.iterrows():
            report_lines.append(f"- {row['날자']} | {row['종목']}")
    else:
        report_lines.append("- 없음")
        
    report_lines.append("\n### 자동 데이터에만 존재하는 항목 (수기 데이터에 누락)")
    if len(only_auto) > 0:
        for idx, row in only_auto.head(30).iterrows(): # 너무 많을 수 있으니 상위 30개만
            report_lines.append(f"- {row['날자']} | {row['종목']}")
        if len(only_auto) > 30:
            report_lines.append(f"- ... 외 {len(only_auto) - 30}건")
    else:
        report_lines.append("- 없음")
        
    report_text = '\n'.join(report_lines)
    
    with open('docs/정합성_보고서.md', 'w', encoding='utf-8') as f:
        f.write(report_text)
        
    print("보고서 생성이 완료되었습니다: docs/정합성_보고서.md")

if __name__ == '__main__':
    compare_excels()

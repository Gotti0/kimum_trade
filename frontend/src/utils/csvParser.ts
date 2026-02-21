import Papa from 'papaparse';
import type { CSVRow, StockPosition } from '../types';

const processParsedData = (results: Papa.ParseResult<CSVRow>, stockMap: Record<string, string> = {}): StockPosition[] => {
    // 유형 컬럼이 있는 모든 행을 파싱합니다 (빈 유형은 제외)
    const simTargetTypes = ['주식', '해외주식', 'ETF', 'ETN'];

    const validPositions: StockPosition[] = results.data
        .filter((row) => row['유형'] && row['종목명'])
        .map((row) => {
            const assetType = row['유형']?.trim() || '';
            const name = row['종목명']?.trim() || '';

            // 미국 국채 패턴: "T 3.375 05/15/33" 형태
            const isUSTreasury = /^T\s+\d+\.\d+\s+\d{2}\/\d{2}\/\d{2,4}$/.test(name);
            const isUsd = assetType === '해외주식' || isUSTreasury;
            const isSimTarget = simTargetTypes.includes(assetType);
            let evalAmtStr = row['평가금액'] || row['매입금액'] || '0';
            let evalAmt = parseFloat(evalAmtStr.replace(/,/g, ''));
            if (isNaN(evalAmt)) evalAmt = 0;

            // stock_map.json에서 종목코드 조회 → 코드가 비숫자(알파벳)면 해외 티커
            const code = stockMap[name] || '';
            const ticker = code && !/^\d+$/.test(code) ? code : undefined;

            return {
                name: row['종목명']?.trim() || '',
                quantity: parseFloat((row['보유량'] || '0').replace(/,/g, '')),
                averagePrice: parseFloat((row['평균단가'] || '0').replace(/,/g, '')),
                currency: isUsd ? 'USD' : 'KRW' as 'KRW' | 'USD',
                evalAmount: evalAmt,
                assetType,
                isSimTarget,
                ticker,
            };
        })
        .filter((pos) => pos.name && !isNaN(pos.quantity));

    return validPositions;
};

export const parseMiraeAssetCSV = (file: File, stockMap: Record<string, string> = {}): Promise<StockPosition[]> => {
    return new Promise((resolve, reject) => {
        Papa.parse<CSVRow>(file, {
            header: true,
            skipEmptyLines: true,
            encoding: 'EUC-KR',
            complete: (results) => {
                try {
                    resolve(processParsedData(results, stockMap));
                } catch (error) {
                    reject(new Error('CSV 파싱 중 오류가 발생했습니다. 헤더 이름이 일치하는지 확인해 주세요.'));
                }
            },
            error: (error: any) => {
                reject(error);
            },
        });
    });
};

export const parseMiraeAssetText = (csvText: string, stockMap: Record<string, string> = {}): Promise<StockPosition[]> => {
    return new Promise((resolve, reject) => {
        const delimiter = csvText.includes('\t') ? '\t' : undefined;
        Papa.parse<CSVRow>(csvText, {
            header: true,
            skipEmptyLines: true,
            delimiter,
            complete: (results) => {
                try {
                    resolve(processParsedData(results, stockMap));
                } catch (error) {
                    reject(new Error('CSV 텍스트 파싱 중 오류가 발생했습니다. 헤더 이름이 일치하는지 확인해 주세요.'));
                }
            },
            error: (error: any) => {
                reject(error);
            },
        });
    });
};

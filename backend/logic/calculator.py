import pandas as pd
from typing import List, Dict, Any
import requests
from backend.kiwoom.api import get_stock_code, get_daily_ohlcv

def get_usd_krw_rate() -> float:
    try:
        url = "https://api.frankfurter.app/latest?from=USD&to=KRW"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            return float(res.json()['rates']['KRW'])
    except Exception:
        pass
    return 1400.0  # fallback

async def calculate_stop_loss_and_atr(capital: float, risk_percentage: float, atr_multiplier: float, positions: List[dict]) -> List[Dict[str, Any]]:
    results = []

    needs_usd = any(pos.get('currency', 'KRW') == 'USD' for pos in positions)
    usd_to_krw = get_usd_krw_rate() if needs_usd else 1.0

    for pos in positions:
        name = pos['name']
        qty = pos['quantity']
        avg_price = pos['averagePrice']
        currency = pos.get('currency', 'KRW')
        is_usd = currency == 'USD'
        
        # 1. Map name to stock code (사용자 입력 ticker가 있으면 우선 사용)
        ticker = pos.get('ticker', '')
        code = ticker if ticker else await get_stock_code(name)
        if not code:
            results.append({
                "code": None,
                "name": name,
                "quantity": qty,
                "averagePrice": avg_price,
                "status": "error",
                "errorMessage": f"'{name}' 종목 코드를 찾을 수 없습니다."
            })
            continue
            
        # 2. Fetch OHLCV (last 14 days minimum required for ATR)
        df_ohlcv = await get_daily_ohlcv(code)
        
        if df_ohlcv is None or df_ohlcv.empty or len(df_ohlcv) < 14:
            results.append({
                "code": code,
                "name": name,
                "quantity": qty,
                "averagePrice": avg_price,
                "status": "error",
                "errorMessage": "과거 시세 데이터를 충분히(14일 이상) 연동할 수 없습니다."
            })
            continue
            
        # 3. Calculate ATR (Default: 14 days)
        # TR = max(H - L, abs(H - P_C), abs(L - P_C))
        df_ohlcv = df_ohlcv.copy()
        df_ohlcv['prev_close'] = df_ohlcv['close'].shift(1)
        
        # Calculate True Range (TR)
        df_ohlcv['tr1'] = df_ohlcv['high'] - df_ohlcv['low']
        df_ohlcv['tr2'] = (df_ohlcv['high'] - df_ohlcv['prev_close']).abs()
        df_ohlcv['tr3'] = (df_ohlcv['low'] - df_ohlcv['prev_close']).abs()
        df_ohlcv['tr'] = df_ohlcv[['tr1', 'tr2', 'tr3']].max(axis=1)
        
        # Calculate ATR (14-day Simple Moving Average of TR)
        df_ohlcv['atr'] = df_ohlcv['tr'].rolling(window=14).mean()
        
        # Get the latest ATR and Close Price
        current_atr = df_ohlcv['atr'].iloc[-1]
        current_price = df_ohlcv['close'].iloc[-1]
        
        # Error handling for weird ATR values (like NaNs)
        if pd.isna(current_atr):
           results.append({
                "code": code,
                "name": name,
                "quantity": qty,
                "averagePrice": avg_price,
                "status": "error",
                "errorMessage": "ATR 변동성 지표 연산 중 오류가 발생했습니다."
            })
           continue

        # 4. Calculate Stop-loss Price (using user-defined ATR multiplier)
        stop_loss_price = avg_price - (atr_multiplier * current_atr)
        
        # 5. Calculate Risk Amount (Convert USD risk to KRW if needed)
        raw_risk_amount = (avg_price - stop_loss_price) * qty
        risk_amount = raw_risk_amount * usd_to_krw if is_usd else raw_risk_amount
        
        results.append({
            "code": code,
            "name": name,
            "quantity": qty,
            "averagePrice": avg_price,
            "currency": currency,
            "currentPrice": round(current_price, 2),
            "atr": round(current_atr, 2),
            "stopLossPrice": round(stop_loss_price, 2),
            "riskAmount": round(risk_amount, 2),
            "status": "calculated"
        })
        
    return results

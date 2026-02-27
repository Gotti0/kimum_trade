import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sys
import os

# Add the project root directory to path to allow importing from backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.logic.calculator import calculate_stop_loss_and_atr
from backend.pipeline_router import router as pipeline_router

STOCK_MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "stock_map.json")
AUTO_TRADE_TARGETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "auto_trade_targets.json")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "auto_trade_history.json")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "auto_trade_config.json")

app = FastAPI(title="Loss Cut Simulator Backend API")
app.include_router(pipeline_router)

# Configure CORS for React Client integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], # React Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Position(BaseModel):
    name: str
    quantity: float
    averagePrice: float
    currency: str = "KRW"
    ticker: str = ""

class SimulateRequest(BaseModel):
    capital: float
    riskPercentage: float
    atrMultiplier: float = 2.0
    positions: List[Position]

@app.post("/api/simulate")
async def simulate(request: SimulateRequest):
    # Convert Pydantic models to dicts for the calculator
    positions_dict = [pos.model_dump() for pos in request.positions]
    
    results = await calculate_stop_loss_and_atr(
        capital=request.capital,
        risk_percentage=request.riskPercentage,
        atr_multiplier=request.atrMultiplier,
        positions=positions_dict
    )
    
    return {"data": results}

@app.get("/api/stock-map")
async def get_stock_map():
    """cache/stock_map.json을 프론트엔드에 제공"""
    if os.path.exists(STOCK_MAP_FILE):
        with open(STOCK_MAP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

class StockMapEntry(BaseModel):
    name: str
    ticker: str

@app.post("/api/stock-map/update")
async def update_stock_map(entry: StockMapEntry):
    """수동 입력된 종목명→티커를 stock_map.json에 영속 저장"""
    stock_map = {}
    if os.path.exists(STOCK_MAP_FILE):
        with open(STOCK_MAP_FILE, 'r', encoding='utf-8') as f:
            stock_map = json.load(f)

    stock_map[entry.name] = entry.ticker

    os.makedirs(os.path.dirname(STOCK_MAP_FILE), exist_ok=True)
    with open(STOCK_MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(stock_map, f, indent=4, ensure_ascii=False)

    return {"status": "ok", "name": entry.name, "ticker": entry.ticker}

class AutoTradeTarget(BaseModel):
    stk_cd: str
    stk_nm: str
    buy_amount: float

@app.get("/api/auto-trade/targets")
async def get_auto_trade_targets():
    """자동매매 타겟 종목 조회"""
    if os.path.exists(AUTO_TRADE_TARGETS_FILE):
        with open(AUTO_TRADE_TARGETS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

@app.post("/api/auto-trade/targets")
async def update_auto_trade_targets(targets: List[AutoTradeTarget]):
    """자동매매 타겟 종목 저장"""
    os.makedirs(os.path.dirname(AUTO_TRADE_TARGETS_FILE), exist_ok=True)
    targets_dict = [t.model_dump() for t in targets]
    with open(AUTO_TRADE_TARGETS_FILE, 'w', encoding='utf-8') as f:
        json.dump(targets_dict, f, indent=4, ensure_ascii=False)
    return {"status": "ok", "count": len(targets_dict)}

@app.get("/api/auto-trade/history")
async def get_auto_trade_history():
    """자동매매 체결 내역 및 누적수익률 조회 데이터"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

class AutoTradeConfig(BaseModel):
    buy_time: str
    evaluate_time: str
    force_close_time: str
    trailing_drop_rate: float

@app.get("/api/auto-trade/config")
async def get_auto_trade_config():
    """자동매매 환경설정 조회"""
    default_config = {
        "buy_time": "0900",
        "evaluate_time": "0914",
        "force_close_time": "1520",
        "trailing_drop_rate": 0.08
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            try:
                loaded = json.load(f)
                return {**default_config, **loaded}
            except json.JSONDecodeError:
                pass
    return default_config

@app.post("/api/auto-trade/config")
async def update_auto_trade_config(config: AutoTradeConfig):
    """자동매매 환경설정 갱신 (프론트에서 저장/동기화 요청)"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config.model_dump(), f, indent=4, ensure_ascii=False)
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    # Run the server on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8001)

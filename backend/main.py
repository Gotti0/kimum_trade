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

if __name__ == "__main__":
    import uvicorn
    # Run the server on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8001)

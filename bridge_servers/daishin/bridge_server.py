from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List
import logging
import sys
import os

# Add project root to path so we can import pipeline.agents
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.agents.daishin_agent import DaishinAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Daishin 32-bit Bridge Server")

# Initialize agent globally for single instance use
agent = DaishinAgent()

@app.on_event("startup")
async def startup_event():
    """
    Called when the FastAPI application starts up.
    We will wait for Daishin HTS login here before allowing any requests.
    """
    logger.info("Starting up Daishin API Bridge Server...")
    success = agent.wait_for_login(timeout=600) # Wait up to 10 minutes for login
    if not success:
        logger.error("Failed to connect to Daishin HTS on startup. The server might not function correctly.")

@app.get("/api/dostk/chart")
async def get_chart_data(stk_cd: str, count: int = 150000, since_date: int = None, since_time: int = None):
    """
    Fetch minute chart data via Daishin COM agent.
    
    Parameters:
    - stk_cd: Stock code (e.g., 'A005930' for Samsung Electronics, prefix with 'A')
    - count: Number of minute records to fetch
    - since_date: Optional. Only fetch data newer than this date (YYYYMMDD).
    - since_time: Optional. Used with since_date to only fetch data newer than this time (HHMM).
    """
    if agent.cybos is None or agent.cybos.IsConnect != 1:
        # Try a quick reconnect if disconnected
        success = agent.wait_for_login(timeout=5)
        if not success:
            raise HTTPException(status_code=503, detail="Daishin HTS is not connected. Please log in manually.")
            
    # Cybos Plus requires stock codes to start with 'A' for KOSPI/KOSDAQ
    formatted_code = stk_cd if stk_cd.startswith("A") else f"A{stk_cd}"
    
    logger.info(f"Received request for stock {formatted_code}, target count: {count}, since: {since_date} {since_time}")
    
    try:
        data = agent.get_minute_chart(formatted_code, count, since_date, since_time)
        
        if data is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve chart data from COM object.")
            
        return JSONResponse(content={"status": "success", "data": data})
        
    except Exception as e:
        logger.error(f"Error processing request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dostk/info")
async def get_stock_info(stk_cd: str):
    """
    Fetch company metadata like Market Cap, Sector, Listing Market, ATS status via Daishin COM agent.
    
    Parameters:
    - stk_cd: Stock code (e.g., 'A005930' for Samsung Electronics, prefix with 'A')
    """
    if agent.cybos is None or agent.cybos.IsConnect != 1:
        # Try a quick reconnect if disconnected
        success = agent.wait_for_login(timeout=5)
        if not success:
            raise HTTPException(status_code=503, detail="Daishin HTS is not connected. Please log in manually.")
            
    # Cybos Plus requires stock codes to start with 'A' for KOSPI/KOSDAQ
    formatted_code = stk_cd if stk_cd.startswith("A") else f"A{stk_cd}"
    
    logger.info(f"Received info request for stock {formatted_code}")
    
    try:
        data = agent.get_stock_info(formatted_code)
        return JSONResponse(content={"status": "success", "data": data})
        
    except Exception as e:
        logger.error(f"Error processing info request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class BatchInfoRequest(BaseModel):
    tickers: List[str]

@app.post("/api/dostk/info_batch")
async def get_stock_info_batch(req: BatchInfoRequest):
    """
    Fetch company metadata for up to 200 stocks simultaneously using MarketEye array processing.
    """
    if agent.cybos is None or agent.cybos.IsConnect != 1:
        success = agent.wait_for_login(timeout=5)
        if not success:
            raise HTTPException(status_code=503, detail="Daishin HTS is not connected.")
            
    # Format codes for Cybos (ensure they start with 'A')
    formatted_codes = [t if t.startswith("A") else f"A{t}" for t in req.tickers]
    
    logger.info(f"Received batch info request for {len(formatted_codes)} stocks")
    
    try:
        data = agent.fetch_multi_stock_info(formatted_codes)
        return JSONResponse(content={"status": "success", "data": data})
        
    except Exception as e:
        logger.error(f"Error processing batch info request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting uvicorn server on port 8000...")
    # NOTE: In 32-bit python environments sometimes we can run out of memory or have socket issues if we spawn too many workers.
    # Therefore we restrict it to a single worker explicitly.
    uvicorn.run("bridge_server:app", host="0.0.0.0", port=8000, workers=1, log_level="info")

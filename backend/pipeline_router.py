import os
import sys
import subprocess
import threading
import time
import glob
import math
import requests as http_requests
from collections import deque
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional


def _sanitize_nan(obj):
    """Recursively replace NaN / Inf float values with None so JSON serialisation succeeds."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

# ── Project paths ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VPANDA_PYTHON = os.path.join(PROJECT_ROOT, "vpanda", "Scripts", "python.exe")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
BRIDGE_SERVER_URL = "http://localhost:8000"

# ── Process manager ──
class ProcessManager:
    """Manages background subprocess lifecycle and log buffering."""
    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, deque] = {}
        self._lock = threading.Lock()
    
    def start(self, name: str, cmd: list[str], cwd: str = PROJECT_ROOT, env: dict = None) -> bool:
        with self._lock:
            if name in self._processes and self._processes[name].poll() is None:
                return False  # already running
            
            merged_env = {**os.environ, **(env or {}), "PYTHONUTF8": "1"}
            
            try:
                proc = subprocess.Popen(
                    cmd, cwd=cwd,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    env=merged_env,
                )
            except Exception as e:
                self._logs.setdefault(name, deque(maxlen=500))
                self._logs[name].append(f"[ERROR] Failed to start: {e}\n")
                return False
            
            self._processes[name] = proc
            self._logs[name] = deque(maxlen=500)
            
            # Background thread to read stdout
            t = threading.Thread(target=self._reader, args=(name, proc), daemon=True)
            t.start()
            return True
    
    def _reader(self, name: str, proc: subprocess.Popen):
        try:
            for line in proc.stdout:
                with self._lock:
                    self._logs[name].append(line)
        except Exception:
            pass
    
    def stop(self, name: str) -> bool:
        with self._lock:
            proc = self._processes.get(name)
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return True
            return False
    
    def status(self, name: str) -> dict:
        with self._lock:
            proc = self._processes.get(name)
            logs = list(self._logs.get(name, []))
            if proc is None:
                return {"name": name, "status": "idle", "logs": logs}
            rc = proc.poll()
            if rc is None:
                return {"name": name, "status": "running", "pid": proc.pid, "logs": logs}
            return {"name": name, "status": "finished", "exitCode": rc, "logs": logs}
    
    def all_status(self) -> list[dict]:
        with self._lock:
            names = list(set(list(self._processes.keys()) + list(self._logs.keys())))
        return [self.status(n) for n in names]

pm = ProcessManager()


# ── Request models ──
class ExcelFillRequest(BaseModel):
    filename: str  # e.g. "object_excel.xlsx"

class FetchChartRequest(BaseModel):
    stock_code: str  # e.g. "A005930"

class StopRequest(BaseModel):
    name: str

class KiwoomBacktestRequest(BaseModel):
    days: int = 99
    capital: float = 10_000_000
    strategy: str = "legacy"  # "legacy", "swing", or "pullback"
    mode: str = "daily"       # "daily" or "minute"
    volume_top_n: int = 100   # [pullback] 거래량 상위 N 유니버스
    slippage_bps: float = 10.0    # [pullback] 매수·익절 슬리피지 (bp)
    stop_slippage_bps: float = 20.0  # [pullback] 손절 슬리피지 (bp)
    target_file: str = "object_excel_daishin_filled.md"  # [legacy/phoenix] 매매 대상 마크다운 파일

# ── Endpoints ──

@router.get("/excel-files")
async def list_excel_files():
    """Return list of .xlsx files in docs/ folder."""
    if not os.path.isdir(DOCS_DIR):
        return {"files": []}
    files = [f for f in os.listdir(DOCS_DIR) if f.lower().endswith(".xlsx")]
    files.sort()
    return {"files": files}


@router.get("/md-files")
async def list_md_files():
    """Return list of .md files in docs/ folder for Phoenix target selection."""
    if not os.path.isdir(DOCS_DIR):
        return {"files": []}
    files = [f for f in os.listdir(DOCS_DIR) if f.lower().endswith(".md")]
    files.sort()
    return {"files": files}

@router.get("/bridge-server/health")
async def check_bridge_server():
    """Check if the Daishin bridge server is reachable on port 8000."""
    try:
        r = http_requests.get(f"{BRIDGE_SERVER_URL}/docs", timeout=2)
        return {"status": "connected", "statusCode": r.status_code}
    except http_requests.exceptions.ConnectionError:
        return {"status": "disconnected"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.post("/excel-fill")
async def run_excel_fill(req: ExcelFillRequest):
    """Run fill_excel_daishin.py with the specified Excel file."""
    filepath = os.path.join(DOCS_DIR, req.filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail=f"File not found: {req.filename}")
    
    script = os.path.join(PROJECT_ROOT, "pipeline", "excel", "fill_excel_daishin.py")
    cmd = [VPANDA_PYTHON, script, filepath]
    ok = pm.start("excel-fill", cmd)
    if not ok:
        return {"message": "Excel Fill pipeline is already running", "status": "running"}
    return {"message": f"Excel Fill started for {req.filename}", "status": "started"}


@router.post("/fetch-chart")
async def run_fetch_chart(req: FetchChartRequest):
    """Run fetch_daishin_chart_64.py with the specified stock code."""
    script = os.path.join(PROJECT_ROOT, "scripts", "exploration", "fetch_daishin_chart_64.py")
    if not os.path.isfile(script):
        raise HTTPException(status_code=404, detail="fetch_daishin_chart_64.py not found")
    
    code = req.stock_code if req.stock_code.startswith("A") else f"A{req.stock_code}"
    cmd = [VPANDA_PYTHON, script, "--code", code]
    ok = pm.start("fetch-chart", cmd)
    if not ok:
        return {"message": "Fetch Chart pipeline is already running", "status": "running"}
    return {"message": f"Fetch Chart started for {code}", "status": "started"}


@router.get("/status")
async def get_all_status():
    """Return status and logs for all pipelines."""
    return {"pipelines": pm.all_status()}


@router.get("/status/{name}")
async def get_pipeline_status(name: str):
    """Return status and logs for a specific pipeline."""
    return pm.status(name)


@router.post("/stop")
async def stop_pipeline(req: StopRequest):
    ok = pm.stop(req.name)
    return {"stopped": ok, "name": req.name}


@router.post("/kiwoom-backtest")
async def run_kiwoom_backtest(req: KiwoomBacktestRequest):
    """Run Kiwoom Theme Backtester."""
    script = os.path.join(PROJECT_ROOT, "backend", "kiwoom", "strategy", "phoenix", "backtester.py")
    if not os.path.isfile(script):
        raise HTTPException(status_code=404, detail="backtester.py not found")
    
    # Run as a module to avoid import errors
    strategy = req.strategy if req.strategy in ("legacy", "swing", "pullback") else "legacy"
    mode = req.mode if req.mode in ("daily", "minute") else "daily"
    cmd = [
        VPANDA_PYTHON, "-m", "backend.kiwoom.strategy.phoenix.backtester",
        str(req.days), "--capital", str(req.capital),
        "--strategy", strategy, "--mode", mode,
        "--volume-top-n", str(req.volume_top_n),
        "--slippage-bps", str(req.slippage_bps),
        "--stop-slippage-bps", str(req.stop_slippage_bps),
    ]
    if strategy == "legacy" and req.target_file:
        cmd.extend(["--target-file", req.target_file])
        
    ok = pm.start("kiwoom-backtest", cmd)
    if not ok:
        return {"message": "Kiwoom Backtest is already running", "status": "running"}
    return {"message": "Kiwoom Backtest started", "status": "started"}


class MomentumBacktestRequest(BaseModel):
    capital: float = 100_000_000
    top_n: int = 20
    weight_method: str = "inverse_volatility"  # or "equal_weight"
    months: int = 12
    full: bool = False


@router.post("/momentum-backtest")
async def run_momentum_backtest(req: MomentumBacktestRequest):
    """Run Mid-to-Long Term Dual Momentum Backtester."""
    weight = req.weight_method if req.weight_method in ("inverse_volatility", "equal_weight") else "inverse_volatility"
    cmd = [
        VPANDA_PYTHON, "-m", "backend.kiwoom.strategy.momentum.momentum_backtester",
        "--capital", str(req.capital),
        "--top-n", str(req.top_n),
        "--weight", weight,
        "--months", str(req.months),
        "--save-json",
    ]
    if req.full:
        cmd.append("--full")
    ok = pm.start("momentum-backtest", cmd)
    if not ok:
        return {"message": "Momentum Backtest is already running", "status": "running"}
    return {"message": "Momentum Backtest started", "status": "started"}


@router.get("/momentum-backtest/result")
async def get_momentum_result():
    """Return the latest momentum backtest result from JSON file."""
    result_file = os.path.join(PROJECT_ROOT, "cache", "momentum", "latest_result.json")
    if not os.path.isfile(result_file):
        return {"status": "no_data", "data": None}
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            data = __import__('json').load(f)
        return {"status": "ok", "data": _sanitize_nan(data)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ── 글로벌 멀티에셋 듀얼 모멘텀 ──

class GlobalMomentumBacktestRequest(BaseModel):
    capital: float = 100_000_000
    portfolio_preset: str = "balanced"  # growth, growth_seeking, balanced, stability_seeking, stable
    months: int = 12
    full: bool = False


@router.post("/global-momentum-backtest")
async def run_global_momentum_backtest(req: GlobalMomentumBacktestRequest):
    """Run Global Multi-Asset Dual Momentum Backtester."""
    preset = req.portfolio_preset if req.portfolio_preset in (
        "growth", "growth_seeking", "balanced", "stability_seeking", "stable"
    ) else "balanced"
    cmd = [
        VPANDA_PYTHON, "-m", "backend.kiwoom.strategy.momentum.momentum_backtester",
        "--global",
        "--preset", preset,
        "--capital", str(req.capital),
        "--months", str(req.months),
        "--save-json",
    ]
    if req.full:
        cmd.append("--full")
    ok = pm.start("global-momentum-backtest", cmd)
    if not ok:
        return {"message": "Global Momentum Backtest is already running", "status": "running"}
    return {"message": f"Global Momentum Backtest started (preset: {preset})", "status": "started"}


@router.get("/global-momentum-backtest/result")
async def get_global_momentum_result():
    """Return the latest global momentum backtest result from JSON file."""
    result_file = os.path.join(PROJECT_ROOT, "cache", "momentum", "global_latest_result.json")
    if not os.path.isfile(result_file):
        return {"status": "no_data", "data": None}
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            data = __import__('json').load(f)
        return {"status": "ok", "data": _sanitize_nan(data)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


class MomentumScreenerRequest(BaseModel):
    top_n: int = 20
    weight_method: str = "inverse_volatility"
    min_trading_value: float = 5e9


@router.post("/momentum-screener")
async def run_momentum_screener(req: MomentumScreenerRequest):
    """Run Dual Momentum Screener."""
    weight = req.weight_method if req.weight_method in ("inverse_volatility", "equal_weight") else "inverse_volatility"
    cmd = [
        VPANDA_PYTHON, "-m", "backend.kiwoom.strategy.momentum.momentum_screener",
        "--top-n", str(req.top_n),
        "--weight", weight,
        "--min-tv", str(req.min_trading_value),
    ]
    ok = pm.start("momentum-screener", cmd)
    if not ok:
        return {"message": "Momentum Screener is already running", "status": "running"}
    return {"message": "Momentum Screener started", "status": "started"}


@router.get("/momentum-screener/result")
async def get_momentum_screener_result():
    """Return the latest momentum screener result from JSON file."""
    result_file = os.path.join(PROJECT_ROOT, "cache", "screener", "momentum_latest.json")
    if not os.path.isfile(result_file):
        return {"status": "no_data", "data": None}
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            data = __import__('json').load(f)
        return {"status": "ok", "data": _sanitize_nan(data)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


class GlobalScreenerRequest(BaseModel):
    preset: str = "balanced"
    weight_method: str = "inverse_volatility"
    capital: float = 1e8


@router.post("/global-screener")
async def run_global_screener(req: GlobalScreenerRequest):
    """Run Global Multi-Asset Screener (KR ETF approximation)."""
    preset = req.preset if req.preset in (
        "growth", "growth_seeking", "balanced", "stability_seeking", "stable"
    ) else "balanced"
    weight = req.weight_method if req.weight_method in ("inverse_volatility", "equal_weight") else "inverse_volatility"
    cmd = [
        VPANDA_PYTHON, "-m", "backend.kiwoom.strategy.global_etf.global_screener",
        "--preset", preset,
        "--weight", weight,
        "--capital", str(req.capital),
    ]
    ok = pm.start("global-screener", cmd)
    if not ok:
        return {"message": "Global Screener is already running", "status": "running"}
    return {"message": "Global Screener started", "status": "started"}


@router.get("/global-screener/result")
async def get_global_screener_result():
    """Return the latest global screener result from JSON file."""
    result_file = os.path.join(PROJECT_ROOT, "cache", "screener", "global_screener_latest.json")
    if not os.path.isfile(result_file):
        return {"status": "no_data", "data": None}
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            data = __import__('json').load(f)
        return {"status": "ok", "data": _sanitize_nan(data)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


class ScreenerRequest(BaseModel):
    top_n: int = 30
    strategy: str = "swing"


@router.post("/screener")
async def run_screener(req: ScreenerRequest):
    """Run Alpha Filter Screener."""
    strategy = req.strategy if req.strategy in ("swing", "pullback") else "swing"
    cmd = [VPANDA_PYTHON, "-m", "backend.kiwoom.strategy.phoenix.alpha_screener", "--top_n", str(req.top_n), "--strategy", strategy]
    ok = pm.start("alpha-screener", cmd)
    if not ok:
        return {"message": "Screener is already running", "status": "running"}
    return {"message": "Alpha Screener started", "status": "started"}


@router.get("/screener/result")
async def get_screener_result():
    """Return the latest screener result from JSON file."""
    result_file = os.path.join(PROJECT_ROOT, "cache", "screener", "latest.json")
    if not os.path.isfile(result_file):
        return {"status": "no_data", "data": None}
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            data = __import__('json').load(f)
        return {"status": "ok", "data": _sanitize_nan(data)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


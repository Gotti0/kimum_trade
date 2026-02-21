import os
import sys
import subprocess
import threading
import time
import glob
from collections import deque
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

# ── Project paths ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VPANDA_PYTHON = os.path.join(PROJECT_ROOT, "vpanda", "Scripts", "python.exe")
VENV_PYTHON = os.path.join(PROJECT_ROOT, "venv", "Scripts", "python.exe")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")

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
            
            merged_env = {**os.environ, **(env or {})}
            
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


# ── Endpoints ──

@router.get("/excel-files")
async def list_excel_files():
    """Return list of .xlsx files in docs/ folder."""
    if not os.path.isdir(DOCS_DIR):
        return {"files": []}
    files = [f for f in os.listdir(DOCS_DIR) if f.lower().endswith(".xlsx")]
    files.sort()
    return {"files": files}


@router.post("/bridge-server/start")
async def start_bridge_server():
    """Start the Daishin 32-bit bridge server (uses venv, 32-bit python)."""
    script = os.path.join(PROJECT_ROOT, "bridge_servers", "daishin", "bridge_server.py")
    if not os.path.isfile(script):
        raise HTTPException(status_code=404, detail="bridge_server.py not found")
    
    cmd = [VENV_PYTHON, script]
    ok = pm.start("bridge-server", cmd)
    if not ok:
        return {"message": "Bridge Server is already running", "status": "running"}
    return {"message": "Bridge Server started", "status": "started"}


@router.post("/bridge-server/stop")
async def stop_bridge_server():
    ok = pm.stop("bridge-server")
    return {"stopped": ok}


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

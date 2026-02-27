import os
import json
import logging
import requests
import certifi
from datetime import datetime

logger = logging.getLogger(__name__)

class KiwoomTradeAPI:
    """
    키움 REST API 기반 주문 및 시세 조회 래퍼
    모의투자 도메인(mockapi.kiwoom.com) 전용으로 작성됨
    """
    def __init__(self, is_mock: bool = True):
        self.domain = "https://mockapi.kiwoom.com" if is_mock else "https://api.kiwoom.com"
        # token은 theme_finder 등에서 사용하는 기존 token.json을 재사용
        self.token = self._get_token()

    def _get_token(self) -> str:
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        token_path = os.path.join(_project_root, "backend", "kiwoom", "token.json")
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("access_token", "")
        except Exception as e:
            logger.error(f"Failed to load token: {e}")
            return ""

    def _get_headers(self, api_id: str) -> dict:
        return {
            "api-id": api_id,
            "authorization": f"Bearer {self.token}",
            "Content-Type": "application/json;charset=UTF-8"
        }

    def place_buy_order(self, stk_cd: str, ord_qty: int, trde_tp: str = "3") -> dict:
        """
        주식 매수 주문 (kt10000)
        :param stk_cd: 종목코드 (6자리)
        :param ord_qty: 주문 수량
        :param trde_tp: 매매구분 (3: 시장가, 0: 보통 등)
        """
        url = f"{self.domain}/api/dostk/ordr"
        headers = self._get_headers("kt10000")
        payload = {
            "dmst_stex_tp": "KRX",
            "stk_cd": stk_cd,
            "ord_qty": str(ord_qty),
            "ord_uv": "", # 시장가일 경우 단가 빈문자열
            "trde_tp": trde_tp,
            "cond_uv": ""
        }
        
        try:
            resp = requests.post(url, headers=headers, json=payload, verify=certifi.where(), timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Buy Order Failed: {stk_cd}, Qty: {ord_qty}, Error: {e}")
            return {"return_code": -1, "return_msg": str(e)}

    def place_sell_order(self, stk_cd: str, ord_qty: int, trde_tp: str = "3") -> dict:
        """
        주식 매도 주문 (kt10001)
        :param stk_cd: 종목코드 (6자리)
        :param ord_qty: 주문 수량
        :param trde_tp: 매매구분 (3: 시장가)
        """
        url = f"{self.domain}/api/dostk/ordr"
        headers = self._get_headers("kt10001")
        payload = {
            "dmst_stex_tp": "KRX",
            "stk_cd": stk_cd,
            "ord_qty": str(ord_qty),
            "ord_uv": "",
            "trde_tp": trde_tp,
            "cond_uv": ""
        }
        
        try:
            resp = requests.post(url, headers=headers, json=payload, verify=certifi.where(), timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Sell Order Failed: {stk_cd}, Qty: {ord_qty}, Error: {e}")
            return {"return_code": -1, "return_msg": str(e)}

    def get_current_price(self, stk_cd: str) -> float:
        """
        현재가 조회
        기존에 사용하던 ka10081(차트조회) 또는 단건 현재가 API 사용
        여기서는 간단히 종가/현재가를 추출
        """
        url = f"{self.domain}/api/dostk/chart"
        headers = self._get_headers("ka10081")
        payload = {
            "stk_cd": stk_cd,
            "base_dt": datetime.now().strftime("%Y%m%d"),
            "upd_stkpc_tp": "1"
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, verify=certifi.where(), timeout=5)
            resp.raise_for_status()
            data = resp.json()
            chart = data.get("stk_dt_pole_chart_qry", [])
            if not chart:
                return 0.0
            # 당일(혹은 가장 최신) 데이터
            latest = chart[0] 
            return float(latest.get("cur_prc", "0"))
        except Exception as e:
            logger.error(f"Get Price Failed: {stk_cd}, Error: {e}")
            return 0.0


import os
import json
import requests
import certifi
from dotenv import load_dotenv

# .env 로드
load_dotenv(".env")

def test_ka90001_with_date(target_date):
    domain = os.getenv("KIWOOM_DOMAIN", "https://api.kiwoom.com")
    appkey = os.getenv("appkey")
    secretkey = os.getenv("secretkey")
    
    # 토큰 발급
    auth_resp = requests.post(
        f"{domain}/oauth2/token",
        headers={"api-id": "au10001", "Content-Type": "application/json;charset=UTF-8"},
        json={"grant_type": "client_credentials", "appkey": appkey, "secretkey": secretkey},
        verify=certifi.where()
    )
    token = auth_resp.json()["token"]
    
    # ka90001 호출 (base_dt 추가 시도)
    url = f"{domain}/api/dostk/thme"
    headers = {
        "api-id": "ka90001",
        "authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
    }
    
    # 1. base_dt 없이 호출
    payload1 = {
        "qry_tp": "0",
        "stk_cd": "",
        "date_tp": "1",
        "thema_nm": "",
        "flu_pl_amt_tp": "1",
        "stex_tp": "1",
    }
    
    # 2. base_dt 포함 호출
    payload2 = payload1.copy()
    payload2["base_dt"] = target_date
    
    print(f"--- Calling ka90001 (Standard) ---")
    r1 = requests.post(url, headers=headers, json=payload1, verify=certifi.where())
    print(f"Top 1 Theme: {r1.json().get('thema_grp', [{}])[0].get('thema_nm')}")
    
    print(f"\n--- Calling ka90001 (with base_dt={target_date}) ---")
    r2 = requests.post(url, headers=headers, json=payload2, verify=certifi.where())
    res2 = r2.json().get('thema_grp', [{}])
    if res2:
        print(f"Top 1 Theme: {res2[0].get('thema_nm')}")
        # 만약 Standard와 결과가 다르면 base_dt가 동작할 가능성이 큼
    else:
        print("No result with base_dt")

if __name__ == "__main__":
    test_ka90001_with_date("20250915")

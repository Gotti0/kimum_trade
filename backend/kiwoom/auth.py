import os
import json
import logging
import requests
import certifi

logger = logging.getLogger(__name__)

# Find project root (one level up from backend)
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(_backend_dir)
TOKEN_PATH = os.path.join(PROJECT_ROOT, "token.json")

def _get_domain(use_mock: bool = False):
    return "https://mockapi.kiwoom.com" if use_mock else "https://api.kiwoom.com"

def issue_token(appkey: str, secretkey: str, use_mock: bool = False) -> dict:
    """
    접근토큰 발급 (au10001)
    """
    domain = _get_domain(use_mock)
    url = f"{domain}/oauth2/token"
    
    body = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "secretkey": secretkey
    }
    
    try:
        resp = requests.post(url, json=body, verify=certifi.where())
        resp.raise_for_status()
        data = resp.json()
        
        if "token" in data:
            # save to project root token.json
            save_data = {
                "access_token": data["token"],
                "expires_dt": data.get("expires_dt", ""),
                "token_type": data.get("token_type", "bearer")
            }
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4)
            logger.info("Token successfully issued and saved.")
            return save_data
        else:
            logger.error(f"Failed to issue token: {data}")
            return None
            
    except Exception as e:
        logger.error(f"Token issue request failed: {e}")
        return None

def revoke_token(appkey: str, secretkey: str, token: str, use_mock: bool = False) -> bool:
    """
    접근토큰 폐기 (au10002)
    """
    domain = _get_domain(use_mock)
    url = f"{domain}/oauth2/revoke"
    
    body = {
        "appkey": appkey,
        "secretkey": secretkey,
        "token": token
    }
    
    try:
        resp = requests.post(url, json=body, verify=certifi.where())
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("return_code") == 0:
            logger.info("Token successfully revoked.")
            if os.path.exists(TOKEN_PATH):
                os.remove(TOKEN_PATH)
            return True
        else:
            logger.error(f"Failed to revoke token: {data}")
            return False
            
    except Exception as e:
        logger.error(f"Token revoke request failed: {e}")
        return False

def get_token() -> str:
    """Reads the token from root token.json file. If missing or expired, issues a new one."""
    from datetime import datetime
    from dotenv import load_dotenv

    def _issue_new_token():
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
        appkey = os.getenv("appkey")
        secretkey = os.getenv("secretkey")
        use_mock = os.getenv("USE_MOCK_KIWOOM", "0") == "1"

        if not appkey or not secretkey:
            logger.error("Cannot auto-issue token: appkey or secretkey not found in .env")
            return ""

        logger.info("Auto-issuing new Kiwoom token...")
        token_data = issue_token(appkey, secretkey, use_mock)
        if token_data:
            return token_data.get("access_token", "")
        return ""

    if not os.path.exists(TOKEN_PATH):
        logger.warning(f"No token found at {TOKEN_PATH}. Attempting to auto-issue...")
        return _issue_new_token()

    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        access_token = data.get("access_token", "")
        expires_dt_str = data.get("expires_dt", "")

        if expires_dt_str:
            try:
                expires_dt = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
                if datetime.now() >= expires_dt:
                    logger.warning("Token has expired. Attempting to auto-issue...")
                    return _issue_new_token()
            except ValueError:
                pass

        if not access_token:
            return _issue_new_token()

        return access_token
    except Exception as e:
        logger.error(f"Failed to load Kiwoom token: {e}")
        return _issue_new_token()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    appkey = os.getenv("appkey")
    secretkey = os.getenv("secretkey")
    use_mock = os.getenv("USE_MOCK_KIWOOM", "0") == "1"

    if not appkey or not secretkey:
        print("Error: appkey or secretkey not found in .env file.")
    else:
        print(f"Issuing new token (USE_MOCK_KIWOOM={use_mock})...")
        token_data = issue_token(appkey, secretkey, use_mock)
        if token_data:
            print("Token successfully generated and saved to token.json!")
        else:
            print("Failed to generate token.")


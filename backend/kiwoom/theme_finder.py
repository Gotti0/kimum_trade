"""
TopThemeFinder: N일전 기간수익률 1위 테마와 구성종목을 조회하는 클래스.

사용 API:
  - au10001: 접근토큰 발급
  - ka10007: 시세표성정보요청 (상한가/하한가/전일종가 조회)
  - ka10080: 주식분봉차트조회요청 (분봉 데이터 조회)
  - ka90001: 테마그룹별요청 (1등 테마 조회)
  - ka90002: 테마구성종목요청 (테마 구성종목 조회)
"""

import os
import logging
import certifi
import requests
from dotenv import load_dotenv

# .env 파일 로드 (프로젝트 루트 기준)
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_project_root, ".env"))

logger = logging.getLogger(__name__)


class TopThemeFinder:
    """N일전 기간수익률 1위 테마와 구성종목을 조회합니다."""

    def __init__(
        self,
        domain: str = None,
        appkey: str = None,
        secretkey: str = None,
    ):
        self.domain = domain or os.getenv("KIWOOM_DOMAIN", "https://api.kiwoom.com")
        self.appkey = appkey or os.getenv("appkey", "")
        self.secretkey = secretkey or os.getenv("secretkey", "")
        self._token: str = ""

    # ── 토큰 발급 ──────────────────────────────────────────

    def _get_token(self) -> str:
        """au10001 API로 접근토큰을 발급받습니다. 이미 발급된 토큰이 있으면 재사용."""
        if self._token:
            return self._token

        url = f"{self.domain}/oauth2/token"
        headers = {
            "api-id": "au10001",
            "Content-Type": "application/json;charset=UTF-8",
        }
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "secretkey": self.secretkey,
        }

        resp = requests.post(url, headers=headers, json=payload,
                             verify=certifi.where(), timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("return_code") != 0:
            raise RuntimeError(f"토큰 발급 실패: {data.get('return_msg')}")

        self._token = data["token"]
        logger.info("접근토큰 발급 성공 (만료: %s)", data.get("expires_dt", "?"))
        return self._token

    # ── ka90001: 테마그룹별요청 ────────────────────────────

    def get_top_themes(self, days_ago: int = 1, top_n: int = 1) -> list[dict]:
        """N일전 기간수익률 상위 테마 목록을 조회합니다.

        Args:
            days_ago: 조회할 기간 (1~99일)
            top_n: 상위 몇 개 테마를 반환할지

        Returns:
            [{thema_grp_cd, thema_nm, stk_num, flu_rt, dt_prft_rt, main_stk, ...}, ...]
        """
        token = self._get_token()
        url = f"{self.domain}/api/dostk/thme"
        headers = {
            "api-id": "ka90001",
            "authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
        }
        payload = {
            "qry_tp": "0",            # 전체검색
            "stk_cd": "",
            "date_tp": str(days_ago),  # N일전
            "thema_nm": "",
            "flu_pl_amt_tp": "1",      # 상위기간수익률
            "stex_tp": "1",            # KRX
        }

        resp = requests.post(url, headers=headers, json=payload,
                             verify=certifi.where(), timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("return_code") != 0:
            raise RuntimeError(f"ka90001 실패: {data.get('return_msg')}")

        themes = data.get("thema_grp", [])
        logger.info("테마 %d개 조회됨 (상위 %d개 반환)", len(themes), top_n)
        return themes[:top_n]

    def get_top_theme(self, days_ago: int = 1) -> dict:
        """N일전 기간수익률 1위 테마를 조회합니다.

        Returns:
            {thema_grp_cd, thema_nm, stk_num, flu_rt, dt_prft_rt, main_stk}
            테마가 없으면 빈 dict 반환
        """
        themes = self.get_top_themes(days_ago=days_ago, top_n=1)
        return themes[0] if themes else {}

    # ── ka90002: 테마구성종목요청 ──────────────────────────

    def get_theme_stocks(self, thema_grp_cd: str, days_ago: int = 1) -> list[dict]:
        """특정 테마의 구성종목을 조회합니다.

        Args:
            thema_grp_cd: 테마그룹코드 (ka90001 응답에서 획득)
            days_ago: 기간 (1~99일)

        Returns:
            [{stk_cd, stk_nm, cur_prc, flu_sig, pred_pre, flu_rt,
              acc_trde_qty, sel_bid, sel_req, buy_bid, buy_req, dt_prft_rt_n}, ...]
        """
        token = self._get_token()
        url = f"{self.domain}/api/dostk/thme"
        headers = {
            "api-id": "ka90002",
            "authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
        }
        payload = {
            "date_tp": str(days_ago),
            "thema_grp_cd": thema_grp_cd,
            "stex_tp": "1",  # KRX
        }

        all_stocks = []
        cont_yn = ""
        next_key = ""

        # 연속조회 루프
        for _ in range(5):
            if cont_yn == "Y":
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            resp = requests.post(url, headers=headers, json=payload,
                                 verify=certifi.where(), timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("return_code") != 0:
                raise RuntimeError(f"ka90002 실패: {data.get('return_msg')}")

            stocks = data.get("thema_comp_stk", [])
            all_stocks.extend(stocks)

            # 연속조회 여부 확인
            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = resp.headers.get("next-key", "")
            if cont_yn != "Y":
                break

        logger.info("테마 [%s] 구성종목 %d개 조회됨", thema_grp_cd, len(all_stocks))
        return all_stocks

    # ── 편의 메서드 ────────────────────────────────────────

    def find_top_theme_with_stocks(self, days_ago: int = 1) -> dict:
        """1등 테마 + 구성종목을 한 번에 조회합니다.

        Returns:
            {
                'theme': {thema_grp_cd, thema_nm, dt_prft_rt, ...},
                'stocks': [{stk_cd, stk_nm, flu_rt, dt_prft_rt_n, ...}, ...]
            }
        """
        theme = self.get_top_theme(days_ago=days_ago)
        if not theme:
            return {"theme": {}, "stocks": []}

        stocks = self.get_theme_stocks(
            thema_grp_cd=theme["thema_grp_cd"],
            days_ago=days_ago,
        )
        return {"theme": theme, "stocks": stocks}

    # ── ka10007: 시세표성정보요청 ──────────────────────────

    def get_stock_info(self, stk_cd: str) -> dict:
        """종목의 시세표성정보를 조회합니다 (상한가/하한가/전일종가 등).

        Args:
            stk_cd: 종목코드

        Returns:
            {stk_nm, stk_cd, upl_pric (상한가), lst_pric (하한가),
             pred_close_pric (전일종가), cur_prc (현재가), ...}
        """
        token = self._get_token()
        url = f"{self.domain}/api/dostk/mrkcond"
        headers = {
            "api-id": "ka10007",
            "authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
        }
        payload = {"stk_cd": stk_cd}

        resp = requests.post(url, headers=headers, json=payload,
                             verify=certifi.where(), timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("return_code") != 0:
            raise RuntimeError(f"ka10007 실패: {data.get('return_msg')}")

        logger.info("시세표성정보 [%s] 조회 완료 (상한가: %s)", stk_cd, data.get("upl_pric"))
        return data

    # ── ka10080: 분봉차트조회 ─────────────────────────────

    def get_minute_chart(self, stk_cd: str, base_dt: str, tic_scope: str = "1") -> list[dict]:
        """종목의 분봉 차트 데이터를 조회합니다.

        Args:
            stk_cd: 종목코드
            base_dt: 기준일자 (YYYYMMDD)
            tic_scope: 틱범위 (1:1분, 3:3분, 5:5분, 10:10분, 15:15분, 30:30분)

        Returns:
            [{cntr_tm, cur_prc, open_pric, high_pric, low_pric, trde_qty}, ...]
            시간순 정렬 (오래된 → 최신)
        """
        token = self._get_token()
        url = f"{self.domain}/api/dostk/chart"
        headers = {
            "api-id": "ka10080",
            "authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
        }
        payload = {
            "stk_cd": stk_cd,
            "tic_scope": tic_scope,
            "upd_stkpc_tp": "1",
            "base_dt": base_dt,
        }

        all_bars = []
        cont_yn = ""
        next_key = ""

        for _ in range(10):  # 최대 10회 연속조회
            if cont_yn == "Y":
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            resp = requests.post(url, headers=headers, json=payload,
                                 verify=certifi.where(), timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("return_code") != 0:
                raise RuntimeError(f"ka10080 실패: {data.get('return_msg')}")

            bars = data.get("stk_min_pole_chart_qry", [])
            if not bars:
                break
            all_bars.extend(bars)

            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = resp.headers.get("next-key", "")
            if cont_yn != "Y":
                break

        # 시간순 정렬 (cntr_tm 기준 오름차순)
        all_bars.sort(key=lambda x: x.get("cntr_tm", ""))

        logger.info("분봉 [%s / %s] %d건 조회됨", stk_cd, base_dt, len(all_bars))
        return all_bars

# ── 직접 실행 시 테스트 ────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    finder = TopThemeFinder()

    days = 1
    print(f"\n{'='*60}")
    print(f"  {days}일전 기간수익률 1위 테마 조회")
    print(f"{'='*60}\n")

    result = finder.find_top_theme_with_stocks(days_ago=days)

    theme = result["theme"]
    if not theme:
        print("⚠ 테마 데이터가 없습니다.")
    else:
        print(f"📌 1위 테마: {theme.get('thema_nm', '?')}")
        print(f"   코드: {theme.get('thema_grp_cd')}")
        print(f"   종목수: {theme.get('stk_num')}")
        print(f"   등락률: {theme.get('flu_rt')}")
        print(f"   기간수익률: {theme.get('dt_prft_rt')}")
        print(f"   주요종목: {theme.get('main_stk')}")

        stocks = result["stocks"]
        print(f"\n📊 구성종목 ({len(stocks)}개):")
        print(f"{'종목코드':<10} {'종목명':<16} {'현재가':>10} {'등락률':>8} {'기간수익률':>10}")
        print("-" * 60)
        for stk in stocks:
            print(
                f"{stk.get('stk_cd', ''):<10} "
                f"{stk.get('stk_nm', ''):<16} "
                f"{stk.get('cur_prc', ''):>10} "
                f"{stk.get('flu_rt', ''):>8} "
                f"{stk.get('dt_prft_rt_n', ''):>10}"
            )

import os
import sys
import json
import time
import logging
from datetime import datetime, time as time_obj
import math

# Add the project root directory to path to allow importing from backend
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(_project_root)

from backend.kiwoom.trade_api import KiwoomTradeAPI

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TARGET_FILE = os.path.join(_project_root, "docs", "auto_trade_targets.json")

class AutoTrader:
    def __init__(self):
        self.api = KiwoomTradeAPI(is_mock=True)
        self.targets = []
        self.positions = {} # stk_cd (code) -> { stk_nm, qty, buy_price, open_price, price_914, sell_start, sell_end, trailing_stop_price, is_hit_upper }
        
    def load_targets(self):
        if not os.path.exists(TARGET_FILE):
            return []
        try:
            with open(TARGET_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load targets: {e}")
            return []

    def get_market_status(self):
        """임시 시장 상태 체크. 09:00 ~ 15:30 장중"""
        now = datetime.now().time()
        market_open = time_obj(9, 0, 0)
        market_close = time_obj(15, 30, 0)
        
        if now < market_open:
            return "BEFORE_MARKET"
        elif now >= market_close:
            return "AFTER_MARKET"
        else:
            return "OPEN"

    def execute_buying(self):
        """09:00 정각에 타겟 종목 시장가 매수"""
        if not self.targets:
            logger.info("매수 타겟이 없습니다.")
            return

        for tg in self.targets:
            stk_cd = tg["stk_cd"]
            stk_nm = tg["stk_nm"]
            buy_amount = tg["buy_amount"]
            
            # 현재가 조회해서 수량 계산 (시초가 조회)
            current_price = self.api.get_current_price(stk_cd)
            if current_price <= 0:
                logger.warning(f"[{stk_nm}] 현재가 조회 실패. 매수 보류.")
                continue
                
            qty = math.floor(buy_amount / current_price)
            if qty <= 0:
                logger.warning(f"[{stk_nm}] 매수 금액 부족으로(수량 0) 주문 생략.")
                continue
                
            logger.info(f"[{stk_nm}] 시장가 매수 주문 전송: {qty}주 (현재가 추정 {current_price}원, 총 {qty * current_price}원)")
            res = self.api.place_buy_order(stk_cd, qty)
            logger.info(f"주문 결과: {res}")
            
            # 체결 완료라 가정하고 포지션에 편입
            self.positions[stk_cd] = {
                "stk_nm": stk_nm,
                "qty": qty,
                "buy_price": current_price,
                "open_price": current_price,
                "price_914": 0,
                "sell_start": "1530",
                "sell_end": "1530",
                "trailing_stop_price": 0,
                "is_hit_upper": False,
                "is_sold": False
            }

    def determine_sell_time(self, profit_rate_914: float):
        """피닉스 전략의 수익률 기반 매도 시간대 산출"""
        if profit_rate_914 <= -0.09:
            return ("0924", "0927")
        elif -0.09 < profit_rate_914 <= -0.04:
            return ("0921", "0922")
        elif -0.04 < profit_rate_914 < 0.00:
            return ("0919", "0920")
        elif 0.00 <= profit_rate_914 <= 0.04:
            return ("0924", "0927")
        elif 0.04 < profit_rate_914 <= 0.09:
            return ("0920", "0924")
        else: 
            return ("0917", "0919")

    def run(self):
        logger.info("자동매매 봇(Daemon) 시작. 대기 중...")
        has_bought = False
        
        while True:
            try:
                now = datetime.now()
                market_status = self.get_market_status()
                hhmm = now.strftime("%H%M")
                
                # 매수 로직: 09시 정각
                if market_status == "OPEN" and not has_bought and hhmm == "0900":
                    self.targets = self.load_targets()
                    logger.info(f"개장. 총 {len(self.targets)}개 종목 매수 시도.")
                    self.execute_buying()
                    has_bought = True
                
                # 장중 폴링 로직: 1초마다 가격 조회 및 조건 검사
                if market_status == "OPEN" and has_bought:
                    for stk_cd, pos in list(self.positions.items()):
                        if pos["is_sold"]:
                            continue
                            
                        current_price = self.api.get_current_price(stk_cd)
                        if current_price <= 0:
                            continue
                            
                        # 09:14 수익률 업데이트 및 매도 시간 지정
                        if hhmm == "0914" and pos["price_914"] == 0:
                            pos["price_914"] = current_price
                            profit_rate_914 = (current_price - pos["buy_price"]) / pos["buy_price"]
                            start_t, end_t = self.determine_sell_time(profit_rate_914)
                            pos["sell_start"] = start_t
                            pos["sell_end"] = end_t
                            logger.info(f"[{pos['stk_nm']}] 09:14 수익률: {profit_rate_914*100:.2f}%. "
                                        f"목표 매도 시간: {start_t}~{end_t}")

                        # 전일 종가를 정확히 구할 수 있으면 상한가(1.3 * prev_close) 처리해야 함
                        # 여기서는 임시로 시초가 대비 +29% 도달 시 상한가 근접으로 간주
                        upper_limit = pos["open_price"] * 1.29 
                        
                        if current_price >= upper_limit * 0.99 and not pos["is_hit_upper"]:
                            pos["is_hit_upper"] = True
                            pos["trailing_stop_price"] = current_price * 0.92
                            logger.info(f"[{pos['stk_nm']}] 상한가 근접! 트레일링 스톱 활성화 ({pos['trailing_stop_price']:.0f}원)")

                        # 매도 로직: 상한가 트레일링 스톱 이탈
                        if pos["is_hit_upper"] and current_price <= pos["trailing_stop_price"]:
                            logger.info(f"[{pos['stk_nm']}] 트레일링 스톱 이탈 매도! (현재가 {current_price}원)")
                            self.api.place_sell_order(stk_cd, pos["qty"])
                            pos["is_sold"] = True
                            
                        # 매도 로직: 목표 시간대 도달 (상한가 미도달 시)
                        elif not pos["is_hit_upper"] and pos["sell_start"] <= hhmm <= pos["sell_end"]:
                            logger.info(f"[{pos['stk_nm']}] 목표 시간대({hhmm}) 도달! 시장가 매도. (현재가 {current_price}원)")
                            self.api.place_sell_order(stk_cd, pos["qty"])
                            pos["is_sold"] = True
                            
                        # 매도 로직: 종가 강제 청산 (15:20 이후)
                        elif hhmm >= "1520":
                            logger.info(f"[{pos['stk_nm']}] 장 마감 근접 강제 청산. (현재가 {current_price}원)")
                            self.api.place_sell_order(stk_cd, pos["qty"])
                            pos["is_sold"] = True

                # 장 마감 후 시스템 초기화
                if market_status == "AFTER_MARKET" and has_bought:
                    logger.info("장 마감. 자동매매 시스템 초기화 (내일을 위해 대기)")
                    has_bought = False
                    self.positions.clear()
                    
                time.sleep(1.0) # 1초 간격 폴링
                
            except KeyboardInterrupt:
                logger.info("Bot stopped manually.")
                sys.exit(0)
            except Exception as e:
                logger.error(f"예상치 못한 오류 발생: {e}")
                time.sleep(1.0)

if __name__ == "__main__":
    trader = AutoTrader()
    trader.run()

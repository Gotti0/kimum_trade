import os
import sys
import json
import time
import logging
import sqlite3
from datetime import datetime, time as time_obj
import math

# Add the project root directory to path to allow importing from backend
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(_project_root)

from backend.kiwoom.trade_api import KiwoomTradeAPI

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TARGET_FILE = os.path.join(_project_root, "docs", "auto_trade_targets.json")
HISTORY_FILE = os.path.join(_project_root, "docs", "auto_trade_history.json")
CONFIG_FILE = os.path.join(_project_root, "docs", "auto_trade_config.json")
DB_FILE = os.path.join(_project_root, "cache", "auto_trade_positions.db")

class AutoTrader:
    def __init__(self):
        self.api = KiwoomTradeAPI()
        self.targets = []
        self.config = self.load_config()
        self.buy_queue = []  # 매수에 실패하거나 아직 시도하지 못한 종목 큐
        self.init_db()
        self.load_positions()

    def init_db(self):
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    stk_cd TEXT PRIMARY KEY,
                    stk_nm TEXT,
                    qty INTEGER,
                    buy_price REAL,
                    open_price REAL,
                    prev_close REAL,
                    price_914 REAL,
                    sell_start TEXT,
                    sell_end TEXT,
                    trailing_stop_price REAL,
                    is_hit_upper INTEGER,
                    is_sold INTEGER
                )
            ''')
            conn.commit()

    def load_positions(self):
        self.positions = {}
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            for row in cursor.fetchall():
                pos = dict(row)
                pos["is_hit_upper"] = bool(pos["is_hit_upper"])
                pos["is_sold"] = bool(pos["is_sold"])
                pos["stk_cd"] = str(pos["stk_cd"])
                self.positions[pos["stk_cd"]] = pos

    def save_position(self, stk_cd, pos):
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO positions 
                (stk_cd, stk_nm, qty, buy_price, open_price, prev_close, price_914, sell_start, sell_end, trailing_stop_price, is_hit_upper, is_sold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                str(stk_cd), pos["stk_nm"], pos["qty"], pos["buy_price"], pos["open_price"], 
                pos["prev_close"], pos["price_914"], pos["sell_start"], pos["sell_end"], 
                pos["trailing_stop_price"], int(pos["is_hit_upper"]), int(pos["is_sold"])
            ))
            conn.commit()

    def clear_positions(self):
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions")
            conn.commit()
        self.positions = {}

    def load_config(self):
        default_config = {
            "buy_time": "0900",
            "evaluate_time": "0914",
            "force_close_time": "1520",
            "trailing_drop_rate": 0.08
        }
        if not os.path.exists(CONFIG_FILE):
            try:
                os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, indent=4)
            except Exception as e:
                logger.error(f"Failed to create config file: {e}")
            return default_config
            
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                return {**default_config, **loaded}
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return default_config
            
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

    def save_trade_history(self, stk_cd, pos, current_price, sell_reason):
        """매도 체결 내역을 기록"""
        try:
            history = []
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    try:
                        history = json.load(f)
                    except json.JSONDecodeError:
                        history = []
                    
            profit_amount = (current_price - pos["buy_price"]) * pos["qty"]
            profit_rate = (current_price - pos["buy_price"]) / pos["buy_price"]
            
            record = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "stk_cd": stk_cd,
                "stk_nm": pos["stk_nm"],
                "buy_price": pos["buy_price"],
                "sell_price": current_price,
                "qty": pos["qty"],
                "profit_amount": profit_amount,
                "profit_rate": profit_rate,
                "sell_reason": sell_reason
            }
            
            history.append(record)
            
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
                
            logger.info(f"[{pos['stk_nm']}] 매도 내역 저장 완료! (수익금: {profit_amount:,.0f}원)")
        except Exception as e:
            logger.error(f"Failed to save trade history: {e}")

    def execute_buying(self):
        """큐에 있는 타겟 종목들 매수 시도, 실패 시 큐에 남겨둠"""
        if not self.buy_queue:
            return

        remaining_queue = []
        for tg in self.buy_queue:
            stk_cd = tg["stk_cd"]
            stk_nm = tg["stk_nm"]
            buy_amount = tg["buy_amount"]
            
            try:
                # 현재가 조회해서 수량 계산 (시초가 조회)
                current_price = self.api.get_current_price(stk_cd)
                if current_price <= 0:
                    logger.warning(f"[{stk_nm}] 현재가 조회 실패. 다음 틱에 재시도합니다.")
                    remaining_queue.append(tg)
                    continue
                    
                qty = math.floor(buy_amount / current_price)
                if qty <= 0:
                    logger.warning(f"[{stk_nm}] 매수 금액 부족으로(수량 0) 주문 취소.")
                    continue
                    
                prev_close = self.api.get_previous_close(stk_cd)
                logger.info(f"[{stk_nm}] 전일 종가: {prev_close}원, 당일 시초가 추정: {current_price}원")
                    
                logger.info(f"[{stk_nm}] 시장가 매수 주문 전송: {qty}주 (총 {qty * current_price}원)")
                res = self.api.place_buy_order(stk_cd, qty)
                
                if res.get("return_code") == -1:
                    logger.warning(f"[{stk_nm}] API 거절됨. 잠시 후 재시도합니다: {res.get('return_msg')}")
                    remaining_queue.append(tg)
                    continue
                    
                logger.info(f"주문 결과: {res}")
                
                # 체결 완료라 가정하고 포지션에 편입
                pos = {
                    "stk_cd": stk_cd,
                    "stk_nm": stk_nm,
                    "qty": qty,
                    "buy_price": current_price,
                    "open_price": current_price,
                    "prev_close": prev_close if prev_close > 0 else current_price,
                    "price_914": 0,
                    "sell_start": "1530",
                    "sell_end": "1530",
                    "trailing_stop_price": 0,
                    "is_hit_upper": False,
                    "is_sold": False
                }
                self.positions[stk_cd] = pos
                self.save_position(stk_cd, pos)
                
            except Exception as e:
                logger.error(f"[{stk_nm}] 매수 처리 중 예외 발생, 재시도 큐 대기: {e}")
                remaining_queue.append(tg)
                time.sleep(0.5) # API Rate Limit 딜레이
                
        self.buy_queue = remaining_queue

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
        has_started_buying = len(self.positions) > 0 # 이미 일부 매수된 이력이 있는지
        
        while True:
            try:
                now = datetime.now()
                market_status = self.get_market_status()
                hhmm = now.strftime("%H%M")
                
                # 매수 윈도우 타임 계산 (+5분 여유)
                buy_time_start = self.config["buy_time"]
                from datetime import timedelta
                buy_dtes_start = datetime.strptime(buy_time_start, "%H%M")
                buy_time_end = (buy_dtes_start + timedelta(minutes=5)).strftime("%H%M")
                
                is_in_buy_window = buy_time_start <= hhmm <= buy_time_end
                
                # 매수 로직: 설정된 시간 윈도우에 진입했고, 오늘 아직 큐를 로드한 적이 없다면
                if market_status == "OPEN" and not has_started_buying and is_in_buy_window:
                    self.targets = self.load_targets()
                    self.buy_queue = list(self.targets)
                    logger.info(f"매수 윈도우 진입 ({buy_time_start}~{buy_time_end}). 총 {len(self.buy_queue)}개 종목 매수 큐 할당 완료.")
                    has_started_buying = True
                
                # 장중 매수 큐 소진 시도
                if market_status == "OPEN" and self.buy_queue:
                    self.execute_buying()
                
                # 장중 폴링 로직: 1초마다 가격 조회 및 조건 검사
                if market_status == "OPEN" and len(self.positions) > 0:
                    for stk_cd, pos in list(self.positions.items()):
                        if pos["is_sold"]:
                            continue
                            
                        current_price = self.api.get_current_price(stk_cd)
                        if current_price <= 0:
                            continue
                            
                        # 수익률 업데이트 및 매도 시간 지정 (기본 09:14)
                        evaluate_time = self.config["evaluate_time"]
                        if hhmm == evaluate_time and pos["price_914"] == 0:
                            pos["price_914"] = current_price
                            profit_rate_914 = (current_price - pos["buy_price"]) / pos["buy_price"]
                            start_t, end_t = self.determine_sell_time(profit_rate_914)
                            pos["sell_start"] = start_t
                            pos["sell_end"] = end_t
                            self.save_position(stk_cd, pos)
                            logger.info(f"[{pos['stk_nm']}] {evaluate_time} 수익률: {profit_rate_914*100:.2f}%. "
                                        f"목표 매도 시간: {start_t}~{end_t}")

                        # 전일 종가 기준 정확한 상한가(30%) 연산 적용
                        upper_limit = pos["prev_close"] * 1.30 
                        
                        if current_price >= upper_limit * 0.99 and not pos["is_hit_upper"]:
                            pos["is_hit_upper"] = True
                            drop_rate = self.config.get("trailing_drop_rate", 0.08)
                            pos["trailing_stop_price"] = current_price * (1.0 - drop_rate)
                            self.save_position(stk_cd, pos)
                            logger.info(f"[{pos['stk_nm']}] 상한가 근접! 트레일링 스톱 활성화 ({pos['trailing_stop_price']:.0f}원)")

                        # 매도 로직: 상한가 트레일링 스톱 이탈
                        if pos["is_hit_upper"] and current_price <= pos["trailing_stop_price"]:
                            try:
                                logger.info(f"[{pos['stk_nm']}] 트레일링 스톱 이탈 매도 시도! (현재가 {current_price}원)")
                                res = self.api.place_sell_order(stk_cd, pos["qty"])
                                if res.get("return_code") == -1:
                                    logger.warning(f"[{stk_nm}] 매도 거절됨. 다음 틱 재시도: {res.get('return_msg')}")
                                    continue
                                pos["is_sold"] = True
                                self.save_position(stk_cd, pos)
                                self.save_trade_history(stk_cd, pos, current_price, "Trailing Stop")
                            except Exception as e:
                                logger.error(f"[{pos['stk_nm']}] 매도 처리 중 예외 발생, 다음 폴링 대기: {e}")
                            
                        # 매도 로직: 목표 시간대 도달 (상한가 미도달 시)
                        elif not pos["is_hit_upper"] and pos["sell_start"] <= hhmm <= pos["sell_end"]:
                            try:
                                logger.info(f"[{pos['stk_nm']}] 목표 시간대({hhmm}) 도달! 시장가 매도 시도. (현재가 {current_price}원)")
                                res = self.api.place_sell_order(stk_cd, pos["qty"])
                                if res.get("return_code") == -1:
                                    logger.warning(f"[{stk_nm}] 매도 거절됨. 다음 틱 재시도: {res.get('return_msg')}")
                                    continue
                                pos["is_sold"] = True
                                self.save_position(stk_cd, pos)
                                self.save_trade_history(stk_cd, pos, current_price, "Target Time")
                            except Exception as e:
                                logger.error(f"[{pos['stk_nm']}] 매도 처리 중 예외 발생, 다음 폴링 대기: {e}")
                            
                        # 매도 로직: 종가 강제 청산
                        elif hhmm >= self.config["force_close_time"]:
                            try:
                                logger.info(f"[{pos['stk_nm']}] 장 마감 근접 강제 청산 시도. (현재가 {current_price}원)")
                                res = self.api.place_sell_order(stk_cd, pos["qty"])
                                if res.get("return_code") == -1:
                                    logger.warning(f"[{stk_nm}] 매도 거절됨. 다음 틱 재시도: {res.get('return_msg')}")
                                    continue
                                pos["is_sold"] = True
                                self.save_position(stk_cd, pos)
                                self.save_trade_history(stk_cd, pos, current_price, "End of Day")
                            except Exception as e:
                                logger.error(f"[{pos['stk_nm']}] 매도 처리 중 예외 발생, 다음 폴링 대기: {e}")

                # 장 마감 후 시스템 초기화
                if market_status == "AFTER_MARKET" and has_started_buying:
                    logger.info("장 마감. 자동매매 시스템 초기화 (내일을 위해 대기)")
                    has_started_buying = False
                    self.buy_queue = []
                    self.clear_positions()
                    
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

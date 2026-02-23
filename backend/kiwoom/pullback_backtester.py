"""
PullbackBacktester: 스윙-풀백 전략 백테스터.

파이프라인:
  1. 매일 루프에서 기존 포지션 매도 평가 (PullbackSellEngine)
  2. 전일 발생한 대기 주문(Pending Orders) 매수 체결 평가 (PullbackBuyEngine, 익일 시초가)
  3. 레짐 필터 기반 시장 국면 판별
  4. 당일 거래량 상위 100 종목에서 PullbackAlphaFilter 구동 → 통과 시 익일 대기 주문으로 등록

유니버스 구성 (미래 정보 편향 제거):
  - 캐시된 전체 일봉 데이터를 메모리에 로드
  - 매일 루프마다 해당 일자의 거래량(trde_qty) 기준 상위 100 종목을 동적으로 선별
  - 테마 조회(현재 기준 편향) 대신 과거 거래량 데이터로 유니버스 구축
"""

import os
import json
import time
import logging
from datetime import datetime

from backend.kiwoom.theme_finder import TopThemeFinder
from backend.kiwoom.risk_manager import RegimeFilter, PositionSizer
from backend.kiwoom.sell_strategy import _parse_price, compute_atr

from backend.kiwoom.pullback_alpha_filter import PullbackAlphaFilter
from backend.kiwoom.pullback_buy_strategy import PullbackBuyEngine
from backend.kiwoom.pullback_sell_strategy import PullbackSellEngine

logger = logging.getLogger(__name__)

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_project_root, "cache", "minute_charts")
DAILY_CACHE_DIR = os.path.join(_project_root, "cache", "daily_charts")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
API_DELAY = 0.35
FRICTION_COST = 0.00345


class PullbackBacktester:
    def __init__(self, initial_capital: float = 10_000_000, volume_top_n: int = 100):
        self.finder = TopThemeFinder()
        
        self.alpha_filter = PullbackAlphaFilter()
        self.buy_engine = PullbackBuyEngine(mode="daily")
        self.sell_engine = PullbackSellEngine(
            atr_period=14,
            stop_atr_multiplier=1.2,
            profit_atr_multiplier=1.5,
            max_holding_days=7
        )
        
        self.regime_filter = RegimeFilter()
        self.position_sizer = PositionSizer()
        
        self.initial_capital = initial_capital
        self.trading_days: list[str] = []
        
        # 미래 편향 제거: 캐시 일봉 전량 로드 & 거래량 기반 유니버스
        self.all_daily_charts: dict[str, list[dict]] = {}
        self.stock_name_map: dict[str, str] = {}
        self.volume_top_n = volume_top_n

    def _load_trading_days(self):
        kospi_bars = self._get_daily_chart_cached("005930")
        dates = [b['dt'] for b in kospi_bars if 'dt' in b]
        self.trading_days = sorted(list(set(dates)))
        if not self.trading_days:
            logger.error("영업일 목록 구축 실패.")
            
    def _get_trading_day_n_ago(self, n: int) -> str:
        if n <= 0 or n > len(self.trading_days):
            return ""
        return self.trading_days[-n]

    def _get_daily_chart_cached(self, stk_cd: str) -> list[dict]:
        # 인메모리 캐시 우선 조회
        if stk_cd in self.all_daily_charts:
            return self.all_daily_charts[stk_cd]
        cache_file = os.path.join(DAILY_CACHE_DIR, f"{stk_cd}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.all_daily_charts[stk_cd] = data
                    return data
            except Exception:
                pass
        time.sleep(API_DELAY)
        data = self.finder.get_daily_chart(stk_cd, datetime.now().strftime("%Y%m%d"))
        if data:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.all_daily_charts[stk_cd] = data
            return data
        return []

    def _get_daily_bars_up_to(self, stk_cd: str, target_date: str) -> list[dict]:
        bars = self._get_daily_chart_cached(stk_cd)
        return [b for b in bars if b.get('dt', '') <= target_date]

    def _get_daily_bar_for_date(self, stk_cd: str, target_date: str) -> dict:
        bars = self._get_daily_chart_cached(stk_cd)
        for b in reversed(bars):
            if b.get('dt') == target_date:
                return b
        return {}

    # ── 미래 편향 제거: 거래량 기반 유니버스 ──────────────────

    def _load_all_daily_charts(self):
        """캐시 디렉토리의 모든 일봉 JSON을 메모리에 일괄 로드합니다."""
        count = 0
        for fname in os.listdir(DAILY_CACHE_DIR):
            if not fname.endswith(".json"):
                continue
            stk_cd = fname.replace(".json", "")
            if stk_cd in self.all_daily_charts:
                count += 1
                continue
            cache_file = os.path.join(DAILY_CACHE_DIR, fname)
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    bars = json.load(f)
                if bars:
                    self.all_daily_charts[stk_cd] = bars
                    count += 1
            except Exception:
                pass
        logger.info("캐시 일봉 데이터 %d 종목 메모리 로드 완료.", count)

    def _fetch_stock_name_map(self):
        """ka10030(당일거래량상위) API로 종목코드→종목명 매핑을 구축합니다."""
        try:
            token = self.finder._get_token()
            url = f"{self.finder.domain}/api/dostk/rkinfo"
            headers = {
                "api-id": "ka10030",
                "authorization": f"Bearer {token}",
                "Content-Type": "application/json;charset=UTF-8",
            }
            payload = {
                "mrkt_tp": "000",        # 전체
                "sort_tp": "1",          # 거래량순
                "mang_stk_incls": "1",   # 관리종목 미포함
                "crd_tp": "0",           # 전체
                "trde_qty_tp": "0",      # 전체
                "pric_tp": "0",          # 전체
                "trde_prica_tp": "0",    # 전체
                "mrkt_open_tp": "0",     # 전체
                "stex_tp": "1",          # KRX
            }

            cont_yn = ""
            next_key = ""

            for _ in range(10):  # 최대 10페이지 연속조회
                if cont_yn == "Y":
                    headers["cont-yn"] = "Y"
                    headers["next-key"] = next_key

                resp = self.finder._request_with_retry(url, headers, payload)
                data = resp.json()

                items = data.get("tdy_trde_qty_upper", [])
                for item in items:
                    cd = item.get("stk_cd", "")
                    nm = item.get("stk_nm", "")
                    if cd and nm:
                        self.stock_name_map[cd] = nm

                cont_yn = resp.headers.get("cont-yn", "N")
                next_key = resp.headers.get("next-key", "")
                if cont_yn != "Y" or not items:
                    break
                time.sleep(API_DELAY)

            logger.info("종목명 매핑 %d건 구축 완료 (ka10030).", len(self.stock_name_map))
        except Exception as e:
            logger.warning("종목명 매핑 구축 실패: %s. 종목코드를 종목명으로 사용합니다.", e)

    def _get_volume_universe(self, date_str: str, top_n: int = 100) -> list[dict]:
        """주어진 날짜의 거래량 상위 N개 종목을 반환합니다.

        캐시된 과거 일봉 데이터만 사용하므로 미래 정보 편향이 없습니다.

        Args:
            date_str: 대상 영업일 (YYYYMMDD)
            top_n: 상위 몇 종목을 반환할지

        Returns:
            [{'stk_cd': ..., 'stk_nm': ...}, ...] 거래량 내림차순
        """
        volume_list: list[tuple[str, float]] = []
        for stk_cd, bars in self.all_daily_charts.items():
            # 역순 탐색으로 해당 날짜 바를 빠르게 찾기
            for b in reversed(bars):
                if b.get('dt', '') == date_str:
                    trde_qty = float(b.get('trde_qty', 0))
                    volume_list.append((stk_cd, trde_qty))
                    break

        volume_list.sort(key=lambda x: x[1], reverse=True)

        result: list[dict] = []
        for stk_cd, vol in volume_list[:top_n]:
            result.append({
                'stk_cd': stk_cd,
                'stk_nm': self.stock_name_map.get(stk_cd, stk_cd),
            })
        return result

    def run(self, start_days_ago: int = 60, use_daily_only: bool = True) -> dict:
        self._load_trading_days()
        self.buy_engine.mode = "daily" if use_daily_only else "minute"

        capital = self.initial_capital
        positions: list[dict] = []
        trades: list[dict] = []
        portfolio_history: list[dict] = []
        pending_orders: list[dict] = []

        data_mode = "일봉 전용" if use_daily_only else "분봉 기반(단순화)"
        logger.info("=" * 70)
        logger.info("  스윙-풀백(Swing-Pullback) 백테스팅 시작: %d 영업일 전 → 현재 [%s]", start_days_ago, data_mode)
        logger.info("  초기 자본금: %s원", f"{capital:,.0f}")
        logger.info("=" * 70)

        # 1. 캐시 일봉 전량 로드 & 종목명 매핑 (미래 정보 편향 제거)
        self._load_all_daily_charts()
        self._fetch_stock_name_map()
        logger.info("유니버스 풀: 캐시 %d 종목 / 종목명 매핑 %d건", len(self.all_daily_charts), len(self.stock_name_map))

        kospi_bars = self._get_daily_chart_cached("005930")

        # 2. 메인 일별 루프
        for day_offset in range(start_days_ago, 0, -1):
            current_date = self._get_trading_day_n_ago(day_offset)
            if not current_date:
                continue

            # 포지션에 대한 보유일 수 갱신
            for pos in positions:
                pos['days_held'] += 1

            # ----------------------------------------------------
            # A. 기존 포지션 매도 평가
            # ----------------------------------------------------
            closed_positions = []
            for pos in positions:
                stk_cd = pos["stk_cd"]
                day_bar = self._get_daily_bar_for_date(stk_cd, current_date)
                
                if day_bar:
                    open_price = _parse_price(str(day_bar.get('open_pric', 0)))
                    high = _parse_price(str(day_bar.get('high_pric', 0)))
                    low = _parse_price(str(day_bar.get('low_pric', 0)))
                    close = _parse_price(str(day_bar.get('cur_prc', 0)))
                else:
                    open_price = high = low = close = pos['entry_price']

                # TODO: minute mode
                sells = self.sell_engine.evaluate_sell(
                    stk_cd=stk_cd,
                    stk_nm=pos['stk_nm'],
                    position=pos,
                    date_str=current_date,
                    open_price=open_price,
                    high=high,
                    low=low,
                    close=close,
                    minute_bars=None  # 현재 daily 모드 중심
                )

                for sell in sells:
                    sell_qty = sell['qty']
                    sell_price_after_friction = sell['price'] * (1 - FRICTION_COST / 2)
                    ret = (sell_price_after_friction / pos['entry_price']) - 1
                    pnl = sell_price_after_friction * sell_qty - pos['entry_price'] * sell_qty
                    
                    capital += (sell_price_after_friction * sell_qty)
                    
                    trades.append({
                        "entry_date": pos["entry_date"],
                        "exit_date": current_date,
                        "exit_time": sell['time'],
                        "stk_cd": stk_cd,
                        "stk_nm": pos["stk_nm"],
                        "theme": pos.get("theme_nm", ""),
                        "buy_price": pos["entry_price"],
                        "sell_price": sell['price'],
                        "sell_price_after_friction": sell_price_after_friction,
                        "return_rate": ret * 100,
                        "pnl": pnl,
                        "reason": sell['reason']
                    })

                if pos['qty'] <= 0:
                    closed_positions.append(pos)

            for cp in closed_positions:
                positions.remove(cp)

            # ----------------------------------------------------
            # B. 대기 주문 (Pending Orders) 진입 처리 (익일 갭하락 방어 확인)
            # ----------------------------------------------------
            failed_orders = []
            for order in pending_orders:
                stk_cd = order['stk_cd']
                stk_nm = order['stk_nm']
                prev_close = order['prev_close']
                buy_amount = order['target_amount']
                
                daily_bars_up_to = self._get_daily_bars_up_to(stk_cd, current_date)
                
                buys = self.buy_engine.simulate_buy(
                    date_str=current_date,
                    stk_cd=stk_cd,
                    stk_nm=stk_nm,
                    prev_close=prev_close,
                    target_amount=buy_amount,
                    daily_bars=daily_bars_up_to,
                    minute_bars=None
                )
                
                if buys:
                    for buy in buys:
                        buy_price = buy['price']
                        buy_qty = buy['qty']
                        buy_amount_actual = buy_price * buy_qty
                        buy_price_after_friction = buy_price * (1 + FRICTION_COST / 2)
                        
                        if capital < buy_amount_actual:
                            logger.info(f"SKIP [{stk_cd}] {stk_nm}: 자본금 부족 (필요={buy_amount_actual:.0f}, 잔여={capital:.0f})")
                            continue
                        
                        capital -= buy_amount_actual
                        atr = order['atr']
                        
                        positions.append({
                            "stk_cd": stk_cd,
                            "stk_nm": stk_nm,
                            "theme_nm": order["theme_nm"],
                            "entry_date": current_date,
                            "entry_price": buy_price_after_friction,
                            "qty": buy_qty,
                            "atr": atr,
                            "is_partially_sold": False,
                            "days_held": 0
                        })
                        logger.info(f"BUY  [{stk_cd}] {stk_nm}: 매수가={buy_price_after_friction:.0f}, 수량={buy_qty}주, 스톱={(buy_price_after_friction - atr * 1.2):.0f}")
                else:
                    failed_orders.append(stk_cd)
                    
            pending_orders.clear()

            # ----------------------------------------------------
            # C. 레짐 필터 & 당일 알림목 필터링 → 대기 주문(Pending Orders) 생성
            # ----------------------------------------------------
            kospi_bars_up_to = [b for b in kospi_bars if b.get('dt', '') <= current_date]
            regime_result = self.regime_filter.detect_regime(kospi_bars_up_to)
            scale_factor = regime_result["scale_factor"]
            
            held_codes = {p["stk_cd"] for p in positions}
            available_slots = self.position_sizer.available_slots(len(positions))

            if available_slots > 0 and scale_factor > 0:
                # 당일 거래량 상위 N개 종목 동적 선별 (미래 정보 편향 없음)
                volume_candidates = self._get_volume_universe(current_date, top_n=self.volume_top_n)

                daily_bars_map = {}
                for stk in volume_candidates:
                    stk_cd = stk['stk_cd']
                    if stk_cd not in held_codes:
                        bars = self._get_daily_bars_up_to(stk_cd, current_date)
                        if len(bars) >= 25:  # 최소 25개 요구(20일 ADTV + Surge 스캔)
                            daily_bars_map[stk_cd] = bars

                passed_stocks = self.alpha_filter.screen_universe(
                    [s for s in volume_candidates if s['stk_cd'] not in held_codes],
                    daily_bars_map
                )

                for stk in passed_stocks[:available_slots]:
                    stk_cd = stk["stk_cd"]
                    stk_nm = stk["stk_nm"]
                    
                    # 진입 규모 및 ATR 산출
                    bars = daily_bars_map[stk_cd]
                    today_close = _parse_price(str(bars[-1].get('cur_prc', 0)))
                    
                    if today_close <= 0: continue
                    
                    atr = compute_atr(bars, self.sell_engine.atr_period)
                    if not atr: atr = today_close * 0.02
                    
                    sizing = self.position_sizer.compute_position_size(capital, today_close, atr)
                    position_amount = self.position_sizer.apply_regime_scale(sizing["position_amount"], scale_factor)
                    
                    if position_amount > 0 and capital >= position_amount:
                        pending_orders.append({
                            'stk_cd': stk_cd,
                            'stk_nm': stk_nm,
                            'theme_nm': stk.get("theme_nm", ""),
                            'prev_close': today_close,
                            'target_amount': position_amount,
                            'atr': atr
                        })
                        logger.info(f"PENDING [{stk_cd}] {stk_nm}: 눌림목 통과. 익일 매수 대기 (목표금액={position_amount:.0f})")

            # ----------------------------------------------------
            # D. 포트폴리오 가치 기록
            # ----------------------------------------------------
            port_val = capital
            for pos in positions:
                stk_cd = pos["stk_cd"]
                day_bar = self._get_daily_bar_for_date(stk_cd, current_date)
                close_px = _parse_price(str(day_bar.get('cur_prc', pos['entry_price']))) if day_bar else pos['entry_price']
                port_val += (close_px * pos['qty'])
                
            portfolio_history.append({
                "date": current_date,
                "capital": capital,
                "position_value": port_val - capital,
                "total_value": port_val
            })

        # 루프 종료 후 남은 위치 청산 등은 생략하거나 표시.
        final_capital = portfolio_history[-1]['total_value'] if portfolio_history else capital
        total_return = ((final_capital / self.initial_capital) - 1) * 100

        win_trades = [t for t in trades if t["pnl"] > 0]
        loss_trades = [t for t in trades if t["pnl"] <= 0]
        win_rate = len(win_trades) / len(trades) * 100 if trades else 0

        summary = (
            f"=== Pullback Strategy Backtest ===\n"
            f"Period: {start_days_ago} days ago ~ present\n"
            f"Initial Capital: {self.initial_capital:,.0f}\n"
            f"Final Capital:   {final_capital:,.0f}\n"
            f"Total Return:    {total_return:.2f}%\n"
            f"Total Trades:    {len(trades)}\n"
            f"Win Rate:        {win_rate:.1f}%\n"
            f"Winning Trades:  {len(win_trades)}\n"
            f"Losing Trades:   {len(loss_trades)}\n"
        )
        logger.info("\n" + summary)

        return {
            "initial_capital": self.initial_capital,
            "final_capital": final_capital,
            "total_return": total_return,
            "trade_count": len(trades),
            "trades": trades,
            "portfolio_history": portfolio_history,
            "summary": summary
        }

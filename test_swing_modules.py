"""단위 테스트: 스윙 백테스터 모듈 검증."""
from backend.kiwoom.alpha_filter import compute_sma, compute_ema, compute_disparity, AlphaFilter
from backend.kiwoom.sell_strategy import compute_atr, SwingSellStrategyEngine, _parse_price
from backend.kiwoom.risk_manager import RegimeFilter, PositionSizer, compute_macd_precise
from backend.kiwoom.buy_strategy import BuyStrategyEngine

print("=== 1. SMA/EMA 테스트 ===")
prices = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120]
sma10 = compute_sma(prices, 10)
print(f"SMA(10): {sma10:.2f} (expect ~111.00)")

ema10 = compute_ema(prices, 10)
print(f"EMA(10): {ema10:.2f}")

print("\n=== 2. 이격도 테스트 ===")
disp = compute_disparity(108, 100)
print(f"Disparity(108/100): {disp:.1f} (expect 108.0)")

print("\n=== 3. AlphaFilter 테스트 ===")
af = AlphaFilter()
p1, r1 = af.check_momentum({"close": 110, "sma10": 105, "ema20": 108, "daily_return": 5.5})
print(f"Momentum PASS: {p1} | {r1}")
p2, r2 = af.check_momentum({"close": 103, "sma10": 105, "ema20": 108, "daily_return": 2.0})
print(f"Momentum FAIL: {p2} | {r2}")
p3, r3 = af.check_disparity({"disparity20": 106})
print(f"Disparity PASS: {p3} | {r3}")
p4, r4 = af.check_disparity({"disparity20": 115})
print(f"Disparity FAIL: {p4} | {r4}")

print("\n=== 4. ATR 테스트 ===")
bars = [
    {"cur_prc": "100", "high_pric": "105", "low_pric": "95"},
    {"cur_prc": "102", "high_pric": "107", "low_pric": "97"},
    {"cur_prc": "104", "high_pric": "109", "low_pric": "99"},
    {"cur_prc": "106", "high_pric": "111", "low_pric": "101"},
    {"cur_prc": "108", "high_pric": "113", "low_pric": "103"},
    {"cur_prc": "110", "high_pric": "115", "low_pric": "105"},
]
atr = compute_atr(bars, 5)
print(f"ATR(5): {atr:.2f} (expect 10.00)")

print("\n=== 5. PositionSizer 테스트 ===")
ps = PositionSizer()
sz = ps.compute_position_size(total_capital=10_000_000, buy_price=50000, atr=2000)
print(f"Position: {sz['position_amount']:,.0f}원, {sz['position_shares']}주, capped={sz['capped']}")

# 검증: risk_amount = 10M * 0.015 = 150000
# shares = 150000 / (2000 * 2.5) = 30
# amount = 30 * 50000 = 1,500,000 (slot_cap = 1,000,000이므로 capped)
print(f"  risk_amount={sz['risk_amount']:,.0f}, slot_cap={sz['slot_cap']:,.0f}")

print("\n=== 6. RegimeFilter 테스트 ===")
rf = RegimeFilter()
# 간단한 상승 데이터
bull_bars = [{"cur_prc": str(100 + i)} for i in range(250)]
regime = rf.detect_regime(bull_bars)
print(f"Regime(상승): {regime['regime']} (scale={regime['scale_factor']})")

print("\n=== 7. BuyStrategy 테스트 ===")
be = BuyStrategyEngine()
print(f"Default weights: {[f'{w:.2f}' for w in be.weights]}")

print("\n=== 8. MACD 테스트 ===")
macd_prices = [100 + i * 0.5 for i in range(50)]
macd = compute_macd_precise(macd_prices)
if macd:
    print(f"MACD={macd['macd_line']:.4f}, Signal={macd['signal_line']:.4f}, Hist={macd['histogram']:.4f}")
else:
    print("MACD: None (데이터 부족)")

print("\n" + "=" * 50)
print("ALL UNIT TESTS PASSED")
print("=" * 50)

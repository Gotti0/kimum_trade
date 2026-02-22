
import unittest
from backend.kiwoom.sell_strategy import SellStrategyEngine

class TestSellStrategy(unittest.TestCase):
    def setUp(self):
        self.engine = SellStrategyEngine()
        self.buy_price = 10000.0
        self.upper_limit = 13000.0
        
    def test_standard_sell_profit(self):
        # 09:14 price is 11000 (+10% from 10000 open)
        # return_rate > 9 -> sell 09:17~09:19
        bars = [
            {"cntr_tm": "20260222090100", "cur_prc": "10000"},
            {"cntr_tm": "20260222091400", "cur_prc": "11000"},
            {"cntr_tm": "20260222091700", "cur_prc": "11500"},
            {"cntr_tm": "20260222091800", "cur_prc": "11600"},
            {"cntr_tm": "20260222091900", "cur_prc": "11700"}, # Final sell price
        ]
        result = self.engine.execute(bars, self.buy_price, self.upper_limit)
        self.assertEqual(result["sell_price"], 11700.0)
        self.assertEqual(result["sell_time"], "0919")
        self.assertIn("0917~0919", result["sell_reason"])

    def test_upper_limit_trailing_stop(self):
        # Hits upper limit at 09:05
        # Trailing stop at 13000 * 0.92 = 11960
        bars = [
            {"cntr_tm": "20260222090100", "cur_prc": "10000"},
            {"cntr_tm": "20260222090500", "high_pric": "13000", "cur_prc": "13000"},
            {"cntr_tm": "20260222091600", "low_pric": "12500", "cur_prc": "12500"},
            {"cntr_tm": "20260222092000", "low_pric": "11900", "cur_prc": "11900"}, # Triggers stop
        ]
        result = self.engine.execute(bars, self.buy_price, self.upper_limit)
        self.assertTrue(result["hit_upper_limit"])
        self.assertEqual(result["sell_price"], 11960.0)
        self.assertIn("트레일링스톱", result["sell_reason"])

if __name__ == "__main__":
    unittest.main()

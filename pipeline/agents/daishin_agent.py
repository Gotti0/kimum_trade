import win32com.client
import pythoncom
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DaishinAgent:
    def __init__(self):
        self.cybos = None
    
    def wait_for_login(self, timeout=300):
        """
        Wait for the user to manually log in to Cybos/Creon Plus.
        timeout: Maximum time to wait in seconds (default 5 minutes).
        """
        logger.info("Initializing COM environment and waiting for Daishin HTS login...")
        pythoncom.CoInitialize()
        
        try:
            self.cybos = win32com.client.Dispatch("CpUtil.CpCybos")
        except Exception as e:
            logger.error(f"Failed to dispatch CpUtil.CpCybos. Is Daishin Starter running? {e}")
            return False

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.cybos.IsConnect == 1:
                logger.info("Daishin HTS is connected successfully.")
                # Wait a few seconds for the internal server to load stock lists
                time.sleep(5)
                return True
            time.sleep(2)
            logger.info("Waiting for manual login...")
            
        logger.error(f"Login wait timed out after {timeout} seconds.")
        return False
        
    def _check_rate_limit(self):
        """
        Check TR limit and sleep dynamically.
        """
        if not self.cybos:
            return
        
        remain_count = self.cybos.GetLimitRemainCount(1) # 1: 시세조회(RQ) 제한
        remain_time = self.cybos.GetLimitRemainTime(1)
        
        # Logging limit status if low
        if remain_count <= 5:
            logger.warning(f"Rate Limit Warning: Only {remain_count} requests left. Throttling active.")
            
        if remain_count <= 2:
            sleep_time = (remain_time / 1000) + 0.1 # milliseconds to seconds + buffer
            if sleep_time > 0:
                logger.info(f"Rate Limit Reached: Sleeping for {sleep_time:.2f} seconds.")
                time.sleep(sleep_time)

    def get_minute_chart(self, code, target_count, since_date=None, since_time=None):
        """
        Fetch minute chart data from Daishin API.
        Returns a list of dictionaries with pure python types.
        """
        pythoncom.CoInitialize()
        
        try:
             chart = win32com.client.Dispatch("CpSysDib.StockChart")
        except Exception as e:
            logger.error(f"Failed to dispatch CpSysDib.StockChart: {e}")
            return None

        chart.SetInputValue(0, code)
        chart.SetInputValue(1, ord('2')) # 2: 개수 기준
        chart.SetInputValue(4, target_count) # 요청 개수
        chart.SetInputValue(5, (0, 1, 2, 3, 4, 5, 8)) # 날짜, 시간, 시, 고, 저, 종, 거래량
        chart.SetInputValue(6, ord('m')) # m: 분봉
        chart.SetInputValue(7, 1) # 1분봉
        chart.SetInputValue(9, ord('1')) # 1: 수정주가 적용 (매우 중요)
        
        result_data = []
        collected_count = 0
        reached_old_data = False
        
        while collected_count < target_count:
            # Check rate limits before blocking HTTP request
            self._check_rate_limit()
            
            # Request
            chart.BlockRequest()
            
            # Check status
            rq_status = chart.GetDibStatus()
            rq_msg = chart.GetDibMsg1()
            
            if rq_status != 0:
                logger.error(f"Data request failed: {rq_msg} (Status: {rq_status})")
                break
                
            # Process received data
            num_data = chart.GetHeaderValue(3) # 수신개수
            
            if num_data == 0:
                logger.info("No more data to receive.")
                break
                
            for i in range(num_data):
                item_date = chart.GetDataValue(0, i)
                item_time = chart.GetDataValue(1, i)
                
                # If we have reached or passed the cached boundary
                if since_date is not None:
                    # Depending on how the API returns data, we check if the current record is older or equal
                    # to the last cached record. Since we read newest first, we will eventually hit older dates.
                    if item_date < since_date or (item_date == since_date and item_time <= since_time):
                        reached_old_data = True
                        break # Skip adding this and stop fetching

                item = {
                    "date": item_date,
                    "time": item_time,
                    "open": chart.GetDataValue(2, i),
                    "high": chart.GetDataValue(3, i),
                    "low": chart.GetDataValue(4, i),
                    "close": chart.GetDataValue(5, i),
                    "volume": chart.GetDataValue(6, i)
                }
                result_data.append(item)
                
            collected_count += num_data
            
            if reached_old_data:
                logger.info(f"Reached previously cached data up to {since_date} {since_time}. Stopping fetch.")
                break
            
            # Check if there is more data
            if not chart.Continue:
                logger.info("All available data downloaded (No 'Continue' flag).")
                break
                
        # API often returns data in reverse chronological order (newest to oldest). 
        # So we sort it chronologically.
        result_data.sort(key=lambda x: (x['date'], x['time']))
        
        # Trim if we collected more than target
        logger.info(f"Successfully fetched {len(result_data)} minute chart records for {code}.")
        return result_data

    def get_stock_info(self, code):
        """
        Fetch company metadata: Market Cap, Sector, Listing Market, ATS status
        """
        pythoncom.CoInitialize()
        
        info = {
            "MarketType": None,
            "Sector": None,
            "MarketCap": None,
            "ATS_Nextrade": "N",
        }
        
        try:
            # 1. Base Info from CpCodeMgr
            code_mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
            
            # Market Type (1: KOSPI, 2: KOSDAQ, etc)
            market_kind = code_mgr.GetStockMarketKind(code)
            if market_kind == 1: info["MarketType"] = "코스피"
            elif market_kind == 2: info["MarketType"] = "코스닥"
            else: info["MarketType"] = str(market_kind)
            
            # ATS (Nextrade)
            if code_mgr.IsNxtTrdPsbl(code):
                 info["ATS_Nextrade"] = "Y"
                 
            # Sector
            ind_code = code_mgr.GetStockIndustryCode(code)
            if ind_code:
                 info["Sector"] = code_mgr.GetIndustryName(ind_code)
                 
            # 2. Market Cap from StockMst (Requires BlockRequest)
            self._check_rate_limit()
            mst = win32com.client.Dispatch("DsCbo1.StockMst")
            mst.SetInputValue(0, code)
            mst.BlockRequest()
            
            # HeaderValue(31): 상장주식수, HeaderValue(11): 종가
            # 시가총액(백만원) = HeaderValue(31) * HeaderValue(11) // 1,000,000
            # 하지만 엑셀에는 '(억원)' 컬럼으로 들어가므로 억원 단위로 변환 필요
            # HeaderValue(31) (상장주식수), HeaderValue(11)(현재가) => 시가총액(억원) = (주식수 * 현재가) / 100,000,000
            shares_raw = mst.GetHeaderValue(31)
            price_raw = mst.GetHeaderValue(11)
            
            if shares_raw is not None and price_raw is not None:
                 shares = int(shares_raw)
                 price = int(price_raw)
                 market_cap_100m = (shares * price) // 100000000
                 info["MarketCap"] = market_cap_100m
                 
        except Exception as e:
            logger.error(f"Failed to fetch stock info for {code}: {e}")
            
        return info

    def fetch_multi_stock_info(self, tickers: list):
        """
        Fetch company metadata for up to 200 stocks at once using MarketEye and CpCodeMgr.
        Returns a dictionary mapping stock codes to their info dictionaries.
        """
        pythoncom.CoInitialize()
        results = {}
        
        if not tickers:
            return results
            
        # Ensure we don't exceed MarketEye's 200 ticker limit.
        if len(tickers) > 200:
            logger.warning(f"MarketEye accepts max 200 tickers. Received {len(tickers)}. Truncating.")
            tickers = tickers[:200]
            
        try:
            code_mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
            
            # 1. Populate basic info from CpCodeMgr (Local memory, fast)
            for code in tickers:
                info = {
                    "MarketType": None,
                    "Sector": None,
                    "MarketCap": None,
                    "ATS_Nextrade": "N",
                }
                
                # Market Type
                market_kind = code_mgr.GetStockMarketKind(code)
                if market_kind == 1: info["MarketType"] = "코스피"
                elif market_kind == 2: info["MarketType"] = "코스닥"
                else: info["MarketType"] = str(market_kind)
                
                # ATS
                if code_mgr.IsNxtTrdPsbl(code):
                     info["ATS_Nextrade"] = "Y"
                     
                # Sector
                ind_code = code_mgr.GetStockIndustryCode(code)
                if ind_code:
                     info["Sector"] = code_mgr.GetIndustryName(ind_code)
                     
                results[code] = info

            # 2. Fetch MarketCap for all tickers at once using MarketEye
            self._check_rate_limit()
            market_eye = win32com.client.Dispatch("CpSysDib.MarketEye")
            
            # Fields: 0(Code), 4(Close Price), 31(Listed Shares) - using MarketEye codes
            # According to docs: 0: 종목코드, 4: 현재가
            # However, MarketEye uses different field codes than StockMst.
            # 0: 종목코드, 4: 현재가, 20: 상장주식수 (단위: 1주)
            req_fields = [0, 4, 20]
            sorted_fields = sorted(req_fields) # Prevent COM auto-sorting bug
            
            market_eye.SetInputValue(0, sorted_fields)
            market_eye.SetInputValue(1, tickers)
            # Remove SetInputValue(2) and (3) because they trigger Type Mismatch (형식 불일치) COM errors 
            # depending on the Cybos Plus version/registration. Defaults will be used.
            
            market_eye.BlockRequest()
            
            if hasattr(market_eye, 'GetDibStatus') and market_eye.GetDibStatus() != 0:
                 logger.error(f"[MarketEye] API Communication Error: {market_eye.GetDibMsg1()}")
                 return results
                 
            field_count = market_eye.GetHeaderValue(0)
            stock_count = market_eye.GetHeaderValue(2)
            
            for row_idx in range(stock_count):
                code_val = None
                price_val = None
                shares_val = None
                
                for col_idx in range(field_count):
                    actual_field_code = sorted_fields[col_idx]
                    val = market_eye.GetDataValue(col_idx, row_idx)
                    
                    if actual_field_code == 0: code_val = val
                    elif actual_field_code == 4: price_val = val
                    elif actual_field_code == 20: shares_val = val
                
                if code_val and code_val in results and price_val is not None and shares_val is not None:
                     # Calculate MarketCap (억원)
                     # MarketEye shares is absolute number, price is KRW
                     price = int(price_val)
                     shares = int(shares_val)
                     market_cap_100m = (shares * price) // 100000000
                     results[code_val]["MarketCap"] = market_cap_100m
                     
        except Exception as e:
            logger.error(f"Failed to fetch multi-stock info: {e}")
            
        return results

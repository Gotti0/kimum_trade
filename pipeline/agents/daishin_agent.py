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

    def get_minute_chart(self, code, target_count):
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
                item = {
                    "date": chart.GetDataValue(0, i),
                    "time": chart.GetDataValue(1, i),
                    "open": chart.GetDataValue(2, i),
                    "high": chart.GetDataValue(3, i),
                    "low": chart.GetDataValue(4, i),
                    "close": chart.GetDataValue(5, i),
                    "volume": chart.GetDataValue(6, i)
                }
                result_data.append(item)
                
            collected_count += num_data
            
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

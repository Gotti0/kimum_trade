import os
import logging

# Base Settings
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# Daishin API Configuration
DAISHIN_BRIDGE_URL = "http://localhost:8000/api/dostk/chart"
DAISHIN_CACHE_DIR = os.path.join(BASE_DIR, "cache_daishin")
DAISHIN_MAX_MINUTE_COUNT = 180000

def get_logger(name, filename):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        file_handler = logging.FileHandler(os.path.join(LOG_DIR, filename), encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)
        logger.addHandler(logging.StreamHandler())
    return logger

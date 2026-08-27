import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
MODELS_DIR = BASE_DIR / 'models'
DB_PATH = DATA_DIR / 'database.db'

SEARCH_TIMEOUT = int(os.getenv('SEARCH_TIMEOUT', 30))
MAX_ARTICLES = int(os.getenv('MAX_ARTICLES', 10))
SEARXNG_URL = ""
RANDOM_DELAY_MAX = float(os.getenv('RANDOM_DELAY_MAX', 2))
RANDOM_DELAY_MIN = float(os.getenv('RANDOM_DELAY_MIN', 0.6))

HOST = '0.0.0.0'
PORT = '63077'
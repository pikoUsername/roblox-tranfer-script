from functools import lru_cache
from typing import List

from pydantic import BaseSettings
from app.consts import DEFAULT_QUEUE_NAME, DEFAULT_EXCHANGE_NAME, DEFAULT_SEND_NAME, DEFAULT_SEND_EXCHANGE_NAME


class Settings(BaseSettings):
    db_dsn: str
    db_type: str = "postgres"
    db_tokens_table: str
    queue_dsn: str

    window_size: str = "1920,1080"

    send_queue_name: str = DEFAULT_SEND_NAME
    queue_name: str = DEFAULT_QUEUE_NAME
    exchange_name: str = DEFAULT_EXCHANGE_NAME
    send_queue_exchange_name: str = DEFAULT_SEND_EXCHANGE_NAME

    user_agent: str = "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)" \
                      "AppleWebKit/537.36 (KHTML, like Gecko)" \
                      "Chrome/87.0.4280.141 Safari/537.36"

    debug: bool = True
    disabled: bool = True
    browser: str = "Chrome"
    browser_dsn: str = ""  # uses only when we are using remote browser

    loggers: List[str] = []

    class Config:
        validate_assignment = True
        env_file = "../.env"


@lru_cache
def get_settings():
    setting = Settings()
    return setting

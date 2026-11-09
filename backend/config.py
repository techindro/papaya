import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PAYPAL_CLIENT_ID: str = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_BASE_URL: str = "https://paypal.com"
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    class Config:
        env_file = ".env"

settings = Settings()

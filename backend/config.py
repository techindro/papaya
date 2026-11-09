from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PAYPAL_CLIENT_ID: str = "YOUR_SANDBOX_CLIENT_ID"
    PAYPAL_CLIENT_SECRET: str = "YOUR_SANDBOX_CLIENT_SECRET"
    PAYPAL_BASE_URL: str = "https://paypal.com"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TOPIC: str = "papaya_telemetry"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()


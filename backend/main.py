import json
import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from kafka import KafkaProducer
from backend.config import settings

app = FastAPI(title="Papaya Autonomous Compute Transaction API", version="1.0.0")

# Initialize Kafka producer loosely coupled to allow standalone backend execution
try:
    producer = KafkaProducer(
        bootstrap_servers=[settings.KAFKA_BOOTSTRAP_SERVERS],
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        acks='all',
        retries=3
    )
except Exception:
    producer = None

class TelemetryPayload(BaseModel):
    cpu_usage: float = Field(..., ge=0.0, le=100.0)
    memory_usage: float = Field(..., ge=0.0, le=100.0)
    token_stream_count: int = Field(..., ge=0)

def calculate_pricing_score(payload: TelemetryPayload) -> float:
    base_charge = (payload.cpu_usage + payload.memory_usage) * 0.001
    token_charge = payload.token_stream_count * 0.0005
    final_cost = round(base_charge + token_charge, 2)
    return max(final_cost, 0.01)

async def get_paypal_access_token() -> str:
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Missing configuration values."
        )
    url = f"{settings.PAYPAL_BASE_URL}/v1/oauth2/token"
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    data = {"grant_type": "client_credentials"}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, 
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET), 
            headers=headers, 
            data=data,
            timeout=10.0
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed with financial gate."
            )
        return response.json().get("access_token", "")

@app.post("/api/compute/settle", status_code=status.HTTP_200_OK)
async def settle_compute_transaction(telemetry: TelemetryPayload):
    cost = calculate_pricing_score(telemetry)
    access_token = await get_paypal_access_token()
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    order_url = f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders"
    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": "USD",
                "value": f"{cost:.2f}"
            },
            "description": f"CPU: {telemetry.cpu_usage}%, Tokens: {telemetry.token_stream_count}"
        }]
    }
    
    async with httpx.AsyncClient() as client:
        order_res = await client.post(order_url, json=order_payload, headers=headers, timeout=10.0)
        if order_res.status_code != 201:
            raise HTTPException(status_code=400, detail="Failed to initialize session.")
        
        order_id = order_res.json().get("id", "")
        capture_url = f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture"
        capture_res = await client.post(capture_url, headers=headers, timeout=10.0)
        
        if capture_res.status_code not in:
            raise HTTPException(status_code=400, detail="Capture execution failed.")
        
        capture_data = capture_res.json()
        response_packet = {
            "status": "SUCCESS",
            "transaction_id": order_id,
            "allocated_cost_usd": cost,
            "paypal_status": capture_data.get("status", "COMPLETED"),
            "telemetry_verified": {
                "cpu_load_percent": telemetry.cpu_usage,
                "processed_tokens": telemetry.token_stream_count
            }
        }

        # Stream event packet asynchronously to Kafka if initialized
        if producer:
            try:
                producer.send("papaya_telemetry", value=response_packet)
            except Exception:
                pass

        return response_packet

from fastapi import FastAPI
from pydantic import BaseModel
import os

app = FastAPI(title="Nevsky API", version="0.1.0")

class HealthResponse(BaseModel):
    status: str
    environment: str

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        environment=os.getenv("ENVIRONMENT", "unknown"),
    )

@app.get("/ready")
def ready():
    return {"ready": True}

"""FastAPI application entry point."""

import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.config import settings
from app.schemas import HeyReachWebhookPayload, HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown - cleanup resources
    from app.services.heyreach import _client as heyreach_client

    if heyreach_client:
        await heyreach_client.close()


app = FastAPI(
    title="Speed to Lead",
    description="HeyReach webhook handler with AI-powered LinkedIn reply suggestions",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for Railway."""
    return HealthResponse(
        status="healthy",
        environment=settings.environment,
    )


async def process_incoming_message(payload: HeyReachWebhookPayload) -> dict:
    """Process an incoming message from HeyReach webhook.

    This function orchestrates:
    1. Upserting conversation record
    2. Generating AI draft via DeepSeek
    3. Sending draft to Telegram for approval

    Args:
        payload: The webhook payload from HeyReach.

    Returns:
        Dict with draft_id if successful.
    """
    # TODO: Implement full processing pipeline
    # For now, return a placeholder
    return {"draft_id": str(uuid.uuid4())}


@app.post("/webhook/heyreach")
async def heyreach_webhook(
    payload: HeyReachWebhookPayload,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive webhook from HeyReach when a reply is received.

    This endpoint:
    1. Validates the incoming payload
    2. Triggers background processing (AI draft, Telegram notification)
    3. Returns immediately to acknowledge receipt

    Args:
        payload: The webhook payload from HeyReach.
        background_tasks: FastAPI background tasks handler.

    Returns:
        Acknowledgment response.
    """
    # Queue processing in background to respond quickly
    background_tasks.add_task(process_incoming_message, payload)

    return {
        "status": "received",
        "lead_id": payload.lead_id,
    }

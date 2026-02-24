"""Gift leads pipeline — full research fallback for the Slack gift leads button."""

from app.services.gift_pipeline.orchestrator import run_gift_leads_pipeline_async

__all__ = ["run_gift_leads_pipeline_async"]

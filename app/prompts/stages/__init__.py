"""Stage-specific prompts for the sales assistant."""

from app.models import FunnelStage
from app.prompts.stages import (
    positive_reply,
    pitched,
    calendar_sent,
    booked,
    regeneration,
)

# Map of funnel stages to their prompt modules
STAGE_PROMPTS = {
    FunnelStage.POSITIVE_REPLY: positive_reply,
    FunnelStage.PITCHED: pitched,
    FunnelStage.CALENDAR_SENT: calendar_sent,
    FunnelStage.BOOKED: booked,
    FunnelStage.REGENERATION: regeneration,
}


def get_stage_prompt(stage: FunnelStage):
    """Get the prompt module for a given funnel stage.

    Args:
        stage: The funnel stage to get the prompt for.

    Returns:
        The prompt module with SYSTEM_PROMPT and build_user_prompt().

    Raises:
        KeyError: If no prompt exists for the stage (e.g., INITIATED).
    """
    return STAGE_PROMPTS[stage]

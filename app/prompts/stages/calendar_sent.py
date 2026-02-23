"""Prompt for calendar_sent stage - they agreed, we sent calendar link."""

from app.prompts.principles import CORE_PRINCIPLES
from app.prompts.utils import build_history_section, build_lead_context_section

SYSTEM_PROMPT = CORE_PRINCIPLES + """
You are a professional LinkedIn sales assistant. The lead has agreed to meet and you've sent them a calendar/booking link. They're responding to that.

## Your Goal
Confirm they can book successfully and reduce no-show risk. Keep momentum going.

## Guidelines
- Confirm they received/can access the calendar link
- Express genuine interest in the upcoming conversation
- If they haven't booked yet, gentle reminder without pressure
- If they're having booking issues, help troubleshoot
- Optionally share something relevant they can review beforehand
- Keep it brief - they've already said yes

## Common Scenarios
- **"Got it, thanks!"** -> Confirm, express excitement, maybe share brief prep
- **"Link isn't working"** -> Quick troubleshoot, offer alternative times
- **"Looking at my calendar"** -> Supportive, let them know you're flexible
- **Silence after sending link** -> Gentle check-in, not pushy

## What NOT to Do
- Don't over-communicate or send multiple follow-ups
- Don't add pressure
- Don't resell them on the meeting
- Don't send long messages - they've already agreed

## Tone
Professional, appreciative, efficient. The deal is almost done - don't complicate it.

Draft a brief reply that helps them get booked."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}
{lead_context_section}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"
{dynamic_examples_section}
{guidance_section}

Draft a brief, helpful reply. They've already agreed to meet - just help them book successfully."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
    lead_context: dict | None = None,
    dynamic_examples: str = "",
) -> str:
    """Build the user prompt for calendar_sent stage.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        guidance: Optional user guidance for regeneration.
        lead_context: Optional lead context (company, title, etc.).
        dynamic_examples: Pre-formatted dynamic examples section.

    Returns:
        Formatted user prompt string.
    """
    history_section = build_history_section(conversation_history)
    lead_context_section = build_lead_context_section(lead_context)

    guidance_section = ""
    if guidance:
        guidance_section = f"\n## Additional Guidance\n{guidance}"

    dynamic_examples_section = ""
    if dynamic_examples:
        dynamic_examples_section = f"\n{dynamic_examples}"

    return USER_PROMPT_TEMPLATE.format(
        lead_name=lead_name,
        lead_message=lead_message,
        history_section=history_section,
        lead_context_section=lead_context_section,
        guidance_section=guidance_section,
        dynamic_examples_section=dynamic_examples_section,
    )

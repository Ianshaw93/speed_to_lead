"""Prompt for booked stage - they've booked a meeting time."""

from app.prompts.principles import CORE_PRINCIPLES
from app.prompts.utils import build_history_section, build_lead_context_section

SYSTEM_PROMPT = CORE_PRINCIPLES + """
You are a LinkedIn sales assistant. The lead has booked a meeting. Keep it casual and short.

YOUR GOAL: Confirm and keep the energy up. That's it. Don't overcomplicate.

TONE & STYLE:
- Text-message style. 1-2 short messages
- Very casual
- Match their energy

REAL EXAMPLES:

Example 1 - Lead confirms booking:
Lead: "Booked for Thursday!"
You: "Nice one. See you then"

Example 2 - Lead needs to reschedule:
Lead: "Something came up, can we move it?"
You: "No worries at all"
You: "Grab another time that works - https://calendly.com/scalingsmiths/discoverycall"

Example 3 - Lead says looking forward to it:
Lead: "Looking forward to chatting"
You: "Same here. See you on there"

DO NOT:
- Write long messages
- Send prep materials unless asked
- Resell them on the meeting
- Use corporate language ("looking forward to connecting", "I appreciate your time")
- Add filler

OUTPUT FORMAT:
Return 1-2 short messages max."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}
{lead_context_section}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"
{dynamic_examples_section}
{guidance_section}

Draft a reply that confirms the meeting and sets positive expectations. Keep it concise and professional."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
    lead_context: dict | None = None,
    dynamic_examples: str = "",
) -> str:
    """Build the user prompt for booked stage.

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

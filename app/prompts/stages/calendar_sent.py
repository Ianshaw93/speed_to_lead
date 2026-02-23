"""Prompt for calendar_sent stage - they agreed, we sent calendar link."""

from app.prompts.principles import CORE_PRINCIPLES
from app.prompts.utils import build_history_section, build_lead_context_section

SYSTEM_PROMPT = CORE_PRINCIPLES + """
You are a LinkedIn sales assistant. The calendar link has been sent. They're responding about booking.

CALENDAR LINK: https://calendly.com/scalingsmiths/discoverycall

YOUR GOAL: Help them book. Keep it ultra short — they've already said yes. Don't overcomplicate it.

TONE & STYLE:
- Text-message style. Short punchy lines
- 1-2 messages max. They've agreed — don't ramble
- Very casual

REAL EXAMPLES:

Example 1 - Lead asks for the calendar:
Lead: "Send me your calendar, I will pick up a time."
You: "Sure"
You: "Book here"
You: "https://calendly.com/scalingsmiths/discoverycall"

Example 2 - Lead has scheduling issue:
Lead: "Only times available this week. I'm OOO this entire week. Got any time the following week?"
You: "Understood. Tell you what - book a time for this wk and I'll move it 7 days"

Example 3 - Lead confirms they'll book:
Lead: "Will take a look"
You: "Sounds good. Should have space still Fri/Sat"

Example 4 - Lead says thanks:
Lead: "Great, thanks!"
You: "Nice one. See you on there"

DO NOT:
- Write long messages — they've already committed
- Say "looking forward to connecting" or other corporate phrases
- Ask qualifying questions at this stage — that ship has sailed
- Resell them on the meeting
- Add "let me know if you have any trouble" type filler

OUTPUT FORMAT:
Return 1-2 short messages max. Ultra brief."""

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

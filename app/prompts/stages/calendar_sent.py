"""Prompt for calendar_sent stage - they agreed, we sent calendar link."""

SYSTEM_PROMPT = """You are a professional LinkedIn sales assistant. The lead has agreed to meet and you've sent them a calendar/booking link. They're responding to that.

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
- **"Got it, thanks!"** → Confirm, express excitement, maybe share brief prep
- **"Link isn't working"** → Quick troubleshoot, offer alternative times
- **"Looking at my calendar"** → Supportive, let them know you're flexible
- **Silence after sending link** → Gentle check-in, not pushy

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

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"

{guidance_section}

Draft a brief, helpful reply. They've already agreed to meet - just help them book successfully."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
) -> str:
    """Build the user prompt for calendar_sent stage.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        guidance: Optional user guidance for regeneration.

    Returns:
        Formatted user prompt string.
    """
    # Build history section
    history_section = "No previous messages."
    if conversation_history:
        history_lines = []
        for msg in conversation_history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            time = msg.get("time", "")
            prefix = "**Lead:**" if role == "lead" else "**You:**"
            if time:
                history_lines.append(f"{prefix} [{time}] {content}")
            else:
                history_lines.append(f"{prefix} {content}")
        if history_lines:
            history_section = "\n".join(history_lines)

    # Build guidance section
    guidance_section = ""
    if guidance:
        guidance_section = f"\n## Additional Guidance\n{guidance}"

    return USER_PROMPT_TEMPLATE.format(
        lead_name=lead_name,
        lead_message=lead_message,
        history_section=history_section,
        guidance_section=guidance_section,
    )

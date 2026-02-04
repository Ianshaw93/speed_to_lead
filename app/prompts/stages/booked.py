"""Prompt for booked stage - they've booked a meeting time."""

SYSTEM_PROMPT = """You are a professional LinkedIn sales assistant. The lead has booked a meeting time on your calendar. They're reaching out about the upcoming meeting.

## Your Goal
Confirm the meeting and set them up for a productive conversation. Reduce no-show risk.

## Guidelines
- Confirm the meeting time/details
- Express genuine enthusiasm for the conversation
- Optionally share relevant prep materials or agenda
- Keep it professional but warm
- Make them feel good about their decision to meet

## Common Scenarios
- **"Booked for [time]!"** → Confirm, express excitement, optional prep share
- **"Need to reschedule"** → Gracious, offer alternatives, no guilt
- **Questions about the meeting** → Answer helpfully, reassure them of value
- **"Looking forward to it"** → Match energy, confirm details

## What NOT to Do
- Don't overwhelm with information
- Don't make them regret booking
- Don't send multiple pre-meeting messages
- Don't resell - they're already committed

## Tone
Enthusiastic but professional. They've made a commitment - acknowledge and respect that.

Draft a reply that confirms and sets positive expectations for the meeting."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"

{guidance_section}

Draft a reply that confirms the meeting and sets positive expectations. Keep it concise and professional."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
) -> str:
    """Build the user prompt for booked stage.

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

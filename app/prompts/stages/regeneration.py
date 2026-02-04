"""Prompt for regeneration stage - re-engaging after drop-off."""

SYSTEM_PROMPT = """You are a professional LinkedIn sales assistant. This conversation went cold - the lead stopped responding after previous exchanges. You're re-engaging them.

## Your Goal
Re-engage with value, not desperation. Give them an easy on-ramp back to conversation.

## Guidelines
- Lead with something valuable (insight, resource, relevant news about their industry)
- Don't reference "just following up" or that they went quiet
- Keep it light and low-pressure
- Provide a natural reason to respond
- Make it about THEM, not you
- One clear, simple ask or conversation starter

## Re-engagement Tactics
- Share a relevant article or insight about their industry
- Reference something new on their profile (new role, post, achievement)
- Share a quick win or case study that might be relevant
- Ask a genuine, open-ended question about their business
- Mention something timely (industry news, trends)

## What NOT to Do
- Don't say "just following up" or "circling back"
- Don't guilt-trip them for not responding
- Don't be passive-aggressive
- Don't resend your previous pitch
- Don't send long messages
- Don't make it weird

## Tone
Casual, value-first, no pressure. You're reaching out because you have something worth sharing, not because you need something from them.

Draft a re-engagement message that leads with value."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}

## Conversation History
{history_section}

## Context
This lead went quiet after previous exchanges. Time to re-engage with value.

## Lead's Last Known Message
"{lead_message}"

{guidance_section}

Draft a re-engagement message that leads with value. Keep it casual and low-pressure. Don't mention that they went quiet."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
) -> str:
    """Build the user prompt for regeneration stage.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent (or last known) message.
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

"""Prompt for positive_reply stage - lead has replied, building rapport."""

SYSTEM_PROMPT = """You are a professional LinkedIn sales assistant. The lead has just replied to your initial outreach or follow-up message. They're showing interest but you haven't pitched a call yet.

## Your Goal
Build rapport and qualify their interest while providing value. DO NOT pitch a call yet.

## Guidelines
- Be warm and conversational, not salesy
- Acknowledge their response genuinely
- Ask a qualifying question to understand their needs/situation
- Reference something specific from their profile or message
- Keep it concise (2-3 sentences typically)
- Match their tone - if they're casual, be casual
- Show genuine curiosity about their business

## What NOT to Do
- Don't jump straight to scheduling a call
- Don't be overly formal or corporate
- Don't use generic templates
- Don't pitch your services yet
- Don't ask multiple questions at once

## Example Flow
They say: "Thanks, sounds interesting!"
You could: Acknowledge + ask about their current situation/challenge

Draft a reply that builds connection while subtly qualifying their interest."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"

{guidance_section}

Draft a warm, conversational reply that builds rapport and qualifies their interest. Keep it to 2-3 sentences."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
) -> str:
    """Build the user prompt for positive_reply stage.

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

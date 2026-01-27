"""System prompts for the sales assistant AI."""

SYSTEM_PROMPT = """You are a professional LinkedIn sales assistant. Your role is to draft thoughtful, personalized reply messages for LinkedIn conversations.

Guidelines:
- Be professional yet warm and approachable
- Keep messages concise (2-4 sentences typically)
- Reference specific details from the lead's message to show you're listening
- Focus on moving the conversation forward with a clear next step or question
- Avoid being overly salesy or pushy
- Match the tone of the conversation (if they're casual, be casual; if formal, be formal)
- Never use generic templates - each message should feel personalized

Your task is to draft a reply to the lead's most recent message, taking into account any conversation history provided."""

USER_PROMPT_TEMPLATE = """Lead Name: {lead_name}

{history_section}

Lead's Latest Message:
"{lead_message}"

{guidance_section}

Please draft a professional, personalized reply message."""

HISTORY_SECTION_TEMPLATE = """Previous Conversation:
{history}
"""

GUIDANCE_SECTION_TEMPLATE = """Additional Guidance:
{guidance}
"""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
) -> str:
    """Build the user prompt for generating a draft reply.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        guidance: Optional user guidance for regeneration.

    Returns:
        Formatted user prompt string.
    """
    # Build history section
    history_section = ""
    if conversation_history:
        history_lines = []
        for msg in conversation_history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "lead":
                history_lines.append(f"Lead: {content}")
            else:
                history_lines.append(f"You: {content}")
        history_text = "\n".join(history_lines)
        history_section = HISTORY_SECTION_TEMPLATE.format(history=history_text)

    # Build guidance section
    guidance_section = ""
    if guidance:
        guidance_section = GUIDANCE_SECTION_TEMPLATE.format(guidance=guidance)

    return USER_PROMPT_TEMPLATE.format(
        lead_name=lead_name,
        lead_message=lead_message,
        history_section=history_section,
        guidance_section=guidance_section,
    )

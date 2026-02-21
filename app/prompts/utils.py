"""Shared prompt utilities for history formatting and lead context rendering."""


def build_history_section(conversation_history: list[dict] | None) -> str:
    """Format conversation history with correct role prefixes.

    Uses the 'role' field from each message dict. Messages with role 'lead'
    are prefixed with **Lead:**, all others with **You:**.

    Args:
        conversation_history: List of message dicts with 'role', 'content',
            and optional 'time' fields.

    Returns:
        Formatted history string, or "No previous messages." if empty.
    """
    if not conversation_history:
        return "No previous messages."

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

    return "\n".join(history_lines) if history_lines else "No previous messages."


def build_lead_context_section(lead_context: dict | None) -> str:
    """Render lead context (company, title, triggering message) into prompt section.

    Args:
        lead_context: Dict with optional keys: company, title,
            triggering_message, personalized_message.

    Returns:
        Formatted lead context string, or empty string if no context.
    """
    if not lead_context:
        return ""

    parts = []

    company = lead_context.get("company")
    if company:
        parts.append(f"**Company:** {company}")

    title = lead_context.get("title")
    if title:
        parts.append(f"**Title:** {title}")

    triggering_message = lead_context.get("triggering_message")
    if triggering_message:
        parts.append(f"\n## Our Last Message To Them\n\"{triggering_message}\"")

    personalized_message = lead_context.get("personalized_message")
    if personalized_message:
        parts.append(f"\n## Original Outreach Message\n\"{personalized_message}\"")

    if not parts:
        return ""

    return "\n".join(parts)

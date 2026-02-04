"""Prompt for pitched stage - we've invited them to a call."""

SYSTEM_PROMPT = """You are a professional LinkedIn sales assistant. You've already pitched a call/meeting to this lead, and they're responding to that pitch.

## Your Goal
Address any hesitation or objections and reinforce the value of meeting. Help them say yes.

## Guidelines
- If they have objections, address them thoughtfully
- Emphasize the specific value they'll get from the call
- Make it easy to say yes (be flexible with timing)
- Keep responses helpful, not pushy
- If they're interested but hesitant, reduce friction
- Focus on what's in it for THEM

## Common Scenarios & Responses
- **"What would we discuss?"** → Share specific agenda items, make it about their goals
- **"I'm pretty busy"** → Acknowledge, offer flexibility, emphasize brevity (15-20 min)
- **"Maybe later"** → Soft acceptance, offer to follow up at specific time
- **"Not interested"** → Graceful exit, leave door open for future

## What NOT to Do
- Don't be desperate or pushy
- Don't repeat the same pitch verbatim
- Don't ignore their concerns
- Don't apply pressure tactics
- Don't send a wall of text

## Tone
Helpful, confident, not salesy. You're offering value, not begging for time.

Draft a reply that addresses their response and moves toward booking."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"

{guidance_section}

Draft a reply that addresses their response and makes it easy for them to say yes to the meeting. Keep it concise."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
) -> str:
    """Build the user prompt for pitched stage.

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

"""Prompt for pitched stage - we've invited them to a call."""

from app.prompts.principles import CORE_PRINCIPLES
from app.prompts.utils import build_history_section, build_lead_context_section

SYSTEM_PROMPT = CORE_PRINCIPLES + """
You are a LinkedIn sales assistant. You've already pitched a call/meeting to this lead, and they're responding to that pitch.

YOUR GOAL: Address any hesitation and help them say yes. If they say yes → send the calendar link.

CALENDAR LINK: https://calendly.com/scalingsmiths/discoverycall

WHEN TO SEND THE LINK:
- They say yes, they're interested, they're open → send it
- They ask about timing → suggest days and offer the link
- They have objections → address them first, then offer the link

TONE & STYLE:
- Text-message style. Short punchy lines, not paragraphs
- Send 2-3 SHORT separate messages, not one block
- Very casual — same style as a mate texting
- Confident but not pushy

REAL EXAMPLES:

Example 1 - Lead is open but has questions about ROI:
Lead: "I'm open to a discussion... How long does it take to get a return on investment?"
You: "Understood"
You: "Our kpi is 4+ calls/wk. We've sold deal sizes ranging up to $25k on LinkedIn"
You: "ROI is dependent on quite a few things - not least their icp and deal size"
You: "Would need to dive deeper on the call"
You: "Are you free some time Mon/Tue?"

Example 2 - Lead says yes enthusiastically:
Lead: "Yeah I'd be down for that!"
You: "Nice one"
You: "Here's my calendar"
You: "https://calendly.com/scalingsmiths/discoverycall"

Example 3 - Lead is busy but interested:
Lead: "Sounds interesting but super busy this week"
You: "No rush at all"
You: "Grab a time that works next wk"
You: "https://calendly.com/scalingsmiths/discoverycall"

Example 4 - Lead wants more info before committing:
Lead: "What would we actually cover on the call?"
You: "Good q"
You: "Basically I'd want to understand your icp and deal size. Then walk through how we'd target them on LinkedIn"
You: "15-20 mins. No pitch - just seeing if there's a fit"

DO NOT:
- Write long formal paragraphs
- Use phrases like "I'd be happy to walk you through" or "looking forward to connecting"
- Sound corporate or salesy
- Repeat the pitch they've already heard
- Send a wall of text

OUTPUT FORMAT:
Return 2-3 short separate messages, each on its own line. Keep each message to 1-2 short sentences max."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}
{lead_context_section}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"
{dynamic_examples_section}
{guidance_section}

Draft a reply that addresses their response and makes it easy for them to say yes to the meeting. Keep it concise."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
    lead_context: dict | None = None,
    dynamic_examples: str = "",
) -> str:
    """Build the user prompt for pitched stage.

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

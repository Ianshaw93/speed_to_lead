"""Prompt for regeneration stage - re-engaging after drop-off."""

from app.prompts.principles import CORE_PRINCIPLES
from app.prompts.utils import build_history_section, build_lead_context_section

SYSTEM_PROMPT = CORE_PRINCIPLES + """
You are a LinkedIn sales assistant. This conversation went cold. You're re-engaging them.

YOUR GOAL: Give them a reason to reply. Lead with value, not desperation.

TONE & STYLE:
- Text-message style. Short punchy lines
- 2-3 messages max
- Very casual — like you just thought of them
- Low pressure

TACTICS (pick ONE):
- Share a relevant case study result ("just added $25k/mo for a client targeting CEOs")
- Reference something about their business from the earlier convo
- Ask a genuine question about how things are going
- Share a quick insight relevant to their industry

REAL EXAMPLES:

Example 1 - Re-engage with social proof:
You: "Hey - just saw another client close a $15k deal through LinkedIn outreach"
You: "Reminded me of your situation. How's things going on your end?"

Example 2 - Re-engage with a question:
You: "Hey [name] - been a minute"
You: "How's the client acq going? Still mainly word of mouth?"

Example 3 - Re-engage with value:
You: "Appreciate the energy Doug"
You: "People don't have an issue with an AI msg. They have issues with messages that are not relevant to them"
You: "Finding prospects showing signals of pain points just improves results 10 fold"
You: "How do you find small businesses for GTML? Is that warm network/referrals? Or on here"

DO NOT:
- Say "just following up" or "circling back"
- Guilt-trip them for not responding
- Resend your previous pitch
- Write long messages or paragraphs
- Include placeholder links like "[Link to article]" — only use real URLs if relevant
- Include meta-commentary explaining why the message works
- Sound corporate or formal

OUTPUT FORMAT:
Return 2-3 short separate messages, each on its own line. Just the messages — no explanations or reasoning."""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}
{lead_context_section}

## Conversation History
{history_section}

## Context
This lead went quiet after previous exchanges. Time to re-engage with value.

## Lead's Last Known Message
"{lead_message}"
{dynamic_examples_section}
{guidance_section}

Draft a re-engagement message that leads with value. Keep it casual and low-pressure. Don't mention that they went quiet."""


def build_user_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    guidance: str | None = None,
    lead_context: dict | None = None,
    dynamic_examples: str = "",
) -> str:
    """Build the user prompt for regeneration stage.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent (or last known) message.
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

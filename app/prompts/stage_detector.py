"""Stage detection prompt for analyzing conversation funnel stage."""

from app.prompts.utils import build_history_section, build_lead_context_section

STAGE_DETECTION_SYSTEM_PROMPT = """You are an expert at analyzing LinkedIn sales conversations to determine which stage of the sales funnel they are in.

## Funnel Stages

Analyze the conversation history and determine which stage this conversation is currently in:

### 1. initiated
We sent an initial outreach message, but the lead hasn't replied yet.
- **Signals:** Only our messages exist, no lead responses
- **Note:** This stage typically won't trigger draft generation since we're waiting for a reply

### 2. positive_reply
The lead has replied to our initial message or follow-ups, but we haven't pitched a call yet.
- **Signals:** Lead's first substantive reply, questions about our offering, interest signals
- **Key phrases from lead:** "sounds interesting", "tell me more", "what do you do", questions about the service
- **Our goal:** Build rapport, qualify interest, provide value

### 3. pitched
We've invited them to get on a call or meeting.
- **Signals:** Our previous message mentioned scheduling/call/meeting/chat
- **Key phrases from us (in history):** "hop on a call", "schedule a time", "15 minutes", "quick chat", "set up a meeting"
- **Our goal:** Address objections, reinforce the value of meeting

### 4. calendar_sent
They agreed to meet and we've sent them a calendar/booking link.
- **Signals:** They said yes to meeting + we provided a Calendly or booking link
- **Key phrases:** Their acceptance of the meeting, our message with calendar link
- **Our goal:** Confirm, reduce no-show risk

### 5. booked
They've confirmed or booked a time in the calendar.
- **Signals:** Explicit confirmation they booked, "see you then", specific time confirmation
- **Note:** This may come from external calendar systems
- **Our goal:** Confirm meeting, share prep materials if needed

### 6. regeneration
Re-engaging after the conversation went cold or they dropped off.
- **Signals:** Extended time gap (days/weeks) since last exchange, previous conversation stalled
- **Context:** Need nurturing approach to re-establish value
- **Our goal:** Re-engage with value, not desperation

## Output Format

You MUST respond with a valid JSON object in this exact format:
```json
{
  "detected_stage": "<stage_name>",
  "reasoning": "<brief 1-2 sentence explanation of why this stage>"
}
```

Only use one of these exact stage names: initiated, positive_reply, pitched, calendar_sent, booked, regeneration
"""

USER_PROMPT_TEMPLATE = """## Lead Information
**Name:** {lead_name}
{lead_context_section}

## Conversation History
{history_section}

## Lead's Latest Message
"{lead_message}"

Based on the conversation history and latest message, determine the current funnel stage. Return your analysis as JSON."""


def build_stage_detection_prompt(
    lead_name: str,
    lead_message: str,
    conversation_history: list[dict] | None = None,
    lead_context: dict | None = None,
) -> str:
    """Build the user prompt for stage detection.

    Args:
        lead_name: Name of the lead.
        lead_message: The lead's most recent message.
        conversation_history: Previous messages in the conversation.
        lead_context: Optional lead context (company, title, etc.).

    Returns:
        Formatted user prompt string for stage detection.
    """
    history_section = build_history_section(conversation_history)
    lead_context_section = build_lead_context_section(lead_context)

    return USER_PROMPT_TEMPLATE.format(
        lead_name=lead_name,
        lead_message=lead_message,
        history_section=history_section,
        lead_context_section=lead_context_section,
    )

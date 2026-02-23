#!/usr/bin/env python3
"""Test prompt quality against real conversations from the database.

Pulls approved conversations at each funnel stage, re-generates drafts
using the current prompts, and displays a side-by-side comparison of:
- The lead's message
- What was actually sent (approved draft)
- What the AI would generate NOW with current prompts

Usage:
    # Test all stages (3 conversations each)
    python scripts/test_prompts.py

    # Test specific stage with more examples
    python scripts/test_prompts.py --stage positive_reply --count 5

    # Test with dynamic examples enabled/disabled for comparison
    python scripts/test_prompts.py --no-dynamic-examples

    # Save results to file
    python scripts/test_prompts.py --output results.md

Environment:
    Requires DATABASE_URL and DEEPSEEK_API_KEY in .env or environment.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set minimal env defaults for config loading
os.environ.setdefault("HEYREACH_API_KEY", "")
os.environ.setdefault("SLACK_BOT_TOKEN", "")
os.environ.setdefault("SLACK_CHANNEL_ID", "")
os.environ.setdefault("SLACK_SIGNING_SECRET", "")
os.environ.setdefault("APIFY_API_TOKEN", "")
os.environ.setdefault("SLACK_ENGAGEMENT_CHANNEL_ID", "")
os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("CONTENT_DB_URL", "")
os.environ.setdefault("PERPLEXITY_API_KEY", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

import ssl

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Conversation, Draft, DraftStatus, FunnelStage
from app.services.deepseek import DeepSeekClient
from app.services.example_retriever import (
    format_examples_for_prompt,
    get_similar_examples,
)


# ANSI colors for terminal output
class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


STAGE_LABELS = {
    "positive_reply": "Positive Reply (Rapport Building)",
    "pitched": "Pitched (Call Invitation)",
    "calendar_sent": "Calendar Sent",
    "booked": "Booked",
    "regeneration": "Re-engagement",
}


async def get_test_conversations(
    session: AsyncSession,
    stage: str,
    count: int = 3,
) -> list[tuple[Conversation, Draft]]:
    """Get approved conversations at a specific funnel stage.

    Returns conversations where we have an approved draft, so we can
    compare what was sent vs what the AI would generate now.
    """
    stage_enum = FunnelStage(stage)
    query = (
        select(Draft, Conversation)
        .join(Conversation, Draft.conversation_id == Conversation.id)
        .where(
            Draft.status == DraftStatus.APPROVED,
            Conversation.funnel_stage == stage_enum,
        )
        .order_by(Draft.created_at.desc())
        .limit(count)
    )
    result = await session.execute(query)
    rows = result.all()
    return [(conv, draft) for draft, conv in rows]


def extract_last_lead_message(history: list[dict] | None) -> str:
    """Get the last lead message from conversation history."""
    if not history:
        return "(no history)"
    for msg in reversed(history):
        if msg.get("role") == "lead" and msg.get("content"):
            return msg["content"]
    return "(no lead message found)"


def format_conversation_context(conv: Conversation, draft: Draft) -> dict:
    """Build lead_context dict from conversation and draft data."""
    return {
        "is_first_reply": draft.is_first_reply,
        "triggering_message": draft.triggering_message,
    }


async def generate_test_draft(
    client: DeepSeekClient,
    conv: Conversation,
    draft: Draft,
    session: AsyncSession,
    use_dynamic_examples: bool = True,
) -> tuple[str, str, str]:
    """Generate a new draft for an existing conversation using current prompts.

    Returns:
        Tuple of (detected_stage, stage_reasoning, new_draft_text)
    """
    lead_message = extract_last_lead_message(conv.conversation_history)
    lead_context = format_conversation_context(conv, draft)

    # Stage detection
    detected_stage, stage_reasoning = await client.detect_stage(
        lead_name=conv.lead_name,
        lead_message=lead_message,
        conversation_history=conv.conversation_history,
        lead_context=lead_context,
    )

    # Dynamic examples
    dynamic_examples_str = ""
    if use_dynamic_examples:
        try:
            similar = await get_similar_examples(
                stage=detected_stage,
                lead_context=lead_context,
                current_lead_message=lead_message,
                db=session,
            )
            dynamic_examples_str = format_examples_for_prompt(similar)
        except Exception as e:
            dynamic_examples_str = f"(example retrieval failed: {e})"

    # Generate reply
    new_reply = await client.generate_with_stage(
        lead_name=conv.lead_name,
        lead_message=lead_message,
        stage=detected_stage,
        conversation_history=conv.conversation_history,
        lead_context=lead_context,
        dynamic_examples=dynamic_examples_str,
    )

    return detected_stage.value, stage_reasoning, new_reply


def print_result(
    idx: int,
    conv: Conversation,
    draft: Draft,
    detected_stage: str,
    stage_reasoning: str,
    new_draft: str,
    output_lines: list[str],
):
    """Print a single test result to terminal and collect for file output."""
    lead_msg = extract_last_lead_message(conv.conversation_history)
    original = draft.actual_sent_text or draft.ai_draft

    # Terminal output
    header = f"\n{'='*70}"
    sub = f"  Test {idx}: {conv.lead_name}"
    print(f"{C.BOLD}{header}{C.END}")
    print(f"{C.BOLD}{sub}{C.END}")
    print(f"{C.DIM}  Stage: {detected_stage} | {stage_reasoning}{C.END}")
    print(f"{C.DIM}  First reply: {draft.is_first_reply}{C.END}")

    print(f"\n{C.CYAN}  Lead's message:{C.END}")
    for line in lead_msg.split("\n"):
        print(f"    {line}")

    if draft.triggering_message:
        print(f"\n{C.DIM}  Our triggering message:{C.END}")
        for line in draft.triggering_message.split("\n"):
            print(f"    {C.DIM}{line}{C.END}")

    print(f"\n{C.GREEN}  What was actually sent (approved):{C.END}")
    for line in original.split("\n"):
        print(f"    {C.GREEN}{line}{C.END}")

    print(f"\n{C.YELLOW}  What AI generates NOW:{C.END}")
    for line in new_draft.split("\n"):
        print(f"    {C.YELLOW}{line}{C.END}")

    # Markdown output for file
    output_lines.append(f"\n### Test {idx}: {conv.lead_name}")
    output_lines.append(f"**Stage:** {detected_stage} | {stage_reasoning}")
    output_lines.append(f"**First reply:** {draft.is_first_reply}")
    output_lines.append(f"\n**Lead's message:**")
    output_lines.append(f"> {lead_msg}")
    if draft.triggering_message:
        output_lines.append(f"\n**Our triggering message:**")
        output_lines.append(f"> {draft.triggering_message}")
    output_lines.append(f"\n**What was actually sent (approved):**")
    output_lines.append(f"```\n{original}\n```")
    output_lines.append(f"\n**What AI generates NOW:**")
    output_lines.append(f"```\n{new_draft}\n```")
    output_lines.append("")


async def run_tests(
    stages: list[str],
    count: int,
    use_dynamic_examples: bool,
    output_file: str | None,
):
    """Run prompt tests against real conversations."""
    # Validate env
    if not settings.deepseek_api_key:
        print(f"{C.RED}Error: DEEPSEEK_API_KEY not set{C.END}")
        sys.exit(1)

    db_url = settings.async_database_url
    if "sqlite" in db_url and "memory" in db_url:
        print(f"{C.RED}Error: DATABASE_URL points to in-memory SQLite. Set it to production DB.{C.END}")
        sys.exit(1)

    # Connect to DB
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    connect_args = {}
    if "postgresql" in db_url:
        connect_args = {"ssl": ssl_context}

    engine = create_async_engine(db_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    client = DeepSeekClient()

    output_lines = [
        "# Prompt Test Results",
        f"\nDynamic examples: {'enabled' if use_dynamic_examples else 'disabled'}",
        f"Model: {settings.deepseek_model}",
        "",
    ]

    total_tests = 0
    total_stages = 0

    for stage in stages:
        label = STAGE_LABELS.get(stage, stage)
        print(f"\n{C.BOLD}{C.HEADER}{'#'*70}{C.END}")
        print(f"{C.BOLD}{C.HEADER}  STAGE: {label}{C.END}")
        print(f"{C.BOLD}{C.HEADER}{'#'*70}{C.END}")

        output_lines.append(f"\n## {label}")
        output_lines.append("")

        async with session_factory() as session:
            conversations = await get_test_conversations(session, stage, count)

            if not conversations:
                msg = f"  No approved conversations found for stage: {stage}"
                print(f"{C.DIM}{msg}{C.END}")
                output_lines.append(f"*{msg}*\n")
                continue

            total_stages += 1
            print(f"  Found {len(conversations)} approved conversations\n")

            for i, (conv, draft) in enumerate(conversations, 1):
                try:
                    detected_stage, reasoning, new_draft = await generate_test_draft(
                        client=client,
                        conv=conv,
                        draft=draft,
                        session=session,
                        use_dynamic_examples=use_dynamic_examples,
                    )
                    print_result(i, conv, draft, detected_stage, reasoning, new_draft, output_lines)
                    total_tests += 1
                except Exception as e:
                    print(f"{C.RED}  Error on test {i} ({conv.lead_name}): {e}{C.END}")
                    output_lines.append(f"\n### Test {i}: {conv.lead_name} — ERROR: {e}\n")

    await engine.dispose()

    # Summary
    print(f"\n{C.BOLD}{'='*70}{C.END}")
    print(f"{C.BOLD}  SUMMARY: {total_tests} tests across {total_stages} stages{C.END}")
    print(f"{C.BOLD}{'='*70}{C.END}")

    output_lines.append(f"\n---\n**Summary:** {total_tests} tests across {total_stages} stages")

    # Save to file if requested
    if output_file:
        Path(output_file).write_text("\n".join(output_lines))
        print(f"\n{C.GREEN}Results saved to: {output_file}{C.END}")


def main():
    parser = argparse.ArgumentParser(description="Test prompt quality against real conversations")
    parser.add_argument(
        "--stage",
        choices=list(STAGE_LABELS.keys()),
        help="Test a specific stage (default: all stages)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of conversations to test per stage (default: 3)",
    )
    parser.add_argument(
        "--no-dynamic-examples",
        action="store_true",
        help="Disable dynamic example retrieval for comparison",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Save results to a markdown file",
    )

    args = parser.parse_args()

    stages = [args.stage] if args.stage else list(STAGE_LABELS.keys())

    asyncio.run(run_tests(
        stages=stages,
        count=args.count,
        use_dynamic_examples=not args.no_dynamic_examples,
        output_file=args.output,
    ))


if __name__ == "__main__":
    main()

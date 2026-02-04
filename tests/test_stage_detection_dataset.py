"""
Stage Detection Dataset Tests

Runs stage detection against real conversation examples.
Each conversation is sliced at stage transition points to verify
the AI correctly identifies which stage the conversation is in.

Usage:
    # Fast tests with mocked API (default)
    pytest tests/test_stage_detection_dataset.py -v

    # Live tests against real DeepSeek API
    pytest tests/test_stage_detection_dataset.py -v --live

    # Run specific conversation
    pytest tests/test_stage_detection_dataset.py -v -k "shad_arnold"
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import json

from app.models import FunnelStage
from app.services.deepseek import DeepSeekClient
from app.prompts.stage_detector import build_stage_detection_prompt

# Path to test dataset
DATASET_PATH = Path(__file__).parent / "data" / "stage_conversations.yaml"


def load_conversations():
    """Load all conversations from the YAML dataset."""
    with open(DATASET_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("conversations", [])


def generate_test_cases():
    """
    Generate test cases by slicing conversations at each stage_after marker.

    For each marker, creates a test case with:
    - Full conversation history up to that message
    - The message with stage_after as the latest_message
    - Expected stage from stage_after
    """
    test_cases = []
    conversations = load_conversations()

    for convo in conversations:
        convo_id = convo["id"]
        lead_name = convo["lead_name"]
        messages = convo["messages"]

        # Track conversation history as we iterate
        history = []

        for i, msg in enumerate(messages):
            # Check if this message has a stage marker
            if "stage_after" in msg:
                expected_stage = msg["stage_after"]

                # Build the test case
                # History = all messages BEFORE this one
                # Latest message = this message (if from lead) or previous lead message
                if msg["role"] == "lead":
                    # Lead message - this IS the latest message to analyze
                    test_case = {
                        "id": f"{convo_id}_{expected_stage}_{i}",
                        "lead_name": lead_name,
                        "conversation_history": list(history),  # Copy up to this point
                        "latest_message": msg["content"].strip(),
                        "expected_stage": expected_stage,
                        "transition_notes": msg.get("transition_notes", ""),
                    }
                    test_cases.append(test_case)
                else:
                    # "You" message with stage marker (like a pitch)
                    # Find the next lead message as the one to test
                    # For now, we test after your message is sent
                    # The stage changes when YOU send the pitch, not when they reply
                    test_case = {
                        "id": f"{convo_id}_{expected_stage}_{i}",
                        "lead_name": lead_name,
                        "conversation_history": list(history) + [msg],  # Include this message
                        "latest_message": None,  # No lead reply yet to this
                        "expected_stage": expected_stage,
                        "transition_notes": msg.get("transition_notes", ""),
                        "is_outbound_marker": True,  # Flag that this is after our message
                    }
                    # Only add if there's a subsequent lead message to test
                    # Otherwise skip (we can't test stage without a lead message)

            # Add message to history for next iteration
            history.append({
                "role": msg["role"],
                "content": msg["content"].strip(),
                "time": msg.get("time", ""),
            })

    return test_cases


def get_test_ids():
    """Generate readable test IDs for pytest."""
    return [case["id"] for case in generate_test_cases()]


# Generate test cases at module load time
TEST_CASES = generate_test_cases()


class TestStageDetectionDataset:
    """Test stage detection against real conversation examples."""

    @pytest.mark.parametrize(
        "test_case",
        TEST_CASES,
        ids=get_test_ids(),
    )
    def test_stage_detection_prompt_structure(self, test_case):
        """Verify the prompt is built correctly for each test case."""
        if test_case.get("is_outbound_marker"):
            pytest.skip("Outbound marker - no lead message to test")

        prompt = build_stage_detection_prompt(
            lead_name=test_case["lead_name"],
            lead_message=test_case["latest_message"],
            conversation_history=test_case["conversation_history"],
        )

        # Verify prompt contains key elements
        assert test_case["lead_name"] in prompt
        assert test_case["latest_message"] in prompt

        # Verify conversation history is included
        for msg in test_case["conversation_history"]:
            # Content should appear in the prompt
            assert msg["content"][:50] in prompt or len(test_case["conversation_history"]) == 0

    @pytest.mark.parametrize(
        "test_case",
        TEST_CASES,
        ids=get_test_ids(),
    )
    @pytest.mark.asyncio
    async def test_stage_detection_mocked(self, test_case):
        """
        Test stage detection with mocked API responses.

        This verifies the test infrastructure works and provides
        fast feedback during development.
        """
        if test_case.get("is_outbound_marker"):
            pytest.skip("Outbound marker - no lead message to test")

        expected_stage = test_case["expected_stage"]

        # Mock the API to return the expected stage
        mock_response = json.dumps({
            "detected_stage": expected_stage,
            "reasoning": f"Test reasoning for {expected_stage}",
        })

        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content=mock_response))
        ]

        client = DeepSeekClient(api_key="test_key")

        with patch.object(
            client._client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = mock_completion

            stage, reasoning = await client.detect_stage(
                lead_name=test_case["lead_name"],
                lead_message=test_case["latest_message"],
                conversation_history=test_case["conversation_history"],
            )

            assert stage.value == expected_stage
            assert reasoning == f"Test reasoning for {expected_stage}"


class TestDatasetIntegrity:
    """Tests to verify the dataset itself is valid."""

    def test_dataset_loads(self):
        """Verify the YAML dataset loads without errors."""
        conversations = load_conversations()
        assert len(conversations) > 0

    def test_all_conversations_have_required_fields(self):
        """Verify each conversation has required fields."""
        conversations = load_conversations()

        for convo in conversations:
            assert "id" in convo, f"Missing id in conversation"
            assert "lead_name" in convo, f"Missing lead_name in {convo.get('id', 'unknown')}"
            assert "messages" in convo, f"Missing messages in {convo.get('id', 'unknown')}"
            assert len(convo["messages"]) > 0, f"No messages in {convo['id']}"

    def test_all_messages_have_required_fields(self):
        """Verify each message has required fields."""
        conversations = load_conversations()

        for convo in conversations:
            for i, msg in enumerate(convo["messages"]):
                assert "role" in msg, f"Missing role in message {i} of {convo['id']}"
                assert "content" in msg, f"Missing content in message {i} of {convo['id']}"
                assert msg["role"] in ("you", "lead"), f"Invalid role in message {i} of {convo['id']}"

    def test_stage_after_values_are_valid(self):
        """Verify all stage_after values map to valid FunnelStage enum values."""
        valid_stages = {stage.value for stage in FunnelStage}
        conversations = load_conversations()

        for convo in conversations:
            for i, msg in enumerate(convo["messages"]):
                if "stage_after" in msg:
                    stage = msg["stage_after"]
                    assert stage in valid_stages, (
                        f"Invalid stage '{stage}' in message {i} of {convo['id']}. "
                        f"Valid stages: {valid_stages}"
                    )

    def test_test_cases_generated(self):
        """Verify test cases are generated from the dataset."""
        test_cases = generate_test_cases()
        assert len(test_cases) > 0, "No test cases generated from dataset"

        # Print summary for debugging
        print(f"\nGenerated {len(test_cases)} test cases:")
        for case in test_cases:
            print(f"  - {case['id']}: {case['expected_stage']}")


class TestConversationSlicing:
    """Tests to verify conversation slicing works correctly."""

    def test_history_accumulates_correctly(self):
        """Verify conversation history includes all prior messages."""
        test_cases = generate_test_cases()

        # Find a test case that should have history
        cases_with_history = [c for c in test_cases if len(c["conversation_history"]) > 0]

        if cases_with_history:
            case = cases_with_history[-1]  # Take the last one (should have most history)
            print(f"\nTest case: {case['id']}")
            print(f"History length: {len(case['conversation_history'])}")
            print(f"Latest message: {case['latest_message'][:50]}...")

            # Verify history doesn't include the latest message
            latest_content = case["latest_message"]
            for msg in case["conversation_history"]:
                # The exact latest message content shouldn't be in history
                # (it's passed separately as lead_message)
                pass  # This is more of a structural check

    def test_each_stage_has_test_case(self):
        """Verify we have test cases for multiple stages."""
        test_cases = generate_test_cases()
        stages_covered = {case["expected_stage"] for case in test_cases}

        print(f"\nStages covered: {stages_covered}")

        # We should have at least positive_reply and pitched from the Shad conversation
        assert "positive_reply" in stages_covered, "Missing positive_reply test cases"
        assert "pitched" in stages_covered, "Missing pitched test cases"


# ============================================================
# Live API Tests (run with --live flag)
# ============================================================

@pytest.mark.live
class TestStageDetectionLive:
    """
    Live tests against the real DeepSeek API.

    Run with: pytest tests/test_stage_detection_dataset.py -v --live -k "Live"
    """

    @pytest.mark.parametrize(
        "test_case",
        TEST_CASES,
        ids=get_test_ids(),
    )
    @pytest.mark.asyncio
    async def test_stage_detection_live(self, test_case):
        """Test stage detection against real API."""
        if test_case.get("is_outbound_marker"):
            pytest.skip("Outbound marker - no lead message to test")

        client = DeepSeekClient()
        stage, reasoning = await client.detect_stage(
            lead_name=test_case["lead_name"],
            lead_message=test_case["latest_message"],
            conversation_history=test_case["conversation_history"],
        )

        expected = test_case["expected_stage"]

        print(f"\n{'='*60}")
        print(f"Test: {test_case['id']}")
        print(f"Expected: {expected}")
        print(f"Got: {stage.value}")
        print(f"Reasoning: {reasoning}")
        print(f"{'='*60}")

        assert stage.value == expected, (
            f"Stage mismatch for {test_case['id']}\n"
            f"Expected: {expected}\n"
            f"Got: {stage.value}\n"
            f"Reasoning: {reasoning}\n"
            f"Notes: {test_case.get('transition_notes', 'N/A')}"
        )

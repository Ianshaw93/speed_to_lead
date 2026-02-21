"""Pipelines API router for triggering multichannel-outreach pipelines."""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

# Path to multichannel-outreach project (default: sibling directory)
MULTICHANNEL_OUTREACH_PATH = os.environ.get(
    "MULTICHANNEL_OUTREACH_PATH",
    str(Path(__file__).resolve().parents[2] / ".." / "multichannel-outreach"),
)
GIFT_LEADS_SCRIPT = str(
    Path(MULTICHANNEL_OUTREACH_PATH) / "execution" / "gift_leads_list.py"
)


class GiftLeadsRequest(BaseModel):
    prospect_url: str
    icp: str | None = None
    pain_points: str | None = None
    days_back: int = 14
    min_reactions: int = 50
    min_leads: int = 10
    max_leads: int = 25
    dry_run: bool = False
    skip_research: bool = False


def _stream_output(proc: subprocess.Popen, prospect_url: str) -> None:
    """Read subprocess stdout/stderr in a background thread and log it."""
    try:
        if proc.stdout:
            for line in proc.stdout:
                logger.info("[gift-leads] %s", line.rstrip())
        proc.wait()
        logger.info(
            "Gift leads pipeline finished for %s (exit code %d)",
            prospect_url,
            proc.returncode,
        )
    except Exception as e:
        logger.error("Error streaming gift-leads output: %s", e)


@router.post("/gift-leads")
async def start_gift_leads(body: GiftLeadsRequest) -> dict:
    """Start the gift-leads pipeline as a background subprocess.

    The pipeline is long-running (1-5 min) so this returns immediately.
    Output is streamed to the app logger.
    """
    if body.skip_research and not body.icp:
        raise HTTPException(
            status_code=400,
            detail="skip_research requires icp to be provided",
        )

    script_path = Path(GIFT_LEADS_SCRIPT)
    if not script_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Gift leads script not found at {GIFT_LEADS_SCRIPT}",
        )

    cmd = [
        sys.executable,
        str(script_path),
        "--prospect-url", body.prospect_url,
        "--days-back", str(body.days_back),
        "--min-reactions", str(body.min_reactions),
        "--min-leads", str(body.min_leads),
        "--max-leads", str(body.max_leads),
    ]

    if body.icp:
        cmd.extend(["--icp", body.icp])
    if body.pain_points:
        cmd.extend(["--pain-points", body.pain_points])
    if body.dry_run:
        cmd.append("--dry-run")
    if body.skip_research:
        cmd.append("--skip-research")

    logger.info("Starting gift-leads pipeline: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(Path(GIFT_LEADS_SCRIPT).parent.parent),
    )

    # Stream output in background thread so we don't block
    thread = threading.Thread(
        target=_stream_output,
        args=(proc, body.prospect_url),
        daemon=True,
    )
    thread.start()

    return {
        "status": "started",
        "message": f"Gift leads pipeline started for {body.prospect_url}",
    }

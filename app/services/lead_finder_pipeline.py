"""Lead finder pipeline: find ICP leads by job title + company keywords via Apify.

5-step pipeline:
  Apify code_crafter/leads-finder -> DB dedup -> ICP qualify (DeepSeek)
  -> Personalize (DeepSeek) -> Upload to HeyReach

Ported from multichannel-outreach/execution/scrape_apify.py to run
as an async FastAPI service with DB-backed dedup and PipelineRun tracking.

Design: scrapes MORE leads than immediately needed and stores the surplus
as Prospect records with personalized_message=NULL. On the next top-up run
these surplus leads are personalized and uploaded first (cheap & fast)
before scraping new ones.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PipelineRun, Prospect, ProspectSource
from app.services.prospect_pipeline import (
    APIFY_COSTS,
    DEEPSEEK_COSTS,
    HEYREACH_LIST_ID,
    _normalize_url,
    create_prospect_records,
    filter_already_processed,
    generate_personalization_deepseek,
    qualify_leads_with_deepseek,
    run_apify_actor,
    upload_to_heyreach,
    _send_pipeline_summary,
)

logger = logging.getLogger(__name__)

# Apify actor for lead search
LEADS_FINDER_ACTOR = "code_crafter~leads-finder"

# Default search parameters (matching multichannel-outreach/execution/scrape_apify.py)
DEFAULT_JOB_TITLES = ["CEO", "Founder", "Managing Director"]
DEFAULT_COMPANY_KEYWORDS = ["agency", "SaaS", "consulting"]
DEFAULT_LOCATION = "united states"


# ===================================================================
# STEP 0: USE SURPLUS FROM PREVIOUS RUNS
# ===================================================================

async def _process_surplus_leads(
    needed: int,
    heyreach_list_id: int,
    session: AsyncSession,
) -> tuple[int, int]:
    """Personalize and upload surplus Prospect records from previous pipeline runs.

    Surplus leads are ICP-qualified Prospect records with source_type in
    (COMPETITOR_POST, SALES_NAV) that have personalized_message=NULL and
    are not yet uploaded to HeyReach.

    Returns (personalized_count, uploaded_count).
    """
    result = await session.execute(
        select(Prospect)
        .where(
            Prospect.source_type.in_([ProspectSource.COMPETITOR_POST, ProspectSource.SALES_NAV]),
            Prospect.personalized_message.is_(None),
            Prospect.icp_match.is_(True),
            Prospect.heyreach_uploaded_at.is_(None),
        )
        .order_by(Prospect.created_at.asc())
        .limit(needed)
    )
    surplus = list(result.scalars().all())

    if not surplus:
        return 0, 0

    logger.info(f"Found {len(surplus)} surplus leads from previous runs, personalizing...")

    # Convert to dicts for pipeline functions
    leads_dicts = []
    for p in surplus:
        leads_dicts.append({
            "linkedinUrl": p.linkedin_url,
            "firstName": p.first_name,
            "lastName": p.last_name,
            "fullName": p.full_name,
            "jobTitle": p.job_title,
            "companyName": p.company_name,
            "companyIndustry": p.company_industry,
            "headline": p.headline,
            "addressWithCountry": p.location,
            "email": p.email,
        })

    # Personalize
    for lead_dict in leads_dicts:
        lead_dict["personalized_message"] = await generate_personalization_deepseek(lead_dict)

    personalized = [l for l in leads_dicts if l.get("personalized_message")]

    # Upload to HeyReach
    uploaded = await upload_to_heyreach(personalized, heyreach_list_id) if personalized else 0

    # Update the Prospect records
    now = datetime.now(timezone.utc)
    for prospect_obj, lead_dict in zip(surplus, leads_dicts):
        if lead_dict.get("personalized_message"):
            prospect_obj.personalized_message = lead_dict["personalized_message"]
            prospect_obj.heyreach_list_id = heyreach_list_id
            prospect_obj.heyreach_uploaded_at = now

    await session.commit()
    logger.info(f"Surplus processing: {len(personalized)} personalized, {uploaded} uploaded")
    return len(personalized), uploaded


# ===================================================================
# STEP 1: FIND LEADS VIA APIFY
# ===================================================================

async def find_leads_apify(
    job_titles: list[str],
    company_keywords: list[str],
    location: str,
    fetch_count: int,
    require_email: bool = True,
) -> list[dict]:
    """Search for leads using Apify code_crafter/leads-finder actor.

    Uses the exact same input format as multichannel-outreach/execution/scrape_apify.py:
    - contact_job_title: list of job titles
    - company_keywords: list of company keywords
    - contact_location: list of locations
    - language: "en"
    - email_status: ["validated"] (optional)

    Returns list of lead dicts with: first_name, last_name, email, job_title,
    company_name, company_domain, linkedin_url, location.
    """
    run_input: dict[str, Any] = {
        "fetch_count": fetch_count,
        "contact_job_title": job_titles,
        "company_keywords": company_keywords,
        "contact_location": [location.lower()],
        "language": "en",
    }

    if require_email:
        run_input["email_status"] = ["validated"]

    logger.info(f"Starting leads-finder: titles={job_titles}, keywords={company_keywords}, "
                f"location={location}, count={fetch_count}")

    items = await run_apify_actor(
        LEADS_FINDER_ACTOR,
        run_input,
        initial_wait=60,
        poll_interval=30,
        max_polls=20,
    )

    # Normalize field names to match our conventions
    normalized: list[dict] = []
    for item in items:
        linkedin_url = (
            item.get("linkedin") or item.get("linkedin_url")
            or item.get("linkedinUrl") or ""
        )
        if not linkedin_url:
            continue
        city = item.get("city") or ""
        state = item.get("state") or ""
        country = item.get("country") or ""
        location_str = ", ".join(filter(None, [city, state, country])) or location
        normalized.append({
            "linkedinUrl": _normalize_url(linkedin_url),
            "firstName": item.get("first_name") or item.get("firstName") or "",
            "lastName": item.get("last_name") or item.get("lastName") or "",
            "fullName": item.get("full_name") or f"{item.get('first_name', '')} {item.get('last_name', '')}".strip(),
            "jobTitle": item.get("job_title") or item.get("jobTitle") or "",
            "companyName": item.get("company_name") or item.get("companyName") or "",
            "companyIndustry": item.get("industry") or item.get("company_industry") or "",
            "headline": item.get("headline") or item.get("job_title") or "",
            "email": item.get("email") or "",
            "addressWithCountry": location_str,
            "company_domain": item.get("company_domain") or item.get("company_website") or "",
        })

    logger.info(f"Leads-finder returned {len(normalized)} leads with LinkedIn URLs")
    return normalized


# ===================================================================
# COST TRACKING
# ===================================================================

def _estimate_lead_finder_costs(counts: dict) -> dict:
    cost_apify = counts.get("leads_found", 0) * APIFY_COSTS["leads_finder"]
    icp_tokens = counts.get("icp_checks", 0) * DEEPSEEK_COSTS["avg_icp_tokens"]
    cost_icp = (icp_tokens / 1_000_000) * (DEEPSEEK_COSTS["input_per_1m"] + DEEPSEEK_COSTS["output_per_1m"]) / 2
    pers_tokens = counts.get("personalizations", 0) * DEEPSEEK_COSTS["avg_personalization_tokens"]
    cost_pers = (pers_tokens / 1_000_000) * (DEEPSEEK_COSTS["input_per_1m"] + DEEPSEEK_COSTS["output_per_1m"]) / 2
    return {
        "cost_apify_google": Decimal(str(round(cost_apify, 4))),  # reusing field for lead finder cost
        "cost_deepseek_icp": Decimal(str(round(cost_icp, 4))),
        "cost_deepseek_personalize": Decimal(str(round(cost_pers, 4))),
        "cost_total": Decimal(str(round(cost_apify + cost_icp + cost_pers, 4))),
    }


# ===================================================================
# MAIN ORCHESTRATOR
# ===================================================================

async def run_lead_finder_pipeline(
    job_titles: list[str] | None = None,
    company_keywords: list[str] | None = None,
    location: str = DEFAULT_LOCATION,
    fetch_count: int = 100,
    heyreach_list_id: int = HEYREACH_LIST_ID,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the lead finder pipeline.

    Strategy: First check for surplus leads from previous runs. If enough
    surplus exists, personalize and upload those (cheap — no Apify cost).
    Only scrape new leads if surplus is insufficient.

    Scrapes 3x the needed amount so surplus is available for future runs.
    """
    from app.database import async_session_factory

    if job_titles is None:
        job_titles = DEFAULT_JOB_TITLES
    if company_keywords is None:
        company_keywords = DEFAULT_COMPANY_KEYWORDS

    async with async_session_factory() as session:
        # Create PipelineRun
        pipeline_run = PipelineRun(run_type="lead_finder", status="started")
        session.add(pipeline_run)
        await session.commit()
        run_id = pipeline_run.id

        counts: dict[str, int] = {}

        try:
            # Step 0: Check for surplus leads from previous runs
            surplus_personalized = 0
            surplus_uploaded = 0
            if not dry_run:
                logger.info("[0/5] Checking for surplus leads from previous runs...")
                surplus_personalized, surplus_uploaded = await _process_surplus_leads(
                    needed=fetch_count, heyreach_list_id=heyreach_list_id, session=session,
                )

                if surplus_uploaded >= fetch_count:
                    # Enough surplus — no need to scrape
                    pipeline_run.status = "completed"
                    pipeline_run.final_leads = surplus_uploaded
                    pipeline_run.completed_at = datetime.now(timezone.utc)
                    duration = (pipeline_run.completed_at - pipeline_run.started_at).total_seconds()
                    pipeline_run.duration_seconds = int(duration)
                    await session.commit()

                    summary = {
                        "run_id": str(run_id),
                        "status": "completed",
                        "source": "surplus",
                        "surplus_personalized": surplus_personalized,
                        "surplus_uploaded": surplus_uploaded,
                        "new_leads_scraped": 0,
                    }
                    await _send_pipeline_summary("Lead Finder (Surplus)", summary)
                    return summary

            # Step 1: Find leads via Apify
            # Scrape 3x what we need to build surplus for future runs
            scrape_count = fetch_count * 3
            logger.info(f"[1/5] Finding leads via Apify (requesting {scrape_count})...")
            leads = await find_leads_apify(job_titles, company_keywords, location, scrape_count)
            counts["leads_found"] = len(leads)
            pipeline_run.posts_found = len(leads)  # reusing field for leads count

            if not leads:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "leads_found": 0}

            # Step 2: DB dedup
            logger.info(f"[2/5] DB dedup...")
            lead_urls = [l["linkedinUrl"] for l in leads]
            new_urls = await filter_already_processed(lead_urls, session)
            new_url_set = set(new_urls)
            leads = [l for l in leads if l["linkedinUrl"] in new_url_set]

            if not leads:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "new_leads": 0}

            # Step 3: ICP qualification
            logger.info(f"[3/5] ICP qualifying {len(leads)} leads...")
            qualified = await qualify_leads_with_deepseek(leads)
            counts["icp_checks"] = len(leads)
            pipeline_run.icp_qualified = len(qualified)

            if not qualified:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "icp_qualified": 0}

            # Only personalize what we need now; store the rest as surplus
            to_personalize = qualified[:fetch_count]
            surplus_to_store = qualified[fetch_count:]

            # Step 4: Personalize (only immediate batch)
            logger.info(f"[4/5] Personalizing {len(to_personalize)} leads (storing {len(surplus_to_store)} as surplus)...")
            for lead in to_personalize:
                lead["personalized_message"] = await generate_personalization_deepseek(lead)
            personalized = [l for l in to_personalize if l.get("personalized_message")]
            counts["personalizations"] = len(personalized)

            # Step 5: Upload to HeyReach
            uploaded = 0
            if not dry_run and personalized:
                logger.info(f"[5/5] Uploading {len(personalized)} leads to HeyReach...")
                uploaded = await upload_to_heyreach(personalized, heyreach_list_id)
            else:
                logger.info("[5/5] Dry run — skipping upload")

            pipeline_run.final_leads = uploaded + surplus_uploaded

            # Create Prospect records for ALL qualified leads (personalized + surplus)
            all_qualified = qualified  # includes both personalized and surplus
            created = await create_prospect_records(
                all_qualified, ProspectSource.SALES_NAV,
                f"{','.join(job_titles)}|{','.join(company_keywords)}",
                heyreach_list_id if uploaded else None,
                session,
            )

            # Update PipelineRun
            costs = _estimate_lead_finder_costs(counts)
            for k, v in costs.items():
                if hasattr(pipeline_run, k):
                    setattr(pipeline_run, k, v)

            pipeline_run.profiles_scraped = len(leads)
            pipeline_run.count_icp_checks = counts.get("icp_checks", 0)
            pipeline_run.count_personalizations = counts.get("personalizations", 0)

            pipeline_run.status = "completed"
            pipeline_run.completed_at = datetime.now(timezone.utc)
            duration = (pipeline_run.completed_at - pipeline_run.started_at).total_seconds()
            pipeline_run.duration_seconds = int(duration)
            await session.commit()

            summary = {
                "run_id": str(run_id),
                "status": "completed",
                "leads_found": counts.get("leads_found", 0),
                "icp_qualified": pipeline_run.icp_qualified,
                "personalized": len(personalized),
                "surplus_stored": len(surplus_to_store),
                "uploaded": uploaded,
                "surplus_from_prev": surplus_uploaded,
                "prospects_created": created,
                "cost_total": str(costs.get("cost_total", "0")),
                "duration_seconds": pipeline_run.duration_seconds,
            }

            await _send_pipeline_summary("Lead Finder", summary)
            return summary

        except Exception as e:
            logger.error(f"Lead finder pipeline failed: {e}", exc_info=True)
            pipeline_run.status = "failed"
            pipeline_run.error_message = str(e)[:500]
            pipeline_run.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return {"run_id": str(run_id), "status": "failed", "error": str(e)}

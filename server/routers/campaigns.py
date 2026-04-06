"""
ClipForge — Campaign Research & Intelligence Router

API endpoints for campaign discovery, management, and upload guidance.
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.campaigns import (
    discover_campaigns,
    filter_campaigns,
    save_campaign_manually,
    generate_upload_guidance,
    get_performance_stats,
    log_performance,
    Campaign,
    _load_local_campaigns,
)
from services.categories import (
    detect_category,
    get_category_list,
    get_category_config,
    CATEGORY_PRESETS,
)

logger = logging.getLogger("clipforge.routers.campaigns")

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


# ── Schemas ────────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    platform: str = "whop"
    title: str
    creator_name: str = ""
    url: str = ""
    target_platforms: List[str] = Field(default_factory=lambda: ["tiktok", "youtube_shorts"])
    allowed_content_types: List[str] = Field(default_factory=list)
    total_budget: float = 0.0
    remaining_budget: float = 0.0
    payout_per_view: float = 0.0
    payout_per_clip: float = 0.0
    min_views_for_payout: int = 0
    max_payout_per_clip: float = 0.0
    min_duration_sec: int = 15
    max_duration_sec: int = 180
    required_hashtags: List[str] = Field(default_factory=list)
    required_disclosure: str = ""
    forbidden_content: List[str] = Field(default_factory=list)
    submission_rules: str = ""
    approval_criteria: str = ""
    status: str = "active"
    start_date: str = ""
    end_date: str = ""
    notes: str = ""


class PerformanceLog(BaseModel):
    campaign_id: str
    clip_id: str
    platform: str = "tiktok"
    views: int = 0
    approved: bool = False
    payout: float = 0.0
    notes: str = ""


class UploadGuidanceRequest(BaseModel):
    campaign_id: str = ""
    clip_title: str = ""
    clip_hook: str = ""
    category: str = "general"


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("")
async def list_campaigns(
    min_budget_pct: float = Query(0, description="Minimum budget % remaining"),
    platform: Optional[str] = Query(None, description="Filter by target platform"),
    min_priority: float = Query(0, description="Minimum priority score"),
):
    """List all known campaigns, optionally filtered."""
    campaigns = _load_local_campaigns()

    # Apply scoring
    from services.campaigns import _score_campaign
    for c in campaigns:
        c.priority_score = _score_campaign(c)

    # Filter if requested
    if min_budget_pct > 0 or platform or min_priority > 0:
        target_platforms = [platform] if platform else None
        campaigns = filter_campaigns(
            campaigns,
            min_budget_pct=min_budget_pct,
            target_platforms=target_platforms,
            min_priority=min_priority,
        )

    campaigns.sort(key=lambda c: c.priority_score, reverse=True)
    return [c.to_dict() for c in campaigns]


@router.post("/discover")
async def run_discovery(
    sources: Optional[List[str]] = None,
):
    """Run campaign discovery from web sources."""
    try:
        campaigns = await discover_campaigns(sources=sources)
        return {
            "discovered": len(campaigns),
            "campaigns": [c.to_dict() for c in campaigns[:20]],
        }
    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add")
async def add_campaign(data: CampaignCreate):
    """Manually add or update a campaign in the knowledge base."""
    campaign = save_campaign_manually(data.model_dump())
    return campaign.to_dict()


@router.post("/guidance")
async def get_upload_guidance(req: UploadGuidanceRequest):
    """Generate upload guidance for a clip + campaign combination."""
    campaigns = _load_local_campaigns()

    # Find the campaign
    campaign = None
    if req.campaign_id:
        for c in campaigns:
            if c.id == req.campaign_id:
                campaign = c
                break

    if not campaign:
        # Use a default empty campaign
        campaign = Campaign(
            target_platforms=["tiktok", "youtube_shorts"],
            min_duration_sec=15,
            max_duration_sec=180,
        )

    guidance = generate_upload_guidance(
        campaign=campaign,
        clip_title=req.clip_title,
        clip_hook=req.clip_hook,
        category=req.category,
    )
    return guidance


@router.post("/performance")
async def log_clip_performance(data: PerformanceLog):
    """Log clip performance for learning over time."""
    log_performance(
        campaign_id=data.campaign_id,
        clip_id=data.clip_id,
        platform=data.platform,
        views=data.views,
        approved=data.approved,
        payout=data.payout,
        notes=data.notes,
    )
    return {"status": "logged"}


@router.get("/stats")
async def performance_stats():
    """Get aggregate performance statistics."""
    return get_performance_stats()


# ── Category Endpoints ─────────────────────────────────────────────────────

@router.get("/categories")
async def list_categories():
    """List available content categories."""
    return get_category_list()


@router.get("/categories/{category_id}")
async def get_category(category_id: str):
    """Get full configuration for a category."""
    config = get_category_config(category_id)
    if not config:
        raise HTTPException(status_code=404, detail="Category not found")
    return config


@router.post("/detect-category")
async def detect_content_category(
    title: str = "",
    description: str = "",
    channel_name: str = "",
    duration: float = 0,
    transcript_text: str = "",
):
    """Auto-detect content category from metadata."""
    category = detect_category(
        transcript_text=transcript_text,
        title=title,
        description=description,
        duration=duration,
        channel_name=channel_name,
    )
    return {
        "detected_category": category,
        "config": get_category_config(category),
    }

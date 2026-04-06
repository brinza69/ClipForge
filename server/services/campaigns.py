"""
ClipForge — Campaign Research & Intelligence Service

Discovers, parses, filters, and ranks clipping reward campaigns from:
  - Whop Content Rewards
  - Vyro
  - Other legitimate clipping/reward platforms

Stores findings locally in JSON knowledge base for learning over time.
"""

import logging
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

from config import settings

logger = logging.getLogger("clipforge.campaigns")

# ---------------------------------------------------------------------------
# Local knowledge base path
# ---------------------------------------------------------------------------
KNOWLEDGE_DIR = settings.data_dir / "knowledge"
CAMPAIGNS_FILE = KNOWLEDGE_DIR / "campaigns.json"
HISTORY_FILE = KNOWLEDGE_DIR / "campaign_history.json"
PERFORMANCE_FILE = KNOWLEDGE_DIR / "performance_log.json"


def _ensure_knowledge_dir():
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Campaign data model
# ---------------------------------------------------------------------------

@dataclass
class Campaign:
    """A clipping reward/campaign opportunity."""
    id: str = ""
    platform: str = ""          # "whop", "vyro", "other"
    title: str = ""
    creator_name: str = ""
    url: str = ""

    # Targets
    target_platforms: List[str] = field(default_factory=list)  # ["tiktok", "youtube_shorts"]
    allowed_content_types: List[str] = field(default_factory=list)  # ["podcast", "livestream"]

    # Budget & payout
    total_budget: float = 0.0
    remaining_budget: float = 0.0
    budget_pct_remaining: float = 0.0
    payout_per_view: float = 0.0
    payout_per_clip: float = 0.0
    min_views_for_payout: int = 0
    max_payout_per_clip: float = 0.0

    # Rules
    min_duration_sec: int = 15
    max_duration_sec: int = 180
    required_hashtags: List[str] = field(default_factory=list)
    required_disclosure: str = ""
    forbidden_content: List[str] = field(default_factory=list)
    submission_rules: str = ""
    approval_criteria: str = ""

    # Status
    status: str = "active"      # active, paused, completed, saturated
    start_date: str = ""
    end_date: str = ""
    last_checked: str = ""
    saturation_estimate: str = "low"  # low, medium, high

    # Scoring
    priority_score: float = 0.0
    fit_score: float = 0.0
    recommended_category: str = ""
    recommended_account: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Campaign discovery — scrape/parse campaign pages
# ---------------------------------------------------------------------------

async def discover_campaigns(
    sources: Optional[List[str]] = None,
) -> List[Campaign]:
    """
    Discover available campaigns from configured sources.

    This is the main entry point for daily campaign research.
    Returns a list of Campaign objects sorted by priority.
    """
    _ensure_knowledge_dir()

    all_campaigns: List[Campaign] = []

    # Load any manually-added campaigns from local knowledge base
    local = _load_local_campaigns()
    all_campaigns.extend(local)

    # Try to discover from web sources
    if sources is None:
        sources = ["whop", "vyro"]

    for source in sources:
        try:
            if source == "whop":
                found = await _discover_whop()
                all_campaigns.extend(found)
            elif source == "vyro":
                found = await _discover_vyro()
                all_campaigns.extend(found)
        except Exception as e:
            logger.warning(f"Failed to discover from {source}: {e}")

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for c in all_campaigns:
        key = c.url or c.title
        if key not in seen_urls:
            seen_urls.add(key)
            unique.append(c)

    # Score and rank
    for c in unique:
        c.priority_score = _score_campaign(c)
        c.last_checked = datetime.now(timezone.utc).isoformat()

    unique.sort(key=lambda c: c.priority_score, reverse=True)

    # Save to knowledge base
    _save_campaigns(unique)

    logger.info(f"Discovered {len(unique)} campaigns")
    return unique


async def _discover_whop() -> List[Campaign]:
    """
    Discover campaigns from Whop Content Rewards.

    In v1 this uses httpx to fetch the public rewards page.
    Falls back to local cache if network fails.
    """
    campaigns = []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Whop content rewards discovery endpoint
            resp = await client.get(
                "https://whop.com/content-rewards/",
                headers={"User-Agent": "ClipForge/1.0"},
                follow_redirects=True,
            )
            if resp.status_code == 200:
                campaigns = _parse_whop_page(resp.text)
    except Exception as e:
        logger.warning(f"Whop discovery failed: {e}")

    return campaigns


async def _discover_vyro() -> List[Campaign]:
    """
    Discover campaigns from Vyro.

    In v1 this uses httpx to check for available campaigns.
    Falls back to local cache if network fails.
    """
    campaigns = []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://vyro.ai/",
                headers={"User-Agent": "ClipForge/1.0"},
                follow_redirects=True,
            )
            if resp.status_code == 200:
                campaigns = _parse_vyro_page(resp.text)
    except Exception as e:
        logger.warning(f"Vyro discovery failed: {e}")

    return campaigns


def _parse_whop_page(html: str) -> List[Campaign]:
    """
    Parse Whop content rewards page for campaign data.

    This extracts structured data from the HTML/JSON embedded in the page.
    Robust to page structure changes — falls back gracefully.
    """
    campaigns = []

    # Look for JSON-LD or embedded data
    json_matches = re.findall(
        r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )

    for match in json_matches:
        try:
            data = json.loads(match)
            if isinstance(data, list):
                for item in data:
                    c = _parse_whop_item(item)
                    if c:
                        campaigns.append(c)
            elif isinstance(data, dict):
                c = _parse_whop_item(data)
                if c:
                    campaigns.append(c)
        except json.JSONDecodeError:
            continue

    # Also try to find campaign cards in HTML (common Whop patterns)
    card_pattern = re.compile(
        r'(?:data-campaign|class="[^"]*reward[^"]*")[^>]*>.*?'
        r'(?:budget|reward|payout)[^<]*<',
        re.DOTALL | re.IGNORECASE
    )
    # This is a best-effort parse — structured API access is preferred

    if not campaigns:
        logger.debug("No structured campaigns found in Whop page — may need API key")

    return campaigns


def _parse_whop_item(data: Dict) -> Optional[Campaign]:
    """Try to extract a Campaign from a Whop JSON data item."""
    if not isinstance(data, dict):
        return None

    title = data.get("name") or data.get("title") or ""
    if not title:
        return None

    return Campaign(
        id=data.get("id", ""),
        platform="whop",
        title=title,
        creator_name=data.get("creator", {}).get("name", "") if isinstance(data.get("creator"), dict) else "",
        url=data.get("url", ""),
        target_platforms=data.get("platforms", ["tiktok"]),
        total_budget=float(data.get("total_budget", 0)),
        remaining_budget=float(data.get("remaining_budget", 0)),
        payout_per_view=float(data.get("payout_per_view", 0)),
        payout_per_clip=float(data.get("payout_per_clip", 0)),
        min_duration_sec=int(data.get("min_duration", 15)),
        max_duration_sec=int(data.get("max_duration", 180)),
        required_hashtags=data.get("required_hashtags", []),
        required_disclosure=data.get("disclosure", ""),
        status="active",
    )


def _parse_vyro_page(html: str) -> List[Campaign]:
    """Parse Vyro page for campaign data."""
    campaigns = []
    # Vyro typically has a different structure — API-based discovery preferred
    json_matches = re.findall(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    for match in json_matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "campaigns" in data:
                for item in data["campaigns"]:
                    c = Campaign(
                        platform="vyro",
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        total_budget=float(item.get("budget", 0)),
                        remaining_budget=float(item.get("remaining", 0)),
                        status="active",
                    )
                    campaigns.append(c)
        except (json.JSONDecodeError, TypeError):
            continue

    return campaigns


# ---------------------------------------------------------------------------
# Campaign scoring and filtering
# ---------------------------------------------------------------------------

def _score_campaign(campaign: Campaign) -> float:
    """
    Score a campaign for priority ranking.

    Factors:
      - Budget remaining (>50% = strong priority)
      - Payout potential
      - Platform fit (TikTok/YouTube Shorts preferred)
      - Duration compatibility (60-90s range)
      - Saturation level
      - Freshness
    """
    score = 50.0  # Base score

    # Budget remaining — major factor
    if campaign.total_budget > 0:
        campaign.budget_pct_remaining = (campaign.remaining_budget / campaign.total_budget) * 100
    else:
        campaign.budget_pct_remaining = 0

    if campaign.budget_pct_remaining > 75:
        score += 30.0
    elif campaign.budget_pct_remaining > 50:
        score += 20.0
    elif campaign.budget_pct_remaining > 25:
        score += 5.0
    elif campaign.budget_pct_remaining > 0:
        score -= 10.0
    else:
        # Unknown budget — neutral
        score += 10.0

    # Payout potential
    if campaign.payout_per_clip > 50:
        score += 15.0
    elif campaign.payout_per_clip > 20:
        score += 10.0
    elif campaign.payout_per_view > 0:
        score += 5.0

    # Platform fit — TikTok and YouTube Shorts are our targets
    target_platforms = {p.lower() for p in campaign.target_platforms}
    if "tiktok" in target_platforms or "youtube_shorts" in target_platforms:
        score += 10.0
    if "tiktok" in target_platforms and "youtube_shorts" in target_platforms:
        score += 5.0  # Bonus for dual-platform

    # Duration compatibility — our sweet spot is 60-90s
    if campaign.min_duration_sec <= 60 and campaign.max_duration_sec >= 90:
        score += 10.0  # Perfect fit
    elif campaign.min_duration_sec <= 30 and campaign.max_duration_sec >= 60:
        score += 5.0   # Acceptable
    elif campaign.max_duration_sec < 30:
        score -= 15.0  # Too short for our pipeline

    # Saturation penalty
    if campaign.saturation_estimate == "high":
        score -= 20.0
    elif campaign.saturation_estimate == "medium":
        score -= 10.0

    # Active status bonus
    if campaign.status == "active":
        score += 5.0
    elif campaign.status in ("paused", "completed"):
        score -= 30.0

    return max(0.0, min(100.0, score))


def filter_campaigns(
    campaigns: List[Campaign],
    min_budget_pct: float = 50.0,
    target_platforms: Optional[List[str]] = None,
    min_priority: float = 40.0,
) -> List[Campaign]:
    """
    Filter campaigns by budget remaining, platform fit, and priority.
    """
    filtered = []
    for c in campaigns:
        # Budget filter — >50% is priority
        if c.budget_pct_remaining > 0 and c.budget_pct_remaining < min_budget_pct:
            continue

        # Platform filter
        if target_platforms:
            c_platforms = {p.lower() for p in c.target_platforms}
            if not c_platforms.intersection(set(p.lower() for p in target_platforms)):
                continue

        # Priority threshold
        if c.priority_score < min_priority:
            continue

        # Skip completed/paused
        if c.status in ("completed", "paused"):
            continue

        filtered.append(c)

    return filtered


# ---------------------------------------------------------------------------
# Upload guidance generation
# ---------------------------------------------------------------------------

def generate_upload_guidance(
    campaign: Campaign,
    clip_title: str = "",
    clip_hook: str = "",
    category: str = "general",
) -> Dict[str, Any]:
    """
    Generate complete upload guidance package for a clip + campaign combo.

    Returns a ready-to-use package with:
      - recommended platform(s)
      - title, caption, hashtags
      - disclosure text
      - submission checklist
      - account/niche recommendation
    """
    from services.categories import CATEGORY_PRESETS

    cat_preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS.get("general", {}))

    # Build hashtags
    hashtags = list(campaign.required_hashtags)
    # Add category-specific hashtags
    cat_tags = cat_preset.get("default_hashtags", [])
    for tag in cat_tags:
        if tag not in hashtags:
            hashtags.append(tag)

    # Ensure reasonable count (5-8 hashtags)
    if len(hashtags) > 8:
        hashtags = hashtags[:8]
    elif len(hashtags) < 3:
        # Add generic viral hashtags
        fallback_tags = ["#fyp", "#viral", "#foryou", "#trending"]
        for tag in fallback_tags:
            if tag not in hashtags and len(hashtags) < 5:
                hashtags.append(tag)

    # Build caption
    caption_parts = []
    if clip_hook:
        caption_parts.append(clip_hook)
    elif clip_title:
        caption_parts.append(clip_title)

    # Add campaign-specific call-to-action if applicable
    cat_cta = cat_preset.get("default_cta", "")
    if cat_cta:
        caption_parts.append(cat_cta)

    caption = "\n".join(caption_parts)

    # Title — short, punchy, for YouTube Shorts title field
    title = clip_title or clip_hook or "Watch this"
    if len(title) > 80:
        title = title[:77] + "..."

    # Disclosure
    disclosure = campaign.required_disclosure or ""

    # Platform recommendations
    platforms = campaign.target_platforms if campaign.target_platforms else ["tiktok", "youtube_shorts"]

    # Submission checklist
    checklist = _build_submission_checklist(campaign, category)

    # First comment suggestion
    first_comment = ""
    if campaign.platform == "whop":
        first_comment = f"Created with @{campaign.creator_name}" if campaign.creator_name else ""

    return {
        "recommended_platforms": platforms,
        "recommended_category": category,
        "recommended_account": cat_preset.get("account_type", "general"),
        "title": title,
        "caption": caption,
        "hashtags": hashtags,
        "hashtags_string": " ".join(hashtags),
        "first_comment": first_comment,
        "disclosure_text": disclosure,
        "submission_checklist": checklist,
        "campaign_id": campaign.id,
        "campaign_title": campaign.title,
        "campaign_platform": campaign.platform,
        "notes": campaign.notes,
        "duration_range": f"{campaign.min_duration_sec}-{campaign.max_duration_sec}s",
        "payout_info": {
            "per_clip": campaign.payout_per_clip,
            "per_view": campaign.payout_per_view,
            "max_per_clip": campaign.max_payout_per_clip,
        },
    }


def _build_submission_checklist(campaign: Campaign, category: str) -> List[str]:
    """Build a submission checklist for a campaign."""
    checklist = []

    # Duration check
    checklist.append(
        f"Clip duration: {campaign.min_duration_sec}-{campaign.max_duration_sec}s"
    )

    # Required hashtags
    if campaign.required_hashtags:
        checklist.append(f"Required hashtags: {', '.join(campaign.required_hashtags)}")

    # Disclosure
    if campaign.required_disclosure:
        checklist.append(f"Include disclosure: \"{campaign.required_disclosure}\"")

    # Forbidden content
    if campaign.forbidden_content:
        checklist.append(f"Avoid: {', '.join(campaign.forbidden_content)}")

    # Platform-specific
    if "tiktok" in [p.lower() for p in campaign.target_platforms]:
        checklist.append("Upload to TikTok as original sound")
        checklist.append("Set to public visibility")
    if "youtube_shorts" in [p.lower() for p in campaign.target_platforms]:
        checklist.append("Upload as YouTube Short (vertical, <60s or with #Shorts)")

    # Submission rules
    if campaign.submission_rules:
        checklist.append(f"Submission: {campaign.submission_rules}")

    # General quality
    checklist.append("Verify captions are readable and correctly timed")
    checklist.append("Check hook text appears in first 3 seconds")
    checklist.append("Preview full clip before uploading")

    return checklist


# ---------------------------------------------------------------------------
# Local knowledge base — persistence
# ---------------------------------------------------------------------------

def _load_local_campaigns() -> List[Campaign]:
    """Load campaigns from local knowledge base."""
    _ensure_knowledge_dir()
    if not CAMPAIGNS_FILE.exists():
        return []

    try:
        data = json.loads(CAMPAIGNS_FILE.read_text(encoding="utf-8"))
        campaigns = []
        for item in data:
            c = Campaign(**{k: v for k, v in item.items() if k in Campaign.__dataclass_fields__})
            campaigns.append(c)
        return campaigns
    except Exception as e:
        logger.warning(f"Failed to load local campaigns: {e}")
        return []


def _save_campaigns(campaigns: List[Campaign]):
    """Save campaigns to local knowledge base."""
    _ensure_knowledge_dir()
    data = [c.to_dict() for c in campaigns]
    CAMPAIGNS_FILE.write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )


def save_campaign_manually(campaign_data: Dict[str, Any]) -> Campaign:
    """
    Save a manually-entered campaign to the knowledge base.

    Used when auto-discovery can't find campaigns (e.g., login-walled platforms).
    """
    existing = _load_local_campaigns()

    c = Campaign(**{k: v for k, v in campaign_data.items() if k in Campaign.__dataclass_fields__})
    c.priority_score = _score_campaign(c)
    c.last_checked = datetime.now(timezone.utc).isoformat()

    # Deduplicate
    existing = [e for e in existing if e.url != c.url or not c.url]
    existing.append(c)

    _save_campaigns(existing)
    return c


def log_performance(
    campaign_id: str,
    clip_id: str,
    platform: str,
    views: int = 0,
    approved: bool = False,
    payout: float = 0.0,
    notes: str = "",
):
    """Log clip performance for learning over time."""
    _ensure_knowledge_dir()

    history = []
    if PERFORMANCE_FILE.exists():
        try:
            history = json.loads(PERFORMANCE_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []

    entry = {
        "campaign_id": campaign_id,
        "clip_id": clip_id,
        "platform": platform,
        "views": views,
        "approved": approved,
        "payout": payout,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    history.append(entry)

    PERFORMANCE_FILE.write_text(
        json.dumps(history, indent=2, default=str),
        encoding="utf-8",
    )


def get_performance_stats() -> Dict[str, Any]:
    """Get aggregate performance statistics from the knowledge base."""
    _ensure_knowledge_dir()

    if not PERFORMANCE_FILE.exists():
        return {
            "total_clips": 0,
            "total_views": 0,
            "total_payout": 0.0,
            "approval_rate": 0.0,
            "by_platform": {},
            "by_campaign": {},
        }

    try:
        history = json.loads(PERFORMANCE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"total_clips": 0, "total_views": 0, "total_payout": 0.0, "approval_rate": 0.0}

    total = len(history)
    approved = sum(1 for h in history if h.get("approved"))
    total_views = sum(h.get("views", 0) for h in history)
    total_payout = sum(h.get("payout", 0) for h in history)

    by_platform: Dict[str, Dict] = {}
    for h in history:
        p = h.get("platform", "unknown")
        if p not in by_platform:
            by_platform[p] = {"clips": 0, "views": 0, "payout": 0.0, "approved": 0}
        by_platform[p]["clips"] += 1
        by_platform[p]["views"] += h.get("views", 0)
        by_platform[p]["payout"] += h.get("payout", 0)
        if h.get("approved"):
            by_platform[p]["approved"] += 1

    return {
        "total_clips": total,
        "total_views": total_views,
        "total_payout": total_payout,
        "approval_rate": (approved / total * 100) if total > 0 else 0.0,
        "by_platform": by_platform,
    }

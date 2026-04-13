# PRP: Utilities Page (Batch 2)

## Goal
Add a Utilities section to ClipForge with two tools:
1. **Shorts/TikTok Downloader** — paste URL, download to local storage for use as a project
2. **Caption/Text/Logo Eraser** — stub UI that explains the feature (FFmpeg-based future implementation)

## Why
- Users need to download shorts/TikToks directly without going through the full pipeline
- The downloader reuses existing yt-dlp infrastructure in `services/downloader.py`
- The eraser is a future feature stub with clear UI so users know it's coming

## What

### Sidebar nav item
Add "Utilities" to `src/components/layout/sidebar.tsx` navItems using the `Wrench` icon from lucide-react.

### Utilities page (`src/app/utilities/page.tsx`)
Two sections in a two-column grid:

**Section 1: Shorts/TikTok Downloader**
- URL input (same as dashboard)
- "Download" button → calls `/api/utilities/download` → returns `{project_id}`
- On success: navigate to `/projects/{project_id}` OR show success toast with link
- Shows progress/status feedback

**Section 2: Caption/Text/Logo Eraser (Coming Soon)**
- Card with description: "Remove hardcoded captions, logos, or watermarks from any video using AI inpainting."
- File upload placeholder (disabled)
- "Coming soon" badge
- Lists planned features: subtitle removal, logo masking, text detection

### Backend endpoint (`server/routers/utilities.py`)
```
POST /api/utilities/download
body: { url: string, title?: string }
response: { project_id: string, job_id: string }
```
Creates a ProjectModel + enqueues a full_pipeline job (reuses existing pattern from projects router).
Register router in `server/main.py`.

## Files to Create/Modify

```
src/app/utilities/page.tsx         — new page
src/components/layout/sidebar.tsx  — add nav item
server/routers/utilities.py        — new router
server/main.py                     — include utilities router
src/lib/api.ts                     — add utilities.download() helper
```

## Implementation Blueprint

### sidebar.tsx addition
```tsx
import { Wrench } from "lucide-react";
// Add to navItems array:
{ label: "Utilities", href: "/utilities", icon: Wrench },
```

### utilities router pattern (mirrors projects router)
```python
@router.post("/download")
async def quick_download(data: UtilityDownload, session: AsyncSession = Depends(get_session)):
    project = ProjectModel(source_url=data.url, title=data.title or "Quick Download")
    session.add(project)
    await session.flush()
    job_id = await job_queue.enqueue(project_id=project.id, job_type=JobType.full_pipeline.value)
    await session.commit()
    return {"project_id": project.id, "job_id": job_id}
```

### api.ts addition
```ts
utilities: {
  download: (url: string, title?: string) =>
    request<{ project_id: string; job_id: string }>("/api/utilities/download", {
      method: "POST",
      body: JSON.stringify({ url, title }),
    }),
},
```

## Gotchas
- The project router creates projects too — reuse the exact same pattern (add to session, flush, enqueue, commit)
- job_queue.enqueue signature: `(project_id, clip_id=None, job_type, metadata={})`
- Don't duplicate validation logic — reuse what exists in the projects router

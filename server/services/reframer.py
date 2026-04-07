"""
ClipForge -- Smart Reframe Service (v2)
Auto-reframes horizontal video to 9:16 vertical by detecting faces + body
and keeping subjects centered with smooth transitions.

Features:
  - Speaker-aware tracking (largest face = primary speaker)
  - Body/pose fallback when face is not visible (turned away, looking down)
  - Temporal coherence (face identity persistence across frames)
  - Smart composition (rule-of-thirds, head room, presentation balance)
  - Velocity-damped smoothing with ease-in-out interpolation
  - Scene-type heuristic (talking head vs presentation vs wide shot)
  - Animated FFmpeg crop via expression-based interpolation (ease-in-out)
"""

import logging
import asyncio
import math
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

from config import settings

logger = logging.getLogger("clipforge.reframer")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
# How often we sample frames (seconds). Lower = smoother but slower analysis.
DEFAULT_SAMPLE_INTERVAL = 0.25  # Slightly more frequent for smoother tracking

# Smoothing parameters
MIN_MOVE_PX = 25            # Ignore moves smaller than this (reduces micro-jitter)
SMOOTHING_ALPHA = 0.10      # Base EMA factor (lower = smoother, less aggressive)
MAX_ALPHA = 0.22            # Adaptive ceiling (reduced from 0.28)
VELOCITY_DAMPING = 0.55     # Velocity retention (lower = less overshoot)
MAX_VEL_RATIO = 0.40        # Clamp velocity (tighter cap for smoother motion)

# Speaker scoring weights
WEIGHT_SIZE_FIRST_FRAME = 1.0
WEIGHT_SIZE = 0.25
WEIGHT_PROXIMITY = 0.75

# Presentation balance: how much to keep context when speaker is off-center
PRESENTATION_CONTEXT_RATIO = 0.20  # Keep 20% more of the "other side"

# Vertical composition
HEAD_ROOM_CLOSE = 0.22       # Face occupies >25% of crop height
HEAD_ROOM_FAR = 0.35         # Face occupies <8% of crop height
FACE_RATIO_CLOSE = 0.25
FACE_RATIO_FAR = 0.08

# Face-lost hold duration: how many consecutive frames before we start
# slowly drifting back toward center (prevents stuck crops on scene changes)
FACE_LOST_HOLD_FRAMES = 8   # ~2.6s at 0.33s interval


async def analyze_reframe(
    video_path: str,
    start_time: float,
    end_time: float,
    mode: str = "auto",
    sample_interval: float = DEFAULT_SAMPLE_INTERVAL,
) -> Dict[str, Any]:
    """
    Analyze video frames and generate reframe keyframes for 9:16 crop.

    Modes:
        auto   -- detect faces+body, center on primary speaker
        single -- lock to single face
        dual   -- split screen for two speakers
        center -- static center crop
        blurred -- blurred background (handled by exporter, still uses auto tracking)

    Returns reframe_data with crop keyframes.
    """
    logger.info(f"Analyzing reframe: {video_path} [{start_time:.1f}s - {end_time:.1f}s] mode={mode}")

    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _analyze_sync(video_path, start_time, end_time, mode, sample_interval),
    )

    return result


def _analyze_sync(
    video_path: str,
    start_time: float,
    end_time: float,
    mode: str,
    sample_interval: float,
) -> Dict[str, Any]:
    """Synchronous reframe analysis."""
    try:
        import cv2
        import mediapipe as mp
        has_mediapipe = True
    except ImportError:
        has_mediapipe = False
        logger.warning("MediaPipe not available -- using center crop fallback")

    # Get video dimensions — try cv2, fall back to ffprobe
    cap = None
    src_width, src_height, fps = 0, 0, 30.0
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        src_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        src_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    except Exception:
        has_mediapipe = False

    if src_width <= 0 or src_height <= 0:
        # cv2 unavailable or returned bad values — use ffprobe
        try:
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,r_frame_rate",
                 "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                src_width = int(parts[0])
                src_height = int(parts[1])
                if len(parts) > 2 and "/" in parts[2]:
                    num, den = parts[2].split("/")
                    fps = float(num) / max(float(den), 1)
                logger.info(f"Got dimensions from ffprobe: {src_width}x{src_height} @ {fps:.2f}fps")
            else:
                raise ValueError("ffprobe returned non-zero")
        except Exception as e:
            logger.warning(f"ffprobe dimension detection failed: {e}. Using 1920x1080 fallback.")
            src_width, src_height, fps = 1920, 1080, 30.0

    # Calculate crop dimensions (what portion of source fits in 9:16)
    target_aspect = 9 / 16  # 0.5625
    source_aspect = src_width / src_height

    if source_aspect > target_aspect:
        crop_height = src_height
        crop_width = int(src_height * target_aspect)
    else:
        crop_width = src_width
        crop_height = int(src_width / target_aspect)

    # H.264 requires even dimensions
    crop_width = crop_width - (crop_width % 2)
    crop_height = crop_height - (crop_height % 2)

    keyframes = []

    effective_mode = mode
    # blurred mode still benefits from face tracking for the foreground crop
    if effective_mode in ("blurred", "blurred_background", "blurredBackground"):
        effective_mode = "auto"

    if has_mediapipe and effective_mode != "center":
        try:
            keyframes = _detect_and_generate_keyframes(
                cap, src_width, src_height,
                crop_width, crop_height,
                start_time, end_time,
                sample_interval, effective_mode, fps,
            )
        except Exception as e:
            logger.warning(f"Face detection failed, using center crop: {e}")

    if cap:
        cap.release()

    # Fallback: center crop
    if not keyframes:
        center_x = (src_width - crop_width) // 2
        center_y = (src_height - crop_height) // 2
        duration = end_time - start_time
        keyframes = [
            {"time": 0.0, "x": center_x, "y": center_y},
            {"time": duration, "x": center_x, "y": center_y},
        ]
        logger.info(f"Reframe: using center crop fallback ({src_width}x{src_height} → {crop_width}x{crop_height})")

    # Smooth the keyframes
    keyframes = _smooth_keyframes(keyframes, min_move_distance=MIN_MOVE_PX)

    reframe_data = {
        "mode": mode,
        "src_width": src_width,
        "src_height": src_height,
        "crop_width": crop_width,
        "crop_height": crop_height,
        "keyframes": keyframes,
    }

    logger.info(f"Reframe analysis complete: {len(keyframes)} keyframes, crop={crop_width}x{crop_height}")
    return reframe_data


# ---------------------------------------------------------------------------
# Face + Body detection & speaker-aware keyframe generation
# ---------------------------------------------------------------------------

def _detect_and_generate_keyframes(
    cap, src_width, src_height,
    crop_width, crop_height,
    start_time, end_time,
    sample_interval, mode, fps,
) -> List[Dict]:
    """
    Use MediaPipe face detection (+ optional pose fallback) to generate
    crop keyframes with speaker-aware tracking and smart vertical composition.
    """
    import cv2
    import mediapipe as mp

    face_detection = mp.solutions.face_detection.FaceDetection(
        model_selection=1,  # Full range model (works up to 5m away)
        min_detection_confidence=0.45,
    )

    # Try to load pose detection as fallback for when face isn't visible
    pose_detection = None
    try:
        pose_detection = mp.solutions.pose.Pose(
            model_complexity=0,  # Lite model for speed
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
            static_image_mode=True,
        )
    except Exception:
        logger.debug("MediaPipe Pose not available, face-only tracking")

    keyframes = []
    duration = end_time - start_time

    # --- Speaker tracking state ---
    tracked_cx: Optional[float] = None
    tracked_cy: Optional[float] = None
    tracked_w: Optional[float] = None
    tracked_h: Optional[float] = None

    last_crop_x: Optional[int] = None
    last_crop_y: Optional[int] = None
    face_lost_count = 0

    center_x_fallback = (src_width - crop_width) // 2
    center_y_fallback = (src_height - crop_height) // 2

    for t in _frange(0, duration, sample_interval):
        frame_time = start_time + t
        frame_num = int(frame_time * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            continue

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # --- Step 1: Detect faces ---
        face_results = face_detection.process(rgb_frame)
        faces_found = face_results.detections if face_results.detections else []

        crop_x: int
        crop_y: int
        subject_found = False

        if faces_found:
            face_lost_count = 0
            subject_found = True

            if mode == "dual" and len(faces_found) >= 2:
                crop_x, crop_y = _handle_dual_mode(
                    faces_found, src_width, src_height,
                    crop_width, crop_height,
                )
            else:
                chosen, all_faces = _pick_speaker_face(
                    faces_found, src_width, src_height,
                    tracked_cx, tracked_cy, tracked_w,
                )

                face_cx, face_cy, face_w, face_h = chosen
                tracked_cx, tracked_cy = face_cx, face_cy
                tracked_w, tracked_h = face_w, face_h

                crop_x, crop_y = _compose_crop(
                    face_cx, face_cy, face_w, face_h,
                    src_width, src_height,
                    crop_width, crop_height,
                    all_faces,
                )

        # --- Step 2: Body/pose fallback when face not detected ---
        if not subject_found and pose_detection is not None:
            body = _detect_body_center(pose_detection, rgb_frame, src_width, src_height)
            if body is not None:
                body_cx, body_cy, body_w, body_h = body
                face_lost_count = 0
                subject_found = True

                # Use body center but with less aggressive vertical composition
                crop_x = int(body_cx - crop_width / 2)
                # Place body roughly in upper 40% of frame
                crop_y = int(body_cy - crop_height * 0.40)

                # Update tracked position toward body (slower blend)
                if tracked_cx is not None:
                    tracked_cx = tracked_cx * 0.6 + body_cx * 0.4
                    tracked_cy = tracked_cy * 0.6 + body_cy * 0.4
                else:
                    tracked_cx, tracked_cy = body_cx, body_cy

        # --- Step 3: No subject at all — hold or drift to center ---
        if not subject_found:
            face_lost_count += 1

            if last_crop_x is not None and face_lost_count <= FACE_LOST_HOLD_FRAMES:
                # Hold last known position
                crop_x = last_crop_x
                crop_y = last_crop_y
            elif last_crop_x is not None:
                # Slowly drift toward center to handle scene changes
                drift = min(1.0, (face_lost_count - FACE_LOST_HOLD_FRAMES) * 0.15)
                crop_x = int(last_crop_x + drift * (center_x_fallback - last_crop_x))
                crop_y = int(last_crop_y + drift * (center_y_fallback - last_crop_y))
            else:
                crop_x = center_x_fallback
                crop_y = center_y_fallback

        # Clamp to valid range
        crop_x = max(0, min(crop_x, src_width - crop_width))
        crop_y = max(0, min(crop_y, src_height - crop_height))

        last_crop_x = crop_x
        last_crop_y = crop_y

        keyframes.append({
            "time": round(t, 3),
            "x": crop_x,
            "y": crop_y,
        })

    face_detection.close()
    if pose_detection is not None:
        pose_detection.close()

    return keyframes


def _detect_body_center(
    pose_detector,
    rgb_frame,
    src_width: int,
    src_height: int,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Use MediaPipe Pose to find body center when face isn't detected.
    Returns (center_x, center_y, approx_width, approx_height) or None.
    """
    results = pose_detector.process(rgb_frame)
    if not results.pose_landmarks:
        return None

    lm = results.pose_landmarks.landmark

    # Key landmarks: nose(0), left_shoulder(11), right_shoulder(12),
    # left_hip(23), right_hip(24)
    # Use shoulders and hips to estimate body bounding box
    key_indices = [0, 11, 12, 23, 24]
    xs, ys = [], []
    for idx in key_indices:
        if idx < len(lm) and lm[idx].visibility > 0.3:
            xs.append(lm[idx].x * src_width)
            ys.append(lm[idx].y * src_height)

    if len(xs) < 3:
        return None

    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    bw = max(xs) - min(xs)
    bh = max(ys) - min(ys)

    # Expand bounding box a bit for framing
    bw = max(bw, src_width * 0.1)
    bh = max(bh, src_height * 0.2)

    return (cx, cy, bw, bh)


def _pick_speaker_face(
    detections,
    src_width: int,
    src_height: int,
    prev_cx: Optional[float],
    prev_cy: Optional[float],
    prev_w: Optional[float],
) -> Tuple[Tuple[float, float, float, float], List[Tuple[float, float, float, float]]]:
    """
    Choose the primary speaker face from a list of detections.

    Strategy:
      1. Extract all face bboxes in pixels.
      2. Score each face by size + proximity to previously tracked face.
      3. Return the best scoring face plus the full list.
    """
    faces: List[Tuple[float, float, float, float]] = []
    for det in detections:
        bbox = det.location_data.relative_bounding_box
        fw = bbox.width * src_width
        fh = bbox.height * src_height
        cx = (bbox.xmin + bbox.width / 2) * src_width
        cy = (bbox.ymin + bbox.height / 2) * src_height
        faces.append((cx, cy, fw, fh))

    if len(faces) == 1:
        return faces[0], faces

    max_area = max(fw * fh for (_, _, fw, fh) in faces) or 1.0

    best_score = -1.0
    best_face = faces[0]

    for face in faces:
        cx, cy, fw, fh = face
        area = fw * fh
        size_score = area / max_area

        prox_score = 0.0
        if prev_cx is not None:
            diag = math.sqrt(src_width ** 2 + src_height ** 2)
            dist = math.sqrt((cx - prev_cx) ** 2 + (cy - prev_cy) ** 2)
            prox_score = max(0.0, 1.0 - dist / (diag * 0.3))

            # Size continuity penalty
            if prev_w is not None and prev_w > 0:
                size_ratio = min(fw, prev_w) / max(fw, prev_w)
                prox_score *= (0.5 + 0.5 * size_ratio)

        if prev_cx is not None:
            score = WEIGHT_SIZE * size_score + WEIGHT_PROXIMITY * prox_score
        else:
            score = size_score

        if score > best_score:
            best_score = score
            best_face = face

    return best_face, faces


def _handle_dual_mode(
    detections,
    src_width: int,
    src_height: int,
    crop_width: int,
    crop_height: int,
) -> Tuple[int, int]:
    """Dual mode: center crop between the two largest faces."""
    faces = sorted(
        detections,
        key=lambda d: d.location_data.relative_bounding_box.width
                      * d.location_data.relative_bounding_box.height,
        reverse=True,
    )[:2]

    centers_x, centers_y = [], []
    for det in faces:
        bbox = det.location_data.relative_bounding_box
        cx = (bbox.xmin + bbox.width / 2) * src_width
        cy = (bbox.ymin + bbox.height / 2) * src_height
        centers_x.append(cx)
        centers_y.append(cy)

    avg_cx = sum(centers_x) / len(centers_x)
    avg_cy = sum(centers_y) / len(centers_y)

    crop_x = int(avg_cx - crop_width / 2)
    crop_y = int(avg_cy - crop_height / 2)
    return crop_x, crop_y


def _compose_crop(
    face_cx: float,
    face_cy: float,
    face_w: float,
    face_h: float,
    src_width: int,
    src_height: int,
    crop_width: int,
    crop_height: int,
    all_faces: List[Tuple[float, float, float, float]],
) -> Tuple[int, int]:
    """
    Compute crop_x, crop_y with smart composition:
      - Center crop on face horizontally with presentation-aware bias
      - Rule-of-thirds vertical positioning with adaptive head room
      - Balance speaker visibility with presentation content
    """
    # --- Horizontal: center crop on face ---
    crop_x = int(face_cx - crop_width / 2)

    # --- Presentation awareness heuristic ---
    # If face is in the left/right 40% and there's no opposing face,
    # the speaker is probably presenting something on the other side.
    # Keep the speaker well-framed but preserve some context.
    face_rel_x = face_cx / src_width

    has_opposing_face = False
    for (ox, oy, ow, oh) in all_faces:
        # Skip if Euclidean distance is within one face-width (same face)
        dist = ((ox - face_cx) ** 2 + (oy - face_cy) ** 2) ** 0.5
        if dist < max(face_w, face_h) * 0.8:
            continue  # same face
        orel = ox / src_width
        if (face_rel_x < 0.4 and orel > 0.6) or (face_rel_x > 0.6 and orel < 0.4):
            has_opposing_face = True
            break

    if not has_opposing_face:
        # Compute how far off-center the face is (0 = center, 1 = edge)
        off_center = abs(face_rel_x - 0.5) * 2  # 0..1

        if off_center > 0.2:
            # Speaker is off-center. Place them at ~35% from the near edge
            # of the crop, keeping 65% of the crop for the "other" content.
            # This is less aggressive than full centering on the face.
            speaker_position_in_crop = 0.35  # speaker at 35% from their side

            if face_rel_x < 0.5:
                # Speaker on left — place face at 35% from left of crop
                crop_x = int(face_cx - crop_width * speaker_position_in_crop)
            else:
                # Speaker on right — place face at 35% from right of crop
                crop_x = int(face_cx - crop_width * (1.0 - speaker_position_in_crop))

            # But never lose the speaker: ensure face is within the inner 80%
            face_in_crop = face_cx - crop_x
            min_margin = crop_width * 0.10
            max_margin = crop_width * 0.90
            if face_in_crop < min_margin:
                crop_x = int(face_cx - min_margin)
            elif face_in_crop > max_margin:
                crop_x = int(face_cx - max_margin)

    # --- Vertical: rule-of-thirds composition ---
    face_top = face_cy - face_h / 2

    face_crop_ratio = face_h / crop_height
    if face_crop_ratio > FACE_RATIO_CLOSE:
        head_room_frac = HEAD_ROOM_CLOSE
    elif face_crop_ratio < FACE_RATIO_FAR:
        head_room_frac = HEAD_ROOM_FAR
    else:
        # Linear interpolation
        t = (FACE_RATIO_CLOSE - face_crop_ratio) / (FACE_RATIO_CLOSE - FACE_RATIO_FAR)
        head_room_frac = HEAD_ROOM_CLOSE + (HEAD_ROOM_FAR - HEAD_ROOM_CLOSE) * t

    crop_y = int(face_top - head_room_frac * crop_height)

    # Ensure the face bottom is also within the crop (don't cut off chin)
    face_bottom = face_cy + face_h / 2
    if face_bottom > crop_y + crop_height * 0.85:
        crop_y = int(face_bottom - crop_height * 0.85)

    return crop_x, crop_y


# ---------------------------------------------------------------------------
# Smoothing with velocity damping (improved)
# ---------------------------------------------------------------------------

def _smooth_keyframes(keyframes: List[Dict], min_move_distance: int = MIN_MOVE_PX) -> List[Dict]:
    """
    Smooth crop position keyframes to avoid jarring jumps.
    Uses exponential smoothing with velocity damping and adaptive alpha.
    """
    if len(keyframes) <= 1:
        return keyframes

    smoothed = [keyframes[0].copy()]
    vel_x = 0.0
    vel_y = 0.0

    for i in range(1, len(keyframes)):
        prev = smoothed[-1]
        curr = keyframes[i]

        target_x = curr["x"]
        target_y = curr["y"]
        prev_x = prev["x"]
        prev_y = prev["y"]

        dx = target_x - prev_x
        dy = target_y - prev_y
        dist = math.sqrt(dx * dx + dy * dy)

        if dist < min_move_distance:
            # Too small — hold position, decay velocity
            vel_x *= 0.4
            vel_y *= 0.4
            smoothed.append({
                "time": curr["time"],
                "x": prev_x,
                "y": prev_y,
            })
        else:
            # Adaptive alpha: bigger jumps get slightly faster catch-up
            dist_factor = min(1.0, dist / 200.0)
            alpha = SMOOTHING_ALPHA + (MAX_ALPHA - SMOOTHING_ALPHA) * dist_factor

            # Velocity damping
            vel_x = VELOCITY_DAMPING * vel_x + alpha * dx
            vel_y = VELOCITY_DAMPING * vel_y + alpha * dy

            # Clamp velocity to prevent overshoot
            max_vel = dist * MAX_VEL_RATIO
            vel_mag = math.sqrt(vel_x * vel_x + vel_y * vel_y)
            if vel_mag > max_vel and vel_mag > 0:
                scale = max_vel / vel_mag
                vel_x *= scale
                vel_y *= scale

            new_x = int(prev_x + vel_x)
            new_y = int(prev_y + vel_y)

            smoothed.append({
                "time": curr["time"],
                "x": new_x,
                "y": new_y,
            })

    return smoothed


# ---------------------------------------------------------------------------
# Animated FFmpeg crop filter builder (with ease-in-out interpolation)
# ---------------------------------------------------------------------------

def build_crop_filter(reframe_data: Dict[str, Any]) -> str:
    """
    Build an FFmpeg crop filter expression from reframe keyframes.

    Uses smoothstep (ease-in-out) interpolation between keyframes
    for cinema-quality smooth motion.
    """
    keyframes = reframe_data.get("keyframes", [])
    crop_w = reframe_data["crop_width"]
    crop_h = reframe_data["crop_height"]
    src_w = reframe_data.get("src_width", crop_w)
    src_h = reframe_data.get("src_height", crop_h)

    if not keyframes:
        return f"crop={crop_w}:{crop_h}:0:0"

    # Check if all keyframes are static
    all_same = all(
        k["x"] == keyframes[0]["x"] and k.get("y", 0) == keyframes[0].get("y", 0)
        for k in keyframes
    )

    if len(keyframes) <= 2 and all_same:
        x = keyframes[0]["x"]
        y = keyframes[0].get("y", 0)
        return f"crop={crop_w}:{crop_h}:{x}:{y}"

    if len(keyframes) == 1:
        x = keyframes[0]["x"]
        y = keyframes[0].get("y", 0)
        return f"crop={crop_w}:{crop_h}:{x}:{y}"

    # Build animated crop with ease-in-out
    x_expr = _build_interpolation_expr(keyframes, "x", src_w - crop_w)
    y_expr = _build_interpolation_expr(keyframes, "y", src_h - crop_h)

    return f"crop={crop_w}:{crop_h}:{x_expr}:{y_expr}"


def _build_interpolation_expr(
    keyframes: List[Dict],
    coord: str,
    max_val: int,
) -> str:
    """
    Build an FFmpeg expression that uses smoothstep (ease-in-out)
    interpolation between keyframes for a given coordinate.

    smoothstep(t) = 3t^2 - 2t^3  (applied to normalized segment progress)
    This gives acceleration at the start and deceleration at the end
    of each transition, making camera movement feel natural.
    """
    if len(keyframes) == 1:
        v = keyframes[0].get(coord, 0)
        return str(max(0, min(v, max_val)))

    # De-duplicate at same timestamp
    deduped: List[Dict] = []
    for kf in keyframes:
        if deduped and abs(kf["time"] - deduped[-1]["time"]) < 0.001:
            deduped[-1] = kf
        else:
            deduped.append(kf)

    if len(deduped) == 1:
        v = deduped[0].get(coord, 0)
        return str(max(0, min(v, max_val)))

    # Build segments with smoothstep interpolation
    # smoothstep: v1 + (v2-v1) * (3*p^2 - 2*p^3)  where p = (t-t1)/(t2-t1)
    segments = []
    for i in range(len(deduped) - 1):
        t1 = deduped[i]["time"]
        t2 = deduped[i + 1]["time"]
        v1 = deduped[i].get(coord, 0)
        v2 = deduped[i + 1].get(coord, 0)

        dt = t2 - t1
        if dt < 0.001 or v1 == v2:
            lerp = str(v1)
        else:
            # p = (t - t1) / dt
            # smoothstep = 3*p*p - 2*p*p*p
            # result = v1 + (v2 - v1) * smoothstep
            # FFmpeg expression (no spaces, escaped commas):
            p_expr = f"(t-{t1:.3f})/{dt:.3f}"
            # Use simpler linear for very short segments to avoid expression bloat
            if dt < 0.2:
                lerp = f"{v1}+({v2}-{v1})*{p_expr}"
            else:
                lerp = f"{v1}+({v2}-{v1})*(3*pow({p_expr}\\,2)-2*pow({p_expr}\\,3))"

        segments.append((t2, lerp))

    # Build nested if expression
    last_val = deduped[-1].get(coord, 0)
    expr = str(last_val)

    for t_end, lerp in reversed(segments):
        expr = f"if(lt(t\\,{t_end:.3f})\\,{lerp}\\,{expr})"

    first_val = deduped[0].get(coord, 0)
    first_t = deduped[0]["time"]
    if first_t > 0.001:
        expr = f"if(lt(t\\,{first_t:.3f})\\,{first_val}\\,{expr})"

    # Clamp
    expr = f"clip({expr}\\,0\\,{max_val})"

    return expr


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _frange(start, stop, step):
    """Float range generator."""
    current = start
    while current < stop:
        yield round(current, 3)
        current += step

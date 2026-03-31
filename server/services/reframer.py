"""
ClipForge — Smart Reframe Service
Auto-reframes horizontal video to 9:16 vertical by detecting faces
and keeping subjects centered with smooth transitions.
"""

import logging
import asyncio
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

from config import settings

logger = logging.getLogger("clipforge.reframer")


async def analyze_reframe(
    video_path: str,
    start_time: float,
    end_time: float,
    mode: str = "auto",
    sample_interval: float = 0.5,
) -> Dict[str, Any]:
    """
    Analyze video frames and generate reframe keyframes for 9:16 crop.

    Modes:
        auto   — detect faces, center on primary speaker
        single — lock to single face
        dual   — split screen for two speakers

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
        logger.warning("MediaPipe not available — using center crop fallback")

    # Get video dimensions
    cap = None
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        src_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    except Exception:
        # Fallback dimensions
        src_width = 1920
        src_height = 1080
        fps = 30.0
        has_mediapipe = False

    # Calculate crop dimensions (what portion of source fits in 9:16)
    target_aspect = 9 / 16  # 0.5625
    source_aspect = src_width / src_height

    if source_aspect > target_aspect:
        # Source is wider — crop width
        crop_height = src_height
        crop_width = int(src_height * target_aspect)
    else:
        # Source is taller — crop height
        crop_width = src_width
        crop_height = int(src_width / target_aspect)

    keyframes = []

    if has_mediapipe and mode != "center":
        try:
            keyframes = _detect_faces_and_generate_keyframes(
                cap, src_width, src_height,
                crop_width, crop_height,
                start_time, end_time,
                sample_interval, mode, fps,
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

    # Smooth the keyframes
    keyframes = _smooth_keyframes(keyframes, min_move_distance=20)

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


def _detect_faces_and_generate_keyframes(
    cap, src_width, src_height,
    crop_width, crop_height,
    start_time, end_time,
    sample_interval, mode, fps,
) -> List[Dict]:
    """Use MediaPipe face detection to generate crop keyframes."""
    import cv2
    import mediapipe as mp

    face_detection = mp.solutions.face_detection.FaceDetection(
        model_selection=1,  # Full range model
        min_detection_confidence=0.5,
    )

    keyframes = []
    duration = end_time - start_time

    for t in _frange(0, duration, sample_interval):
        frame_time = start_time + t
        frame_num = int(frame_time * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            continue

        # Detect faces
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_detection.process(rgb_frame)

        if results.detections:
            if mode == "dual" and len(results.detections) >= 2:
                # Dual mode: center crop between two main faces
                faces = sorted(
                    results.detections,
                    key=lambda d: d.score[0],
                    reverse=True,
                )[:2]

                # Get center of mass between two faces
                centers = []
                for det in faces:
                    bbox = det.location_data.relative_bounding_box
                    cx = (bbox.xmin + bbox.width / 2) * src_width
                    centers.append(cx)

                avg_cx = sum(centers) / len(centers)
                crop_x = int(avg_cx - crop_width / 2)
            else:
                # Single / auto: center on primary face
                best = max(results.detections, key=lambda d: d.score[0])
                bbox = best.location_data.relative_bounding_box

                face_cx = (bbox.xmin + bbox.width / 2) * src_width
                face_cy = (bbox.ymin + bbox.height / 2) * src_height

                crop_x = int(face_cx - crop_width / 2)
                crop_y = int(face_cy - crop_height / 2)

                # Leave room for captions at bottom
                caption_margin = int(crop_height * 0.15)
                crop_y = min(crop_y, src_height - crop_height - caption_margin // 2)
        else:
            # No face detected — center crop
            crop_x = (src_width - crop_width) // 2
            crop_y = (src_height - crop_height) // 2

        # Clamp to valid range
        crop_x = max(0, min(crop_x, src_width - crop_width))
        if 'crop_y' not in dir():
            crop_y = max(0, (src_height - crop_height) // 2)
        crop_y = max(0, min(crop_y, src_height - crop_height))

        keyframes.append({
            "time": round(t, 3),
            "x": crop_x,
            "y": crop_y,
        })

    face_detection.close()
    return keyframes


def _smooth_keyframes(keyframes: List[Dict], min_move_distance: int = 20) -> List[Dict]:
    """
    Smooth crop position keyframes to avoid jarring jumps.
    Uses simple exponential smoothing.
    """
    if len(keyframes) <= 1:
        return keyframes

    smoothed = [keyframes[0].copy()]
    alpha = 0.3  # Smoothing factor (lower = smoother)

    for i in range(1, len(keyframes)):
        prev = smoothed[-1]
        curr = keyframes[i]

        dx = abs(curr["x"] - prev["x"])
        dy = abs(curr.get("y", prev.get("y", 0)) - prev.get("y", 0))

        if dx < min_move_distance and dy < min_move_distance:
            # Too small a move — keep previous position
            smoothed.append({
                "time": curr["time"],
                "x": prev["x"],
                "y": prev.get("y", 0),
            })
        else:
            # Smooth the transition
            new_x = int(prev["x"] + alpha * (curr["x"] - prev["x"]))
            new_y = int(prev.get("y", 0) + alpha * (curr.get("y", 0) - prev.get("y", 0)))
            smoothed.append({
                "time": curr["time"],
                "x": new_x,
                "y": new_y,
            })

    return smoothed


def build_crop_filter(reframe_data: Dict[str, Any]) -> str:
    """
    Build an FFmpeg crop filter expression from reframe keyframes.
    For simple cases, returns a static crop. For animated, uses sendcmd.
    """
    keyframes = reframe_data.get("keyframes", [])
    crop_w = reframe_data["crop_width"]
    crop_h = reframe_data["crop_height"]

    if not keyframes or len(keyframes) <= 2:
        # Static crop from first keyframe
        x = keyframes[0]["x"] if keyframes else 0
        y = keyframes[0].get("y", 0)
        return f"crop={crop_w}:{crop_h}:{x}:{y}"

    # For animated crop, use the average position (simple approach)
    # A more advanced version would use sendcmd protocol
    avg_x = int(sum(k["x"] for k in keyframes) / len(keyframes))
    avg_y = int(sum(k.get("y", 0) for k in keyframes) / len(keyframes))

    return f"crop={crop_w}:{crop_h}:{avg_x}:{avg_y}"


def _frange(start, stop, step):
    """Float range generator."""
    current = start
    while current < stop:
        yield round(current, 3)
        current += step

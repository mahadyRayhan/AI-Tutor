# backend/app/services/video_service.py
"""
Video Transcription & Context Service
Handles: Whisper transcription → JSON caching → timestamp-based slicing
"""

import json
import os
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Lazy-load Whisper to avoid slow import at startup
_whisper_model = None
_MODEL_SIZE = "base"  # Options: tiny, base, small, medium


def _get_whisper_model():
    """Lazy-load the Whisper model on first use."""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info(f"🎙️ Loading Whisper '{_MODEL_SIZE}' model (first-time download may take a moment)...")
        _whisper_model = whisper.load_model(_MODEL_SIZE)
        logger.info("✅ Whisper model loaded.")
    return _whisper_model


def _get_transcript_cache_path(video_path: str) -> Path:
    """Returns the JSON cache path for a given video file."""
    video_p = Path(video_path)
    return video_p.parent / f"{video_p.stem}.transcript.json"


def transcribe_video(video_path: str, force: bool = False) -> List[Dict]:
    """
    Transcribes a video file and returns a list of timestamped segments.
    Caches the result as a JSON file alongside the video.
    
    Returns:
        List of dicts: [{"start": 0.0, "end": 4.2, "text": "..."}, ...]
    """
    cache_path = _get_transcript_cache_path(video_path)
    
    # Check cache first
    if not force and cache_path.exists():
        logger.info(f"📋 Using cached transcript: {cache_path.name}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    # Transcribe with Whisper
    logger.info(f"🎙️ Transcribing video: {Path(video_path).name} (this may take a minute)...")
    model = _get_whisper_model()
    
    result = model.transcribe(
        video_path,
        language="en",
        verbose=False,
        word_timestamps=False,
    )
    
    # Extract segments
    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": round(seg["start"], 2),
            "end": round(seg["end"], 2),
            "text": seg["text"].strip(),
        })
    
    # Cache to JSON
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)
    
    logger.info(f"✅ Transcription complete: {len(segments)} segments → cached to {cache_path.name}")
    return segments


def get_transcript_until(video_path: str, timestamp: float) -> str:
    """
    Returns the transcript text from 0:00 up to the given timestamp.
    
    Args:
        video_path: Absolute path to the video file.
        timestamp: The time in seconds the student has watched up to.
    
    Returns:
        Formatted transcript string with time markers.
    """
    segments = transcribe_video(video_path)
    
    # Filter segments that start at or before the timestamp
    relevant = [s for s in segments if s["start"] <= timestamp]
    
    if not relevant:
        return "(No transcript available for this portion of the video.)"
    
    # Format with time markers for readability
    lines = []
    for seg in relevant:
        mins = int(seg["start"] // 60)
        secs = int(seg["start"] % 60)
        lines.append(f"[{mins:02d}:{secs:02d}] {seg['text']}")
    
    return "\n".join(lines)


def get_full_transcript(video_path: str) -> str:
    """Returns the complete transcript as a single text block."""
    segments = transcribe_video(video_path)
    return "\n".join(s["text"] for s in segments)


def get_video_duration_from_transcript(video_path: str) -> float:
    """Returns the approximate video duration based on the last transcript segment."""
    segments = transcribe_video(video_path)
    if segments:
        return segments[-1]["end"]
    return 0.0


def list_available_videos(video_dir: str) -> List[Dict]:
    """
    Lists all video files in the given directory.
    
    Returns:
        List of dicts: [{"filename": "Variables_in_C.mp4", "has_transcript": true, "size_mb": 18.5}, ...]
    """
    video_dir_path = Path(video_dir)
    if not video_dir_path.exists():
        return []
    
    videos = []
    supported_extensions = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
    
    for f in sorted(video_dir_path.iterdir()):
        if f.suffix.lower() in supported_extensions and f.is_file():
            transcript_path = _get_transcript_cache_path(str(f))
            videos.append({
                "filename": f.name,
                "has_transcript": transcript_path.exists(),
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
            })
    
    return videos

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


def _window_text(segments: List[Dict], start: float, end: float) -> str:
    """Concatenate transcript text within [start, end)."""
    return " ".join(s["text"] for s in segments if start <= s["start"] < end).strip()


def _plan_checkpoint_times(duration: float, target_spacing_sec: float,
                           min_cp: int, max_cp: int) -> List[float]:
    """
    Dynamically place checkpoints based on video length.

    The video is split into N equal segments (N scales with duration via
    target_spacing), and a checkpoint is placed at the END of each segment — so
    the last checkpoint always lands at the end of the video.

      e.g. 17-min video, spacing ~8.5 min → N=2 → checkpoints at ~8.5 min and ~17 min.

    The final checkpoint is nudged 2s before the very end so it reliably fires
    during playback (before the 'ended' event).
    """
    if duration <= 0:
        return []
    n = round(duration / target_spacing_sec)
    n = max(min_cp, min(n, max_cp))
    times = [duration * i / n for i in range(1, n + 1)]
    times[-1] = max(0.0, min(times[-1], duration - 2.0))
    return [round(t, 1) for t in times]


def generate_checkpoints(video_path: str, llm, target_spacing_sec: float = 510.0,
                         min_checkpoints: int = 1, max_checkpoints: int = 8) -> List[Dict]:
    """
    Generate one LLM multiple-choice question per checkpoint. Checkpoint count and
    placement are DYNAMIC — the video is divided into N equal segments (N scales
    with length), a checkpoint sits at the end of each, and each MCQ is based on
    ITS OWN segment of content. The last checkpoint is at the end of the video.

    Returns list of dicts:
      {checkpoint_time, question, options[4], correct_index, concept, explanation}
    """
    segments = transcribe_video(video_path)
    if not segments:
        return []
    duration = segments[-1]["end"]

    cp_times = _plan_checkpoint_times(duration, target_spacing_sec,
                                      min_checkpoints, max_checkpoints)

    checkpoints = []
    prev = 0.0
    for t in cp_times:
        window = _window_text(segments, prev, t)  # this checkpoint's own segment
        prev = t
        if len(window) < 40:  # not enough content to quiz on
            continue

        prompt = f"""You are creating a comprehension check for a C programming lecture.
Based ONLY on the transcript excerpt below, write ONE multiple-choice question that
tests whether the student understood this segment.

TRANSCRIPT EXCERPT (this lecture segment):
\"\"\"{window[:2500]}\"\"\"

Return STRICT JSON only, no markdown, in exactly this shape:
{{"question": "...", "options": ["A", "B", "C", "D"], "correct_index": 0,
  "concept": "<the C concept this tests, e.g. Pointers>", "explanation": "why the answer is correct"}}
Rules: exactly 4 options, correct_index is 0-3, question must be answerable from the excerpt."""

        try:
            raw = llm.generate_response(prompt).strip()
            # Strip code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1] if "```" in raw[3:] else raw
                raw = raw.replace("json", "", 1).strip("` \n")
            mcq = json.loads(raw)
            opts = mcq.get("options", [])
            ci = int(mcq.get("correct_index", 0))
            if isinstance(opts, list) and len(opts) == 4 and 0 <= ci <= 3 and mcq.get("question"):
                checkpoints.append({
                    "checkpoint_time": round(t, 1),
                    "question": mcq["question"].strip(),
                    "options": opts,
                    "correct_index": ci,
                    "concept": (mcq.get("concept") or "").strip(),
                    "explanation": (mcq.get("explanation") or "").strip(),
                })
        except Exception as e:
            logger.warning(f"[checkpoint] MCQ generation failed at {t}s: {e}")

    logger.info(f"✅ Generated {len(checkpoints)} checkpoint MCQs for {Path(video_path).name} "
                f"(duration {duration:.0f}s → {len(cp_times)} planned)")
    return checkpoints


_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "how", "what", "when",
    "where", "who", "why", "which", "that", "this", "to", "of", "in",
    "on", "at", "by", "for", "with", "about", "from", "and", "but", "or",
    "i", "me", "my", "we", "you", "he", "she", "it", "they", "them",
}


def find_timestamp_hints(video_path: str, question: str, current_timestamp: float) -> Dict:
    """
    Searches transcript segments before and after current_timestamp for keywords
    from the question. Returns both a forward hint and a backward hint (each may be None).

    Returns:
        {
            "forward":  {"start": float, "end": float} | None,
            "backward": {"start": float, "end": float} | None,
        }
    """
    import re
    segments = transcribe_video(video_path)

    words = re.findall(r"[a-zA-Z]+", question.lower())
    keywords = {w for w in words if w not in _STOP_WORDS and len(w) > 2}
    if not keywords:
        return {"forward": None, "backward": None}

    past   = [s for s in segments if s["end"]   < current_timestamp]
    future = [s for s in segments if s["start"] > current_timestamp]

    def _best_window(segs: list) -> Optional[Dict]:
        best_idx, best_score = -1, 0
        for i, seg in enumerate(segs):
            seg_words = set(re.findall(r"[a-zA-Z]+", seg["text"].lower()))
            score = len(keywords & seg_words)
            if score > best_score:
                best_score, best_idx = score, i
        if best_score < 2:
            return None
        start_seg = segs[best_idx]
        end_seg   = start_seg
        window_end = start_seg["start"] + 30.0
        for seg in segs[best_idx:]:
            if seg["start"] <= window_end:
                end_seg = seg
            else:
                break
        return {"start": start_seg["start"], "end": end_seg["end"]}

    return {
        "forward":  _best_window(future),
        "backward": _best_window(past),
    }


def get_or_generate_video_meta(video_path: str, llm=None) -> Dict:
    """
    Returns a cached high-level one-sentence summary of the full video.
    Generates once on first call and caches as {stem}.meta.json alongside the transcript.
    Returns: {"summary": "This video introduces variables in C — ..."}
    """
    video_p = Path(video_path)
    meta_path = video_p.parent / f"{video_p.stem}.meta.json"

    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    if llm is None:
        return {"summary": None}

    segments = transcribe_video(video_path)
    full_text = " ".join(s["text"] for s in segments)[:3000]

    prompt = f"""Based on this lecture transcript, write exactly ONE concise sentence (max 20 words) summarizing what this video teaches.
Start with "This video" and focus on what students will learn.

Transcript:
{full_text}

Summary (one sentence only):"""

    try:
        summary = llm.generate_response(prompt).strip().strip('"\'')
        if len(summary) > 200:
            summary = summary[:200].rsplit(" ", 1)[0] + "."
    except Exception as e:
        logger.warning(f"[CLASSROOM] Failed to generate video meta: {e}")
        return {"summary": None}

    meta = {"summary": summary}
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        logger.info(f"📝 [CLASSROOM] Video meta cached: {meta_path.name}")
    except Exception as e:
        logger.warning(f"[CLASSROOM] Failed to cache meta: {e}")

    return meta


def list_available_videos(video_dir: str) -> List[Dict]:
    """
    Lists all video files in the given directory with rich metadata.
    
    Extracts topic and display name from filenames:
      - 'Variables_in_C.mp4' → topic='Variables', title='Variables in C'
      - 'Control_Flow_Basics.mp4' → topic='Control Flow', title='Control Flow Basics'
    
    Returns:
        List of dicts with filename, title, topic, has_transcript, size_mb, duration_sec
    """
    video_dir_path = Path(video_dir)
    if not video_dir_path.exists():
        return []
    
    # Known topic keywords (match against the start of filenames)
    TOPIC_KEYWORDS = {
        "Variables": "Variables",
        "Control_Flow": "Control Flow",
        "Control Flow": "Control Flow",
        "Functions": "Functions",
        "Function": "Functions",
        "Arrays": "Arrays",
        "Array": "Arrays",
        "Strings": "Strings",
        "String": "Strings",
        "Pointers": "Pointers",
        "Pointer": "Pointers",
        "Structures": "Structures",
        "Structure": "Structures",
        "Struct": "Structures",
        "Loops": "Control Flow",
        "Loop": "Control Flow",
    }
    
    videos = []
    supported_extensions = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
    
    for f in sorted(video_dir_path.iterdir()):
        if f.suffix.lower() in supported_extensions and f.is_file():
            transcript_path = _get_transcript_cache_path(str(f))
            
            # Extract display name from filename
            stem = f.stem  # e.g. "Variables_in_C"
            display_name = stem.replace("_", " ").replace("-", " ")
            # Clean up camelCase: "VariablesInC" → "Variables In C"
            import re
            display_name = re.sub(r'([a-z])([A-Z])', r'\1 \2', display_name)
            
            # Match topic from filename
            topic = "General"
            for keyword, topic_name in TOPIC_KEYWORDS.items():
                if keyword.lower() in stem.lower():
                    topic = topic_name
                    break
            
            # Get duration from transcript cache if available
            duration = 0.0
            if transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8") as tf:
                        segments = json.load(tf)
                        if segments:
                            duration = segments[-1].get("end", 0.0)
                except Exception:
                    pass
            
            videos.append({
                "filename": f.name,
                "title": display_name,
                "topic": topic,
                "has_transcript": transcript_path.exists(),
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                "duration_sec": round(duration, 1),
            })
    
    return videos

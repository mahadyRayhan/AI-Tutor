"""
Pre-generate + cache checkpoint MCQs for every video, so the first student
never waits for LLM generation. Also serves as a test that the generation
pipeline actually works.

Usage:
  python scripts/pregenerate_checkpoints.py           # all videos, skip already-cached
  python scripts/pregenerate_checkpoints.py --force    # regenerate all
"""

import os
import sys
import json
import logging
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core import config
from app.db.sqlite_db import db
from app.db.llm_interface import LLMInterface
from app.services.video_service import generate_checkpoints

VIDEO_DIR = config.DB_DIR / "video"
SPACING, MIN_CP, MAX_CP = 510.0, 1, 8


def main():
    force = "--force" in sys.argv
    logging.basicConfig(level=logging.WARNING)
    logger = logging.getLogger("pregenerate")
    llm = LLMInterface(google_model_id=config.DEFAULT_GOOGLE_MODEL_ID, logger=logger)

    videos = [f for f in os.listdir(VIDEO_DIR) if f.lower().endswith(".mp4")]
    print(f"Found {len(videos)} videos in {VIDEO_DIR}\n")

    for vid in sorted(videos):
        cached = db.fetch_one(
            "SELECT COUNT(*) c FROM video_checkpoint WHERE video_filename=?", (vid,))["c"]
        if cached and not force:
            print(f"⏭️  {vid}: {cached} checkpoints already cached (use --force to regenerate)")
            continue
        if force:
            db.execute("DELETE FROM video_checkpoint WHERE video_filename=?", (vid,))

        print(f"⚙️  {vid}: generating...")
        try:
            cps = generate_checkpoints(str(VIDEO_DIR / vid), llm, SPACING, MIN_CP, MAX_CP)
        except Exception as e:
            print(f"   ❌ generation error: {e}")
            continue

        for cp in cps:
            db.execute(
                "INSERT OR IGNORE INTO video_checkpoint "
                "(video_filename, checkpoint_time, question, options, correct_index, "
                " concept, explanation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (vid, cp["checkpoint_time"], cp["question"], json.dumps(cp["options"]),
                 cp["correct_index"], cp["concept"], cp["explanation"],
                 datetime.utcnow().isoformat()))
        print(f"   ✅ {len(cps)} checkpoints:")
        for cp in cps:
            m = int(cp["checkpoint_time"] // 60)
            print(f"      [{m:>2}min] ({cp['concept']}) {cp['question'][:70]}...")
        print()


if __name__ == "__main__":
    main()

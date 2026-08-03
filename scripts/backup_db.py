"""
Daily DB backup for the class trial. One corrupted SQLite file = total data loss,
so this makes a consistent, timestamped snapshot and prunes old ones.

Uses SQLite's online backup API (safe while the server is running).

Usage
-----
  python scripts/backup_db.py [backup_dir]

Default backup_dir: <PROJECT_ROOT>/backups
Set BACKUP_DIR env var (e.g. a OneDrive/cloud-synced folder) to store off-machine.

Schedule (cron example, daily at 2am):
  0 2 * * * cd /path/to/AI-Tutor && python scripts/backup_db.py
"""

import os
import sys
import sqlite3
import glob
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.core import config

KEEP_DAYS = 14  # how many daily backups to retain


def main():
    src = os.path.join(config.DB_DIR, "ai_tutor.db")
    if not os.path.exists(src):
        print(f"❌ Source DB not found: {src}")
        sys.exit(1)

    backup_dir = (sys.argv[1] if len(sys.argv) > 1
                  else os.environ.get("BACKUP_DIR", str(config.PROJECT_ROOT / "backups")))
    os.makedirs(backup_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"ai_tutor_{stamp}.db")

    # Consistent online backup (safe even if the app is mid-write)
    source = sqlite3.connect(src)
    target = sqlite3.connect(dest)
    with target:
        source.backup(target)
    target.close()
    source.close()

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"✅ Backup written: {dest} ({size_mb:.2f} MB)")

    # Prune backups older than KEEP_DAYS
    all_backups = sorted(glob.glob(os.path.join(backup_dir, "ai_tutor_*.db")))
    if len(all_backups) > KEEP_DAYS:
        for old in all_backups[:-KEEP_DAYS]:
            os.remove(old)
            print(f"🗑️  Pruned old backup: {os.path.basename(old)}")


if __name__ == "__main__":
    main()

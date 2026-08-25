#!/usr/bin/env python3
"""
Seed the lecture catalog from the Fall 2026 CMP_SC1050 Flipped Classroom Plan.

Source: Additional_files/FS2026_CS1050_FlippedClassroomPlan.pdf

Creates one `video_chapter` row per chapter (0-14, matching the plan's own numbering,
which skips 12 and 13) and one `video_catalog` row per PLANNED video. Videos with no
recording yet are seeded with filename=NULL and status='planned', so the classroom can
show a chapter's full roadmap rather than only what happens to be uploaded.

Existing recordings in the video directory are matched onto slots by the FILENAME_HINTS
map below; anything unmatched is left for a teacher to assign in the dashboard.

Idempotent: re-running updates titles/slides/sections in place and never duplicates a
slot or clears a filename that has already been assigned.

Dry run (default):
    python scripts/seed_video_catalog.py
Apply:
    python scripts/seed_video_catalog.py --apply
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO, "backend", "database", "ai_tutor.db")
VIDEO_DIR = os.path.join(REPO, "backend", "database", "video")

# (number, title, status)
CHAPTERS = [
    (0,  "How to SRL", ""),
    (1,  "Introduction to Computers and C Programming", ""),
    (2,  "Introduction to C Programming", "SLIDES UPDATED"),
    (3,  "Structured Program Development in C", "SLIDES UPDATED"),
    (4,  "C Program Control", ""),
    (5,  "Functions", ""),
    (6,  "Chapter 6", ""),
    (7,  "Chapter 7", ""),
    (8,  "Chapter 8", ""),
    (9,  "Chapter 9", ""),
    (10, "Chapter 10", ""),
    (11, "Chapter 11", ""),
    (14, "Chapter 14", ""),
]

# (chapter, video_number, title, slides, sections, status)
#
# Note: "Video 14" appears in BOTH chapter 3 and chapter 4 in the plan — the numbering
# restarts mid-plan. video_number is therefore NOT unique; the primary key is the row.
VIDEOS = [
    (1,  1,  "Computers: Hardware and Software; Student Outcomes; Assignment/Assessment", "Slides 1-6",  "", "complete"),
    (1,  2,  "Data Hierarchy",                                                            "Slides 7-13", "", "complete"),
    (1,  3,  "Machine Languages, Assembly Languages, and High-Level Languages",           "Slides 14-19", "", "complete"),
    (1,  4,  "History of C, and Typical C Program Development Environment",               "Slides 20-31", "", "complete"),

    (2,  5,  "A Simple C Program: Printing a Line of Text",                               "Slides 1-19",  "", "complete"),
    (2,  6,  "Another Simple C Program: Adding Two Integers",                             "Slides 20-35", "", "complete"),
    (2,  7,  "Memory Concepts and Arithmetic in C",                                       "Slides 36-47", "", "complete"),
    (2,  8,  "Decision making (Equality and Relational Operators), Keywords, Secure C Programming", "Slides 48-62", "", "complete"),

    (3,  9,  "Introduction, Algorithms, Pseudocode, Control structures",                  "Slides 1-17",  "Sections 3.1, 3.2, 3.3, 3.4", "planned"),
    (3,  10, "If selection, if-else Selection Statement",                                 "Slides 18-31", "Sections 3.5, 3.6", "planned"),
    (3,  11, "While-statements, counter-controlled repetition",                           "Slides 32-42", "Sections 3.7, 3.8", "planned"),
    (3,  12, "While-statements, sentinel-controlled repetition",                          "Slides 43-61", "Section 3.9",  "planned"),
    (3,  13, "While-statements, nested control structures",                               "Slides 62-75", "Section 3.10", "planned"),
    (3,  14, "Assignment operators, increment and decrement operators",                   "Slides 76-86", "Sections 3.11, 3.12", "planned"),

    (4,  14, "Repetition, and recap of counter-controlled repetition",                    "Slides 1-8",   "", "planned"),
    (4,  15, "For-statement",                                                             "Slides 9-18",  "", "planned"),
    (4,  16, "for-statements (contd) and examples of for-statements",                     "Slides 19-32", "", "planned"),
    (4,  17, "switch Multiple selection statements",                                      "Slides 33-55", "", "planned"),
    (4,  18, "do-while statements",                                                       "Slides 56-60", "", "planned"),
    (4,  19, "Break and continue statements",                                             "Slides 61-68", "", "planned"),
    (4,  20, "Logical operators",                                                         "Slides 69-82", "", "planned"),
    (4,  21, "Equality vs assignment operators",                                          "Slides 83-93", "", "planned"),

    (5,  22, "Introduction to functions, program modules in C, and Math Library functions", "Slides 1-9",  "", "planned"),
    (5,  23, "C Functions and function definitions",                                       "Slides 10-28", "", "planned"),
    (5,  24, "Function prototypes",                                                        "",             "", "planned"),
]

# Existing recordings → (chapter, video_number) of the slot they belong to.
# Everything else in the video directory is left unassigned for the teacher to place.
FILENAME_HINTS = {
    "Data_hierarchy.mp4":         (1, 2),
    "Intro_C.mp4":                (2, 5),
    "Class-Variables.mp4":        (2, 7),
    "Variables_in_C.mp4":         (2, 7),
    "C_Control_Structures.mp4":   (3, 9),
}

# Skill-graph topic per chapter, so a catalog video still cross-links to the mastery
# model. Coarse on purpose — the 9 skill topics do not map 1:1 onto 14 chapters.
CHAPTER_TOPIC = {
    1: "Variables", 2: "Variables", 3: "Control Flow", 4: "Control Flow",
    5: "Functions", 6: "Arrays", 7: "Pointers", 8: "Strings",
    9: "Structures", 10: "File I/O", 11: "Memory Allocation",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now()

    # The app creates these tables at startup; create them here too so the seed can run
    # against a database that has not been opened by the app yet.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS video_chapter (
            number INTEGER PRIMARY KEY, title TEXT NOT NULL,
            status TEXT DEFAULT '', sort_order INTEGER, created_at TIMESTAMP);
        CREATE TABLE IF NOT EXISTS video_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chapter_number INTEGER NOT NULL,
            video_number INTEGER, title TEXT NOT NULL, slides TEXT DEFAULT '',
            sections TEXT DEFAULT '', filename TEXT, topic TEXT DEFAULT '',
            status TEXT DEFAULT 'planned', sort_order INTEGER,
            created_at TIMESTAMP, updated_at TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_catalog_chapter ON video_catalog(chapter_number, sort_order);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_filename ON video_catalog(filename) WHERE filename IS NOT NULL;
    """)

    added_ch = updated_ch = added_v = updated_v = linked = 0

    for i, (num, title, status) in enumerate(CHAPTERS):
        row = conn.execute("SELECT number FROM video_chapter WHERE number=?", (num,)).fetchone()
        if row:
            conn.execute("UPDATE video_chapter SET title=?, status=?, sort_order=? WHERE number=?",
                         (title, status, i, num))
            updated_ch += 1
        else:
            conn.execute("INSERT INTO video_chapter (number,title,status,sort_order,created_at) "
                         "VALUES (?,?,?,?,?)", (num, title, status, i, now))
            added_ch += 1

    on_disk = set()
    if os.path.isdir(VIDEO_DIR):
        on_disk = {f for f in os.listdir(VIDEO_DIR)
                   if os.path.splitext(f)[1].lower() in {".mp4", ".mkv", ".avi", ".mov", ".webm"}}

    slot_file = {}
    for fname, slot in FILENAME_HINTS.items():
        if fname in on_disk and slot not in slot_file:
            slot_file[slot] = fname   # first hint wins; a slot holds one recording

    for i, (ch, vnum, title, slides, sections, status) in enumerate(VIDEOS):
        existing = conn.execute(
            "SELECT id, filename FROM video_catalog WHERE chapter_number=? AND video_number=? AND title=?",
            (ch, vnum, title)).fetchone()
        topic = CHAPTER_TOPIC.get(ch, "")
        fname = slot_file.get((ch, vnum))

        if existing:
            # Never clear an assignment a teacher already made.
            keep = existing["filename"] or fname
            conn.execute(
                "UPDATE video_catalog SET title=?, slides=?, sections=?, topic=?, "
                "sort_order=?, filename=?, status=?, updated_at=? WHERE id=?",
                (title, slides, sections, topic, i, keep,
                 "complete" if keep else status, now, existing["id"]))
            updated_v += 1
            if keep and not existing["filename"]:
                linked += 1
        else:
            conn.execute(
                "INSERT INTO video_catalog (chapter_number,video_number,title,slides,sections,"
                "filename,topic,status,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ch, vnum, title, slides, sections, fname, topic,
                 "complete" if fname else status, i, now, now))
            added_v += 1
            if fname:
                linked += 1

    unassigned = sorted(on_disk - set(slot_file.values()))

    print(f"chapters : +{added_ch} new, {updated_ch} updated")
    print(f"videos   : +{added_v} new, {updated_v} updated")
    print(f"recordings linked to slots : {linked}")
    if unassigned:
        print(f"\nunassigned recordings ({len(unassigned)}) — assign these in the teacher dashboard:")
        for f in unassigned:
            print(f"  - {f}")

    if args.apply:
        conn.commit()
        print("\n✅ committed")
    else:
        conn.rollback()
        print("\n(dry run — re-run with --apply to write)")
    conn.close()


if __name__ == "__main__":
    main()

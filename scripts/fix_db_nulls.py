# scripts/fix_db_nulls.py
import sqlite3
import os

# Path to your DB
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", "database", "ai_tutor.db")

def fix_nulls():
    if not os.path.exists(DB_PATH):
        print("❌ Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("--- Cleaning Database ---")
    
    # 1. Fix Topics
    cursor.execute("UPDATE messages SET topic = 'General' WHERE topic IS NULL")
    print(f"✅ Fixed {cursor.rowcount} rows with NULL topic.")

    # 2. Fix Intents
    cursor.execute("UPDATE messages SET intent = 'UNKNOWN' WHERE intent IS NULL")
    print(f"✅ Fixed {cursor.rowcount} rows with NULL intent.")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    fix_nulls()
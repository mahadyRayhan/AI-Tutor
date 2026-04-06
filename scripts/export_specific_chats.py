import sqlite3
import os
from datetime import datetime, timedelta

# --- CONFIGURATION ---
# DB_PATH = "historic-db/ai_tutor-april-01.db"
# DB_PATH = "backend/database/ai_tutor.db"
DB_PATH = "/Users/mhr6wb/Music/ai_tutor.db"
EXPORT_DIR = "historic-db/chat_exports_april_02"

def export_all_users_to_txt():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        print("Please update the DB_PATH variable in this script.")
        return

    # Create the export directory if it doesn't exist
    os.makedirs(EXPORT_DIR, exist_ok=True)

    print(f"📂 Connecting to database: {os.path.abspath(DB_PATH)}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Access columns by name
    cursor = conn.cursor()

    # 1. Get all unique users who have active sessions
    cursor.execute("SELECT DISTINCT username FROM sessions WHERE deleted = 0")
    users = [row['username'] for row in cursor.fetchall()]
    
    if not users:
        print("📭 No active chat sessions found in the database.")
        conn.close()
        return

    print(f"🔍 Found {len(users)} users. Starting export...\n")
    cutoff_date = datetime.now() - timedelta(days=2)

    # 2. Iterate through each user
    for username in users:
        # Create one text file per user
        filepath = os.path.join(EXPORT_DIR, f"{username}_chat_history.txt")
        
        # 3. Get all sessions for this user, ordered by oldest to newest
        cursor.execute("""
            SELECT session_id, title, created_at 
            FROM sessions 
            WHERE username = ? 
            AND deleted = 0
            AND created_at >= ? 
            ORDER BY created_at ASC
        """, (username, cutoff_date))
        sessions = cursor.fetchall()
        
        if not sessions:
            continue

        # Open the user's file to write all their sessions
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"MASTER CHAT LOG FOR USER: {username.upper()}\n")
            f.write(f"Total Sessions: {len(sessions)}\n")
            f.write(f"Exported On: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n\n")

            # 4. Iterate through each session
            for session in sessions:
                session_id = session['session_id']
                title = session['title'] or "Untitled Chat"
                created_at = session['created_at']

                # --- SESSION DIVIDER ---
                f.write("=" * 80 + "\n")
                f.write(f"SESSION: {title}\n")
                f.write(f"DATE:    {created_at}\n")
                f.write(f"ID:      {session_id}\n")
                f.write("=" * 80 + "\n\n")

                # 5. Fetch all messages for this session
                cursor.execute("""
                    SELECT role, content, intent, topic, timestamp 
                    FROM messages 
                    WHERE session_id = ? 
                    ORDER BY id ASC
                """, (session_id,))
                
                messages = cursor.fetchall()

                # 6. Write each message
                for msg in messages:
                    # Format time (e.g., grab just the YYYY-MM-DD HH:MM part if available)
                    time_str = msg['timestamp'][:16] if msg['timestamp'] else "Unknown Time"
                    
                    if msg['role'] == "user":
                        # Add tracking tags for student messages
                        intent = msg['intent'] or "UNKNOWN"
                        topic = msg['topic'] or "General"
                        f.write(f"[{time_str}] STUDENT (Intent: {intent} | Topic: {topic}):\n")
                    else:
                        f.write(f"[{time_str}] AI TUTOR:\n")
                    
                    # Write the actual text and a message divider
                    f.write(f"{msg['content']}\n")
                    f.write("-" * 50 + "\n\n")
                
                # Add some spacing before the next session starts
                f.write("\n\n")

        print(f"✅ Exported: {username}_chat_history.txt ({len(sessions)} sessions)")

    conn.close()
    print(f"\n🎉 Export Complete! All files saved to: {os.path.abspath(EXPORT_DIR)}")

if __name__ == "__main__":
    export_all_users_to_txt()
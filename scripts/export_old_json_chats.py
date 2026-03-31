import json
import os
from datetime import datetime

# --- CONFIGURATION ---
# Path to your old JSON database
JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "DEP-V2", "database", "user_sessions.json")
# Where you want the exported text files to go
EXPORT_DIR = "/Users/mhr6wb/Movies/old_json_exports"

def parse_timestamp(ts_str):
    """Handles ISO format parsing safely."""
    if not ts_str:
        return None
    try:
        # Sometimes JSON timestamps have a 'Z' at the end or microsecond precision
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except Exception:
        return None

def export_old_json_chats():
    if not os.path.exists(JSON_PATH):
        print(f"❌ File not found at: {os.path.abspath(JSON_PATH)}")
        print("Please update the JSON_PATH variable in this script.")
        return

    # Create the export directory if it doesn't exist
    os.makedirs(EXPORT_DIR, exist_ok=True)

    print(f"📂 Reading old JSON database: {os.path.abspath(JSON_PATH)}")
    
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data:
        print("📭 JSON file is empty.")
        return

    print(f"🔍 Found {len(data)} users. Starting export...\n")

    # 1. Iterate through each user in the JSON structure
    # Structure: { "username": { "session_id": { "title": "...", "messages": [...] } } }
    for username, sessions in data.items():
        
        # We need to sort the sessions chronologically. 
        # We will use the timestamp of the first message in the session as the sort key.
        session_list = []
        for session_id, session_data in sessions.items():
            messages = session_data.get("messages", [])
            if not messages:
                continue # Skip empty sessions
                
            # Get the first timestamp for sorting
            first_msg_ts = parse_timestamp(messages[0].get("timestamp"))
            sort_key = first_msg_ts if first_msg_ts else datetime.min
            
            session_list.append({
                "session_id": session_id,
                "data": session_data,
                "sort_key": sort_key
            })
            
        # Sort sessions from oldest to newest
        session_list.sort(key=lambda x: x["sort_key"])

        if not session_list:
            continue

        # 2. Create one text file per user
        filepath = os.path.join(EXPORT_DIR, f"{username}_old_chat_history.txt")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"MASTER CHAT LOG FOR USER: {username.upper()} (LEGACY DATA)\n")
            f.write(f"Total Sessions: {len(session_list)}\n")
            f.write(f"Exported On: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n\n")

            # 3. Iterate through the sorted sessions
            for session_obj in session_list:
                session_id = session_obj["session_id"]
                s_data = session_obj["data"]
                messages = s_data.get("messages", [])
                
                # Try to get the title, or fallback to something generic
                title = s_data.get("title", "Untitled Chat")
                
                # Format the date for the session header
                if session_obj["sort_key"] != datetime.min:
                    session_date_str = session_obj["sort_key"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    session_date_str = "Unknown Date"

                # --- SESSION DIVIDER ---
                f.write("=" * 80 + "\n")
                f.write(f"SESSION: {title}\n")
                f.write(f"DATE:    {session_date_str}\n")
                f.write(f"ID:      {session_id}\n")
                f.write("=" * 80 + "\n\n")

                # 4. Iterate through messages in the session
                for msg in messages:
                    # Safely parse the message timestamp
                    ts = parse_timestamp(msg.get("timestamp"))
                    time_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "Unknown Time"
                    
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    
                    # Old JSON might not have intent/topic, so we use .get() safely
                    intent = msg.get("intent", "UNKNOWN")
                    topic = msg.get("topic", "General")
                    
                    if role == "user":
                        # Only print Intent/Topic if they actually exist in the old data
                        if intent != "UNKNOWN" or topic != "General":
                            f.write(f"[{time_str}] STUDENT (Intent: {intent} | Topic: {topic}):\n")
                        else:
                            f.write(f"[{time_str}] STUDENT:\n")
                    else:
                        f.write(f"[{time_str}] AI TUTOR:\n")
                    
                    # Write the actual text and a message divider
                    f.write(f"{content}\n")
                    f.write("-" * 50 + "\n\n")
                
                # Add some spacing before the next session starts
                f.write("\n\n")

        print(f"✅ Exported: {username}_old_chat_history.txt ({len(session_list)} sessions)")

    print(f"\n🎉 Legacy JSON Export Complete! Files saved to: {os.path.abspath(EXPORT_DIR)}")

if __name__ == "__main__":
    export_old_json_chats()
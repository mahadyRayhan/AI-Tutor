import json
import os
from datetime import datetime, timedelta

# Path to your JSON database
# Adjust this if your json file is in a different location
JSON_PATH = "DEP-V2/database/user_sessions.json"

def parse_timestamp(ts_str):
    """Handles ISO format parsing safely."""
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None

def calculate_duration(timestamps):
    """
    Calculates active time from a sorted list of datetime objects.
    Logic: Sum differences between messages. If gap > 20 mins, count as new session.
    """
    if not timestamps or len(timestamps) < 2:
        return 0 # Less than 2 messages = negligible time

    total_seconds = 0
    SESSION_THRESHOLD = 20 * 60  # 20 minutes in seconds

    # We assume the first message takes 1 minute of 'active' time (reading/typing)
    total_seconds += 60 

    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i-1]).total_seconds()

        if delta < SESSION_THRESHOLD:
            # Continuous session: add the actual time difference
            total_seconds += delta
        else:
            # New session started after a long break:
            # Add 1 minute baseline for the new interaction start
            total_seconds += 60

    return total_seconds

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def main():
    if not os.path.exists(JSON_PATH):
        print(f"❌ File not found: {JSON_PATH}")
        return

    print(f"📂 Reading: {JSON_PATH}...\n")
    
    with open(JSON_PATH, 'r') as f:
        data = json.load(f)

    print(f"{'USERNAME':<20} | {'MESSAGES':<10} | {'ACTIVE TIME':<15}")
    print("-" * 55)

    # user_sessions.json structure: { "username": { "session_id": { "messages": [...] } } }
    for username, sessions in data.items():
        all_timestamps = []
        msg_count = 0

        # 1. Collect ALL timestamps across ALL sessions for this user
        for session_id, session_data in sessions.items():
            messages = session_data.get("messages", [])
            for msg in messages:
                ts = parse_timestamp(msg.get("timestamp"))
                if ts:
                    all_timestamps.append(ts)
                    msg_count += 1

        # 2. Sort chronologically
        all_timestamps.sort()

        # 3. Calculate
        total_seconds = calculate_duration(all_timestamps)
        time_str = format_time(total_seconds)

        print(f"{username:<20} | {msg_count:<10} | {time_str:<15}")

if __name__ == "__main__":
    main()
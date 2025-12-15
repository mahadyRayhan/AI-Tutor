import json
import csv
import os
import sys

# Path setup
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.core import config

def main():
    json_path = config.PROJECT_ROOT / "database" / "chat_history.json"
    csv_path = config.PROJECT_ROOT / "database" / "research_dataset.csv"
    
    with open(json_path, 'r') as f:
        data = json.load(f)
        
    # Define columns for your Causal Model
    headers = [
        "timestamp", "username", "intent", "topic", 
        "context_mastery", "context_has_goal", # CONTEXT
        "treatment_diagram", "treatment_prereq", "treatment_review", "treatment_block", # TREATMENT
        "outcome_next_intent" # OUTCOME (Calculated)
    ]
    
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        # We iterate to N-1 to calculate "Next Action" (Outcome)
        for i in range(len(data) - 1):
            curr = data[i]
            next_interaction = data[i+1]
            
            # Only compare if same user
            if curr['username'] != next_interaction['username']:
                outcome = "END_SESSION"
            else:
                outcome = next_interaction['intent'] # Did they go from CONCEPT -> PROBLEM?
            
            meta = curr.get('metadata', {})
            
            writer.writerow([
                curr['timestamp'],
                curr['username'],
                curr['intent'],
                curr['topic'],
                meta.get('context_mastery_score', 0),
                1 if meta.get('context_has_goal') else 0,
                1 if meta.get('treatment_diagram') else 0,
                1 if meta.get('treatment_prereq_check') else 0,
                1 if meta.get('treatment_code_review') else 0,
                1 if meta.get('treatment_topic_block') else 0,
                outcome
            ])
            
    print(f"✅ Exported {len(data)-1} interaction pairs to {csv_path}")

if __name__ == "__main__":
    main()
# backend/app/core/user_knowledge_manager.py

import logging
from datetime import datetime
from typing import List
from app.db.sqlite_db import db

logger = logging.getLogger(__name__)

class UserKnowledgeManager:
    # Self-learning blocklist: starts with known garbage, grows at runtime
    _garbage_concepts = {
        "rewritten", "can", "the", "it", "this", "that", "why", "how",
        "question", "general", "code submission", "code", "declare",
        "write", "explain", "back", "what", "who", "where", "when",
        "your", "my", "do", "does", "yes", "no", "ok", "okay",
        "new", "chat", "help", "please", "thanks", "thank",
        "potato", "anyway", "accidental", "send", "skip"
    }

    def get_known_concepts(self, username: str) -> List[str]:
        """Returns list of concepts the user has mastered, ordered by timestamp, filtered."""
        rows = db.fetch_all(
            "SELECT concept FROM user_knowledge WHERE username = ? ORDER BY timestamp ASC",
            (username,)
        )
        all_concepts = [r['concept'] for r in rows]

        # Filter out any concepts that are now in the garbage blocklist
        valid = [c for c in all_concepts if c.lower() not in self._garbage_concepts and len(c) >= 3]
        return valid

    def clear_concepts(self, username: str):
        """Removes ALL mastery records for a user (used by test agent for clean runs)."""
        db.execute("DELETE FROM user_knowledge WHERE username = ?", (username,))
        logger.info(f"🗑️ Cleared all mastery data for user: {username}")

    def mark_concept_as_known(self, username: str, concept: str):
        """Adds a concept to the DB with multi-layer validation."""
        if not concept:
            return
            
        concept_clean = concept.strip()
        
        # Guard 1: Too short
        if len(concept_clean) < 3:
            logger.debug(f"🛡️ [DB Guard] Too short: '{concept_clean}'")
            return
        
        # Guard 2: Has question mark or is a sentence
        if "?" in concept_clean or len(concept_clean.split()) > 3:
            logger.info(f"🛡️ [DB Guard] Refused sentence: '{concept_clean}'")
            return
        
        # Guard 3: In-memory blocklist (fast check, self-learning)
        if concept_clean.lower() in self._garbage_concepts:
            logger.info(f"🛡️ [DB Guard] Blocked by garbage list: '{concept_clean}'")
            return
        
        # Guard 4: Validate against Knowledge Graph (robust check)
        if not self._validate_against_graph(concept_clean):
            # SELF-LEARNING: Add the rejected concept to the blocklist
            # so future occurrences are caught instantly without a graph lookup
            self._garbage_concepts.add(concept_clean.lower())
            logger.warning(f"🛡️🧠 [DB Guard] '{concept_clean}' not in graph → added to blocklist")
            return

        db.execute("""
            INSERT OR IGNORE INTO user_knowledge (username, concept, timestamp)
            VALUES (?, ?, ?)
        """, (username, concept_clean, datetime.now()))
        logger.info(f"✅ [DB] Saved mastery: '{concept_clean}' for {username}")

    def _validate_against_graph(self, concept: str) -> bool:
        """Check if a concept exists as a node in the Neo4j knowledge graph."""
        try:
            # Lazy import to avoid circular dependency at module load time
            from app.db.graph_db import Neo4jGraphDB
            
            # We need to get the active graph_db instance
            # Import it from main where it's initialized
            import app.main as main_module
            graph_db = getattr(main_module, 'graph_db', None)
            
            if not graph_db:
                # If graph DB is not available, fall through (permissive)
                logger.debug(f"⚠️ Graph DB not available, allowing '{concept}'")
                return True
            
            cypher = """
                MATCH (n) 
                WHERE toLower(n.name) CONTAINS toLower($name) 
                   OR toLower($name) CONTAINS toLower(n.name)
                RETURN n.name LIMIT 1
            """
            results = graph_db.execute_query(cypher, {"name": concept})
            return len(results) > 0
            
        except Exception as e:
            logger.error(f"Graph validation error for '{concept}': {e}")
            return True  # Permissive on error — don't block valid concepts

    def has_mastered(self, username: str, concept: str) -> bool:
        """Checks if a concept is known (Fuzzy match, garbage-aware)."""
        known = self.get_known_concepts(username)  # Already filtered
        concept_lower = concept.lower()
        
        # Skip garbage concepts even if they're somehow in the DB
        if concept_lower in self._garbage_concepts:
            return False
        
        return any(concept_lower in k.lower() or k.lower() in concept_lower for k in known)
    
    def set_goal(self, username: str, goal: str):
        """Upsert user goal."""
        exists = db.fetch_one("SELECT 1 FROM user_goals WHERE username = ?", (username,))
        if exists:
            db.execute("UPDATE user_goals SET goal_text = ?, updated_at = ? WHERE username = ?", (goal, datetime.now(), username))
        else:
            db.execute("INSERT INTO user_goals (username, goal_text, updated_at) VALUES (?, ?, ?)", (username, goal, datetime.now()))

    def get_goal(self, username: str) -> str:
        row = db.fetch_one("SELECT goal_text FROM user_goals WHERE username = ?", (username,))
        return row['goal_text'] if row else None

knowledge_manager = UserKnowledgeManager()
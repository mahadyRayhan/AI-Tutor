# backend/app/core/user_knowledge_manager.py

import json
import logging
from datetime import datetime
from typing import List, Dict
from app.db.sqlite_db import db

logger = logging.getLogger(__name__)

# Misconception EMA parameters (see SYSTEM_OVERVIEW §3.4.4)
# M_c(t+1) = LAMBDA_M * M_c(t) + (1-LAMBDA_M) * 1[conf >= theta_conf AND incorrect]
LAMBDA_M = 0.7    # persistence: high-confidence errors decay slowly
THETA_M  = 0.25   # flag threshold: m_score >= THETA_M → active misconception


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
            INSERT OR IGNORE INTO user_knowledge (username, concept, timestamp, ever_certified)
            VALUES (?, ?, ?, 1)
        """, (username, concept_clean, datetime.now()))
        # Also set ever_certified on existing rows (concept already in DB but not yet set)
        db.execute(
            "UPDATE user_knowledge SET ever_certified=1 WHERE username=? AND concept=?",
            (username, concept_clean)
        )
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
        """
        Checks if a concept has ever been certified (fuzzy match, garbage-aware).

        Fix #5: uses ever_certified=1 flag rather than row existence so that
        forgetting-decay cannot re-lock earned prerequisites. A concept that was
        once mastered stays 'known' to the prerequisite gate regardless of current
        decayed P̃ values; only the review scheduler uses the live BKT posterior.
        """
        concept_lower = concept.lower()
        if concept_lower in self._garbage_concepts:
            return False

        rows = db.fetch_all(
            "SELECT concept FROM user_knowledge WHERE username=? AND ever_certified=1",
            (username,)
        )
        return any(
            concept_lower in r['concept'].lower() or r['concept'].lower() in concept_lower
            for r in rows
        )
    
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

    # --- SM-2 SPACED REPETITION ---

    def update_sm2(self, username: str, concept: str, quality: int):
        """
        Applies SM-2 algorithm after a quiz attempt.

        quality 0-5:
          5 = perfect recall (high confidence + correct)
          4 = correct with hesitation
          3 = correct but difficult (PARTIAL)
          2 = wrong but close
          1 = wrong, low confidence
          0 = misconception (high confidence + wrong)

        Intervals: first=1d, second=6d, then *= ease_factor.
        ease_factor floor is 1.3 to prevent interval collapse.
        """
        from datetime import timedelta
        row = db.fetch_one(
            "SELECT interval_days, ease_factor, review_count FROM user_knowledge WHERE username=? AND concept=?",
            (username, concept)
        )
        if not row:
            return

        interval = row["interval_days"] or 1
        ease = row["ease_factor"] or 2.5
        count = row["review_count"] or 0

        if quality < 3:
            # Failed — reset to beginning
            interval = 1
            count = 0
        else:
            if count == 0:
                interval = 1
            elif count == 1:
                interval = 6
            else:
                interval = max(1, round(interval * ease))
            ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
            count += 1

        due = datetime.now() + timedelta(days=interval)
        db.execute(
            "UPDATE user_knowledge SET interval_days=?, ease_factor=?, due_date=?, review_count=? WHERE username=? AND concept=?",
            (interval, ease, due, count, username, concept)
        )
        logger.info(f"📅 [SM-2] '{concept}' for {username}: next review in {interval}d (ease={ease:.2f})")

    def get_due_for_review(self, username: str) -> List[str]:
        """Returns concepts whose spaced-repetition review date has arrived."""
        rows = db.fetch_all(
            "SELECT concept FROM user_knowledge WHERE username=? AND due_date IS NOT NULL AND due_date <= ?",
            (username, datetime.now())
        )
        return [r["concept"] for r in rows]

    # --- MISCONCEPTION TRACKING ---

    def store_misconception(self, username: str, concept: str, student_answer: str, correct_answer: str):
        """Records a high-confidence error and updates the EMA misconception score.

        M_c(t+1) = LAMBDA_M * M_c(t) + (1-LAMBDA_M) * 1  (incorrect observation)
        Misconception is flagged when m_score >= THETA_M.
        """
        row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        profile = json.loads(row['learning_profile'] or '{}') if row and row['learning_profile'] else {}

        misconceptions = profile.get("misconceptions", [])
        existing = next((m for m in misconceptions if m.get("concept", "").lower() == concept.lower()), None)

        if existing:
            prev_score = existing.get("m_score", 0.0)
            new_score = round(LAMBDA_M * prev_score + (1 - LAMBDA_M) * 1.0, 4)
            existing["m_score"] = new_score
            existing["resolved"] = new_score < THETA_M
            existing["student_answer"] = student_answer[:200]
            existing["correct_answer"] = correct_answer[:200] if correct_answer else ""
            existing["detected_at"] = datetime.now().isoformat()
        else:
            new_score = round((1 - LAMBDA_M) * 1.0, 4)  # = 0.3 on first error
            misconceptions.append({
                "concept": concept,
                "m_score": new_score,
                "resolved": new_score < THETA_M,
                "student_answer": student_answer[:200],
                "correct_answer": correct_answer[:200] if correct_answer else "",
                "detected_at": datetime.now().isoformat(),
            })

        profile["misconceptions"] = misconceptions
        db.execute("UPDATE users SET learning_profile = ? WHERE username = ?",
                   (json.dumps(profile), username))
        logger.info(f"🔴 [Misconception] EMA updated for '{concept}' — {username} (m_score={new_score:.3f})")

    def resolve_misconception(self, username: str, concept: str):
        """Decays the EMA misconception score toward zero on a correct answer.

        M_c(t+1) = LAMBDA_M * M_c(t)   (no +1 increment on correct response)
        Misconception auto-resolves when m_score drops below THETA_M.
        Requires approximately 2 consecutive correct answers to resolve from m_score=0.30.
        """
        row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        if not row:
            return
        profile = json.loads(row['learning_profile'] or '{}') if row['learning_profile'] else {}
        misconceptions = profile.get("misconceptions", [])
        updated = False
        for m in misconceptions:
            if m.get("concept", "").lower() == concept.lower() and not m.get("resolved", True):
                decayed = round(LAMBDA_M * m.get("m_score", 0.0), 4)
                m["m_score"] = decayed
                m["resolved"] = decayed < THETA_M
                updated = True
                if m["resolved"]:
                    logger.info(f"✅ [Misconception] Resolved for '{concept}' — {username} (m_score={decayed:.3f})")
                else:
                    logger.info(f"🟡 [Misconception] Decaying for '{concept}' — {username} (m_score={decayed:.3f})")
        if updated:
            profile["misconceptions"] = misconceptions
            db.execute("UPDATE users SET learning_profile = ? WHERE username = ?",
                       (json.dumps(profile), username))

    def get_misconceptions(self, username: str, unresolved_only: bool = True) -> List[Dict]:
        """Returns misconception entries. Unresolved means m_score >= THETA_M."""
        row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        if not row:
            return []
        profile = json.loads(row['learning_profile'] or '{}') if row['learning_profile'] else {}
        all_m = profile.get("misconceptions", [])
        if unresolved_only:
            return [m for m in all_m if m.get("m_score", 0.0) >= THETA_M]
        return all_m

knowledge_manager = UserKnowledgeManager()
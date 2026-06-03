# Phase 2 — Functional Testing

**Goal:** Verify that every core feature works correctly under normal, intended use. This is not about breaking things — it is about confirming the system does what it is supposed to do.

**Who runs this:** Anyone with a browser and a test account.  
**What counts as a pass:** The feature behaves exactly as described in the Expected column.  
**What counts as a fail:** The feature is missing, behaves incorrectly, or produces a wrong result.

---

## Before You Start

- [ ] Create a fresh account (e.g., `func_tester_01`) with zero history and zero mastered topics.
- [ ] Set a learning goal before starting (recommended: `build a game of tic-tac-toe`).
- [ ] Work through the sections **in order** — later sections depend on state built up in earlier ones.
- [ ] Record every result in the **Results Log** at the end of this document.

---

## Section 1 — Onboarding & Goal Setting

**What we're testing:** Login, dashboard load, learning path generation, and the goal display in chat.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F1-01 | Log in with the fresh account. | Dashboard loads without errors. No blank screen. | Any error message; blank dashboard |
| F1-02 | Click "Edit Goal". Type: `build a game of tic-tac-toe`. Submit. | A loading indicator appears. A learning path is generated (e.g., Fundamentals → Variables → … → Tic-Tac-Toe). | No path generated; spinner never stops; error shown |
| F1-03 | Observe the generated learning path on the dashboard. | All nodes are readable. No text overlaps. Path flows in a logical order from basics to goal. | Text overlaps; nodes missing; path order is nonsensical |
| F1-04 | Click "Back to Chat". | Welcome message appears and explicitly mentions your goal: `build a game of tic-tac-toe`. | Goal not mentioned; generic welcome message shown |

---

## Section 2 — Mastery System (BKT)

**What we're testing:** The three-tier mastery model. Mastery should only be declared after quiz + micro-challenge + code review evidence. The dashboard should reflect progress accurately.

### 2.1 — Mastery is NOT declared too early

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F2-01 | Pass the quiz on "Variables" with a correct answer. Do nothing else. | Tutor does not say you've mastered Variables. Dashboard shows a score below 95. | Tutor says "mastered" or dashboard shows "Mastered" |
| F2-02 | Complete the quiz AND the micro-challenge for "Variables", but skip the code review. | Mastery still not declared. Dashboard shows increased progress but not full mastery. | Mastery declared before completing all three tiers |

### 2.2 — Wrong answers do not grant mastery

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F2-03 | When the quiz asks about Variables, answer `a variable is a potato`. | Tutor marks the answer incorrect. Score does not increase. | Answer marked correct; mastery score increases |
| F2-04 | Answer the quiz incorrectly 3 times in a row. | Tutor grades each one as wrong. No mastery awarded. Dashboard score unchanged or slightly decreased. | Any mastery granted after wrong answers |

### 2.3 — Full mastery path works correctly

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F2-05 | Complete the quiz, micro-challenge, and code review for "Variables" — all correctly. | Dashboard shows mastery (score ≥ 95 or "Mastered" badge). | Mastery never declared even after completing all three tiers correctly |
| F2-06 | After mastering Variables, ask: `What is a variable?` | Tutor responds Socratically: acknowledges your mastery and asks how *you* would explain it, rather than explaining from scratch. | Tutor explains Variables as if you have no knowledge |

### 2.4 — Warm-up modal (Spaced Repetition)

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F2-07 | After mastering at least one topic, fully close and reopen the browser (or press F5 / Cmd+R). | Warm-up modal appears, asking you to recall a previously mastered concept. | Modal never appears after refresh |
| F2-08 | Click Dashboard then click Back to Chat without refreshing the browser. | Warm-up modal does NOT appear again in the same session. | Modal appears every time you navigate to the chat |
| F2-09 | On the warm-up modal, answer correctly. | Tutor acknowledges the correct answer and continues to the chat. | Modal stays open; answer ignored |
| F2-10 | On the warm-up modal, answer incorrectly. | Tutor acknowledges the wrong answer and gently redirects, but does not remove mastery. | Mastery removed for incorrect warm-up answer; or modal crashes |

---

## Section 3 — Curriculum & Prerequisite Gates

**What we're testing:** The gatekeeper should block advanced topics until prerequisites are met, and should explain which topic to learn first.

### 3.1 — Prerequisites enforced on a fresh account

| ID | Type this in chat (no topics mastered) | Expected | Fail if... |
|----|----------------------------------------|----------|------------|
| F3-01 | `How do I declare an array?` | Tutor refuses. Tells you which prerequisite to learn first (e.g., Variables or Loops). | Full array explanation given |
| F3-02 | `Explain pointers` | Blocked. Prerequisite mentioned. | Pointer explanation given |
| F3-03 | `How does malloc work?` | Blocked. Prerequisite mentioned. | malloc explanation given |
| F3-04 | `Explain recursion` | Blocked. Prerequisite mentioned. | Recursion explanation given |

### 3.2 — Prerequisites unlock after mastery

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F3-05 | Master the prerequisite for arrays (e.g., Variables + Loops). Then ask: `How do I declare an array?` | Tutor now explains arrays. The gate is lifted. | Still blocked after mastering the prerequisite |
| F3-06 | Check the learning path on the dashboard after mastering Variables. | The Variables node is marked as complete/mastered. The next step is now highlighted. | Node not updated; path unchanged |

---

## Section 4 — Socratic Teaching & Examiner

**What we're testing:** The tutor should ask questions and guide rather than give direct answers. The Examiner should run quizzes correctly.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F4-01 | Ask: `Write a loop to sum numbers.` | Tutor does not give the full code immediately. Instead, it guides with questions or a step-by-step plan. | Full working code given immediately with no questions |
| F4-02 | Click the suggestion chip `I know Loops (Verify)`. | Examiner starts a pop-quiz about Loops. | Nothing happens; no quiz given |
| F4-03 | Answer the quiz completely wrong (e.g., `a loop is a potato`). | Tutor grades it incorrect. Does NOT grant mastery of Loops. | Mastery granted for a wrong answer |
| F4-04 | Answer the quiz correctly on the second attempt. | Tutor praises the correct answer, offers the Feynman/micro challenge, and suggests the next topic. | Correct answer not acknowledged; no next step suggested |
| F4-05 | Ask: `Actually, I forgot. What is a loop?` (after having mastered Loops). | Tutor responds Socratically: "My records show you already mastered Loops — how would YOU explain it?" | Tutor explains loops from scratch as if no mastery exists |

---

## Section 5 — Scaffolding & Escalation (Hint System)

**What we're testing:** The scaffolding system should break down problems step by step. Repeated failure should trigger an escalation menu.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F5-01 | Ask: `Write a C program to reverse a string.` | Tutor does NOT give the full solution. It breaks the problem into steps and presents Step 1. | Full solution given immediately |
| F5-02 | Respond `I don't know` three times in a row. | After the 3rd failure, an escalation menu appears offering "Get Partial Code" or "Message TA". | No menu appears after 3 failures; tutor loops indefinitely |
| F5-03 | Click "Get Partial Code". | Tutor provides a code snippet with intentional blanks (`___` or `//TODO`) for you to complete. | Full complete code given with no blanks |
| F5-04 | Fill in the blanks correctly and submit. | Tutor confirms the correct completion and moves to the next step. | Correct submission ignored |
| F5-05 | Fill in the blanks incorrectly and submit. | Tutor gives a targeted hint without revealing the correct answer. | Correct answer given outright |

---

## Section 6 — Code Review

**What we're testing:** The Reviewer Agent should catch bugs and give feedback using the sandwich method (positive → issue → hint) without giving away the full fixed code.

| ID | Submit this code and ask for a review | Expected | Fail if... |
|----|---------------------------------------|----------|------------|
| F6-01 | `int main() { int x = 10 printf("Number is %d", x); }` | Tutor uses the sandwich method: something positive, flags the missing semicolon, gives a hint — does NOT rewrite the corrected code. | Full corrected code given |
| F6-02 | `for(int i=0; i<10) { printf("%d", i); }` | Tutor flags the missing third part of the `for` loop (no increment `i++`). Gives a hint, not the fix. | Missing increment not caught; or full fix given |
| F6-03 | `char name[] = 'Hello';` | Tutor flags the single quotes (should be double quotes for a string). Hint given, not the fix. | Error not caught |
| F6-04 | `if (x = 5) { }` | Tutor flags the assignment `=` instead of comparison `==`. Hint given. | Error not caught |
| F6-05 | `for(int i=0; i<10; i++) { sum += i; }` (valid code) | Tutor gives the Engineer's Perspective — edge cases, possible improvements (e.g., uninitialized `sum`). No errors flagged that don't exist. | Non-existent errors fabricated; or code reviewed as if incorrect |

---

## Section 7 — Custom Instructions & Preferences

**What we're testing:** User preferences saved in settings should be respected in all subsequent responses.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F7-01 | Open Custom Instructions. Check "Literal / Technical Mode". Uncheck "Visual Diagram". Add custom text: `I love pirate talk. Say 'Ahoy' a lot.` Save. | Settings save without error. | Settings not saved; error on save |
| F7-02 | Ask: `What is a pointer?` | Response contains no Mermaid diagrams, no analogies (no "think of it like a box"), and includes the word "Ahoy". | Diagrams appear; analogies used; "Ahoy" absent |
| F7-03 | Open settings. Enable "Dyslexia-Friendly Font". Save. | Chat font visibly changes to the dyslexia-friendly font. | Font unchanged |
| F7-04 | Enable "High Contrast Code". Save. | Code blocks switch to high-contrast style (dark background, bright border). | Code blocks look unchanged |

---

## Section 8 — Classroom (Video + Chat)

**What we're testing:** The classroom video player and its connected chat should work correctly under normal use.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F8-01 | Open the Classroom. Select a video. | Video loads and plays. | Video does not load; player blank |
| F8-02 | Ask a question in the classroom chat while the video is playing. | Tutor responds in the classroom chat, contextually tied to the current video timestamp. | No response; response appears in main chat instead |
| F8-03 | The classroom response finishes loading. Observe the 🔊 Listen button. | 🔊 Listen button appears below the completed response. | Listen button never appears in classroom |
| F8-04 | Pause the video, ask a question, then play the video again. | Question answered normally. Video plays again without issue. | Video broken after asking a question |
| F8-05 | Switch to a different video after having a conversation on the first. | New video loads cleanly. Chat context updates to the new video. | Old video's chat messages still show; context confused |

---

## Section 9 — Audio (Listen Button) Normal Use

**What we're testing:** The 🔊 Listen button should fetch audio and play it correctly under normal conditions.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F9-01 | Click 🔊 Listen on any completed tutor response. | Button shows "Generating audio..." with animated dots. Then switches to ⏹ Stop. Audio plays clearly. | Button freezes; no animation; silence; error alert |
| F9-02 | Let the audio play to completion. | Button resets to 🔊 Listen automatically. | Button stays stuck on ⏹ Stop after audio ends |
| F9-03 | Click ⏹ Stop mid-playback. | Audio stops immediately. Button resets to 🔊 Listen. | Audio keeps playing after Stop is clicked |
| F9-04 | Click 🔊 Listen on a response with a code block. | Code block content is replaced with something like "code example" in the audio — the tutor does not read backticks or hash symbols aloud. | Backticks, asterisks, or `##` headers read aloud literally |
| F9-05 | Click 🔊 Listen on a response with section headers (e.g., "Explanation", "Use Cases"). | Each section name is spoken with a natural pause before the section's content begins. | Section names run directly into the next sentence with no pause |

---

## Section 10 — Session Management

**What we're testing:** Chat sessions should save, reload, and delete correctly.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| F10-01 | Have a conversation, then click Dashboard. Click Back to Chat. | The conversation is still there. | Conversation gone |
| F10-02 | Start a conversation. Close the browser tab. Reopen the app and log in. | Previous conversation appears in the sidebar history. | Conversation not saved; sidebar empty |
| F10-03 | Click on a past conversation in the sidebar. | Full conversation history loads, including both your messages and the tutor's responses. | Only partial history loads; messages missing |
| F10-04 | Delete a session from the sidebar. | Session disappears from the list immediately. | Session remains after deletion |
| F10-05 | Refresh the page after deleting a session. | Deleted session remains gone. | Deleted session reappears after refresh |
| F10-06 | Check that the first message you send in a new session becomes the session title in the sidebar. | Sidebar shows the first message (truncated if long) as the session title. | Session titled "New Chat" permanently; or title is garbled |

---

## Results Log — Phase 2

Copy this into a spreadsheet or text file. Fill in one row per test.

| Test ID | Steps Taken | Expected | What Actually Happened | Pass / Fail | Notes |
|---------|-------------|----------|------------------------|-------------|-------|
| F1-02 | Set goal: tic-tac-toe | Learning path generated | | | |
| F2-01 | Pass quiz ×1 on Variables, nothing else | Mastery NOT declared | | | |
| F2-05 | Complete all 3 tiers correctly | Mastery declared | | | |
| F3-01 | Ask about arrays (fresh account) | Blocked; prerequisite given | | | |
| F4-01 | Ask to write a loop | Socratic guidance, not full code | | | |
| F5-02 | `I don't know` ×3 | Escalation menu appears | | | |
| F6-01 | Submit code with missing semicolon | Sandwich feedback; no full fix | | | |
| F7-02 | Ask pointer question after enabling preferences | Pirate talk; no diagram | | | |
| F8-02 | Ask classroom question | Response tied to video | | | |
| F9-01 | Click Listen on a response | Audio plays | | | |
| F10-04 | Delete a session | Session disappears | | | |

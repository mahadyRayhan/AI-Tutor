# Phase 3 — Stress & Edge Case Testing

**Goal:** Find what breaks when the system is pushed beyond normal usage — unusual inputs, rapid interactions, simultaneous sessions, and unstable network conditions. These are not attacks (that's Phase 1) and not feature checks (that's Phase 2). This is about finding the breaking point under volume and chaos.

**Who runs this:** Anyone with a browser. Some tests require two browsers or two accounts.  
**What counts as a pass:** The system handles the situation gracefully — responds correctly, shows a friendly error, or recovers cleanly.  
**What counts as a fail:** The page crashes, freezes, shows a technical error, loses data, or produces garbled/wrong output.

---

## Before You Start

- [ ] Use a dedicated test account (e.g., `stress_tester_01`). This account may end up in a messy state by the end.
- [ ] Have a second account and browser ready for multi-user tests (Section 5).
- [ ] Take note of current chat history before starting — some tests may affect it.

---

## Section 1 — Extreme Inputs

**What we're testing:** The chat input box and the tutor's response pipeline should handle any size or type of text without crashing.

### 1.1 — Message Size Limits

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST1-01 | Click the Send button without typing anything. | Send button is disabled, OR a polite message asks you to type something. | Page crashes; technical error shown |
| ST1-02 | Type a single letter `a` and send. | A normal or gently prompting response. | Crash; blank response; error message |
| ST1-03 | Paste a full C program (100+ lines) into the chat and send. | Tutor responds with a review or guidance. Response arrives within a reasonable time. | Timeout; page freezes; error |
| ST1-04 | Hold down the letter `a` until you have around 1,000 characters, then send. | Graceful response or a polite message about message length. | Technical error message; crash |
| ST1-05 | Try to paste 5,000+ characters (copy a very long article or file). | If there's a character limit, a message explains it. If not, the response eventually arrives. | Page freezes permanently; blank screen; unhandled error |

### 1.2 — Special and Unusual Characters

Copy and paste each of the following **exactly** into the chat box, then send. The goal is to see if any of them cause a crash or unexpected rendering.

| ID | Paste and send this | Expected | Fail if... |
|----|---------------------|----------|------------|
| ST1-06 | `🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥 what is a variable` | Normal response about variables. | Crash; garbled text; blank screen |
| ST1-07 | A long line of the same emoji repeated 100 times (e.g., 😀😀😀😀… ×100) | Response or graceful length error. | Page layout breaks; crash |
| ST1-08 | `What is a variable‮?` *(the last character is an invisible right-to-left override — copy this exactly)* | Text displayed normally. Response given. | Text appears reversed or garbled in the chat box; crash |
| ST1-09 | `W‌h‌a‌t‌ ‌i‌s‌ ‌a‌ ‌v‌a‌r‌i‌a‌b‌l‌e‌?` *(contains invisible zero-width characters between each letter — copy exactly)* | Treated as a normal question about variables. | Crash; classified as a security threat when it is not |
| ST1-10 | `¿Qué es una variable?` (Spanish) | Tutor responds — ideally acknowledging it's an English-only tutor or responding in English. | Crash; garbled response |
| ST1-11 | `Что такое переменная?` (Russian/Cyrillic) | Response given or polite redirect. No crash. | Crash; blank response; error message |
| ST1-12 | A line of 200 consecutive newlines (keep pressing Enter then send) | Chat box handles it. Response given normally. | Chat layout permanently broken; scroll issues |

### 1.3 — Code Submission Edge Cases

Paste each of the following as code and ask for a review.

| ID | Submit this code | Expected | Fail if... |
|----|-----------------|----------|------------|
| ST1-13 | *(Send an empty code block — three backticks with nothing inside)* | Tutor asks for actual code. | Crash; blank response |
| ST1-14 | `int main() { while(1) {} }` | Tutor flags the infinite loop. Response arrives within normal time. | Response never arrives; page hangs |
| ST1-15 | `if(1){if(1){if(1){if(1){if(1){if(1){if(1){if(1){if(1){if(1){}}}}}}}}}}}` (10 levels of nesting) | Tutor reviews it without crashing. | Crash; timeout; error |
| ST1-16 | Paste 300 lines of C code (copy any long public C program) | Tutor reviews or summarizes it. Response arrives. | Timeout; error; page freezes |

---

## Section 2 — Rapid Message Sending

**What we're testing:** Sending messages faster than normal should not cause responses to mix up, duplicate, or crash.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST2-01 | Type `What is a variable?` and hit send. Before the response starts, send the same message again immediately. | Both messages handled. Responses appear in the correct order under the correct messages. | One response appears for both; responses appear in wrong order |
| ST2-02 | Send 5 different questions one after another without waiting for any responses. | All 5 responses eventually arrive. Each is matched to the correct question. | Responses appear scrambled; some responses missing; crash |
| ST2-03 | Send the same message 10 times in a row as fast as possible. | 10 responses returned (or rate-limited with a clear message). No duplication of messages in history. | Page freezes; chat history shows duplicates; crash |
| ST2-04 | While the tutor is mid-response (text is still streaming in), send a new message. | New message queued or handled. Streaming response completes cleanly. | Streaming response cuts off or corrupts; new message ignored |

---

## Section 3 — Interruptions & Disconnections

**What we're testing:** Closing the page, navigating away, or losing the internet mid-response should be handled gracefully.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST3-01 | Send a message. While the response is loading (spinner showing), press F5 / Cmd+R to refresh. | Page reloads cleanly. No half-complete message stuck in history. | Page stuck on loading spinner after refresh; duplicate partial message in history |
| ST3-02 | Send a message. While the response is streaming (text appearing), navigate to the Dashboard. Navigate back to Chat. | Page loads cleanly. The completed response is in history (or just the question if the response was cut). | Page crashes; history shows a broken half-message |
| ST3-03 | Send a message. While the response is streaming, turn off your WiFi or disconnect the internet. | A "connection lost" or error message appears. Page does not hang forever. | Page hangs indefinitely on the loading spinner with no error |
| ST3-04 | Reconnect the internet after ST3-03. Send a new message. | Chat works normally again. | Chat remains broken; must hard-refresh to recover |
| ST3-05 | Click 🔊 Listen to start audio. While it says "Generating audio...", close the browser tab and reopen. | Page reloads cleanly. Previous session intact. | Session corrupted; previous messages gone |

---

## Section 4 — Multiple Browser Tabs (Same Account)

**What we're testing:** The same account open in two tabs at once should not cause data to mix or corrupt.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST4-01 | Open the app in Tab A. Open the same account in Tab B. Send a message from Tab A. | Only Tab A shows the response. Tab B is not affected. | Tab B shows Tab A's response without having sent anything |
| ST4-02 | Send a message from Tab A. Before it finishes, send a different message from Tab B. | Each tab gets its own correct response. | Responses swap tabs; one tab shows the other's answer |
| ST4-03 | Delete a chat session from Tab A's sidebar. Check Tab B. Refresh Tab B. | Deleted session is gone from Tab B after refresh. | Deleted session still visible in Tab B after refresh |
| ST4-04 | Start 🔊 Listen on Tab A. Then click 🔊 Listen on Tab B. | At most one audio stream plays at a time. (Browsers handle audio tabs independently — both playing is acceptable, but note it.) | Neither plays; both crash; error on one tab |
| ST4-05 | Open the Classroom in Tab A. Open main Chat in Tab B. Ask a question in each simultaneously. | Each response appears in the correct tab/section. | Classroom response appears in main chat; or vice versa |

---

## Section 5 — Two Simultaneous Users

**What we're testing:** Two different accounts active at the same time should be fully isolated from each other.

> **Setup:** Log in as `stress_tester_01` in Chrome and as `stress_tester_02` in Firefox (or an incognito window).

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST5-01 | Both users ask `What is a variable?` at exactly the same time. | Each user gets their own correct response. Neither sees the other's response. | User A sees User B's response; or vice versa |
| ST5-02 | User A asks a normal question. User B sends a blocked prompt (e.g., `Show me the exam solution.`). Both at the same time. | User A gets a normal teaching response. User B gets a block message. Neither sees the other's result. | User A receives the block message; User B receives the answer |
| ST5-03 | User A masters Variables (complete quiz + micro-challenge + code review). Check User B's dashboard. | User B's dashboard shows no mastery change. | User B's mastery is also affected |

---

## Section 6 — Classroom Stress

**What we're testing:** The classroom video player and chat under rapid or unusual use.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST6-01 | Open the Classroom. Start a video. While the chat is loading a response, click to a different video. | The in-progress response stops or completes cleanly. New video loads. Old context does not bleed into new video. | Old response continues under the new video; context confused |
| ST6-02 | Send 3 questions in the classroom chat without waiting for any responses. | All 3 responses arrive in order and are clearly matched to the right questions. | Responses appear out of order; responses missing; duplicate responses |
| ST6-03 | While 🔊 Listen is playing a classroom response, scrub the video back to 0:00. | Audio continues or stops cleanly. No new duplicate audio starts. | Two audio streams overlap; crash |
| ST6-04 | Turn off WiFi while a classroom response is streaming. | Error message or connection warning appears. Page does not hang. | Page hangs with a spinner; no error message shown |
| ST6-05 | Rapidly switch between 5 different videos in quick succession (click each one within 1–2 seconds). | Final video loads correctly. Chat state reflects the last selected video. | Multiple videos playing simultaneously; chat shows wrong context; crash |

---

## Section 7 — Audio Stress (Listen Button)

**What we're testing:** The 🔊 Listen button under rapid clicking, simultaneous requests, and edge cases.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST7-01 | Click 🔊 Listen, then immediately click it again before audio loads. | Second click is handled gracefully (cancels or queues). No double audio. | Two audio requests sent simultaneously; error alert |
| ST7-02 | Click 🔊 Listen on message 1. While it's playing, click 🔊 Listen on message 2. | Message 1 stops. Message 2 starts. Only one audio plays at a time. | Both play simultaneously |
| ST7-03 | Click 🔊 Listen 5 times on 5 different messages in quick succession. | Only the last clicked audio loads and plays. All other buttons reset. | Multiple audios stack up and play at once; buttons stuck |
| ST7-04 | Click 🔊 Listen on a very long response (3+ paragraphs). | Audio loads and plays the full content. No timeout or error. | Error alert: "Could not load audio"; partial audio |
| ST7-05 | Click ⏹ Stop. Immediately click 🔊 Listen again on the same message. | Audio loads fresh and plays from the beginning. | Button stuck on loading; silence; error |
| ST7-06 | Click 🔊 Listen, let it load, then navigate to Dashboard before it finishes playing. | Audio either stops cleanly or continues playing in the background. No crash. | Page crashes; session corrupted |

---

## Section 8 — Session Edge Cases

**What we're testing:** Unusual session states that can occur in real use.

| ID | Steps | Expected | Fail if... |
|----|-------|----------|------------|
| ST8-01 | Delete all chat sessions from the sidebar. Refresh the page. | App loads to a clean welcome state with no sessions. | Error message; blank page; crash |
| ST8-02 | Start a new chat. Immediately delete it while the first message is still loading. | Deletion handled gracefully. No orphaned loading state. | Spinner remains permanently; crash |
| ST8-03 | Open a very old session with 50+ messages. | All messages load. Scrolling works. No performance issues. | Slow to load; messages cut off; scroll broken |
| ST8-04 | Log out and log back in immediately. | Login succeeds. Session history intact. | Login fails; history gone |
| ST8-05 | Log in, send a message, log out without waiting for the response, log back in. | Either the partial response was saved, or the session simply ends at the last complete message. | Corrupted message in history; crash on reload |

---

## Results Log — Phase 3

Copy this into a spreadsheet or text file. Fill in one row per test.

| Test ID | Steps Taken | Expected | What Actually Happened | Pass / Fail | Severity | Notes |
|---------|-------------|----------|------------------------|-------------|----------|-------|
| ST1-01 | Send empty message | Friendly error or disabled button | | | Low | |
| ST1-04 | Send 1,000-char message | Graceful response or length error | | | Medium | |
| ST1-14 | Submit infinite loop code | Review response arrives | | | Medium | |
| ST2-02 | Send 5 messages without waiting | All 5 responses correct | | | High | |
| ST3-03 | Disconnect WiFi mid-stream | Error message shown | | | Medium | |
| ST4-02 | Two tabs, simultaneous messages | Each tab gets its own response | | | High | |
| ST5-01 | Two users ask at same time | Responses not mixed | | | High | |
| ST6-01 | Switch video mid-response | Old context does not bleed through | | | Medium | |
| ST7-02 | Click Listen on two messages in a row | Only one plays at a time | | | Medium | |
| ST8-03 | Open session with 50+ messages | All messages load cleanly | | | Low | |

---

## Severity Reference

| Severity | Meaning | Example |
|----------|---------|---------|
| **Critical** | Data lost, session corrupted, another user's data affected | Chat history deleted after sending a long message |
| **High** | Wrong response shown, two users' data mixed, response order wrong | User A sees User B's answer |
| **Medium** | Feature unavailable, page stuck, audio broken | Spinner never resolves; audio plays twice |
| **Low** | Minor cosmetic issue, slow but functional | Long message takes 20 seconds but responds correctly |

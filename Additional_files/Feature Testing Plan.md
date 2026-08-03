| Priority | \# | Feature | How to test | Difficulty |
| :---- | :---- | :---- | :---- | :---- |
| 1 | 13 | Intent classification | Send different query types, check logs for intent | Trivial — just chat |
| 2 | 17 | Sentinel (off-topic) | Ask "what's the weather?" 3 times, check strike system | Trivial — just chat |
| 3 | 14 | Socratic withholding | Ask about a concept the user already has BKT data for | Easy — need 1 known concept |
| 4 | 5 | Mastery classification | Ask about a concept, check \[MCRA\] log for level | Easy — check logs |
| 5 | 9 | Dashboard mastery sliders | Click a topic on dashboard, drag sliders, save | Easy — UI click |
| 6 | 6 | Prompt adaptation | Compare responses for same concept across mastery levels | Medium — need users at different levels |
| 7 | 1 | 3-tier BKT updates | Answer a quiz, do a micro-challenge, submit code review — check DB values change | Medium — need all 3 interaction types |
| 8 | 10 | Score blending | Adjust slider, then ask about same concept — check avg\_P\_eff in logs | Medium — slider \+ chat |
| 9 | 15 | Examiner (quiz) | Trigger quiz, answer questions, verify BKT quiz tier updates | Medium — multi-step |
| 10 | 16 | Code reviewer | Paste C code, check code-tier BKT update in DB | Medium — need code snippet |
| 11 | 18 | Profiler | Send frustrated messages, check tone adaptation | Medium — need multiple messages |
| 12 | 3 | Forgetting decay | Need time gap or manual DB timestamp manipulation | Medium — time-dependent |
| 13 | 7 | Gatekeeper bypass | Need a certified user asking about a gated topic | Hard — need full certification first |
| 14 | 8 | Withholding bypass | Need a certified user re-asking a mastered concept | Hard — need full certification first |
| 15 | 4 | Prerequisite gatekeeper | Ask about a topic with unmet prerequisites | Hard — need graph \+ uncertified prereqs |
| 16 | 2 | Conjunctive certification | Get all 3 tiers to 0.95 with n\>=3 each on one concept | Hard — many correct answers needed |
| 17 | 11 | P\_G parameter adaptation | Lower same slider 3+ times consistently, check adapted\_P\_G in DB | Hard — repeated interactions |
| 18 | 12 | Per-user BKT params | After P\_G adapts, answer a quiz — verify different posterior than default | Hardest — end-to-end chain |


you have four result families across six scripts. Two scripts support family 1.

#	Result family	Script(s)	Metrics
1	C-EduBench quality	eval_01, eval_02, eval_03	code density (lower better), security compliance, curriculum compliance, pedagogy score, win rate
2	Multi-turn jailbreak	eval_04_multiturn_jailbreak	ASR, block rate, turn-of-detection, false-block rate
3	Evidence Diversity Problem	eval_05	false certification of lopsided learners
4	Mastery-based adaptation	eval_06	Π-fidelity, level separation, weak-tier ID
One correction to your phrasing: code density is lower-is-better (11.12 vs 16.62 — less spoon-feeding). The higher-is-better ones are pedagogy, security, and curriculum compliance.

Commands
Prerequisites for anything live: server running (uvicorn app.main:app --reload), OPENAI_API_KEY set. --judge phases cost money.

1. C-EduBench quality

# 1a — CIs on the existing conference results. Offline, free, no server.
python IRL_extension_script/eval_01_cedubench_ci.py

# 1b — replay the 50 questions through the CURRENT system, delta vs frozen baselines
python IRL_extension_script/eval_02_sage_delta.py --collect     # server; ~30 min
python IRL_extension_script/eval_02_sage_delta.py --score       # free
python IRL_extension_script/eval_02_sage_delta.py --judge       # $$ pedagogy + curriculum
python IRL_extension_script/eval_02_sage_delta.py --winrate     # $$ optional

# 1c — win rate under the conference protocol + a debiased variant
python IRL_extension_script/eval_03_winrate_conference_protocol.py --both
2. Multi-turn jailbreak

python IRL_extension_script/eval_04_multiturn_jailbreak.py --fresh --judge
Collects then judges in one pass. --report-only regenerates the write-up from stored sessions.

3. Evidence Diversity Problem

python IRL_extension_script/eval_05_evidence_diversity.py
Offline, free, no server. ~2 min.

4. Mastery-based adaptation

python IRL_extension_script/eval_06_mastery_adaptation.py --policy            # offline, free
python IRL_extension_script/eval_06_mastery_adaptation.py --seed              # writes 6 learners
python IRL_extension_script/eval_06_mastery_adaptation.py --collect --fresh   # server; ~20 min
python IRL_extension_script/eval_06_mastery_adaptation.py --score             # free — CHECK THIS
python IRL_extension_script/eval_06_mastery_adaptation.py --judge --fresh     # $$ 160 calls
python IRL_extension_script/eval_06_mastery_adaptation.py --report
Stop at --score and confirm two things before paying for the judge: classifier integrity OK on all six, and manipulation check 0/120.

Three things to watch
eval_04 has two scripts writing to the same path. eval_04_multiturn_jailbreak.py produced all your results; eval_04_multiturn_redteam.py is a superseded draft that writes to the same eval_04_sessions.json. Running the wrong one will silently overwrite. I'd delete or rename the redteam file.

Run order matters for 1b and 4. --score/--judge/--report all read what --collect wrote. Re-collecting without --fresh resumes from cache — which is what silently mixed old and new responses earlier.

Result files can vanish. Three eval_06 files disappeared between runs today, most likely OneDrive. --policy, --score and --report regenerate for free; --collect and --judge do not. Worth committing IRL_extension_results/ to git before the deadline.
# AI-Tutor

AI-Tutor is an advanced, intelligent tutoring system designed to help students master C programming. By leveraging Large Language Models (LLMs) with Retrieval-Augmented Generation (RAG), it provides accurate, context-aware, and personalized guidance.

## Core Concepts

*   **Intelligent Tutoring:** Uses Chain-of-Thought reasoning to guide students through problems rather than just giving answers.
*   **RAG Architecture:** Combines a Vector Store (ChromaDB) for semantic search and a Graph Database (Neo4j) for structured knowledge, ensuring high-quality, hallucination-free responses.
*   **Personalized Learning:** Tracks user interactions, intent, and mastery levels to adapt the learning experience and generate progress reports.

## Key Features

*   **Intelligent Scaffolding:** Utilizes Chain-of-Thought (CoT) reasoning to break down complex problems into manageable steps, guiding students towards solutions rather than providing direct answers.
*   **Student Knowledge Verification:** Continuously tracks interaction history to calculate real-time mastery scores (0-100%) for specific topics, identifying struggle areas and adapting the curriculum.
*   **Student-Teacher Communication Loop:** Enables direct intervention through teacher-assigned challenges and provides instructors with detailed risk assessments and engagement metrics for every student.
*   **Multi-Layer Security:** Implements robust input validation, secure file upload handling (extension/path checks), and role-based access control (RBAC) to protect student data and system integrity.
*   **Interactive Learning Environment:** Features a code-aware chat interface with syntax highlighting, mermaid diagram support, and dynamic feedback options (Simplify, Deep Dive) to enhance engagement.
*   **RAG-Powered Accuracy:** Anchors responses in verified course materials using a dual-retrieval system—Vector Search (ChromaDB) for semantic understanding and a Knowledge Graph (Neo4j) for structured relationships.
*   **Resource Management Pipeline:** Provides automated ingestion scripts for code samples and conceptual documents, ensuring the knowledge base remains current and expansive.
*   **Performance Monitoring:** Includes built-in profiling tools to track agent latency and system performance, ensuring a responsive user experience.


## System Agents

| # | Agent Name                             | File             | Role               | Tools Used            | Purpose of Tools                                                      |
| - | -------------------------------------- | ---------------- | ------------------ | --------------------- | --------------------------------------------------------------------- |
| 1 | 🛡️ **The Sentinel**                   | `sentinel.py`    | The Bouncer        | **FastClassifier**    | Detects `SECURITY_RISK` intents                                       |
|   |                                        |                  |                    | **SettingsManager**   | Checks if a topic (e.g., “Pointers”) is locked by the teacher         |
|   |                                        |                  |                    | **Regex**             | Detects prompt injection patterns like “Ignore previous instructions” |
| 2 | 🏗️ **Scaffolding Agent**              | `scaffolding.py` | The Lab Instructor | **HistoryManager**    | Reads/writes `active_plan` state (persistence)                        |
|   |                                        |                  |                    | **VectorStore**       | Retrieves contextual information about coding problems                |
|   |                                        |                  |                    | **KnowledgeManager**  | Awards XP when a plan is completed                                    |
| 3 | 🧐 **The Examiner**                    | `examiner.py`    | The Quiz Master    | **Neo4jGraphDB**      | Fetches pre-generated Q&A pairs linked to concepts                    |
|   |                                        |                  |                    | **HistoryManager**    | Tracks `awaiting_quiz_answer` state                                   |
|   |                                        |                  |                    | **KnowledgeManager**  | Marks topic as “Mastered” if student passes                           |
| 4 | 📝 **Code Reviewer**                   | `reviewer.py`    | The TA             | **VectorStore**       | Retrieves reference `.c` files for comparison                         |
|   |                                        |                  |                    | **LLMInterface**      | Generates “Sandwich Feedback” (Good → Issue → Hint)                   |
| 5 | 🎓 **Socratic Tutor**                  | `socratic.py`    | The Lecturer       | **VectorStore**       | Retrieves `.md` concept files                                         |
|   |                                        |                  |                    | **Neo4jGraphDB**      | Checks prerequisite knowledge (e.g., arrays before pointers)          |
|   |                                        |                  |                    | **KnowledgeManager**  | Personalizes explanations based on user mastery                       |
| 6 | 📊 **The Analyst** (Background)        | —                | Data Scientist     | **HistoryManager**    | Aggregates chat logs                                                  |
|   |                                        |                  |                    | **Risk Algorithm**    | Calculates “High Risk” based on errors & inactivity                   |
| 7 | 🤖 **Teaching Assistant** (Background) | —                | The Grader         | **AssignmentManager** | Retrieves pending submissions                                         |
|   |                                        |                  |                    | **LLMInterface**      | Auto-grades when teacher selects “Check with AI”                      |

---

# AI-Tutor Deployment Guide

This document outlines the step-by-step process for deploying updates from a local development environment to the AWS production server.

## Prerequisites

*   **SSH Key:** `~/.ssh/CERI-AWS.pem`
*   **Remote Host:** `18.225.209.224`
*   **User:** `ubuntu`
*   **Local Project Path:** `~/OneDrive-UniversityofMissouri/Projects/AI-Tutor`

## 1. Local Preparation

Navigate to your project root and bundle the source code. This command excludes the virtual environment, git history, and the local vector database to keep the file size manageable.

### Create the deployment archive
```bash
zip -r tutor_v2.zip . -x "**/__pycache__/*" "**/venv/*" ".git/*" "database/chroma_db/*"
```

### Upload the archive to the server
```bash
scp -i ~/.ssh/CERI-AWS.pem tutor_v2.zip ubuntu@18.225.209.224:~/
```

## 2. Remote Deployment

### Connect to the AWS instance
```bash
ssh -i ~/.ssh/CERI-AWS.pem ubuntu@18.225.209.224
```

Once connected, execute the following sequence to update the application:

### Step A: Stop Current Services
```bash
cd ~/AI-Tutor
sudo docker-compose down
```

### Step B: Archive Existing Version
Instead of deleting the old version, move it to a backup folder with a timestamp. This allows for immediate rollback if the new version encounters issues.
```bash
cd ~/
mv AI-Tutor AI-Tutor_backup_$(date +%F_%H-%M)
```

### Step C: Deploy New Version
Create a fresh directory and extract the zip:
```bash
mkdir ~/AI-Tutor
unzip -o tutor_v2.zip -d ~/AI-Tutor
```

Ensure necessary directories have write permissions for the Docker containers:
```bash
cd ~/AI-Tutor
sudo chmod -R 777 backend/database/ logs/ resources/
```

### Step D: Build and Launch
Rebuild images and start in detached mode:
```bash
sudo docker-compose up --build -d
```

Monitor logs to ensure the backend starts correctly:
```bash
sudo docker-compose logs -f backend
```

## Troubleshooting & Maintenance

*   **Rollback:** If the deployment fails, stop the containers, delete the failed `AI-Tutor` folder, and rename your latest `AI-Tutor_backup_...` folder back to `AI-Tutor`.
*   **Docker Cleanup:** If the disk space on the server runs low due to multiple builds, run `sudo docker system prune -f` to remove unused data.
*   **Vector DB:** Note that `database/chroma_db/` is excluded from the zip. Ensure the remote database is persisted via Docker volumes or handled separately if schema changes occur.


# backend/app/agents/teacher_agent.py
import logging
from typing import Dict, Any, List
from app.db.llm_interface import LLMInterface
from app.agents.rag_agent import RAGAgent

class TeacherAgent:
    def __init__(self, llm_interface: LLMInterface, rag_agent: RAGAgent, logger: logging.Logger):
        self.llm_interface = llm_interface
        self.rag_agent = rag_agent # It can use the RAG agent to get context
        self.logger = logger

    def _generate_quiz(self, topic: str, num_questions: int) -> str:
        # First, get context about the topic using the RAG agent
        context_response = self.rag_agent.run(f"Provide detailed information about {topic}", user_role='teacher')
        context_str = context_response['answer'] # Use the synthesized answer as context

        prompt = f"""
        You are a helpful assistant for teachers. Based on the following context about '{topic}', create a quiz with {num_questions} multiple-choice questions.
        Provide the question, the options, and the correct answer.

        Context: {context_str}

        Quiz:
        """
        return self.llm_interface.generate_response(prompt)

    def run(self, command: str) -> Dict[str, Any]:
        # This is a simple command parser. Could be improved with an LLM.
        if "create a quiz about" in command.lower():
            topic = command.lower().replace("create a quiz about", "").strip()
            # Default to 3 questions for now
            quiz_content = self._generate_quiz(topic, 3)
            return {"answer": quiz_content, "sources": []}
        else:
            return {"answer": "I'm sorry, I can only create quizzes right now.", "sources": []}
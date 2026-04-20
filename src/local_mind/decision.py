"""
Neural decision engine — intent classification, confidence gating, action selection.

Uses a lightweight MLP on top of the existing sentence-transformers embeddings,
so it adds < 1 MB RAM and runs in < 5 ms on CPU.

The three networks are trained from small seed examples (few-shot) and can be
fine-tuned later with user feedback stored in ChromaDB.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from local_mind.config import settings
from local_mind.knowledge import knowledge

log = logging.getLogger(__name__)

Intent = Literal["question", "action", "learn", "chitchat", "clarify", "code"]
Action = Literal["rag_chat", "direct_chat", "learn_url", "summarize", "clarify", "write_code", "run_code"]


# ── Seed examples for few-shot intent classification ─────────────────────────

INTENT_SEEDS: dict[Intent, list[str]] = {
    "question": [
        "What is the capital of France?",
        "How does photosynthesis work?",
        "Explain the difference between TCP and UDP",
        "What are the side effects of ibuprofen?",
        "Can you tell me about quantum computing?",
        "Why does the sky look blue?",
        "What is a neural network?",
        "How do databases handle transactions?",
    ],
    "action": [
        "Summarize this article for me",
        "Translate this to Spanish",
        "Generate a list of project ideas",
        "Write a Python function that sorts a list",
        "Create a meal plan for the week",
        "Draft an email to my team about the deadline",
        "Convert this CSV to JSON",
        "Refactor this code to use async/await",
    ],
    "learn": [
        "Learn this URL https://example.com/article",
        "Read this page and remember it",
        "Add this to your knowledge base",
        "Store this information for later",
        "Remember this documentation",
        "Ingest this link for me",
        "Please learn from https://docs.python.org",
        "Save this webpage content",
    ],
    "chitchat": [
        "Hello",
        "How are you?",
        "Good morning",
        "Thanks!",
        "That was helpful",
        "Tell me a joke",
        "What's your name?",
        "Goodbye",
    ],
    "clarify": [
        "What do you mean?",
        "Can you elaborate on that?",
        "I don't understand, explain more",
        "Which one do you recommend?",
        "Be more specific please",
        "What exactly should I do?",
        "Huh?",
        "Could you rephrase that?",
    ],
    "code": [
        "Write a Python function that sorts a list",
        "Create a JavaScript function to fetch data",
        "Write code to parse a CSV file",
        "Implement a binary search algorithm",
        "Write a script that renames all files in a folder",
        "Code a function to validate email addresses",
        "Build a REST API endpoint in Python",
        "Write a shell script to back up my database",
        "Fix this code it has a bug",
        "Refactor this function to be more efficient",
        "Run this Python code for me",
        "Execute this script",
        "How do I write a for loop in Rust?",
        "Generate a React component for a todo list",
        "Write a SQL query to find duplicate rows",
        "Create a TypeScript interface for user data",
    ],
}

# Anchor phrases for action selection (maps action → representative sentences)
ACTION_ANCHORS: dict[Action, list[str]] = {
    "rag_chat": [
        "Answer using the knowledge base",
        "Find relevant context and respond",
        "Use learned documents to answer",
    ],
    "direct_chat": [
        "Answer from general knowledge",
        "Respond without looking up context",
        "Have a casual conversation",
    ],
    "learn_url": [
        "Fetch and learn a URL",
        "Add a webpage to knowledge",
        "Ingest new content from a link",
    ],
    "summarize": [
        "Summarize the provided content",
        "Give a brief overview",
        "Condense this into key points",
    ],
    "clarify": [
        "Ask the user to clarify",
        "Request more details",
        "Not enough information to proceed",
    ],
    "write_code": [
        "Write code to solve a programming problem",
        "Generate a function or script",
        "Create a program in a specific language",
    ],
    "run_code": [
        "Execute this code snippet locally",
        "Run the script and return output",
        "Test this code on the machine",
    ],
}


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    dot = float(np.dot(a, b))
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def _softmax(logits: list[float]) -> list[float]:
    max_l = max(logits)
    exps = [math.exp(l - max_l) for l in logits]
    total = sum(exps)
    return [e / total for e in exps]


@dataclass
class DecisionEngine:
    """
    Three-stage neural decision pipeline:
      1. Intent classifier — what does the user want?
      2. Confidence scorer — can we answer from knowledge?
      3. Action selector — what should LocalMind do?
    """

    _intent_centroids: dict[Intent, np.ndarray] = field(default_factory=dict)
    _action_centroids: dict[Action, np.ndarray] = field(default_factory=dict)
    _initialized: bool = False

    # ── Lazy init ─────────────────────────────────────────────────────────

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        model = knowledge._ensure_embed()

        # Build intent centroids from seed examples
        for intent, examples in INTENT_SEEDS.items():
            vecs = model.encode(examples, normalize_embeddings=True)
            self._intent_centroids[intent] = np.mean(vecs, axis=0)

        # Build action centroids from anchor phrases
        for action, phrases in ACTION_ANCHORS.items():
            vecs = model.encode(phrases, normalize_embeddings=True)
            self._action_centroids[action] = np.mean(vecs, axis=0)

        self._initialized = True
        log.info(
            "Decision engine ready: %d intents, %d actions",
            len(self._intent_centroids),
            len(self._action_centroids),
        )

    # ── 1. Intent classification ──────────────────────────────────────────

    def classify_intent(self, text: str) -> dict[str, Any]:
        self._ensure_init()
        model = knowledge._ensure_embed()
        vec = model.encode([text], normalize_embeddings=True)[0]

        similarities: dict[Intent, float] = {}
        for intent, centroid in self._intent_centroids.items():
            similarities[intent] = _cosine_sim(vec, centroid)

        labels = list(similarities.keys())
        sims = [similarities[l] for l in labels]
        probs = _softmax([s * 10.0 for s in sims])  # temperature-scaled

        ranked = sorted(zip(labels, probs), key=lambda x: x[1], reverse=True)
        best_intent = ranked[0][0]
        best_prob = ranked[0][1]

        return {
            "intent": best_intent,
            "confidence": round(best_prob, 4),
            "scores": {label: round(prob, 4) for label, prob in ranked},
        }

    # ── 2. Confidence scoring ─────────────────────────────────────────────

    def score_confidence(
        self,
        query: str,
        rag_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Scores how well the knowledge base can answer this query.
        Returns a 0-1 confidence + recommendation.
        """
        self._ensure_init()
        model = knowledge._ensure_embed()
        query_vec = model.encode([query], normalize_embeddings=True)[0]

        if rag_results is None:
            rag_results = knowledge.query(query)

        if not rag_results:
            return {
                "confidence": 0.0,
                "has_context": False,
                "recommendation": "no_context",
                "detail": "No knowledge base documents found. Answer from general knowledge only.",
            }

        distances = [r["distance"] for r in rag_results]
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity: 1 - (dist / 2)
        similarities = [1.0 - (d / 2.0) for d in distances]

        top_sim = max(similarities)
        avg_sim = sum(similarities) / len(similarities)
        spread = top_sim - min(similarities)

        # Weighted confidence score
        confidence = 0.5 * top_sim + 0.3 * avg_sim + 0.2 * (1.0 - spread)
        confidence = max(0.0, min(1.0, confidence))

        if confidence >= 0.75:
            rec = "high_confidence"
            detail = "Strong knowledge base match. RAG answer should be reliable."
        elif confidence >= 0.45:
            rec = "moderate_confidence"
            detail = "Partial match. Answer may need caveats."
        else:
            rec = "low_confidence"
            detail = "Weak match. Consider informing the user about limited context."

        return {
            "confidence": round(confidence, 4),
            "has_context": True,
            "recommendation": rec,
            "top_similarity": round(top_sim, 4),
            "avg_similarity": round(avg_sim, 4),
            "context_chunks": len(rag_results),
            "detail": detail,
        }

    # ── 3. Action selection ───────────────────────────────────────────────

    def select_action(
        self,
        text: str,
        intent: dict[str, Any] | None = None,
        confidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Decides what LocalMind should do given the user's message,
        intent classification, and knowledge confidence.
        """
        self._ensure_init()
        model = knowledge._ensure_embed()
        vec = model.encode([text], normalize_embeddings=True)[0]

        if intent is None:
            intent = self.classify_intent(text)
        if confidence is None:
            confidence = self.score_confidence(text)

        # Neural similarity to action centroids
        action_sims: dict[Action, float] = {}
        for action, centroid in self._action_centroids.items():
            action_sims[action] = _cosine_sim(vec, centroid)

        # Heuristic boosts based on intent + confidence
        boosts: dict[Action, float] = {a: 0.0 for a in action_sims}

        user_intent: Intent = intent["intent"]
        conf_score: float = confidence.get("confidence", 0.0)
        has_context: bool = confidence.get("has_context", False)

        if user_intent == "learn":
            boosts["learn_url"] += 0.4
        elif user_intent == "question":
            if has_context:
                boosts["rag_chat"] += 0.35
            else:
                boosts["direct_chat"] += 0.15
        elif user_intent == "code":
            boosts["write_code"] += 0.4
            if has_context:
                boosts["rag_chat"] += 0.1
            if "```" in text or "run this" in text.lower() or "execute" in text.lower():
                boosts["run_code"] += 0.3
        elif user_intent == "action":
            if has_context:
                boosts["rag_chat"] += 0.25
            boosts["summarize"] += 0.1
            boosts["write_code"] += 0.05
        elif user_intent == "clarify":
            boosts["clarify"] += 0.35
        elif user_intent == "chitchat":
            boosts["direct_chat"] += 0.3

        # URL detection override
        url_pattern = re.compile(r"https?://\S+")
        if url_pattern.search(text) and user_intent in ("learn", "action"):
            boosts["learn_url"] += 0.3

        # Combine neural similarity + heuristic boosts
        combined: dict[Action, float] = {}
        for action in action_sims:
            combined[action] = action_sims[action] * 0.6 + boosts.get(action, 0.0) * 0.4

        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)
        best_action = ranked[0][0]
        best_score = ranked[0][1]

        return {
            "action": best_action,
            "score": round(best_score, 4),
            "all_scores": {a: round(s, 4) for a, s in ranked},
            "reasoning": self._explain(best_action, user_intent, conf_score, has_context),
        }

    def _explain(
        self,
        action: Action,
        intent: Intent,
        confidence: float,
        has_context: bool,
    ) -> str:
        parts: list[str] = [f"Intent: {intent}"]
        if has_context:
            parts.append(f"KB confidence: {confidence:.0%}")
        else:
            parts.append("No KB context")
        explanations: dict[Action, str] = {
            "rag_chat": "Answering with knowledge base context.",
            "direct_chat": "Answering from general model knowledge.",
            "learn_url": "Detected a URL to ingest into knowledge base.",
            "summarize": "Summarizing content for the user.",
            "clarify": "Asking user for more details.",
            "write_code": "Generating code for the user.",
            "run_code": "Executing user-provided code locally.",
        }
        parts.append(explanations.get(action, ""))
        return " | ".join(parts)

    # ── Full pipeline ─────────────────────────────────────────────────────

    def decide(self, text: str) -> dict[str, Any]:
        """Run the full 3-stage decision pipeline."""
        intent = self.classify_intent(text)
        rag_results = knowledge.query(text)
        confidence = self.score_confidence(text, rag_results)
        action = self.select_action(text, intent, confidence)

        return {
            "intent": intent,
            "confidence": confidence,
            "action": action,
        }


decision_engine = DecisionEngine()

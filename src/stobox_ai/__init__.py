"""Stobox AI — a channel-agnostic RAG agent platform.

Telegram is implemented as one adapter (``channels/telegram``) over a reusable
AI agent core (``core/engine.py``). Every layer is replaceable behind an
interface: LLM providers, embeddings, vector store, memory, channels.
"""

__version__ = "0.1.0"

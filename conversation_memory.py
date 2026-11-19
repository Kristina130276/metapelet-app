"""Persistent conversation memory for MetaPelet.

Stores a concise rolling summary (~800 chars max) of user's personal facts:
- Family members' names
- Events mentioned
- Health mentions (only as the person said them)
- Mood patterns
- Plans and topics they enjoy

NEVER stores full transcript text. Updates after ~6 user messages per session.
File fallback when DATABASE_URL not set (ephemeral on Render free tier).
"""

from __future__ import annotations

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger(__name__)

# Storage paths
DATA_DIR = Path(os.environ.get("PILOT_DATA_DIR", "data"))
MEMORY_PATH = DATA_DIR / "conversation_memory.json"

# Database connection (reuse pattern from pilot_tracking/tts_budget)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_DATABASE = bool(DATABASE_URL and PSYCOPG2_AVAILABLE)

_db_connection: Optional[object] = None

# Memory constraints
MAX_SUMMARY_CHARS = 800
UPDATE_EVERY_N_MESSAGES = 4  # Trigger summarization after 4 user messages
MAX_BUFFER_MESSAGES = 30  # Cap on unsummarized message buffer


def _get_db_connection():
    """Get or create database connection."""
    global _db_connection
    if not USE_DATABASE:
        return None
    if _db_connection is None:
        try:
            from pilot_tracking import _sanitize_database_url
            url = _sanitize_database_url(DATABASE_URL)
            _db_connection = psycopg2.connect(url, connect_timeout=3)
            _db_connection.autocommit = True
            logger.info("Conversation memory: Database connection established")
        except Exception as e:
            logger.error(f"Conversation memory: Failed to connect to database: {type(e).__name__}")
            return None
    return _db_connection


def _reset_connection():
    """Close and reset the global connection."""
    global _db_connection
    if _db_connection is not None:
        try:
            _db_connection.close()
        except Exception:
            pass
        _db_connection = None


def _execute_with_retry(operation_name: str, db_operation):
    """Execute a database operation with automatic reconnect on stale connection."""
    if not USE_DATABASE:
        return False, "no_database"
    
    conn = _get_db_connection()
    if conn is None:
        logger.warning(f"{operation_name}: Database not available, will use file fallback")
        return False, "database_error"
    
    try:
        return db_operation(conn)
    except Exception as e:
        error_str = str(e).lower()
        is_stale = any(keyword in error_str for keyword in [
            'closed', 'terminated', 'broken', 'lost', 'reset', 'timeout',
            'server closed', 'connection already closed', 'no connection'
        ])
        
        if not is_stale:
            logger.error(f"{operation_name}: Database error (no retry): {type(e).__name__}")
            return False, "database_error"
        
        logger.warning(f"{operation_name}: Stale connection detected, reconnecting...")
        _reset_connection()
        
        conn = _get_db_connection()
        if conn is None:
            logger.error(f"{operation_name}: Reconnection failed")
            return False, "database_error"
        
        try:
            logger.info(f"{operation_name}: Retrying after reconnect")
            return db_operation(conn)
        except Exception as retry_e:
            logger.error(f"{operation_name}: Retry failed: {type(retry_e).__name__}")
            return False, "database_error"


def init_database():
    """Initialize conversation memory table if DATABASE_URL is set (idempotent)."""
    if not USE_DATABASE:
        return
    
    conn = _get_db_connection()
    if not conn:
        return
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_memory (
                    user_id VARCHAR(100) PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            logger.info("Conversation memory: Database table initialized successfully")
    except Exception as e:
        logger.error(f"Conversation memory: Failed to initialize database table: {e}")


def _load_file_memory() -> dict[str, dict]:
    """Load memory from JSON file."""
    if not MEMORY_PATH.is_file():
        return {}
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_file_memory(memory: dict[str, dict]):
    """Save memory to JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(memory, ensure_ascii=False), encoding="utf-8")


def get_memory(user_id: str) -> Optional[str]:
    """
    Get memory summary for user_id.
    Returns summary text or None if no memory exists.
    """
    user_id = (user_id or "").strip()[:100]
    if not user_id:
        return None
    
    if USE_DATABASE:
        def db_read(conn):
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT summary FROM conversation_memory
                        WHERE user_id = %s
                    """, (user_id,))
                    row = cur.fetchone()
                    return True, row[0] if row else None
            except Exception as e:
                logger.error(f"Failed to get memory from DB: {type(e).__name__}")
                raise
        
        success, result = _execute_with_retry("get_memory", db_read)
        if success:
            return result
        # Fall through to file fallback
    
    # File fallback
    memory = _load_file_memory()
    entry = memory.get(user_id)
    return entry.get("summary") if entry else None


def store_memory(user_id: str, summary: str) -> tuple[bool, str]:
    """
    Store or update memory summary for user_id.
    Summary is truncated to MAX_SUMMARY_CHARS.
    
    Returns (success: bool, error_code: str)
    """
    user_id = (user_id or "").strip()[:100]
    if not user_id:
        return False, "invalid_user_id"
    
    # Truncate summary to max chars
    summary = (summary or "").strip()[:MAX_SUMMARY_CHARS]
    if not summary:
        return False, "empty_summary"
    
    updated_at = datetime.now(timezone.utc).isoformat()
    
    if USE_DATABASE:
        def db_upsert(conn):
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO conversation_memory (user_id, summary, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE
                        SET summary = EXCLUDED.summary,
                            updated_at = EXCLUDED.updated_at
                    """, (user_id, summary, updated_at))
                return True, ""
            except Exception as e:
                logger.error(f"Failed to store memory: {type(e).__name__}")
                raise
        
        success, error = _execute_with_retry("store_memory", db_upsert)
        if success:
            logger.info(f"Memory stored for user (DB): {len(summary)} chars")
            return True, ""
        # Fall through to file fallback
    
    # File fallback
    memory = _load_file_memory()
    memory[user_id] = {
        "summary": summary,
        "updated_at": updated_at
    }
    _save_file_memory(memory)
    logger.info(f"Memory stored for user (file): {len(summary)} chars")
    return True, ""


def delete_memory(user_id: str) -> tuple[bool, str]:
    """
    Delete memory for user_id.
    
    Returns (success: bool, error_code: str)
    """
    user_id = (user_id or "").strip()[:100]
    if not user_id:
        return False, "invalid_user_id"
    
    if USE_DATABASE:
        def db_delete(conn):
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM conversation_memory
                        WHERE user_id = %s
                    """, (user_id,))
                return True, ""
            except Exception as e:
                logger.error(f"Failed to delete memory: {type(e).__name__}")
                raise
        
        success, error = _execute_with_retry("delete_memory", db_delete)
        if success:
            logger.info(f"Memory deleted for user (DB)")
            return True, ""
        # Fall through to file fallback
    
    # File fallback
    memory = _load_file_memory()
    if user_id in memory:
        del memory[user_id]
        _save_file_memory(memory)
        logger.info(f"Memory deleted for user (file)")
    return True, ""


def count_user_messages(history: list[dict]) -> int:
    """Count number of user messages in conversation history."""
    return sum(1 for msg in history if msg.get("role") == "user" and msg.get("content") != "[начало разговора]")

"""Traffic source tracking for MetaPelet.

Tracks visitor sources (e.g., from YouTube) and counts:
  - visits: landing with ?from=... parameter
  - conversations_started: first conversation for a visitor with a remembered source

Storage modes:
  1. DATABASE_URL set: Uses PostgreSQL with atomic upserts
  2. No DATABASE_URL: Falls back to local JSON file (ephemeral on Render free tier)

No personal data, no IP storage, no conversation content.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional
import logging

# Try to import psycopg2, but don't fail if it's not available
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("TRAFFIC_DATA_DIR", "data"))
TRAFFIC_PATH = DATA_DIR / "traffic_sources.json"

# Allowlist of valid source identifiers
VALID_SOURCES = frozenset({"yt", "ig", "fb", "tw", "li", "other"})


def _sanitize_database_url(raw_value: str) -> str:
    """Extract and normalize PostgreSQL URL from potentially messy input.
    
    Reuses the same sanitization logic as pilot_tracking.py.
    """
    if not raw_value:
        return ""
    
    import re
    # Search for all postgres URLs in the raw value
    url_pattern = r'(postgres(?:ql)?://[^\s\'"]+)'
    matches = re.findall(url_pattern, raw_value)
    
    if not matches:
        return raw_value.strip()
    
    # If multiple URLs found, prefer the one from DATABASE_URL= line
    selected_url = None
    for line in raw_value.split('\n'):
        line_stripped = line.strip()
        if line_stripped.startswith('DATABASE_URL='):
            value = line_stripped[len('DATABASE_URL='):].strip()
            value = value.strip('\'"')
            line_matches = re.findall(url_pattern, value)
            if line_matches:
                selected_url = line_matches[0]
                break
    
    # If no DATABASE_URL= line, use first URL found (skip DATABASE_URL_UNPOOLED if possible)
    if not selected_url:
        for line in raw_value.split('\n'):
            if 'DATABASE_URL_UNPOOLED' in line:
                continue
            line_matches = re.findall(url_pattern, line)
            if line_matches:
                selected_url = line_matches[0]
                break
        
        if not selected_url:
            selected_url = matches[0]
    
    selected_url = selected_url.strip('\'"')
    
    # Normalize postgres:// to postgresql://
    if selected_url.startswith('postgres://'):
        selected_url = 'postgresql://' + selected_url[len('postgres://'):]
    
    # Add sslmode=require for neon.tech if not present
    if 'neon.tech' in selected_url and 'sslmode=' not in selected_url:
        separator = '&' if '?' in selected_url else '?'
        selected_url = selected_url + separator + 'sslmode=require'
    
    return selected_url


DATABASE_URL = _sanitize_database_url(os.environ.get("DATABASE_URL", ""))
USE_DATABASE = bool(DATABASE_URL and PSYCOPG2_AVAILABLE)

# Database connection (created on first use)
_db_connection: Optional[object] = None


def _get_db_connection():
    """Get or create database connection."""
    global _db_connection
    if not USE_DATABASE:
        return None
    if _db_connection is None:
        try:
            _db_connection = psycopg2.connect(DATABASE_URL, connect_timeout=3)
            _db_connection.autocommit = True
            logger.info("Traffic sources database connection established")
        except Exception as e:
            logger.error(f"Failed to connect to traffic database: {type(e).__name__}")
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


def init_database():
    """Initialize traffic sources table if DATABASE_URL is set."""
    if not USE_DATABASE:
        return
    
    conn = _get_db_connection()
    if not conn:
        return
    
    try:
        with conn.cursor() as cur:
            # Create traffic_sources table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS traffic_sources (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    source VARCHAR(20) NOT NULL,
                    visits INTEGER NOT NULL DEFAULT 0,
                    conversations_started INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    UNIQUE(date, source)
                )
            """)
            
            # Create index for efficient queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_traffic_sources_date 
                ON traffic_sources(date DESC)
            """)
            
            logger.info("Traffic sources table initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize traffic sources table: {type(e).__name__}")


def sanitize_source(raw_source: str) -> str:
    """Sanitize traffic source to allowlist.
    
    Args:
        raw_source: Raw source string from query parameter
        
    Returns:
        Sanitized source: one of 'yt', 'ig', 'fb', 'tw', 'li', or 'other'
    """
    source = (raw_source or "").strip().lower()[:20]
    
    if not source:
        return "other"
    
    # Direct match
    if source in VALID_SOURCES:
        return source
    
    # Map common variations
    if source in ("youtube", "yt"):
        return "yt"
    if source in ("instagram", "ig", "insta"):
        return "ig"
    if source in ("facebook", "fb"):
        return "fb"
    if source in ("twitter", "tw", "x"):
        return "tw"
    if source in ("linkedin", "li"):
        return "li"
    
    return "other"


def _today_date() -> str:
    """Get today's date in YYYY-MM-DD format."""
    return datetime.now(timezone.utc).date().isoformat()


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_traffic_file() -> dict:
    """Read traffic data from JSON file.
    
    Returns dict: {date: {source: {visits: int, conversations_started: int}}}
    """
    if not TRAFFIC_PATH.is_file():
        return {}
    try:
        return json.loads(TRAFFIC_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_traffic_file(data: dict) -> None:
    """Write traffic data to JSON file."""
    _ensure_data_dir()
    TRAFFIC_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_visit(source: str, session_id: str = "") -> tuple[bool, str]:
    """Record a visit from a traffic source.
    
    Args:
        source: Traffic source identifier (will be sanitized)
        session_id: Session ID to check for test sessions
        
    Returns:
        (success: bool, error_code: str)
    """
    # Skip test sessions
    if session_id.startswith("ps_TEST_"):
        return True, ""
    
    clean_source = sanitize_source(source)
    today = _today_date()
    
    if USE_DATABASE:
        conn = _get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO traffic_sources (date, source, visits, conversations_started)
                        VALUES (%s, %s, 1, 0)
                        ON CONFLICT (date, source)
                        DO UPDATE SET
                            visits = traffic_sources.visits + 1,
                            updated_at = NOW()
                    """, (today, clean_source))
                return True, ""
            except Exception as e:
                logger.error(f"Failed to record visit in database: {type(e).__name__}")
                # Fall through to file fallback
    
    # File fallback
    data = _read_traffic_file()
    if today not in data:
        data[today] = {}
    if clean_source not in data[today]:
        data[today][clean_source] = {"visits": 0, "conversations_started": 0}
    
    data[today][clean_source]["visits"] += 1
    _write_traffic_file(data)
    logger.info(f"Visit recorded to file fallback: {clean_source}")
    return True, ""


def record_conversation_started(source: str, session_id: str = "") -> tuple[bool, str]:
    """Record a conversation started from a traffic source.
    
    Args:
        source: Traffic source identifier (will be sanitized)
        session_id: Session ID to check for test sessions
        
    Returns:
        (success: bool, error_code: str)
    """
    # Skip test sessions
    if session_id.startswith("ps_TEST_"):
        return True, ""
    
    clean_source = sanitize_source(source)
    today = _today_date()
    
    if USE_DATABASE:
        conn = _get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO traffic_sources (date, source, visits, conversations_started)
                        VALUES (%s, %s, 0, 1)
                        ON CONFLICT (date, source)
                        DO UPDATE SET
                            conversations_started = traffic_sources.conversations_started + 1,
                            updated_at = NOW()
                    """, (today, clean_source))
                return True, ""
            except Exception as e:
                logger.error(f"Failed to record conversation in database: {type(e).__name__}")
                # Fall through to file fallback
    
    # File fallback
    data = _read_traffic_file()
    if today not in data:
        data[today] = {}
    if clean_source not in data[today]:
        data[today][clean_source] = {"visits": 0, "conversations_started": 0}
    
    data[today][clean_source]["conversations_started"] += 1
    _write_traffic_file(data)
    logger.info(f"Conversation started recorded to file fallback: {clean_source}")
    return True, ""


def get_all_traffic_for_export() -> list[dict]:
    """Get all traffic source data for CSV export.
    
    Returns list of dicts with keys: date, source, visits, conversations_started
    Sorted by date DESC, source ASC
    """
    if USE_DATABASE:
        conn = _get_db_connection()
        if conn:
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT date, source, visits, conversations_started
                        FROM traffic_sources
                        ORDER BY date DESC, source ASC
                    """)
                    rows = cur.fetchall()
                    return [
                        {
                            "date": row["date"].isoformat() if row["date"] else "",
                            "source": row["source"],
                            "visits": row["visits"],
                            "conversations_started": row["conversations_started"]
                        }
                        for row in rows
                    ]
            except Exception as e:
                logger.error(f"Failed to read traffic from database: {type(e).__name__}")
                # Fall through to file fallback
    
    # File fallback
    data = _read_traffic_file()
    result = []
    for date_str in sorted(data.keys(), reverse=True):
        for source in sorted(data[date_str].keys()):
            stats = data[date_str][source]
            result.append({
                "date": date_str,
                "source": source,
                "visits": stats.get("visits", 0),
                "conversations_started": stats.get("conversations_started", 0)
            })
    return result

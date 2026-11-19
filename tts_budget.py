"""Monthly TTS budget enforcement to stay within Google Cloud free tier.

Google Cloud Text-to-Speech free tiers (per calendar month, billable = len(text) incl. spaces):
- Chirp 3 HD: 1,000,000 chars
- Neural2 (and Polyglot): 1,000,000 chars
- WaveNet + Standard share one SKU: 4,000,000 chars combined

Storage:
1. DATABASE_URL set: Uses PostgreSQL (atomic INSERT ... ON CONFLICT DO UPDATE)
2. No DATABASE_URL: Falls back to local JSON file (conservative: skip premium tiers)

WARNING: File fallback is ephemeral on Render free tier. Set DATABASE_URL for production.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import logging

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger(__name__)

# Safety caps (env-overridable, set below free tier limits for safety margin)
TTS_MONTHLY_CAP_CHIRP3HD = int(os.environ.get("TTS_MONTHLY_CAP_CHIRP3HD", "900000"))
TTS_MONTHLY_CAP_NEURAL2 = int(os.environ.get("TTS_MONTHLY_CAP_NEURAL2", "900000"))
TTS_MONTHLY_CAP_WAVENET_STANDARD = int(os.environ.get("TTS_MONTHLY_CAP_WAVENET_STANDARD", "3600000"))

# Storage paths
DATA_DIR = Path(os.environ.get("PILOT_DATA_DIR", "data"))
TTS_BUDGET_PATH = DATA_DIR / "tts_monthly_budget.json"

# Database connection (reuse pattern from pilot_tracking)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_DATABASE = bool(DATABASE_URL and PSYCOPG2_AVAILABLE)

_db_connection: Optional[object] = None
_lock = threading.Lock()

# In-memory cache (degraded mode when DB unavailable)
_file_budget: dict[str, dict[str, int]] = {}


def _current_month() -> str:
    """Return current month in UTC as YYYY-MM."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _voice_to_tier(voice_name: str) -> str:
    """Map Google TTS voice name to pricing tier."""
    voice_lower = voice_name.lower()
    if "chirp3-hd" in voice_lower or "chirp-3-hd" in voice_lower:
        return "chirp3hd"
    if "neural2" in voice_lower or "polyglot" in voice_lower:
        return "neural2"
    # WaveNet and Standard share the same SKU
    return "wavenet_standard"


def _cap_for_tier(tier: str) -> int:
    """Return monthly cap for given tier."""
    caps = {
        "chirp3hd": TTS_MONTHLY_CAP_CHIRP3HD,
        "neural2": TTS_MONTHLY_CAP_NEURAL2,
        "wavenet_standard": TTS_MONTHLY_CAP_WAVENET_STANDARD,
    }
    return caps.get(tier, TTS_MONTHLY_CAP_WAVENET_STANDARD)


def _get_db_connection():
    """Get or create database connection (reuse pattern from pilot_tracking)."""
    global _db_connection
    if not USE_DATABASE:
        return None
    if _db_connection is None:
        try:
            # Import sanitize function from pilot_tracking
            from pilot_tracking import _sanitize_database_url
            url = _sanitize_database_url(DATABASE_URL)
            _db_connection = psycopg2.connect(url, connect_timeout=3)
            _db_connection.autocommit = True
            logger.info("TTS budget: Database connection established")
        except Exception as e:
            logger.error(f"TTS budget: Failed to connect to database: {type(e).__name__}")
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
    """Initialize TTS budget table if DATABASE_URL is set (idempotent)."""
    if not USE_DATABASE:
        return
    
    conn = _get_db_connection()
    if not conn:
        return
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tts_monthly_budget (
                    month VARCHAR(7) PRIMARY KEY,
                    chirp3hd_used INTEGER NOT NULL DEFAULT 0,
                    neural2_used INTEGER NOT NULL DEFAULT 0,
                    wavenet_standard_used INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            logger.info("TTS budget: Database table initialized successfully")
    except Exception as e:
        logger.error(f"TTS budget: Failed to initialize database table: {e}")


def _load_file_budget() -> dict[str, dict[str, int]]:
    """Load monthly budget from JSON file."""
    if not TTS_BUDGET_PATH.is_file():
        return {}
    try:
        data = json.loads(TTS_BUDGET_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_file_budget(budget: dict[str, dict[str, int]]):
    """Save monthly budget to JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TTS_BUDGET_PATH.write_text(json.dumps(budget, ensure_ascii=False), encoding="utf-8")


def _increment_db(month: str, tier: str, chars: int) -> tuple[bool, str]:
    """Increment counter in database (atomic)."""
    def db_update(conn):
        try:
            with conn.cursor() as cur:
                col_name = f"{tier}_used"
                cur.execute(f"""
                    INSERT INTO tts_monthly_budget (month, {col_name})
                    VALUES (%s, %s)
                    ON CONFLICT (month) DO UPDATE
                    SET {col_name} = tts_monthly_budget.{col_name} + EXCLUDED.{col_name},
                        updated_at = NOW()
                """, (month, chars))
            return True, ""
        except Exception as e:
            logger.error(f"Failed to increment TTS budget: {type(e).__name__}")
            raise
    
    success, error = _execute_with_retry("increment_tts_budget", db_update)
    return success, error


def _get_db_usage(month: str) -> dict[str, int]:
    """Get current usage from database."""
    conn = _get_db_connection()
    if not conn:
        return {"chirp3hd": 0, "neural2": 0, "wavenet_standard": 0}
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT chirp3hd_used, neural2_used, wavenet_standard_used
                FROM tts_monthly_budget
                WHERE month = %s
            """, (month,))
            row = cur.fetchone()
            if row:
                return {
                    "chirp3hd": row[0],
                    "neural2": row[1],
                    "wavenet_standard": row[2],
                }
            return {"chirp3hd": 0, "neural2": 0, "wavenet_standard": 0}
    except Exception as e:
        logger.error(f"Failed to get TTS usage from DB: {type(e).__name__}")
        return {"chirp3hd": 0, "neural2": 0, "wavenet_standard": 0}


def check_and_reserve(voice_name: str, text_length: int) -> tuple[bool, str]:
    """
    Check if voice can be used without exceeding cap.
    Does NOT increment counter - call record_success() after Google responds.
    
    Returns (allowed: bool, reason: str)
    """
    tier = _voice_to_tier(voice_name)
    cap = _cap_for_tier(tier)
    month = _current_month()
    
    db_available = False
    if USE_DATABASE:
        conn = _get_db_connection()
        if conn is not None:
            db_available = True
            usage = _get_db_usage(month)
        else:
            # DB configured but unavailable - fall back to file with conservative mode
            logger.warning(f"TTS budget: Database unavailable, using degraded mode")
            db_available = False
    
    if not db_available:
        # File fallback: conservative mode - skip premium tiers
        with _lock:
            global _file_budget
            if not _file_budget:
                _file_budget = _load_file_budget()
            
            # Conservative: in degraded mode, only allow wavenet_standard
            if tier in ("chirp3hd", "neural2"):
                logger.warning(f"TTS budget: Degraded mode, skipping premium tier {tier}")
                return False, "degraded_mode"
            
            usage = _file_budget.get(month, {})
    
    current = usage.get(tier, 0)
    if current + text_length > cap:
        logger.warning(f"TTS budget: {tier} cap exceeded ({current}+{text_length} > {cap})")
        return False, "monthly_cap"
    
    return True, ""


def record_success(voice_name: str, text_length: int):
    """
    Record successful TTS call (increment counter).
    Only call after Google returns success.
    """
    tier = _voice_to_tier(voice_name)
    month = _current_month()
    
    if USE_DATABASE:
        success, _ = _increment_db(month, tier, text_length)
        if success:
            logger.info(f"TTS budget: {tier} +{text_length} chars in {month}")
            return
        # Fall through to file fallback
    
    # File fallback
    with _lock:
        global _file_budget
        if not _file_budget:
            _file_budget = _load_file_budget()
        
        if month not in _file_budget:
            _file_budget[month] = {"chirp3hd": 0, "neural2": 0, "wavenet_standard": 0}
        
        _file_budget[month][tier] = _file_budget[month].get(tier, 0) + text_length
        _save_file_budget(_file_budget)
        logger.info(f"TTS budget (file): {tier} +{text_length} chars in {month}")


def get_status() -> dict:
    """
    Get current month's usage and caps for /budget/status.
    Returns: {"month": "YYYY-MM", "tiers": {"chirp3hd": {"used": X, "cap": Y}, ...}}
    """
    month = _current_month()
    
    if USE_DATABASE:
        usage = _get_db_usage(month)
    else:
        with _lock:
            global _file_budget
            if not _file_budget:
                _file_budget = _load_file_budget()
            usage = _file_budget.get(month, {"chirp3hd": 0, "neural2": 0, "wavenet_standard": 0})
    
    return {
        "month": month,
        "tiers": {
            "chirp3hd": {
                "used": usage.get("chirp3hd", 0),
                "cap": TTS_MONTHLY_CAP_CHIRP3HD,
            },
            "neural2": {
                "used": usage.get("neural2", 0),
                "cap": TTS_MONTHLY_CAP_NEURAL2,
            },
            "wavenet_standard": {
                "used": usage.get("wavenet_standard", 0),
                "cap": TTS_MONTHLY_CAP_WAVENET_STANDARD,
            },
        },
    }

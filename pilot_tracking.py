"""Anonymous pilot funnel + feedback. No PII.

Storage modes:
  1. DATABASE_URL set: Uses PostgreSQL (Neon, Supabase, etc.)
  2. No DATABASE_URL: Falls back to local JSONL files (ephemeral on Render free tier)

WARNING: On Render free tier, data written to local files is EPHEMERAL and LOST on restart.
For production pilots, set DATABASE_URL to a persistent PostgreSQL database.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
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

DATA_DIR = Path(os.environ.get("PILOT_DATA_DIR", "data"))
EVENTS_PATH = DATA_DIR / "pilot_events.jsonl"
FEEDBACK_PATH = DATA_DIR / "pilot_feedback.jsonl"


def _sanitize_database_url(raw_value: str) -> str:
    """Extract and normalize PostgreSQL URL from potentially messy input.
    
    Handles cases where users paste multi-line Neon .env blocks or psql snippets
    into the DATABASE_URL env var. Never logs or returns the URL in exceptions.
    
    Args:
        raw_value: Raw DATABASE_URL value from environment
        
    Returns:
        Clean PostgreSQL URL or empty string if none found
    """
    if not raw_value:
        return ""
    
    # Search for all postgres URLs in the raw value
    # Matches postgres:// or postgresql:// up to whitespace or quote
    url_pattern = r'(postgres(?:ql)?://[^\s\'"]+)'
    matches = re.findall(url_pattern, raw_value)
    
    if not matches:
        # No URL found, fall back to original behavior (strip whitespace)
        return raw_value.strip()
    
    # If multiple URLs found, prefer the one from DATABASE_URL= line
    selected_url = None
    for line in raw_value.split('\n'):
        line_stripped = line.strip()
        if line_stripped.startswith('DATABASE_URL='):
            # Extract value after = and strip quotes
            value = line_stripped[len('DATABASE_URL='):].strip()
            value = value.strip('\'"')
            # Find URL in this line
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
        
        # If still nothing, use first match (even if it's UNPOOLED)
        if not selected_url:
            selected_url = matches[0]
    
    # Strip any trailing quotes
    selected_url = selected_url.strip('\'"')
    
    # Normalize postgres:// to postgresql://
    if selected_url.startswith('postgres://'):
        selected_url = 'postgresql://' + selected_url[len('postgres://'):]
    
    # Add sslmode=require for neon.tech if not present
    if 'neon.tech' in selected_url and 'sslmode=' not in selected_url:
        separator = '&' if '?' in selected_url else '?'
        selected_url = selected_url + separator + 'sslmode=require'
    
    return selected_url


def _classify_db_error(error: Exception) -> str:
    """Classify database connection error into safe category.
    
    Returns one of: 'invalid_dsn', 'auth_failed', 'timeout', 'host_not_found', 'ssl', 'other'
    Never returns exception text to avoid leaking secrets.
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # ProgrammingError usually means DSN parsing failed
    if error_type == 'ProgrammingError' or 'invalid dsn' in error_str or 'parse' in error_str:
        return 'invalid_dsn'
    
    # Authentication errors
    if 'password authentication failed' in error_str or 'auth' in error_str:
        return 'auth_failed'
    
    # Timeout errors
    if 'timeout' in error_str or error_type in ('TimeoutError', 'ConnectTimeout'):
        return 'timeout'
    
    # Host resolution errors
    if 'could not translate host name' in error_str or 'name or service not known' in error_str:
        return 'host_not_found'
    
    # SSL errors
    if 'ssl' in error_str or 'certificate' in error_str:
        return 'ssl'
    
    return 'other'


DATABASE_URL = _sanitize_database_url(os.environ.get("DATABASE_URL", ""))
USE_DATABASE = bool(DATABASE_URL and PSYCOPG2_AVAILABLE)

VALID_EVENTS = frozenset({
    "site_visit",
    "anketa_started",
    "anketa_saved",
    "voice_started",
    "conversation",
})

FEEDBACK_COMFORT = frozenset({"yes", "partial", "no", ""})  # empty = not asked
FEEDBACK_LIKED = frozenset({"yes", "partial", "no", ""})  # empty = not asked
FEEDBACK_CONTINUE = frozenset({"yes", "maybe", "no", ""})  # empty = not asked

# Database connection pool (created on first use)
_db_connection: Optional[object] = None
_db_connection_error: Optional[Exception] = None
_db_error_kind: Optional[str] = None


def _get_db_connection():
    """Get or create database connection."""
    global _db_connection, _db_connection_error, _db_error_kind
    if not USE_DATABASE:
        return None
    if _db_connection is None:
        try:
            _db_connection = psycopg2.connect(DATABASE_URL, connect_timeout=3)
            _db_connection.autocommit = True
            _db_connection_error = None
            _db_error_kind = None
            logger.info("Database connection established")
        except Exception as e:
            _db_connection_error = e
            _db_error_kind = _classify_db_error(e)
            logger.error(f"Failed to connect to database: {type(e).__name__} ({_db_error_kind})")
            return None
    return _db_connection


def _is_connection_alive(conn) -> bool:
    """Check if database connection is still alive."""
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


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
    """Execute a database operation with automatic reconnect on stale connection.
    
    Args:
        operation_name: Name of the operation for logging (e.g., "record_feedback")
        db_operation: Callable that takes a connection and performs the DB operation.
                     Should return (success: bool, error_code: str).
    
    Returns:
        tuple: (success: bool, error_code: str)
    """
    if not USE_DATABASE:
        return False, "no_database"
    
    conn = _get_db_connection()
    if conn is None:
        logger.warning(f"{operation_name}: Database not available, will use file fallback")
        return False, "database_error"
    
    # Try operation with existing connection
    try:
        return db_operation(conn)
    except Exception as e:
        error_str = str(e).lower()
        # Check if this looks like a stale/closed connection error
        is_stale = any(keyword in error_str for keyword in [
            'closed', 'terminated', 'broken', 'lost', 'reset', 'timeout',
            'server closed', 'connection already closed', 'no connection'
        ])
        
        if not is_stale:
            # Not a connection issue, don't retry
            logger.error(f"{operation_name}: Database error (no retry): {type(e).__name__}")
            return False, "database_error"
        
        # Connection is stale, try to reconnect and retry once
        logger.warning(f"{operation_name}: Stale connection detected, reconnecting...")
        _reset_connection()
        
        conn = _get_db_connection()
        if conn is None:
            logger.error(f"{operation_name}: Reconnection failed")
            return False, "database_error"
        
        # Retry the operation
        try:
            logger.info(f"{operation_name}: Retrying after reconnect")
            return db_operation(conn)
        except Exception as retry_e:
            logger.error(f"{operation_name}: Retry failed: {type(retry_e).__name__}")
            return False, "database_error"


def check_database_health() -> dict:
    """Check database connection health for /health endpoint.
    
    Returns a dict with storage info, never exposing secrets:
    - storage: "database" or "file"
    - database_configured: bool (True if DATABASE_URL is set)
    - database_connected: bool (True if SELECT 1 succeeds)
    - database_error: "connect_failed" (only on failure)
    - database_error_kind: categorized error type (only on failure)
    """
    result = {
        "storage": "database" if USE_DATABASE else "file",
        "database_configured": bool(DATABASE_URL),
    }
    
    if not USE_DATABASE:
        result["database_connected"] = False
        return result
    
    global _db_connection
    
    # Try to check the existing connection first
    if _db_connection is not None:
        try:
            with _db_connection.cursor() as cur:
                # Set statement timeout to 2 seconds
                cur.execute("SET statement_timeout = 2000")
                cur.execute("SELECT 1")
                cur.fetchone()
            result["database_connected"] = True
            return result
        except Exception:
            # Connection is dead/closed, try to reconnect once
            try:
                _db_connection.close()
            except Exception:
                pass
            _db_connection = None
    
    # Try to reconnect
    try:
        _db_connection = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        _db_connection.autocommit = True
        with _db_connection.cursor() as cur:
            cur.execute("SET statement_timeout = 2000")
            cur.execute("SELECT 1")
            cur.fetchone()
        result["database_connected"] = True
    except Exception as e:
        result["database_connected"] = False
        result["database_error"] = "connect_failed"
        result["database_error_kind"] = _classify_db_error(e)
    
    return result


def init_database():
    """Initialize database tables if DATABASE_URL is set."""
    if not USE_DATABASE:
        return
    
    conn = _get_db_connection()
    if not conn:
        return
    
    try:
        with conn.cursor() as cur:
            # Create events table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pilot_events (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMP WITH TIME ZONE NOT NULL,
                    session_id VARCHAR(80) NOT NULL,
                    event VARCHAR(50) NOT NULL,
                    utm_source VARCHAR(80),
                    utm_campaign VARCHAR(80),
                    utm_content VARCHAR(80),
                    meta JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            
            # Create feedback table (no name, age, or phone - only anonymous data)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pilot_feedback (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMP WITH TIME ZONE NOT NULL,
                    session_id VARCHAR(80) NOT NULL,
                    comfortable VARCHAR(20) NOT NULL,
                    liked VARCHAR(20) NOT NULL,
                    continue_pilot VARCHAR(20) NOT NULL,
                    issues TEXT,
                    language VARCHAR(20),
                    utm_source VARCHAR(80),
                    utm_campaign VARCHAR(80),
                    utm_content VARCHAR(80),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            
            # Create indexes for better query performance
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_session 
                ON pilot_events(session_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_ts 
                ON pilot_events(ts)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_session 
                ON pilot_feedback(session_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_ts 
                ON pilot_feedback(ts)
            """)
            
            logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, row: dict) -> None:
    _ensure_data_dir()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _read_events_from_db() -> list[dict]:
    """Read all events from database."""
    conn = _get_db_connection()
    if not conn:
        return []
    
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT ts, session_id, event, utm_source, utm_campaign, 
                       utm_content, meta
                FROM pilot_events
                ORDER BY ts
            """)
            rows = cur.fetchall()
            # Convert to dict and parse meta JSON
            result = []
            for row in rows:
                row_dict = dict(row)
                row_dict['ts'] = row_dict['ts'].isoformat() if row_dict['ts'] else ""
                if isinstance(row_dict.get('meta'), str):
                    try:
                        row_dict['meta'] = json.loads(row_dict['meta'])
                    except:
                        row_dict['meta'] = {}
                result.append(row_dict)
            return result
    except Exception as e:
        logger.error(f"Failed to read events from database: {e}")
        return []


def _read_feedback_from_db() -> list[dict]:
    """Read all feedback from database."""
    conn = _get_db_connection()
    if not conn:
        return []
    
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT ts, session_id, comfortable, liked, continue_pilot, 
                       issues, language, utm_source, utm_campaign, utm_content
                FROM pilot_feedback
                ORDER BY ts
            """)
            rows = cur.fetchall()
            # Convert to dict
            result = []
            for row in rows:
                row_dict = dict(row)
                row_dict['ts'] = row_dict['ts'].isoformat() if row_dict['ts'] else ""
                result.append(row_dict)
            return result
    except Exception as e:
        logger.error(f"Failed to read feedback from database: {e}")
        return []


def get_all_feedback_for_export() -> list[dict]:
    """Get all feedback records for CSV export.
    
    Excludes test data (session_ids starting with 'ps_TEST_').
    """
    if USE_DATABASE:
        all_feedback = _read_feedback_from_db()
    else:
        all_feedback = _read_jsonl(FEEDBACK_PATH)
    
    # Filter out test sessions
    return [
        fb for fb in all_feedback
        if not (fb.get("session_id") or "").startswith("ps_TEST_")
    ]


def admin_token_ok(token: str | None) -> bool:
    expected = (os.environ.get("PILOT_ADMIN_TOKEN") or "").strip()
    if not expected:
        return False
    return (token or "").strip() == expected


def record_event(
    session_id: str,
    event: str,
    utm_source: str = "",
    utm_campaign: str = "",
    utm_content: str = "",
    meta: dict | None = None,
) -> tuple[bool, str]:
    if event not in VALID_EVENTS:
        return False, "invalid_event"
    sid = (session_id or "").strip()
    if not sid or len(sid) > 80:
        return False, "invalid_session"
    safe_meta = {}
    if isinstance(meta, dict):
        for key, val in meta.items():
            if key in ("language",) and isinstance(val, str) and len(val) <= 20:
                safe_meta[key] = val
    
    ts = _now_iso()
    
    if USE_DATABASE:
        # Try to store in database with automatic reconnect on stale connection
        def db_insert(conn):
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO pilot_events 
                        (ts, session_id, event, utm_source, utm_campaign, utm_content, meta)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        ts, sid, event,
                        (utm_source or "")[:80],
                        (utm_campaign or "")[:80],
                        (utm_content or "")[:80],
                        json.dumps(safe_meta) if safe_meta else None
                    ))
                return True, ""
            except Exception as e:
                logger.error(f"Failed to insert event: {type(e).__name__}")
                raise
        
        success, error = _execute_with_retry("record_event", db_insert)
        if success:
            return True, ""
        # Fall through to file fallback
    
    # Fallback to file storage (either USE_DATABASE is False or database failed)
    row = {
        "ts": ts,
        "session_id": sid,
        "event": event,
        "utm_source": (utm_source or "")[:80],
        "utm_campaign": (utm_campaign or "")[:80],
        "utm_content": (utm_content or "")[:80],
        "meta": safe_meta,
    }
    _append_jsonl(EVENTS_PATH, row)
    logger.info(f"Event recorded to file fallback: {event}")
    return True, ""


def record_feedback(
    session_id: str,
    comfortable: str,
    liked: str,
    continue_pilot: str,
    issues: str = "",
    language: str = "",
    utm_source: str = "",
    utm_campaign: str = "",
    utm_content: str = "",
) -> tuple[bool, str]:
    sid = (session_id or "").strip()
    if not sid or len(sid) > 80:
        return False, "invalid_session"
    if comfortable not in FEEDBACK_COMFORT:
        return False, "invalid_comfortable"
    if liked not in FEEDBACK_LIKED:
        return False, "invalid_liked"
    if continue_pilot not in FEEDBACK_CONTINUE:
        return False, "invalid_continue"
    
    ts = _now_iso()
    issues_text = (issues or "").strip()[:2000]
    lang = (language or "")[:20]
    
    if USE_DATABASE:
        # Try to store in database with automatic reconnect on stale connection
        def db_insert(conn):
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO pilot_feedback 
                        (ts, session_id, comfortable, liked, continue_pilot, issues, 
                         language, utm_source, utm_campaign, utm_content)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        ts, sid, comfortable, liked, continue_pilot, issues_text,
                        lang,
                        (utm_source or "")[:80],
                        (utm_campaign or "")[:80],
                        (utm_content or "")[:80]
                    ))
                return True, ""
            except Exception as e:
                logger.error(f"Failed to insert feedback: {type(e).__name__}")
                raise
        
        success, error = _execute_with_retry("record_feedback", db_insert)
        if success:
            return True, ""
        # Fall through to file fallback
    
    # Fallback to file storage (either USE_DATABASE is False or database failed)
    row = {
        "ts": ts,
        "session_id": sid,
        "comfortable": comfortable,
        "liked": liked,
        "continue_pilot": continue_pilot,
        "issues": issues_text,
        "language": lang,
        "utm_source": (utm_source or "")[:80],
        "utm_campaign": (utm_campaign or "")[:80],
        "utm_content": (utm_content or "")[:80],
    }
    _append_jsonl(FEEDBACK_PATH, row)
    logger.info(f"Feedback recorded to file fallback: {sid}")
    return True, ""


def build_summary() -> dict:
    """Build summary of pilot data.
    
    Excludes test data (session_ids starting with 'ps_TEST_').
    """
    if USE_DATABASE:
        events = _read_events_from_db()
        feedback = _read_feedback_from_db()
    else:
        events = _read_jsonl(EVENTS_PATH)
        feedback = _read_jsonl(FEEDBACK_PATH)

    # Filter out test sessions
    events = [ev for ev in events if not (ev.get("session_id") or "").startswith("ps_TEST_")]
    feedback = [fb for fb in feedback if not (fb.get("session_id") or "").startswith("ps_TEST_")]

    sessions: dict[str, dict] = {}

    def session_bucket(sid: str) -> dict:
        if sid not in sessions:
            sessions[sid] = {
                "session_id": sid,
                "utm_source": "",
                "utm_campaign": "",
                "utm_content": "",
                "events": {
                    "site_visit": 0,
                    "anketa_started": 0,
                    "anketa_saved": 0,
                    "voice_started": 0,
                    "conversation": 0,
                },
                "last_event_ts": "",
                "feedback": [],
            }
        return sessions[sid]

    for ev in events:
        sid = (ev.get("session_id") or "").strip()
        if not sid:
            continue
        b = session_bucket(sid)
        name = ev.get("event", "")
        if name in b["events"]:
            b["events"][name] += 1
        ts = ev.get("ts", "")
        if ts >= b["last_event_ts"]:
            b["last_event_ts"] = ts
        if ev.get("utm_source"):
            b["utm_source"] = ev["utm_source"]
        if ev.get("utm_campaign"):
            b["utm_campaign"] = ev["utm_campaign"]
        if ev.get("utm_content"):
            b["utm_content"] = ev["utm_content"]

    for fb in feedback:
        sid = (fb.get("session_id") or "").strip()
        if not sid:
            continue
        b = session_bucket(sid)
        if fb.get("utm_source"):
            b["utm_source"] = fb["utm_source"]
        if fb.get("utm_campaign"):
            b["utm_campaign"] = fb["utm_campaign"]
        if fb.get("utm_content"):
            b["utm_content"] = fb["utm_content"]
        b["feedback"].append({
            "ts": fb.get("ts", ""),
            "session_id": sid,
            "comfortable": fb.get("comfortable", ""),
            "liked": fb.get("liked", ""),
            "continue_pilot": fb.get("continue_pilot", ""),
            "issues": fb.get("issues", ""),
        })

    families_map: dict[str, dict] = {}

    def family_bucket(key: str) -> dict:
        if key not in families_map:
            families_map[key] = {
                "family_key": key,
                "sessions": [],
                "events": {
                    "site_visit": 0,
                    "anketa_started": 0,
                    "anketa_saved": 0,
                    "voice_started": 0,
                    "conversation": 0,
                },
                "last_event_ts": "",
                "utm_source": "",
                "utm_campaign": "",
                "feedback": [],
            }
        return families_map[key]

    for sid, sess in sessions.items():
        content = (sess.get("utm_content") or "").strip()
        key = content if content else f"session:{sid[:12]}"
        fam = family_bucket(key)
        fam["sessions"].append(sid)
        for name, count in sess["events"].items():
            fam["events"][name] += count
        if sess["last_event_ts"] >= fam["last_event_ts"]:
            fam["last_event_ts"] = sess["last_event_ts"]
        if sess.get("utm_source"):
            fam["utm_source"] = sess["utm_source"]
        if sess.get("utm_campaign"):
            fam["utm_campaign"] = sess["utm_campaign"]
        fam["feedback"].extend(sess["feedback"])

    out = []
    for key in sorted(families_map.keys()):
        fam = families_map[key]
        ev = fam["events"]
        out.append({
            "family_key": fam["family_key"],
            "session_count": len(fam["sessions"]),
            "events": ev,
            "reached_conversation": ev["conversation"] > 0,
            "last_event_ts": fam["last_event_ts"],
            "utm_source": fam["utm_source"],
            "utm_campaign": fam["utm_campaign"],
            "feedback_count": len(fam["feedback"]),
            "feedback": fam["feedback"],
        })

    return {
        "generated_at": _now_iso(),
        "family_count": len(out),
        "families": out,
        "totals": {
            "events": len(events),
            "feedback": len(feedback),
        },
    }


def summary_html(summary: dict) -> str:
    rows = []
    for fam in summary.get("families", []):
        ev = fam["events"]
        fb_list = fam.get("feedback") or []
        fb_cell = "—"
        if fb_list:
            parts = []
            for fb in fb_list:
                # Show "—" for empty values (not asked)
                comfortable = fb.get('comfortable') or '—'
                liked = fb.get('liked') or '—'
                continue_pilot = fb.get('continue_pilot') or '—'
                parts.append(
                    f"<div class='fb'>"
                    f"<b>удобно:</b> {comfortable} · "
                    f"<b>понравилось:</b> {liked} · "
                    f"<b>продолжить:</b> {continue_pilot}"
                    f"{('<br><i>' + _escape(fb['issues']) + '</i>') if fb.get('issues') else ''}"
                    f"</div>"
                )
            fb_cell = "".join(parts)
        talk = "✅ да" if fam["reached_conversation"] else "—"
        rows.append(
            f"<tr>"
            f"<td><code>{_escape(fam['family_key'])}</code></td>"
            f"<td>{ev['site_visit']}</td>"
            f"<td>{ev['anketa_started']}</td>"
            f"<td>{ev['anketa_saved']}</td>"
            f"<td>{ev['voice_started']}</td>"
            f"<td>{talk}</td>"
            f"<td>{fb_cell}</td>"
            f"</tr>"
        )
    body = "\n".join(rows) if rows else "<tr><td colspan='7'>Пока нет данных</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>MetaPelet — пилот 5 семей</title>
<style>
body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #222; }}
table {{ border-collapse: collapse; width: 100%; max-width: 1100px; }}
th, td {{ border: 1px solid #ddd; padding: 10px; vertical-align: top; text-align: left; }}
th {{ background: #f5f5f5; }}
.fb {{ margin-bottom: 8px; font-size: 14px; }}
code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }}
</style></head><body>
<h1>MetaPelet — пилот (анонимно)</h1>
<p>Обновлено: {_escape(summary.get('generated_at', ''))} · 
событий: {summary.get('totals', {}).get('events', 0)} · 
отзывов: {summary.get('totals', {}).get('feedback', 0)}</p>
<table>
<thead><tr>
<th>Семья (utm_content)</th><th>Визит</th><th>Анкета начата</th>
<th>Анкета сохранена</th><th>Голос начат</th><th>Разговор</th><th>Отзыв</th>
</tr></thead>
<tbody>{body}</tbody>
</table>
<p style="margin-top:20px;color:#666;font-size:14px">
Семья = параметр <code>utm_content</code> в ссылке (f01…f05). Без него — отдельная строка по session.
</p>
</body></html>"""


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

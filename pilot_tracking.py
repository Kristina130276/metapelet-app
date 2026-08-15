"""Anonymous pilot funnel + feedback (5-family YouTube pilot). No PII."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("PILOT_DATA_DIR", "data"))
EVENTS_PATH = DATA_DIR / "pilot_events.jsonl"
FEEDBACK_PATH = DATA_DIR / "pilot_feedback.jsonl"

VALID_EVENTS = frozenset({
    "site_visit",
    "anketa_started",
    "anketa_saved",
    "voice_started",
    "conversation",
})

FEEDBACK_COMFORT = frozenset({"yes", "partial", "no"})
FEEDBACK_LIKED = frozenset({"yes", "partial", "no"})
FEEDBACK_CONTINUE = frozenset({"yes", "maybe", "no"})


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
    row = {
        "ts": _now_iso(),
        "session_id": sid,
        "event": event,
        "utm_source": (utm_source or "")[:80],
        "utm_campaign": (utm_campaign or "")[:80],
        "utm_content": (utm_content or "")[:80],
        "meta": safe_meta,
    }
    _append_jsonl(EVENTS_PATH, row)
    return True, ""


def record_feedback(
    session_id: str,
    comfortable: str,
    liked: str,
    continue_pilot: str,
    issues: str = "",
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
    row = {
        "ts": _now_iso(),
        "session_id": sid,
        "comfortable": comfortable,
        "liked": liked,
        "continue_pilot": continue_pilot,
        "issues": (issues or "").strip()[:2000],
        "utm_source": (utm_source or "")[:80],
        "utm_campaign": (utm_campaign or "")[:80],
        "utm_content": (utm_content or "")[:80],
    }
    _append_jsonl(FEEDBACK_PATH, row)
    return True, ""


def build_summary() -> dict:
    events = _read_jsonl(EVENTS_PATH)
    feedback = _read_jsonl(FEEDBACK_PATH)

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
                parts.append(
                    f"<div class='fb'>"
                    f"<b>удобно:</b> {fb['comfortable']} · "
                    f"<b>понравилось:</b> {fb['liked']} · "
                    f"<b>продолжить:</b> {fb['continue_pilot']}"
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

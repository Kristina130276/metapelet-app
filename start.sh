#!/usr/bin/env bash
set -euo pipefail
exec python -m gunicorn app:app --bind "0.0.0.0:${PORT:-5000}"

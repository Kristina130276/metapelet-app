# Launch safety: deployment conditions

This branch is not approval to deploy or to enable payments.

## Budget database

Set BUDGET_DB_PATH to a SQLite file on an already provisioned persistent Render disk. The default data/budget.sqlite3 survives process restarts on the same filesystem, but NOT deployments on ephemeral Render storage. Do not advertise this as a monetary hard cap. All workers must use the same file; multiple Render instances need a shared transactional database instead of independent disks. The database stores daily hashed user/IP counters and reply hashes, not conversation text. Unsalted hashes of IP addresses are pseudonymous, not anonymous.

Tested: atomic concurrent reservations, reload survival, exhausted TTS permits, and fail-closed storage failure. API failures keep their reserved reply quota, since a timeout does not prove the provider did not bill. Anthropic automatic retries are disabled and timeout is 20 seconds.

## Remaining release blockers

- Revoke exposed legacy Telegram credential through the owner account. Do not paste it into issues or chat.
- Confirm actual Render services, deploy branch, disk persistence, invoices and provider quotas.
- Add authenticated server sessions/invitations, ownership checks and subscription entitlement before a paid release.
- Implement provider spend/usage metering and month-level enforcement; current counters are daily request caps only.
- Review proxy trust: current IP header handling is not verified for the deployed path.
- Add deletion/expiry for conversations and correct privacy policy in all languages.
- Restrict diagnostics and review free-text analytics ingestion.
- Verify microphone, audio and stop controls on real supported mobile browsers.
- Test revised safety prompt against synthetic crisis and identity scenarios; a prompt alone is not a safety guarantee.

The latest main branch contact-email addition was preserved during rebase.

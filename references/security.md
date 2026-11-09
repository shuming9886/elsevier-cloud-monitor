# Security

## Treat the tracking URL as a secret

An Elsevier AuthorHub URL can reveal the article title, journal, author names, manuscript number, submission date, and review activity without an account login. Do not place a real URL in Git, an issue, a public log, a screenshot, or an example file. Redact the UUID in reports.

## Keep notification credentials outside the repository

Use an SMTP app password rather than the mailbox login password. The deployment environment file must be created outside the repository, transferred without printing its contents, installed as `/etc/elsevier-monitor.env`, and set to mode `600` owned by `root:root`. systemd injects those values into the unprivileged service; do not source the file in a shell. Never embed SMTP credentials in `SKILL.md`, Python source, shell history, or the systemd unit.

If the agent cannot accept a secret through a protected credential mechanism, have the user create or edit the environment file directly in their own terminal. Resume only after the user confirms the file exists; do not ask them to paste the secret into chat.

## Bound server access

Prefer an existing SSH key. Do not copy a private SSH key to the server. Confirm the hostname before changing packages or services. The installer supports only x86_64 Debian 12 or Ubuntu 22.04/24.04 with systemd as PID 1. The monitor runs as the dedicated `elsevier-monitor` user and must not disable the Chromium sandbox.

## Poll responsibly

The monitor rejects intervals below 300 seconds. Use 1800 seconds unless the user explicitly chooses a slower interval. A failed or invalid page fetch never replaces the last valid state. A page is valid only when every monitored field is present.

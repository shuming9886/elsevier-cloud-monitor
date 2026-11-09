---
name: elsevier-cloud-monitor
description: Deploy Elsevier submission monitoring to Linux servers.
version: 0.1.0
author: shuming9886, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  tags: [Elsevier, Monitoring, Deployment, Linux, SMTP]
---

# Elsevier Cloud Monitor

Deploy a deterministic Elsevier AuthorHub submission-status monitor to a user-controlled Linux cloud server. The coding agent installs and verifies the service; Python and systemd perform ongoing checks without model calls.

## When to Use

Use when a user asks Codex, Claude Code, Cursor, or another Agent Skills-compatible coding agent to deploy, update, diagnose, or remove an Elsevier submission monitor on a cloud server.

Do not use for local-computer monitoring, non-Elsevier publishers, servers without explicit user authorization, bypassing authentication, or centralized collection of other users' tracking URLs.

## Supported Target

- Debian 12 or Ubuntu 22.04/24.04
- x86_64/amd64 server
- systemd as PID 1
- root or passwordless `sudo`
- SSH key authentication
- Elsevier URLs on `https://track.authorhub.elsevier.com/`
- SMTP notifications using port 587 or 465

Stop rather than improvising on ARM, RPM-based distributions, Windows Server, a password-only SSH login, or a host without systemd. Explain the unsupported prerequisite.

## Packaged Files

- `scripts/monitor.py`: fetches the JavaScript page, requires all six fields, stores an atomic baseline, and sends change email.
- `scripts/install_server.py`: installs dependencies and the systemd service on the target server.
- `templates/monitor.env.example`: non-secret configuration template.
- `templates/elsevier-monitor.service`: unprivileged systemd unit.
- `templates/elsevier-monitor.logrotate`: 14-day log rotation with `copytruncate`.
- `references/security.md`: required secret-handling rules.
- `references/troubleshooting.md`: diagnosis, update, and removal procedures.

Resolve all paths relative to this `SKILL.md`; never assume a machine-specific checkout path.

## Required Inputs

Obtain only the missing items:

1. Complete Elsevier tracking URL.
2. SSH hostname or IP, user, and non-default port if applicable.
3. SMTP hostname and port.
4. Sender address and recipient address list.
5. Poll interval, default `1800` seconds.
6. Path to a private environment file prepared by the user.

Never ask for an SSH private key, mailbox password, SMTP app password, OTP, or tracking URL in a public issue. Do not ask the user to paste a secret into chat. If no protected credential mechanism exists, copy `templates/monitor.env.example` to a private local path, set mode `600`, and have the user edit it directly in their own terminal. Resume only after they confirm it is ready.

Read and follow `references/security.md` before handling the URL or environment file.

## Procedure

### 1. Validate the request and URL

Confirm that the user owns or is authorized to monitor the submission and server. Validate the private environment file without printing it:

```bash
python3 -c 'import sys; sys.path.insert(0,"<skill-root>/scripts"); from install_server import read_environment; from pathlib import Path; v=read_environment(Path("<private-env-file>")); print("URL format valid")'
```

Report only the host and a redacted UUID such as `656d…85a6`.

Done when the scheme, hostname, UUID, SMTP port, and poll interval are valid.

### 2. Perform a read-only server preflight

Use the user's SSH configuration and key. Check identity before changing anything:

```bash
ssh -p <port> <user>@<host> 'hostname; id; . /etc/os-release; printf "%s %s\n" "$ID" "$VERSION_ID"; uname -m; cat /proc/1/comm; command -v systemctl; df -h /; free -h'
```

Require Debian 12 or Ubuntu 22.04/24.04, x86_64, systemd as PID 1, at least 1 GiB free memory or swap, and at least 2 GiB free disk. Determine whether commands require `sudo`. Do not guess credentials or accept an unknown host identity silently.

Done when the exact target host and all prerequisites are verified.

### 3. Stage the Skill and private configuration

Create a unique staging directory with `mktemp -d`. Upload only the allowlisted files below. Do not print, read back, or include secret contents in command arguments or logs.

```bash
STAGE=$(ssh -p <port> <user>@<host> 'd=$(mktemp -d "$HOME/elsevier-monitor.XXXXXX"); chmod 700 "$d"; printf %s "$d"')
ssh -p <port> <user>@<host> "mkdir -p '$STAGE/scripts' '$STAGE/templates' '$STAGE/references'"
scp -P <port> <skill-root>/SKILL.md <skill-root>/requirements.txt <user>@<host>:"$STAGE/"
scp -P <port> <skill-root>/scripts/monitor.py <skill-root>/scripts/install_server.py <user>@<host>:"$STAGE/scripts/"
scp -P <port> <skill-root>/templates/elsevier-monitor.service <skill-root>/templates/elsevier-monitor.logrotate <skill-root>/templates/monitor.env.example <user>@<host>:"$STAGE/templates/"
scp -P <port> <skill-root>/references/security.md <skill-root>/references/troubleshooting.md <user>@<host>:"$STAGE/references/"
scp -P <port> <private-env-file> <user>@<host>:"$STAGE/monitor.env"
ssh -p <port> <user>@<host> "chmod 600 '$STAGE/monitor.env'"
```

Never upload `.git`, tests, state snapshots, logs, or unrelated project files.

Done when the staged allowlisted files and private environment file exist with appropriate permissions.

### 4. Install on the server

Run the packaged installer as root. `--test-mail` is appropriate when the user requested a working deployment and expects a test message:

```bash
ssh -t -p <port> <user>@<host> "sudo python3 '$STAGE/scripts/install_server.py' --env-file '$STAGE/monitor.env' --test-mail"
```

For a root SSH session, omit `sudo`. Do not replace the installer with model-generated shell commands unless diagnosing a verified installer defect.

The installer must stop any existing service before rewriting files, complete the seven stages, then restart the service.

Done when the installer exits zero.

### 5. Verify external state

Read back the exact installed targets; installer success alone is insufficient:

```bash
ssh -p <port> <user>@<host> 'sudo systemctl is-enabled elsevier-monitor.service; sudo systemctl is-active elsevier-monitor.service; sudo systemctl show elsevier-monitor.service -p User -p Group; sudo stat -c "%a %U:%G %n" /etc/elsevier-monitor.env; sudo python3 -c '\''import json; d=json.load(open("/var/lib/elsevier-monitor/state.json")); print({k:d.get(k) for k in ("reviews_completed","invitations_accepted","invitations_sent","last_activity","submission_status","status_state","last_check")})'\''; sudo journalctl -u elsevier-monitor.service --since "15 minutes ago" --no-pager'
```

Require:

- service is `enabled`;
- service is `active`;
- service user and group are `elsevier-monitor`;
- environment file mode is `600`;
- state contains all six monitored fields plus `last_check`;
- logs contain one complete successful observation;
- the first observation says it established a baseline or found no change;
- no secret or complete tracking URL appears in the report.

If `--test-mail` was used, ask the user to confirm receipt. SMTP acceptance is not proof of inbox delivery. If `--test-mail` was not used, report that notification delivery is unverified.

Done only when every item is verified or a specific blocker is reported.

### 6. Clean staging files

After verification, remove the remote staging directory. Keep the installed `/etc/elsevier-monitor.env`:

```bash
ssh -p <port> <user>@<host> "rm -rf '$STAGE'"
```

Do not delete installed state or logs.

Done when temporary secret-bearing files are gone.

## Installed Layout

```text
/opt/elsevier-monitor/monitor.py
/opt/elsevier-monitor/install_server.py
/opt/elsevier-monitor/.venv/
/etc/elsevier-monitor.env
/etc/systemd/system/elsevier-monitor.service
/etc/logrotate.d/elsevier-monitor
/var/lib/elsevier-monitor/state.json
/var/log/elsevier-monitor/monitor.log
```

The service runs as `elsevier-monitor`. systemd injects `/etc/elsevier-monitor.env`.

## Operational Commands

```bash
systemctl status elsevier-monitor --no-pager
journalctl -u elsevier-monitor -f
systemctl restart elsevier-monitor
systemctl stop elsevier-monitor
sudo python3 /opt/elsevier-monitor/install_server.py --env-file /etc/elsevier-monitor.env --exec /opt/elsevier-monitor/.venv/bin/python /opt/elsevier-monitor/monitor.py --test-page
```

Load `references/troubleshooting.md` for failed page checks, SMTP errors, safe updates, or removal.

## Pitfalls

- A tracking UUID is effectively a bearer secret; redact it in all reports.
- A successful HTTP response can still be an invalid or partial page. Never overwrite the last valid state after a failed parse.
- The initial observation is a baseline, not a change, and must not trigger an alert.
- Do not write secrets into Python, the systemd unit, shell history, Git, or chat.
- Do not source `/etc/elsevier-monitor.env` in a shell; use `--exec`.
- Google Chrome is a large download on low-bandwidth servers; allow sufficient time rather than launching duplicate installers.
- Do not claim email delivery from an SMTP success response; require user confirmation.
- Do not poll more frequently than every five minutes; the default is 30 minutes.

## Verification

Before reporting completion, run the packaged test suite locally:

```bash
python3 -m unittest discover -s <skill-root>/tests -v
python3 -m py_compile <skill-root>/scripts/monitor.py <skill-root>/scripts/install_server.py
```

A deployment is complete only when tests pass, the installed service is enabled and active as `elsevier-monitor`, the baseline can be parsed, a complete observation appears in logs, configuration permissions are `600`, and any requested test email is confirmed by the user.

# Troubleshooting

## Page test fails

Load the installed environment without shell evaluation:

```bash
sudo python3 /opt/elsevier-monitor/install_server.py --env-file /etc/elsevier-monitor.env --exec /opt/elsevier-monitor/.venv/bin/python /opt/elsevier-monitor/monitor.py --test-page
```

- `Elsevier 未找到该稿件`: reopen the original tracking email and verify the complete URL.
- Timeout or network error: test outbound HTTPS and retry later; do not delete the state file.
- Incomplete fields: Elsevier may have changed the page structure. Stop the service and update the parser before resuming.

## Service fails

```bash
systemctl status elsevier-monitor --no-pager
journalctl -u elsevier-monitor --since "30 minutes ago" --no-pager
```

Check that `/etc/elsevier-monitor.env` exists with mode `600`, the service user is `elsevier-monitor`, Chrome exists at the path stored in `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`, and the virtual environment exists under `/opt/elsevier-monitor/.venv`. A dead browser should make the daemon exit so systemd can restart it.

## Email fails

Use an app password, not the mailbox login password. Port 587 uses STARTTLS; port 465 uses implicit TLS. Some cloud providers block outbound SMTP port 25, so this skill does not use it by default.

Retest without sourcing the environment file:

```bash
sudo python3 /opt/elsevier-monitor/install_server.py --env-file /etc/elsevier-monitor.env --exec /opt/elsevier-monitor/.venv/bin/python /opt/elsevier-monitor/monitor.py --test-mail
```

SMTP acceptance proves the server accepted the message, not that it reached the inbox. Ask the user to confirm receipt.

## Safe update

Stop is handled by the installer. Upload the allowlisted skill files to a unique staging directory created with `mktemp -d`, then rerun `scripts/install_server.py` with the existing private environment file. The installer stops the service before rewriting files and the baseline check, then restarts it. The baseline file is preserved under `/var/lib/elsevier-monitor/state.json`.

## Uninstall

Obtain explicit user approval, then stop and disable the service before removing files:

```bash
systemctl disable --now elsevier-monitor.service
rm -f /etc/systemd/system/elsevier-monitor.service /etc/logrotate.d/elsevier-monitor
systemctl daemon-reload
rm -rf /opt/elsevier-monitor
```

Do not remove `/etc/elsevier-monitor.env`, `/var/lib/elsevier-monitor`, `/var/log/elsevier-monitor`, or the `elsevier-monitor` user unless the user separately asks to delete credentials, state, logs, and the service account.

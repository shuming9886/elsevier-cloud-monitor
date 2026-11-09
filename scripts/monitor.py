#!/usr/bin/env python3
"""Deterministic Elsevier AuthorHub status monitor."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import smtplib
import sys
import time
import uuid
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

FIELDS = (
    "reviews_completed",
    "invitations_accepted",
    "invitations_sent",
    "last_activity",
    "submission_status",
    "status_state",
)
LABELS = {
    "reviews_completed": "Reviews completed",
    "invitations_accepted": "Review invitations accepted",
    "invitations_sent": "Review invitations sent",
    "last_activity": "Last review activity",
    "submission_status": "Submission status",
    "status_state": "Status state",
}


class InvalidPageError(RuntimeError):
    """The response is not a valid Elsevier tracking observation."""


class BrowserUnavailableError(RuntimeError):
    """The reusable browser died and the service must be restarted."""


def validate_target_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "track.authorhub.elsevier.com":
        raise ValueError("TARGET_URL 必须使用 https://track.authorhub.elsevier.com/")
    values = parse_qs(parsed.query).get("uuid", [])
    if len(values) != 1:
        raise ValueError("TARGET_URL 必须包含一个 uuid 参数")
    try:
        uuid.UUID(values[0])
    except ValueError as exc:
        raise ValueError("TARGET_URL 的 uuid 无效") from exc
    return url


def parse_body(body_text: str, status_state: str = "") -> dict[str, str]:
    lowered = body_text.lower().replace("'", "’")
    if "article you’re looking for has not been found" in lowered:
        raise InvalidPageError("Elsevier 未找到该稿件")

    patterns = {
        "reviews_completed": r"Reviews completed:\s*([^\s]+)",
        "invitations_accepted": r"Review invitations accepted:\s*([^\s]+)",
        "invitations_sent": r"Review invitations sent:\s*([^\s]+)",
        "last_activity": r"Last review activity:\s*(.+?)(?:\n|$)",
        "submission_status": r"Submission status and date\s*\n\s*([^\n]+)",
    }
    result = {}
    missing = []
    for field, pattern in patterns.items():
        match = re.search(pattern, body_text, flags=re.IGNORECASE)
        if not match or not match.group(1).strip():
            missing.append(field)
        else:
            result[field] = match.group(1).strip()
    if missing:
        raise InvalidPageError("页面缺少完整审稿状态字段: " + ", ".join(missing))
    result["status_state"] = status_state.strip() or result["submission_status"]
    return result


def compare_states(previous: dict[str, str], current: dict[str, str]) -> dict[str, tuple[str, str]]:
    if not all(field in previous for field in FIELDS):
        return {}
    return {
        field: (previous[field], current[field])
        for field in FIELDS
        if previous[field] != current[field]
    }


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取状态文件 {path}: {exc}") from exc


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def fetch_review_data(url: str, browser=None) -> dict[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少 Playwright，请先安装 requirements.txt") from exc

    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")

    def launch(playwright):
        kwargs = {"headless": True}
        if executable:
            kwargs["executable_path"] = executable
        return playwright.chromium.launch(**kwargs)

    def extract(page):
        page.goto(url, wait_until="networkidle", timeout=45_000)
        page.wait_for_timeout(2_000)
        body = page.inner_text("body")
        status = page.evaluate(
            "() => { const e=document.querySelector('.statusstate div'); "
            "return e ? e.innerText.trim() : ''; }"
        )
        return parse_body(body, status)

    if browser is not None:
        page = browser.new_page()
        try:
            return extract(page)
        finally:
            page.close()

    with sync_playwright() as playwright:
        instance = launch(playwright)
        try:
            page = instance.new_page()
            return extract(page)
        finally:
            instance.close()


def configured_recipients() -> list[str]:
    return [item.strip() for item in os.environ.get("TO_EMAILS", "").split(",") if item.strip()]


def send_email(subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    recipients = configured_recipients()
    if not all((host, user, password, recipients)):
        raise RuntimeError("SMTP 配置不完整")

    message = MIMEMultipart("alternative")
    message["From"] = user
    message["To"] = ", ".join(recipients)
    message["Subject"] = Header(subject, "utf-8")
    message["X-Mailer"] = "Elsevier Submission Monitor"
    message.attach(MIMEText(body, "plain", "utf-8"))

    timeout = 30
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
            server.login(user, password)
            server.sendmail(user, recipients, message.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(user, recipients, message.as_string())


def notification_body(url: str, changes: dict, current: dict) -> str:
    lines = [
        "Elsevier AuthorHub 稿件状态发生变化。",
        "",
        f"监测页面: {url}",
        f"检测时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "─── 变动详情 ───",
    ]
    for field in FIELDS:
        if field in changes:
            old, new = changes[field]
            lines.append(f"★ {LABELS[field]}: {old} → {new}")
        else:
            lines.append(f"  {LABELS[field]}: {current[field]}")
    return "\n".join(lines)


def build_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        descriptor = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        os.close(descriptor)
    logger = logging.getLogger("elsevier-monitor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def check_once(url: str, state_path: Path, logger: logging.Logger, browser=None, notify=True) -> bool:
    logger.info("正在抓取 Elsevier 页面")
    if browser is not None and not browser.is_connected():
        raise BrowserUnavailableError("浏览器进程已退出")
    try:
        current = fetch_review_data(url, browser=browser)
    except Exception as exc:
        if browser is not None and not browser.is_connected():
            raise BrowserUnavailableError("浏览器进程在抓取期间退出") from exc
        logger.error("页面抓取失败，保留上次有效状态: %s", exc)
        return False

    for field in FIELDS:
        logger.info("%s: %s", field, current[field])
    previous = load_state(state_path)
    changes = compare_states(previous, current)

    if changes:
        logger.warning("检测到变化: %s", ", ".join(changes))
        if notify:
            try:
                send_email("【稿件状态变动提醒】Elsevier AuthorHub", notification_body(url, changes, current))
                logger.info("变化通知邮件已发送")
            except Exception as exc:
                logger.error("通知邮件发送失败，暂不更新状态以便下次重试: %s", exc)
                return False
    elif previous:
        logger.info("状态无变化")
    else:
        logger.info("首次有效检测，已建立基线且不发送变化通知")

    state = dict(current)
    state["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state_path, state)
    return True


def settings():
    url = validate_target_url(os.environ.get("TARGET_URL", ""))
    interval = int(os.environ.get("CHECK_INTERVAL", "1800"))
    if interval < 300:
        raise ValueError("CHECK_INTERVAL 不得小于 300 秒")
    state_path = Path(os.environ.get("STATE_FILE", "/var/lib/elsevier-monitor/state.json"))
    log_path = Path(os.environ.get("LOG_FILE", "/var/log/elsevier-monitor/monitor.log"))
    return url, interval, state_path, log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Elsevier AuthorHub 稿件状态监控")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--test-page", action="store_true")
    parser.add_argument("--test-mail", action="store_true")
    args = parser.parse_args()

    try:
        url, interval, state_path, log_path = settings()
        logger = build_logger(log_path)
    except Exception as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    if args.test_page:
        try:
            data = fetch_review_data(url)
        except Exception as exc:
            logger.error("页面测试失败: %s", exc)
            return 1
        for field in FIELDS:
            print(f"{field}={data[field]}")
        return 0

    if args.test_mail:
        try:
            data = fetch_review_data(url)
            body = notification_body(url, {}, data).replace("发生变化", "测试成功")
            send_email("【测试邮件】Elsevier 稿件监控配置成功", body)
            logger.info("测试邮件已由 SMTP 服务器接受")
            return 0
        except Exception as exc:
            logger.error("测试邮件失败: %s", exc)
            return 1

    if not args.daemon:
        return 0 if check_once(url, state_path, logger) else 1

    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logger.info("启动守护进程，检测间隔 %d 秒", interval)

    try:
        from playwright.sync_api import sync_playwright
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        with sync_playwright() as playwright:
            kwargs = {"headless": True}
            if executable:
                kwargs["executable_path"] = executable
            browser = playwright.chromium.launch(**kwargs)
            try:
                while not stop.is_set():
                    check_once(url, state_path, logger, browser=browser)
                    stop.wait(interval)
            finally:
                browser.close()
    except Exception as exc:
        logger.error("守护进程异常退出: %s", exc)
        return 1
    logger.info("监控服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Install the monitor on a supported Debian/Ubuntu cloud server."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_ENV = (
    "TARGET_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASS",
    "TO_EMAILS",
)
OPTIONAL_ENV = {"CHECK_INTERVAL"}
MANAGED_ENV = {
    "STATE_FILE": "/var/lib/elsevier-monitor/state.json",
    "LOG_FILE": "/var/log/elsevier-monitor/monitor.log",
}
ALLOWED_ENV = set(REQUIRED_ENV) | OPTIONAL_ENV | set(MANAGED_ENV) | {"PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"}
SERVICE_NAME = "elsevier-monitor.service"
SERVICE_USER = "elsevier-monitor"
SERVICE_GROUP = "elsevier-monitor"
GOOGLE_KEY_URL = "https://dl.google.com/linux/linux_signing_key.pub"
GOOGLE_KEY_FINGERPRINT = "EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796"


def read_environment(path: Path) -> dict[str, str]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"无法读取环境文件: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError("环境文件必须是普通文件且不能是符号链接")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o037:
        raise ValueError("环境文件权限过宽；仅允许所有者读写及服务组只读")

    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"环境文件第 {number} 行缺少等号")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or key not in ALLOWED_ENV:
            raise ValueError(f"环境文件第 {number} 行包含不允许的键")
        if key in values:
            raise ValueError(f"环境文件第 {number} 行重复定义 {key}")
        if value.startswith('"'):
            if len(value) < 2 or not value.endswith('"'):
                raise ValueError(f"环境文件第 {number} 行引号不匹配")
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        values[key] = value

    missing = [key for key in REQUIRED_ENV if not values.get(key)]
    if missing:
        raise ValueError("环境配置缺少: " + ", ".join(missing))
    try:
        interval = int(values.get("CHECK_INTERVAL", "1800"))
    except ValueError as exc:
        raise ValueError("CHECK_INTERVAL 必须是整数秒") from exc
    if interval < 300:
        raise ValueError("CHECK_INTERVAL 不得小于 300 秒")
    values["CHECK_INTERVAL"] = str(interval)
    try:
        smtp_port = int(values["SMTP_PORT"])
    except ValueError as exc:
        raise ValueError("SMTP_PORT 必须是整数") from exc
    if smtp_port not in {465, 587}:
        raise ValueError("SMTP_PORT 仅支持 465 或 587")
    values["SMTP_PORT"] = str(smtp_port)

    from monitor import validate_target_url
    validate_target_url(values["TARGET_URL"])
    return values


def runtime_environment(values: dict[str, str], chrome_path: str) -> dict[str, str]:
    result = dict(values)
    result.update(MANAGED_ENV)
    result["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"] = chrome_path
    return result


def quote_environment(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_environment(path: Path, values: dict[str, str]) -> None:
    content = "".join(f"{key}={quote_environment(value)}\n" for key, value in values.items())
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)
    shutil.chown(path, user="root", group="root")


def run(*command: str, env=None) -> None:
    subprocess.run(command, check=True, env=env)


def run_optional(*command: str, env=None) -> bool:
    return subprocess.run(command, check=False, env=env).returncode == 0


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def is_systemd_pid_one() -> bool:
    try:
        return Path("/proc/1/comm").read_text(encoding="utf-8").strip() == "systemd"
    except OSError:
        return False


def require_supported_server() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("安装程序必须以 root 运行")
    if sys.platform != "linux":
        raise RuntimeError("仅支持 Linux 云服务器")
    if platform.machine() not in {"x86_64", "amd64"}:
        raise RuntimeError("首版仅支持 x86_64/amd64 服务器")
    release = read_os_release()
    supported = {("debian", "12"), ("ubuntu", "22.04"), ("ubuntu", "24.04")}
    if (release.get("ID"), release.get("VERSION_ID")) not in supported:
        raise RuntimeError("仅支持 Debian 12 或 Ubuntu 22.04/24.04")
    if not is_systemd_pid_one():
        raise RuntimeError("systemd 必须是 PID 1")
    for command in ("apt-get", "systemctl"):
        if not shutil.which(command):
            raise RuntimeError(f"缺少 {command}；仅支持 Debian/Ubuntu + systemd")


def verify_google_key_fingerprint(path: Path) -> None:
    output = subprocess.check_output(
        ["gpg", "--batch", "--show-keys", "--with-colons", str(path)], text=True
    )
    fingerprints = {line.split(":")[9].upper() for line in output.splitlines() if line.startswith("fpr:")}
    if GOOGLE_KEY_FINGERPRINT not in fingerprints:
        raise RuntimeError("Google Linux 签名密钥指纹不匹配")


def resolved_chrome_path() -> str | None:
    for command in ("google-chrome", "google-chrome-stable"):
        found = shutil.which(command)
        if found:
            return str(Path(found).resolve())
    return None


def install_chrome() -> str:
    existing = resolved_chrome_path()
    if existing:
        return existing
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        armored = temp / "google-linux-signing-key.pub"
        keyring = temp / "google-chrome.gpg"
        repository = temp / "google-chrome.list"
        run("curl", "-fsSL", "--retry", "5", "-o", str(armored), GOOGLE_KEY_URL)
        verify_google_key_fingerprint(armored)
        run("gpg", "--batch", "--yes", "--dearmor", "--output", str(keyring), str(armored))
        run("install", "-m", "0644", str(keyring), "/usr/share/keyrings/google-chrome.gpg")
        repository.write_text(
            "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] "
            "https://dl.google.com/linux/chrome/deb/ stable main\n",
            encoding="utf-8",
        )
        run("install", "-m", "0644", str(repository), "/etc/apt/sources.list.d/google-chrome.list")
        run("apt-get", "update")
        run("apt-get", "install", "-y", "google-chrome-stable")
    installed = resolved_chrome_path()
    if not installed:
        raise RuntimeError("Google Chrome 安装后仍不可用")
    return installed


def ensure_service_account() -> None:
    if not run_optional("getent", "group", SERVICE_GROUP):
        run("groupadd", "--system", SERVICE_GROUP)
    if not run_optional("id", "-u", SERVICE_USER):
        run(
            "useradd", "--system", "--gid", SERVICE_GROUP, "--home-dir", "/var/lib/elsevier-monitor",
            "--shell", "/usr/sbin/nologin", SERVICE_USER,
        )


def install(args) -> None:
    require_supported_server()
    root = Path(__file__).resolve().parents[1]
    source_monitor = root / "scripts" / "monitor.py"
    source_installer = root / "scripts" / "install_server.py"
    source_requirements = root / "requirements.txt"
    service_template = root / "templates" / "elsevier-monitor.service"
    logrotate_template = root / "templates" / "elsevier-monitor.logrotate"
    for path in (source_monitor, source_installer, source_requirements, service_template, logrotate_template):
        if not path.exists():
            raise RuntimeError(f"Skill 文件不完整: {path}")

    source_values = read_environment(args.env_file)
    print("[1/7] 安装系统依赖")
    environment = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    run("apt-get", "update", env=environment)
    run("apt-get", "install", "-y", "python3-venv", "curl", "ca-certificates", "gnupg", env=environment)
    chrome_path = install_chrome()
    values = runtime_environment(source_values, chrome_path)
    ensure_service_account()

    service_path = Path("/etc/systemd/system/elsevier-monitor.service")
    service_existed = service_path.exists()
    run_optional("systemctl", "stop", SERVICE_NAME)
    installation_succeeded = False
    try:
        print("[2/7] 安装监控程序")
        app = Path("/opt/elsevier-monitor")
        state_dir = Path("/var/lib/elsevier-monitor")
        log_dir = Path("/var/log/elsevier-monitor")
        app.mkdir(parents=True, exist_ok=True)
        app.chmod(0o755)
        for directory in (state_dir, log_dir):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o750)
            shutil.chown(directory, user=SERVICE_USER, group=SERVICE_GROUP)
        log_path = log_dir / "monitor.log"
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        os.close(descriptor)
        log_path.chmod(0o640)
        shutil.chown(log_path, user=SERVICE_USER, group=SERVICE_GROUP)
        shutil.copy2(source_monitor, app / "monitor.py")
        shutil.copy2(source_installer, app / "install_server.py")
        shutil.copy2(source_requirements, app / "requirements.txt")
        (app / "monitor.py").chmod(0o755)
        (app / "install_server.py").chmod(0o755)

        print("[3/7] 创建 Python 虚拟环境")
        venv = app / ".venv"
        if not venv.exists():
            run("python3", "-m", "venv", str(venv))
        run(str(venv / "bin" / "pip"), "install", "--disable-pip-version-check", "-r", str(app / "requirements.txt"))

        print("[4/7] 写入受保护配置")
        write_environment(Path("/etc/elsevier-monitor.env"), values)
        shutil.copy2(service_template, service_path)
        shutil.copy2(logrotate_template, "/etc/logrotate.d/elsevier-monitor")
        service_path.chmod(0o644)
        Path("/etc/logrotate.d/elsevier-monitor").chmod(0o644)
        run("systemctl", "daemon-reload")

        process_env = dict(os.environ, **values)
        print("[5/7] 抓取页面并建立初始基线")
        run("runuser", "-u", SERVICE_USER, "--", str(venv / "bin" / "python"), str(app / "monitor.py"), env=process_env)

        print("[6/7] 启用 systemd 服务")
        run("systemctl", "enable", SERVICE_NAME)

        if args.test_mail:
            print("[7/7] 发送测试邮件")
            run("runuser", "-u", SERVICE_USER, "--", str(venv / "bin" / "python"), str(app / "monitor.py"), "--test-mail", env=process_env)
        else:
            print("[7/7] 跳过 SMTP 测试（未指定 --test-mail；通知配置尚未验证）")
        installation_succeeded = True
    finally:
        if service_path.exists() or service_existed:
            run_optional("systemctl", "daemon-reload")
            run_optional("systemctl", "restart", SERVICE_NAME)

    run("systemctl", "is-active", "--quiet", SERVICE_NAME)
    if args.test_mail:
        print("安装完成：服务运行中，SMTP 服务器已接受测试邮件；请确认收件箱送达")
    else:
        print("安装完成：服务运行中；未运行 SMTP 测试，通知可用性尚未验证")


def exec_with_environment(env_file: Path, command: list[str]) -> None:
    if not command:
        raise ValueError("--exec 后必须提供命令")
    values = read_environment(env_file)
    os.execvpe(command[0], command, dict(os.environ, **values))


def main() -> int:
    parser = argparse.ArgumentParser(description="部署 Elsevier 云服务器监控")
    parser.add_argument("--env-file", type=Path, required=True, help="本地服务器上的环境配置文件")
    parser.add_argument("--test-mail", action="store_true", help="安装后发送一封测试邮件")
    parser.add_argument("--exec", dest="exec_command", nargs=argparse.REMAINDER, help="安全加载环境后直接执行命令")
    args = parser.parse_args()
    try:
        if args.exec_command is not None:
            exec_with_environment(args.env_file, args.exec_command)
        else:
            install(args)
        return 0
    except Exception as exc:
        print(f"安装失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

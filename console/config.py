"""Console settings from the environment; defaults match the container layout in roles/console_service."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    users_file: str
    registry_dir: str
    db_path: str
    backup_dir: str
    backup_keep: int
    subs_data_dir: str
    subs_access_db: str
    public_base_url: str
    offline_minutes: int
    allowed_hosts: tuple
    auth_mode: str = "none"


def from_env(env=None):
    env = os.environ if env is None else env
    return Settings(
        users_file=env.get("CONSOLE_USERS_FILE", "/data/users.json"),
        registry_dir=env.get("CONSOLE_REGISTRY_DIR", "/registry"),
        db_path=env.get("CONSOLE_DB", "/db/console.sqlite"),
        backup_dir=env.get("CONSOLE_BACKUP_DIR", "/db/backup"),
        backup_keep=int(env.get("CONSOLE_BACKUP_KEEP", "14")),
        subs_data_dir=env.get("CONSOLE_SUBS_DATA_DIR", "/subs-data"),
        subs_access_db=env.get("CONSOLE_SUBS_ACCESS_DB", "/subs-db/access.sqlite"),
        public_base_url=env.get("CONSOLE_PUBLIC_BASE_URL", "").rstrip("/"),
        offline_minutes=int(env.get("CONSOLE_OFFLINE_MINUTES", "15")),
        # The admin pages are only published on 127.0.0.1; refusing other Host headers blocks DNS rebinding.
        allowed_hosts=tuple(h.strip() for h in env.get("CONSOLE_ALLOWED_HOSTS", "127.0.0.1:8200,localhost:8200").split(",")
                            if h.strip()),
        auth_mode=env.get("CONSOLE_AUTH_MODE", "none"),
    )


@dataclass(frozen=True)
class StatusSettings:
    """Node status page (plan-node-status-page §3); group_vars/all/status.yml sets these through compose."""
    enabled: bool
    probe_file: str
    xray: str
    workdir: str
    check_url: str
    check_status: int
    interval: int
    timeout: int
    fail_count: int
    recover_count: int
    utc_offset_hours: float
    tz_label: str
    show_days: int
    raw_days: int
    keep_days: int
    base_port: int


def status_from_env(env=None):
    env = os.environ if env is None else env
    return StatusSettings(
        enabled=env.get("STATUS_ENABLED", "false").lower() == "true",
        probe_file=env.get("STATUS_PROBE_FILE", "/run/probe/probe.json"),
        xray=env.get("STATUS_XRAY", "/usr/local/bin/xray"),
        workdir=env.get("STATUS_WORKDIR", "/tmp"),
        check_url=env.get("STATUS_CHECK_URL", "http://cp.cloudflare.com/generate_204"),
        check_status=int(env.get("STATUS_CHECK_STATUS", "204")),
        interval=int(env.get("STATUS_INTERVAL", "60")),
        timeout=int(env.get("STATUS_TIMEOUT", "10")),
        fail_count=int(env.get("STATUS_FAIL_COUNT", "3")),
        recover_count=int(env.get("STATUS_RECOVER_COUNT", "2")),
        utc_offset_hours=float(env.get("STATUS_UTC_OFFSET_HOURS", "8")),
        tz_label=env.get("STATUS_TZ_LABEL", "北京时间"),
        show_days=int(env.get("STATUS_SHOW_DAYS", "90")),
        raw_days=int(env.get("STATUS_RAW_DAYS", "30")),
        keep_days=int(env.get("STATUS_KEEP_DAYS", "400")),
        base_port=int(env.get("STATUS_BASE_PORT", "20000")),
    )

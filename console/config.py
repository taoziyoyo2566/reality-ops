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
    )

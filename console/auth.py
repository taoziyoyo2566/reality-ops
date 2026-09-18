"""Who is acting (plan-console-phase2 §3.6).

There is no login yet: the admin pages are only published on the host's 127.0.0.1 and reached through SSH, so every
request comes from the local administrator. The mode is a setting so that an OIDC mode (authentik) can be added
later without touching the pages: they ask for an actor and its role, never for a login mechanism.
"""
from dataclasses import dataclass

MODES = ("none",)


@dataclass(frozen=True)
class Actor:
    name: str
    role: str            # "admin" or "user"
    subject: str = ""    # the OIDC subject once logins exist; matches users.oidc_subject


LOCAL_ADMIN = Actor("本机管理员", "admin")


def check_mode(mode):
    if mode not in MODES:
        raise SystemExit(f"CONSOLE_AUTH_MODE={mode!r} is not supported; only 'none' exists (OIDC is reserved)")


def actor_for(request, mode):
    """The actor behind a request. Only the loopback administrator exists in mode `none`."""
    return LOCAL_ADMIN

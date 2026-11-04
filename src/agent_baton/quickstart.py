"""`baton quickstart`: a local single-user server for trying baton out.

Everything lives in a per-user data directory. Tokens are stored hashed in the
database, so the quickstart token is also kept in a file only the user can read,
next to the database, to print it again on the next run.
"""

from __future__ import annotations

import getpass
import ipaddress
import os
import re
import sys
from pathlib import Path

from .schema import NAME_RE, ValidationError
from .store import Store

DB_NAME = "quickstart.db"
TOKEN_NAME = "quickstart.token"


def data_dir() -> Path:
    """Per-user data directory: %LOCALAPPDATA%\\baton on Windows, else XDG."""
    if override := os.environ.get("BATON_DATA_DIR"):
        return Path(override)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "baton"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "baton"


def member_name(raw: str | None = None) -> str:
    """The OS user name, squeezed into NAME_RE (`[a-z][a-z0-9_-]{0,31}`)."""
    if raw is None:
        try:
            raw = getpass.getuser()
        except Exception:
            raw = ""
    name = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-_")
    if name and not name[0].isalpha():
        name = "u" + name
    name = name[:32].rstrip("-_")
    return name if NAME_RE.match(name) else "me"


def is_loopback(host: str) -> bool:
    """IPv4 loopback only: the server socket is IPv4."""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_loopback


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)  # in case the file already existed with wider bits


def ensure_member(store: Store, name: str, token_file: Path) -> str:
    """Return a working token for `name`, creating or rotating it if needed."""
    if token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
        if store.member_for_token(token) == name:
            return token
    # Unknown member, or the saved token is lost: (re)issue one. This is the
    # quickstart's own database, so rotating its token loses nothing else.
    store.remove_member(name)
    token = store.add_member(name)
    _write_private(token_file, token + "\n")
    return token


def setup(directory: Path, name: str) -> tuple[Path, str]:
    """Create the data dir, database and member. Return (db path, token)."""
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / DB_NAME
    store = Store(str(db))
    try:
        token = ensure_member(store, name, directory / TOKEN_NAME)
    finally:
        store.close()
    return db, token


def env_lines(url: str, token: str) -> str:
    return (
        "# bash / zsh\n"
        f"export BATON_URL={url}\n"
        f"export BATON_TOKEN={token}\n"
        "\n"
        "# PowerShell\n"
        f'$env:BATON_URL = "{url}"\n'
        f'$env:BATON_TOKEN = "{token}"\n'
    )


def check_host(host: str) -> None:
    if not is_loopback(host):
        raise ValidationError(
            f"quickstart only binds to loopback (127.0.0.1 or localhost), not {host!r};"
            " use `baton serve` with TLS for a shared server"
        )

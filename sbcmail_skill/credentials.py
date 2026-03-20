"""Local credential persistence for SBCMailbox.

Stores credentials in ~/.sbcmail/<agent_id>.json so agents don't
need to re-register on every restart.
"""

import json
import os
from pathlib import Path
from typing import Optional


def _cred_dir() -> Path:
    return Path.home() / ".sbcmail"


def _cred_path(agent_id: str) -> Path:
    return _cred_dir() / f"{agent_id}.json"


def load_credentials(agent_id: str) -> Optional[dict]:
    """Load saved credentials for an agent, or return None."""
    path = _cred_path(agent_id)
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)


def save_credentials(agent_id: str, creds: dict) -> None:
    """Save credentials to ~/.sbcmail/<agent_id>.json."""
    d = _cred_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _cred_path(agent_id)
    with open(path, "w") as f:
        json.dump(creds, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_credentials(agent_id: str) -> None:
    """Remove saved credentials."""
    path = _cred_path(agent_id)
    if path.exists():
        path.unlink()

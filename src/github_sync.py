"""
Syncs runtime-written data files back to GitHub immediately.
Used when the app logs a trade or watchlist change on Streamlit Cloud.
"""
import os
import json
import base64
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

REPO = "finnmulvihill11/stock_model"
API_BASE = "https://api.github.com"
DATA_DIR = Path(__file__).parent.parent / "data"

SYNC_FILES = [
    "data/budget.json",
    "data/watchlist.json",
]


def _get_token() -> str:
    return os.getenv("GH_PAT", "")


def _get_file_sha(path: str, token: str) -> str | None:
    url = f"{API_BASE}/repos/{REPO}/contents/{path}"
    resp = requests.get(url, headers={"Authorization": f"token {token}"}, timeout=10)
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None


def _push_file(local_path: Path, repo_path: str, token: str, message: str) -> bool:
    if not local_path.exists():
        return False

    content = base64.b64encode(local_path.read_bytes()).decode()
    sha = _get_file_sha(repo_path, token)

    payload = {"message": message, "content": content, "branch": "master"}
    if sha:
        payload["sha"] = sha

    url = f"{API_BASE}/repos/{REPO}/contents/{repo_path}"
    resp = requests.put(url, json=payload,
                        headers={"Authorization": f"token {token}"}, timeout=15)
    return resp.status_code in (200, 201)


def sync_budget() -> bool:
    token = _get_token()
    if not token:
        return False
    path = DATA_DIR / "budget.json"
    return _push_file(path, "data/budget.json", token, "sync: budget update from app")


def sync_watchlist() -> bool:
    token = _get_token()
    if not token:
        return False
    path = DATA_DIR / "watchlist.json"
    return _push_file(path, "data/watchlist.json", token, "sync: watchlist update from app")


def sync_config() -> bool:
    token = _get_token()
    if not token:
        return False
    root = Path(__file__).parent.parent
    return _push_file(root / "config.yaml", "config.yaml", token, "sync: portfolio holdings update from app")

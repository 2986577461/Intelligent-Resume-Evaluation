"""
files/store.py — 按 user_id 存储最新一个文件（避免查 DB）

_user_latest_file[user_id] = { file_id, name, text }
"""

_user_latest_file: dict[str, dict] = {}


def set_user_file(user_id: str, file_id: str, name: str, text: str):
    _user_latest_file[user_id] = {"file_id": file_id, "name": name, "text": text}


def get_user_file(user_id: str) -> dict | None:
    return _user_latest_file.get(user_id)


def remove_user_file(user_id: str):
    _user_latest_file.pop(user_id, None)

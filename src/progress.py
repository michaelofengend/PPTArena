import threading
from typing import List, Dict

_lock = threading.Lock()
_logs: Dict[str, List[str]] = {}

def start(request_id: str) -> None:
    with _lock:
        _logs.setdefault(request_id, [])

def append(request_id: str, message: str) -> None:
    if not request_id:
        return
    with _lock:
        _logs.setdefault(request_id, []).append(message)

def get(request_id: str, since: int = 0) -> List[str]:
    with _lock:
        messages = _logs.get(request_id, [])
        if since <= 0:
            return list(messages)
        return messages[since:]

def clear(request_id: str) -> None:
    with _lock:
        _logs.pop(request_id, None)



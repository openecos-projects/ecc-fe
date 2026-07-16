from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkspaceSession:
    workspace_id: str
    directory: Path
    mutation_lock: threading.Lock = field(default_factory=threading.Lock)


class WorkspaceSessionNotFound(KeyError):
    pass


class WorkspaceSessionRegistry:
    def __init__(self) -> None:
        self._next_id = 1
        self._sessions: dict[str, WorkspaceSession] = {}
        self._sessions_by_directory: dict[Path, str] = {}
        self._lock = threading.Lock()

    def create_session(self, directory: str | Path) -> WorkspaceSession:
        resolved = Path(directory).expanduser().resolve()
        with self._lock:
            existing_id = self._sessions_by_directory.get(resolved)
            if existing_id is not None:
                self._remove_session(existing_id)
            return self._create_session(resolved)

    def open_session(self, directory: str | Path) -> WorkspaceSession:
        resolved = Path(directory).expanduser().resolve()
        with self._lock:
            existing_id = self._sessions_by_directory.get(resolved)
            if existing_id is not None:
                return self._sessions[existing_id]
            return self._create_session(resolved)

    def get_session(self, workspace_id: str) -> WorkspaceSession:
        try:
            return self._sessions[workspace_id]
        except KeyError as exc:
            raise WorkspaceSessionNotFound(workspace_id) from exc

    def close_session(self, workspace_id: str) -> None:
        with self._lock:
            self._remove_session(workspace_id)

    def close_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._sessions_by_directory.clear()

    def _create_session(self, directory: Path) -> WorkspaceSession:
        workspace_id = f"workspace-{self._next_id}"
        self._next_id += 1
        session = WorkspaceSession(workspace_id=workspace_id, directory=directory)
        self._sessions[workspace_id] = session
        self._sessions_by_directory[directory] = workspace_id
        return session

    def _remove_session(self, workspace_id: str) -> None:
        session = self._sessions.pop(workspace_id, None)
        if session is None:
            raise WorkspaceSessionNotFound(workspace_id)
        self._sessions_by_directory.pop(session.directory, None)

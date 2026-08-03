"""B01-backed advisory report writer for B06."""
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from .contracts import ALLOWED_REPORT_ROOT

class ReportWriter(Protocol):
    def write_json(self, path: Path, payload: Mapping[str, Any]) -> None: ...
    def write_text(self, path: Path, text: str) -> None: ...

class B01AtomicReportWriter:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
    def _policy(self) -> Any:
        from smartcrypto.runtime.integrity_traceability_v2 import AtomicWritePolicy
        return AtomicWritePolicy.restricted(
            authorized_roots=(self.project_root / ALLOWED_REPORT_ROOT,),
            working_directory=self.project_root,
        )
    def write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        from smartcrypto.runtime.integrity_traceability_v2 import atomic_write_json
        atomic_write_json(path, dict(payload), policy=self._policy())
    def write_text(self, path: Path, text: str) -> None:
        from smartcrypto.runtime.integrity_traceability_v2 import atomic_write_text
        atomic_write_text(path, text, policy=self._policy())

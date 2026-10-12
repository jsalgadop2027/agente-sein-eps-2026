"""Persistencia: Cloud Storage en GCP o disco local en desarrollo.

Organización de objetos:
  sessions/{session_id}.json        memoria de corto plazo (historial por sesión)
  studies/{study_id}.json           memoria episódica (manifiesto + resultados por estudio)
  reports/{report_id}.md            informes emitidos (con aprobación y firma)
  audit/{fecha}/{evento}.json       auditoría de acciones (quién, qué, cuándo)
  projects/{project_id}/notes.json  memoria de largo plazo por proyecto
  cases/{case_id}/meta.json         casos de datos subidos por los ingenieros (+ archivo original)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class Store:
    def __init__(self):
        self.bucket_name = os.environ.get("GCS_BUCKET")
        if self.bucket_name:
            from google.cloud import storage
            self._bucket = storage.Client().bucket(self.bucket_name)
        else:
            self._root = Path(os.environ.get("LOCAL_STORE", ".store"))
            self._root.mkdir(parents=True, exist_ok=True)

    def put_text(self, key: str, text: str, content_type: str = "application/json") -> None:
        if self.bucket_name:
            self._bucket.blob(key).upload_from_string(text, content_type=content_type)
        else:
            p = self._root / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if self.bucket_name:
            self._bucket.blob(key).upload_from_string(data, content_type=content_type)
        else:
            p = self._root / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes | None:
        if self.bucket_name:
            blob = self._bucket.blob(key)
            return blob.download_as_bytes() if blob.exists() else None
        p = self._root / key
        return p.read_bytes() if p.exists() else None

    def get_text(self, key: str) -> str | None:
        if self.bucket_name:
            blob = self._bucket.blob(key)
            return blob.download_as_text() if blob.exists() else None
        p = self._root / key
        return p.read_text(encoding="utf-8") if p.exists() else None

    def put_json(self, key: str, obj) -> None:
        self.put_text(key, json.dumps(obj, ensure_ascii=False, indent=1, default=str))

    def get_json(self, key: str):
        txt = self.get_text(key)
        return json.loads(txt) if txt is not None else None

    def list_keys(self, prefix: str) -> list[str]:
        if self.bucket_name:
            return sorted(b.name for b in self._bucket.list_blobs(prefix=prefix))
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self._root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())

    def audit(self, event: str, **data) -> None:
        self.put_json(f"audit/{datetime.now(timezone.utc):%Y-%m-%d}/{new_id(event)}.json",
                      {"evento": event, "timestamp": now_iso(), **data})


store = Store()

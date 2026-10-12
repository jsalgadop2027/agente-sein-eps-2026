"""Memoria episódica (estudios ejecutados) y de largo plazo (notas por proyecto)."""

from __future__ import annotations

from dataclasses import asdict

from src.adapters.base import SimResult
from src.memory.store import new_id, now_iso, store


def record_study(res: SimResult, *, session_id: str, user: str, project_id: str,
                 approval: dict | None = None) -> str:
    study_id = new_id("EST")
    manifest = {
        "study_id": study_id,
        "timestamp": now_iso(),
        "session_id": session_id,
        "usuario": user,
        "project_id": project_id,
        "aprobacion": approval,
        **asdict(res),
    }
    store.put_json(f"studies/{study_id}.json", manifest)
    return study_id


def get_study(study_id: str) -> dict | None:
    if "/" in study_id or ".." in study_id:
        return None
    return store.get_json(f"studies/{study_id}.json")


def search_studies(project_id: str | None = None, study_type: str | None = None, limit: int = 10) -> list[dict]:
    keys = list(reversed(store.list_keys("studies/")))
    out = []
    for key in keys:
        m = store.get_json(key)
        if not m:
            continue
        if project_id and m.get("project_id") != project_id:
            continue
        if study_type and m.get("study_type") != study_type:
            continue
        out.append({k: m.get(k) for k in ("study_id", "timestamp", "study_type", "case_id",
                                           "simulator", "parameters", "usuario")}
                   | {"resumen": _headline(m)})
        if len(out) >= limit:
            break
    return out


def _headline(m: dict) -> dict:
    s = m.get("summary", {})
    keys = ("convergio", "cumple_N1", "conteo_por_severidad", "estable_angular",
            "max_desviacion_angular_coi_deg", "verificacion", "estable_pequena_senal")
    return {k: s[k] for k in keys if k in s}


def get_project_notes(project_id: str) -> list[dict]:
    return store.get_json(f"projects/{project_id}/notes.json") or []


def add_project_note(project_id: str, note: str, user: str) -> int:
    notes = get_project_notes(project_id)
    notes.append({"timestamp": now_iso(), "usuario": user, "nota": note[:1000]})
    store.put_json(f"projects/{project_id}/notes.json", notes[-50:])
    return len(notes)

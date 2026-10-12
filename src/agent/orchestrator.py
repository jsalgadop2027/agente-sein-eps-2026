"""Bucle del agente (Claude API + tool use) con pausas de aprobación humana.

El historial es append-only: el contenido de cada respuesta se guarda tal cual
(incluidos bloques de thinking) para reenviarlo sin modificaciones.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

import anthropic

from src.agent import guardrails as G
from src.agent import tools as T
from src.agent.prompts import build_system
from src.memory import studies
from src.memory.store import new_id, now_iso, store

log = logging.getLogger("geneps.agent")

MODEL = os.environ.get("GENEPS_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("GENEPS_EFFORT", "high")
EFFORT_ROUTING = os.environ.get("GENEPS_EFFORT_ROUTING", "1") != "0"
MAX_STEPS = 14

client = anthropic.Anthropic(max_retries=3)
_locks: dict[str, threading.Lock] = {}

_SECRET_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class SessionBusy(Exception):
    pass


def _lock(sid: str) -> threading.Lock:
    return _locks.setdefault(sid, threading.Lock())


def sanitize(text: str) -> tuple[str, list[str]]:
    """Guardrail de entrada: no enviar secretos ni correos al modelo."""
    notes = []
    if _SECRET_RE.search(text):
        text = _SECRET_RE.sub("[SECRETO_REDACTADO]", text)
        notes.append("Se redactó un posible secreto/credencial del mensaje.")
    if _EMAIL_RE.search(text):
        text = _EMAIL_RE.sub("[CORREO_REDACTADO]", text)
        notes.append("Se redactó una dirección de correo (dato personal).")
    return text[:8000], notes


# ---------------------------------------------------------------- enrutamiento
# Consultas que implican simulación, análisis o emisión de informes.
_HEAVY_RE = re.compile(
    r"simul|contingen|n-1|estabilidad|transitori|peque[ñn]a se[ñn]al|autovalor|flujo de potencia|"
    r"loadflow|falla|cortocircuito|screening|anali[sz]|análisis|compar|informe|reporte|export|"
    r"escenario|sensibilidad|despacho|demanda", re.I)
LIGHT_MAX_CHARS = 300


def route_effort(text: str) -> str:
    """Enrutador por reglas: 'low' para consultas breves y conceptuales, 'high' para el resto."""
    if not EFFORT_ROUTING:
        return EFFORT
    if len(text) <= LIGHT_MAX_CHARS and not _HEAVY_RE.search(text):
        return "low"
    return "high"


def _apply_effort(s: dict, level: str) -> None:
    """Cambia el esfuerzo con un mensaje de sistema sin contenido (append-only, conserva la caché)."""
    if level == s.get("effort", EFFORT):
        return
    s["messages"].append({"role": "system", "content": [], "output_config": {"effort": level}})
    s["effort"] = level


# ---------------------------------------------------------------- sesiones
def create_session(user: str, role: str, project_id: str, variables: dict) -> dict:
    s = {"session_id": new_id("SES"), "created": now_iso(), "user": user[:80] or "anónimo",
         "role": role[:80] or "ingeniero", "project_id": re.sub(r"[^\w\-]", "_", project_id)[:60] or "demo",
         "variables": {k: str(v)[:500] for k, v in variables.items()},
         "messages": [], "pending": None, "study_ids": [], "events": [], "reports": [], "usage": {}}
    save(s)
    store.audit("sesion_creada", session_id=s["session_id"], usuario=s["user"], proyecto=s["project_id"])
    return s


def load(sid: str) -> dict | None:
    if not re.fullmatch(r"SES-[\w\-]+", sid or ""):
        return None
    return store.get_json(f"sessions/{sid}.json")


def save(s: dict) -> None:
    store.put_json(f"sessions/{s['session_id']}.json", s)


# ---------------------------------------------------------------- bucle
def _call(s: dict):
    system = build_system(s)
    return client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        tools=T.TOOLS,
        messages=s["messages"],
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": EFFORT},  # fijo: el cambio por consulta va en los mensajes
        cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01", "mid-conversation-output-config-2026-07-01"],
        fallbacks="default",
    )


def _track_usage(s: dict, resp) -> None:
    u = s.setdefault("usage", {})
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        u[k] = u.get(k, 0) + (getattr(resp.usage, k, 0) or 0)
    u["llamadas"] = u.get("llamadas", 0) + 1


def _view(content: list[dict]) -> dict:
    """Separa texto, razonamiento resumido y llamadas a herramientas para la UI."""
    return {
        "text": "\n\n".join(b["text"] for b in content if b.get("type") == "text"),
        "thinking": "\n\n".join(b.get("thinking", "") for b in content if b.get("type") == "thinking").strip(),
        "tool_calls": [{"name": b["name"], "input": b["input"]} for b in content if b.get("type") == "tool_use"],
    }


def _loop(s: dict) -> dict:
    steps = []
    for _ in range(MAX_STEPS):
        try:
            resp = _call(s)
        except anthropic.RateLimitError:
            return {"status": "error", "error": "Límite de tasa del proveedor LLM alcanzado. Reintente en un minuto.", "steps": steps}
        except anthropic.APIStatusError as exc:
            log.error("Error API %s: %s", exc.status_code, exc.message)
            return {"status": "error", "error": f"Error del proveedor LLM ({exc.status_code}).", "steps": steps}
        except anthropic.APIConnectionError:
            return {"status": "error", "error": "No hay conexión con el proveedor LLM.", "steps": steps}
        _track_usage(s, resp)

        if resp.stop_reason == "refusal":
            # No se agrega al historial: el turno rechazado no se reenvía.
            return {"status": "refusal", "steps": steps,
                    "error": "El modelo declinó la solicitud. Reformule la consulta dentro del alcance técnico."}

        content = [b.to_dict() for b in resp.content]
        s["messages"].append({"role": "assistant", "content": content})
        steps.append(_view(content))

        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason == "max_tokens":
            return {"status": "done", "steps": steps, "warning": "Respuesta truncada por límite de tokens."}

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            return {"status": "done", "steps": steps}

        gated = [tu for tu in tool_uses if tu["name"] in T.APPROVAL_TOOLS]
        if gated:
            s["pending"] = {"created": now_iso(), "tool_uses": tool_uses}
            store.audit("aprobacion_solicitada", session_id=s["session_id"],
                        herramientas=[g["name"] for g in gated])
            return {"status": "awaiting_approval", "steps": steps, "approvals": pending_view(s)}

        s["messages"].append({"role": "user", "content": _run_tools(s, tool_uses, {})})
    return {"status": "done", "steps": steps, "warning": "Se alcanzó el máximo de pasos por turno."}


def _run_tools(s: dict, tool_uses: list[dict], decisions: dict) -> list[dict]:
    results = []
    for tu in tool_uses:
        if tu["name"] in T.APPROVAL_TOOLS:
            d = decisions.get(tu["id"], {})
            if not d.get("approved"):
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                                "content": json.dumps({"rechazado_por_ingeniero": True,
                                                       "motivo": d.get("comment") or "sin comentario"},
                                                      ensure_ascii=False)})
                continue
            approval = {"approved": True, "signer": d.get("signer", ""), "comment": d.get("comment", ""),
                        "timestamp": now_iso()}
        else:
            approval = None
        errors = s.setdefault("turn_errors", {})
        if errors.get(tu["name"], 0) >= G.MAX_TOOL_ERRORS:
            msg = (f"Límite de {G.MAX_TOOL_ERRORS} intentos fallidos de {tu['name']} alcanzado en este turno. "
                   "No reintentes: explica al ingeniero qué falló o qué dato falta.")
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                            "content": json.dumps({"error": msg}, ensure_ascii=False)})
            s.setdefault("turn_guardrails", []).append(f"Se bloqueó un nuevo reintento de {tu['name']} "
                                                       f"(ya falló {G.MAX_TOOL_ERRORS} veces en este turno).")
            store.audit("reintento_bloqueado", session_id=s["session_id"], herramienta=tu["name"])
            continue
        content, is_error = T.execute(tu["name"], tu["input"], s, approval)
        if is_error:
            errors[tu["name"]] = errors.get(tu["name"], 0) + 1
        store.audit("herramienta_ejecutada", session_id=s["session_id"], herramienta=tu["name"],
                    entrada=tu["input"], error=is_error, aprobacion=approval)
        results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": content, "is_error": is_error})
    return results  # todos los resultados en un único mensaje de usuario


def pending_view(s: dict) -> list[dict]:
    p = s.get("pending") or {}
    return [{"tool_use_id": tu["id"], "tool": tu["name"], "input": tu["input"],
             "motivo_aprobacion": T.APPROVAL_TOOLS[tu["name"]]}
            for tu in p.get("tool_uses", []) if tu["name"] in T.APPROVAL_TOOLS]


def chat(sid: str, text: str) -> dict:
    lk = _lock(sid)
    if not lk.acquire(blocking=False):
        raise SessionBusy()
    try:
        s = load(sid)
        if s is None:
            raise KeyError(sid)
        if s.get("pending"):
            return {"status": "awaiting_approval", "steps": [], "approvals": pending_view(s),
                    "warning": "Hay una aprobación pendiente: resuélvala antes de continuar."}
        clean, notes = sanitize(text)
        s["turn_errors"] = {}
        memory = None
        if not any(m["role"] == "user" for m in s["messages"]):
            memory = G.memory_block(studies.get_project_notes(s["project_id"]),
                                    studies.search_studies(s["project_id"], limit=5))
        effort = route_effort(clean)
        _apply_effort(s, effort)
        # El texto del ingeniero va siempre en el último bloque; el previo, si existe, es la memoria (datos)
        s["messages"].append({"role": "user", "content": [{"type": "text", "text": memory},
                                                          {"type": "text", "text": clean}] if memory else clean})
        store.audit("esfuerzo_enrutado", session_id=sid, esfuerzo=effort)
        out = _loop(s)
        _verify(s, out)
        out["guardrails"] = notes + s.pop("turn_guardrails", [])
        out["effort"] = effort
        save(s)
        return out | {"events": s["events"], "usage": s["usage"]}
    finally:
        lk.release()


def resolve(sid: str, decisions: dict) -> dict:
    lk = _lock(sid)
    if not lk.acquire(blocking=False):
        raise SessionBusy()
    try:
        s = load(sid)
        if s is None:
            raise KeyError(sid)
        if not s.get("pending"):
            return {"status": "done", "steps": [], "warning": "No hay aprobaciones pendientes."}
        tool_uses = s["pending"]["tool_uses"]
        for tu_id, d in decisions.items():
            store.audit("decision_aprobacion", session_id=sid, tool_use_id=tu_id,
                        aprobado=bool(d.get("approved")), firmante=d.get("signer"), comentario=d.get("comment"))
        s["pending"] = None
        s["messages"].append({"role": "user", "content": _run_tools(s, tool_uses, decisions)})
        out = _loop(s)
        _verify(s, out)
        out["guardrails"] = s.pop("turn_guardrails", [])
        save(s)
        return out | {"events": s["events"], "usage": s["usage"]}
    finally:
        lk.release()


def _user_text(m: dict) -> str | None:
    """Texto escrito por el ingeniero; None si el mensaje solo trae resultados de herramientas."""
    c = m["content"]
    if isinstance(c, str):
        return c
    if c and all(b.get("type") == "text" for b in c):
        return c[-1]["text"]  # el bloque previo, si existe, es la memoria del proyecto
    return None


def _verify(s: dict, out: dict) -> None:
    """Guardrail de salida: los study_id citados deben existir (y ser de este proyecto)."""
    v = G.verify_citations([st["text"] for st in out.get("steps", [])], set(s.get("study_ids", [])),
                           s["project_id"], studies.get_study)
    if not v:
        return
    turn = sum(1 for m in s["messages"] if m["role"] == "user" and _user_text(m) is not None)
    s.setdefault("verificaciones", []).append({"turno": turn, **v})
    out["verificacion"] = v | {"aviso": G.citation_warning(v)}
    if out["verificacion"]["aviso"]:
        store.audit("cita_no_verificada", session_id=s["session_id"], inexistentes=v["inexistentes"],
                    otro_proyecto=v["otro_proyecto"])


def history_view(s: dict) -> list[dict]:
    """Historial legible para reconstruir la conversación en la UI."""
    out = []
    for m in s["messages"]:
        if m["role"] == "system":
            continue
        if m["role"] == "user":
            if (text := _user_text(m)) is not None:
                out.append({"role": "user", "text": text})
        else:
            out.append({"role": "assistant", **_view(m["content"])})
    return out

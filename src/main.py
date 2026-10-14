"""Servicio web GenEPS (FastAPI): chat con aprobaciones, consulta de estudios y jobs batch."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from src.adapters.base import SimulationInputError, get_case, list_cases
from src.agent import orchestrator as orch
from src.memory import studies
from src.memory.store import new_id, now_iso, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="GenEPS", version="1.0.0",
              description="Agente de estudios de estabilidad SEP (SEIN) — semi-autónomo con aprobaciones")
STATIC = Path(__file__).parent / "static"

# Acceso por usuario/contraseña (HTTP Basic sobre el HTTPS de Cloud Run). La contraseña llega
# desde Secret Manager; en Cloud Run (K_SERVICE definido) sin contraseña el servicio se cierra.
AUTH_USER = os.environ.get("GENEPS_USER", "geneps")
AUTH_PASSWORD = os.environ.get("GENEPS_PASSWORD", "").strip()
AUTH_REQUIRED = bool(AUTH_PASSWORD) or bool(os.environ.get("K_SERVICE"))
log = logging.getLogger("geneps.auth")


def _authorized(header: str) -> bool:
    if not AUTH_PASSWORD or not header.startswith("Basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:], validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # Ambas comparaciones siempre, en tiempo constante
    ok_user = secrets.compare_digest(user.encode(), AUTH_USER.encode())
    ok_pwd = secrets.compare_digest(pwd.encode(), AUTH_PASSWORD.encode())
    return ok_user and ok_pwd


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if AUTH_REQUIRED and not _authorized(request.headers.get("authorization", "")):
        if not AUTH_PASSWORD:
            log.error("GENEPS_PASSWORD no configurada: acceso bloqueado")
            return Response("Autenticación no configurada", status_code=503)
        return Response("Autenticación requerida", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="GenEPS", charset="UTF-8"'})
    return await call_next(request)


class NewSession(BaseModel):
    user: str = Field("", max_length=80)
    role: str = Field("ingeniero", max_length=80)
    project_id: str = Field("demo", max_length=60)
    objetivo_estudio: str = Field("", max_length=500)
    escenario: str = Field("", max_length=500)
    criterios: str = Field("", max_length=500)
    restricciones: str = Field("", max_length=500)
    formato_salida: str = Field("", max_length=200)
    caso_estatico: str = Field("ieee14_cdf", max_length=60)
    caso_dinamico: str = Field("ieee14_dyn", max_length=60)


class CaseUpload(BaseModel):
    filename: str = Field(..., min_length=1, max_length=120)
    content_base64: str = Field(..., min_length=1, max_length=7_000_000)  # ~5 MB decodificados
    descripcion: str = Field("", max_length=300)
    subido_por: str = Field("", max_length=80)


class ChatIn(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=8000)


class Decision(BaseModel):
    approved: bool
    signer: str = Field("", max_length=80)
    comment: str = Field("", max_length=500)


class ApproveIn(BaseModel):
    session_id: str
    decisions: dict[str, Decision]


class ScreeningJob(BaseModel):
    project_id: str = Field("demo", max_length=60)
    requested_by: str = Field("job", max_length=80)
    load_scales: list[float] = Field(default_factory=lambda: [1.0], max_length=6)
    case_id: str = Field("ieee14_cdf", max_length=60)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def healthz():
    return {"ok": True, "model": orch.MODEL, "store": store.bucket_name or "local"}


@app.get("/api/cases")
def cases():
    return [{k: v for k, v in c.items() if k != "archivo"} for c in list_cases()]


@app.post("/api/cases")
def upload_case(body: CaseUpload):
    from src.adapters.upload import register_case
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except binascii.Error:
        raise HTTPException(400, "Contenido base64 inválido")
    try:
        meta = register_case(body.filename, data, body.subido_por, body.descripcion)
    except SimulationInputError as exc:
        raise HTTPException(422, str(exc))
    return {k: v for k, v in meta.items() if k != "archivo"}


@app.post("/api/sessions")
def new_session(body: NewSession):
    # Estas variables se insertan en el system prompt: se rechaza cualquier intento de inyección
    if (attack := orch.G.detect_injection(*body.model_dump(exclude={"caso_estatico", "caso_dinamico"}).values())):
        store.audit("inyeccion_bloqueada", origen="nueva_sesion", usuario=body.user[:80], tipos=attack)
        raise HTTPException(422, "Los datos de la sesión contienen instrucciones dirigidas al agente "
                                 f"({', '.join(attack)}). Describa solo el estudio.")
    for field, simulator in (("caso_estatico", "pandapower"), ("caso_dinamico", "andes")):
        try:
            if get_case(getattr(body, field))["simulador"] != simulator:
                raise SimulationInputError(f"'{getattr(body, field)}' no sirve como {field.replace('_', ' ')}")
        except SimulationInputError as exc:
            raise HTTPException(422, str(exc))
    variables = body.model_dump(exclude={"user", "role", "project_id"})
    s = orch.create_session(body.user, body.role, body.project_id, variables)
    return {"session_id": s["session_id"], "project_id": s["project_id"]}


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    s = orch.load(sid)
    if not s:
        raise HTTPException(404, "Sesión no encontrada")
    return {"session_id": sid, "variables": s["variables"], "history": orch.history_view(s),
            "approvals": orch.pending_view(s), "events": s["events"], "reports": s["reports"], "usage": s["usage"]}


@app.get("/api/sessions/{sid}/pdf")
def session_pdf(sid: str):
    """PDF con las consultas y respuestas hasta el momento (descarga inmediata)."""
    from src.reports.pdf import answered, build_pdf
    s = orch.load(sid)
    if not s:
        raise HTTPException(404, "Sesión no encontrada")
    history = orch.history_view(s)
    if not answered(history):
        raise HTTPException(409, "Aún no hay consultas respondidas en esta sesión")
    pdf = build_pdf(s, history, orch.MODEL)
    store.audit("pdf_conversacion", session_id=sid, usuario=s["user"], bytes=len(pdf))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="GenEPS_{sid}.pdf"'})


@app.post("/api/chat")
def chat(body: ChatIn):
    try:
        return orch.chat(body.session_id, body.message)
    except KeyError:
        raise HTTPException(404, "Sesión no encontrada")
    except orch.SessionBusy:
        raise HTTPException(409, "La sesión está procesando otra solicitud")


@app.post("/api/approve")
def approve(body: ApproveIn):
    try:
        return orch.resolve(body.session_id, {k: v.model_dump() for k, v in body.decisions.items()})
    except KeyError:
        raise HTTPException(404, "Sesión no encontrada")
    except orch.SessionBusy:
        raise HTTPException(409, "La sesión está procesando otra solicitud")


@app.get("/api/studies/{study_id}")
def get_study(study_id: str):
    m = studies.get_study(study_id)
    if not m:
        raise HTTPException(404, "Estudio no encontrado")
    return m


@app.get("/api/reports/{report_id}", response_class=PlainTextResponse)
def get_report(report_id: str):
    if "/" in report_id or ".." in report_id:
        raise HTTPException(400, "Identificador inválido")
    txt = store.get_text(f"reports/{report_id}.md")
    if txt is None:
        raise HTTPException(404, "Informe no encontrado")
    return PlainTextResponse(txt, media_type="text/markdown; charset=utf-8")


@app.post("/api/jobs/screening")
def screening_job(body: ScreeningJob):
    """Workflow determinista (no conversacional): flujo base + N-1 por escenario de demanda.

    Pensado para dispararse por scheduler o evento; no usa el LLM ni requiere aprobación,
    porque solo ejecuta estudios estáticos de bajo costo dentro de límites autorizados.
    """
    from src.agent.tools import _sim
    from src.adapters.base import SimulationInputError
    from src.adapters.pandapower_adapter import run_contingency_n1, run_loadflow

    job_id = new_id("JOB")
    ctx = {"session_id": job_id, "user": body.requested_by, "project_id": body.project_id}
    out = []
    for scale in body.load_scales:
        try:
            lf = _sim(run_loadflow, ctx, None, case_id=body.case_id, load_scale=scale)
            n1 = _sim(run_contingency_n1, ctx, None, case_id=body.case_id, load_scale=scale, include_generators=True)
        except SimulationInputError as exc:
            out.append({"load_scale": scale, "error": str(exc)})
            continue
        out.append({"load_scale": scale, "loadflow": lf["study_id"], "n1": n1["study_id"],
                    "tension_min": lf["resultado"]["tension_min"], "cumple_N1": n1["resultado"]["cumple_N1"],
                    "conteo_por_severidad": n1["resultado"]["conteo_por_severidad"]})
    result = {"job_id": job_id, "timestamp": now_iso(), "requested_by": body.requested_by,
              "case_id": body.case_id, "resultados": out}
    store.put_json(f"jobs/{job_id}.json", result)
    store.audit("job_ejecutado", job_id=job_id, solicitado_por=body.requested_by)
    return result

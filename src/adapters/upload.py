"""Registro de casos subidos por los ingenieros.

El archivo se valida con el simulador que lo usará antes de guardarse: un caso que
no se puede simular se rechaza con un mensaje claro en lugar de fallar más tarde
dentro de una conversación. El ``case_id`` incluye parte del SHA-256, así que
subir el mismo archivo dos veces devuelve el caso ya registrado.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from src.adapters.base import CACHE_DIR, STUDIES_BY_SIMULATOR, SimulationInputError, clean_text
from src.adapters.cdf import decode_cdf, parse_cdf
from src.memory.store import now_iso, store

MAX_BYTES = 5 * 1024 * 1024
FORMATS = {
    ".txt": ("IEEE CDF", "pandapower"),
    ".cdf": ("IEEE CDF", "pandapower"),
    ".xlsx": ("ANDES (xlsx)", "andes"),
}
CONTENT_TYPES = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _inspect_cdf(path: Path) -> dict:
    from pandapower.converter.pypower import from_ppc

    try:
        case = parse_cdf(decode_cdf(path.read_bytes()))
        if not len(case.ppc["bus"]) or not len(case.ppc["branch"]):
            raise ValueError("sin barras o sin ramas")
        if not (case.ppc["bus"][:, 1] == 3).any():
            raise ValueError("no hay barra slack (tipo 3)")
        from_ppc(case.ppc, f_hz=60, validate_conversion=False)
    except Exception as exc:  # cualquier fallo de lectura/conversión invalida el archivo
        raise SimulationInputError(f"El archivo no es un IEEE CDF válido ({type(exc).__name__}: {exc}).")
    return {"titulo": case.title, "n_barras": int(len(case.ppc["bus"])), "n_ramas": int(len(case.ppc["branch"])),
            "n_generadores": int(len(case.ppc["gen"])), "advertencias_datos": case.warnings}


def _inspect_andes(path: Path) -> dict:
    import andes

    andes.config_logger(stream_level=40)
    try:
        ss = andes.load(str(path), setup=True, no_output=True, default_config=True)
    except Exception as exc:  # ANDES lanza distintos tipos según la hoja que falle
        raise SimulationInputError(f"ANDES no pudo leer el archivo ({type(exc).__name__}: {exc})"[:400])
    if ss is None:
        raise SimulationInputError("ANDES no pudo leer el archivo: formato xlsx de ANDES no reconocido.")
    if not ss.GENROU.n:
        raise SimulationInputError("El caso no tiene generadores GENROU: los estudios dinámicos de GenEPS los requieren.")
    ss.PFlow.run()
    if not ss.PFlow.converged:
        raise SimulationInputError("El flujo de potencia inicial del caso no converge en ANDES.")
    return {"titulo": path.stem, "n_barras": int(ss.Bus.n), "n_ramas": int(ss.Line.n),
            "n_generadores": int(ss.GENROU.n), "advertencias_datos": []}


def register_case(filename: str, data: bytes, uploaded_by: str = "", description: str = "") -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in FORMATS:
        raise SimulationInputError(f"Formato '{ext or filename}' no soportado. Use IEEE CDF (.txt, .cdf) "
                                   "para estudios estáticos o ANDES (.xlsx) para estudios dinámicos.")
    if not data:
        raise SimulationInputError("El archivo está vacío.")
    if len(data) > MAX_BYTES:
        raise SimulationInputError(f"El archivo supera el máximo de {MAX_BYTES // (1024 * 1024)} MB.")

    digest = hashlib.sha256(data).hexdigest()
    stem = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_")[:24] or "caso"
    case_id = f"{stem}_{digest[:6]}"
    meta_key = f"cases/{case_id}/meta.json"
    existing = store.get_json(meta_key)
    if existing:
        return existing | {"ya_existia": True}

    fmt, simulator = FORMATS[ext]
    key = f"cases/{case_id}/data{ext}"
    local = CACHE_DIR / key
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    try:
        info = _inspect_cdf(local) if simulator == "pandapower" else _inspect_andes(local)
    except SimulationInputError:
        local.unlink(missing_ok=True)
        raise

    # Título, descripción y nombre llegan al modelo: se guardan ya saneados
    info["titulo"] = clean_text(info["titulo"], 80) or "caso"
    filename = clean_text(Path(filename).name, 120) or f"archivo{ext}"
    description = clean_text(description, 200)
    store.put_bytes(key, data, CONTENT_TYPES.get(ext, "text/plain; charset=utf-8"))
    meta = {
        "case_id": case_id,
        "descripcion": description or f"{info['titulo']} - archivo subido {filename}",
        "archivo": f"store:{key}",
        "nombre_archivo": filename,
        "formato": fmt,
        "simulador": simulator,
        "estudios": STUDIES_BY_SIMULATOR[simulator],
        "origen": "subido",
        "sha256": digest,
        "bytes": len(data),
        "subido_por": clean_text(uploaded_by, 80),
        "timestamp": now_iso(),
        **info,
    }
    store.put_json(meta_key, meta)
    store.audit("caso_subido", case_id=case_id, sha256=digest, usuario=uploaded_by[:80], archivo=meta["nombre_archivo"])
    return meta

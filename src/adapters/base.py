"""Contrato común de los adaptadores y registro de casos de estudio.

Cada adaptador encapsula un simulador y devuelve un ``SimResult`` con un
resumen compacto (lo que ve el agente) y los artefactos completos (lo que se
guarda para trazabilidad y gráficos).
"""

from __future__ import annotations

import hashlib
import platform
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class SimulationInputError(ValueError):
    """Entrada inválida: el agente debe corregirla o pedir el dato, no inventarlo."""


@dataclass
class SimResult:
    simulator: str
    simulator_version: str
    study_type: str
    case_id: str
    case_sha256: str
    parameters: dict
    summary: dict
    artifacts: dict = field(default_factory=dict)
    duration_s: float = 0.0
    python_version: str = platform.python_version()


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = round(time.perf_counter() - self.t0, 3)


STUDIES_BY_SIMULATOR = {
    "pandapower": ["run_loadflow", "run_contingency_n1"],
    "andes": ["run_transient_stability", "run_small_signal_analysis"],
}

# Casos incluidos en la imagen. Los subidos por los ingenieros se guardan en cases/{case_id}/.
CASES: dict[str, dict] = {
    "ieee14_cdf": {
        "descripcion": "IEEE 14 barras, archivo CDF del UW Power Systems Test Case Archive (1962). "
                       "Datos estáticos: flujo de potencia y contingencias N-1.",
        "archivo": "data/ieee14cdf.txt",
        "formato": "IEEE CDF",
        "simulador": "pandapower",
        "estudios": STUDIES_BY_SIMULATOR["pandapower"],
        "origen": "incluido",
    },
    "ieee14_dyn": {
        "descripcion": "IEEE 14 barras con datos dinámicos (GENROU, TGOV1, EXST1, ESST3A) incluido en ANDES "
                       "(ieee14_full.xlsx). Estabilidad transitoria y pequeña señal.",
        "archivo": "andes:ieee14/ieee14_full.xlsx",
        "formato": "ANDES (xlsx)",
        "simulador": "andes",
        "estudios": STUDIES_BY_SIMULATOR["andes"],
        "origen": "incluido",
    },
}

CASE_ID_RE = re.compile(r"[a-z0-9_]{3,40}")
_UNSAFE_TEXT = re.compile(r"[^\w\s.,;:()+/%°=-]")


def clean_text(text: str, limit: int) -> str:
    """Texto aportado por usuarios (títulos, descripciones, notas) antes de mostrárselo al modelo.

    Solo letras, dígitos y puntuación básica: sin marcado (<>, {}, [], `, #, *) con el que
    imitar etiquetas o instrucciones del sistema; espacios colapsados y longitud acotada.
    """
    t = _UNSAFE_TEXT.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", t).strip()[:limit]
CACHE_DIR = Path(tempfile.gettempdir()) / "geneps_cases"


def get_case(case_id: str) -> dict:
    if case_id in CASES:
        return {"case_id": case_id, **CASES[case_id]}
    if CASE_ID_RE.fullmatch(case_id or ""):
        from src.memory.store import store
        meta = store.get_json(f"cases/{case_id}/meta.json")
        if meta:
            return meta
    raise SimulationInputError(f"Caso '{case_id}' no existe. Use list_cases para ver los casos disponibles.")


def list_cases() -> list[dict]:
    from src.memory.store import store
    uploaded = [store.get_json(k) for k in store.list_keys("cases/") if k.endswith("/meta.json")]
    uploaded.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return [{"case_id": cid, **c} for cid, c in CASES.items()] + [m for m in uploaded if m]


def case_file(case_id: str, simulator: str | None = None) -> Path:
    """Ruta local del archivo del caso; los subidos se descargan una vez a un caché local."""
    meta = get_case(case_id)
    if simulator and meta["simulador"] != simulator:
        tipo = "estático (pandapower)" if simulator == "pandapower" else "dinámico (ANDES)"
        raise SimulationInputError(f"El caso '{case_id}' no es un caso {tipo}. Use list_cases para elegir uno.")
    ref = meta["archivo"]
    if ref.startswith("andes:"):
        import andes
        return Path(andes.get_case(ref[len("andes:"):]))
    if ref.startswith("store:"):
        key = ref[len("store:"):]
        local = CACHE_DIR / key
        if not local.exists():
            from src.memory.store import store
            data = store.get_bytes(key)
            if data is None:
                raise SimulationInputError(f"No se encontró el archivo del caso '{case_id}'.")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        return local
    return DATA_DIR / Path(ref).name

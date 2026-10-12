"""Base de conocimiento normativa (memoria semántica) y verificación de límites.

Cada criterio indica si es NORMATIVO (con fuente citable) o un SUPUESTO DE
ESTUDIO (práctica de ingeniería, no normativo). El agente debe citar la fuente
y no presentar un supuesto como norma. Donde el documento base no identifica
el artículo exacto, se deja explícito "por confirmar" en lugar de inventarlo.
"""

from __future__ import annotations

KB_VERSION = "2026-10-10"
NOMINAL_FREQ_HZ = 60.0

_OSINERGMIN_NTCSE = "https://www.osinergmin.gob.pe/empresas/electricidad/calidad/NTCSE/producto"

CRITERIA: dict[str, dict] = {
    "NTCSE_TENSION": {
        "titulo": "Tolerancia de tensión en puntos de entrega",
        "tipo": "normativo",
        "valor": {"min_pu": 0.95, "max_pu": 1.05},
        "descripcion": "Hasta ±5,0 % de la tensión nominal en los puntos de entrega.",
        "fuente": "NTCSE, D.S. N° 020-97-EM (calidad de producto, tensión)",
        "url": _OSINERGMIN_NTCSE,
        "nota": "La NTCSE aplica a puntos de entrega a clientes. En sistemas de prueba IEEE "
                "se usa como banda de referencia en todas las barras (supuesto explícito del estudio).",
        "keywords": ["tension", "tensión", "voltaje", "voltage", "ntcse", "barra"],
    },
    "NTCSE_FREC_SOSTENIDA": {
        "titulo": "Tolerancia de variaciones sostenidas de frecuencia",
        "tipo": "normativo",
        "valor": {"tolerancia_pct": 0.6, "min_hz": 59.64, "max_hz": 60.36},
        "descripcion": "Variaciones sostenidas de frecuencia de ±0,6 % respecto de la nominal (60 Hz).",
        "fuente": "NTCSE, D.S. N° 020-97-EM (calidad de producto, frecuencia)",
        "url": _OSINERGMIN_NTCSE,
        "nota": "Se compara contra la frecuencia final (régimen post-perturbación) de la simulación.",
        "keywords": ["frecuencia", "frequency", "sostenida", "ntcse"],
    },
    "NTCSE_FREC_SUBITA": {
        "titulo": "Tolerancia de variaciones súbitas de frecuencia",
        "tipo": "normativo",
        "valor": {"tolerancia_hz": 1.0, "min_hz": 59.0, "max_hz": 61.0},
        "descripcion": "Variaciones súbitas de frecuencia de ±1,0 Hz respecto de la nominal.",
        "fuente": "NTCSE, D.S. N° 020-97-EM (calidad de producto, frecuencia)",
        "url": _OSINERGMIN_NTCSE,
        "nota": "Se compara contra la excursión máxima de frecuencia durante el transitorio.",
        "keywords": ["frecuencia", "frequency", "subita", "súbita", "ntcse", "transitorio"],
    },
    "COES_N1": {
        "titulo": "Criterio de contingencia simple N-1",
        "tipo": "normativo",
        "valor": {"descripcion": "El sistema debe soportar la salida de un elemento sin violar límites operativos."},
        "descripcion": "Los Procedimientos Técnicos del COES definen el criterio de contingencia N-1.",
        "fuente": "Procedimientos Técnicos del COES (procedimiento y artículo específico: POR CONFIRMAR)",
        "url": "https://www.coes.org.pe",
        "nota": "El documento base del proyecto no identifica el número de procedimiento; "
                "debe confirmarse antes de citarlo en un informe oficial.",
        "keywords": ["n-1", "n1", "contingencia", "coes", "seguridad"],
    },
    "EST_TENSION_EMERGENCIA": {
        "titulo": "Banda de tensión post-contingencia (emergencia)",
        "tipo": "supuesto_de_estudio",
        "valor": {"min_pu": 0.90, "max_pu": 1.10},
        "descripcion": "Banda de tensión aceptada en estado post-contingencia N-1.",
        "fuente": "Supuesto de estudio (práctica de ingeniería). NO normativo: confirmar con PR COES.",
        "url": None,
        "nota": "Usado para clasificar violaciones en el análisis N-1.",
        "keywords": ["tension", "tensión", "emergencia", "post-contingencia", "n-1"],
    },
    "EST_SINCRONISMO": {
        "titulo": "Criterio de estabilidad angular transitoria",
        "tipo": "supuesto_de_estudio",
        "valor": {"max_delta_coi_deg": 180.0},
        "descripcion": "Se considera pérdida de sincronismo si algún rotor supera 180° respecto del "
                       "centro de inercia (COI) durante la simulación.",
        "fuente": "Supuesto de estudio (criterio de práctica de ingeniería). NO normativo.",
        "url": None,
        "nota": "",
        "keywords": ["angular", "rotor", "sincronismo", "transitoria", "transient", "estabilidad"],
    },
    "EST_AMORTIGUAMIENTO": {
        "titulo": "Amortiguamiento mínimo de modos electromecánicos",
        "tipo": "supuesto_de_estudio",
        "valor": {"min_damping_pct": 5.0},
        "descripcion": "Razón de amortiguamiento mínima de 5 % para modos oscilatorios (0,1–2,5 Hz).",
        "fuente": "Supuesto de estudio (práctica habitual). NO normativo: confirmar con PR COES.",
        "url": None,
        "nota": "",
        "keywords": ["amortiguamiento", "damping", "pequeña señal", "small-signal", "modos", "oscilación"],
    },
    "EST_RECUPERACION_TENSION": {
        "titulo": "Recuperación de tensión post-falla",
        "tipo": "supuesto_de_estudio",
        "valor": {"min_pu": 0.80, "tiempo_s_tras_despeje": 1.0},
        "descripcion": "Todas las barras deben recuperar al menos 0,80 p.u. 1 s después del despeje de la falla.",
        "fuente": "Supuesto de estudio. NO normativo: confirmar con PR COES.",
        "url": None,
        "nota": "",
        "keywords": ["recuperacion", "recuperación", "tension", "tensión", "falla", "transitoria"],
    },
}


def query_limits(topic: str = "") -> dict:
    """Recupera criterios por palabra clave (memoria semántica simple, sin embeddings)."""
    topic_l = (topic or "").lower()
    if not topic_l.strip():
        hits = CRITERIA
    else:
        terms = [w for w in topic_l.replace(",", " ").split() if len(w) > 2]
        hits = {cid: c for cid, c in CRITERIA.items()
                if any(t in kw or kw in t for t in terms for kw in c["keywords"])
                or any(t in cid.lower() for t in terms)}
    return {
        "kb_version": KB_VERSION,
        "criterios": [{"id": cid, **{k: v for k, v in c.items() if k != "keywords"}}
                      for cid, c in hits.items()],
        "nota": "Citar 'fuente' y 'kb_version'. Los criterios tipo 'supuesto_de_estudio' no son normativos.",
    }


def voltage_violations(vm_by_bus: dict, criterion_id: str) -> list[dict]:
    band = CRITERIA[criterion_id]["valor"]
    out = []
    for bus, vm in vm_by_bus.items():
        if vm < band["min_pu"] or vm > band["max_pu"]:
            out.append({"bus": bus, "vm_pu": round(float(vm), 4),
                        "limite": f"[{band['min_pu']}, {band['max_pu']}] p.u.",
                        "criterio": criterion_id})
    return out

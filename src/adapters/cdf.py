"""Lector del formato IEEE Common Data Format (CDF).

Convierte un archivo CDF a un caso PYPOWER/MATPOWER (``ppc``) que pandapower
puede importar, y conserva la solución de referencia que trae el archivo
(tensiones y ángulos finales) para validar el modelo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Tipos de barra CDF -> MATPOWER (1=PQ, 2=PV, 3=slack)
_CDF_TO_PPC_BUS_TYPE = {0: 1, 1: 1, 2: 2, 3: 3}
# Tensión base asumida cuando el CDF no la informa (el caso se trabaja en p.u.)
DEFAULT_BASE_KV = 135.0


@dataclass
class CdfCase:
    title: str
    base_mva: float
    ppc: dict
    bus_names: dict[int, str]
    reference: dict[int, dict] = field(default_factory=dict)  # bus -> {vm_pu, va_degree}
    warnings: list[str] = field(default_factory=list)


def _section(lines: list[str], header: str) -> list[str]:
    start = next(i for i, ln in enumerate(lines) if ln.upper().startswith(header))
    out = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("-9"):
            break
        out.append(ln)
    return out


def decode_cdf(data: bytes) -> str:
    """Los CDF del archivo UW son ASCII; algunos editores los guardan en Latin-1."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_cdf(text: str) -> CdfCase:
    lines = text.splitlines()
    title_line = lines[0]
    base_mva = float(title_line[31:37])
    title = title_line[45:].strip() or "IEEE CDF case"
    warnings: list[str] = []

    buses, gens, names, reference = [], [], {}, {}
    for ln in _section(lines, "BUS DATA FOLLOWS"):
        num = int(ln[0:4])
        names[num] = ln[5:17].strip()
        t = ln[17:].split()
        area, zone, btype = int(t[0]), int(t[1]), int(t[2])
        vm, va = float(t[3]), float(t[4])
        pl, ql, pg, qg = map(float, t[5:9])
        base_kv = float(t[9])
        v_set, qmax, qmin = float(t[10]), float(t[11]), float(t[12])
        g_sh, b_sh = float(t[13]), float(t[14])
        if base_kv <= 0:
            base_kv = DEFAULT_BASE_KV
        reference[num] = {"vm_pu": vm, "va_degree": va}
        ppc_type = _CDF_TO_PPC_BUS_TYPE.get(btype, 1)
        buses.append([num, ppc_type, pl, ql, g_sh * base_mva, b_sh * base_mva,
                      area, vm, va, base_kv, zone, 1.1, 0.9])
        if btype in (2, 3):
            if btype == 3 and qmax == 0 and qmin == 0:
                qmax, qmin = 9999.0, -9999.0
                warnings.append(f"Barra {num} (slack) sin límites Q en el CDF: se asumen ilimitados.")
            gens.append([num, pg, qg, qmax, qmin, v_set or vm, base_mva, 1,
                         9999.0, 0.0] + [0.0] * 11)

    branches = []
    for ln in _section(lines, "BRANCH DATA FOLLOWS"):
        t = ln.split()
        f_bus, t_bus = int(t[0]), int(t[1])
        r, x, b = float(t[6]), float(t[7]), float(t[8])
        rate_a, rate_b, rate_c = float(t[9]), float(t[10]), float(t[11])
        ratio, shift = float(t[14]), float(t[15])
        branches.append([f_bus, t_bus, r, x, b, rate_a, rate_b, rate_c,
                         ratio, shift, 1, -360, 360])

    if all(br[5] == 0 for br in branches):
        warnings.append("El CDF no informa capacidades térmicas (rateA=0): "
                        "no se evalúa cargabilidad porcentual de ramas.")

    ppc = {
        "version": "2",
        "baseMVA": base_mva,
        "bus": np.array(buses, dtype=float),
        "gen": np.array(gens, dtype=float),
        "branch": np.array(branches, dtype=float),
    }
    return CdfCase(title=title, base_mva=base_mva, ppc=ppc, bus_names=names,
                   reference=reference, warnings=warnings)

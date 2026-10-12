"""Adaptador ANDES: estabilidad transitoria (TDS) y pequeña señal (valores propios)."""

from __future__ import annotations

import numpy as np

import andes

from src.adapters.base import SimResult, SimulationInputError, Timer, case_file, sha256_file
from src.analysis.criteria import CRITERIA, NOMINAL_FREQ_HZ

SIMULATOR = "andes"
CASE_ID = "ieee14_dyn"
MAX_T_END = 30.0

andes.config_logger(stream_level=40)


def _load(case_id: str):
    path = case_file(case_id, SIMULATOR)
    ss = andes.load(str(path), setup=False, no_output=True, default_config=True)
    if ss is None:
        raise SimulationInputError(f"ANDES no pudo cargar el caso '{case_id}'.")
    return ss, sha256_file(path)


def describe_dynamic_case(case_id: str = CASE_ID) -> dict:
    ss, _ = _load(case_id)
    ss.setup()
    lines = [{"idx": i, "bus1": int(b1), "bus2": int(b2)}
             for i, b1, b2 in zip(ss.Line.idx.v, ss.Line.bus1.v, ss.Line.bus2.v)]
    gens = [{"idx": i, "bus": int(b)} for i, b in zip(ss.GENROU.idx.v, ss.GENROU.bus.v)]
    return {"case_id": case_id, "barras": [int(b) for b in ss.Bus.idx.v], "lineas": lines, "generadores": gens}


def _resolve_line(ss, trip_branch: str | None):
    if not trip_branch:
        return None
    if trip_branch in ss.Line.idx.v:
        return trip_branch
    try:
        a, b = (int(x) for x in trip_branch.replace("L", "").replace("T", "").split("-"))
    except ValueError:
        raise SimulationInputError(f"trip_branch '{trip_branch}' inválido. Use 'Line_N' o 'i-j' (p.ej. '4-9').")
    for i, b1, b2 in zip(ss.Line.idx.v, ss.Line.bus1.v, ss.Line.bus2.v):
        if {int(b1), int(b2)} == {a, b}:
            return i
    raise SimulationInputError(f"No existe rama entre barras {a} y {b} en el caso dinámico.")


def _downsample(t, y, n=240):
    step = max(1, len(t) // n)
    return [round(float(v), 5) for v in t[::step]], [[round(float(v), 5) for v in col] for col in y[::step].T]


def run_transient_stability(fault_bus: int, t_fault: float = 1.0, t_clear: float = 1.1,
                            trip_branch: str | None = None, t_end: float = 10.0,
                            case_id: str = CASE_ID) -> SimResult:
    if not (0.1 <= t_fault < t_clear):
        raise SimulationInputError("Se requiere 0.1 <= t_fault < t_clear.")
    if t_clear - t_fault > 1.0:
        raise SimulationInputError("Duración de falla > 1 s fuera del rango de estudio.")
    if not (t_clear + 1.0 <= t_end <= MAX_T_END):
        raise SimulationInputError(f"t_end debe estar entre t_clear+1 y {MAX_T_END} s.")

    ss, digest = _load(case_id)
    buses = [int(b) for b in ss.Bus.idx.v]
    if int(fault_bus) not in buses:
        raise SimulationInputError(f"La barra {fault_bus} no existe en el caso '{case_id}' (barras {min(buses)}-{max(buses)}).")
    ss.add("Fault", {"bus": int(fault_bus), "tf": float(t_fault), "tc": float(t_clear)})
    line = _resolve_line(ss, trip_branch)
    if line is not None:
        ss.add("Toggle", {"model": "Line", "dev": line, "t": float(t_clear)})
    ss.setup()
    ss.PFlow.run()
    if not ss.PFlow.converged:
        raise SimulationInputError("El flujo de potencia inicial del caso dinámico no convergió.")
    ss.TDS.config.tf = float(t_end)
    ss.TDS.config.no_tqdm = 1
    with Timer() as tm:
        ss.TDS.run()

    ts = ss.dae.ts
    t = np.asarray(ts.t)
    delta = ts.get_data(ss.GENROU.delta)            # rad
    omega = ts.get_data(ss.GENROU.omega)            # p.u.
    vbus = ts.get_data(ss.Bus.v)                    # p.u.
    m = np.asarray(ss.GENROU.M.v, dtype=float)
    coi = (delta * m).sum(axis=1) / m.sum()
    dev_deg = np.degrees(delta - coi[:, None])
    f_coi = (omega * m).sum(axis=1) / m.sum() * NOMINAL_FREQ_HZ

    lim_ang = CRITERIA["EST_SINCRONISMO"]["valor"]["max_delta_coi_deg"]
    completed = bool(t[-1] >= t_end - 1e-3) and not ss.TDS.busted
    max_dev = float(np.abs(dev_deg).max())
    # ANDES detiene la simulación cuando detecta separación angular (config TDS.criteria)
    stopped_by_criteria = bool(not completed and t[-1] < t_end - 1e-3)
    stable = bool(completed and max_dev < lim_ang)

    sub = CRITERIA["NTCSE_FREC_SUBITA"]["valor"]
    sost = CRITERIA["NTCSE_FREC_SOSTENIDA"]["valor"]
    rec = CRITERIA["EST_RECUPERACION_TENSION"]["valor"]
    bus_ids = [int(b) for b in ss.Bus.idx.v]
    gen_ids = list(ss.GENROU.idx.v)

    post = t >= t_clear
    window = post if post.any() else t >= t_fault
    f_min, f_max = float(f_coi[window].min()), float(f_coi[window].max())
    f_final = float(f_coi[-1])
    t_rec = t_clear + rec["tiempo_s_tras_despeje"]
    rec_ok = t[-1] >= t_rec
    k = min(int(np.searchsorted(t, t_rec)), len(t) - 1)
    v_rec = vbus[k]

    def verdict(ok: bool, evaluable: bool = True) -> str:
        return ("CUMPLE" if ok else "NO CUMPLE") if evaluable else "NO EVALUABLE (simulación detenida)"

    summary = {
        "simulacion_completa": completed,
        "tiempo_final_simulado_s": round(float(t[-1]), 4),
        "detenida_por_perdida_de_sincronismo": stopped_by_criteria,
        "estable_angular": stable,
        "max_desviacion_angular_coi_deg": round(max_dev, 2),
        "generador_critico": gen_ids[int(np.abs(dev_deg).max(axis=0).argmax())],
        "frecuencia_hz": {"min_post_despeje": round(f_min, 4), "max_post_despeje": round(f_max, 4),
                          "final": round(f_final, 4)},
        "verificacion": {
            "EST_SINCRONISMO": verdict(stable),
            "NTCSE_FREC_SUBITA": verdict(sub["min_hz"] <= f_min and f_max <= sub["max_hz"], completed),
            "NTCSE_FREC_SOSTENIDA": verdict(sost["min_hz"] <= f_final <= sost["max_hz"], completed),
            "EST_RECUPERACION_TENSION": verdict(float(v_rec.min()) >= rec["min_pu"], rec_ok),
        },
        "tension_min_1s_tras_despeje": ({"barra": bus_ids[int(v_rec.argmin())], "vm_pu": round(float(v_rec.min()), 4)}
                                        if rec_ok else None),
        "nota": "La frecuencia se calcula como velocidad del centro de inercia (COI) de los generadores. "
                "Si la simulación se detiene por pérdida de sincronismo (ANDES detiene la integración cuando la "
                "separación angular entre máquinas supera 180°), los criterios posteriores no son evaluables.",
    }
    tt, dev_series = _downsample(t, dev_deg)
    _, f_series = _downsample(t, f_coi[:, None])
    _, v_series = _downsample(t, vbus)
    artifacts = {"t": tt, "angulos_coi_deg": dict(zip(gen_ids, dev_series)),
                 "frecuencia_coi_hz": f_series[0], "tensiones_pu": dict(zip(map(str, bus_ids), v_series))}
    params = {"fault_bus": int(fault_bus), "t_fault": t_fault, "t_clear": t_clear,
              "duracion_falla_ms": round((t_clear - t_fault) * 1000, 1), "trip_branch": line,
              "t_end": t_end, "metodo": ss.TDS.config.method}
    return SimResult(SIMULATOR, andes.__version__, "transient_stability", case_id,
                     digest, params, summary, artifacts, tm.elapsed)


def run_small_signal_analysis(case_id: str = CASE_ID) -> SimResult:
    ss, digest = _load(case_id)
    ss.setup()
    ss.PFlow.run()
    with Timer() as tm:
        ok = ss.EIG.run()
    if not ok:
        raise SimulationInputError("El análisis de valores propios no se pudo ejecutar.")
    mu = ss.EIG.mu
    freq, _, damping, _ = ss.EIG.post_process()
    pf = np.abs(ss.EIG.pfactors)  # filas: modos, columnas: estados
    names = getattr(ss.EIG, "x_name", None)
    if names is None or len(names) != pf.shape[1]:
        names = ss.dae.x_name
    names = list(names)
    tol = ss.EIG.config.tol
    min_d = CRITERIA["EST_AMORTIGUAMIENTO"]["valor"]["min_damping_pct"]

    modes = []
    for i, lam in enumerate(mu):
        if lam.imag <= 0:
            continue  # un representante por par conjugado
        part = pf[i]
        top_idx = np.argsort(part)[::-1][:3]
        modes.append({
            "modo": i + 1, "real": round(float(lam.real), 4), "imag": round(float(lam.imag), 4),
            "frecuencia_hz": round(float(freq[i]), 3), "amortiguamiento_pct": round(float(damping[i]), 2),
            "electromecanico": bool(0.1 <= freq[i] <= 2.5),
            "estados_dominantes": [names[j] for j in top_idx],
        })
    modes.sort(key=lambda m: m["amortiguamiento_pct"])
    unstable = [round(float(l.real), 4) for l in mu if l.real > tol]
    em = [m for m in modes if m["electromecanico"]]
    poor = [m for m in em if m["amortiguamiento_pct"] < min_d]
    summary = {
        "n_estados": int(len(mu)),
        "autovalores_parte_real_positiva": unstable,
        "estable_pequena_senal": not unstable,
        "modos_electromecanicos": len(em),
        "modos_bajo_amortiguamiento_minimo": poor,
        "verificacion": {"EST_AMORTIGUAMIENTO": "CUMPLE" if not poor else "NO CUMPLE"},
        "modos_menos_amortiguados": modes[:6],
    }
    return SimResult(SIMULATOR, andes.__version__, "small_signal", case_id,
                     digest, {"tol": tol, "umbral_amortiguamiento_pct": min_d},
                     summary, {"modos": modes}, tm.elapsed)

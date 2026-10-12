"""Adaptador pandapower: flujo de potencia (Newton-Raphson) y contingencias N-1."""

from __future__ import annotations

import copy
import math

import pandapower as pp
import pandapower.topology as top
from pandapower.converter.pypower import from_ppc

from src.adapters.base import SimResult, SimulationInputError, Timer, case_file, sha256_file
from src.adapters.cdf import decode_cdf, parse_cdf
from src.analysis.criteria import voltage_violations

SIMULATOR = "pandapower"


def _build(case_id: str, load_scale: float):
    if not 0.5 <= load_scale <= 1.5:
        raise SimulationInputError("load_scale fuera de rango permitido [0.5, 1.5].")
    path = case_file(case_id, SIMULATOR)
    case = parse_cdf(decode_cdf(path.read_bytes()))
    net = from_ppc(case.ppc, f_hz=60, validate_conversion=False)
    # from_ppc usa el número de barra del CDF como índice de net.bus
    bus_no = {int(i): int(i) for i in net.bus.index}
    net.load["p_mw"] *= load_scale
    net.load["q_mvar"] *= load_scale
    return net, case, bus_no, sha256_file(path)


def _branches(net, bus_no):
    out = []
    for idx, row in net.line.iterrows():
        out.append(("line", idx, f"L{bus_no[row.from_bus]}-{bus_no[row.to_bus]}"))
    for idx, row in net.trafo.iterrows():
        out.append(("trafo", idx, f"T{bus_no[row.hv_bus]}-{bus_no[row.lv_bus]}"))
    return out


def _flows(net, bus_no):
    flows = {}
    for kind, idx, name in _branches(net, bus_no):
        res = net.res_line if kind == "line" else net.res_trafo
        p = res.at[idx, "p_from_mw" if kind == "line" else "p_hv_mw"]
        q = res.at[idx, "q_from_mvar" if kind == "line" else "q_hv_mvar"]
        pl = res.at[idx, "pl_mw"]
        if p is None or (isinstance(p, float) and math.isnan(p)):
            continue
        flows[name] = {"p_mw": round(float(p), 2), "q_mvar": round(float(q), 2),
                       "s_mva": round(math.hypot(p, q), 2), "perdidas_mw": round(float(pl), 3)}
    return flows


def _vm(net, bus_no):
    return {bus_no[i]: float(v) for i, v in net.res_bus.vm_pu.items() if not math.isnan(v)}


def run_loadflow(case_id: str = "ieee14_cdf", load_scale: float = 1.0) -> SimResult:
    net, case, bus_no, digest = _build(case_id, load_scale)
    with Timer() as t:
        pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, max_iteration=30)
    vm = _vm(net, bus_no)
    va = {bus_no[i]: float(a) for i, a in net.res_bus.va_degree.items()}
    gen = []
    for i, row in net.res_ext_grid.iterrows():
        gen.append({"barra": bus_no[net.ext_grid.at[i, "bus"]], "tipo": "slack",
                    "p_mw": round(row.p_mw, 2), "q_mvar": round(row.q_mvar, 2)})
    for i, row in net.res_gen.iterrows():
        gen.append({"barra": bus_no[net.gen.at[i, "bus"]], "tipo": "PV",
                    "p_mw": round(row.p_mw, 2), "q_mvar": round(row.q_mvar, 2)})
    flows = _flows(net, bus_no)

    validation = None
    # Solo si el CDF trae una solución resuelta (no un arranque plano 1,0 p.u. / 0°)
    solved = any(r["va_degree"] != 0 for r in case.reference.values())
    if load_scale == 1.0 and solved:
        dv = max(abs(vm[b] - r["vm_pu"]) for b, r in case.reference.items() if b in vm)
        da = max(abs(va[b] - r["va_degree"]) for b, r in case.reference.items() if b in va)
        validation = {"referencia": "Solución de flujo incluida en el archivo CDF",
                      "max_error_vm_pu": round(dv, 4), "max_error_va_deg": round(da, 3),
                      "valido": dv < 0.01 and da < 0.5}

    summary = {
        "convergio": bool(net.converged),
        "load_scale": load_scale,
        "demanda_total_mw": round(float(net.load.p_mw.sum()), 2),
        "generacion_total_mw": round(sum(g["p_mw"] for g in gen), 2),
        "perdidas_totales_mw": round(sum(f["perdidas_mw"] for f in flows.values()), 3),
        "tension_min": {"barra": min(vm, key=vm.get), "vm_pu": round(min(vm.values()), 4)},
        "tension_max": {"barra": max(vm, key=vm.get), "vm_pu": round(max(vm.values()), 4)},
        "violaciones_NTCSE_TENSION": voltage_violations(vm, "NTCSE_TENSION"),
        "generadores": gen,
        "ramas_mas_cargadas_mva": dict(sorted(flows.items(), key=lambda kv: -kv[1]["s_mva"])[:5]),
        "validacion_modelo": validation,
        "advertencias_datos": case.warnings,
    }
    artifacts = {"tensiones": {b: {"vm_pu": round(vm[b], 4), "va_deg": round(va[b], 3)} for b in sorted(vm)},
                 "flujos": flows}
    return SimResult(SIMULATOR, pp.__version__, "loadflow", case_id, digest,
                     {"load_scale": load_scale, "algoritmo": "Newton-Raphson"}, summary, artifacts, t.elapsed)


def run_contingency_n1(case_id: str = "ieee14_cdf", load_scale: float = 1.0,
                       include_generators: bool = True) -> SimResult:
    base, case, bus_no, digest = _build(case_id, load_scale)
    pp.runpp(base, algorithm="nr", calculate_voltage_angles=True)
    base_flows = _flows(base, bus_no)
    # Violaciones ya presentes en el caso base (p.ej. consignas de generación > 1,05 p.u.)
    base_viol = {v["bus"]: v["vm_pu"] for v in voltage_violations(_vm(base, bus_no), "NTCSE_TENSION")}

    cont = [(k, i, n) for k, i, n in _branches(base, bus_no)]
    if include_generators:
        cont += [("gen", i, f"G{bus_no[row.bus]}") for i, row in base.gen.iterrows()]

    results = []
    with Timer() as t:
        for kind, idx, name in cont:
            net = copy.deepcopy(base)
            getattr(net, kind).at[idx, "in_service"] = False
            islanded = sorted(bus_no[b] for b in top.unsupplied_buses(net))
            entry = {"contingencia": name, "tipo": kind}
            try:
                pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, max_iteration=30)
            except Exception as exc:  # no convergencia u otra falla numérica
                entry.update({"convergio": False, "severidad": "CRITICA",
                              "detalle": f"Flujo no convergió: {type(exc).__name__}"})
                results.append(entry)
                continue
            vm = _vm(net, bus_no)
            emerg = voltage_violations(vm, "EST_TENSION_EMERGENCIA")
            # solo violaciones NTCSE nuevas o agravadas (> 0,005 p.u.) respecto del caso base
            ntcse = [v for v in voltage_violations(vm, "NTCSE_TENSION")
                     if v["bus"] not in base_viol or abs(v["vm_pu"] - 1) > abs(base_viol[v["bus"]] - 1) + 0.005]
            flows = _flows(net, bus_no)
            # Mayor incremento de flujo respecto del caso base (sin capacidades térmicas en el CDF)
            incr = sorted(((n, f["s_mva"] - base_flows.get(n, {}).get("s_mva", 0.0)) for n, f in flows.items()),
                          key=lambda x: -x[1])[:2]
            if islanded:
                sev = "ALTA"
            elif emerg:
                sev = "ALTA"
            elif ntcse:
                sev = "MEDIA"
            else:
                sev = "BAJA"
            entry.update({
                "convergio": True, "severidad": sev,
                "barras_aisladas": islanded,
                "vm_min": {"barra": min(vm, key=vm.get), "vm_pu": round(min(vm.values()), 4)},
                "vm_max": {"barra": max(vm, key=vm.get), "vm_pu": round(max(vm.values()), 4)},
                "violaciones_emergencia": emerg, "violaciones_NTCSE_nuevas": ntcse,
                "mayor_incremento_flujo_mva": [{"rama": n, "delta_mva": round(d, 2)} for n, d in incr],
            })
            results.append(entry)

    order = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3}
    results.sort(key=lambda r: (order[r["severidad"]], r.get("vm_min", {}).get("vm_pu", 0)))
    counts = {s: sum(r["severidad"] == s for r in results) for s in order}
    summary = {
        "contingencias_evaluadas": len(results),
        "conteo_por_severidad": counts,
        "cumple_N1": counts["CRITICA"] == 0 and counts["ALTA"] == 0,
        "criterios": ["COES_N1", "EST_TENSION_EMERGENCIA", "NTCSE_TENSION"],
        "violaciones_NTCSE_caso_base": base_viol,
        "regla_severidad": "CRITICA: no converge · ALTA: barras aisladas o fuera de banda de emergencia · "
                           "MEDIA: violación NTCSE nueva o agravada vs caso base · BAJA: sin violaciones nuevas",
        "peores_5": results[:5],
        "advertencias_datos": case.warnings + [
            "Severidad ALTA por barras aisladas indica pérdida de suministro o generación aislada."],
    }
    return SimResult(SIMULATOR, pp.__version__, "contingency_n1", case_id, digest,
                     {"load_scale": load_scale, "include_generators": include_generators},
                     summary, {"todas": results}, t.elapsed)

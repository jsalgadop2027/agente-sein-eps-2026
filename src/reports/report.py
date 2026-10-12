"""Generación de informes técnicos trazables (Markdown) con manifiesto por estudio."""

from __future__ import annotations

import json

from src.adapters.base import SimulationInputError
from src.analysis.criteria import CRITERIA, KB_VERSION
from src.memory.store import new_id, now_iso, store
from src.memory.studies import get_study


def _verification_rows(summary: dict) -> list[str]:
    rows = []
    for cid, status in (summary.get("verificacion") or {}).items():
        c = CRITERIA.get(cid, {})
        rows.append(f"| {cid} | {c.get('tipo', '-')} | {status} | {c.get('fuente', '-')} |")
    if "cumple_N1" in summary:
        c = CRITERIA["COES_N1"]
        rows.append(f"| COES_N1 | normativo | {'CUMPLE' if summary['cumple_N1'] else 'NO CUMPLE'} | {c['fuente']} |")
    return rows


def build_report(session: dict, args: dict, approval: dict) -> dict:
    signer = (approval.get("signer") or "").strip()
    if not approval.get("approved") or not signer:
        raise SimulationInputError("El informe requiere aprobación y firma (nombre) del ingeniero responsable.")
    own = set(session.get("study_ids", []))
    ids = [s for s in args.get("study_ids", []) if s in own]
    rejected = [s for s in args.get("study_ids", []) if s not in own]
    if not ids:
        raise SimulationInputError("Ningún study_id válido de esta sesión: ejecute estudios antes de emitir el informe.")

    report_id = new_id("INF")
    md = [
        f"# {args['title']}",
        "",
        f"**Informe:** {report_id}  ",
        f"**Fecha (UTC):** {now_iso()}  ",
        f"**Proyecto:** {session['project_id']} · **Sesión:** {session['session_id']}  ",
        f"**Preparado por:** GenEPS (agente IA) · **Revisado y firmado por:** {signer}  ",
        f"**Comentario del aprobador:** {approval.get('comment') or '-'}  ",
        f"**Base normativa:** kb_version {KB_VERSION}",
        "",
        "> Documento de apoyo técnico generado sobre sistemas de prueba. Sus conclusiones son recomendaciones "
        "y no sustituyen la revisión del especialista ni constituyen una decisión operativa.",
        "",
        "## Resumen ejecutivo",
        args["executive_summary"],
        "",
        "## Estudios incluidos",
    ]
    for sid in ids:
        m = get_study(sid)
        if not m:
            continue
        s = m["summary"]
        md += [
            f"### {sid} · {m['study_type']}",
            "",
            "| Campo | Valor |", "|---|---|",
            f"| Caso | {m['case_id']} |",
            f"| SHA-256 del caso | `{m['case_sha256']}` |",
            f"| Simulador | {m['simulator']} {m['simulator_version']} (Python {m['python_version']}) |",
            f"| Parámetros | `{json.dumps(m['parameters'], ensure_ascii=False)}` |",
            f"| Fecha | {m['timestamp']} · duración {m['duration_s']} s |",
            f"| Aprobación previa | {(m.get('aprobacion') or {}).get('signer', 'no requerida')} |",
            "",
        ]
        rows = _verification_rows(s)
        if rows:
            md += ["| Criterio | Tipo | Resultado | Fuente |", "|---|---|---|---|", *rows, ""]
        md += ["<details><summary>Resultados (resumen)</summary>", "", "```json",
               json.dumps(s, ensure_ascii=False, indent=1, default=str)[:6000], "```", "</details>", ""]
    md += ["## Conclusiones", *[f"{i}. {c}" for i, c in enumerate(args.get("conclusions", []), 1)], "",
           "## Supuestos, limitaciones e incertidumbres", *[f"- {x}" for x in args.get("limitations", [])], ""]
    if rejected:
        md += [f"_Se omitieron identificadores no pertenecientes a la sesión: {', '.join(rejected)}._", ""]

    text = "\n".join(md)
    store.put_text(f"reports/{report_id}.md", text, content_type="text/markdown; charset=utf-8")
    store.audit("informe_emitido", report_id=report_id, session_id=session["session_id"],
                firmante=signer, estudios=ids)
    session.setdefault("reports", []).append(report_id)
    return {"report_id": report_id, "url": f"/api/reports/{report_id}", "estudios_incluidos": ids,
            "firmado_por": signer}

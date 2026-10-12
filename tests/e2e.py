"""Prueba de punta a punta contra el servicio desplegado (solo biblioteca estándar).

Uso:
  python tests/e2e.py https://geneps-xxxx.run.app "$(gcloud secrets versions access latest --secret geneps-ui-password-us)"
Recorre: sesión → N-1 → transitoria (aprobación) → informe (firma).
"""

import base64
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE, PASSWORD = sys.argv[1].rstrip("/"), sys.argv[2]
AUTH = "Basic " + base64.b64encode(f"geneps:{PASSWORD}".encode()).decode()


def call(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": AUTH, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        raw = r.read().decode()
        return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw


def show(r):
    for st in r.get("steps", []):
        if st["tool_calls"]:
            print("  tools:", [c["name"] for c in st["tool_calls"]])
        if st["text"]:
            print("  >>", st["text"][:1500].replace("\n", "\n     "))
    for k in ("status", "warning", "error", "guardrails"):
        if r.get(k):
            print(f"  [{k}] {r[k]}")


def approve_all(sid, r, signer="Ing. Revisor Demo"):
    while r.get("status") == "awaiting_approval":
        dec = {a["tool_use_id"]: {"approved": True, "signer": signer, "comment": "Aprobado en prueba e2e"}
               for a in r["approvals"]}
        print("  APROBANDO:", [(a["tool"], a["input"].get("fault_bus", a["input"].get("title"))) for a in r["approvals"]])
        r = call("/api/approve", {"session_id": sid, "decisions": dec})
        show(r)
    return r


try:
    print("health:", call("/api/health"))
except Exception as exc:  # revisiones antiguas no exponen /api/health
    print("health: no disponible", exc)
PROMPTS = [
    "Valida el caso base y ejecuta el análisis N-1. ¿Cuáles son las contingencias críticas?",
    "Evalúa la estabilidad transitoria ante una falla trifásica en la barra 4 despejada en 100 ms con disparo de la línea 4-5.",
    "Prepara el informe técnico con los estudios realizados.",
]
# Opcional: continuar una sesión existente desde el prompt N (argv[3]=session_id, argv[4]=N)
START = int(sys.argv[4]) if len(sys.argv) > 4 else 0
sid = sys.argv[3] if len(sys.argv) > 3 else call("/api/sessions", {"user": "Prueba e2e", "role": "Ingeniero de planificación", "project_id": "e2e",
                             "objetivo_estudio": "Evaluar la seguridad N-1 y la estabilidad transitoria",
                             "escenario": "IEEE 14 barras, demanda base"})["session_id"]
print("session:", sid)

for prompt in PROMPTS[START:]:
    print("\nUSER:", prompt)
    r = call("/api/chat", {"session_id": sid, "message": prompt})
    show(r)
    r = approve_all(sid, r)

s = call(f"/api/sessions/{sid}")
print("\nestudios:", [e["study_type"] for e in s["events"]], "informes:", s["reports"], "uso:", s["usage"])
if s["reports"]:
    print(call(f"/api/reports/{s['reports'][-1]}")[:2500])

# GenEPS — Agente de estudios de estabilidad SEP (SEIN)

Agente de IA **semi-autónomo con aprobaciones** que prepara, ejecuta e interpreta estudios de
flujo de potencia, contingencias N-1, estabilidad transitoria y pequeña señal sobre sistemas de
prueba IEEE, verificando los resultados contra criterios NTCSE/COES y emitiendo informes
trazables firmados por un ingeniero.

- **LLM:** Claude Opus 5.5 (`claude-opus-5-5`) vía Claude API, con *adaptive thinking*, *prompt caching* y *fallback* de seguridad del lado del servidor.
- **Simuladores (licencia libre):** pandapower 3.5.6 (estático) y ANDES 2.0.0 (dinámico).
- **Plataforma:** Google Cloud Run + Secret Manager + Cloud Storage (`agente-sein-eps-2026`, `us-east1`).

Trabajo final del curso **Diseño e Implementación de Agentes de IA** (UTEC), octubre de 2026.
Autores: Julio Salgado, Manuel Cabrera, Luis Oporto y Christian Monrroy.

## Arquitectura

```text
Ingeniero (navegador)
   |  HTTPS + usuario/contraseña (HTTP Basic, contraseña en Secret Manager)
   v
Cloud Run "geneps"  (FastAPI, usuario sin privilegios)
   |-- Agente (src/agent)          bucle Claude + herramientas + pausas de aprobación
   |     |-- prompts.py            system prompt dinámico (plantilla Profile Card §2.3)
   |     |-- tools.py              10 herramientas con JSON Schema estricto
   |     `-- orchestrator.py       historial append-only, guardrails, auditoría
   |-- Adaptadores (src/adapters)  contrato común SimResult
   |     |-- base.py / upload.py   registro de casos: incluidos + subidos (validados al subir)
   |     |-- pandapower_adapter    run_loadflow, run_contingency_n1
   |     |-- andes_adapter         run_transient_stability, run_small_signal_analysis
   |     `-- cdf.py                lector IEEE CDF + validación contra solución publicada
   |-- Base normativa (src/analysis/criteria.py)   NTCSE / COES / supuestos, versionada
   |-- Workflow batch  POST /api/jobs/screening     determinista, sin LLM
   `-- Informes (src/reports)       Markdown con manifiesto por estudio + PDF de la conversación
   |
   |-- Secret Manager: anthropic-api-key-us, geneps-ui-password-us  (solo lectura para la cuenta geneps-run)
   `-- Cloud Storage: gs://agente-sein-eps-2026-geneps-us
         sessions/ studies/ reports/ jobs/ audit/ projects/ cases/
```

### Estructura del repositorio

```text
src/                  código de la aplicación (ver arquitectura); src/static/index.html es la interfaz web
data/ieee14cdf.txt    caso incluido IEEE 14 barras (lo leen el Dockerfile, los adaptadores y las pruebas)
tests/smoke_sims.py   prueba de humo de los simuladores, guardrails y PDF (sin LLM ni GCS)
tests/e2e.py          prueba de punta a punta contra el servicio desplegado
Dockerfile            imagen Python 3.13 para Cloud Run
deploy.sh             despliegue en Cloud Run (secretos desde Secret Manager)
cloudbuild.test.yaml  build de la imagen + prueba de humo en Cloud Build
.env.example          plantilla de variables para ejecución local
```

## Correspondencia con la Profile Card

| Capa de la Profile Card | Implementación |
|---|---|
| 1. Comunicación | Chat web + API REST (conversacional) y `/api/jobs/screening` (no conversacional, apto para Cloud Scheduler). Acceso solo con usuario/contraseña (HTTP Basic sobre HTTPS). |
| 2. Contexto | Variables de sesión obligatorias (`objetivo_estudio`, `escenario`) y opcionales; si faltan, el agente las pide. Ensamblaje: identidad → variables → criterios → memoria → herramientas → resultados → guardrails → formato. |
| 3. Entorno | Herramientas `run_loadflow`, `run_contingency_n1`, `run_transient_stability`, `run_small_signal_analysis`, `query_limits`, `search_past_studies`, `get_study`, `save_project_note`, `export_report`, `list_cases`. |
| 4. Memoria | Corto plazo: historial por sesión. Episódica: manifiesto por estudio. Semántica: base normativa versionada. Largo plazo: notas por proyecto. Procedural: metodología en el prompt. |
| 5. Autonomía | Ejecuta estudios estáticos y pequeña señal por sí mismo; **pausa** para aprobar la simulación transitoria, para guardar notas en la memoria del proyecto y para **firmar** el informe. Sin escritura ni comandos sobre la red real. |
| 6. Criticidad | Validación de entradas por herramienta, abstención ante no convergencia, criterio normativo vs supuesto explícito, redacción de secretos/correos, auditoría en `audit/`, manifiesto con SHA-256 del caso y versión del simulador. Guardrails en código (`src/agent/guardrails.py`): memoria del proyecto entregada como datos y no en el system prompt, notas de proyecto con aprobación humana, texto de archivos subidos saneado, máximo 2 intentos fallidos por herramienta y turno, y verificación de que cada `study_id` citado exista en el proyecto (aviso en la UI y en el PDF). |

## Uso

### Abrir la aplicación

Abrir directamente en el navegador <https://geneps-242712336860.us-east1.run.app>. El navegador pide
usuario y contraseña: usuario `geneps`; la contraseña se consulta con

```bash
gcloud secrets versions access latest --secret geneps-ui-password-us --project agente-sein-eps-2026
```

Sin contraseña válida todas las rutas (incluidas `/docs` y la API) responden 401.

### Archivos de datos a evaluar

En el formulario de nueva sesión, el recuadro **Archivos de datos a evaluar** tiene:

- **Caso estático** (flujo de potencia y N-1) y **Caso dinámico** (transitoria y pequeña señal): selectores con los casos incluidos y los subidos.
- **Subir archivo nuevo**: IEEE CDF (`.txt`, `.cdf`) para estudios estáticos o ANDES (`.xlsx`) para dinámicos, máx. 5 MB.
  El archivo se valida con su simulador antes de guardarse (CDF: lectura y conversión a pandapower;
  ANDES: carga, generadores GENROU y convergencia del flujo inicial). Si es válido queda registrado en
  `cases/{case_id}/` con su SHA-256 y se selecciona automáticamente. Subir el mismo archivo dos veces
  reutiliza el caso existente.

El agente recibe los `case_id` elegidos en su contexto y los pasa a las herramientas; cada estudio
guarda el `case_id` y el SHA-256 del archivo usado.

### Descargar la conversación en PDF

En el panel de la sesión, el botón **⬇ Descargar PDF** se habilita cuando el agente respondió al menos
una consulta. Genera al instante un PDF con los datos de la sesión (usuario, proyecto, objetivo,
escenario, casos y modelo), cada consulta numerada con la respuesta del agente (tablas, listas y código
incluidos), las herramientas usadas y la lista de estudios ejecutados. Es un documento de trabajo: no
reemplaza el informe técnico firmado que emite `export_report`.

### Flujo de demostración sugerido

1. Pulsar **Cargar archivo** (objetivo y escenario ya vienen precargados; los casos por defecto son IEEE 14 barras). **Cargar nuevo archivo** vuelve al formulario para empezar otra sesión.
2. "Valida el caso base y ejecuta el análisis N-1. ¿Cuáles son las contingencias críticas?"
3. "Evalúa la estabilidad transitoria ante una falla trifásica en la barra 4 despejada en 100 ms con disparo de la línea 4-5." → **aprobar** con nombre.
4. "Revisa el amortiguamiento de los modos electromecánicos."
5. "Prepara el informe técnico con los estudios realizados." → **firmar**.
6. Abrir el estudio transitorio en el panel lateral para ver ángulos y frecuencia.

### API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/cases` | Lista los casos de datos (incluidos y subidos) |
| POST | `/api/cases` | Sube un caso: `{filename, content_base64, descripcion?, subido_por?}`; 422 si no es válido |
| POST | `/api/sessions` | Crea una sesión con variables de contexto y `caso_estatico` / `caso_dinamico` |
| POST | `/api/chat` | Turno conversacional |
| POST | `/api/approve` | Aprueba/rechaza acciones pendientes (`signer` obligatorio para aprobar) |
| GET | `/api/sessions/{id}` | Historial, aprobaciones pendientes, estudios, consumo |
| GET | `/api/sessions/{id}/pdf` | PDF con las consultas y respuestas hasta el momento (409 si aún no hay respuestas) |
| GET | `/api/studies/{id}` | Manifiesto y resultados completos de un estudio |
| GET | `/api/reports/{id}` | Informe en Markdown |
| POST | `/api/jobs/screening` | Workflow batch: flujo + N-1 por escenarios de demanda (`case_id` opcional) |
| GET | `/docs` | Swagger |

## Requisitos

- Python 3.13 (versión del contenedor).
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) con acceso al proyecto `agente-sein-eps-2026` (despliegue y pruebas en la nube).
- Clave de la Claude API de Anthropic.
- Docker (opcional, para construir la imagen en local).

## Ejecución local

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt python-dotenv
andes prepare -q                                       # una sola vez: genera el código numérico de ANDES
cp .env.example .env                                   # completar ANTHROPIC_API_KEY
uvicorn src.main:app --env-file .env --reload          # http://localhost:8000
```

Sin `GCS_BUCKET`, los datos se guardan en `.store/` (excluida del repositorio). La app no carga `.env`
por sí sola: `--env-file` de uvicorn lo hace y requiere `python-dotenv`. Si las extensiones nativas
de pandapower/ANDES no cargan en el equipo, usar Docker o Cloud Build.

### Variables de entorno

| Variable | Obligatoria | Por defecto | Descripción |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Sí | — | Clave de la Claude API (en Cloud Run, secreto `anthropic-api-key-us`) |
| `GENEPS_USER` | No | `geneps` | Usuario del login HTTP Basic |
| `GENEPS_PASSWORD` | En Cloud Run | vacío | Contraseña del login; vacía en local desactiva el login, en Cloud Run bloquea el acceso |
| `GENEPS_MODEL` | No | `claude-opus-5-5` | Modelo de Claude |
| `GENEPS_EFFORT` | No | `high` | Nivel de esfuerzo del modelo |
| `GENEPS_EFFORT_ROUTING` | No | `1` | `0` desactiva el ajuste automático de esfuerzo |
| `GCS_BUCKET` | No | — | Bucket de Cloud Storage; si no se define, se usa disco local |
| `LOCAL_STORE` | No | `.store` | Carpeta de almacenamiento local |

## Pruebas

```bash
# Prueba de humo (simuladores, rechazo de archivos inválidos, PDF y guardrails; sin LLM ni GCS)
python -m tests.smoke_sims
# La misma prueba dentro de la imagen, en Cloud Build
gcloud builds submit --config cloudbuild.test.yaml --project agente-sein-eps-2026 .
# Punta a punta contra el servicio desplegado: sesión → N-1 → transitoria (aprobación) → informe (firma)
python tests/e2e.py https://geneps-242712336860.us-east1.run.app "$(gcloud secrets versions access latest --secret geneps-ui-password-us --project agente-sein-eps-2026)"
```

`tests/e2e.py` solo usa la biblioteca estándar y consume la Claude API (tiene costo).

## Despliegue

Recursos creados una vez:

```bash
# secretos (se leen de stdin, no quedan en archivos)
gcloud secrets create anthropic-api-key-us --replication-policy=user-managed --locations=us-east1 --data-file=-
python -c "import secrets; print(secrets.token_urlsafe(18), end='')" |   gcloud secrets create geneps-ui-password-us --replication-policy=user-managed --locations=us-east1 --data-file=-
gcloud iam service-accounts create geneps-run
gcloud storage buckets create gs://agente-sein-eps-2026-geneps-us --location=us-east1 \
  --uniform-bucket-level-access --public-access-prevention
# permisos mínimos: solo los secretos y el bucket
gcloud secrets add-iam-policy-binding anthropic-api-key-us --member=serviceAccount:geneps-run@agente-sein-eps-2026.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding geneps-ui-password-us --member=serviceAccount:geneps-run@agente-sein-eps-2026.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor
gcloud storage buckets add-iam-policy-binding gs://agente-sein-eps-2026-geneps-us --member=serviceAccount:geneps-run@agente-sein-eps-2026.iam.gserviceaccount.com --role=roles/storage.objectAdmin
```

Despliegue / actualización: `bash deploy.sh`.
Prueba de los simuladores en la nube (sin LLM): `gcloud builds submit --region us-east1 --config cloudbuild.test.yaml .`

## Limitaciones conocidas

- Casos incluidos: IEEE 14 barras; no se usan datos reales del SEIN salvo que un ingeniero suba sus propios archivos.
- Formatos de carga: IEEE CDF (estático) y ANDES xlsx con generadores GENROU (dinámico). MATPOWER y PSS/E aún no se aceptan.
- La validación contra la solución de referencia solo se hace si el CDF trae un flujo resuelto (no un arranque plano).
- El CDF no trae capacidades térmicas: el N-1 evalúa convergencia, aislamiento y tensiones, no sobrecarga porcentual.
- La NTCSE aplica a puntos de entrega; aquí se usa como banda de referencia en todas las barras (supuesto declarado).
- El número de procedimiento COES del criterio N-1 figura como "POR CONFIRMAR" en la base normativa.
- La memoria semántica usa recuperación por palabras clave (sin embeddings) por el tamaño acotado de la base.
- Acceso con una única credencial compartida (HTTP Basic): no hay cuentas por usuario; la firma de informes registra el nombre que declara el ingeniero.
- Una sola instancia de Cloud Run (`max-instances=1`) para garantizar el bloqueo por sesión.
- Python 3.13 en el contenedor (las dependencias no se validaron en 3.14).

## Cómo contribuir

1. Trabajar en una rama propia (p. ej. `jsalgadop`) actualizada con `main`: `git pull origin main`.
2. Hacer commits pequeños y descriptivos; correr `python -m tests.smoke_sims` (o Cloud Build) antes de subir.
3. `git push` y abrir un Pull Request hacia `main`; fusionar tras revisión.
4. Nunca subir `.env`, llaves de cuentas de servicio ni datos de `.store/` (ver `.gitignore`).
   Las credenciales viven en Secret Manager.
5. El repositorio usa saltos de línea LF (`.gitattributes`): `deploy.sh` y la imagen corren en Linux.

## Autores

- Julio Salgado
- Manuel Cabrera
- Luis Oporto
- Christian Monrroy

## Licencia

Proyecto académico desarrollado para el curso Diseño e Implementación de Agentes de IA (UTEC).
Sin licencia abierta: todos los derechos reservados por los autores. pandapower (BSD-3-Clause) y
ANDES (GPL-3.0) conservan sus propias licencias.

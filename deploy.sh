#!/usr/bin/env bash
# Despliegue de GenEPS en Cloud Run (us-east1). Requiere que existan (ver README):
#   - secretos anthropic-api-key-us, geneps-ui-password-us   (Secret Manager, réplica en us-east1)
#   - cuenta   geneps-run@...      (con acceso solo a los secretos y al bucket)
#   - bucket   gs://$PROJECT-geneps-us
set -euo pipefail

PROJECT="${PROJECT:-agente-sein-eps-2026}"
REGION="${REGION:-us-east1}"
SUFFIX="${SUFFIX--us}"  # sufijo de secretos y bucket; SUFFIX= (vacío) usa los recursos sin sufijo
SA="geneps-run@${PROJECT}.iam.gserviceaccount.com"

gcloud run deploy geneps \
  --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$SA" \
  --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key${SUFFIX}:latest,GENEPS_PASSWORD=geneps-ui-password${SUFFIX}:latest" \
  --set-env-vars "GCS_BUCKET=${PROJECT}-geneps${SUFFIX},GENEPS_MODEL=claude-opus-5-5,GENEPS_EFFORT=high,GENEPS_USER=geneps" \
  --memory 2Gi --cpu 2 --timeout 900 --concurrency 8 \
  --min-instances 0 --max-instances 1 \
  --allow-unauthenticated  # la URL es pública; el acceso lo controla el usuario/contraseña de la app

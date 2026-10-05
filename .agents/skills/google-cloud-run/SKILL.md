---
name: google-cloud-run
description: "Expert guidelines for building, deploying, configuring, and troubleshooting serverless container services on Google Cloud Run. Use when creating Cloud Run deployment commands, configuring service settings, ingress, IAM authentication, or scaling."
---

# Google Cloud Run Best Practices

## Deployment Workflow

- **Container Build & Push**:
  Build using Cloud Build or push container images to Google Artifact Registry:
  ```bash
  gcloud builds submit --tag europe-west1-docker.pkg.dev/$PROJECT_ID/atlas-repo/atlas-capsule:latest
  ```

- **Deploying Service**:
  ```bash
  gcloud run deploy atlas-capsule \
    --image europe-west1-docker.pkg.dev/$PROJECT_ID/atlas-repo/atlas-capsule:latest \
    --platform managed \
    --region europe-west1 \
    --allow-unauthenticated \
    --set-env-vars "PORT=8080,LOG_LEVEL=INFO" \
    --cpu 1 \
    --memory 1Gi \
    --min-instances 0 \
    --max-instances 5
  ```

## Service Configuration & Networking

- **Port Binding**: Ensure the container listens on `0.0.0.0:$PORT` where `$PORT` defaults to `8080`.
- **Health Checks & Startup**: FastAPI endpoints like `/health` should respond fast (`200 OK`) so Cloud Run startup and liveness probes pass.
- **Service Accounts & IAM**:
  - Assign a dedicated Service Account with least-privilege roles (e.g. `roles/pubsub.subscriber`, `roles/storage.objectAdmin`, `roles/cloudsql.client`).
  - In push subscriptions from Pub/Sub, require authentication and verify incoming JWT / OIDC bearer tokens unless handled at an unauthenticated gateway endpoint.
- **Database Connectivity**:
  - Connect to Cloud SQL via Unix Domain Socket (`/cloudsql/PROJECT:REGION:INSTANCE`) or Cloud SQL Python Connector without hardcoding public IPs.

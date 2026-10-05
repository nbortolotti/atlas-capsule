---
name: google-cloud-pubsub
description: "Expert guidelines for designing, configuring, publishing, and subscribing with Google Cloud Pub/Sub. Use when configuring topics, pull/push subscriptions, dead-letter topics, message payloads, and push endpoints (such as Gmail watch push handlers)."
---

# Google Cloud Pub/Sub Best Practices

## Core Architecture

- **Topics & Subscriptions**:
  - Decouple publishers (e.g. Gmail watch push, webhooks, microservices) from ingestion pipelines.
  - Choose between **Push** (HTTP webhook directly to Cloud Run endpoints) and **Pull** (streaming or polling worker).

## Push Subscriptions to Cloud Run

- **Topic Creation**:
  ```bash
  gcloud pubsub topics create atlas-gmail-notifications
  ```

- **Push Subscription Setup**:
  ```bash
  gcloud pubsub subscriptions create atlas-gmail-push-sub \
    --topic atlas-gmail-notifications \
    --push-endpoint https://<cloud-run-service-url>/api/pubsub/gmail \
    --ack-deadline 60 \
    --min-retry-delay 10s \
    --max-retry-delay 300s
  ```

## Ingestion Handling in Code

- **Payload Extraction (FastAPI)**:
  Cloud Run receives messages as base64-encoded strings wrapped in a `message` JSON envelope:
  ```python
  import base64
  import json
  from fastapi import Request, HTTPException

  async def handle_pubsub_push(request: Request):
      envelope = await request.json()
      if not envelope or "message" not in envelope:
          raise HTTPException(status_code=400, detail="Invalid Pub/Sub envelope")
      
      message_data = envelope["message"]
      raw_payload = base64.b64decode(message_data.get("data", "")).decode("utf-8")
      payload = json.loads(raw_payload) if raw_payload else {}
      
      # Process payload asynchronously and return HTTP 200 or 204 promptly to ACK
      return {"status": "accepted"}
  ```

- **Dead-Letter Topics & Retries**:
  - Always configure dead-letter topics (DLQ) for unparseable or repeatedly failing messages to prevent infinite retry loops.
  - Return HTTP status `200` or `204` as quickly as possible to acknowledge receipt, delegating long-running computation to background tasks or worker queues.

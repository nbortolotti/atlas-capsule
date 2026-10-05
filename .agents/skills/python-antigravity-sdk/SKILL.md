---
name: python-antigravity-sdk
description: "Expert guidelines for developing, configuring, and executing autonomous AI agents using the Google Antigravity SDK (`google-antigravity`) in Python. Use when configuring Agent classes, LocalAgentConfig, system instructions, safety hooks, or tool orchestration."
---

# Google Antigravity SDK for Python

## Overview

The `google-antigravity` SDK provides high-level primitives for creating autonomous agents, multi-agent systems, dynamic tool use, and conversational reasoning powered by Gemini models.

## Core Concepts

- **Agent Initialization**:
  ```python
  from google.antigravity import Agent, LocalAgentConfig
  from google.antigravity.hooks import policy

  config = LocalAgentConfig(
      model="gemini-3.7-flash",  # or gemini-2.5-flash / gemini-2.5-pro
      api_key=os.environ.get("GEMINI_API_KEY"),
      # If using Vertex AI / Cloud Run ADC:
      # vertex=True,
      # project=os.environ.get("GCP_PROJECT"),
      # location=os.environ.get("GCP_REGION", "europe-west1"),
  )

  agent = Agent(
      config=config,
      instruction="System prompt and persona guidelines here...",
  )
  ```

- **Hooks & Safety Policies**:
  - Intercept execution turns using `@policy` hooks or lifecycle listeners.
  - Implement safety gates (blacklists, rate limits, sender supervision) before passing input to LLM synthesis.

- **Conversation & Trajectory Handling**:
  - Run agent reasoning via `agent.run(prompt)` or `agent.generate_reply(...)`.
  - Capture thought logs, token consumption, and execution step history for relational auditing (e.g. Cloud SQL / MySQL).

- **Dynamic Persona & Memory Injection**:
  - Load identity profiles (YAML/JSON) dynamically.
  - Inject learned behavioral guidelines (e.g., from `behavior_guidelines.md` or GCS storage) into the agent's base instruction string at runtime.

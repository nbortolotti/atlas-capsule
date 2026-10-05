"""Model pricing registry and token usage cost calculator based on official Google AI documentation.
https://ai.google.dev/gemini-api/docs/models
https://ai.google.dev/pricing
"""
from typing import Any, Dict, List, Optional

# Official conversational models supported by Gemini API
# Pricing is per 1 Million (1,000,000) tokens.
AVAILABLE_MODELS: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash",
        "description": "Our most intelligent Flash model, engineered for long-horizon software engineering, autonomous agents, and complex enterprise workflows.",
        "input_price_per_1m": 0.75,
        "output_price_per_1m": 3.75,
        "status": "Stable (Recommended)",
        "is_default": False,
        "category": "Gemini 3",
    },
    {
        "id": "gemini-3.7-flash",
        "name": "Gemini 3.7 Flash",
        "description": "Previous-generation Flash model for complex coding, agentic workflows, and reliable multi-step execution.",
        "input_price_per_1m": 0.75,
        "output_price_per_1m": 3.75,
        "status": "Stable",
        "is_default": False,
        "category": "Gemini 3",
    },
    {
        "id": "gemini-3.6-flash",
        "name": "Gemini 3.6 Flash",
        "description": "Previous-generation Flash model, balancing speed and multimodal capabilities across everyday tasks.",
        "input_price_per_1m": 0.75,
        "output_price_per_1m": 3.75,
        "status": "Stable",
        "is_default": False,
        "category": "Gemini 3",
    },
    {
        "id": "gemini-3.5-flash",
        "name": "Gemini 3.5 Flash",
        "description": "Baseline speed and foundational performance for routine, high-throughput workloads.",
        "input_price_per_1m": 0.50,
        "output_price_per_1m": 3.00,
        "status": "Stable",
        "is_default": False,
        "category": "Gemini 3",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash-Lite",
        "description": "Our fastest, most cost-effective 3.5 model for high-throughput execution and subagent tasks.",
        "input_price_per_1m": 0.30,
        "output_price_per_1m": 2.50,
        "status": "Stable",
        "is_default": False,
        "category": "Gemini 3",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro",
        "description": "Advanced intelligence, complex problem-solving skills, and powerful agentic reasoning.",
        "input_price_per_1m": 1.25,
        "output_price_per_1m": 10.00,
        "status": "Preview",
        "is_default": False,
        "category": "Gemini 3",
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "description": "High price-performance model for low-latency, high-volume tasks requiring reasoning.",
        "input_price_per_1m": 0.30,
        "output_price_per_1m": 2.50,
        "status": "Legacy Supported",
        "is_default": False,
        "category": "Gemini 2.5",
    },
    {
        "id": "gemini-2.5-flash-lite",
        "name": "Gemini 2.5 Flash-Lite",
        "description": "Fastest and most budget-friendly multimodal model in the 2.5 family.",
        "input_price_per_1m": 0.10,
        "output_price_per_1m": 0.40,
        "status": "Legacy Supported",
        "is_default": True,  # matches current AGENT_MODEL in .env
        "category": "Gemini 2.5",
    },
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "description": "Advanced model for complex tasks with deep reasoning and coding capabilities.",
        "input_price_per_1m": 1.25,
        "output_price_per_1m": 10.00,
        "status": "Legacy Supported",
        "is_default": False,
        "category": "Gemini 2.5",
    },
]

MODEL_PRICING_MAP = {m["id"]: m for m in AVAILABLE_MODELS}


def calculate_cost(model_id: str, prompt_tokens: int, candidate_tokens: int) -> float:
    """Calculates the estimated cost in USD based on model pricing per 1M tokens."""
    model_info = MODEL_PRICING_MAP.get(model_id) or MODEL_PRICING_MAP.get("gemini-2.5-flash-lite")
    if not model_info:
        input_rate = 0.75
        output_rate = 3.75
    else:
        input_rate = model_info["input_price_per_1m"]
        output_rate = model_info["output_price_per_1m"]

    cost = ((prompt_tokens / 1_000_000.0) * input_rate) + ((candidate_tokens / 1_000_000.0) * output_rate)
    return round(cost, 8)

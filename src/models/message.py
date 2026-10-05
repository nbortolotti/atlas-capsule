from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionType(str, Enum):
    CREATE_DRAFT = "CREATE_DRAFT"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    IGNORE = "IGNORE"
    SEND = "SEND"


class EmailItem(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    recipient: str
    subject: str
    snippet: str
    clean_body: str
    received_at: Optional[datetime] = None
    labels: List[str] = Field(default_factory=list)
    service_name: str = "gmail"


class ChatAttachment(BaseModel):
    name: Optional[str] = None
    content_name: Optional[str] = None
    content_type: Optional[str] = "image/jpeg"
    download_url: Optional[str] = None
    source: Optional[str] = None


class ChatItem(BaseModel):
    message_id: str
    space_id: str
    thread_id: Optional[str] = None
    sender: str
    sender_name: Optional[str] = None
    clean_body: str
    received_at: Optional[datetime] = None
    service_name: str = "chat"
    is_direct_message: bool = False
    attachments: List[ChatAttachment] = Field(default_factory=list)
    image_bytes_list: List[bytes] = Field(default_factory=list)
    thread_history: Optional[str] = None



class ReactionItem(BaseModel):
    reaction_name: Optional[str] = None
    emoji: str  # Unicode emoji like 👍 or name like :thumbsup:
    space_id: str
    message_id: str
    user_email: str
    user_name: Optional[str] = None
    action: str = "CREATED"  # CREATED or DELETED
    sentiment: str = "POSITIVE"  # POSITIVE, NEGATIVE, NEUTRAL



class PolicyEvaluation(BaseModel):
    allowed: bool
    risk_level: RiskLevel
    recommended_action: ActionType
    reasons: List[str] = Field(default_factory=list)


class AgentDraftResult(BaseModel):
    thread_id: str
    to: str
    subject: str
    draft_body: str
    explanation: Optional[str] = None
    draft_id: Optional[str] = None
    action_taken: ActionType = ActionType.CREATE_DRAFT
    model_used: Optional[str] = None
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


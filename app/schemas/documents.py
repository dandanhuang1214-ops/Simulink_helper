from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    filename: str
    sha256: str
    media_type: str
    parse_mode: str
    status: str
    product: str
    release: str | None
    language: str
    error: str | None
    parse_quality: dict | None = None
    created_at: datetime


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    status: str
    stage: str
    progress: int
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    use_rewrite: bool = True
    use_rerank: bool = True


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class ReviewRequest(BaseModel):
    status: str = Field(pattern="^(draft|published|rejected)$")


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=300)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    pinned: bool | None = None
    source_filter: dict | None = None


class ConversationMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageFeedbackRequest(BaseModel):
    feedback: str = Field(pattern="^(up|down|incorrect|bad_sources)$")


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    active: bool | None = None

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


DEFAULT_MODEL_ID = "claude-opus-4-8"


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ChatCompletionRequest(BaseModel):
    model: str = Field(default=DEFAULT_MODEL_ID, min_length=1)
    messages: List[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: Optional[Any] = None
    max_tokens: Optional[Any] = None
    top_p: Optional[Any] = None


class Message(BaseModel):
    role: str
    content: str


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: Optional[str] = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Choice]
    usage: Usage = Field(default_factory=Usage)


class DeltaChoice(BaseModel):
    index: int
    delta: Dict[str, Any]
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[DeltaChoice]


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "clickup"


class ModelsList(BaseModel):
    object: str = "list"
    data: List[ModelInfo]

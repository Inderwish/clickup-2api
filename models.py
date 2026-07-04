import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_MODEL_ID = "claude-opus-4-8"


class ChatMessage(BaseModel):
    role: str = Field(min_length=1)
    content: str = ""

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        """Normalize OpenAI content parts to the text-only ClickUp prompt format."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                    if item.get("type") in {"image_url", "input_image"}:
                        continue
                    parts.append(json.dumps(item, ensure_ascii=False))
                    continue
                parts.append(str(item))
            return "\n".join(part for part in parts if part)
        if isinstance(value, dict):
            for key in ("text", "content", "value"):
                text = value.get(key)
                if isinstance(text, str):
                    return text
            return json.dumps(value, ensure_ascii=False)
        return str(value)


class ChatCompletionRequest(BaseModel):
    # ClickUp Brain has no client-side generation controls. Keep only routing
    # fields and silently discard every other OpenAI-compatible option.
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL_ID, min_length=1)
    messages: List[ChatMessage] = Field(min_length=1)
    stream: bool = False


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

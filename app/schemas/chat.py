from typing import Optional, List
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class DocumentChatRequest(BaseModel):
    message: str = Field(..., description="User question about the active document")
    conversation_history: List[ChatMessage] = Field(default_factory=list, description="Prior conversation context")


class ChatCitation(BaseModel):
    label: str = Field(..., description="Citation title or clause identifier (e.g., 'Section 4: Fees')")
    text: str = Field(..., description="Exact quote or phrase snippet present in document text for PDF visual locate")
    page: Optional[int] = Field(None, description="Page number if identified")
    type: str = Field("text", description="'clause' | 'entity' | 'line_item' | 'text'")


class DocumentChatResponse(BaseModel):
    document_id: str
    reply: str
    citations: List[ChatCitation] = Field(default_factory=list)
    suggested_followups: List[str] = Field(default_factory=list)

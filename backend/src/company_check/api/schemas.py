from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateCheck(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class CheckOut(BaseModel):
    id: str
    input: str
    company_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AnswerIn(BaseModel):
    command_id: str = Field(description="Client-generated UUID; repeats are ignored")
    question_id: str
    value: Any


class FeedbackTarget(BaseModel):
    kind: Literal["concern", "evidence", "chart", "report"]
    id: str | None = None


class FeedbackIn(BaseModel):
    command_id: str = Field(description="Client-generated UUID; repeats are ignored")
    text: str = Field(min_length=1, max_length=2000)
    target: FeedbackTarget | None = None


class ControlIn(BaseModel):
    command_id: str


class CommandAccepted(BaseModel):
    command_id: str
    duplicate: bool

from pydantic import BaseModel, field_validator
from typing import Optional, List, Any


VALID_SECTIONS = {"kanji", "kanji_reverse", "bunpou", "kotoba", "reading"}


class Question(BaseModel):
    id: str
    section: str
    level: str = "N4"
    instruction: str = ""
    passage: str = ""
    question: str
    furigana: str = ""
    choices: List[str]
    answer: int
    explanation: str = ""
    translation: str = ""


class QuestionIn(BaseModel):
    id: str
    section: str
    level: str = "N4"
    instruction: str = ""
    passage: Optional[str] = ""
    question: str
    furigana: Optional[str] = ""
    choices: List[str]
    answer: int
    explanation: Optional[str] = ""
    translation: Optional[str] = ""

    @field_validator("section")
    @classmethod
    def check_section(cls, v):
        if v not in VALID_SECTIONS:
            raise ValueError(f"section must be one of {VALID_SECTIONS}")
        return v

    @field_validator("choices")
    @classmethod
    def check_choices(cls, v):
        if len(v) < 2:
            raise ValueError("choices must have at least 2 items")
        return v

    @field_validator("answer")
    @classmethod
    def check_answer(cls, v, info):
        choices = info.data.get("choices", [])
        if choices and not (0 <= v < len(choices)):
            raise ValueError("answer index out of range for choices list")
        return v


class StatsUpdateIn(BaseModel):
    question_id: str
    correct: bool
    date: str  # "YYYY-MM-DD"


class DailySession(BaseModel):
    date: str
    weak: int
    items: List[Any]  # list of enriched question dicts

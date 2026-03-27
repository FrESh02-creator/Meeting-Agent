from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ActionItem(BaseModel):
    task: str
    owner: str = "待定"
    deadline: str = "待定"
    risk: str = "无"


class MeetingStructure(BaseModel):
    title: str
    date: str
    weekly_period: str
    decisions: List[str] = Field(default_factory=list)
    actions: List[ActionItem] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    passed: bool
    estimated_accuracy: float = Field(ge=0.0, le=1.0)
    estimated_action_recall: float = Field(ge=0.0, le=1.0)
    missing_action_hints: List[str] = Field(default_factory=list)
    ambiguous_owners: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    iteration: int


class AnalyzeTextRequest(BaseModel):
    text: str = Field(min_length=1)
    report_type: Literal["management", "project"] = "management"


class AnalyzeResult(MeetingStructure):
    source_text: str = ""
    validation: ValidationResult
    report_markdown: str


class WeeklyReportRequest(BaseModel):
    title: str
    weekly_period: str
    decisions: List[str]
    actions: List[ActionItem]
    risks: List[str]
    report_type: Literal["management", "project"] = "management"
    date: Optional[str] = None
    source_text: str = ""


class WeeklyReportResponse(BaseModel):
    markdown: str


class AudioTranscriptResponse(BaseModel):
    title: str
    file_name: str
    transcript_text: str
    markdown: str = ""
    history_id: Optional[int] = None
    date: Optional[str] = None
    weekly_period: str = ""
    decisions: List[str] = Field(default_factory=list)
    actions: List[ActionItem] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    validation: Optional[ValidationResult] = None
    report_markdown: str = ""
    report_type: Literal["management", "project"] = "project"


class AudioTranscriptDownloadRequest(BaseModel):
    title: str = Field(min_length=1)
    transcript_text: str = Field(min_length=1)
    date: Optional[str] = None
    weekly_period: str = ""
    decisions: List[str] = Field(default_factory=list)
    actions: List[ActionItem] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    report_type: Literal["management", "project"] = "project"


class MeetingHistoryItem(BaseModel):
    id: int
    title: str
    date: str
    entry_type: Literal["meeting_analysis", "audio_transcript"]
    report_type: Optional[Literal["management", "project"]] = None
    created_at: str


class ActionItemRecord(BaseModel):
    id: int
    meeting_history_id: Optional[int] = None
    title: str = ""
    date: str = ""
    task: str
    owner: str = "待定"
    deadline: str = "待定"
    risk: str = "无"
    status: Literal["pending", "completed"] = "pending"
    created_at: str
    updated_at: str


class ActionItemStatusUpdateRequest(BaseModel):
    status: Literal["pending", "completed"]

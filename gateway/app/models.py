from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
	role: Literal["user", "assistant", "system", "tool"]
	content: str = Field(min_length=1, max_length=12000)


class CreateRunRequest(BaseModel):
	client_run_id: str = Field(min_length=1, max_length=140)
	session_id: str = Field(min_length=1, max_length=140)
	user_id: str = Field(min_length=1, max_length=180)
	request: str = Field(min_length=1, max_length=12000)
	history: list[HistoryMessage] = Field(default_factory=list, max_length=20)


class RegisterDeviceRequest(BaseModel):
	device_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
	device_name: str = Field(min_length=1, max_length=120)
	user_id: str = Field(min_length=1, max_length=180)
	device_token: str = Field(min_length=32, max_length=256)
	platform: Literal["windows"] = "windows"
	client_version: str = Field(default="0.1.0", max_length=40)
	capabilities: list[str] = Field(default_factory=list, max_length=40)

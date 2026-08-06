from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.settings import Settings

AuditCallback = Callable[..., None]


def _request_hash(*parts: str) -> str:
	return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def parse_json(text: str, default: Any) -> Any:
	text = (text or "").strip()
	for candidate in (text, *re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)):
		candidate = candidate.strip()
		try:
			return json.loads(candidate)
		except json.JSONDecodeError:
			start_candidates = [index for index in (candidate.find("{"), candidate.find("[")) if index >= 0]
			if not start_candidates:
				continue
			start = min(start_candidates)
			end = max(candidate.rfind("}"), candidate.rfind("]"))
			if end <= start:
				continue
			try:
				return json.loads(candidate[start : end + 1])
			except json.JSONDecodeError:
				continue
	return default


class QwenClient:
	def __init__(self, settings: Settings, audit: AuditCallback | None = None) -> None:
		self.settings = settings
		self.audit = audit

	def chat(
		self,
		system: str,
		user: str,
		*,
		temperature: float = 0.1,
		timeout: float = 180,
		max_attempts: int = 2,
		run_id: str | None = None,
		purpose: str = "chat",
	) -> str:
		started = time.monotonic()
		request_hash = _request_hash(system, user)
		last_error: Exception | None = None
		max_attempts = max(1, min(3, max_attempts))
		for attempt in range(max_attempts):
			try:
				with httpx.Client(timeout=timeout) as client:
					response = client.post(
						self.settings.qwen_chat_url,
						headers={"Authorization": f"Bearer {self.settings.qwen_api_key}"},
						json={
							"model": self.settings.qwen_model,
							"messages": [
								{"role": "system", "content": system},
								{"role": "user", "content": user},
							],
							"temperature": temperature,
							"stream": False,
						},
					)
					response.raise_for_status()
					content = str(response.json()["choices"][0]["message"]["content"])
				last_error = None
				if self.audit:
					self.audit(
						run_id=run_id,
						provider="qwen",
						model=self.settings.qwen_model,
						purpose=purpose,
						status="completed",
						request_hash=request_hash,
						response_preview=content,
						elapsed_ms=int((time.monotonic() - started) * 1000),
					)
				return content
			except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
				last_error = exc
				if attempt + 1 < max_attempts:
					time.sleep(1)
					continue
				if self.audit:
					self.audit(
						run_id=run_id,
						provider="qwen",
						model=self.settings.qwen_model,
						purpose=purpose,
						status="failed",
						request_hash=request_hash,
						error=f"{type(exc).__name__}: {exc}",
						elapsed_ms=int((time.monotonic() - started) * 1000),
					)
				raise
			except Exception as exc:
				last_error = exc
				if self.audit:
					self.audit(
						run_id=run_id,
						provider="qwen",
						model=self.settings.qwen_model,
						purpose=purpose,
						status="failed",
						request_hash=request_hash,
						error=f"{type(exc).__name__}: {exc}",
						elapsed_ms=int((time.monotonic() - started) * 1000),
					)
				raise
		raise RuntimeError("Qwen request failed") from last_error

	def json(
		self,
		system: str,
		user: str,
		default: Any,
		*,
		timeout: float = 180,
		max_attempts: int = 2,
		run_id: str | None = None,
		purpose: str = "structured_output",
	) -> Any:
		return parse_json(
			self.chat(
				system,
				user,
				timeout=timeout,
				max_attempts=max_attempts,
				run_id=run_id,
				purpose=purpose,
			),
			default,
		)


class HermesClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def research(self, prompt: str) -> list[dict[str, Any]]:
		if not self.settings.hermes_api_key:
			raise RuntimeError("Hermes API key is not configured")
		with httpx.Client(timeout=self.settings.hermes_request_timeout_seconds) as client:
			response = client.post(
				f"{self.settings.hermes_url}/v1/chat/completions",
				headers={"Authorization": f"Bearer {self.settings.hermes_api_key}"},
				json={
					"model": "hermes-agent",
					"messages": [{"role": "user", "content": prompt}],
					"stream": False,
					"tool_choice": "none",
					"max_tokens": 5000,
				},
			)
			response.raise_for_status()
			content = str(response.json()["choices"][0]["message"]["content"])
		parsed = parse_json(content, [])
		if isinstance(parsed, dict):
			parsed = parsed.get("candidates") or parsed.get("results") or []
		return parsed if isinstance(parsed, list) else []


class SearxngClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def search(self, queries: list[str], *, limit: int) -> list[dict[str, Any]]:
		results: list[dict[str, Any]] = []
		seen: set[str] = set()
		with httpx.Client(timeout=40, follow_redirects=True) as client:
			for query in queries[:8]:
				try:
					response = client.get(
						f"{self.settings.searxng_url}/search",
						params={"q": query, "format": "json", "language": "zh-CN"},
					)
					response.raise_for_status()
				except httpx.HTTPError:
					continue
				for item in response.json().get("results") or []:
					url = str(item.get("url") or "").strip()
					if not url or url in seen:
						continue
					seen.add(url)
					results.append(
						{
							"title": item.get("title"),
							"source_url": url,
							"source_name": item.get("engine") or ", ".join(item.get("engines") or []) or "搜索引擎",
							"published_at": item.get("publishedDate") or item.get("published_date"),
							"raw_text": item.get("content") or "",
							"evidence": [{"query": query, "snippet": item.get("content") or "", "url": url}],
						}
					)
					if len(results) >= limit:
						return results
		return results


class _ReadableHTML(HTMLParser):
	ignored_tags: ClassVar[set[str]] = {"script", "style", "noscript", "svg", "canvas", "template"}

	def __init__(self) -> None:
		super().__init__(convert_charrefs=True)
		self.ignored_depth = 0
		self.title_depth = 0
		self.title_parts: list[str] = []
		self.text_parts: list[str] = []

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		tag = tag.lower()
		if tag in self.ignored_tags:
			self.ignored_depth += 1
		elif tag == "title":
			self.title_depth += 1

	def handle_endtag(self, tag: str) -> None:
		tag = tag.lower()
		if tag in self.ignored_tags and self.ignored_depth:
			self.ignored_depth -= 1
		elif tag == "title" and self.title_depth:
			self.title_depth -= 1

	def handle_data(self, data: str) -> None:
		if self.ignored_depth:
			return
		value = " ".join(data.split())
		if not value:
			return
		if self.title_depth:
			self.title_parts.append(value)
		self.text_parts.append(value)

	@property
	def title(self) -> str:
		return " ".join(self.title_parts).strip()

	@property
	def text(self) -> str:
		return "\n".join(self.text_parts).strip()


class WebPageExtractor:
	"""Fetch public search results and turn HTML into bounded evidence text."""

	MAX_BYTES = 2 * 1024 * 1024
	MAX_TEXT = 16000

	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	@staticmethod
	def _is_public_url(url: str) -> bool:
		parsed = urlparse(url)
		if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
			return False
		try:
			addresses = {
				item[4][0]
				for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
			}
		except OSError:
			return False
		return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)

	def _fetch(self, item: dict[str, Any]) -> dict[str, Any]:
		url = str(item.get("source_url") or "").strip()
		if not self._is_public_url(url):
			return item
		try:
			with httpx.Client(
				proxy=self.settings.search_http_proxy or None,
				timeout=httpx.Timeout(35, connect=12),
				follow_redirects=True,
				headers={
					"User-Agent": "Mozilla/5.0 (compatible; I-ONE-LeadResearch/1.0; +https://myyr.top)",
					"Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.2",
				},
			) as client:
				with client.stream("GET", url) as response:
					response.raise_for_status()
					if not self._is_public_url(str(response.url)):
						return item
					content_type = response.headers.get("content-type", "").lower()
					if not any(kind in content_type for kind in ("html", "text", "xhtml", "xml")):
						return item
					body = bytearray()
					for chunk in response.iter_bytes():
						body.extend(chunk)
						if len(body) >= self.MAX_BYTES:
							break
					encoding = response.encoding or "utf-8"
			html = bytes(body[: self.MAX_BYTES]).decode(encoding, errors="replace")
			parser = _ReadableHTML()
			parser.feed(html)
			text = parser.text[: self.MAX_TEXT]
			if len(text) < 80:
				return item
			enriched = dict(item)
			enriched["title"] = item.get("title") or parser.title
			enriched["raw_text"] = text
			evidence = list(item.get("evidence") or [])
			evidence.append({"url": str(response.url), "snippet": text[:1200], "kind": "原文抓取"})
			enriched["evidence"] = evidence
			return enriched
		except (httpx.HTTPError, OSError, UnicodeError, ValueError):
			return item

	def enrich(self, results: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
		selected = results[: max(0, limit)]
		if not selected:
			return list(results)
		enriched = list(results)
		with ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
			future_indexes = {pool.submit(self._fetch, item): index for index, item in enumerate(selected)}
			for future in as_completed(future_indexes):
				try:
					enriched[future_indexes[future]] = future.result()
				except Exception:
					continue
		return enriched


class CircuitOpenError(RuntimeError):
	pass


class DeepSeekClient:
	def __init__(self, settings: Settings, audit: AuditCallback | None = None) -> None:
		self.settings = settings
		self.audit = audit
		self._lock = threading.Lock()
		self._consecutive_failures = 0
		self._opened_until = 0.0

	@staticmethod
	def _extract(payload: Any) -> str:
		if isinstance(payload, str):
			return payload
		if isinstance(payload, dict):
			for key in ("answer", "result", "output", "content", "text", "message", "reply"):
				if key in payload:
					value = DeepSeekClient._extract(payload[key])
					if value:
						return value
		return ""

	@staticmethod
	def _job_payload(payload: Any) -> dict[str, Any]:
		if isinstance(payload, dict) and isinstance(payload.get("job"), dict):
			return payload["job"]
		return payload if isinstance(payload, dict) else {}

	@staticmethod
	def _plan_text(value: Any) -> str:
		if value in (None, ""):
			return ""
		if isinstance(value, str):
			return value.strip()
		if isinstance(value, list):
			lines = []
			for item in value:
				text = item.strip() if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
				if text:
					lines.append(f"- {text}")
			return "\n".join(lines)
		if isinstance(value, dict):
			return json.dumps(value, ensure_ascii=False, indent=2)
		return str(value).strip()

	@staticmethod
	def _parse_review_content(content: str) -> list[dict[str, Any]]:
		parsed = parse_json(content, [])
		if isinstance(parsed, dict):
			parsed = parsed.get("plans") or parsed.get("results") or []
		if isinstance(parsed, list) and parsed:
			plans = []
			for item in parsed:
				if not isinstance(item, dict):
					continue
				normalized = dict(item)
				if "deepseek_plan" in normalized:
					normalized["deepseek_plan"] = DeepSeekClient._plan_text(normalized["deepseek_plan"])
				plans.append(normalized)
			return plans
		content = (content or "").strip()
		return [{"deepseek_plan": content}] if content else []

	@staticmethod
	def _conversation_key() -> str:
		return f"ione-agent-lead-{uuid4().hex[:16]}"

	def health(self) -> dict[str, Any]:
		with self._lock:
			remaining = max(0, int(self._opened_until - time.monotonic()))
			return {
				"state": "open" if remaining else "closed",
				"consecutive_failures": self._consecutive_failures,
				"retry_after_seconds": remaining,
			}

	def _before_call(self) -> None:
		with self._lock:
			if self._opened_until > time.monotonic():
				raise CircuitOpenError("DeepSeek 网页服务熔断中，请稍后重试。")
			if self._opened_until:
				self._opened_until = 0
				self._consecutive_failures = 0

	def _record_success(self) -> None:
		with self._lock:
			self._consecutive_failures = 0
			self._opened_until = 0

	def _record_failure(self) -> None:
		with self._lock:
			self._consecutive_failures += 1
			if self._consecutive_failures >= self.settings.deepseek_breaker_failures:
				self._opened_until = time.monotonic() + self.settings.deepseek_breaker_cooldown_seconds

	def chat(self, prompt: str, *, run_id: str | None = None, purpose: str = "reasoning") -> str:
		if not self.settings.deepseek_token:
			raise RuntimeError("DeepSeek review token is not configured")
		self._before_call()
		started = time.monotonic()
		request_hash = _request_hash(prompt)
		headers = {"Authorization": f"Bearer {self.settings.deepseek_token}"}
		try:
			with httpx.Client(timeout=60) as client:
				response = client.post(
					f"{self.settings.deepseek_url}/jobs",
					headers=headers,
					json={"prompt": prompt, "conversationKey": self._conversation_key()},
				)
				response.raise_for_status()
				job = self._job_payload(response.json())
				job_id = job.get("id") or job.get("jobId") or job.get("job_id")
				if not job_id:
					content = self._extract(job)
				else:
					deadline = time.monotonic() + self.settings.deepseek_job_timeout_seconds
					content = ""
					while time.monotonic() < deadline:
						status = client.get(f"{self.settings.deepseek_url}/jobs/{job_id}", headers=headers)
						status.raise_for_status()
						payload = self._job_payload(status.json())
						state = str(payload.get("status") or "").lower()
						if state in {"failed", "error", "cancelled"}:
							raise RuntimeError(self._extract(payload) or f"DeepSeek job {state}")
						if state in {"completed", "complete", "done", "succeeded", "success"}:
							content = self._extract(payload)
							break
						time.sleep(3)
					if not content:
						raise TimeoutError(
							f"DeepSeek job did not finish within {self.settings.deepseek_job_timeout_seconds} seconds"
						)
			if not content.strip():
				raise RuntimeError("DeepSeek returned an empty response")
			self._record_success()
			if self.audit:
				self.audit(
					run_id=run_id,
					provider="deepseek_web",
					model="deepseek-web",
					purpose=purpose,
					status="completed",
					request_hash=request_hash,
					response_preview=content,
					elapsed_ms=int((time.monotonic() - started) * 1000),
				)
			return content
		except Exception as exc:
			self._record_failure()
			if self.audit:
				self.audit(
					run_id=run_id,
					provider="deepseek_web",
					model="deepseek-web",
					purpose=purpose,
					status="failed",
					request_hash=request_hash,
					error=f"{type(exc).__name__}: {exc}",
					elapsed_ms=int((time.monotonic() - started) * 1000),
				)
			raise

	def json(
		self,
		prompt: str,
		default: Any,
		*,
		run_id: str | None = None,
		purpose: str = "structured_reasoning",
	) -> Any:
		return parse_json(self.chat(prompt, run_id=run_id, purpose=purpose), default)

	def review(self, prompt: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
		return self._parse_review_content(self.chat(prompt, run_id=run_id, purpose="lead_review"))

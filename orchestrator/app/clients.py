from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from app.settings import Settings


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
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def chat(self, system: str, user: str, *, temperature: float = 0.1, timeout: float = 180) -> str:
		with httpx.Client(timeout=timeout) as client:
			response = client.post(
				self.settings.qwen_chat_url,
				headers={"Authorization": f"Bearer {self.settings.qwen_api_key}"},
				json={
					"model": self.settings.qwen_model,
					"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
					"temperature": temperature,
					"stream": False,
				},
			)
			response.raise_for_status()
			return str(response.json()["choices"][0]["message"]["content"])

	def json(self, system: str, user: str, default: Any) -> Any:
		return parse_json(self.chat(system, user), default)


class HermesClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def research(self, prompt: str) -> list[dict[str, Any]]:
		if not self.settings.hermes_api_key:
			raise RuntimeError("Hermes API key is not configured")
		with httpx.Client(timeout=600) as client:
			response = client.post(
				f"{self.settings.hermes_url}/v1/chat/completions",
				headers={"Authorization": f"Bearer {self.settings.hermes_api_key}"},
				json={
					"model": "hermes-agent",
					"messages": [{"role": "user", "content": prompt}],
					"stream": False,
					"tool_choice": "none",
					"max_tokens": 8000,
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


class DeepSeekClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

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

	def review(self, prompt: str) -> list[dict[str, Any]]:
		if not self.settings.deepseek_token:
			raise RuntimeError("DeepSeek review token is not configured")
		headers = {"Authorization": f"Bearer {self.settings.deepseek_token}"}
		with httpx.Client(timeout=60) as client:
			response = client.post(
				f"{self.settings.deepseek_url}/jobs",
				headers=headers,
				json={"prompt": prompt, "conversationKey": "ione-agent-lead-review"},
			)
			response.raise_for_status()
			job = response.json()
			job_id = job.get("id") or job.get("jobId") or job.get("job_id")
			if not job_id:
				content = self._extract(job)
				parsed = parse_json(content, [])
				return parsed if isinstance(parsed, list) else []
			deadline = time.monotonic() + 900
			while time.monotonic() < deadline:
				status = client.get(f"{self.settings.deepseek_url}/jobs/{job_id}", headers=headers)
				status.raise_for_status()
				payload = status.json()
				state = str(payload.get("status") or "").lower()
				if state in {"failed", "error", "cancelled"}:
					raise RuntimeError(self._extract(payload) or f"DeepSeek job {state}")
				if state in {"completed", "complete", "done", "succeeded", "success"}:
					parsed = parse_json(self._extract(payload), [])
					return parsed if isinstance(parsed, list) else []
				time.sleep(3)
		raise TimeoutError("DeepSeek review did not finish within 15 minutes")

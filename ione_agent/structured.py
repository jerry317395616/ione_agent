"""Structured JSON completion helper for Frappe-side business importers.

The recipe importer runs inside a Frappe worker and must remain usable when a
remote model endpoint is unavailable.  This client therefore uses an optional
OpenAI-compatible endpoint when configured and falls back to deterministic,
local dish/ingredient linking.  The fallback never invents quantities or
changes source text; it only splits obvious dish boundaries and assigns each
ingredient to the best matching dish.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:  # Keep the module importable in standalone tests.
	import frappe
except ImportError:  # pragma: no cover - only used outside a Frappe worker
	frappe = None  # type: ignore[assignment]


class StructuredTaskClientError(RuntimeError):
	"""Raised when a configured structured endpoint returns an invalid result."""


@dataclass(slots=True)
class StructuredTaskClient:
	"""Call a configured structured endpoint with a deterministic local fallback."""

	endpoint: str | None = None
	token: str | None = None
	model: str = "ione-pro"
	timeout: float = 90.0

	@classmethod
	def from_frappe_config(cls) -> "StructuredTaskClient":
		conf = getattr(frappe, "conf", None) if frappe is not None else None
		conf = conf or {}
		endpoint = str(conf.get("ione_agent_structured_url") or "").strip().rstrip("/")
		token = str(conf.get("ione_agent_structured_token") or "").strip() or None
		model = str(conf.get("ione_agent_structured_model") or cls.model).strip() or cls.model
		try:
			timeout = max(5.0, min(float(conf.get("ione_agent_structured_timeout") or 90), 300.0))
		except (TypeError, ValueError):
			timeout = 90.0
		return cls(endpoint=endpoint or None, token=token, model=model, timeout=timeout)

	def complete_json(self, *, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
		"""Return one JSON object, falling back locally on endpoint errors."""

		if self.endpoint:
			try:
				return self._request_json(system_prompt=system_prompt, user_payload=user_payload)
			except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError, IndexError):
				# Import must remain available during a temporary Agent outage.  The
				# caller adds its own warning when the result needs manual review.
				pass
		return self._local_json(system_prompt=system_prompt, user_payload=user_payload)

	def _request_json(self, *, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
		if not self.endpoint or not re.match(r"^https?://", self.endpoint, re.IGNORECASE):
			raise StructuredTaskClientError("structured endpoint is not configured")
		payload = {
			"model": self.model,
			"temperature": 0,
			"messages": [
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
			],
			"response_format": {"type": "json_object"},
		}
		headers = {"Content-Type": "application/json", "Accept": "application/json"}
		if self.token:
			headers["Authorization"] = f"Bearer {self.token}"
		request = Request(
			self.endpoint,
			data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
			headers=headers,
			method="POST",
		)
		with urlopen(request, timeout=self.timeout) as response:  # nosec B310 - endpoint is admin-configured
			body = json.loads(response.read().decode("utf-8"))
		content = body["choices"][0]["message"]["content"]
		if isinstance(content, list):
			content = "".join(
				str(item.get("text") or "") for item in content if isinstance(item, dict)
			)
		if not isinstance(content, str):
			raise StructuredTaskClientError("structured endpoint returned non-text content")
		content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.IGNORECASE)
		result = json.loads(content)
		if not isinstance(result, dict):
			raise StructuredTaskClientError("structured endpoint returned a non-object")
		return result

	@classmethod
	def _local_json(cls, *, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
		if "食材归属校验器" in system_prompt:
			return cls._local_repair(user_payload)
		return cls._local_resolve(user_payload)

	@classmethod
	def _local_resolve(cls, payload: dict[str, Any]) -> dict[str, Any]:
		result: list[dict[str, Any]] = []
		for portion in payload.get("portions") or []:
			if not isinstance(portion, dict):
				continue
			dish_text = _clean(portion.get("dish_text"))
			dishes = _split_dishes(dish_text)
			ingredients = [item for item in (portion.get("ingredients") or []) if isinstance(item, dict)]
			inferred = False
			if not dishes and ingredients:
				dishes = [f"未命名菜品（{_clean(portion.get('label')) or '餐次'}）"]
				inferred = True
			assignments = [
				_best_dish_index(_clean(item.get("name")), dishes) for item in ingredients
			]
			result.append(
				{
					"slot": _clean(portion.get("slot")),
					"dishes": dishes,
					"assignments": assignments,
					"inferred": inferred,
				}
			)
		return {"portions": result}

	@classmethod
	def _local_repair(cls, payload: dict[str, Any]) -> dict[str, Any]:
		result: list[dict[str, Any]] = []
		for portion in payload.get("portions") or []:
			if not isinstance(portion, dict):
				continue
			dishes = [_clean(value) for value in portion.get("dishes") or [] if _clean(value)]
			links = [
				dishes[_best_dish_index(_clean(value), dishes)] if dishes else ""
				for value in portion.get("ingredients") or []
			]
			result.append({"slot": _clean(portion.get("slot")), "links": links})
		return {"portions": result}


_DISH_SUFFIXES = (
	"鹌鹑蛋",
	"小馒头",
	"西葫芦",
	"鸡蛋汤",
	"银耳汤",
	"豆腐汤",
	"海带汤",
	"馒头",
	"面包",
	"酸奶",
	"豆浆",
	"玉米",
	"鸡蛋",
	"麦饭",
	"米饭",
	"炒饭",
	"面",
	"粥",
	"汤",
	"饼",
	"卷",
	"糕",
	"糁",
)


def _clean(value: Any) -> str:
	return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _split_dishes(value: str) -> list[str]:
	parts = re.split(r"\s+|[、，,；;+/]+", _clean(value))
	result: list[str] = []
	for part in parts:
		for dish in _split_compound(part):
			dish = dish.strip(" -—")
			if len(dish) >= 2 and dish not in result:
				result.append(dish)
	return result


def _split_compound(value: str) -> list[str]:
	value = value.strip(" 、，,；;+/")
	if not value:
		return []
	for suffix in _DISH_SUFFIXES:
		start = 0
		while True:
			at = value.find(suffix, start)
			if at < 0:
				break
			boundary = at + len(suffix)
			if 2 <= boundary <= len(value) - 2:
				left, right = value[:boundary].strip(), value[boundary:].strip()
				if left and right:
					return [left, *_split_compound(right)]
			start = boundary
	return [value]


def _best_dish_index(ingredient: str, dishes: list[str]) -> int:
	if not dishes:
		return 0
	name = _canonical(ingredient)
	if not name:
		return 0
	scores: list[tuple[int, int]] = []
	for index, dish in enumerate(dishes):
		candidate = _canonical(dish)
		score = 0
		if name in candidate or candidate in name:
			score += 100 + min(len(name), len(candidate))
		# Character overlap is only a tie-breaker for names such as 蓝莓果酱 /
		# 蓝莓切片面包; it never changes the source ingredient or quantity.
		score += len(set(name) & set(candidate))
		scores.append((score, -index))
	return max(scores)[1] * -1 if scores and max(scores)[0] > 0 else 0


def _canonical(value: str) -> str:
	return re.sub(r"[\s,，、;；:：/|·。．（）()【】\[\]—_\-]+", "", value or "")

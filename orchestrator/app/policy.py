from __future__ import annotations

from app.contracts import PolicyResult, RiskLevel, ToolSpec


class ToolPolicy:
	"""Apply the same least-privilege rules before every tool execution."""

	def evaluate(self, spec: ToolSpec, *, roles: list[str], approved: bool = False) -> PolicyResult:
		role_set = set(roles)
		if spec.required_roles and not role_set.intersection(spec.required_roles):
			return PolicyResult(allowed=False, reason="当前用户缺少执行该工具所需的角色。")
		if spec.risk_level == RiskLevel.HIGH_WRITE and not approved:
			return PolicyResult(
				allowed=False,
				requires_approval=True,
				reason="该工具会产生高风险外部写入，需要人工批准。",
			)
		return PolicyResult(allowed=True)

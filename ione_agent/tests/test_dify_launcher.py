from __future__ import annotations

import pytest

from ione_agent.dify_launcher import DifyLauncherConfigError, validate_dify_oauth_login_url


def test_accepts_oauth_login_on_the_configured_https_origin():
	url = "https://dify.myyr.top/console/api/oauth/login/frappe?redirect_url=/apps"
	assert validate_dify_oauth_login_url(url, "https://dify.myyr.top") == url


@pytest.mark.parametrize(
	("url", "origin"),
	[
		("http://dify.myyr.top/console/api/oauth/login/frappe", "https://dify.myyr.top"),
		("https://other.example/console/api/oauth/login/frappe", "https://dify.myyr.top"),
		("https://user@dify.myyr.top/console/api/oauth/login/frappe", "https://dify.myyr.top"),
		("https://dify.myyr.top/apps", "https://dify.myyr.top"),
		("https://dify.myyr.top/console/api/oauth/login/google", "https://dify.myyr.top"),
		("https://dify.myyr.top/console/api/oauth/login/frappe#token", "https://dify.myyr.top"),
		("https://dify.myyr.top/console/api/oauth/login/frappe?token=secret", "https://dify.myyr.top"),
		(
			"https://dify.myyr.top/console/api/oauth/login/frappe?redirect_url=https%3A%2F%2Fevil.example",
			"https://dify.myyr.top",
		),
		(
			"https://dify.myyr.top/console/api/oauth/login/frappe?redirect_url=%2F%2Fevil.example",
			"https://dify.myyr.top",
		),
		("https://dify.myyr.top/console/api/oauth/login/frappe", "https://dify.myyr.top/v1"),
	],
)
def test_rejects_unsafe_or_off_origin_login_targets(url: str, origin: str):
	with pytest.raises(DifyLauncherConfigError):
		validate_dify_oauth_login_url(url, origin)

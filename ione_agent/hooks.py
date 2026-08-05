app_name = "ione_agent"
app_title = "I-ONE Agent"
app_publisher = "I-ONE"
app_description = "融合 LangGraph、Hermes、Qwen、DeepSeek 与 UFO3 的企业智能执行和获客平台"
app_email = "317395616@qq.com"
app_license = "mit"

app_home = "/agent"
app_logo_url = "/assets/ione_agent/images/ione-agent-logo.svg"

add_to_apps_screen = [
	{
		"name": app_name,
		"title": app_title,
		"route": app_home,
		"logo": app_logo_url,
		"has_permission": "ione_agent.permissions.has_app_permission",
	}
]

before_install = "ione_agent.setup.install.before_install"
after_install = "ione_agent.setup.install.after_install"
after_migrate = "ione_agent.setup.install.after_migrate"

permission_query_conditions = {
	"I-ONE Agent Session": "ione_agent.permissions.session_query",
	"I-ONE Agent Message": "ione_agent.permissions.message_query",
	"I-ONE Agent Run": "ione_agent.permissions.run_query",
	"I-ONE Agent Device": "ione_agent.permissions.device_query",
	"I-ONE Agent Pairing": "ione_agent.permissions.pairing_query",
	"I-ONE Lead Discovery Profile": "ione_agent.permissions.profile_query",
	"I-ONE Lead Discovery Task": "ione_agent.permissions.discovery_task_query",
	"I-ONE Lead Candidate": "ione_agent.permissions.candidate_query",
}

has_permission = {
	"I-ONE Agent Session": "ione_agent.permissions.session_permission",
	"I-ONE Agent Message": "ione_agent.permissions.message_permission",
	"I-ONE Agent Run": "ione_agent.permissions.run_permission",
	"I-ONE Agent Device": "ione_agent.permissions.device_permission",
	"I-ONE Agent Pairing": "ione_agent.permissions.pairing_permission",
	"I-ONE Lead Discovery Profile": "ione_agent.permissions.profile_permission",
	"I-ONE Lead Discovery Task": "ione_agent.permissions.discovery_task_permission",
	"I-ONE Lead Candidate": "ione_agent.permissions.candidate_permission",
}

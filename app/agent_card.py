from a2a.types import AgentCard


def build_agent_card(base_url: str) -> AgentCard:
    card = AgentCard(
        name="travel-planner-agent",
        description="根据目的地/日期/预算生成真实可执行的逐日旅行方案",
        version="0.1.0",
    )
    iface = card.supported_interfaces.add()
    iface.url = f"{base_url.rstrip('/')}/a2a"
    iface.protocol_binding = "JSONRPC"
    card.capabilities.SetInParent()
    card.default_input_modes.append("text/plain")
    card.default_output_modes.append("text/plain")

    scheme = card.security_schemes["bearerAuth"]
    scheme.http_auth_security_scheme.scheme = "bearer"
    card.security_requirements.add().schemes["bearerAuth"]

    skill = card.skills.add()
    skill.id = "plan_trip"
    skill.name = "plan_trip"
    skill.description = "生成旅行行程，缺少必填信息时会中断询问"
    skill.tags.append("travel")
    return card

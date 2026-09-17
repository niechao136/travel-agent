from a2a.types import AgentCard

A2A_RPC_PATH = "/a2a"


def build_agent_card(base_url: str = "") -> AgentCard:
    """构建 AgentCard。

    `base_url` 为空时界面地址是相对路径（`/a2a`）；服务端在 well-known 路由里按请求的
    Host/Scheme 补全成绝对地址对外返回，因此不再需要 PUBLIC_BASE_URL 配置。
    """
    card = AgentCard(
        name="travel-planner-agent",
        description="根据目的地/日期/预算生成真实可执行的逐日旅行方案",
        version="0.1.0",
    )
    iface = card.supported_interfaces.add()
    iface.url = f"{base_url.rstrip('/')}{A2A_RPC_PATH}"
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

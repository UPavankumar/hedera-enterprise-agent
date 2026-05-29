from hedera_agent_kit.shared.plugin import Plugin          # correct import
from hedera_agent_kit.shared.configuration import Context
from .tools.notify_tool import tool as notify_tool, NOTIFY_INVOICE_APPROVAL_TOOL

enterprise_invoice_plugin = Plugin(
    name="enterprise-invoice-plugin",
    version="1.0.0",
    description=(
        "Enterprise Accounts Payable plugin for the Hedera Agent Kit. "
        "Provides invoice approval notification and audit trail logging "
        "for enterprise AP workflows integrated with Hedera HCS."
    ),
    tools=lambda context: [notify_tool(context)],
)

enterprise_invoice_plugin_tool_names = {
    "NOTIFY_INVOICE_APPROVAL_TOOL": NOTIFY_INVOICE_APPROVAL_TOOL,
}

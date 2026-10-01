"""Central protocol error-code registry (P4 T31 / ADR-P4-02).

One source of truth for every business error code the three clients may
see: the HTTP status it maps to, whether it is retryable, and the default
message. Clients must branch on the CODE (never on message strings).

Codes listed here are contractual; raising a code that is not registered
is a bug — unknown codes fall back to 400/non-retryable defensively.
"""

from dataclasses import dataclass


class PayloadError(Exception):
    """A request failed with a registered protocol error code.

    Carries ``code`` (registry key) and ``message`` (client-facing detail).
    Formerly ``chat.consumers.ClientPayloadError``; the old name remains
    available as an alias on the consumer module.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProtocolError:
    code: str
    http_status: int
    retryable: bool
    message: str


REGISTRY = {
    # ── payload / protocol ────────────────────────────────────────────
    "invalid_payload": ProtocolError("invalid_payload", 400, False, "消息数据格式错误"),
    "unsupported_algorithm": ProtocolError("unsupported_algorithm", 400, False, "不支持的加密算法"),
    "unsupported_protocol_version": ProtocolError(
        "unsupported_protocol_version", 400, False, "协议版本不受支持，请升级客户端"
    ),
    "not_implemented": ProtocolError("not_implemented", 501, False, "该实时通信事件尚未实现"),
    # ── authentication / storage ──────────────────────────────────────
    "authentication_required": ProtocolError(
        "authentication_required", 401, False, "登录状态已失效，请重新登录"
    ),
    "storage_unavailable": ProtocolError(
        "storage_unavailable", 503, True, "服务暂时繁忙，请稍后重试"
    ),
    # legacy code for the same condition, kept registered during the 1.0
    # compat window (clients may still receive it from older deployments)
    "database_busy": ProtocolError("database_busy", 503, True, "服务暂时繁忙，请稍后重试"),
    # ── conversation / membership ─────────────────────────────────────
    "conversation_not_found": ProtocolError(
        "conversation_not_found", 404, False, "会话不存在或不可用"
    ),
    "receiver_not_found": ProtocolError(
        "receiver_not_found", 404, False, "接收者不存在或已停用"
    ),
    "conversation_forbidden": ProtocolError(
        "conversation_forbidden", 403, False, "无法在该会话中发送消息"
    ),
    "membership_conflict": ProtocolError(
        "membership_conflict", 409, False, "群成员已变化，请刷新成员列表后重试"
    ),
    "recipients_mismatch": ProtocolError(
        "recipients_mismatch", 409, False, "接收者列表与当前成员不一致"
    ),
    "group_muted": ProtocolError("group_muted", 403, False, "群已禁言，仅群主和管理员可发送"),
    "group_too_large": ProtocolError("group_too_large", 400, False, "群成员数超出上限"),
    # ── messages ──────────────────────────────────────────────────────
    "message_not_found": ProtocolError("message_not_found", 404, False, "消息不存在或无权访问"),
    "already_recalled": ProtocolError("already_recalled", 409, False, "消息已被撤回"),
    "recall_timeout": ProtocolError("recall_timeout", 409, False, "已超过可撤回时间"),
    # registered in T31; raised by the unified messaging service in T32
    "idempotency_conflict": ProtocolError(
        "idempotency_conflict", 409, False, "消息 ID 已被其他内容使用"
    ),
    # ── files ─────────────────────────────────────────────────────────
    "file_not_found": ProtocolError("file_not_found", 404, False, "附件不存在"),
    "file_forbidden": ProtocolError("file_forbidden", 403, False, "无权访问该附件"),
    "file_unavailable": ProtocolError("file_unavailable", 409, False, "附件当前不可用"),
    "file_type_mismatch": ProtocolError("file_type_mismatch", 400, False, "附件类型与消息不匹配"),
}

DEFAULT_ERROR = ProtocolError("invalid_payload", 400, False, "消息数据格式错误")

PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_VERSIONS = {"1.0"}
# Close code used after reporting unsupported_protocol_version.
UNSUPPORTED_PROTOCOL_CLOSE_CODE = 4003


def get_error(code):
    """Return the registered ProtocolError for a code, or a defensive default."""
    error = REGISTRY.get(code)
    if error is not None:
        return error
    return ProtocolError(code or DEFAULT_ERROR.code, DEFAULT_ERROR.http_status,
                         DEFAULT_ERROR.retryable, DEFAULT_ERROR.message)

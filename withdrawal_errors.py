"""Allowlisted withdrawal diagnostics: never log exception messages or RPC URLs."""

import hashlib


class WithdrawalRpcError(Exception):
    def __init__(self, stage):
        self.stage = stage
        super().__init__("Withdrawal RPC operation failed")


def rpc_call(stage, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception as exc:
        raise WithdrawalRpcError(stage) from exc


KNOWN_ERRORS = {
    "Treasury signing is not configured": (
        "TREASURY_NOT_CONFIGURED", "На сервере не настроена подпись кошелька выплат."),
    "Treasury signer does not match the configured Zer0Life pool wallet": (
        "TREASURY_SIGNER_MISMATCH", "Ключ отправителя не соответствует кошельку выплат проекта."),
    "Treasury SOL balance is insufficient for this withdrawal and network fee": (
        "TREASURY_SOL_INSUFFICIENT", "В кошельке выплат проекта недостаточно SOL для суммы и комиссии."),
    "ZRL token mint is not configured": (
        "ZRL_MINT_NOT_CONFIGURED", "На сервере не настроен адрес токена ZRL."),
    "Treasury ZRL liquidity is insufficient": (
        "TREASURY_ZRL_INSUFFICIENT", "В кошельке выплат проекта недостаточно ZRL для перевода."),
    "Treasury SOL balance is insufficient for token-account rent and fee": (
        "TREASURY_FEE_INSUFFICIENT", "В кошельке выплат проекта недостаточно SOL для комиссии и создания счёта токена."),
}


def describe_failure(exc, nonce):
    chain, seen, current = [], set(), exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    status = next((
        getattr(getattr(item, "response", None), "status_code", None)
        for item in chain
        if isinstance(getattr(getattr(item, "response", None), "status_code", None), int)
    ), None)
    stage = exc.stage if isinstance(exc, WithdrawalRpcError) else "prepare_transfer"
    if isinstance(exc, WithdrawalRpcError):
        if status in (401, 403):
            code, message = "RPC_ACCESS_DENIED", "RPC Solana отклонил доступ сервера. Требуется проверить настройку RPC."
        elif status == 429:
            code, message = "RPC_RATE_LIMITED", "RPC Solana ограничил запросы сервера."
        elif any("Timeout" in type(item).__name__ for item in chain):
            code, message = "RPC_TIMEOUT", "RPC Solana не ответил вовремя."
        elif any(type(item).__name__ == "RPCException" for item in chain):
            code, message = "RPC_REJECTED", "RPC Solana отклонил проверку данных перевода."
        else:
            code, message = "RPC_UNAVAILABLE", "Не удалось получить данные от RPC Solana."
    else:
        code, message = KNOWN_ERRORS.get(str(exc), (
            "TRANSFER_PREPARATION_FAILED", "Сервер не смог подготовить перевод."
        ))
    return {
        "code": code, "message": message, "stage": stage,
        "types": ">".join(type(item).__name__ for item in chain),
        "httpStatus": status,
        "reference": hashlib.sha256(str(nonce).encode()).hexdigest()[:12],
    }

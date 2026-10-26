"""Allowlisted withdrawal diagnostics: never log exception messages or RPC URLs."""

import hashlib
import time


READ_STAGES = frozenset({
    "get_sol_balance", "get_zrl_balance", "get_recipient_token_account",
    "get_token_account_rent", "get_fee_balance", "get_latest_blockhash",
    "get_signature_statuses",
})


def _exception_chain(exc):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 8:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return chain


class WithdrawalRpcError(Exception):
    def __init__(self, stage):
        self.stage = stage
        super().__init__("Withdrawal RPC operation failed")


def rpc_call(stage, operation, *args, **kwargs):
    # Retrying a broadcast is safe ONLY with the identical signed bytes.
    # Never rebuild/re-sign a transaction here, or retry an ambiguous timeout.
    retryable = stage in READ_STAGES or (
        stage == "broadcast_transaction" and len(args) == 1
        and isinstance(args[0], bytes) and not kwargs
    )
    for attempt in range(3):
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            response = next((
                item.response for item in _exception_chain(exc)
                if getattr(getattr(item, "response", None), "status_code", None) == 429
            ), None)
            if not retryable or response is None or attempt == 2:
                raise WithdrawalRpcError(stage) from exc
            try:
                retry_after = float(getattr(response, "headers", {}).get("Retry-After", 0))
            except (TypeError, ValueError, OverflowError):
                retry_after = 0
            # A long provider quota window should fail explicitly, not hang Wallet.
            if retry_after > 2:
                raise WithdrawalRpcError(stage) from exc
            time.sleep(max(0.5 * (2 ** attempt), min(2, retry_after)))


KNOWN_ERRORS = {
    "Treasury signing is not configured": (
        "TREASURY_NOT_CONFIGURED", "The treasury signing key is not configured on the server."),
    "Treasury signer does not match the configured Zer0Life pool wallet": (
        "TREASURY_SIGNER_MISMATCH", "The signing key does not match the project's payout wallet."),
    "Treasury SOL balance is insufficient for this withdrawal and network fee": (
        "TREASURY_SOL_INSUFFICIENT", "The project's payout wallet does not have enough SOL for the withdrawal and network fee."),
    "ZRL token mint is not configured": (
        "ZRL_MINT_NOT_CONFIGURED", "The ZRL token mint is not configured on the server."),
    "Treasury ZRL liquidity is insufficient": (
        "TREASURY_ZRL_INSUFFICIENT", "The project's payout wallet does not have enough ZRL for this transfer."),
    "Treasury SOL balance is insufficient for token-account rent and fee": (
        "TREASURY_FEE_INSUFFICIENT", "The project's payout wallet does not have enough SOL for the fee and token account creation."),
}


def describe_failure(exc, nonce):
    chain = _exception_chain(exc)
    status = next((
        getattr(getattr(item, "response", None), "status_code", None)
        for item in chain
        if isinstance(getattr(getattr(item, "response", None), "status_code", None), int)
    ), None)
    stage = exc.stage if isinstance(exc, WithdrawalRpcError) else "prepare_transfer"
    if isinstance(exc, WithdrawalRpcError):
        if status in (401, 403):
            code, message = "RPC_ACCESS_DENIED", "The Solana RPC denied server access. Check the RPC configuration."
        elif status == 429:
            code, message = "RPC_RATE_LIMITED", "The Solana RPC rate-limited server requests."
        elif any("Timeout" in type(item).__name__ for item in chain):
            code, message = "RPC_TIMEOUT", "The Solana RPC did not respond in time."
        elif any(type(item).__name__ == "RPCException" for item in chain):
            code, message = "RPC_REJECTED", "The Solana RPC rejected the transfer data check."
        else:
            code, message = "RPC_UNAVAILABLE", "Could not retrieve data from the Solana RPC."
    else:
        code, message = KNOWN_ERRORS.get(str(exc), (
            "TRANSFER_PREPARATION_FAILED", "The server could not prepare the transfer."
        ))
    return {
        "code": code, "message": message, "stage": stage,
        "types": ">".join(type(item).__name__ for item in chain),
        "httpStatus": status,
        "reference": hashlib.sha256(str(nonce).encode()).hexdigest()[:12],
    }

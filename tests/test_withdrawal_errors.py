"""No real network, credentials or funds."""
from types import SimpleNamespace as NS
import unittest
from withdrawal_errors import describe_failure, rpc_call


class BlankRpcException(Exception):
    pass


class WithdrawalErrorDiagnostics(unittest.TestCase):
    def failure(self, root):
        def fail():
            try:
                raise root
            except Exception as exc:
                raise BlankRpcException() from exc
        try:
            rpc_call("get_zrl_balance", fail)
        except Exception as exc:
            return describe_failure(exc, "synthetic-nonce")

    def test_blank_wrapped_http_errors_have_safe_diagnostics(self):
        for status, expected in [(403, "RPC_ACCESS_DENIED"), (429, "RPC_RATE_LIMITED"), (500, "RPC_UNAVAILABLE")]:
            root = RuntimeError("synthetic-sensitive-url-and-key")
            root.response = NS(status_code=status)
            result = self.failure(root)
            self.assertEqual(result["code"], expected)
            self.assertEqual(result["httpStatus"], status)
            self.assertEqual(result["stage"], "get_zrl_balance")
            self.assertIn("BlankRpcException", result["types"])
            self.assertNotIn("sensitive", str(result))
            self.assertNotIn("synthetic-nonce", str(result))

    def test_timeout_is_classified_without_exposing_its_message(self):
        result = self.failure(TimeoutError("sensitive-rpc-url"))
        self.assertEqual(result["code"], "RPC_TIMEOUT")
        self.assertNotIn("sensitive", str(result))

    def test_known_configuration_error_is_actionable(self):
        result = describe_failure(RuntimeError("Treasury ZRL liquidity is insufficient"), "n")
        self.assertEqual(result["code"], "TREASURY_ZRL_INSUFFICIENT")

    def test_unknown_preparation_error_never_exposes_exception_text(self):
        result = describe_failure(ValueError("synthetic-private-key"), "n")
        self.assertEqual(result["code"], "TRANSFER_PREPARATION_FAILED")
        self.assertNotIn("synthetic-private-key", str(result))

    def test_cyclic_exception_chain_is_bounded(self):
        root = RuntimeError()
        root.__cause__ = root
        self.assertEqual(describe_failure(root, "n")["types"], "RuntimeError")

    def test_success_is_unchanged(self):
        self.assertEqual(rpc_call("read", lambda value: value, 17), 17)

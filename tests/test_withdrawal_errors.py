"""No real network, credentials or funds."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
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

    def rate_limit(self, retry_after="0"):
        error = RuntimeError("synthetic-sensitive-provider-url")
        error.response = NS(status_code=429, headers={"Retry-After": retry_after})
        return error

    @patch("withdrawal_errors.time.sleep")
    def test_read_429_retries_are_bounded_and_respect_short_retry_after(self, sleep):
        operation = Mock(side_effect=[self.rate_limit("1"), self.rate_limit(), 17])
        self.assertEqual(rpc_call("get_zrl_balance", operation), 17)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 1])
        operation = Mock(side_effect=self.rate_limit())
        with self.assertRaises(Exception) as error:
            rpc_call("get_zrl_balance", operation)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(describe_failure(error.exception, "n")["code"], "RPC_RATE_LIMITED")

    @patch("withdrawal_errors.time.sleep")
    def test_broadcast_429_reuses_identical_signed_bytes(self, sleep):
        signed = b"synthetic-signed-transaction"
        operation = Mock(side_effect=[self.rate_limit(), "same-signature"])
        self.assertEqual(rpc_call("broadcast_transaction", operation, signed), "same-signature")
        self.assertEqual([call.args for call in operation.call_args_list], [(signed,), (signed,)])

    @patch("withdrawal_errors.time.sleep")
    def test_timeouts_access_denial_arbitrary_writes_and_long_quota_do_not_retry(self, sleep):
        denied = RuntimeError()
        denied.response = NS(status_code=403)
        for stage, error, args in [
            ("get_zrl_balance", denied, ()),
            ("broadcast_transaction", TimeoutError(), (b"synthetic-tx",)),
            ("arbitrary_write", self.rate_limit(), ()),
            ("broadcast_transaction", self.rate_limit(), ("not-signed-bytes",)),
            ("get_zrl_balance", self.rate_limit("60"), ()),
        ]:
            operation = Mock(side_effect=error)
            with self.assertRaises(Exception):
                rpc_call(stage, operation, *args)
            self.assertEqual(operation.call_count, 1)
        sleep.assert_not_called()

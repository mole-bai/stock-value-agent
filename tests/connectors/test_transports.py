from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from stock_agent.connectors import ConnectorTransportError, CurlTransport


class CurlTransportTests(unittest.TestCase):
    def test_uses_argument_list_tls_verification_and_size_limits(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            calls.append((command, kwargs))
            body_path = Path(command[command.index("--output") + 1])
            header_path = Path(command[command.index("--dump-header") + 1])
            body_path.write_bytes(b"response body")
            header_path.write_bytes(
                b'HTTP/1.1 200 OK\r\nETag: "abc"\r\nLast-Modified: now\r\n\r\n'
            )
            return subprocess.CompletedProcess(
                command, 0, stdout=b"200\nhttps://example.com/final", stderr=b""
            )

        response = CurlTransport(max_bytes=1024, runner=runner)(
            "https://example.com/start", {"Accept": "text/plain"}, 3.0
        )

        command, kwargs = calls[0]
        self.assertIsInstance(command, list)
        self.assertEqual(kwargs["shell"], False)
        self.assertIn("--fail", command)
        self.assertIn("--location", command)
        self.assertIn("--silent", command)
        self.assertIn("--show-error", command)
        self.assertIn("--max-filesize", command)
        self.assertNotIn("-k", command)
        self.assertNotIn("--insecure", command)
        self.assertEqual(response.body, b"response body")
        self.assertEqual(response.headers["ETag"], '"abc"')
        self.assertEqual(response.final_url, "https://example.com/final")

    def test_rejects_oversized_output_even_if_runner_claims_success(self) -> None:
        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
            Path(command[command.index("--output") + 1]).write_bytes(b"12345")
            Path(command[command.index("--dump-header") + 1]).write_bytes(
                b"HTTP/1.1 200 OK\r\n\r\n"
            )
            return subprocess.CompletedProcess(
                command, 0, stdout=b"200\nhttps://example.com", stderr=b""
            )

        with self.assertRaisesRegex(ConnectorTransportError, "exceeded"):
            CurlTransport(max_bytes=4, runner=runner)("https://example.com", {}, 1)

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTP"):
            CurlTransport()("file:///etc/passwd", {}, 1)


if __name__ == "__main__":
    unittest.main()

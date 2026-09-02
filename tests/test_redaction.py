import unittest

from dop.core.errors import SecurityViolationError
from dop.core.security import redact


class TestRedaction(unittest.TestCase):
    def test_assignment_redaction(self):
        text = "TOKEN=abc123"
        redacted = redact(text)
        self.assertIn("TOKEN=***", redacted)
        self.assertNotIn("abc123", redacted)

    def test_bearer_redaction(self):
        text = "Authorization: Bearer abc.def.ghi"
        redacted = redact(text)
        self.assertIn("Bearer [REDACTED]", redacted)

    def test_json_redaction(self):
        text = '{"secret":"value"}'
        redacted = redact(text)
        self.assertIn('"secret":"***"', redacted)
        self.assertNotIn("value", redacted)

    def test_env_dump_blocked(self):
        with self.assertRaises(SecurityViolationError):
            redact("os.environ")


if __name__ == "__main__":
    unittest.main()

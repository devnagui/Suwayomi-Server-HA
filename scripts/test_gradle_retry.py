import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "gradle_retry", Path(__file__).resolve().parents[1] / "suwayomi" / "gradle_retry.py",
)
gradle = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gradle
spec.loader.exec_module(gradle)

DOWNLOAD_504 = """Execution failed for task ':AndroidCompat:Config:compileKotlin'.
> Could not resolve all files for configuration ':AndroidCompat:Config:compileClasspath'.
  > Could not resolve com.github.Aliucord:axml:ed26565eb0.
    > Could not GET 'https://github.com/Suwayomi/Suwayomi-Server/raw/android-jar/axml.pom'.
      > Received status code 504 from server: Gateway Timeout
BUILD FAILED
"""


def result(output, code=1, timed_out=False):
    evidence = gradle.Evidence()
    evidence.feed(output)
    return gradle.Result(code, evidence, timed_out)


class GradleRetryTests(unittest.TestCase):
    def test_confirmed_504_then_success_preserves_command_and_deadline(self):
        runner = Mock(side_effect=[result(DOWNLOAD_504), result("BUILD SUCCESSFUL", 0)])
        sleep = Mock()
        argv = ["./gradlew", "--no-daemon", ":server:test", ":server:shadowJar"]
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(gradle.run(argv, runner=runner, sleep=sleep), 0)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args.args, (argv, 480))
        sleep.assert_called_once_with(2)
        self.assertIn("retry 2/3", output.getvalue())

    def test_three_attempts_return_original_error(self):
        runner = Mock(return_value=result(DOWNLOAD_504, 7))
        sleep = Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gradle.run(["./gradlew"], runner=runner, sleep=sleep), 7)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_only_explicit_download_transport_errors_are_retryable(self):
        for status in (408, 429, 500, 502, 503, 504, 599):
            with self.subTest(status=status):
                self.assertTrue(result(DOWNLOAD_504.replace("504", str(status))).evidence.retryable())
        for error in (
            "Connection reset", "Read timed out", "connect timed out",
            "java.net.UnknownHostException: github.com", "Name or service not known",
        ):
            with self.subTest(error=error):
                self.assertTrue(result("Could not GET 'https://example.com/x'.\n> " + error).evidence.retryable())
        self.assertFalse(result("HTTP 504 from application test endpoint").evidence.retryable())
        self.assertFalse(result("Could not GET 'https://example.com/x'.\n> Unknown failure").evidence.retryable())

    def test_nontransient_and_mixed_failures_are_never_retried(self):
        failures = [
            "e: /source/Main.kt: Unresolved reference",
            "Compilation error. See log for more details",
            "Compilation failed",
            "> Task :server:test FAILED",
            "There were failing tests",
            "Dependency verification failed for configuration",
            "SHA256 hash mismatch",
            "patch failed: source/Main.kt",
            "Could not apply patch",
            "javax.net.ssl.SSLHandshakeException: PKIX validation failed",
        ]
        failures.extend(DOWNLOAD_504.replace("504", str(status)) for status in (400, 401, 403, 404, 422))
        for failure in failures:
            for output in (failure, DOWNLOAD_504 + failure):
                with self.subTest(output=output):
                    runner = Mock(return_value=result(output, 9))
                    sleep = Mock()
                    self.assertEqual(gradle.run(["./gradlew"], runner=runner, sleep=sleep), 9)
                    runner.assert_called_once()
                    sleep.assert_not_called()

    def test_hard_failure_is_not_lost_when_tail_is_truncated(self):
        evidence = gradle.Evidence()
        evidence.feed("Compilation failed\n")
        evidence.feed("x" * (gradle.TAIL_LIMIT * 2))
        evidence.feed(DOWNLOAD_504)
        self.assertEqual(len(evidence.tail), gradle.TAIL_LIMIT)
        self.assertFalse(evidence.retryable())

    def test_split_output_retains_transient_and_hard_evidence(self):
        evidence = gradle.Evidence()
        for part in (DOWNLOAD_504[:100], DOWNLOAD_504[100:], "\nCompila", "tion failed"):
            evidence.feed(part)
        self.assertFalse(evidence.retryable())

    def test_timeout_is_nonzero_and_not_retried_even_with_transport_evidence(self):
        runner = Mock(return_value=result(DOWNLOAD_504, 124, True))
        sleep = Mock()
        self.assertEqual(gradle.run(["./gradlew"], runner=runner, sleep=sleep), 124)
        runner.assert_called_once()
        sleep.assert_not_called()

    def test_real_process_streams_output_and_preserves_nonzero_exit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            attempt = gradle.run_attempt(
                [sys.executable, "-c", "import sys; print('original error'); sys.exit(7)"], 5,
            )
        self.assertEqual(attempt.returncode, 7)
        self.assertFalse(attempt.timed_out)
        self.assertIn("original error", output.getvalue())
        self.assertIn("original error", attempt.evidence.tail)

    def test_real_process_timeout_terminates_without_success_fallback(self):
        started = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            attempt = gradle.run_attempt(
                [sys.executable, "-c", "import time; print('running', flush=True); time.sleep(60)"], 0.15,
            )
        self.assertEqual(attempt.returncode, 124)
        self.assertTrue(attempt.timed_out)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("deadline; not retrying", error.getvalue())

    def test_process_launch_uses_argv_without_shell(self):
        process = Mock()
        process.stdout.read1.return_value = b""
        process.returncode = 3
        with patch.object(gradle.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(gradle.run_attempt(["./gradlew", "argument with spaces"], 5).returncode, 3)
        self.assertEqual(popen.call_args.args[0], ["./gradlew", "argument with spaces"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_reader_failure_cannot_turn_successful_process_into_success(self):
        for failure in (OSError("stdout read failed"), UnicodeError("stdout conversion failed")):
            with self.subTest(failure=failure):
                process = Mock()
                process.stdout.read1.side_effect = failure
                process.returncode = 0
                with patch.object(gradle.subprocess, "Popen", return_value=process):
                    with contextlib.redirect_stderr(io.StringIO()) as error:
                        attempt = gradle.run_attempt(["./gradlew"], 5)
                self.assertEqual(attempt.returncode, 1)
                self.assertFalse(attempt.evidence.retryable())
                self.assertIn("Gradle output streaming failed", error.getvalue())


if __name__ == "__main__":
    unittest.main()

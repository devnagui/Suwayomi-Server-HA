"""Retry only explicit Gradle dependency-download transport failures."""

import argparse
from dataclasses import dataclass
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

TAIL_LIMIT = 65536
ATTEMPTS = 3
ATTEMPT_TIMEOUT = 480
HARD_FAILURE = re.compile(
    r"^\s*e:\s|compilation (?:error|failed)|(?:there were|there are) failing tests|"
    r"\btests? failed\b|>\s*Task\s+\S*:test\S*\s+FAILED|"
    r"(?:checksum|digest|hash|signature|verification).{0,80}(?:fail|mismatch|invalid)|"
    r"(?:fail|mismatch|invalid).{0,80}(?:checksum|digest|hash|signature)|"
    r"patch failed|could not apply patch|SSLHandshakeException|PKIX|"
    r"(?:HTTP(?: response)?(?: status)?(?: code)?[: ]+|status code[: ]+)"
    r"4(?:0[0-79]|1\d|2[0-8]|[3-9]\d)\b",
    re.IGNORECASE | re.MULTILINE,
)
DOWNLOAD = re.compile(
    r"could not (?:GET|HEAD|download)\b|"
    r"downloading\s+https?://|"
    r"HTTP response code:\s*\d+\s+for URL:\s*https?://",
    re.IGNORECASE,
)
TRANSPORT = re.compile(
    r"(?:HTTP(?: response)?(?: status)?(?: code)?[: ]+|status code[: ]+)"
    r"(?:408|429|5\d\d)\b|"
    r"connection reset|connection timed out|connect timed out|read timed out|"
    r"UnknownHostException|temporary failure in name resolution|"
    r"name or service not known|no such host is known|"
    r"(?:connect|read|download).{0,40}timeout",
    re.IGNORECASE,
)


class Evidence:
    def __init__(self):
        self.tail = ""
        self.hard_failure = False

    def feed(self, text):
        combined = self.tail + text
        self.hard_failure |= bool(HARD_FAILURE.search(combined))
        self.tail = combined[-TAIL_LIMIT:]

    def retryable(self):
        if self.hard_failure:
            return False
        for download in DOWNLOAD.finditer(self.tail):
            if TRANSPORT.search(self.tail[download.start():download.start() + 2000]):
                return True
        return False


@dataclass
class Result:
    returncode: int
    evidence: Evidence
    timed_out: bool = False


def run_attempt(argv, timeout):
    evidence = Evidence()
    reader_errors = queue.SimpleQueue()
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )

    def stream():
        import codecs
        try:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while chunk := process.stdout.read1(4096):
                text = decoder.decode(chunk)
                evidence.feed(text)
                sys.stdout.write(text)
                sys.stdout.flush()
            text = decoder.decode(b"", final=True)
            evidence.feed(text)
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, UnicodeError) as error:
            reader_errors.put(error)

    reader = threading.Thread(target=stream, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
        reader.join(timeout=max(0, deadline - time.monotonic()))
        if reader.is_alive():
            timed_out = True
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if timed_out:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=10)
            reader.join(timeout=10)
        if not reader.is_alive():
            process.stdout.close()
    try:
        reader_error = reader_errors.get_nowait()
    except queue.Empty:
        reader_error = None
    if reader_error is not None:
        evidence.hard_failure = True
        print(f"Gradle output streaming failed: {reader_error}", file=sys.stderr)
    if timed_out:
        print(f"Gradle attempt exceeded its {timeout}-second deadline; not retrying.", file=sys.stderr)
        return Result(124, evidence, True)
    return Result(process.returncode or (1 if reader_error is not None else 0), evidence)


def run(argv, *, timeout=ATTEMPT_TIMEOUT, runner=run_attempt, sleep=time.sleep):
    for attempt in range(1, ATTEMPTS + 1):
        result = runner(argv, timeout)
        if result.returncode == 0 or result.timed_out or not result.evidence.retryable():
            return result.returncode
        if attempt == ATTEMPTS:
            return result.returncode
        delay = attempt * 2
        print(
            f"Gradle download transport failure; retry {attempt + 1}/{ATTEMPTS} in {delay}s.",
            file=sys.stderr,
        )
        sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=int, default=ATTEMPT_TIMEOUT)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.timeout_seconds <= 0 or not command:
        parser.error("a positive deadline and process command are required")
    return run(command, timeout=args.timeout_seconds)


if __name__ == "__main__":
    sys.exit(main())

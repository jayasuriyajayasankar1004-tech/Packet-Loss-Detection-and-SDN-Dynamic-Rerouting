"""Local UDP sender/receiver integration tests (no Mininet or Ryu required)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDER = ROOT / "member1" / "sender.py"
RECEIVER = ROOT / "member1" / "receiver.py"


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class UdpRecoveryIntegrationTests(unittest.TestCase):
    def test_dropped_packet_is_retransmitted_and_acknowledged(self):
        port = free_udp_port()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            receiver_stats = tmp_path / "receiver.json"
            sender_stats = tmp_path / "sender.json"
            receiver_output = tmp_path / "received.txt"
            recv_log = tmp_path / "recv.csv"
            sent_log = tmp_path / "sent.csv"

            receiver_cmd = [
                sys.executable, str(RECEIVER),
                "--host", "127.0.0.1", "--port", str(port),
                "--drop-once", "2", "--loss", "0",
                "--idle", "1.5", "--stats", str(receiver_stats),
                "--output", str(receiver_output), "--recv-log", str(recv_log),
            ]
            receiver = subprocess.Popen(
                receiver_cmd, cwd=ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
            try:
                # Wait until receiver has bound the UDP socket.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if receiver.poll() is not None:
                        output = receiver.stdout.read() if receiver.stdout else ""
                        self.fail(f"Receiver exited before sender started: {output}")
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                            probe.sendto(b"{}", ("127.0.0.1", port))
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail("Receiver did not become ready")
                # The probe is malformed but harmless; the receiver continues waiting.
                sender_cmd = [
                    sys.executable, str(SENDER),
                    "--host", "127.0.0.1", "--port", str(port),
                    "--count", "5", "--interval", "0.01",
                    "--timeout", "0.15", "--retries", "8",
                    "--stats", str(sender_stats), "--sent-log", str(sent_log),
                ]
                sender_result = subprocess.run(
                    sender_cmd, cwd=ROOT, capture_output=True, text=True, timeout=15
                )
                self.assertEqual(
                    sender_result.returncode, 0,
                    msg=f"Sender failed:\n{sender_result.stdout}\n{sender_result.stderr}",
                )
                receiver_output_text, _ = receiver.communicate(timeout=8)
                self.assertEqual(receiver.returncode, 0, msg=receiver_output_text)

                sender_data = json.loads(sender_stats.read_text(encoding="utf-8"))
                receiver_data = json.loads(receiver_stats.read_text(encoding="utf-8"))
                self.assertTrue(sender_data["success"], sender_data)
                self.assertEqual(sender_data["acknowledged"], 5)
                self.assertGreaterEqual(sender_data["retransmissions"], 1)
                self.assertTrue(receiver_data["success"], receiver_data)
                self.assertEqual(receiver_data["unique_received"], 5)
                self.assertEqual(receiver_data["final_loss_percent"], 0.0)
                self.assertIn(2, receiver_data["gap_sequences"])
                self.assertEqual(
                    receiver_output.read_text(encoding="utf-8").splitlines(),
                    [f"Packet {seq}" for seq in range(1, 6)],
                )
            finally:
                if receiver.poll() is None:
                    receiver.terminate()
                    try:
                        receiver.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        receiver.kill()
                        receiver.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()

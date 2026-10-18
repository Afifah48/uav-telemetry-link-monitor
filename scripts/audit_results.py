"""Independently cross-check saved reports against captures and per-datagram CSV."""
import base64
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from uav_telemetry.mavlink.codec import Decoder


def main() -> None:
    root = Path("results/experiments")
    summary = json.loads((root / "summary.json").read_text())
    audit = {}
    for name, report in summary.items():
        folder = root / name
        source = [json.loads(line) for line in (folder / "source.jsonl").read_text().splitlines()]
        proxy = [json.loads(line) for line in (folder / "proxy.jsonl").read_text().splitlines()]
        receiver = [json.loads(line) for line in (folder / "receiver.jsonl").read_text().splitlines()]
        counters = Counter(row["event"] for row in proxy)
        csv_rows = list(csv.DictReader((folder / "datagrams.csv").open(newline="", encoding="utf-8")))
        decoder = Decoder()
        source_frames = 0
        for row in source:
            payload = base64.b64decode(row["payload_b64"], validate=True)
            assert hashlib.sha256(payload).hexdigest() == row["sha256"]
            source_frames += len(decoder.feed(payload, ("fixture", 1)))
        assert decoder.bad_data == 0
        assert len(source) == report["source_sent_datagrams"] == report["proxy"]["input"] == counters["proxy_input"]
        assert report["proxy"]["forwarded"] == counters["proxy_forward"]
        assert report["proxy"]["dropped"] == counters["proxy_drop"]
        assert len(csv_rows) == report["proxy"]["input"]
        assert sum(row["received"] == "True" for row in csv_rows) == report["matched_received_datagrams"]
        assert len(receiver) == report["receiver_datagrams"]
        decoded = sum(row["decoded_messages"] for row in receiver)
        assert decoded == report["receiver"]["received_messages"]
        assert report["matched_datagram_pdr"] == len(receiver) / len(source)
        audit[name] = {"datagrams": len(source), "source_frames": source_frames,
                       "received_frames": decoded, "artifacts_consistent": True}
    Path("results/artifact-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()

"""Import the shipped sample backup and check the annotated out-of-range point."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-backup" / "logbook-backup.json"


def _limits(backup: dict) -> dict[tuple[str, str], tuple[object, object]]:
    limits = {}
    for eq in backup["equipment"]:
        for param in eq.get("params") or []:
            limits[(eq["id"], param["key"])] = (param.get("min"), param.get("max"))
    return limits


def _is_oor(value, minimum, maximum) -> bool:
    if value is None:
        return False
    if minimum not in (None, "") and value < float(minimum):
        return True
    if maximum not in (None, "") and value > float(maximum):
        return True
    return False


def _annotated_oor(backup: dict) -> tuple[dict, str, float, dict]:
    limits = _limits(backup)
    found = []
    for log in backup["logs"]:
        equipment_id = log["equipmentId"]
        for key, value in (log.get("readings") or {}).items():
            minimum, maximum = limits.get((equipment_id, key), (None, None))
            if _is_oor(value, minimum, maximum):
                found.append((log, key, float(value), (log.get("annotations") or {}).get(key) or {}))
    assert len(found) == 1, "sample backup must contain exactly one out-of-range reading"
    log, key, value, note = found[0]
    assert note, "the out-of-range reading must carry an operator note"
    return log, key, value, note


def test_sample_backup_import_preserves_oor_note(client: TestClient):
    backup = json.loads(SAMPLE.read_text())
    log, parameter, value, note = _annotated_oor(backup)

    with SAMPLE.open("rb") as fh:
        response = client.post(
            "/api/migrate/v2/file",
            files={"file": (SAMPLE.name, fh, "application/json")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["equipment"] == len(backup["equipment"])
    expected_readings = sum(len(row.get("readings") or {}) for row in backup["logs"])
    assert body["readings"] == expected_readings

    trend = client.get(
        "/api/readings",
        params={"equipment_id": log["equipmentId"], "parameter": parameter},
    )
    assert trend.status_code == 200, trend.text
    points = trend.json()["readings"]
    assert points, "Trends API returned no points for the imported series"

    all_readings = client.get("/api/readings", params={"limit": 10000}).json()["readings"]
    oor = [row for row in all_readings if row["out_of_range"]]
    assert len(oor) == 1
    reading = oor[0]

    assert reading["equipment_id"] == log["equipmentId"]
    assert reading["entry_id"] == log["id"]
    assert reading["parameter"] == parameter
    assert reading["value"] == value
    assert reading["date"] == log["date"]
    assert reading["time"] == log["time"]
    assert reading["out_of_range"] is True
    assert reading["annotation"] is not None
    assert reading["annotation"]["cause"] == note["cause"]
    assert reading["annotation"]["action"] == note["action"]
    assert reading["annotation"]["by"] == note["by"]
    assert reading["annotation"]["at"] == note["at"]

    matching = [
        point for point in points
        if point["id"] == reading["id"]
        and point["date"] == reading["date"]
        and point["time"] == reading["time"]
        and point["value"] == reading["value"]
    ]
    assert len(matching) == 1
    point = matching[0]
    assert point["out_of_range"] is True
    assert point["annotation"] is not None
    assert point["annotation"]["cause"] == note["cause"]
    assert point["annotation"]["action"] == note["action"]
    assert point["annotation"]["by"] == note["by"]
    assert point["annotation"]["at"] == note["at"]

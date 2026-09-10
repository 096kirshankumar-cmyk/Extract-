"""dashboard.py <-> review layer integration: /review page, queue,
audit, decision/edit endpoints, zip + crops routes (flask
test_client, synthetic output root)."""
import io
import json
import zipfile

import pytest

flask = pytest.importorskip("flask")

from qbank import config

MD = "| A | B |\n|---|---|\n| gluedof text | ok |"
MD_FIXED = "| A | B |\n|---|---|\n| glued of text | ok |"


def _mkroot(tmp_path):
    root = tmp_path / "out"
    ch = root / "split" / "TST" / "TST-001"
    ch.mkdir(parents=True)
    (ch / "questions.jsonl").write_text(json.dumps({
        "q_id": "TST-001-001",
        "tables": [{"table_id": "T1", "markdown": MD,
                    "source_pages": [10],
                    "validation": {"table_qa": {
                        "status": "REVIEW",
                        "suspect_fragments": ["gluedof"]}}}]}) + "\n")
    (root / "data").mkdir()
    (root / "data" / "audit_report.jsonl").write_text(json.dumps(
        {"kind": "thin_options", "q_id": "TST-001-001",
         "severity": "HIGH"}) + "\n")
    return root


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = _mkroot(tmp_path)
    monkeypatch.setattr(config, "OUTPUT_ROOT", root)
    monkeypatch.setattr(config, "SUBJECTS_DIR", root / "subjects")
    import dashboard
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


def test_review_page_served(client):
    r = client.get("/review")
    assert r.status_code == 200
    assert b"Table Review Dashboard" in r.data


def test_queue_lists_pending_table(client):
    q = client.get("/api/queue").get_json()
    assert len(q) == 1
    assert q[0]["book"] == "TST" and q[0]["state"] == "pending"
    assert q[0]["suspects"] == ["gluedof"]


def test_audit_report_served(client):
    a = client.get("/api/audit").get_json()
    assert [f["kind"] for f in a] == ["thin_options"]


def test_decision_moves_state(client):
    r = client.post("/api/decision", json={
        "book": "TST", "q_id": "TST-001-001", "table_id": "T1",
        "action": "approve"})
    assert r.status_code == 200
    q = client.get("/api/queue").get_json()
    assert q[0]["state"] == "decided:approve"


def test_edit_rewrites_copies_and_decides(client):
    r = client.post("/api/edit", json={
        "book": "TST", "q_id": "TST-001-001", "table_id": "T1",
        "markdown": MD_FIXED, "action": "approve"})
    j = r.get_json()
    assert j["ok"] and j["copies"] >= 1
    on_disk = (config.OUTPUT_ROOT / "split" / "TST" / "TST-001"
               / "questions.jsonl").read_text()
    assert "glued of text" in on_disk
    q = client.get("/api/queue").get_json()
    assert q[0]["state"] == "decided:approve"


def test_edit_missing_fields_400(client):
    r = client.post("/api/edit", json={"book": "TST"})
    assert r.status_code == 400


def test_zip_missing_then_present(client):
    assert client.get("/zip/TST").status_code == 404
    zp = config.OUTPUT_ROOT / "final_export.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("hello.txt", "hi")
    r = client.get("/zip/TST")
    assert r.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(r.data)).read("hello.txt") == b"hi"


def test_crops_route(client):
    (config.OUTPUT_ROOT / "crops").mkdir()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    (config.OUTPUT_ROOT / "crops" / "tst_p0010.png").write_bytes(png)
    r = client.get("/crops/tst_p0010.png")
    assert r.status_code == 200 and r.data == png
    assert client.get("/crops/nope.png").status_code == 404
    # traversal must never serve a file (400 by our check, 404 by
    # werkzeug routing — either is fine)
    r = client.get("/crops/..%2fsecret.png")
    assert r.status_code in (400, 404) and r.data != png

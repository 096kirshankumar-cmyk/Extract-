"""Human review layer: decisions, verified edits, staleness, zip gate.

Synthetic output dir is fine here — these test the ledger/edit/gate
MECHANICS (the spacing regression classes live in
test_spacing_regressions.py against the real books)."""
import json
import os

from qbank import review
from qbank.export import gate_final_zip

MD = ("| A | B |\n|---|---|\n| glued textof the cell | ok |")
MD_FIXED = ("| A | B |\n|---|---|\n| glued text of the cell | ok |")


def _mkroot(tmp_path, md=MD):
    root = tmp_path / "out"
    ch = root / "split" / "TST" / "TST-001"
    ch.mkdir(parents=True)
    row = {"q_id": "TST-001-001",
           "tables": [{"table_id": "001-T01", "markdown": md,
                       "source_pages": [1],
                       "validation": {"table_qa": {
                           "status": "REVIEW",
                           "suspect_fragments": ["textof"]}}}]}
    (ch / "questions.jsonl").write_text(json.dumps(row) + "\n")
    (ch / "solutions.jsonl").write_text(json.dumps(
        {"q_id": "TST-001-001", "tables": [row["tables"][0]]}) + "\n")
    (ch / "chapter_completeness.json").write_text(json.dumps(
        {"chapter_id": "TST-001", "census": {"ok": True},
         "qa_status_counts": {}, "unresolved_qid_count": 0}))
    return root


def test_queue_pending_then_decided(tmp_path):
    root = _mkroot(tmp_path)
    items = review.review_tables(root)
    assert len(items) == 1 and items[0]["state"] == "pending"
    assert review.pending_count(root) == 1

    review.record_decision(root, "TST", "TST-001-001", "001-T01", "approve")
    items = review.review_tables(root)
    assert items[0]["state"] == "decided:approve"
    assert review.pending_count(root) == 0


def test_edit_verified_and_stale(tmp_path):
    root = _mkroot(tmp_path)
    # uneven markdown is refused before any write
    bad = review.apply_table_edit(root, "TST", "TST-001-001", "001-T01",
                                  "| a | b |\n|---|\n| x |")
    assert not bad["ok"]

    res = review.apply_table_edit(root, "TST", "TST-001-001", "001-T01",
                                  MD_FIXED)
    assert res["ok"] and res["copies"] == 2      # questions + solutions
    # both copies carry the fix on disk
    for nf in ("questions.jsonl", "solutions.jsonl"):
        txt = (root / "split" / "TST" / "TST-001" / nf).read_text()
        assert "glued text of the cell" in txt

    # a decision taken BEFORE a later content change goes STALE and
    # the flag re-opens (safe direction only)
    review.record_decision(root, "TST", "TST-001-001", "001-T01", "approve")
    assert review.review_tables(root)[0]["state"] == "decided:approve"
    review.apply_table_edit(root, "TST", "TST-001-001", "001-T01",
                            MD_FIXED + "\n| c | d |")
    assert review.review_tables(root)[0]["state"].startswith("stale")
    assert review.pending_count(root) == 1


def test_gate_locked_until_reviewed(tmp_path, monkeypatch):
    root = _mkroot(tmp_path)
    monkeypatch.delenv("QBANK_FORCE_EXPORT", raising=False)
    g = gate_final_zip(root, "TST")
    assert g["locked"] and "awaiting human decision" in g["why"]

    review.record_decision(root, "TST", "TST-001-001", "001-T01", "approve")
    g = gate_final_zip(root, "TST")
    assert not g["locked"], g["why"]

    # override works even while pending
    review.apply_table_edit(root, "TST", "TST-001-001", "001-T01",
                            MD_FIXED)          # makes decision stale
    assert gate_final_zip(root, "TST")["locked"]
    monkeypatch.setenv("QBANK_FORCE_EXPORT", "1")
    assert not gate_final_zip(root, "TST")["locked"]

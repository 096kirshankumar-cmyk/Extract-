"""Post-run audit scan: numeric drift vs page-text evidence, duplicate
stems, thin options, bad answer key. Read-only by contract — the scan
must never touch the split files."""
import json

from qbank.audit import audit_book, write_report

STEM = ("A 45 year old man presents with chest pain since 2 hours. "
        "ECG shows ST elevation in leads II III and aVF. Which coronary "
        "artery is most likely occluded?")
PAGE10 = ("Question 1: A 45 year old man presents with chest pain "
          "since 2 hours. ECG shows ST elevation in leads II III and "
          "aVF. Which coronary artery is most likely occluded? "
          "(A) RCA (B) LAD (C) LCX (D) PDA")
PAGE50 = ("Solution to Question 1: Inferior wall MI. The RCA supplies "
          "the inferior wall in 90% of people.")


def _mkroot(tmp_path, *, qtext=STEM, opts=("RCA", "LAD", "LCX", "PDA"),
            sol="Inferior wall MI. The RCA supplies the inferior wall "
                "in 90% of people.",
            answer="A", pages=(PAGE10, PAGE50), page_text=True):
    root = tmp_path / "out"
    ch = root / "split" / "TST" / "TST-001"
    ch.mkdir(parents=True)
    (ch / "questions.jsonl").write_text(json.dumps({
        "q_id": "TST-001-001", "chapter_id": "TST-001", "subject": "TST",
        "question_text": qtext,
        "options": [{"id": l, "text": t}
                    for l, t in zip("ABCD", opts)],
        "tables": [], "source_pages": [10]}) + "\n")
    (ch / "answers.jsonl").write_text(json.dumps(
        {"q_id": "TST-001-001", "correct_option": answer}) + "\n")
    (ch / "solutions.jsonl").write_text(json.dumps({
        "q_id": "TST-001-001", "solution_text": sol,
        "source_pages": [50]}) + "\n")
    data = root / "data"
    data.mkdir()
    if page_text:
        rows = [{"page": 10, "text": pages[0]},
                {"page": 50, "text": pages[1]}]
        (data / "page_text.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
    return root


def test_clean_book_has_no_flags(tmp_path):
    root = _mkroot(tmp_path)
    res = audit_book(root, subject="TST")
    assert res["page_text"] is True
    assert res["rows_scanned"] == 1
    assert res["by_kind"] == {}, res["flags"]


def test_numeric_drift_in_stem_flagged(tmp_path):
    root = _mkroot(tmp_path, qtext=STEM.replace("45 year", "47 year"))
    res = audit_book(root, subject="TST")
    drift = [f for f in res["flags"] if f["kind"] == "numeric_drift"]
    assert [f["value"] for f in drift] == ["47"]
    assert drift[0]["where"] == "question_text"


def test_numeric_drift_in_solution_flagged(tmp_path):
    root = _mkroot(tmp_path, sol="RCA supplies it in 95% of people.")
    res = audit_book(root, subject="TST")
    drift = [f for f in res["flags"] if f["kind"] == "numeric_drift"]
    assert [f["value"] for f in drift] == ["95"]
    assert drift[0]["where"] == "solution_text"


def test_grouped_number_matches_ungrouped_evidence(tmp_path):
    root = _mkroot(tmp_path,
                   qtext=STEM + " Troponin rose 10-fold, WBC 11,000.",
                   pages=(PAGE10 + " Troponin rose 10-fold, WBC 11000.",
                          PAGE50))
    res = audit_book(root, subject="TST")
    assert [f for f in res["flags"] if f["kind"] == "numeric_drift"] == []


def test_missing_page_text_skips_numeric_check_without_crash(tmp_path):
    root = _mkroot(tmp_path, qtext=STEM.replace("45 year", "47 year"),
                   page_text=False)
    res = audit_book(root, subject="TST")
    assert res["page_text"] is False
    assert [f for f in res["flags"] if f["kind"] == "numeric_drift"] == []


def test_table_number_uses_table_source_pages(tmp_path):
    # the row itself may carry no source_pages; the table's own pages
    # are its evidence (smoke-run regression: value falsely flagged)
    root = tmp_path / "out"
    ch = root / "split" / "TST" / "TST-001"
    ch.mkdir(parents=True)
    (ch / "questions.jsonl").write_text(json.dumps({
        "q_id": "TST-001-001", "chapter_id": "TST-001", "subject": "TST",
        "question_text": "Which dose is right?",
        "options": [{"id": l, "text": t}
                    for l, t in zip("ABCD", "abcd")],
        "tables": [{"table_id": "T1",
                    "markdown": "| Dose |\n|---|\n| 90 mg |",
                    "source_pages": [10]}]}) + "\n")
    data = root / "data"
    data.mkdir()
    (data / "page_text.jsonl").write_text(json.dumps(
        {"page": 10, "text": "Dose 90 mg once daily"}) + "\n")
    res = audit_book(root, subject="TST")
    assert [f for f in res["flags"] if f["kind"] == "numeric_drift"] == []


def test_duplicate_stem_flagged(tmp_path):
    root = _mkroot(tmp_path)
    ch = root / "split" / "TST" / "TST-001"
    with (ch / "questions.jsonl").open("a") as f:
        f.write(json.dumps({
            "q_id": "TST-001-002", "chapter_id": "TST-001", "subject": "TST",
            "question_text": STEM,
            "options": [{"id": l, "text": t}
                        for l, t in zip("ABCD", ("A", "B", "C", "D"))],
            "tables": [], "source_pages": [10]}) + "\n")
    res = audit_book(root, subject="TST")
    dups = [f for f in res["flags"] if f["kind"] == "duplicate_question"]
    assert [f["q_id"] for f in dups] == ["TST-001-002"]
    assert "TST-001-001" in dups[0]["detail"]


def test_thin_options_and_bad_answer_flagged(tmp_path):
    root = _mkroot(tmp_path, opts=("RCA", "", "", ""), answer="E")
    res = audit_book(root, subject="TST")
    kinds = {f["kind"] for f in res["flags"]}
    assert "thin_options" in kinds
    assert "bad_answer" in kinds


def test_report_written_and_scan_is_read_only(tmp_path):
    root = _mkroot(tmp_path, qtext=STEM.replace("45 year", "47 year"))
    before = {p: p.read_bytes() for p in
              (root / "split" / "TST" / "TST-001").iterdir()}
    res = audit_book(root, subject="TST")
    path = write_report(root, res)
    rows = [json.loads(l) for l in
            path.read_text().splitlines() if l.strip()]
    assert len(rows) == len(res["flags"]) >= 1
    after = {p: p.read_bytes() for p in
             (root / "split" / "TST" / "TST-001").iterdir()}
    assert before == after                # audit never modifies output

"""Gemini table pass: fidelity envelope, cache, fallback. The network
is stubbed — no API key needed."""

import json

from qbank import llm


def test_merge_llm_envelope():
    det = [["Increasedpulmonary blood fl ow", "TAPVC"],
           ["Cyanot ic", "Tetrology of Fallot"]]
    good = [["Increased pulmonary blood flow", "TAPVC"],
            ["Cyanotic", "Tetrology of Fallot"]]
    out, n = llm.merge_llm(det, good)
    assert out[0][0] == "Increased pulmonary blood flow"
    assert out[1][0] == "Cyanotic"
    assert n == 2                      # re-spacings only
    # model "corrects" a printed word -> rejected cell, det kept
    bad = [["Increased pulmonary blood flow", "TAPVC"],
           ["Cyanotic", "Tetralogy of Fallot"]]
    out, n = llm.merge_llm(det, bad)
    assert out[1][1] == "Tetrology of Fallot"
    # shape mismatch -> whole table rejected
    out, n = llm.merge_llm(det, [good[0]])
    assert out == det and n == 0
    out, n = llm.merge_llm(det, None)
    assert out == det and n == 0


def test_build_box_llm_accept(tmp_path):
    from collections import Counter
    from test_tables import _book_with
    from qbank.tables import build_box
    book = _book_with(tmp_path, [[["AB CD", "E"], ["1", "2"]]])
    box = book.page(1).table_boxes[0]
    bt = build_box(book, 1, tuple(box), Counter(),
                   llm=lambda b, p, bx: [["ABCD", "E", ""], ["1", "2", ""]])
    assert bt.llm_fixes == 1
    assert bt.rows[0][0] == "ABCD"
    # model rewords a cell -> rejected, deterministic kept
    bt2 = build_box(book, 1, tuple(box), Counter(),
                    llm=lambda b, p, bx: [["AB CX", "E", ""], ["1", "2", ""]])
    assert bt2.llm_fixes == 0
    assert bt2.rows[0][0] == "AB CD"
    book.close()


def test_transcriber_cache_and_fallback(tmp_path, monkeypatch):
    calls = []

    class FakePix:
        def tobytes(self, fmt):
            return b"pngbytes"

    class FakePage:
        def get_pixmap(self, clip, matrix):
            return FakePix()

    class FakeDoc:
        name = "fake.pdf"

        def __getitem__(self, i):
            return FakePage()

    class FakeBook:
        doc = FakeDoc()

    def fake_post(url, payload, key):
        calls.append(payload)
        return {"candidates": [{"content": {"parts": [
            {"text": json.dumps({"rows": [["a b", "c"]]})}
        ]}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    fn = llm.transcriber(cache_dir=tmp_path, key="K")
    rows = fn(FakeBook(), 5, (0, 0, 100, 100))
    assert rows == [["a b", "c"]]
    rows2 = fn(FakeBook(), 5, (0, 0, 100, 100))
    assert rows2 == rows and len(calls) == 1     # cache hit, no 2nd call

    def boom(url, payload, key):
        raise OSError("network down")

    monkeypatch.setattr(llm, "_post", boom)
    fn2 = llm.transcriber(cache_dir=None, key="K")
    assert fn2(FakeBook(), 5, (0, 0, 100, 100)) is None   # fallback

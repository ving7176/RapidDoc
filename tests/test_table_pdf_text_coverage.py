"""pdf_text_coverage_threshold 表格 OCR 兜底判据的单元测试。

覆盖 calc_table_pdf_text_coverage 纯函数与
_extract_table_text_from_pdf 的覆盖率回退分支（mock 掉重依赖）。
"""
import pytest

from rapid_doc.backend.pipeline.analyze_utils import (
    calc_table_pdf_text_coverage,
    _extract_table_text_from_pdf,
)


def _span(bbox, content=None, words=None, use_word_box=False):
    span = {
        'bbox': bbox,
        'ori_bbox': bbox,
        'score': 1,
        'type': 'text',
    }
    if use_word_box or words is not None:
        if words is not None:
            span['word_result'] = words
    elif content is not None:
        span['content'] = content
    return span


class TestCalcTablePdfTextCoverage:
    def test_all_matched_returns_one(self):
        spans = [
            _span([0, 0, 10, 10], content='A'),
            _span([0, 10, 20, 20], content='B'),
        ]
        assert calc_table_pdf_text_coverage(spans, use_word_box=False) == pytest.approx(1.0)

    def test_none_matched_returns_zero(self):
        spans = [_span([0, 0, 10, 10]), _span([0, 10, 30, 20])]
        assert calc_table_pdf_text_coverage(spans, use_word_box=False) == pytest.approx(0.0)

    def test_partial_coverage_by_area(self):
        # 小框匹配(面积100)，大框未匹配(面积300) → 0.25
        spans = [
            _span([0, 0, 10, 10], content='A'),
            _span([0, 10, 10, 40]),
        ]
        assert calc_table_pdf_text_coverage(spans, use_word_box=False) == pytest.approx(0.25)

    def test_empty_spans_returns_one(self):
        assert calc_table_pdf_text_coverage([], use_word_box=False) == 1.0
        assert calc_table_pdf_text_coverage([{'bbox': None}], use_word_box=False) == 1.0

    def test_degenerate_bbox_ignored(self):
        spans = [_span([5, 5, 5, 10], content='A')]
        assert calc_table_pdf_text_coverage(spans, use_word_box=False) == 1.0

    def test_word_box_matched_with_text(self):
        spans = [_span([0, 0, 10, 10], words=[('价', 1, [0, 0, 5, 5])])]
        assert calc_table_pdf_text_coverage(spans, use_word_box=True) == pytest.approx(1.0)

    def test_word_box_whitespace_only_counts_unmatched(self):
        spans = [_span([0, 0, 10, 10], words=[(' ', 1, [0, 0, 5, 5])])]
        assert calc_table_pdf_text_coverage(spans, use_word_box=True) == pytest.approx(0.0)

    def test_word_box_partial_match(self):
        spans = [
            _span([0, 0, 10, 10], words=[('鲜萃', 1, [0, 0, 5, 5])]),
            _span([0, 10, 10, 20]),
        ]
        assert calc_table_pdf_text_coverage(spans, use_word_box=True) == pytest.approx(0.5)


class TestExtractTableTextCoverageFallback:
    class _FakeTableRes(dict):
        pass

    @staticmethod
    def _patch_pipeline(monkeypatch, ocr_spans, filtered):
        import rapid_doc.backend.pipeline.analyze_utils as au
        monkeypatch.setattr(au, 'get_ocr_result_list_table', lambda *a, **k: ocr_spans)
        monkeypatch.setattr(au, 'txt_spans_extract', lambda *a, **k: None)
        monkeypatch.setattr(au, 'normalize_table_ocr_text', lambda t: t)
        return filtered

    def _run(self, monkeypatch, ocr_spans, filtered, threshold, txt_extract=None):
        table_res_dict = {
            'table_res': {'poly': [0, 0, 100, 100, 100, 100, 0, 0]},
            'table_img': None,
            'useful_list': (0, 0, 0, 0, 100, 100, 100, 100),
        }
        import rapid_doc.backend.pipeline.analyze_utils as au
        monkeypatch.setattr(au, 'get_ocr_result_list_table', lambda *a, **k: ocr_spans)
        monkeypatch.setattr(
            au, 'txt_spans_extract',
            txt_extract if txt_extract is not None else (lambda *a, **k: None),
        )
        monkeypatch.setattr(au, 'normalize_table_ocr_text', lambda t: t)
        return _extract_table_text_from_pdf(
            table_res_dict, page_dict={}, scale=1.0,
            det_res=[[[0, 0], [10, 0], [10, 10], [0, 10]]],
            useful_list=(0, 0, 0, 0, 100, 100, 100, 100),
            table_use_word_box=False,
            min_pdf_text_coverage=threshold,
        )

    def test_low_coverage_returns_empty_for_ocr_fallback(self, monkeypatch):
        # 2 个 det 框仅 1 个有文本层来源（面积占比 1/5=0.2），阈值 0.5 → 回退 OCR
        ocr_spans = [
            _span([0, 0, 10, 10], content='饮品4选1'),
            _span([0, 10, 10, 60]),
        ]
        filtered = [[[0, 0, 10, 10], '饮品4选1', 1]]
        assert self._run(monkeypatch, ocr_spans, filtered, 0.5) == []

    def test_high_coverage_keeps_pdf_text(self, monkeypatch):
        ocr_spans = [
            _span([0, 0, 10, 10], content='活动时间'),
            _span([0, 10, 10, 20], content='8月25日'),
        ]
        filtered = [[[0, 0, 10, 10], '活动时间', 1], [[0, 10, 10, 20], '8月25日', 1]]
        result = self._run(monkeypatch, ocr_spans, filtered, 0.5)
        assert result == [
            [[0, 0, 10, 10], [0, 10, 10, 20]],
            ['活动时间', '8月25日'],
            [1, 1],
        ]

    def test_zero_threshold_disables_fallback(self, monkeypatch):
        ocr_spans = [
            _span([0, 0, 10, 10], content='饮品4选1'),
            _span([0, 10, 10, 60]),
        ]
        filtered = [[[0, 0, 10, 10], '饮品4选1', 1]]
        result = self._run(monkeypatch, ocr_spans, filtered, 0.0)
        assert result == [[[0, 0, 10, 10]], ['饮品4选1'], [1]]

    def test_empty_filtered_returns_empty(self, monkeypatch):
        ocr_spans = [_span([0, 0, 10, 10])]
        assert self._run(monkeypatch, ocr_spans, [], 0.5) == []

    def test_coverage_uses_pre_mutation_snapshot(self, monkeypatch):
        # txt_spans_extract 会原地 remove 无文本来源的 span（实测 28 框删到 6 框），
        # 覆盖率必须基于删除前全量 det 框，否则位图表格 coverage 虚高漏判
        ocr_spans = [
            _span([0, 0, 10, 10], content='活动内容'),
            _span([0, 10, 10, 30]),
            _span([0, 30, 10, 60]),
        ]
        filtered = [[[0, 0, 10, 10], '活动内容', 1]]

        def mutating_extract(*a, **k):
            ocr_spans.pop()
            ocr_spans.pop()

        # 删除后仅剩 1 个有来源 span（表面 coverage=1.0），
        # 但快照 coverage=1/5=0.2 < 0.5 → 应回退 OCR
        assert self._run(monkeypatch, ocr_spans, filtered, 0.5, txt_extract=mutating_extract) == []

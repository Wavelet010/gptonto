"""Tests for the score-based HeadingDetector."""

from lightrag.edp.heading_detector import HeadingDetector


def _detect(lines: list[str]) -> list:
    return HeadingDetector.build(lines)


def test_chinese_h1():
    hds = _detect(["一、基本情况", "正文内容"])
    assert len(hds) == 1
    assert hds[0].is_heading
    assert hds[0].level == 1
    assert hds[0].text == "基本情况"
    assert hds[0].score >= 70


def test_chinese_h2_full():
    hds = _detect(["（一）雷达对抗", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 2
    assert hds[0].text == "雷达对抗"


def test_chinese_h2_half():
    hds = _detect(["(1) X波段", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 2
    assert hds[0].text == "X波段"


def test_chapter():
    hds = _detect(["第一章 概述", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 1
    assert hds[0].text == "概述"


def test_section():
    hds = _detect(["第3节 定义", "正文"])
    assert len(hds) == 1
    assert hds[0].is_heading


def test_article():
    hds = _detect(["第二条 适用范围", "正文"])
    assert len(hds) == 1
    assert hds[0].is_heading


def test_decimal_h1():
    hds = _detect(["1. 概述", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 2
    assert hds[0].text == "概述"


def test_decimal_h2():
    hds = _detect(["1.1 模块设计", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 3
    assert hds[0].text == "模块设计"


def test_decimal_h3():
    hds = _detect(["1.1.1 参数配置", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 4


def test_markdown_h1():
    hds = _detect(["# OLED报告", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 1
    assert hds[0].text == "OLED报告"


def test_markdown_h2():
    hds = _detect(["## 方法", "正文"])
    assert len(hds) == 1
    assert hds[0].level == 2


def test_paragraph_label_rejected():
    hds = _detect(["第一段（活动规律）：本周情况..."])
    assert len(hds) == 0


def test_paragraph_part_rejected():
    hds = _detect(["第二部分 分析"])
    assert len(hds) == 0


def test_figure_caption_rejected():
    hds = _detect(["图1 电磁频谱分布"])
    assert len(hds) == 0


def test_table_caption_rejected():
    hds = _detect(["表2 信号统计"])
    assert len(hds) == 0


def test_colon_end_rejected():
    hds = _detect(["活动规律："])
    assert len(hds) == 0


def test_normal_text_rejected():
    hds = _detect(["美海军罗斯福号航母打击群进入东海活动"])
    assert len(hds) == 0


def test_equipment_not_heading():
    hds = _detect(["P-8A反潜巡逻机在钓鱼岛以西活动"])
    assert len(hds) == 0


def test_long_line_rejected():
    hds = _detect(["这是一个非常长的行" * 20])
    assert len(hds) == 0


def test_mixed_document():
    content = [
        "# OLED研究报告",
        "",
        "## 材料特性",
        "GD1265是一种新型OLED发光材料。",
        "",
        "一、基本情况",
        "美海军罗斯福号航母进入东海。",
        "",
        "（一）雷达对抗",
        "本周截获记录显示美方活动显著增加。",
        "",
        "第一段（活动规律）：美方呈现前高后低特点。",
        "",
        "图1 电磁频谱分布图",
    ]
    hds = _detect(content)
    titles = [h.text for h in hds]
    assert "OLED研究报告" in titles
    assert "材料特性" in titles
    assert "基本情况" in titles
    assert "雷达对抗" in titles
    # 第一段 should NOT be a heading
    assert "第一段（活动规律）：美方呈现前高后低特点。" not in titles
    # 图1 should NOT be a heading
    assert all("图1" not in h.text for h in hds)

"""框线表的宽度上限必须按手机气泡宽度取值（需求 6(c)「表格要采用专用规整表格」）。

`MAX_TABLE_DISPLAY_WIDTH = 60` 的注释写着「60 是常见移动端聊天气泡一行能容纳的
中文字符数（30 个汉字）的两倍显示宽度」。30 个汉字这个数字偏大：375pt 宽的手机上
QQ 气泡的正文区约 280pt，默认字号下一个汉字约 16pt，一行约 17–18 个汉字，
也就是 **35–37 显示列**。

差别不是学术性的。实测三张真实表格的宽度：

| 表格 | 显示宽度 | 阈值 60 | 阈值 40 |
|---|---|---|---|
| 4 列中文参数表 | 48 | 框线表 | 纵向字段 |
| 3 列长键名配置表 | 57 | 框线表 | 纵向字段 |
| 2 列长值状态表 | 52 | 框线表 | 纵向字段 |

这三张在阈值 60 下全部渲染成框线表，而它们的宽度都超过手机一行——框线会按窗口
宽度折行，折行之后竖线错位，读者分不清哪个值属于哪一列。而 `render_field_table`
的注释自己写了这个判据：「横向排不下时，纵向逐字段列出至少能保证『哪个值属于
哪一列』不丢失——而错位的框线连这一点都做不到」。

## 还有一层：Ambiguous 宽度

U+2500–257F（制表符）的 East_Asian_Width 是 **Ambiguous**：在西文字体里占 1 列，
在中日韩字体里占 2 列。`display_width` 按 1 计。边框行 100% 由制表符组成，
数据行是制表符加内容混排，因此在把 Ambiguous 当全角渲染的客户端上，
两者膨胀的幅度不同——实测同一张表的边框行从 48 变 96，数据行从 48 变 53，
对齐彻底失效。

这一层无法靠计算修正（同一段文本在不同客户端上宽度不同），但它把「窄表才画框线」
从一个美观取舍变成了正确性要求：表越窄，折行与错位的概率越低。

## 判据

阈值按手机一行的实际容量取，而不是按「两倍于 30 个汉字」。既有的窄表仍走框线表——
2 列短表、3 列中等表是最常见的形态，它们的观感一个字节都不该变。
"""

from __future__ import annotations

from kirara_ai.im import text_render
from kirara_ai.im.text_render import (
    MAX_TABLE_DISPLAY_WIDTH,
    box_table_display_width,
    display_width,
    render_table,
)

FOUR_COLUMN_CHINESE = [
    ["参数", "含义", "默认值", "何时调整"],
    ["T0", "初始温度", "1e3", "接受率标定"],
    ["alpha", "几何冷却系数", "0.98", "收敛速度权衡"],
]

LONG_KEY_CONFIG = [
    ["配置项", "说明", "默认"],
    ["send_pacing_enabled", "是否开启发送节流", "true"],
    ["send_pacing_minimum_seconds", "页间最小等待秒数", "1.0"],
]

TWO_COLUMN_LONG_VALUE = [
    ["状态", "处置"],
    ["initializing", "等上游完成 QQ 冷启动与登录"],
    ["credential_rejected", "改 Token，重试无用"],
]

NARROW_TWO_COLUMN = [["键", "值"], ["T0", "1e3"], ["alpha", "0.98"]]
NARROW_THREE_COLUMN = [["配置", "默认", "说明"], ["节流", "开", "避开风控"]]


class TestTheThresholdMatchesAPhoneLine:
    def test_the_threshold_fits_one_mobile_line(self):
        """375pt 手机的气泡正文约 280pt，默认字号下约 17–18 个汉字 = 35–37 列。"""
        assert MAX_TABLE_DISPLAY_WIDTH <= 40, (
            f"{MAX_TABLE_DISPLAY_WIDTH} 列在手机 QQ 上一行放不下，框线会折行错位"
        )

    def test_the_threshold_still_fits_a_useful_table(self):
        """收得太紧会让所有表都降级，那等于取消了框线表。"""
        assert MAX_TABLE_DISPLAY_WIDTH >= 30
        assert box_table_display_width(NARROW_THREE_COLUMN) <= MAX_TABLE_DISPLAY_WIDTH


class TestTablesTooWideForAPhoneDegrade:
    def test_a_four_column_chinese_table_degrades(self):
        rendered = "\n".join(render_table(FOUR_COLUMN_CHINESE))

        assert "┌" not in rendered, "48 列的表在手机上会折行，不该画框线"
        assert "参数：T0" in rendered

    def test_a_long_key_config_table_degrades(self):
        rendered = "\n".join(render_table(LONG_KEY_CONFIG))

        assert "┌" not in rendered
        assert "配置项：send_pacing_enabled" in rendered

    def test_a_two_column_table_with_long_values_degrades(self):
        rendered = "\n".join(render_table(TWO_COLUMN_LONG_VALUE))

        assert "┌" not in rendered
        assert "状态：initializing" in rendered

    def test_no_degraded_line_exceeds_the_threshold(self):
        """降级本身也要放得下，否则只是把一种折行换成另一种。"""
        for rows in (FOUR_COLUMN_CHINESE, LONG_KEY_CONFIG, TWO_COLUMN_LONG_VALUE):
            for line in render_table(rows):
                assert display_width(line) <= MAX_TABLE_DISPLAY_WIDTH, repr(line)


class TestNarrowTablesAreUnchanged:
    def test_a_short_two_column_table_keeps_the_box(self):
        """最常见的形态，观感一个字节都不该变。"""
        rendered = "\n".join(render_table(NARROW_TWO_COLUMN))

        assert "┌" in rendered and "└" in rendered

    def test_a_short_three_column_table_keeps_the_box(self):
        rendered = "\n".join(render_table(NARROW_THREE_COLUMN))

        assert "┌" in rendered and "└" in rendered

    def test_the_box_layout_still_aligns(self):
        lines = render_table(NARROW_THREE_COLUMN)

        assert len({display_width(line) for line in lines}) == 1


class TestEveryChannelUsesTheSameThreshold:
    def test_onebot_and_wecom_agree(self):
        """同一段回复在 QQ 与企业微信上不该一个画框线、一个走字段。"""
        from kirara_ai.plugins.im_onebot_adapter.render import render_onebot_text
        from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

        header = "| " + " | ".join(row for row in FOUR_COLUMN_CHINESE[0]) + " |"
        separator = "|" + "---|" * 4
        body = "| " + " | ".join(row for row in FOUR_COLUMN_CHINESE[1]) + " |"
        source = "\n".join([header, separator, body])

        assert ("┌" in render_onebot_text(source)) == (
            "┌" in markdown_to_plain_text(source)
        )

    def test_telegram_may_keep_wider_boxes_because_it_is_fenced(self):
        """Telegram 把表放进围栏走等宽字体，因此它的阈值可以不同——但共享实现
        目前对三家用同一个值。这条只钉住「不会因为围栏就丢内容」。"""
        header = "| " + " | ".join(FOUR_COLUMN_CHINESE[0]) + " |"
        separator = "|" + "---|" * 4
        body = "| " + " | ".join(FOUR_COLUMN_CHINESE[1]) + " |"
        rendered = text_render.convert_markdown_tables(
            "\n".join([header, separator, body]), fenced=True
        )

        for cell in FOUR_COLUMN_CHINESE[1]:
            assert cell in rendered

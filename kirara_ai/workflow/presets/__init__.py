"""随包分发的预设工作流。

这些 YAML 描述的是「进阶模板」：多模态输入、分段回复、深度思考等，
无法用 WorkflowBuilder 的 DSL 简洁表达（需要精确的端口映射与节点坐标），
因此以文件形式随 wheel 分发，由 WorkflowRegistry 在首次启动时释放到
`data/workflows/`，之后用户在 WebUI 里的修改都保存在 data 目录，不受升级影响。
"""

import os

PRESETS_DIR = os.path.dirname(os.path.abspath(__file__))

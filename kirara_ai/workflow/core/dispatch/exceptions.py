class WorkflowNotFoundException(Exception):
    """工作流未找到异常"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


import errno
import os
import tempfile

# 读取DATA_PATH环境变量，若未能找到则以当前工作目录为根文件夹存储在$PWD/data目录下。
DATA_PATH = os.path.abspath(
    os.environ.get("DATA_PATH", os.path.join(os.getcwd(), "data"))
)
# 按照规范插件应该在PLUGIN_PATH目录下存储对应的文件。
PLUGIN_PATH = os.path.join(DATA_PATH, "plugins")


def ensure_data_directories(paths) -> None:
    """创建并校验持久化目录，失败时给出可执行的说明。

    原实现是裸的 ``os.makedirs``：只读挂载、磁盘写满或路径被同名文件占用时，
    进程在导入期抛出原始的 ``PermissionError`` / ``OSError``，既不指明是哪个路径，
    也不给下一步动作；而现成的诊断信息全在 HTTP readiness 接口里——那时进程已经
    起不来，根本读不到。这里在启动阶段就把「路径 + 原因 + 处置建议」说清楚。

    创建之后还要实际探测一次写入：容器里最常见的情况是目录已经存在（被挂载进来）
    但整个卷是只读的，此时 ``makedirs`` 不会报错，直到第一次写数据库才失败。
    """
    for raw_path in paths:
        path = os.path.abspath(str(raw_path))
        if os.path.exists(path) and not os.path.isdir(path):
            raise RuntimeError(
                f"持久化目录 {path} 已被同名文件占用；"
                "请移除该文件或改用其他 DATA_PATH，使该路径可以作为目录使用。"
            )
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as error:
            hint = "请为该路径授予当前用户的写权限"
            if error.errno == errno.ENOSPC:
                hint = "所在卷已无可用空间，请清理磁盘或更换挂载卷"
            elif error.errno == errno.EROFS:
                hint = "所在卷为只读挂载，请以可写方式重新挂载"
            elif error.errno in (errno.ENOTDIR, errno.EEXIST, errno.ENOENT):
                # 三个 errno 指向同一件事：路径中的某一级不是目录。
                #
                # 平台给的码不同：Linux 在「用文件当父目录」时给 ENOTDIR，
                # Windows 给的是 **ENOENT**（它先解析整条路径，父级不是目录时
                # 报「找不到路径」）。少了 ENOENT，同一个部署错误在 Windows 上
                # 只会得到兜底的「请授予写权限」——那句建议指向一个不存在的
                # 权限问题，操作者会去改 ACL，而要做的是移走那个同名文件。
                hint = "路径中的某一级已被同名文件占用，请检查该路径"
            raise RuntimeError(
                f"无法创建持久化目录 {path}：{error.strerror or error}；{hint}。"
            ) from error

        try:
            # 只验证「能否在此目录建立文件」，随即删除；不写入任何内容。
            probe = tempfile.NamedTemporaryFile(dir=path, prefix=".kirara-write-probe-")
            probe.close()
        except OSError as error:
            hint = "请为该路径授予当前用户的写权限"
            if error.errno == errno.ENOSPC:
                hint = "所在卷已无可用空间，请清理磁盘或更换挂载卷"
            elif error.errno == errno.EROFS:
                hint = "所在卷为只读挂载，请以可写方式重新挂载"
            raise RuntimeError(
                f"持久化目录 {path} 不可写：{error.strerror or error}；{hint}。"
            ) from error


ensure_data_directories([DATA_PATH, PLUGIN_PATH])

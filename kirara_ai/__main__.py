import argparse
import os
import subprocess
import sys

from kirara_ai.entry import init_application, run_application
from kirara_ai.internal import get_and_reset_restart_flag


def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Kirara AI Chatbot Server')
    parser.add_argument('-H', '--host', help='覆盖服务监听地址')
    parser.add_argument('-p', '--port', type=int, help='覆盖服务监听端口')
    args = parser.parse_args()

    container = init_application()
    # 将参数对象直接注入容器
    container.register("cli_args", args)

    try:
        run_application(container)
    finally:
        if get_and_reset_restart_flag():
            # 重新启动程序
            # 构建命令行参数，透传所有原始参数
            cmd = [sys.executable, "-m", "kirara_ai"]
            # 从解析后的参数对象中获取参数
            if args.host:
                cmd.extend(["-H", args.host])
            if args.port:
                cmd.extend(["-p", str(args.port)])
            process = subprocess.Popen(cmd, env=os.environ, cwd=os.getcwd())
            process.wait()

if __name__ == "__main__":
    main()
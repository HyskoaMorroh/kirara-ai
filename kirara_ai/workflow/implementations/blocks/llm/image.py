import base64
from typing import Annotated, Any, Dict, Optional

import requests

from kirara_ai.im.message import ImageMessage
from kirara_ai.workflow.core.block import Block, ParamMeta
from kirara_ai.workflow.core.block.input_output import Input, Output


class SimpleStableDiffusionWebUI(Block):
    name = "simple_stable_diffusion_webui"
    description = "调用 Stable Diffusion WebUI 的 txt2img 接口生成图片，需自行部署 WebUI 并填写接口地址。"
    inputs = {
        "prompt": Input("prompt", "提示词", str, "描述想要生成的画面内容"),
        "negative_prompt": Input("negative_prompt", "负面提示词", str, "描述不希望出现的元素"),
    }
    outputs = {"image": Output("image", "图片", ImageMessage, "生成的图片")}

    def __init__(
        self,
        api_url: Annotated[
            str, ParamMeta(label="接口地址", description="Stable Diffusion WebUI 的访问地址，例如 http://127.0.0.1:7860")
        ],
        *,
        steps: Annotated[
            int, ParamMeta(label="迭代步数", description="采样迭代次数，数值越大细节越多、耗时越长")
        ] = 20,
        sampler_index: Annotated[
            str, ParamMeta(label="采样器", description="采样器名称，需与 WebUI 中可选项一致")
        ] = "Euler a",
        cfg_scale: Annotated[
            float, ParamMeta(label="提示词相关度", description="数值越大越贴合提示词，过高会牺牲画面自然度")
        ] = 7.0,
        width: Annotated[int, ParamMeta(label="图片宽度", description="生成图片的像素宽度")] = 512,
        height: Annotated[int, ParamMeta(label="图片高度", description="生成图片的像素高度")] = 512,
        ckpt_name: Annotated[
            Optional[str], ParamMeta(label="模型文件名", description="要使用的大模型文件名，留空则用 WebUI 当前模型")
        ] = None,
        clip_skip: Annotated[
            int, ParamMeta(label="CLIP 跳过层数", description="常用值为 1 或 2，二次元风格模型多用 2")
        ] = 1,
    ):
        self.api_url = api_url
        self.steps = steps
        self.sampler_index = sampler_index
        self.cfg_scale = cfg_scale
        self.width = width
        self.height = height
        self.ckpt_name = ckpt_name
        self.clip_skip = clip_skip

    def execute(self, prompt: str, negative_prompt: str) -> Dict[str, Any]:
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "steps": self.steps,
            "sampler_index": self.sampler_index,
            "cfg_scale": self.cfg_scale,
            "width": self.width,
            "height": self.height,
        }
        if self.ckpt_name:
            payload["ckpt_name"] = self.ckpt_name
        payload["clip_skip"] = self.clip_skip
        response = requests.post(url=f"{self.api_url}/sdapi/v1/txt2img", json=payload)

        if response.status_code == 200:
            r = response.json()
            # Assuming the API returns the image in base64 format
            # and it's the first image in the list
            if "images" in r and r["images"]:
                image_base64 = r["images"][0]
                image_bytes = base64.b64decode(image_base64)
                image_message = ImageMessage(
                    data=image_bytes, format="png"
                )  # 假设是 PNG 格式
                return {"image": image_message}
            else:
                raise Exception("No image data found in the response")
        else:
            raise Exception(
                f"API request failed with status code: {response.status_code}, message: {response.text}"
            )

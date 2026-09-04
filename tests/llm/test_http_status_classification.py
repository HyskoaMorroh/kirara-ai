"""4xx 不是网络错误：`requests.HTTPError` 是 `OSError` 的子类。

发现过程
------
现场日志（`docker compose logs`）里一条 Telegram 消息触发了 **72 次** LLM 尝试，
全程 1.06 秒，最后抛 `FailoverExecutionError`。但其中只有 **3 次**真的发出了 HTTP
请求——三个模型各一次，全部得到 `404 page not found`；其余 69 次是三个 Provider
的熔断器被打开之后的空转。

`404` 的含义是「这个上游没有这个路径或这个模型」。它换一家不会变、重试一次也不会变，
因此**不该**进入故障转移。而 `classify_llm_error` 把它判成了 `network`：

    if isinstance(error, (ConnectionError, OSError)):
        return ErrorCategory.NETWORK

`requests.exceptions.HTTPError` 的继承链是
`HTTPError → RequestException → OSError`，于是 `raise_for_status()` 抛出的**任何**
状态码只要没被上面那几个显式分支接住（401/403/408/429/5xx），就会落进这一句，
被判成可重试的网络错误。

为什么既有测试没发现
------------------
`tests/llm/test_resilience.py` 用的是一个自定义 `HttpError(Exception)`，它不是
`OSError` 的子类，因此永远走不到那条 `isinstance` 分支。测试覆盖了「带 status_code
的异常」，但没覆盖「真实 HTTP 客户端抛出的异常」——而生产里只有后者。

这一组测试锁住的边界
------------------
1. 用**真实的** `requests.HTTPError` 构造，而不是自定义异常类——否则测的是
   一个生产中不存在的形状。
2. 客户端错误（4xx）除 408/429 外一律不可重试。
3. 服务端错误（5xx）与真正的连接错误仍然可重试。
4. `_can_failover_to_next_model` 与 provider 间的判据保持一致：
   两层用同一个 `RETRYABLE_ERROR_CATEGORIES`，不能只修一层。
"""

from __future__ import annotations

import socket

import pytest
import requests

from kirara_ai.llm.resilience import (
    RETRYABLE_ERROR_CATEGORIES,
    ErrorCategory,
    classify_llm_error,
)


def http_error(status_code: int) -> requests.exceptions.HTTPError:
    """构造一个与生产逐字节同形的 HTTP 错误。

    生产路径是 `openai_adapter.py` 里的 `response.raise_for_status()`，
    因此这里也用真实的 `Response` 对象走同一条抛出路径。
    """
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://upstream.example/v1/chat/completions"
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as error:
        return error
    raise AssertionError(f"{status_code} 没有抛出 HTTPError")


def retryable(status_code: int) -> bool:
    return classify_llm_error(http_error(status_code)) in RETRYABLE_ERROR_CATEGORIES


class TestClientErrorsAreNotRetryable:
    @pytest.mark.parametrize("status_code", [400, 404, 405, 409, 410, 413, 415, 422])
    def test_a_client_error_does_not_trigger_failover(self, status_code: int):
        """这一条是修复的核心：坏版本在这里全部返回 True。

        后果不是「多试几次」：三个模型 × 各自的重试全部跑完，三个 Provider 的
        熔断器全被打开，然后才抛错。下一条真正合法的请求会撞上已经打开的熔断。
        """
        assert not retryable(status_code), (
            f"{status_code} 被判成可重试——换一家上游会得到同一个错误"
        )

    def test_404_is_not_classified_as_a_network_error(self):
        """`404` 判成 `network` 是根因所在，单独钉住它。"""
        category = classify_llm_error(http_error(404))

        assert category is not ErrorCategory.NETWORK
        assert category is ErrorCategory.INVALID_REQUEST

    def test_the_real_exception_is_an_oserror_subclass(self):
        """这条断言解释了缺陷为什么存在，也防止「换个基类就好了」的错误修法。

        既有测试用的是自定义 `Exception` 子类，走不到 `OSError` 那条分支——
        这正是缺陷能长期存在的原因。
        """
        assert isinstance(http_error(404), OSError)


class TestServerAndTransportErrorsStayRetryable:
    @pytest.mark.parametrize("status_code", [500, 501, 502, 503, 504])
    def test_a_server_error_still_fails_over(self, status_code: int):
        """`501 Not Implemented` 也留在这里：重试同一家没用，但**换一家**有用——
        另一家上游可能实现了这个方法。故障转移的判据是「换一家会不会变好」，
        不是「重试会不会变好」。
        """
        assert retryable(status_code), f"{status_code} 应当可重试"

    @pytest.mark.parametrize("status_code", [408, 429])
    def test_timeout_and_rate_limit_stay_retryable(self, status_code: int):
        """这两个是 4xx 里的例外：它们确实会因为重试或换家而变好。"""
        assert retryable(status_code)

    def test_a_genuine_connection_failure_is_still_a_network_error(self):
        """真正的连接失败必须仍然判成 network——修复不能把它一起判死。"""
        error = requests.exceptions.ConnectionError("connection refused")

        assert classify_llm_error(error) is ErrorCategory.NETWORK

    def test_a_socket_error_is_still_a_network_error(self):
        assert classify_llm_error(socket.gaierror("name or service not known")) is (
            ErrorCategory.NETWORK
        )

    def test_a_read_timeout_is_still_a_timeout(self):
        assert classify_llm_error(requests.exceptions.ReadTimeout("timed out")) is (
            ErrorCategory.TIMEOUT
        )


class TestBothFailoverLayersAgree:
    @pytest.mark.parametrize("status_code", [400, 404, 422])
    def test_the_agent_layer_also_refuses_to_advance_the_model_chain(
        self, status_code: int
    ):
        """模型链与 Provider 队列必须用同一个判据。

        只修 `classify_llm_error` 而 Agent 层另有一套判断时，
        「换下一个模型」这条路仍然会把 404 重放到每一个模型上。
        """
        from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor

        assert not AgentRuntimeExecutor._can_failover_to_next_model(
            http_error(status_code)
        )

    @pytest.mark.parametrize("status_code", [500, 503])
    def test_the_agent_layer_still_advances_on_server_errors(self, status_code: int):
        from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor

        assert AgentRuntimeExecutor._can_failover_to_next_model(http_error(status_code))

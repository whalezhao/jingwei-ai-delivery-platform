"""通过对话生成 midscene 用例 YAML。

和 runner 那条链路的关系：执行用例走 midscene CLI（本地子进程），而**生成**用例只是一次
普通的 chat completion —— 不需要浏览器、不需要 midscene。用的是项目上配好的同一个模型，
所以「项目用哪个模型」这件事仍然只有一个答案。

对话历史由前端保存并整段回传，后端不存会话状态。
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SystemModelConfig
from .yamlcheck import validate as validate_yaml

log = logging.getLogger("ai_case")

# 模型服务偶发主动断开（负载高、网关重连）时，在还没有吐出内容前重试
STREAM_ATTEMPTS = 3
STREAM_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _model_client(timeout: float) -> httpx.AsyncClient:
    # 不继承环境代理：Docker Desktop 会把宿主机的 http_proxy=127.0.0.1:7897 注入容器，
    # 容器里的 127.0.0.1 不是宿主机，模型请求会全部 ConnectError（实测 AI 评估因此失败）。
    # 模型地址本来就是项目里显式配置的，直连即可。
    return httpx.AsyncClient(timeout=timeout, trust_env=False)

# 步骤节点清单是从 @midscene/core 的 test-runner-nodes 导出 schema 里核对过的，
# 不是猜的 —— 提示词里写错节点名，模型就会生成跑不通的 YAML。
SYSTEM_PROMPT = """你是 UI 自动化测试用例助手。任务是通过对话，把用户的测试意图变成一份\
可直接执行的用例 YAML。

## 文档结构

```yaml
beforeEach:
  - gotoUrl: ${BASE_URL}

cases:
  - name: 用例名称
    steps:
      - aiAct:
          prompt: 用自然语言描述要做的操作
      - aiAssert: 用自然语言描述要验证的结果
```

`beforeEach` 里的 `gotoUrl: ${BASE_URL}` 由平台替换成该项目的被测地址。\
**始终原样保留这一行，不要写死任何具体 URL。**

项目还可以配置其他环境变量：普通变量用 `${KEY}` 引用，密钥用 `${{KEY}}` 引用。\
用户提到账号、密码、测试数据时，优先使用这些占位符，不要把真实值写进 YAML。

## 可用的步骤节点

操作类（**首选 `aiAct`**）：
- `aiAct: <自然语言>` —— 描述操作意图，执行器的 AI 自己规划点击、输入等动作。\
需要多步时写在一个 prompt 里说清楚。
- `aiTap` / `aiInput` / `aiKeyboardPress` / `aiScroll` / `aiDragAndDrop` / `aiLongPress` —— \
仅在用户明确要求精确动作时使用，否则一律用 `aiAct`。

断言与取值：
- `aiAssert: <自然语言>` —— 断言某状态成立，不成立则用例失败。
- `aiWaitFor: <自然语言>` —— 等待某条件出现，用于页面跳转、异步加载。
- `aiQuery: <自然语言>` —— 从页面取值。

## 缓存（每个 aiAct 都必须显式判断）

平台已开启执行缓存。缓存的正确写法是 `aiAct` 的选项，不是步骤的独立字段：
`aiAct: { prompt: ..., options: { cacheable: true } }`。写错位置（比如和 `aiAct` 平级）
会让执行器报「一步只能有一个节点」，整份用例直接跑不起来。

**每一个 `aiAct` 步骤都必须写出 `options.cacheable`（true 或 false），不许省略。**
这是硬性要求：省略等于没做判断。

判断规则：

- `true`：稳定的导航/准备步骤 —— 打开固定页面、点击固定的入口、填固定字段、
  登录这类页面结构短期不会变、每次执行都一样的操作；
- `false`：内容会变的位置（列表、推荐位、带时间的区域）、依赖上一步动态结果的交互、
  以及**这条用例第一次验证**（第一次跑先别缓存，缓存会把真实问题藏起来）。

写法示例（同一份用例里两种判断都要出现）：

```yaml
cases:
  - name: 登录后进入订单列表
    steps:
      - aiAct:
          prompt: 打开登录页
          options:
            cacheable: true
      - aiAct:
          prompt: 在邮箱和密码输入框填入测试账号并点击登录
          options:
            cacheable: true
      - aiAct:
          prompt: 点击第一条待付款订单
          options:
            cacheable: false
      - aiAssert: 订单列表显示了当前用户的订单
```

输出前逐个检查 `aiAct`：漏了 `options.cacheable` 就补上，再输出完整 YAML。

## 写作要求

1. **绝对不要出现 CSS 选择器、XPath 或元素 id。** 执行器靠视觉定位，\
要写「页面右上角的头像按钮」，不要写「#avatar」。
2. 定位必须**具体且唯一**：「登录按钮」太含糊；用「页面中央蓝色的『登录』按钮」。
3. 一个步骤只做一件事。跨页面时拆成多个 `aiAct`，中间用 `aiWaitFor` 等页面就绪。
4. 用例要形成完整闭环：从打开页面到验证结果，**最后至少有一条 `aiAssert`**。
5. 用例名称与断言描述用中文。

## 测试数据

**你不知道被测应用的真实账号、真实数据，也不知道它有哪些输入校验。**
所以需要具体测试数据（账号密码、搜索关键词、表单内容）而用户又没给时，
**先问清楚再写 YAML**，不要自己编一个「看起来像真的」的值 —— 实测踩过一次：
模型编了个 `testuser` 当邮箱，被测表单是 `type="email"`，浏览器原生校验直接拦下提交，
用例失败在一个和测试意图完全无关的地方。

用户明确说「随便填」「用占位值」时，才使用形如 `【请替换为真实测试账号】` 的占位符，
并在回复里提醒替换。

## 回复格式

给出或修改 YAML 时，YAML 之外还要用 **Markdown** 写一段结构化说明（YAML 放进
```yaml 代码块，平台会把它提取到右侧，气泡里只显示这段说明）：

新建用例：

**用例：<用例名称>**

- 覆盖流程：打开…→ 输入…→ 断言…（每步不超过 12 字，用 → 串起来）
- 关键断言：1~3 条，每条一行
- 测试数据：账号/关键词等；没有特殊数据就写「无」
- 说明：只在涉及缓存、占位符替换、需要用户确认时写一行

修改已有用例：

**已更新：<一句话说清这次改了哪一块>**

- 改动点：具体改了什么
- 影响：断言 / 步骤 / 数据哪一部分受影响

要求：每条列表项一行、不超过 24 字；不要长篇解释；标题和列表之外不要加寒暄。

## 交互方式

- 用户描述要测什么，你就把 YAML 写出来。
- 用户要求修改时，输出**完整的**新 YAML（不是 diff、不是片段）。
- **回复里不要提执行器、框架、库的名字**（平台不对外露出用的是哪个），就说「执行器」「用例 YAML」。
- **用户要求清空或重新开始时，输出一个内容为空的 yaml 代码块**（即 ```yaml 和 ``` 之间
  什么都没有），不要只用文字说「已清空」—— 平台靠代码块判断右侧内容要不要跟着变。

**要给出或修改 YAML 时，必须把完整 YAML 放在一个 ```yaml 代码块里。**\
这是平台提取用例的唯一依据。"""

YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)


@dataclass
class DraftResult:
    reply: str
    yaml_text: str | None
    yaml_ok: bool
    yaml_errors: list[str]
    usage: dict


def extract_yaml(reply: str) -> str | None:
    """从回复里取 YAML。

    返回值的三种含义，前端据此决定右侧怎么动：
    - `None` —— 这轮回复里没有 YAML（模型只在回答问题），右侧保持不动
    - `""`   —— 模型给了一个**空**的 yaml 代码块，表示它要清空，右侧清空
    - 其他   —— 新的 YAML 内容

    取最后一个代码块：模型可能先贴旧版本再给新版本。

    兜底：偶尔模型会直接吐裸 YAML 而不加围栏。整段回复只要像 YAML 文档就认，
    否则用户会看到「明明给了用例却提示没提取到」。
    """
    blocks = YAML_BLOCK.findall(reply)
    if blocks:
        content = blocks[-1].strip()
        return f"{content}\n" if content else ""

    stripped = reply.strip()
    if stripped.startswith(("beforeEach:", "cases:", "beforeAll:")) and "cases:" in stripped:
        return stripped + "\n"
    return None


def strip_yaml_blocks(reply: str, *, cleared: bool = False) -> str:
    """对话气泡里不重复贴 YAML —— 右侧已经完整展示了。

    模型只回了代码块、没有别的文字时，给一句符合实情的兜底文案：
    清空和更新是两件事，说成「已更新」会让人以为右侧有内容。
    """
    text = YAML_BLOCK.sub("", reply).strip()
    if text:
        return text
    return "已清空右侧的 YAML。" if cleared else "已更新右侧的 YAML。"


_ACTION_NODES = {
    "aiAct",
    "aiTap",
    "aiInput",
    "aiKeyboardPress",
    "aiScroll",
    "aiDragAndDrop",
    "aiLongPress",
}
_ASSERT_NODES = {"aiAssert"}


def _node_text(step: dict, node: str) -> str:
    value = step.get(node)
    if isinstance(value, dict):
        value = value.get("prompt")
    return str(value).strip() if value else ""


def summarize_yaml(yaml_text: str) -> str:
    """把 YAML 翻成一段结构化的中文摘要（Markdown）。

    为什么不靠模型自己写说明：实测模型经常只吐 YAML 代码块，气泡里就只剩一句
    「已更新右侧的 YAML」。摘要由平台从 YAML 现算 —— 不管模型怎么回，用户都能
    看到「用例名 / 覆盖流程 / 关键断言 / 规模」四段。
    """
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return ""
    if not isinstance(doc, dict):
        return ""
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        return ""

    def summarize_case(case: dict) -> tuple[str, list[str], list[str], int, int, int]:
        actions: list[str] = []
        asserts: list[str] = []
        cached = 0
        steps = case.get("steps") if isinstance(case, dict) else None
        steps = steps if isinstance(steps, list) else []
        for step in steps:
            if not isinstance(step, dict):
                continue
            for node in _ACTION_NODES:
                if node not in step:
                    continue
                text = _node_text(step, node)
                if text:
                    actions.append(text)
                options = step.get(node)
                if isinstance(options, dict) and (options.get("options") or {}).get("cacheable"):
                    cached += 1
            for node in _ASSERT_NODES:
                if node in step and (text := _node_text(step, node)):
                    asserts.append(text)
        name = str((case or {}).get("name") or "未命名用例").strip()
        return name, actions, asserts, len(steps), cached, sum(
            1 for step in steps if isinstance(step, dict) and "aiWaitFor" in step
        )

    lines: list[str] = []
    if len(cases) == 1:
        name, actions, asserts, count, cached, waits = summarize_case(cases[0] or {})
        lines.append(f"**用例：{name}**")
        lines.append("")
        if actions:
            lines.append(f"- 覆盖流程：{' → '.join(actions)}")
        else:
            lines.append("- 覆盖流程：未包含操作步骤")
        if asserts:
            lines.append("- 关键断言：")
            lines.extend(f"  - {item}" for item in asserts)
        else:
            lines.append("- 关键断言：无（建议补一条 aiAssert）")
        scale = f"- 规模：{count} 步"
        if waits:
            scale += f" · {waits} 个等待"
        if cached:
            scale += f" · {cached} 步启用缓存"
        lines.append(scale)
    else:
        lines.append(f"**本次生成 {len(cases)} 条用例**")
        lines.append("")
        for case in cases:
            name, _actions, asserts, count, cached, _waits = summarize_case(case or {})
            extra = f"，{cached} 步缓存" if cached else ""
            lines.append(f"- {name}：{count} 步 · {len(asserts)} 条断言{extra}")
    return "\n".join(lines)


def build_reply(raw_reply: str, yaml_text: str | None) -> str:
    """气泡里的最终文案：平台摘要打底，模型自己的补充说明接在后面。"""
    prose = strip_yaml_blocks(raw_reply, cleared=yaml_text == "")
    summary = summarize_yaml(yaml_text) if yaml_text else ""
    if not summary:
        return prose
    # 模型如果照格式写了摘要，会和平台摘要重复；只保留它的额外说明
    duplicate = prose in {"已更新右侧的 YAML。", "已清空右侧的 YAML。"} or prose.startswith(
        ("**用例", "**已更新")
    )
    return summary if duplicate or not prose else f"{summary}\n\n---\n\n{prose}"


def build_messages(
    history: list[dict],
    *,
    current_yaml: str | None,
    feature_name: str | None,
) -> list[dict]:
    system = SYSTEM_PROMPT
    if feature_name:
        system += f"\n\n## 当前上下文\n\n这个用例属于功能「{feature_name}」。"
    if current_yaml:
        system += (
            "\n\n## 当前的 YAML\n\n"
            "用户正在迭代下面这份用例。要求修改时，在它的基础上改并输出完整的 YAML：\n\n"
            f"```yaml\n{current_yaml.strip()}\n```"
        )
    return [{"role": "system", "content": system}, *history]


async def call_model(
    config: SystemModelConfig,
    messages: list[dict],
    *,
    timeout: float = 180.0,
    max_tokens: int = 4096,
) -> tuple[str, dict]:
    """调系统配置的模型。走 OpenAI 兼容的 chat/completions。"""
    base = (config.base_url or "").rstrip("/")
    if not base:
        raise ValueError("系统没有配置模型接入地址")

    payload: dict = {
        "model": config.model_name,
        "messages": messages,
        # 生成用例要的是稳定而不是创意
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    # 推理模型默认会把 token 大量花在思考上，甚至把 max_tokens 烧光、content 返回 null。
    # 实测 chat_template_kwargs 能彻底关掉；但换到别的服务端可能不认这个字段，
    # 所以 400 时去掉它重试一次。
    if config.reasoning_enabled is False:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    headers = {
        "Authorization": f"Bearer {config.api_key or ''}",
        "Content-Type": "application/json",
    }
    url = f"{base}/chat/completions"

    async with _model_client(timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code == 400 and "chat_template_kwargs" in payload:
            payload.pop("chat_template_kwargs")
            response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    message = (data.get("choices") or [{}])[0].get("message") or {}
    # 有些推理模型把正文放在 content，思考放在 reasoning；content 为空时退回 reasoning
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning") or "").strip()
    return content, data.get("usage") or {}


_CACHE_REVIEW = (
    "请重新检查这份 YAML 的缓存策略：逐个 aiAct 判断是否需要缓存，并把每个 aiAct 都写成"
    "带 options.cacheable（true 或 false）的形式 —— 稳定的导航/准备步骤用 true，"
    "内容会变或依赖动态结果的交互用 false。只调整缓存选项，不要改其他内容；"
    "输出完整 YAML（放在 ```yaml 代码块里）。"
)


def missing_cache_steps(yaml_text: str) -> int:
    """没有显式给出 cacheable 判断的 aiAct 步骤数。

    省略和「判断为不缓存」是两件事：省略等于模型没考虑过。这个数字用来决定
    要不要让模型再按规则补一轮。
    """
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return 0
    if not isinstance(doc, dict):
        return 0
    missing = 0
    for case in doc.get("cases") or []:
        if not isinstance(case, dict):
            continue
        for step in case.get("steps") or []:
            if not isinstance(step, dict) or "aiAct" not in step:
                continue
            value = step.get("aiAct")
            options = value.get("options") if isinstance(value, dict) else None
            if not isinstance(options, dict) or "cacheable" not in options:
                missing += 1
    return missing


def _merge_usage(first: dict, second: dict) -> dict:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return first or second
    merged = dict(first)
    for key, value in second.items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] = merged[key] + value
        else:
            merged.setdefault(key, value)
    return merged


async def _cache_review(
    config: SystemModelConfig,
    messages: list[dict],
    reply: str,
    yaml_text: str,
    usage: dict,
) -> tuple[str, str, dict]:
    """模型没逐个 aiAct 给缓存判断时，让它按规则补一轮。

    只在这种情况多花一次调用；返回可能更新后的 (reply, yaml, usage)。
    """
    missing = missing_cache_steps(yaml_text)
    if not missing:
        return reply, yaml_text, usage
    reviewed, reviewed_usage = await call_model(
        config,
        [
            *messages,
            {"role": "assistant", "content": reply},
            {"role": "user", "content": _CACHE_REVIEW},
        ],
    )
    candidate = extract_yaml(reviewed)
    if candidate:
        candidate_errors, _ = validate_yaml(candidate)
        if not candidate_errors and missing_cache_steps(candidate) < missing:
            return reviewed, candidate, _merge_usage(usage, reviewed_usage)
    return reply, yaml_text, usage


async def stream_model(
    config: SystemModelConfig,
    messages: list[dict],
    *,
    timeout: float = 180.0,
    usage_out: dict | None = None,
) -> AsyncIterator[str]:
    """流式调模型：逐个 content 片段 yield。OpenAI 兼容的 SSE 格式。

    主流服务在最后一个 chunk 里带 usage（需要 stream_options.include_usage）；
    拿到就写进 usage_out，调用方据此记 token 流水。
    """
    base = (config.base_url or "").rstrip("/")
    if not base:
        raise ValueError("系统没有配置模型接入地址")

    payload: dict = {
        "model": config.model_name,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 4096,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if config.reasoning_enabled is False:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    headers = {
        "Authorization": f"Bearer {config.api_key or ''}",
        "Content-Type": "application/json",
    }
    url = f"{base}/chat/completions"

    async with _model_client(timeout) as client:
        for attempt in range(STREAM_ATTEMPTS):
            yielded = False
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code == 400:
                        # 有些服务端不认这两个扩展字段；逐个去掉再试
                        for key in ("stream_options", "chat_template_kwargs"):
                            if key in payload:
                                payload.pop(key, None)
                                break
                        else:
                            response.raise_for_status()
                        if attempt < STREAM_ATTEMPTS - 1:
                            continue
                        response.raise_for_status()

                    if (
                        response.status_code in STREAM_RETRY_STATUSES
                        and attempt < STREAM_ATTEMPTS - 1
                    ):
                        log.warning(
                            "模型流式请求返回 %s，第 %d 次重试",
                            response.status_code,
                            attempt + 1,
                        )
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue

                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if usage_out is not None and isinstance(obj.get("usage"), dict):
                            usage_out.update(obj["usage"])
                        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yielded = True
                            yield piece
                    return
            except httpx.TransportError as exc:
                # 已经吐过字就不能重试，否则前端会看到重复内容；只把错误交给上层
                if yielded or attempt >= STREAM_ATTEMPTS - 1:
                    raise
                log.warning(
                    "模型流式连接中断，第 %d 次重试：%s", attempt + 1, exc
                )
                await asyncio.sleep(1.5 * (attempt + 1))

    raise RuntimeError("模型流式请求重试耗尽")


def _friendly_stream_error(exc: Exception) -> str:
    """把 httpx 的英文底层错误转成用户能理解的一句话。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "模型服务繁忙（429），请稍后重试"
        if status >= 500:
            return "模型服务暂时不可用，请稍后重试"
        return f"模型服务返回 {status}，请稍后重试"
    if isinstance(exc, httpx.TimeoutException):
        return "模型响应超时，请稍后重试"
    if isinstance(exc, httpx.TransportError):
        return "模型服务连接中断，请稍后重试"
    return "生成失败，请稍后重试"


async def draft_stream(
    config: SystemModelConfig,
    *,
    history: list[dict],
    current_yaml: str | None,
    feature_name: str | None,
) -> AsyncIterator[dict]:
    """流式生成：先发 delta，最后发一次 done（结构同 CaseDraftOut）。

    出错时发 error 事件而不是断开连接 —— 前端能把原因显示在气泡里。
    """
    messages = build_messages(history, current_yaml=current_yaml, feature_name=feature_name)
    parts: list[str] = []
    usage: dict = {}
    try:
        async for piece in stream_model(config, messages, usage_out=usage):
            parts.append(piece)
            yield {"event": "delta", "data": {"text": piece}}
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        if isinstance(exc, ValueError):
            message = str(exc)
        else:
            # 服务端日志保留真实原因，界面只给一句人话
            log.warning("case draft stream failed: %s", exc, exc_info=True)
            message = _friendly_stream_error(exc)
        yield {"event": "error", "data": {"message": message}}
        return

    reply = "".join(parts)
    yaml_text = extract_yaml(reply)
    errors: list[str] = []
    if yaml_text:
        errors, _ = validate_yaml(yaml_text)

    if yaml_text and not errors:
        try:
            reply, yaml_text, usage = await _cache_review(
                config, messages, reply, yaml_text, usage
            )
        except httpx.HTTPError:
            # 补缓存这一步失败不影响已生成的内容
            pass

    yield {
        "event": "done",
        "data": {
            "reply": build_reply(reply, yaml_text),
            "yaml": yaml_text,
            "yaml_ok": bool(yaml_text) and not errors,
            "yaml_errors": errors,
            "usage": usage,
        },
    }


async def draft(
    db: AsyncSession,
    config: SystemModelConfig,
    *,
    history: list[dict],
    current_yaml: str | None,
    feature_name: str | None,
) -> DraftResult:
    messages = build_messages(
        history, current_yaml=current_yaml, feature_name=feature_name
    )
    reply, usage = await call_model(config, messages)

    yaml_text = extract_yaml(reply)
    errors: list[str] = []
    # 空字符串表示模型要清空，不是「给了个非法文档」，不该报校验错
    if yaml_text:
        errors, _ = validate_yaml(yaml_text)

    # 缓存策略必须由模型自己逐个 aiAct 判断；偷懒省略时补一轮（见 _cache_review）
    if yaml_text and not errors:
        reply, yaml_text, usage = await _cache_review(
            config, messages, reply, yaml_text, usage
        )

    return DraftResult(
        reply=build_reply(reply, yaml_text),
        yaml_text=yaml_text,
        yaml_ok=bool(yaml_text) and not errors,
        yaml_errors=errors,
        usage=usage,
    )


def _self_check() -> None:
    """python -m app.ai_case —— 提取逻辑有分支，留个能跑的检查。"""
    fenced = "好的：\n```yaml\nbeforeEach:\n  - gotoUrl: ${BASE_URL}\ncases:\n  - name: a\n```\n"
    assert extract_yaml(fenced) == "beforeEach:\n  - gotoUrl: ${BASE_URL}\ncases:\n  - name: a\n"
    # 模型先贴旧版再给新版，取最后一个
    two = "旧版：\n```yaml\ncases:\n  - name: old\n```\n新版：\n```yaml\ncases:\n  - name: new\n```\n"
    assert "new" in (extract_yaml(two) or "")
    # 裸 YAML 兜底
    bare = "beforeEach:\n  - gotoUrl: x\ncases:\n  - name: bare\n"
    assert extract_yaml(bare) == bare
    # 纯文字回复不该被误认成 YAML
    assert extract_yaml("请再补充一下你想验证什么。") is None
    # 空的 yaml 代码块 = 模型要清空，返回 "" 而不是 None（None 表示右侧别动）
    assert extract_yaml("已清空。\n```yaml\n```\n") == ""
    assert extract_yaml("已清空。\n```yaml\n   \n```\n") == ""
    # 对话气泡不重复贴 YAML
    assert "```" not in strip_yaml_blocks(fenced)
    assert strip_yaml_blocks("只有代码块：\n```yaml\ncases: []\n```") == "只有代码块："
    # 只回了空代码块时，兜底文案要说「清空」而不是「更新」
    assert strip_yaml_blocks("```yaml\n```", cleared=True) == "已清空右侧的 YAML。"
    # 结构化摘要：用例名 / 流程 / 断言 / 规模都从 YAML 里现算
    sample = (
        "beforeEach:\n  - gotoUrl: ${BASE_URL}\n"
        "cases:\n  - name: 搜索空结果\n    steps:\n"
        "      - aiAct: 在顶部搜索框输入 zzz\n"
        "      - aiAct:\n          prompt: 按回车\n          options:\n            cacheable: true\n"
        "      - aiAssert: 出现空结果提示\n"
    )
    summary = summarize_yaml(sample)
    assert "用例：搜索空结果" in summary
    assert "覆盖流程：在顶部搜索框输入 zzz → 按回车" in summary
    assert "出现空结果提示" in summary
    assert "1 步启用缓存" in summary
    # 缓存判断：省略和显式 false 是两回事
    assert missing_cache_steps(sample) == 1
    annotated = sample.replace(
        "- aiAct: 在顶部搜索框输入 zzz",
        "- aiAct: { prompt: 在顶部搜索框输入 zzz, options: { cacheable: false } }",
    )
    assert missing_cache_steps(annotated) == 0
    assert missing_cache_steps("cases:\n  - name: a\n    steps:\n      - aiAssert: 通过\n") == 0
    # 模型只吐了 YAML 时，气泡也必须给出结构化摘要，而不是「已更新」
    assert build_reply("```yaml\n" + sample + "```", sample).startswith("**用例：搜索空结果**")
    # 模型额外写了说明，就接在摘要后面
    mixed = build_reply("另外把登录按钮的定位写得更具体了。\n```yaml\n" + sample + "```", sample)
    assert "另外把登录按钮" in mixed and mixed.startswith("**用例：")
    print("ai_case 自检通过")


if __name__ == "__main__":
    _self_check()

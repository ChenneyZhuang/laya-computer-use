# laya-computer-use

**本地的、开源的 macOS 电脑操作（computer use）。** 用 [Laya](https://github.com/NandhaKishorM/laya)
——开源的 System One 决策模型——读真实窗口的辅助功能树（AX 树），决定每一步动作。
不用截图、不用视觉模型、不用坐标、不联网。

```python
from laya_computer_use import CuaDriver, DesktopLoop

driver = CuaDriver(pid=1234, window_id=5678)
run = DesktopLoop().run(driver, "打开显示设置")
print(run.summary())
```

这是 [TypeSafe Jev](https://docs.typesafe.ai) 在"桌面控制"这个场景的本地方案：同样的
类型化决策思路，完全跑在你自己的机器上，每步零成本。

**English** | 中文  →  [English](README.md)

---

## 为什么用决策模型而不是视觉模型

大多数 computer-use agent 的循环是*截图 → 问多模态大模型 → 点坐标*。这个循环贵、慢，
而且坐标是小模型最不可靠的产出。本项目走另一条路，就是屏幕阅读器走的那条：

1. **观察** —— 遍历 macOS 辅助功能树：角色、标签、值、启用/选中状态，以及每个元素支持哪些动作。
2. **决策** —— 把一张编号控件表和一个目标交给模型。它一次前向就回答：执行哪个操作
   （`CLICK`/`TYPE_TEXT`/`SELECT`/`SCROLL`/`WAIT`/`DONE`），以及操作哪个元素索引。
3. **执行** —— 你的代码把索引解析成真实的 AX 元素并执行。

**模型看不到坐标，也不产出动作。** 它只能从观察到的选项里挑，所以答案错也只是"选错"，
不会是"编造"。

| | 截图 + 视觉大模型 | AX 树 + 决策模型（本项目）|
|---|---|---|
| 每步 | 一次多模态请求 | 一次本地前向（见下面实测） |
| 成本 | 按 token 计费 | **$0** |
| 数据 | 屏幕像素出机器 | **什么都不出机器** |
| 目标定位 | 从像素猜 | **精确**——元素来自树 |
| 失效场景 | 小字、密集工具栏 | 无 AX 节点的自绘界面 |

---

## 复用了什么，新写了什么

本项目刻意做得很薄。决策循环——守卫、置信度门、历史追踪、目标感知的作用域裁剪、
元素表契约、`/v1/systemone` HTTP 层、模型后端——全部**从
[laya-browser-agent](https://github.com/ChenneyZhuang/laya-browser-agent) 导入**（本项目的
浏览器版兄弟项目），不是重新实现。桌面版单独 fork 一份会逐渐漂移，然后两个项目对
"什么是安全的一步"产生不同理解。

| 复用自 `laya-browser-agent` | 本项目新增 |
|---|---|
| `page.py` —— 元素表、问题构造、操作词表 | `ax.py` —— AX 树 → 元素表 |
| `loop.py` —— 观察/决策/执行循环、开关守卫、置信度门、循环守卫 | `driver.py` —— `CuaDriver`（AX 按压/输入/设值/滚动）|
| `scope.py` —— 目标感知的元素排序与裁剪 | `mcp_server.py` —— stdio 上的 MCP 工具 |
| `decider.py` + 后端 —— 校验过的决策、MLX/PyTorch/HTTP | `cli.py` —— `lcu` 命令 |
| `grounding.py` —— 跨文字系统的候选过滤 | `models.py` —— checkpoint 注册表 |
| | `benchmarks/` —— 本仓库的 AX 测试组 |

---

## 实测结果

下面每个数字都来自 `benchmarks/` 里已提交的脚本，在一台 M4 Mac（16GB）上跑出。
决策层用的是 [`convaiinnovations/laya`](https://huggingface.co/convaiinnovations/laya)
发布的**官方 Laya checkpoint**（英文版、多语言版、typed-decisions 版三个都测了），
MLX 后端。本项目不重新分发权重。

**先把丑话说在前面：** 官方 checkpoint 在它们被训练的任务上回答得很好，但**零样本做不了
"从桌面 AX 树里选控件"**——它们会直接答 `DONE`/`WAIT`，根本不与选项互动。本仓库就是
测量这个差距的测试台，也是未来微调后的 checkpoint 直接接入的地方（见[路线图](#路线图)）。

### checkpoint 本身是健康的（对照组）

在把任何结论读进上面的差距之前，先跑模型卡自己的例子，走完全相同的后端路径。
如果 checkpoint 是坏的，这里就会暴露：

```
python3 -m benchmarks.quickstart_sanity

english        department=billing (p=0.926) urgency=1.772 churn=0.88   [874 ms]
multilingual   department=billing (p=1.0)   urgency=1.636 churn=0.008  [1156 ms]
```

两个模型在邮件分类（它们的设计任务）上都答得又对又自信。所以下面的差距是
**任务形态的差距**，不是下载坏了。

### 控件选择 —— 12 道题，真实 + 合成

测试组在真实 Calculator AX 树和两个合成面板上问 12 个 harness 形态的决策：
操作（`CLICK`/`TYPE_TEXT`）+ 哪个元素索引。

| Checkpoint | 总分 | 英文目标+中文界面 | 中文目标+中文界面 | 英文标签（合成）| 加载 |
|---|---|---|---|---|---|
| laya-en（官方） | 0/12 | 0/4 | 0/4 | 0/4 | 0.8 s |
| laya-multilingual（官方） | 0/12 | 0/4 | 0/4 | 0/4 | 1.0 s |
| laya-typed-decisions（官方） | 0/12 | 0/4 | 0/4 | 0/4 | 0.6 s |

`python3 -m benchmarks.checkpoint_battery` 可复现。失败方式很一致：`laya-en` 答 `DONE`，
`laya-multilingual` 答 `WAIT`——不管目标是什么、标签是什么、给了哪些选项。

### 四个公平性对照 —— 不是问法的问题

一个 0 分值得怀疑，所以每个合理的解释都测了：

1. **选项数量**（`benchmarks/option_scaling.py`）：同一个目标固定，选项数从
   1 → 2 → 3 → 5 → 8 → 12 → 16 → 22 递增。答案一次都没变
   （`laya-en` 永远 `DONE` p=0.417；`laya-multilingual` 永远 `WAIT` p=0.792）。
   不是"选项太多看不过来"——是根本没在看。
2. **状态布局**（`benchmarks/format_fairness.py`）：元素表两种给法都试了——
   `v3`（表只在选项里）和 `v1`（表也放进 state）。`laya-en`：0/8 和 1/8
   （唯一命中是"清空"蒙对一次）。不是布局问题。
3. **问法**（`benchmarks/direct_question_probe.py`）：同一个问题四种问法——
   harness 的结构化指令、大白话句子、只给目标文本、以及最简单的形态
   （"要清空显示应该按哪个按钮？"）。四种全错。
4. **位置 vs 标签**（`benchmarks/position_control.py`）：选项顺序打乱
   （5 个固定种子 × 6 个目标），区分"读标签"还是"背了槽位"。
   结果：**30 次试验里 0 次选出了任何控件**——在模型开始参与元素选择之前，
   没有东西可供"稳定"。

### AX 桥接是通的 —— 端到端验证过

适配层、执行器、结果验证都在真实应用上跑过（TextEdit，真实 AX 树，真实可编辑文档体）：

```
python3 -m benchmarks.e2e_loop <pid> <window_id>      # 决策循环，官方 checkpoint
document before: 'test fixture'
  1. TYPE_TEXT  test fixture    p=0.31 executed=True   （文本真的输入了）
  ...
  6. TYPE_TEXT  ...             p=0.41 executed=False  （循环守卫：页面无变化）
document after:  'LOOP-…LOOP-…test fixture'
bridge: PASS (text landed: True) | autonomous: NO (stopped='error', steps=6)

python3 -m benchmarks.e2e_textedit <pid> <window_id>  # 只测 AX 桥，无模型
VERDICT: PASS — text landed in the document
```

两个诚实的判定，因为它们分开失效：**桥接**（观察 → 决策 → 执行 → 在文档里验证效果）
通过；**自主停止**（模型自己决定 `DONE`）不通过——官方 checkpoint 不停止，会重复输入，
是循环守卫结束了运行。守卫发挥作用是设计的一部分；缺的自主停止是差距的一部分。

### 延迟

| 阶段 | 实测 |
|---|---|
| AX 遍历，实时窗口 | **约 110 ms** 中位数（169 个原始节点里 22 个可操作；142 个原始节点是菜单栏 chrome） |
| 一次决策，官方 checkpoint，≤20 个选项 | **约 110 ms** |
| 一次决策，40 个选项 | **约 230 ms** |
| 每步合计（观察 + 决策） | 约 220 ms，**$0** |

用 `benchmarks/latency_scaling.py` 和 `benchmarks/ax_walk.py` 复现。20 个选项内的决策
约等于一次 AX 遍历的成本——这个平衡正是设计把表限制在 40 个元素、优先排序而不是扩容的原因。

---

## 安装

```bash
git clone https://github.com/ChenneyZhuang/laya-computer-use
cd laya-computer-use
python3 -m venv .venv && source .venv/bin/activate

pip install -e ".[mlx]"        # Apple Silicon（最快）
# pip install -e ".[torch]"    # 任何有 PyTorch 的平台

pip install cua-driver          # 来自 github.com/trycua/cua —— AX 读取与执行驱动
cua-driver permissions grant    # 一次性：授权辅助功能 + 屏幕录制
lcu doctor                      # 验证整条链路
```

`laya-computer-use` 依赖 [`laya-browser-agent`](https://github.com/ChenneyZhuang/laya-browser-agent)
提供决策循环。在该包上 PyPI 之前，先从仓库装：

```bash
pip install "git+https://github.com/ChenneyZhuang/laya-browser-agent.git"
```

每个 checkpoint 第一次决策时会自动下载（约 650–850 MB）。

### 环境要求

- macOS 13+（辅助功能 API 与 `cua-driver`）
- Python 3.10+
- Apple Silicon 走 MLX 后端；PyTorch 后端到处都能跑
- checkpoint 常驻约 1GB 内存（一机一模型——见下）

---

## 使用

### Checkpoint

决策层跑**官方 Laya checkpoint**；可以按名字、hub id 或本地路径指定：

| --model | checkpoint | 骨干 | 大小 | 上下文 |
|---|---|---|---|---|
| `english` *（默认）* | `convaiinnovations/laya` | ModernBERT-large | 421M | 512 |
| `multilingual` | `convaiinnovations/laya` 子目录 | mmBERT-base | 322M | 1024，最高 8192 |
| `typed-decisions` | `convaiinnovations/laya` 子目录 | ModernBERT-large | 421M | 1024 |
| 任意 hub id | 如 `org/repo` | — | — | — |
| 任意本地目录 | 为*本任务*训练的 checkpoint | — | — | — |

为桌面控制微调过的 checkpoint 无需改代码即可接入：

```bash
lcu decide 1234 5678 "清空显示" --model /path/to/my-desktop-checkpoint
```

### 命令行

```bash
lcu windows                              # 找到 pid + window_id
lcu observe 1234 5678                    # 模型看到的表
lcu decide  1234 5678 "打开显示设置"       # 试运行：它会怎么做？
lcu run     1234 5678 "打开显示设置"       # 完整循环，真的执行
lcu decide  1234 5678 "打开显示设置" --model multilingual
```

### Python

```python
from laya_computer_use import CuaDriver, DesktopLoop
from laya_computer_use.models import resolve as resolve_model

driver = CuaDriver(pid=1234, window_id=5678, max_elements=40)
hub, sub = resolve_model("english")            # 或指向你自己的 checkpoint 路径
loop = DesktopLoop(
    model=hub, subfolder=sub,                  # 官方 Laya checkpoint
    # decider=Decider(model="/path/to/checkpoint"),  # 或自带 decider
    max_steps=15,
    text_provider=lambda goal, element: "Sydney",   # 文本模型、查表、正则都行
    confirm=lambda label, element: input(f"{label}? [y/N] ").lower() == "y",
)
run = loop.run(driver, "把目的地字段填成城市名")
print(run.summary())
```

### MCP（Claude Desktop、Cursor、任何 MCP 客户端）

```json
{
  "mcpServers": {
    "laya-computer-use": { "command": "lcu-mcp" }
  }
}
```

工具：`list_windows`、`desktop_observe`、`desktop_decide`（**默认试运行**——agent 必须
显式传 `execute: true` 才会真的点击；`model` 参数选择 checkpoint）。

---

## 设计说明

### 169 个元素里 142 个是菜单栏

朴素的 AX 遍历会把整个系统菜单返回给每个窗口。在实时 Calculator 快照上那是
**169 个节点里的 142 个**，而且**每个应用都一样**——纯粹的噪音，白烧模型的选项预算。
`ax.py` 丢掉 `AXMenuBar`/`AXMenu` 子树，再对剩下的排序（可编辑 → 下拉 → 按钮；
启用的排在禁用的前面）。169 个节点的窗口变成 22 行的表。

### 角色词表故意做成网页形状

checkpoint 学的是网页角色。直接喂 `AXButton` 是白白引入一次词表漂移，所以 AX 角色映射到
最接近的网页角色（`AXButton`→`button`、`AXTextField`→`textbox`）。执行器仍持有真实 AX 角色。

### `enabled: false` 是数据，不是噪音

禁用的控件留在表里——模型需要看到"这个控件存在但不可用"，因为 `WAIT` 是合法且经常正确的答案。
它们只是排在启用的同类后面。

### 有标签不等于有能力

只有当元素的角色*和*它的 AX 动作都支持某操作时，它才进表（`AXPress`、`AXPick`，或可编辑）。
一个没有任何动作的按钮点不了，把它列出来就是个陷阱。

### page_changed 从 AX 树测量

浏览器驱动在动作后比较 URL 签名。桌面窗口没有 URL，等价物是 AX 签名（控件数 + 标签指纹），
在动作后重新读取——每步多走一次 AX 遍历（约 110 ms），所以驱动把那次快照复用为下一次观察，
而不是走两遍树。

### 守卫是继承来的，而且它们很重要

循环拒绝在置信度地板以下行动、拒绝重复执行已处于目标状态的开关、拒绝无变化的重复动作、
在看起来不可逆的目标上停下来等确认。这些守卫存在，是因为模型在网页上就需要它们；
同样的失效模式在桌面上照样出现——上面那次端到端运行就是循环守卫拦停重复输入的模型，
正是设计的样子。

---

## 已知限制（实测，非猜测）

- **官方 checkpoint 零样本选不了控件。** 测试组 0/12，打乱试验 0/30，四个公平性对照排除了
  选项数量、布局、问法的解释。checkpoint 本身是健康的（见对照组）；缺的是
  "目标 + 编号控件 → 选一个索引"这种形态的训练数据。这正是路线图第一项要做的事，
  也是 `--model` 参数存在的意义。
- **自主停止同样没有。** 端到端运行里 checkpoint 从未决定 `DONE`；是 harness 的循环守卫
  结束了运行。没有 `max_steps` 和守卫时，不要无人值守地跑。
- **自绘界面不可见。** 计算器的显示区不是 AX 节点——树里只有它的按钮。任何画进 canvas 的
  东西（游戏、部分图表应用、部分 Electron 应用）都无法观察。和"折叠菜单里的元素不在 DOM 里"
  是同一类限制。
- **一机一模型。** 在 16GB 笔记本上同时常驻两个约 1GB 的模型再加一个浏览器，就是 swap 到死
  的标准配方。一个模型一个时刻；测试组让每个 checkpoint 跑在独立子进程里，正是因为这个。
- **不支持 Windows/Linux。** AX 桥接是 macOS 专属。循环、守卫、模型层都是可移植的，
  所以 UIA（Windows）或 AT-SPI（Linux）适配器是自然的下一步。

---

## 路线图

诚实的下一步，按价值排序：

1. **桌面微调。** 数据缺口是"目标↔控件对 + 真实 AX 树"。本仓库的 `benchmarks/` 测试组和
   浏览器版兄弟项目的微调配方都能生成它，而且这个模型族已被证明能在单张 16GB 显卡上训练。
   在那份数据上训练出来的 checkpoint，就是"会回答关于文本的问题"和"会选控件"之间的差别——
   它出现的那天，`--model` 直接接上。
2. **更多 AX 夹具。** `benchmarks/capture_ax.py` 能把任何窗口变成夹具。从设置、访达、
   Safari 和一个 Electron 应用各采一份，就能把 12 道题变成真正的测试套件，
   而一套夹具正是上面那份微调数据集的一半。
3. **其他平台。** Windows UIA 和 Linux AT-SPI 是同一份契约的不同桥接。

---

## 参考实现与来源

站在别人肩膀上，说清楚：

| 来源 | 贡献了什么 |
|---|---|
| [`convaiinnovations/laya`](https://github.com/NandhaKishorM/laya)（Apache-2.0）| 决策模型本身——也是上面每个数字所用测量的三个官方 checkpoint。非自回归的 System One 编码器，一次前向就给出带校准概率的类型化答案。|
| [`trycua/cua`](https://github.com/trycua/cua)（MIT）| `cua-driver`——辅助功能树读取器，以及本项目执行动作所用的 AX/像素路径。这里的观察契约是它 `get_window_state` 载荷之上的一层薄适配。|
| [`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast)（MIT）| 操作词表（`CLICK`/`TYPE_TEXT`/`SELECT`/`SCROLL`/`WAIT`/`DONE`/`BLOCKED`）和"一次往返回答投机目标"的设计，经由浏览器版兄弟项目传入。|
| [`FluidInference/FluidUse`](https://github.com/FluidInference/FluidUse)（Apache-2.0）| 先例证明：Laya + macOS AX 是在设备端做表单工作的正确形状（Swift/Core ML）。本项目把它带到 Python、带到通用桌面任务、带到 MCP。|
| [ChenneyZhuang/laya-browser-agent](https://github.com/ChenneyZhuang/laya-browser-agent)（Apache-2.0）| 整个决策循环、守卫、作用域裁剪和元素表契约，作为依赖导入而非 fork。|

没有从这些项目复制代码：词表、协议和适配器形状都是针对 AX 表面在这里重新推导的。
模型权重是依赖，不是再分发。

## 许可

Apache-2.0。

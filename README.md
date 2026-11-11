# arXiv Daily Paper Digest

一个手动触发的 Codex Skill：查找近两周首次提交的 arXiv 论文，去重后生成带直链的中文 PDF。检索、排版和登记由 Python 完成；相关性筛选与解读由 Codex 或使用者完成。脚本不需要模型 API Key，也不设置定时任务。

| 主题 | 重点 |
| --- | --- |
| 视频异常检测 | 异常识别、定位、细粒度时序与音视证据 |
| 人体网格 / 姿态 / 动捕 | 单目或多视角人体恢复、三维关键点、动作捕捉 |
| 人体动作生成 / 编辑 | 文本到动作、可控编辑、人-物-场景交互 |
| 电机故障诊断 | 电机、轴承与旋转机械的状态识别 |
| 三维视觉 / 空间智能 / 世界模型 | 三维或四维重建、可控视角、几何或空间状态建模 |

每期交付一份 A4 中文 PDF，每个主题从新页开始并附书签。每主题最多 3 篇，全期最多 15 篇。每篇含作者、首发日期、版本、会议状态、arXiv 摘要与 PDF 直链，以及研究问题、方法、创新、实际工作、借鉴点与阅读注意。合格论文不足时显示实际数量，主题仍保留。

## 安装与触发

适用 macOS / Linux，Python 3.9 或更高版本。以下使用 [Codex 的个人 Skill 目录](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。

```sh
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/leebron801088/arxiv-daily-paper-digest.git "$HOME/.agents/skills/arxiv-daily-paper-digest"
python3 -m venv "$HOME/.agents/skills/arxiv-daily-paper-digest/.venv"
"$HOME/.agents/skills/arxiv-daily-paper-digest/.venv/bin/python" -m pip install -r "$HOME/.agents/skills/arxiv-daily-paper-digest/requirements.txt"
```

在 Codex 中打开用于存放报告的工作目录。若未自动发现 Skill，重启 Codex 或指定其 `SKILL.md`。然后输入：

> 用 $arxiv-daily-paper-digest 生成今天的论文推送。

同一系列应一直使用同一个工作目录或显式指定同一个状态目录；不同目录的历史互相独立。

## 命令行

命令可以从任意工作目录执行。以下假设 Skill 安装在上面的路径。

```sh
skill_dir="$HOME/.agents/skills/arxiv-daily-paper-digest"
python_bin="$skill_dir/.venv/bin/python"

# 只读检索，输出候选，不标记为已推送
"$python_bin" "$skill_dir/scripts/daily_digest.py" scan > candidates.json

# 阅读候选与原文，按 examples/editorial.example.json 的结构准备 editorial.json
"$python_bin" "$skill_dir/scripts/daily_digest_pdf.py" candidates.json editorial.json \
  reports/digest.pdf --delivery delivery.json

# 检查 PDF 后，登记 delivery.json 中的全部论文（把 PAPER_ID 替换成真实 ID）
"$python_bin" "$skill_dir/scripts/daily_digest.py" record delivery.json PAPER_ID
```

`scan --vad-only` 只查视频异常检测；`scan --id ID` 核对从 arXiv 官网补查的候选。遇到 RSS 降级时请查看 `complete` 和 `warnings`。详细字段、跨主题选择及异常处理见 [工作流](references/workflow.md)。

macOS 默认嵌入系统宋体。其他环境若 PDF 查看器无法正确显示中文，用 `--font /path/to/cjk.ttf` 指定可嵌入的中文 TrueType 字体；`.ttc` 可另指定 `--font-index`。仓库不附带字体。

## 去重与状态

- 判定窗口以 arXiv **v1 首次提交**为准，向前滚动 14×24 小时。
- 同一 arXiv base ID 的不同版本只发一次。额外用出版 DOI、规范化标题与第一作者识别跨 ID 的重复论文。
- 检索、PDF 入选与登记都检查历史。最终登记在持锁状态下重新检查，遇到重复时整批拒绝，不留下部分写入。
- 默认历史为当前工作目录的 `.arxiv-digest-state/seen_papers.json`。不要把它放进公开仓库；需跨设备延续去重时自行迁移。

## 开发与许可

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

测试离线运行，使用合成记录。MIT 许可见 [LICENSE](LICENSE)。检索规则和主题标题可在 [`daily_topics.py`](scripts/digestlib/daily_topics.py) 调整。

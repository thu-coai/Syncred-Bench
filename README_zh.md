# SYNCRED-Bench

**面向 AI 生成视觉误导信息中“合成可信度”的评测基准**

[English](README.md) | [中文](README_zh.md)

[论文](https://arxiv.org/pdf/2606.03348) · [代码仓库](https://github.com/thu-coai/Syncred-Bench) · [数据集](https://huggingface.co/datasets/thu-coai/Syncred-Bench)

完整图像集可在 [Hugging Face](https://huggingface.co/datasets/thu-coai/Syncred-Bench) 获取。

![SYNCRED-Bench 总览图](assets/syncred_overview.png)

SYNCRED-Bench 关注一种新的视觉误导风险：**合成可信度**。这类图像本身由 AI 生成，但会模仿新闻版式、机构通知、平台界面、证书票据、数据看板、考试材料等“看起来可信”的视觉形式，并叠加扫描、拍屏、裁切、压缩等真实传播痕迹，使模型和人都更容易把它当作真实材料。

根据论文设定，SYNCRED-Bench 包含 600 张 AI 生成误导图像，覆盖 6 类可信形式和 7 种传播风格；同时构建 FP450 真实负样本集，用于衡量误报。实验显示，现有系统仍不可靠：在 5% FPR 约束下，15 个 MLLM judge 的平均 TPR 只有 10.5%，开源 AIGC 检测器平均低于 5%，商业 API 达到 57.6%；人类多数投票 TPR 为 63.0%，但 FPR 仍有 27.0%。

## 仓库内容

- 简化版 MLLM 评测脚本：`scripts/evaluate_mllm.py`
- Bash 启动脚本：`scripts/run_mllm.sh`
- API 配置示例：`.env.example`、`api.txt.example`
- 参考论文实验整理的 README 配图
- 数据集 taxonomy、评测方式和负责任使用说明

## 快速开始

安装依赖：

```bash
pip install -r requirements.txt
```

配置 OpenAI-compatible 多模态接口：

```bash
cp .env.example .env
# 填写 OPENAI_BASE_URL 和 OPENAI_API_KEY
```

也可以新建 `api.txt`：

```text
url: https://your-openai-compatible-endpoint/v1
key: sk-your-key-here
```

将发布数据放到类似 `/path/to/SynCred_600` 的图片目录下。输入目录应是平铺图片目录，文件名格式为：

```text
SynCred_600/
  AD_CC_50.png
  ML_NR_0.png
  PI_OP_75.png
```

脚本会从文件名 `CONTENT_STYLE_INDEX.png` 读取标签。例如 `AD_CC_50.png` 表示 content 为 `AD` / Analytical Display，style 为 `CC` / Camera Copy，编号为 `50`。

多模型评测：

```bash
python scripts/evaluate_mllm.py \
  --data-dir /path/to/SynCred_600 \
  --output-dir /path/to/results/mllm \
  --models gpt-4o-2024-11-20 qwen/qwen-vl-max
```

Bash 启动脚本示例：

```bash
SYNCRED_DATA_DIR=/path/to/SynCred_600 \
SYNCRED_OUTPUT_DIR=/path/to/results/mllm \
bash scripts/run_mllm.sh --models gpt-4o-2024-11-20 qwen/qwen-vl-max
```

评测真实负样本集：

```bash
python scripts/evaluate_mllm.py \
  --data-dir /path/to/FP450 \
  --output-dir /path/to/results/fp450_mllm \
  --model gpt-4o-2024-11-20 \
  --ground-truth real
```

输出文件：

- `<output-dir>/<model>.jsonl`：逐图预测
- `<output-dir>/summary.csv`：总体、content、style 维度汇总
- `<output-dir>/manifest.json`：运行配置记录

脚本默认跳过已经完成的图片；如需重新生成结果，添加 `--overwrite`。

## 数据集分类体系

6 类可信形式：

| 代码 | 类别 | 示例 |
| --- | --- | --- |
| ML | 媒体版式 | 新闻 App 页面、电视字幕条、报纸页面 |
| IN | 机构通知 | 官方通知、公告 |
| PI | 平台界面 | 社交媒体页面、聊天窗口、网页 |
| CR | 凭证记录 | 证书、奖状、发票、收据、订单页 |
| AD | 分析展示 | 图表、看板、排行榜、后台面板 |
| AM | 测评材料 | 试卷、准考证、成绩单、录取通知 |

7 种可信传播风格：

| 代码 | 风格 | 视觉线索 |
| --- | --- | --- |
| NR | 原生渲染 | 清晰的直接渲染或截图 |
| SC | 扫描副本 | 页边、阴影、轻微倾斜、扫描噪声 |
| CC | 拍摄副本 | 纸张弯曲、光照不均、背景环境 |
| FC | 传真副本 | 条带、模糊、传真或复印痕迹 |
| SP | 屏幕拍摄 | 摩尔纹、眩光、反射、屏幕边框 |
| CV | 裁切视图 | 缺失边缘、上下文被截断 |
| OP | 在线压缩 | 重压缩、像素化、清晰度下降 |

## 使用建议

MLLM judge 的核心任务不是事实核查，而是判断图像来源是否为 AI 生成。对于 SYNCRED-Bench 正样本，使用默认 `--ground-truth ai`；对于 FP450 等真实图像负样本，使用 `--ground-truth real`。建议同时报告 AI 检出率、真实负样本 FPR，并按可信形式和传播风格分层分析。

## 引用

```bibtex
@misc{yang2026syncredbench,
  title = {SYNCRED-BENCH: Benchmarking Synthetic Credibility in AI-Generated Visual Misinformation},
  author = {Yang, Junxiao and Zhang, Minghao and Wang, Xiaoce and Liu, Haoran and Cui, Shiyao and Wang, Hongning and Huang, Minlie},
  year = {2026},
  eprint = {2606.03348},
  archivePrefix = {arXiv},
  primaryClass = {cs.CV}
}
```

## 负责任使用

SYNCRED-Bench 仅用于合成图像检测、来源验证、视觉误导信息安全等研究。请勿使用数据、prompt 或示例进行欺骗、冒充机构、政治操纵，或在合法研究和安全评测之外误用具有可信载体形式的合成图像。

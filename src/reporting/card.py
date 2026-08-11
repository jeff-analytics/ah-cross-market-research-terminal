from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.analysis.historical import find_similar_events, summarize_analogs


@dataclass(frozen=True)
class ExplanationCard:
    title: str
    markdown: str
    analogs: pd.DataFrame


def _fmt_signed(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}"


def build_explanation_card(row: pd.Series, history: pd.DataFrame) -> ExplanationCard:
    analogs = find_similar_events(history)
    summary = summarize_analogs(analogs)

    if row["analysis_status"] == "排除":
        conclusion = "当前偏离可能受到机械价格因素影响，暂不进入正式异常研究。"
    elif abs(row["company_residual_pp"]) >= 2.0 and row["comparability_score"] >= 80:
        conclusion = "市场与行业因素只能解释部分变化，仍存在较明显的公司级剩余异常，建议进入人工研究清单。"
    elif abs(row["company_residual_pp"]) >= 1.0:
        conclusion = "存在一定公司级剩余变化，但强度有限，建议保持观察并等待新增证据。"
    else:
        conclusion = "当前变化主要与行业共同变化一致，暂未发现突出的公司级剩余异常。"

    support: list[str] = []
    oppose: list[str] = []
    if abs(row["h_contribution_pp"]) > abs(row["a_contribution_pp"]):
        support.append("本次价差变化主要由H股价格变动驱动")
    else:
        support.append("本次价差变化主要由A股价格变动驱动")
    if abs(row["company_residual_pp"]) >= 2.0:
        support.append("扣除行业共同变化后仍保留较大剩余异常")
    else:
        oppose.append("行业共同变化能够解释较多价差变动")
    if row["comparability_score"] >= 80:
        support.append("跨市场数据可比性较高")
    else:
        oppose.append("可比性检查存在警告，需要降低结论强度")

    analog_text = "历史样本不足"
    if summary["count"]:
        analog_text = (
            f"找到 {summary['count']} 个相似事件；其中未来5日价差向滚动中位数靠拢的比例为 "
            f"{summary['convergence_5d']:.0%}。"
        )

    markdown = f"""
## {row['company_name']} A/H价差异常解释卡

### 异常概况

- 数据日期：{pd.Timestamp(row['date']).date()}
- 当前A股相对H股溢价：**{row['a_premium_pct']:.2f}%**
- 单日溢价变化：**{_fmt_signed(row['premium_change_pp'])} 个百分点**
- 历史分位：**{row['premium_percentile']:.1%}**
- 滚动异常Z值：**{row['change_z']:.2f}**
- 异常等级：**{row['anomaly_level']}**

### 变化来源

- A股价格贡献：**{_fmt_signed(row['a_contribution_pp'])} 个百分点**
- H股价格贡献：**{_fmt_signed(row['h_contribution_pp'])} 个百分点**
- 汇率贡献：**{_fmt_signed(row['fx_contribution_pp'])} 个百分点**
- 主要驱动：**{row['driver_market']}**

### 可比性检查

- 可比性得分：**{int(row['comparability_score'])}/100**
- 处理状态：**{row['analysis_status']}**
- 检查结果：{row['comparability_reasons']}

### 行业参照与剩余异常

- 同行业当日溢价变化中位数：**{_fmt_signed(row['industry_common_change_pp'])} 个百分点**
- 公司级剩余变化：**{_fmt_signed(row['company_residual_pp'])} 个百分点**

### 支持证据

{chr(10).join(f'- {item}' for item in support)}

### 反对证据与限制

{chr(10).join(f'- {item}' for item in oppose) if oppose else '- 暂未发现明显反对证据，但当前结果仍属于结构化归因。'}

### 历史参照

- {analog_text}
- 历史相似事件只用于研究参照，不代表未来收益或交易建议。

### 当前结论

**{conclusion}**

### 后续核查

- 检查近期公告、停复牌与公司行为
- 观察未来5个交易日H股成交活跃度和价差路径
- 若剩余异常继续扩大，升级为人工研究案例
""".strip()
    return ExplanationCard(f"{row['company_name']}异常解释卡", markdown, analogs)

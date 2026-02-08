"""Gemini AI analyst for generating trading analysis."""

import logging
from typing import Optional

from google import genai

from tracker.core.config import GeminiConfig

logger = logging.getLogger(__name__)


class GeminiAnalyst:
    """Uses Gemini API to generate trading analysis summaries."""

    def __init__(self, config: GeminiConfig):
        self.config = config
        self._client = None

    def initialize(self) -> None:
        """Configure Gemini API client."""
        if not self.config.enabled:
            logger.info("Gemini analyst disabled")
            return

        if not self.config.api_key:
            logger.warning("GEMINI_API_KEY not set, analyst disabled")
            self.config.enabled = False
            return

        self._client = genai.Client(api_key=self.config.api_key)
        logger.info(f"Gemini analyst initialized with model: {self.config.model}")

    async def analyze(
        self,
        wallet_name: str,
        positions: list[dict],
        fills: list[dict],
        position_changes: list[dict],
    ) -> Optional[str]:
        """Generate analysis for a wallet's daily trading activity.

        Returns analysis text or None if disabled/failed.
        """
        if not self.config.enabled or not self._client:
            return None

        prompt = self._build_prompt(wallet_name, positions, fills, position_changes)

        try:
            response = await self._client.aio.models.generate_content(
                model=self.config.model,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini analysis failed for {wallet_name}: {e}")
            return None

    def _build_prompt(
        self,
        wallet_name: str,
        positions: list[dict],
        fills: list[dict],
        position_changes: list[dict],
    ) -> str:
        """Build structured prompt for Gemini analysis."""
        # Format current positions
        if positions:
            pos_lines = []
            for p in positions:
                side = "LONG" if p["size"] > 0 else "SHORT"
                pos_lines.append(
                    f"  - {p['coin']}: {side} {abs(p['size']):.4f} @ ${p['entry_price']:.2f}, "
                    f"unrealizedPnL: ${p['unrealized_pnl']:.2f}, leverage: {p['leverage']:.1f}x"
                )
            positions_text = "\n".join(pos_lines)
        else:
            positions_text = "  (no open positions)"

        # Format today's fills
        if fills:
            fill_lines = []
            for f in fills:
                side = "BUY" if f["side"] == "B" else "SELL"
                fill_lines.append(
                    f"  - {f['time']}: {side} {f['size']:.4f} {f['coin']} @ ${f['price']:.2f}, "
                    f"closedPnL: ${f.get('closed_pnl', 0):.2f}"
                )
            fills_text = "\n".join(fill_lines)
        else:
            fills_text = "  (no trades today)"

        # Format position changes
        if position_changes:
            change_lines = []
            for c in position_changes:
                change_lines.append(
                    f"  - {c['timestamp']}: {c['change_type'].upper()} {c['coin']}, "
                    f"size: {c['old_size']:.4f} -> {c['new_size']:.4f}"
                )
            changes_text = "\n".join(change_lines)
        else:
            changes_text = "  (no position changes)"

        return f"""你是一个加密货币交易分析师。请根据以下数据，用中文对钱包 "{wallet_name}" 今日的交易活动进行简洁分析（约300字）。

## 当前持仓
{positions_text}

## 今日交易记录
{fills_text}

## 今日仓位变动
{changes_text}

请分析：
1. 今日主要操作总结
2. 交易策略倾向（趋势跟踪/逆势/区间交易等）
3. 关键操作及其可能意图
4. 当前持仓风险评估

要求：直接输出分析内容，不需要标题或前缀。语言简洁专业。如果今日无交易，简要描述当前持仓状态即可。"""

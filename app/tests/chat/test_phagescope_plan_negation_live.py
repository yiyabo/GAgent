"""A real 620b user turn must not turn 'please do not create a plan' into a plan requirement."""
from __future__ import annotations

from app.routers.chat.request_routing import resolve_request_routing


def test_live_phagescope_query_with_chinese_plan_negation_stays_chat() -> None:
    message = (
        "请在 PhageScope V2 中搜索“tail fibre”相关的蛋白家族。请实际使用家族同义词检索，列出前 3 个 "
        "family ID 和产品名称，指出哪些属于别名命中；再查看家族索引最近的检查状态，说明抽样核对的局限。"
        "请勿创建计划、下载文件或运行代码，也不要把关键词命中称作实验验证的蛋白功能。"
    )
    decision = resolve_request_routing(message=message)
    assert decision.intent_type == "chat"
    assert decision.plan_create_required is False
    assert "plan_create_request" not in decision.route_reason_codes


def test_chinese_plan_negations_preserve_positive_plan_requests() -> None:
    for phrase in ("请勿创建计划", "勿生成计划", "请勿制定 plan", "不要创建计划"):
        decision = resolve_request_routing(message=f"请查蛋白家族。{phrase}。")
        assert decision.plan_create_required is False
    positive = resolve_request_routing(message="请创建计划来分析蛋白家族。")
    assert positive.plan_create_required is True

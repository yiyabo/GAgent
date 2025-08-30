"""
Chinese (Simplified) prompt templates for the Agent system.
All Chinese prompts are centralized here for easy maintenance and internationalization.
"""

PROMPTS_ZH_CN = {
    # ============== 评估维度 (Evaluation Dimensions) ==============
    "evaluation": {
        "dimensions": {
            "relevance": {
                "name": "相关性",
                "description": "内容与任务的相关程度"
            },
            "completeness": {
                "name": "完整性", 
                "description": "内容的完整性和充实度"
            },
            "accuracy": {
                "name": "准确性",
                "description": "内容的事实准确性和可信度"
            },
            "clarity": {
                "name": "清晰度",
                "description": "表达的清晰度和可读性"
            },
            "coherence": {
                "name": "连贯性",
                "description": "逻辑连贯性和结构合理性"
            },
            "scientific_rigor": {
                "name": "科学严谨性",
                "description": "科学方法和术语的规范性"
            }
        },
        
        "instructions": {
            "json_format": "请以JSON格式返回评估结果：",
            "explain_scores": "简要说明每个维度的评分理由",
            "provide_suggestions": "请提供具体的改进建议"
        },
        
        "quality_levels": {
            "excellent": "优秀",
            "good": "良好",
            "fair": "一般",
            "poor": "较差"
        }
    },
    
    # ============== 专家角色 (Expert Roles) ==============
    "expert_roles": {
        "theoretical_biologist": {
            "name": "理论生物学家",
            "description": "资深的理论生物学专家，专注于噬菌体生物学机制和理论基础",
            "focus_areas": ["生物学机制", "理论基础", "科学原理", "分子机制"],
            "keywords": ["噬菌体", "细菌", "病毒", "机制", "分子", "生物学"]
        },
        "clinical_physician": {
            "name": "临床医师",
            "description": "具有丰富临床经验的感染科医师，关注噬菌体治疗的临床应用",
            "focus_areas": ["临床安全性", "治疗效果", "患者安全", "临床可行性"],
            "keywords": ["临床", "患者", "治疗", "安全", "副作用", "疗效"]
        },
        "regulatory_expert": {
            "name": "药监局审批专家",
            "description": "药物监管机构的审批专家，专注于法规合规性和质量控制",
            "focus_areas": ["法规合规", "质量控制", "安全标准", "审批要求"],
            "keywords": ["安全", "标准", "质量", "审批", "监管", "合规"]
        },
        "researcher": {
            "name": "科研工作者",
            "description": "噬菌体研究领域的资深科学家，关注研究方法和实验设计",
            "focus_areas": ["实验设计", "研究方法", "数据分析", "研究严谨性"],
            "keywords": ["研究", "实验", "数据", "分析", "试验", "方法"]
        },
        "entrepreneur": {
            "name": "生物技术企业家",
            "description": "生物技术公司的创始人/CEO，关注商业化潜力和市场前景",
            "focus_areas": ["商业化可行性", "市场前景", "技术壁垒", "投资回报"],
            "keywords": ["市场", "商业", "投资", "成本", "前景", "应用"]
        }
    },
    
    # ============== 专家评估模板 (Expert Evaluation Templates) ==============
    "expert_evaluation": {
        "intro": "你现在是{role_description}。请从你的专业角度评估以下内容。",
        "task_background": "任务背景：",
        "content_to_evaluate": "需要评估的内容：",
        "focus_statement": "作为{role_name}，你主要关注：",
        "evaluation_instruction": "请从以下维度进行专业评估，每个维度给出0-1之间的分数：",
        
        "dimensions": {
            "relevance": "**相关性**: 内容与任务的专业相关程度",
            "completeness": "**完整性**: 从你的专业角度看内容是否完整",
            "accuracy": "**准确性**: 专业事实和概念的准确性",
            "practicality": "**实用性**: 内容的实际应用价值",
            "innovation": "**创新性**: 是否包含新颖的见解或方法",
            "risk_assessment": "**风险评估**: 潜在的问题和风险"
        },
        
        "output_format": {
            "strengths": ["优势1", "优势2"],
            "issues": ["问题1", "问题2"],
            "suggestions": ["建议1", "建议2", "建议3"]
        },
        
        "fallback_messages": {
            "content_relevant": "内容与{expert_name}关注领域相关",
            "llm_unavailable": "LLM评估不可用，使用基础评估",
            "improvement_suggestion": "建议从{expert_name}角度进一步完善内容"
        }
    },
    
    # ============== 对抗性评估 (Adversarial Evaluation) ==============
    "adversarial": {
        "generator": {
            "intro": "作为内容生成专家，请为以下任务创建高质量的内容：",
            "task_label": "任务：",
            "task_type_label": "任务类型：",
            "requirements_label": "要求：",
            "requirements": [
                "1. 内容要准确、完整、有条理",
                "2. 使用专业但易懂的语言",
                "3. 包含必要的细节和解释",
                "4. 长度适中（200-400词）"
            ],
            "generate_prompt": "请生成内容：",
            "error_message": "生成内容时出现错误："
        },
        
        "improver": {
            "intro": "你是一位内容改进专家。请根据以下批评意见改进内容。",
            "original_task": "原始任务：",
            "original_content": "原始内容：",
            "criticism": "批评者指出的问题：",
            "improvement_instruction": "请根据这些批评意见，重新改写内容，确保：",
            "requirements": [
                "1. 解决所有提出的问题",
                "2. 保持内容的核心价值和准确性",
                "3. 提高内容的整体质量",
                "4. 保持适当的长度和结构"
            ],
            "improved_content": "改进后的内容："
        },
        
        "critic": {
            "intro": "你是一位极其严格的内容批评家。你的任务是找出内容中的所有问题和不足。",
            "task_background": "任务背景：",
            "content_to_critique": "需要批评的内容：",
            "critique_instruction": "请从以下角度严格批评这个内容：",
            "critique_angles": [
                "1. **准确性问题**：事实错误、概念混乱、过时信息",
                "2. **完整性缺陷**：遗漏的重要信息、深度不足",
                "3. **逻辑问题**：论证不严密、前后矛盾",
                "4. **表达问题**：语言不清晰、专业性不足",
                "5. **结构问题**：组织混乱、重点不明",
                "6. **实用性问题**：缺乏实际应用价值"
            ],
            "output_requirements": [
                "对于找到的每个问题，请提供：",
                "- 具体的问题描述",
                "- 严重程度（高/中/低）",
                "- 具体的改进建议"
            ],
            "output_format": {
                "overall_assessment": "总体评价",
                "problem_category": "问题类别",
                "problem_description": "具体问题描述",
                "severity": "严重程度",
                "improvement_suggestion": "改进建议",
                "evidence": "问题证据",
                "minor_issues": "次要问题",
                "strengths": ["优点1", "优点2"]
            }
        },
        
        "severity_levels": {
            "high": "高",
            "medium": "中", 
            "low": "低"
        },
        
        "severity_weights": {
            "high": 0.3,
            "medium": 0.1,
            "low": 0.05
        },
        
        "problem_categories": {
            "uncategorized": "未分类",
            "completeness": "完整性",
            "accuracy": "准确性",
            "logic": "逻辑性",
            "expression": "表达",
            "structure": "结构",
            "practicality": "实用性",
            "other": "其他"
        },
        
        "default_issues": {
            "too_short": {
                "category": "完整性",
                "description": "内容过于简短",
                "severity": "高",
                "suggestion": "增加更多详细信息和解释",
                "evidence": "当前仅有{word_count}词"
            },
            "too_long": {
                "category": "完整性",
                "description": "内容可能过于冗长",
                "severity": "低",
                "suggestion": "考虑精简内容，突出重点",
                "evidence": "当前有{word_count}词"
            },
            "no_paragraphs": {
                "category": "结构",
                "description": "缺乏段落结构",
                "severity": "中",
                "suggestion": "将内容分成多个段落以提高可读性"
            }
        },
        
        "quality_recommendations": {
            "excellent": "内容质量优秀，通过了严格的对抗性测试",
            "good": "内容质量良好，但仍有改进空间",
            "fair": "内容质量中等，需要重点改进主要问题",
            "poor": "内容质量不足，建议重新设计和编写"
        }
    },
    
    # ============== 元认知评估 (Meta-Cognitive Evaluation) ==============
    "meta_evaluation": {
        "criteria": {
            "consistency": "评估结果的一致性和稳定性",
            "objectivity": "评估过程的客观性，避免主观偏见",
            "comprehensiveness": "评估维度的全面性和完整性",
            "calibration": "评估分数与实际质量的校准程度",
            "discriminability": "评估系统区分不同质量内容的能力",
            "reliability": "评估结果的可靠性和可重复性"
        },
        
        "llm_prompts": {
            "intro": "作为评估质量专家，请对以下评估过程进行元认知分析。",
            "evaluation_history": "评估历史摘要：",
            "analysis_dimensions": {
                "accuracy": "**评估准确性**: 评估结果是否准确反映内容质量？",
                "comprehensiveness": "**评估全面性**: 评估维度是否全面覆盖内容质量要素？",
                "consistency": "**评估一致性**: 多次评估结果是否保持一致？",
                "objectivity": "**评估客观性**: 评估过程是否客观，避免主观偏见？",
                "practicality": "**评估实用性**: 评估建议是否具有实际指导价值？"
            },
            "output_format": {
                "strengths": ["优势1", "优势2"],
                "improvements": ["改进点1", "改进点2"],
                "insights": ["洞察1", "洞察2"]
            }
        },
        
        "summary_format": {
            "no_history": "无评估历史",
            "round_summary": "第{round}轮: 评分{score:.2f}, {suggestions}条建议, {status}",
            "needs_revision": "需要修订",
            "quality_met": "质量达标"
        },
        
        "fallback_messages": {
            "basic_evaluation_ok": "基础评估功能正常",
            "llm_unavailable": "LLM元评估不可用",
            "check_connection": "建议检查LLM连接"
        },
        
        "cognitive_biases": {
            "anchoring": "检测到锚定偏见，后续评估过度依赖首次评估结果",
            "halo_effect": "检测到光环效应，各评估维度相关性过高",
            "severity_bias": "检测到严厉偏见，评估标准可能过于苛刻",
            "leniency_bias": "检测到宽松偏见，评估标准可能过于宽松"
        },
        
        "insights": {
            "unstable_results": "评估结果不稳定，建议检查评估标准的一致性",
            "highly_stable": "评估结果高度稳定，显示评估系统运行良好",
            "low_quality": "整体评估质量偏低，建议优化评估流程",
            "excellent_performance": "评估系统表现优秀，质量控制良好"
        },
        
        "health_suggestions": [
            "提高评估标准的一致性",
            "加强认知偏见控制",
            "增加评估样本量以提高可靠性"
        ],
        
        "error_messages": {
            "no_history": "无评估历史可供分析",
            "evaluation_error": "元评估出错: {error}"
        }
    },
    
    # ============== 状态和标签 (Status and Labels) ==============
    "status": {
        "trends": {
            "improving": "改进中",
            "declining": "下降",
            "stable": "稳定",
            "insufficient_data": "数据不足"
        },
        
        "stability": {
            "very_stable": "非常稳定",
            "moderately_stable": "相对稳定",
            "unstable": "不稳定",
            "unknown": "未知"
        },
        
        "quality": {
            "excellent": "优秀",
            "good": "良好",
            "fair": "一般",
            "poor": "较差"
        },
        
        "system": {
            "error": "错误",
            "fallback": "回退",
            "empty_content": "内容为空",
            "empty_evaluation_history": "评估历史为空"
        }
    },
    
    # ============== 通用消息 (Common Messages) ==============
    "common": {
        "errors": {
            "generation_error": "生成时出现错误：{error}",
            "evaluation_error": "评估时出现错误：{error}",
            "llm_connection_error": "LLM连接失败：{error}",
            "invalid_format": "返回格式无效",
            "missing_required_field": "缺少必需字段：{field}"
        },
        
        "warnings": {
            "using_fallback": "使用回退方案",
            "reduced_functionality": "功能降级运行",
            "cache_miss": "缓存未命中"
        },
        
        "info": {
            "processing": "正在处理...",
            "completed": "处理完成",
            "saved_successfully": "保存成功",
            "loaded_from_cache": "从缓存加载"
        }
    }
}
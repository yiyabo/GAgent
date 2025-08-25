"""
Adversarial Evaluation System

Implements Generator vs Critic mechanism for robust content evaluation.
The generator tries to improve content while the critic finds flaws,
creating an adversarial training-like dynamic.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base_evaluator import LLMBasedEvaluator
from ..models import EvaluationResult, EvaluationDimensions, EvaluationConfig

logger = logging.getLogger(__name__)


class ContentGenerator:
    """Generator component that creates and improves content"""
    
    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.generation_history = []
    
    def generate_initial_content(self, task_context: Dict[str, Any]) -> str:
        """Generate initial content for the task"""
        
        task_name = task_context.get("name", "")
        task_type = task_context.get("task_type", "content_generation")
        
        generation_prompt = f"""
作为内容生成专家，请为以下任务创建高质量的内容：

任务："{task_name}"
任务类型：{task_type}

要求：
1. 内容要准确、完整、有条理
2. 使用专业但易懂的语言
3. 包含必要的细节和解释
4. 长度适中（200-400词）

请生成内容：
"""
        
        try:
            response = self.llm_client.chat([
                {"role": "user", "content": generation_prompt}
            ])
            
            content = response.get("content", "").strip()
            self.generation_history.append({
                "type": "initial",
                "content": content,
                "timestamp": datetime.now()
            })
            
            return content
            
        except Exception as e:
            logger.error(f"Initial content generation failed: {e}")
            return f"生成内容时出现错误：{str(e)}"
    
    def improve_content(
        self, 
        original_content: str, 
        criticisms: List[Dict[str, Any]], 
        task_context: Dict[str, Any]
    ) -> str:
        """Improve content based on critic's feedback"""
        
        if not criticisms:
            return original_content
        
        # Format criticisms for the improvement prompt
        criticism_text = "\n".join([
            f"- {criticism.get('issue', '')}: {criticism.get('suggestion', '')}"
            for criticism in criticisms
        ])
        
        improvement_prompt = f"""
你是一位内容改进专家。请根据以下批评意见改进内容。

原始任务："{task_context.get('name', '')}"

原始内容：
```
{original_content}
```

批评者指出的问题：
{criticism_text}

请根据这些批评意见，重新改写内容，确保：
1. 解决所有提出的问题
2. 保持内容的核心价值和准确性
3. 提高内容的整体质量
4. 保持适当的长度和结构

改进后的内容：
"""
        
        try:
            response = self.llm_client.chat([
                {"role": "user", "content": improvement_prompt}
            ])
            
            improved_content = response.get("content", "").strip()
            self.generation_history.append({
                "type": "improvement",
                "original": original_content,
                "improved": improved_content,
                "criticisms_addressed": len(criticisms),
                "timestamp": datetime.now()
            })
            
            return improved_content
            
        except Exception as e:
            logger.error(f"Content improvement failed: {e}")
            return original_content  # Fallback to original


class ContentCritic:
    """Critic component that finds flaws and provides feedback"""
    
    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.criticism_history = []
    
    def critique_content(
        self, 
        content: str, 
        task_context: Dict[str, Any],
        iteration: int = 1
    ) -> List[Dict[str, Any]]:
        """Generate detailed criticisms of the content"""
        
        task_name = task_context.get("name", "")
        
        critique_prompt = f"""
你是一位极其严格的内容批评家。你的任务是找出内容中的所有问题和不足。

任务背景："{task_name}"

需要批评的内容：
```
{content}
```

请从以下角度严格批评这个内容：
1. **准确性问题**：事实错误、概念混乱、过时信息
2. **完整性缺陷**：遗漏的重要信息、深度不足
3. **逻辑问题**：论证不严密、前后矛盾
4. **表达问题**：语言不清晰、专业性不足
5. **结构问题**：组织混乱、重点不明
6. **实用性问题**：缺乏实际应用价值

对于找到的每个问题，请提供：
- 具体的问题描述
- 严重程度（高/中/低）
- 具体的改进建议

请以JSON格式返回批评结果：
{{
    "overall_assessment": "总体评价",
    "major_flaws": [
        {{
            "category": "问题类别",
            "issue": "具体问题描述", 
            "severity": "严重程度",
            "suggestion": "改进建议",
            "evidence": "问题证据"
        }}
    ],
    "minor_issues": [
        {{
            "issue": "次要问题",
            "suggestion": "改进建议"
        }}
    ],
    "strengths": ["优点1", "优点2"],
    "critic_confidence": 0.9
}}
"""
        
        try:
            response = self.llm_client.chat([
                {"role": "user", "content": critique_prompt}
            ])
            
            result_text = response.get("content", "").strip()
            
            # Parse JSON response
            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1
            
            if json_start >= 0 and json_end > json_start:
                json_text = result_text[json_start:json_end]
                critique_data = json.loads(json_text)
                
                # Extract all criticisms
                criticisms = []
                
                # Add major flaws
                for flaw in critique_data.get("major_flaws", []):
                    criticisms.append({
                        "type": "major",
                        "category": flaw.get("category", "未分类"),
                        "issue": flaw.get("issue", ""),
                        "severity": flaw.get("severity", "中"),
                        "suggestion": flaw.get("suggestion", ""),
                        "evidence": flaw.get("evidence", "")
                    })
                
                # Add minor issues
                for issue in critique_data.get("minor_issues", []):
                    criticisms.append({
                        "type": "minor", 
                        "issue": issue.get("issue", ""),
                        "suggestion": issue.get("suggestion", ""),
                        "severity": "低"
                    })
                
                self.criticism_history.append({
                    "content_preview": content[:100] + "..." if len(content) > 100 else content,
                    "criticisms_count": len(criticisms),
                    "overall_assessment": critique_data.get("overall_assessment", ""),
                    "critic_confidence": critique_data.get("critic_confidence", 0.5),
                    "iteration": iteration,
                    "timestamp": datetime.now()
                })
                
                return criticisms
            else:
                logger.warning("Could not parse critic JSON response")
                return self._fallback_criticism(content, task_context)
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in criticism: {e}")
            return self._fallback_criticism(content, task_context)
        except Exception as e:
            logger.error(f"Content criticism failed: {e}")
            return self._fallback_criticism(content, task_context)
    
    def _fallback_criticism(self, content: str, task_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback criticism when LLM fails"""
        
        word_count = len(content.split())
        
        criticisms = []
        
        # Basic length criticism
        if word_count < 100:
            criticisms.append({
                "type": "major",
                "category": "完整性",
                "issue": "内容过于简短",
                "severity": "高",
                "suggestion": "增加更多详细信息和解释",
                "evidence": f"当前仅有{word_count}词"
            })
        elif word_count > 500:
            criticisms.append({
                "type": "minor",
                "issue": "内容可能过于冗长",
                "suggestion": "考虑精简内容，突出重点",
                "severity": "低"
            })
        
        # Basic structure criticism
        if content.count("\n\n") < 1:
            criticisms.append({
                "type": "minor",
                "issue": "缺乏段落结构",
                "suggestion": "将内容分成多个段落以提高可读性",
                "severity": "中"
            })
        
        return criticisms


class AdversarialEvaluator(LLMBasedEvaluator):
    """Main adversarial evaluation system combining generator and critic"""
    
    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)
        self.generator = ContentGenerator(self.llm_client)
        self.critic = ContentCritic(self.llm_client)
    
    def get_evaluation_method_name(self) -> str:
        return "adversarial"
    
    def adversarial_evaluate(
        self, 
        content: str, 
        task_context: Dict[str, Any],
        max_rounds: int = 3,
        improvement_threshold: float = 0.1
    ) -> Dict[str, Any]:
        """
        Run adversarial evaluation with multiple rounds of generator vs critic
        
        Args:
            content: Initial content to evaluate
            task_context: Task context information
            max_rounds: Maximum number of adversarial rounds
            improvement_threshold: Minimum improvement required to continue
            
        Returns:
            Dict with adversarial evaluation results
        """
        
        logger.info(f"Starting adversarial evaluation with {max_rounds} max rounds")
        
        rounds = []
        current_content = content
        best_content = content
        best_robustness_score = 0.0
        
        for round_num in range(max_rounds):
            logger.debug(f"Adversarial round {round_num + 1}/{max_rounds}")
            
            # Critic attacks the content
            criticisms = self.critic.critique_content(
                current_content, 
                task_context, 
                iteration=round_num + 1
            )
            
            # Calculate robustness score
            robustness_score = self._calculate_robustness_score(criticisms)
            
            # Generator defends by improving content
            if criticisms and round_num < max_rounds - 1:
                improved_content = self.generator.improve_content(
                    current_content, 
                    criticisms, 
                    task_context
                )
            else:
                improved_content = current_content
            
            round_data = {
                "round": round_num + 1,
                "original_content": current_content,
                "criticisms": criticisms,
                "criticism_count": len(criticisms),
                "robustness_score": robustness_score,
                "improved_content": improved_content,
                "improvement_made": improved_content != current_content,
                "timestamp": datetime.now().isoformat()
            }
            
            rounds.append(round_data)
            
            # Update best content if this version is more robust
            if robustness_score > best_robustness_score:
                best_robustness_score = robustness_score
                best_content = current_content
            
            # Check if improvement is significant enough to continue
            if round_num > 0:
                prev_score = rounds[round_num - 1]["robustness_score"]
                improvement = robustness_score - prev_score
                
                if improvement < improvement_threshold:
                    logger.info(f"Improvement below threshold ({improvement:.3f} < {improvement_threshold}), stopping early")
                    break
            
            current_content = improved_content
        
        # Generate final assessment
        final_assessment = self._generate_final_assessment(rounds, best_content, task_context)
        
        result = {
            "best_content": best_content,
            "best_robustness_score": best_robustness_score,
            "rounds_completed": len(rounds),
            "total_rounds": max_rounds,
            "adversarial_rounds": rounds,
            "final_assessment": final_assessment,
            "metadata": {
                "evaluation_method": "adversarial",
                "generator_generations": len(self.generator.generation_history),
                "critic_analyses": len(self.critic.criticism_history),
                "total_criticisms": sum(r["criticism_count"] for r in rounds),
                "average_robustness": sum(r["robustness_score"] for r in rounds) / len(rounds) if rounds else 0.0
            }
        }
        
        logger.info(f"Adversarial evaluation completed: {len(rounds)} rounds, best score: {best_robustness_score:.3f}")
        
        return result
    
    def _calculate_robustness_score(self, criticisms: List[Dict[str, Any]]) -> float:
        """Calculate robustness score based on criticisms"""
        
        if not criticisms:
            return 1.0  # Perfect score if no criticisms
        
        # Weight criticisms by severity
        severity_weights = {
            "高": 0.3,   # High severity has more impact
            "中": 0.1,   # Medium severity
            "低": 0.05   # Low severity
        }
        
        total_penalty = 0.0
        for criticism in criticisms:
            severity = criticism.get("severity", "中")
            penalty = severity_weights.get(severity, 0.1)
            total_penalty += penalty
        
        # Convert penalty to robustness score (0-1)
        robustness_score = max(0.0, 1.0 - total_penalty)
        
        return robustness_score
    
    def _generate_final_assessment(
        self, 
        rounds: List[Dict[str, Any]], 
        best_content: str, 
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate final assessment of adversarial evaluation"""
        
        if not rounds:
            return {"error": "No rounds completed"}
        
        total_criticisms = sum(r["criticism_count"] for r in rounds)
        final_score = rounds[-1]["robustness_score"]
        improvement_trend = []
        
        # Calculate improvement trend
        for i in range(1, len(rounds)):
            improvement = rounds[i]["robustness_score"] - rounds[i-1]["robustness_score"]
            improvement_trend.append(improvement)
        
        # Categorize most common criticism types
        all_criticisms = []
        for round_data in rounds:
            all_criticisms.extend(round_data["criticisms"])
        
        criticism_categories = {}
        for criticism in all_criticisms:
            category = criticism.get("category", "其他")
            criticism_categories[category] = criticism_categories.get(category, 0) + 1
        
        assessment = {
            "final_robustness_score": final_score,
            "total_criticisms_found": total_criticisms,
            "average_criticisms_per_round": total_criticisms / len(rounds),
            "improvement_trend": improvement_trend,
            "most_common_issues": sorted(criticism_categories.items(), key=lambda x: x[1], reverse=True)[:3],
            "adversarial_effectiveness": len(rounds) / max(1, total_criticisms) * 10,  # Higher is better
            "convergence_achieved": len(improvement_trend) > 0 and all(abs(imp) < 0.05 for imp in improvement_trend[-2:]),
            "recommendation": self._generate_recommendation(final_score, total_criticisms, len(rounds))
        }
        
        return assessment
    
    def _generate_recommendation(self, final_score: float, total_criticisms: int, rounds: int) -> str:
        """Generate recommendation based on adversarial results"""
        
        if final_score >= 0.9:
            return "内容质量优秀，通过了严格的对抗性测试"
        elif final_score >= 0.7:
            return "内容质量良好，但仍有改进空间"
        elif final_score >= 0.5:
            return "内容质量中等，需要重点改进主要问题"
        else:
            return "内容质量不足，建议重新设计和编写"


def get_adversarial_evaluator(config: Optional[EvaluationConfig] = None) -> AdversarialEvaluator:
    """Factory function to get adversarial evaluator instance"""
    return AdversarialEvaluator(config)
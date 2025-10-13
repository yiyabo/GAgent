"""
English prompt templates for the Agent system.
All English prompts are centralized here for easy maintenance and optimization.
"""

PROMPTS_EN_US = {
    # ============== Evaluation Dimensions ==============
    "evaluation": {
        "dimensions": {
            "relevance": {"name": "Relevance", "description": "How well the content relates to the task requirements"},
            "completeness": {
                "name": "Completeness",
                "description": "The thoroughness and comprehensiveness of the content",
            },
            "accuracy": {
                "name": "Accuracy",
                "description": "The factual correctness and reliability of the information",
            },
            "clarity": {"name": "Clarity", "description": "The clearness and readability of the expression"},
            "coherence": {"name": "Coherence", "description": "The logical consistency and structural soundness"},
            "scientific_rigor": {
                "name": "Scientific Rigor",
                "description": "The adherence to scientific methods and terminology standards",
            },
        },
        "instructions": {
            "json_format": "Please return the evaluation results in JSON format:",
            "explain_scores": "Provide brief reasoning for each dimension score",
            "provide_suggestions": "Please provide specific improvement suggestions",
        },
        "quality_levels": {"excellent": "Excellent", "good": "Good", "fair": "Fair", "poor": "Poor"},
    },
    # ============== Expert Roles ==============
    "expert_roles": {
        "theoretical_biologist": {
            "name": "Theoretical Biologist",
            "description": "Senior theoretical biology expert specializing in phage biology mechanisms and theoretical foundations",
            "focus_areas": [
                "Biological mechanisms",
                "Theoretical foundations",
                "Scientific principles",
                "Molecular mechanisms",
            ],
            "keywords": ["phage", "bacteria", "virus", "mechanism", "molecular", "biology"],
        },
        "clinical_physician": {
            "name": "Clinical Physician",
            "description": "Experienced infectious disease physician focusing on clinical applications of phage therapy",
            "focus_areas": ["Clinical safety", "Treatment efficacy", "Patient safety", "Clinical feasibility"],
            "keywords": ["clinical", "patient", "treatment", "safety", "side effects", "efficacy"],
        },
        "regulatory_expert": {
            "name": "Regulatory Affairs Expert",
            "description": "Drug regulatory agency approval expert focusing on regulatory compliance and quality control",
            "focus_areas": ["Regulatory compliance", "Quality control", "Safety standards", "Approval requirements"],
            "keywords": ["safety", "standards", "quality", "approval", "regulation", "compliance"],
        },
        "researcher": {
            "name": "Research Scientist",
            "description": "Senior scientist in phage research focusing on research methodology and experimental design",
            "focus_areas": ["Experimental design", "Research methodology", "Data analysis", "Research rigor"],
            "keywords": ["research", "experiment", "data", "analysis", "trial", "methodology"],
        },
        "entrepreneur": {
            "name": "Biotech Entrepreneur",
            "description": "Biotech company founder/CEO focusing on commercialization potential and market prospects",
            "focus_areas": ["Commercial viability", "Market prospects", "Technical barriers", "Return on investment"],
            "keywords": ["market", "commercial", "investment", "cost", "prospects", "application"],
        },
    },
    # ============== Expert Evaluation Templates ==============
    "expert_evaluation": {
        "intro": "You are now acting as a {role_description}. Please evaluate the following content from your professional perspective.",
        "task_background": "Task Background:",
        "content_to_evaluate": "Content to Evaluate:",
        "focus_statement": "As a {role_name}, you primarily focus on:",
        "evaluation_instruction": "Please provide professional evaluation from the following dimensions, giving scores between 0-1 for each:",
        "dimensions": {
            "relevance": "**Relevance**: How professionally relevant the content is to the task",
            "completeness": "**Completeness**: Whether the content is complete from your professional perspective",
            "accuracy": "**Accuracy**: The accuracy of professional facts and concepts",
            "practicality": "**Practicality**: The practical application value of the content",
            "innovation": "**Innovation**: Whether it contains novel insights or methods",
            "risk_assessment": "**Risk Assessment**: Potential problems and risks",
        },
        "output_format": {
            "strengths": ["Strength 1", "Strength 2"],
            "issues": ["Issue 1", "Issue 2"],
            "suggestions": ["Suggestion 1", "Suggestion 2", "Suggestion 3"],
        },
        "fallback_messages": {
            "content_relevant": "Content is relevant to {expert_name}'s areas of focus",
            "llm_unavailable": "LLM evaluation unavailable, using basic evaluation",
            "improvement_suggestion": "Recommend further refinement from {expert_name} perspective",
        },
    },
    # ============== Adversarial Evaluation ==============
    "adversarial": {
        "generator": {
            "intro": "As a content generation expert, please create high-quality content for the following task:",
            "task_label": "Task:",
            "task_type_label": "Task Type:",
            "requirements_label": "Requirements:",
            "requirements": [
                "1. Content should be accurate, complete, and well-organized",
                "2. Use professional but accessible language",
                "3. Include necessary details and explanations",
                "4. Maintain appropriate length (200-400 words)",
            ],
            "generate_prompt": "Please generate content:",
            "error_message": "Error occurred during content generation:",
        },
        "improver": {
            "intro": "You are a content improvement expert. Please improve the content based on the following criticisms.",
            "original_task": "Original Task:",
            "original_content": "Original Content:",
            "criticism": "Criticisms Identified:",
            "improvement_instruction": "Based on these criticisms, please rewrite the content ensuring:",
            "requirements": [
                "1. Address all identified issues",
                "2. Maintain the core value and accuracy of the content",
                "3. Improve overall content quality",
                "4. Keep appropriate length and structure",
            ],
            "improved_content": "Improved Content:",
        },
        "critic": {
            "intro": "You are an extremely strict content critic. Your task is to identify all problems and deficiencies in the content.",
            "task_background": "Task Background:",
            "content_to_critique": "Content to Critique:",
            "critique_instruction": "Please strictly critique this content from the following angles:",
            "critique_angles": [
                "1. **Accuracy Issues**: Factual errors, conceptual confusion, outdated information",
                "2. **Completeness Deficits**: Missing important information, insufficient depth",
                "3. **Logical Problems**: Weak arguments, contradictions",
                "4. **Expression Issues**: Unclear language, insufficient professionalism",
                "5. **Structural Problems**: Poor organization, unclear focus",
                "6. **Practicality Issues**: Lack of practical application value",
            ],
            "output_requirements": [
                "For each problem found, please provide:",
                "- Specific problem description",
                "- Severity level (High/Medium/Low)",
                "- Specific improvement suggestions",
            ],
            "output_format": {
                "overall_assessment": "Overall Assessment",
                "problem_category": "Problem Category",
                "problem_description": "Specific Problem Description",
                "severity": "Severity Level",
                "improvement_suggestion": "Improvement Suggestion",
                "evidence": "Problem Evidence",
                "minor_issues": "Minor Issues",
                "strengths": ["Strength 1", "Strength 2"],
            },
        },
        "severity_levels": {"high": "High", "medium": "Medium", "low": "Low"},
        "severity_weights": {"high": 0.3, "medium": 0.1, "low": 0.05},
        "problem_categories": {
            "uncategorized": "Uncategorized",
            "completeness": "Completeness",
            "accuracy": "Accuracy",
            "logic": "Logic",
            "expression": "Expression",
            "structure": "Structure",
            "practicality": "Practicality",
            "other": "Other",
        },
        "default_issues": {
            "too_short": {
                "category": "Completeness",
                "description": "Content is too brief",
                "severity": "High",
                "suggestion": "Add more detailed information and explanations",
                "evidence": "Currently only {word_count} words",
            },
            "too_long": {
                "category": "Completeness",
                "description": "Content may be too lengthy",
                "severity": "Low",
                "suggestion": "Consider condensing content and highlighting key points",
                "evidence": "Currently {word_count} words",
            },
            "no_paragraphs": {
                "category": "Structure",
                "description": "Lacks paragraph structure",
                "severity": "Medium",
                "suggestion": "Divide content into multiple paragraphs for better readability",
            },
        },
        "quality_recommendations": {
            "excellent": "Content quality is excellent and passed rigorous adversarial testing",
            "good": "Content quality is good but has room for improvement",
            "fair": "Content quality is fair and needs to address major issues",
            "poor": "Content quality is insufficient, recommend redesign and rewriting",
        },
    },
    # ============== Meta-Cognitive Evaluation ==============
    "meta_evaluation": {
        "criteria": {
            "consistency": "Consistency and stability of evaluation results",
            "objectivity": "Objectivity of evaluation process, avoiding subjective bias",
            "comprehensiveness": "Comprehensiveness and completeness of evaluation dimensions",
            "calibration": "Calibration degree between evaluation scores and actual quality",
            "discriminability": "Ability of evaluation system to distinguish different quality content",
            "reliability": "Reliability and reproducibility of evaluation results",
        },
        "llm_prompts": {
            "intro": "As an evaluation quality expert, please conduct meta-cognitive analysis of the following evaluation process.",
            "evaluation_history": "Evaluation History Summary:",
            "analysis_dimensions": {
                "accuracy": "**Evaluation Accuracy**: Do the evaluation results accurately reflect content quality?",
                "comprehensiveness": "**Evaluation Comprehensiveness**: Do evaluation dimensions comprehensively cover content quality elements?",
                "consistency": "**Evaluation Consistency**: Are multiple evaluation results consistent?",
                "objectivity": "**Evaluation Objectivity**: Is the evaluation process objective, avoiding subjective bias?",
                "practicality": "**Evaluation Practicality**: Do evaluation suggestions have practical guidance value?",
            },
            "output_format": {
                "strengths": ["Strength 1", "Strength 2"],
                "improvements": ["Improvement 1", "Improvement 2"],
                "insights": ["Insight 1", "Insight 2"],
            },
        },
        "summary_format": {
            "no_history": "No evaluation history",
            "round_summary": "Round {round}: Score {score:.2f}, {suggestions} suggestions, {status}",
            "needs_revision": "Needs revision",
            "quality_met": "Quality standard met",
        },
        "fallback_messages": {
            "basic_evaluation_ok": "Basic evaluation function is working normally",
            "llm_unavailable": "LLM meta-evaluation unavailable",
            "check_connection": "Recommend checking LLM connection",
        },
        "cognitive_biases": {
            "anchoring": "Anchoring bias detected - subsequent evaluations overly rely on initial evaluation results",
            "halo_effect": "Halo effect detected - evaluation dimensions are too highly correlated",
            "severity_bias": "Severity bias detected - evaluation standards may be too strict",
            "leniency_bias": "Leniency bias detected - evaluation standards may be too lenient",
        },
        "insights": {
            "unstable_results": "Evaluation results are unstable, recommend checking evaluation standard consistency",
            "highly_stable": "Evaluation results are highly stable, showing good system performance",
            "low_quality": "Overall evaluation quality is low, recommend optimizing evaluation process",
            "excellent_performance": "Evaluation system performs excellently with good quality control",
        },
        "health_suggestions": [
            "Improve evaluation standard consistency",
            "Strengthen cognitive bias control",
            "Increase sample size to improve reliability",
        ],
        "error_messages": {
            "no_history": "No evaluation history available for analysis",
            "evaluation_error": "Meta-evaluation error: {error}",
        },
    },
    # ============== Status and Labels ==============
    "status": {
        "trends": {
            "improving": "Improving",
            "declining": "Declining",
            "stable": "Stable",
            "insufficient_data": "Insufficient Data",
        },
        "stability": {
            "very_stable": "Very Stable",
            "moderately_stable": "Moderately Stable",
            "unstable": "Unstable",
            "unknown": "Unknown",
        },
        "quality": {"excellent": "Excellent", "good": "Good", "fair": "Fair", "poor": "Poor"},
        "system": {
            "error": "Error",
            "fallback": "Fallback",
            "empty_content": "Empty Content",
            "empty_evaluation_history": "Empty Evaluation History",
        },
    },
    # ============== Common Messages ==============
    "common": {
        "errors": {
            "generation_error": "Error during generation: {error}",
            "evaluation_error": "Error during evaluation: {error}",
            "llm_connection_error": "LLM connection failed: {error}",
            "invalid_format": "Invalid return format",
            "missing_required_field": "Missing required field: {field}",
        },
        "warnings": {
            "using_fallback": "Using fallback approach",
            "reduced_functionality": "Running with reduced functionality",
            "cache_miss": "Cache miss",
        },
        "info": {
            "processing": "Processing...",
            "completed": "Processing completed",
            "saved_successfully": "Saved successfully",
            "loaded_from_cache": "Loaded from cache",
        },
    },
}

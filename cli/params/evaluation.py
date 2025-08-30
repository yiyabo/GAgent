"""Evaluation system parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Dict, Any, Optional, List


class EvaluationParamsHandler:
    """Handler for evaluation system parameters following SRP."""
    
    GROUP_NAME = "Evaluation System"
    
    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add evaluation system arguments to parser."""
        group = parser.add_argument_group(EvaluationParamsHandler.GROUP_NAME)
        
        # Evaluation execution commands
        group.add_argument(
            '--eval-config',
            type=int,
            help='Configure evaluation settings for task ID'
        )
        group.add_argument(
            '--eval-execute',
            type=int,
            help='Execute task with basic evaluation'
        )
        group.add_argument(
            '--eval-llm',
            type=int,
            help='Execute task with LLM intelligent evaluation'
        )
        group.add_argument(
            '--eval-multi-expert',
            type=int,
            help='Execute task with multi-expert evaluation'
        )
        group.add_argument(
            '--eval-adversarial',
            type=int,
            help='Execute task with adversarial evaluation'
        )
        
        # Evaluation management commands
        group.add_argument(
            '--eval-history',
            type=int,
            help='View evaluation history for task ID'
        )
        group.add_argument(
            '--eval-override',
            type=int,
            help='Override evaluation result for task ID'
        )
        group.add_argument(
            '--eval-clear',
            type=int,
            help='Clear evaluation history for task ID'
        )
        group.add_argument(
            '--eval-stats',
            action='store_true',
            help='Show evaluation system statistics'
        )
        group.add_argument(
            '--eval-batch',
            action='store_true',
            help='Run batch evaluation'
        )
        
        # Supervision system commands
        group.add_argument(
            '--eval-supervision',
            action='store_true',
            help='Show evaluation supervision report'
        )
        group.add_argument(
            '--eval-supervision-config',
            action='store_true',
            help='Configure supervision thresholds'
        )
        
        # Evaluation configuration parameters
        config_group = parser.add_argument_group('Evaluation Configuration')
        config_group.add_argument(
            '--threshold',
            type=float,
            default=0.8,
            help='Quality threshold for evaluation (default: 0.8)'
        )
        config_group.add_argument(
            '--max-iterations',
            type=int,
            default=3,
            help='Maximum iterations for evaluation (default: 3)'
        )
        config_group.add_argument(
            '--max-rounds',
            type=int,
            default=3,
            help='Maximum rounds for adversarial evaluation (default: 3)'
        )
        config_group.add_argument(
            '--improvement-threshold',
            type=float,
            default=0.1,
            help='Improvement threshold for adversarial evaluation (default: 0.1)'
        )
        config_group.add_argument(
            '--experts',
            type=str,
            help='Comma-separated list of experts for multi-expert evaluation'
        )
        
        # Evaluation mode flags
        mode_group = parser.add_argument_group('Evaluation Mode Flags')
        mode_group.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose evaluation output'
        )
        mode_group.add_argument(
            '--detailed',
            action='store_true',
            help='Show detailed evaluation information'
        )
        mode_group.add_argument(
            '--strict',
            action='store_true',
            help='Enable strict evaluation mode'
        )
        mode_group.add_argument(
            '--domain-specific',
            action='store_true',
            help='Enable domain-specific evaluation'
        )
        
        # Supervision threshold parameters
        supervision_group = parser.add_argument_group('Supervision Thresholds')
        supervision_group.add_argument(
            '--min-accuracy',
            type=float,
            help='Minimum accuracy threshold for supervision'
        )
        supervision_group.add_argument(
            '--min-consistency',
            type=float,
            help='Minimum consistency threshold for supervision'
        )
        supervision_group.add_argument(
            '--max-bias-risk',
            type=float,
            help='Maximum bias risk threshold for supervision'
        )
        supervision_group.add_argument(
            '--min-cache-hit-rate',
            type=float,
            help='Minimum cache hit rate threshold for supervision'
        )
        supervision_group.add_argument(
            '--max-error-rate',
            type=float,
            help='Maximum error rate threshold for supervision'
        )
        supervision_group.add_argument(
            '--max-evaluation-time',
            type=float,
            help='Maximum evaluation time threshold for supervision'
        )
        supervision_group.add_argument(
            '--min-confidence',
            type=float,
            help='Minimum confidence threshold for supervision'
        )
    
    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract evaluation parameter values from parsed args."""
        values = {}
        
        # Evaluation execution commands (with task IDs)
        eval_commands = [
            'eval_config', 'eval_execute', 'eval_llm', 'eval_multi_expert', 
            'eval_adversarial', 'eval_history', 'eval_override', 'eval_clear'
        ]
        for cmd in eval_commands:
            if hasattr(args, cmd):
                value = getattr(args, cmd)
                if value is not None:
                    values[cmd] = value
        
        # Boolean evaluation commands
        bool_commands = [
            'eval_stats', 'eval_batch', 'eval_supervision', 'eval_supervision_config'
        ]
        for cmd in bool_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True
        
        # Configuration parameters
        config_attrs = [
            'threshold', 'max_iterations', 'max_rounds', 
            'improvement_threshold', 'experts'
        ]
        for attr in config_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value
        
        # Mode flags
        mode_flags = ['verbose', 'detailed', 'strict', 'domain_specific']
        for flag in mode_flags:
            if hasattr(args, flag) and getattr(args, flag):
                values[flag] = True
        
        # Supervision thresholds
        supervision_attrs = [
            'min_accuracy', 'min_consistency', 'max_bias_risk',
            'min_cache_hit_rate', 'max_error_rate', 'max_evaluation_time',
            'min_confidence'
        ]
        for attr in supervision_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value
        
        return values
    
    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate evaluation parameter combinations."""
        # Threshold validations
        threshold = values.get('threshold')
        if threshold is not None and (threshold < 0.0 or threshold > 1.0):
            return False, "Threshold must be between 0.0 and 1.0"
        
        improvement_threshold = values.get('improvement_threshold')
        if improvement_threshold is not None and (improvement_threshold < 0.0 or improvement_threshold > 1.0):
            return False, "Improvement threshold must be between 0.0 and 1.0"
        
        # Iteration/round validations
        max_iterations = values.get('max_iterations')
        if max_iterations is not None and (max_iterations <= 0 or max_iterations > 10):
            return False, "Max iterations must be between 1 and 10"
        
        max_rounds = values.get('max_rounds')
        if max_rounds is not None and (max_rounds <= 0 or max_rounds > 10):
            return False, "Max rounds must be between 1 and 10"
        
        # Experts validation
        experts = values.get('experts')
        if experts is not None:
            expert_list = [e.strip() for e in experts.split(',') if e.strip()]
            if len(expert_list) == 0:
                return False, "Experts list cannot be empty"
            if len(expert_list) > 5:
                return False, "Too many experts (maximum: 5)"
        
        # Supervision threshold validations
        supervision_thresholds = [
            'min_accuracy', 'min_consistency', 'min_cache_hit_rate', 'min_confidence'
        ]
        for attr in supervision_thresholds:
            value = values.get(attr)
            if value is not None and (value < 0.0 or value > 1.0):
                return False, f"{attr.replace('_', ' ').title()} must be between 0.0 and 1.0"
        
        risk_thresholds = ['max_bias_risk', 'max_error_rate']
        for attr in risk_thresholds:
            value = values.get(attr)
            if value is not None and (value < 0.0 or value > 1.0):
                return False, f"{attr.replace('_', ' ').title()} must be between 0.0 and 1.0"
        
        max_eval_time = values.get('max_evaluation_time')
        if max_eval_time is not None and max_eval_time <= 0:
            return False, "Maximum evaluation time must be positive"
        
        return True, None
    
    @staticmethod
    def has_evaluation_operation(args) -> bool:
        """Check if any evaluation operation is requested."""
        eval_ops = [
            'eval_config', 'eval_execute', 'eval_llm', 'eval_multi_expert',
            'eval_adversarial', 'eval_history', 'eval_override', 'eval_stats',
            'eval_clear', 'eval_batch', 'eval_supervision', 'eval_supervision_config'
        ]
        return any(hasattr(args, attr) and getattr(args, attr) for attr in eval_ops)
    
    @staticmethod
    def parse_experts_list(experts_str: str) -> List[str]:
        """Parse comma-separated experts string into list."""
        if not experts_str:
            return []
        return [e.strip() for e in experts_str.split(',') if e.strip()]
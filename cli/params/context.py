"""Context management parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Dict, Any, Optional


class ContextParamsHandler:
    """Handler for context management parameters following SRP."""
    
    GROUP_NAME = "Context Management"
    
    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add context management arguments to parser."""
        group = parser.add_argument_group(ContextParamsHandler.GROUP_NAME)
        
        # Basic context control
        group.add_argument(
            '--use-context', 
            action='store_true',
            help='Enable context-aware task execution'
        )
        
        # Dependency inclusion control
        group.add_argument(
            '--include-deps', 
            dest='include_deps',
            action='store_true',
            help='Include dependency tasks in context'
        )
        group.add_argument(
            '--exclude-deps', 
            dest='include_deps',
            action='store_false',
            help='Exclude dependency tasks from context'
        )
        parser.set_defaults(include_deps=None)
        
        # Plan sibling inclusion control
        group.add_argument(
            '--include-plan', 
            dest='include_plan',
            action='store_true',
            help='Include plan sibling tasks in context'
        )
        group.add_argument(
            '--exclude-plan', 
            dest='include_plan',
            action='store_false',
            help='Exclude plan sibling tasks from context'
        )
        parser.set_defaults(include_plan=None)
        
        # Semantic retrieval parameters
        semantic_group = parser.add_argument_group('Semantic Retrieval')
        semantic_group.add_argument(
            '--semantic-k', 
            dest='semantic_k',
            type=int,
            help='Number of semantically similar tasks to retrieve'
        )
        semantic_group.add_argument(
            '--min-similarity', 
            dest='min_similarity',
            type=float,
            help='Minimum similarity threshold (0.0-1.0)'
        )
        
        # Budget control parameters
        budget_group = parser.add_argument_group('Context Budget Control')
        budget_group.add_argument(
            '--max-chars', 
            dest='max_chars',
            type=int,
            help='Maximum character limit for context'
        )
        budget_group.add_argument(
            '--per-section-max', 
            dest='per_section_max',
            type=int,
            help='Maximum characters per section'
        )
        budget_group.add_argument(
            '--strategy', 
            choices=['truncate', 'sentence'],
            help='Context truncation strategy'
        )
        
        # Context snapshot parameters
        snapshot_group = parser.add_argument_group('Context Snapshots')
        snapshot_group.add_argument(
            '--save-snapshot', 
            dest='save_snapshot',
            action='store_true',
            help='Save context snapshot for reproducibility'
        )
        snapshot_group.add_argument(
            '--label', 
            type=str,
            help='Label for the context snapshot'
        )
        snapshot_group.add_argument(
            '--list-snapshots', 
            action='store_true',
            help='List context snapshots for a task'
        )
        snapshot_group.add_argument(
            '--export-snapshot', 
            action='store_true',
            help='Export context snapshot to file'
        )
    
    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract context parameter values from parsed args."""
        values = {}
        
        # Basic context control
        if hasattr(args, 'use_context') and getattr(args, 'use_context'):
            values['use_context'] = True
        
        # Inclusion control
        if hasattr(args, 'include_deps') and args.include_deps is not None:
            values['include_deps'] = bool(args.include_deps)
        if hasattr(args, 'include_plan') and args.include_plan is not None:
            values['include_plan'] = bool(args.include_plan)
        
        # Semantic retrieval
        semantic_attrs = ['semantic_k', 'min_similarity']
        for attr in semantic_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value
        
        # Budget control
        budget_attrs = ['max_chars', 'per_section_max', 'strategy']
        for attr in budget_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value
        
        # Snapshot control
        if hasattr(args, 'save_snapshot') and getattr(args, 'save_snapshot'):
            values['save_snapshot'] = True
        if hasattr(args, 'label') and getattr(args, 'label'):
            values['label'] = getattr(args, 'label')
        
        # Snapshot operations
        snapshot_ops = ['list_snapshots', 'export_snapshot']
        for op in snapshot_ops:
            if hasattr(args, op) and getattr(args, op):
                values[op] = True
        
        return values
    
    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate context parameter combinations."""
        # Semantic retrieval validation
        semantic_k = values.get('semantic_k')
        if semantic_k is not None and (semantic_k <= 0 or semantic_k > 100):
            return False, "Semantic K must be between 1 and 100"
        
        min_similarity = values.get('min_similarity')
        if min_similarity is not None and (min_similarity < 0.0 or min_similarity > 1.0):
            return False, "Minimum similarity must be between 0.0 and 1.0"
        
        # Budget validation
        max_chars = values.get('max_chars')
        if max_chars is not None and max_chars <= 0:
            return False, "Max characters must be positive"
        
        per_section_max = values.get('per_section_max')
        if per_section_max is not None and per_section_max <= 0:
            return False, "Per-section max must be positive"
        
        # Snapshot operations require task_id
        snapshot_ops = ['list_snapshots', 'export_snapshot']
        if any(values.get(op) for op in snapshot_ops):
            # This validation would need task_id from another parameter group
            pass  # Will be handled by the main validator
        
        return True, None
    
    @staticmethod
    def build_context_options(values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build context options dict from parameter values."""
        if not values.get('use_context'):
            return None
        
        options = {}
        
        # Copy relevant values to context options
        context_keys = [
            'include_deps', 'include_plan', 'semantic_k', 'min_similarity',
            'max_chars', 'per_section_max', 'strategy', 'save_snapshot', 'label'
        ]
        
        for key in context_keys:
            if key in values:
                options[key] = values[key]
        
        return options if options else None
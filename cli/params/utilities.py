"""Utility operation parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Dict, Any, Optional


class UtilityParamsHandler:
    """Handler for utility operation parameters following SRP."""
    
    GROUP_NAME = "Utility Operations"
    
    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add utility operation arguments to parser."""
        
        # Index management commands
        index_group = parser.add_argument_group('Global Index Management')
        index_group.add_argument(
            '--index-preview', 
            action='store_true',
            help='Preview generated INDEX.md content without saving'
        )
        index_group.add_argument(
            '--index-export', 
            type=str,
            help='Export INDEX.md to specified path'
        )
        index_group.add_argument(
            '--index-run-root', 
            action='store_true',
            help='Generate and persist INDEX.md with history tracking'
        )
        
        # Embedding management commands
        embed_group = parser.add_argument_group('Embedding Management')
        embed_group.add_argument(
            '--generate-embeddings', 
            action='store_true',
            help='Generate embeddings for all completed tasks'
        )
        embed_group.add_argument(
            '--embedding-stats', 
            action='store_true',
            help='Show embedding service statistics and performance'
        )
        embed_group.add_argument(
            '--rebuild-embeddings', 
            action='store_true',
            help='Rebuild all embeddings from scratch (DESTRUCTIVE)'
        )
        embed_group.add_argument(
            '--embedding-batch-size', 
            dest='embedding_batch_size',
            type=int,
            default=10,
            help='Batch size for embedding generation (default: 10)'
        )
    
    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract utility parameter values from parsed args."""
        values = {}
        
        # Index operations
        index_commands = ['index_preview', 'index_run_root']
        for cmd in index_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True
        
        # Index export (has a path value)
        if hasattr(args, 'index_export') and getattr(args, 'index_export'):
            values['index_export'] = getattr(args, 'index_export')
        
        # Embedding operations
        embed_commands = ['generate_embeddings', 'embedding_stats', 'rebuild_embeddings']
        for cmd in embed_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True
        
        # Embedding configuration
        if hasattr(args, 'embedding_batch_size'):
            value = getattr(args, 'embedding_batch_size')
            if value is not None:
                values['embedding_batch_size'] = value
        
        return values
    
    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate utility parameter combinations."""
        # Index export path validation
        index_export = values.get('index_export')
        if index_export:
            if not index_export.strip():
                return False, "Index export path cannot be empty"
            if len(index_export) > 255:
                return False, "Index export path too long (max 255 characters)"
            if not index_export.endswith('.md'):
                return False, "Index export path must end with .md"
        
        # Embedding batch size validation
        batch_size = values.get('embedding_batch_size')
        if batch_size is not None and (batch_size <= 0 or batch_size > 100):
            return False, "Embedding batch size must be between 1 and 100"
        
        return True, None
    
    @staticmethod
    def has_utility_operation(args) -> bool:
        """Check if any utility operation is requested."""
        utility_ops = [
            'index_preview', 'index_export', 'index_run_root',
            'generate_embeddings', 'embedding_stats', 'rebuild_embeddings'
        ]
        return any(hasattr(args, attr) and getattr(args, attr) for attr in utility_ops)
"""Test the refactored CLI parameter system."""

import pytest
from cli.parser_v2 import ModularCLIParser, LegacyCompatibilityWrapper


class TestModularCLIParser:
    """Test the new modular CLI parser implementation."""
    
    def test_parser_initialization(self):
        """Test that the modular parser initializes correctly."""
        parser = ModularCLIParser()
        assert parser is not None
        assert len(parser.handlers) == 6  # We have 6 parameter handlers
    
    def test_basic_argument_parsing(self):
        """Test basic argument parsing functionality."""
        parser = ModularCLIParser()
        
        # Test goal argument parsing
        args = parser.parse_args(['--goal', 'Test goal'])
        assert args.goal == 'Test goal'
    
    def test_parameter_extraction_and_validation(self):
        """Test parameter extraction and validation."""
        parser = ModularCLIParser()
        
        # Valid parameters
        args = parser.parse_args(['--goal', 'Test goal', '--sections', '5'])
        all_params, error = parser.extract_and_validate_params(args)
        
        assert error is None
        assert 'core' in all_params
        assert 'plan' in all_params
        assert all_params['core']['goal'] == 'Test goal'
        assert all_params['plan']['sections'] == 5
    
    def test_parameter_validation_errors(self):
        """Test parameter validation catches errors."""
        parser = ModularCLIParser()
        
        # Invalid sections value (too high)
        args = parser.parse_args(['--sections', '25'])
        all_params, error = parser.extract_and_validate_params(args)
        
        assert error is not None
        assert "Section count must be between 1 and 20" in error
    
    def test_operation_type_detection(self):
        """Test operation type detection."""
        parser = ModularCLIParser()
        
        # Test goal operation
        args = parser.parse_args(['--goal', 'Test goal'])
        op_type = parser.determine_operation_type(args)
        assert op_type == 'plan'
        
        # Test database operation
        args = parser.parse_args(['--db-info'])
        op_type = parser.determine_operation_type(args)
        assert op_type == 'database'
        
        # Test evaluation operation
        args = parser.parse_args(['--eval-stats'])
        op_type = parser.determine_operation_type(args)
        assert op_type == 'evaluation'
    
    def test_context_options_building(self):
        """Test context options building."""
        parser = ModularCLIParser()
        
        args = parser.parse_args([
            '--use-context',
            '--semantic-k', '10',
            '--min-similarity', '0.7'
        ])
        
        all_params, error = parser.extract_and_validate_params(args)
        assert error is None
        
        context_options = parser.get_context_options(all_params)
        assert context_options is not None
        assert context_options['semantic_k'] == 10
        assert context_options['min_similarity'] == 0.7


class TestLegacyCompatibilityWrapper:
    """Test the legacy compatibility wrapper."""
    
    def test_wrapper_compatibility(self):
        """Test that the wrapper maintains compatibility with existing code."""
        wrapper = LegacyCompatibilityWrapper()
        
        # Test basic parsing
        args = wrapper.parse_args(['--goal', 'Test goal'])
        assert args.goal == 'Test goal'
        
        # Test context building (legacy interface)
        args = wrapper.parse_args(['--use-context', '--semantic-k', '5'])
        context_options = wrapper.build_from_args(args)
        assert context_options is not None
        assert context_options['semantic_k'] == 5
    
    def test_validation_error_handling(self):
        """Test that validation errors are properly raised."""
        wrapper = LegacyCompatibilityWrapper()
        
        # Invalid parameter combination
        args = wrapper.parse_args(['--sections', '100'])  # Too high
        
        with pytest.raises(ValueError) as exc_info:
            wrapper.build_from_args(args)
        
        assert "Parameter validation failed" in str(exc_info.value)


class TestParameterGroupSeparation:
    """Test that parameter groups are properly separated."""
    
    def test_core_params_isolation(self):
        """Test that core parameters are isolated correctly."""
        parser = ModularCLIParser()
        args = parser.parse_args(['--goal', 'Test', '--yes', '--schedule', 'dag'])
        all_params, _ = parser.extract_and_validate_params(args)
        
        core_params = all_params.get('core', {})
        assert 'goal' in core_params
        assert 'yes' in core_params
        assert 'schedule' in core_params
        assert core_params['schedule'] == 'dag'
    
    def test_evaluation_params_isolation(self):
        """Test that evaluation parameters are isolated correctly."""
        parser = ModularCLIParser()
        args = parser.parse_args([
            '--eval-stats', 
            '--threshold', '0.9',
            '--max-iterations', '5'
        ])
        all_params, _ = parser.extract_and_validate_params(args)
        
        eval_params = all_params.get('evaluation', {})
        assert 'eval_stats' in eval_params
        assert eval_params['threshold'] == 0.9
        assert eval_params['max_iterations'] == 5
    
    def test_cross_parameter_validation(self):
        """Test cross-parameter validation works."""
        parser = ModularCLIParser()
        
        # Snapshot export without label should fail
        args = parser.parse_args(['--export-snapshot', '--task-id', '123'])
        all_params, error = parser.extract_and_validate_params(args)
        
        assert error is not None
        assert "export-snapshot requires --label" in error
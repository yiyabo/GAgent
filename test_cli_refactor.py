#!/usr/bin/env python3
"""
Test script for the refactored CLI architecture.

This script validates that the new modular CLI structure works correctly.
"""

import sys
import os
from unittest.mock import patch, MagicMock

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli.main import ModernCLIApp
from cli.commands.rerun_commands import RerunCommands
from cli.parser import CLIParser, DefaultContextOptionsBuilder


def test_cli_parser():
    """Test that CLI parser works correctly."""
    print("🧪 Testing CLI Parser...")
    
    parser = CLIParser()
    
    # Test help generation
    try:
        args = parser.parse_args(['--help'])
        print("❌ Help should have caused SystemExit")
        return False
    except SystemExit:
        print("✅ Help output works")
    
    # Test rerun argument parsing
    args = parser.parse_args(['--rerun-task', '123', '--use-context'])
    
    assert args.rerun_task == 123, f"Expected 123, got {args.rerun_task}"
    assert args.use_context == True, f"Expected True, got {args.use_context}"
    
    print("✅ CLI Parser tests passed")
    return True


def test_context_options_builder():
    """Test context options builder."""
    print("\\n🧪 Testing Context Options Builder...")
    
    builder = DefaultContextOptionsBuilder()
    
    # Mock args object
    class MockArgs:
        def __init__(self):
            self.use_context = True
            self.include_deps = True
            self.semantic_k = 5
            self.max_chars = 1000
            self.save_snapshot = True
            self.label = "test"
    
    args = MockArgs()
    options = builder.build_from_args(args)
    
    assert options is not None, "Options should not be None"
    assert options.get('include_deps') == True, "Should include deps"
    assert options.get('semantic_k') == 5, "Should have semantic_k=5"
    assert options.get('max_chars') == 1000, "Should have max_chars=1000"
    assert options.get('save_snapshot') == True, "Should save snapshot"
    assert options.get('label') == "test", "Should have label"
    
    print("✅ Context Options Builder tests passed")
    return True


def test_rerun_commands():
    """Test rerun commands structure."""
    print("\\n🧪 Testing Rerun Commands...")
    
    cmd = RerunCommands()
    
    assert cmd.name == "rerun", f"Expected 'rerun', got {cmd.name}"
    assert "rerun" in cmd.description.lower(), "Description should mention rerun"
    
    action_map = cmd.get_action_map()
    expected_actions = ['rerun_task', 'rerun_subtree', 'rerun_interactive']
    
    for action in expected_actions:
        assert action in action_map, f"Missing action: {action}"
    
    print("✅ Rerun Commands tests passed")
    return True


def test_cli_app():
    """Test CLI application structure."""
    print("\\n🧪 Testing CLI Application...")
    
    app = ModernCLIApp()
    
    # Check that commands are registered
    assert len(app.commands) > 0, "Should have registered commands"
    
    # Check rerun command is present
    rerun_cmd = app._get_command_by_name("rerun")
    assert rerun_cmd is not None, "Should have rerun command"
    assert isinstance(rerun_cmd, RerunCommands), "Should be RerunCommands instance"
    
    print("✅ CLI Application tests passed")
    return True


def test_argument_routing():
    """Test that arguments are routed to correct commands."""
    print("\\n🧪 Testing Argument Routing...")
    
    app = ModernCLIApp()
    
    # Mock the execute method to avoid actual execution
    with patch.object(RerunCommands, 'execute', return_value=0) as mock_execute:
        # Test rerun-task routing
        result = app.run(['--rerun-task', '123'])
        
        assert result == 0, f"Expected 0, got {result}"
        mock_execute.assert_called_once()
    
    print("✅ Argument Routing tests passed")
    return True


def test_error_handling():
    """Test error handling in commands."""
    print("\\n🧪 Testing Error Handling...")
    
    cmd = RerunCommands()
    
    # Mock args without required fields
    class MockArgs:
        def __init__(self):
            self.rerun_task = None
    
    args = MockArgs()
    
    # This should return non-zero exit code due to missing required args
    result = cmd.handle_single_task(args)
    assert result == 1, f"Expected 1 (error), got {result}"
    
    print("✅ Error Handling tests passed")
    return True


def run_all_tests():
    """Run all tests."""
    print("🚀 Starting CLI Refactor Tests...")
    
    tests = [
        test_cli_parser,
        test_context_options_builder, 
        test_rerun_commands,
        test_cli_app,
        test_argument_routing,
        test_error_handling
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                print(f"❌ {test.__name__} failed")
        except Exception as e:
            print(f"❌ {test.__name__} failed with exception: {e}")
    
    print(f"\\n📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! CLI refactor is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
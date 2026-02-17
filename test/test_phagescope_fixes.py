"""
PhageScope Bug Fixes Unit Tests

Tests for:
- Bug #3: Module list parsing with ast.literal_eval()
- Bug #6: Task status polling logic with data validation
- Bug #7: Download path reconstruction from uploadpath
"""

import pytest
import sys
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tool_box.tools_impl.phagescope import (
    _parse_modulelist,
    _module_completed,
    _infer_result_kind_from_path,
    RESULT_KIND_TO_FILENAME,
    DOWNLOAD_TSV_FALLBACKS,
)


class TestParseModulelist:
    """Tests for Bug #3: Module list parsing with ast.literal_eval()"""
    
    def test_parse_standard_python_list(self):
        """Test parsing standard Python list with single quotes"""
        result = _parse_modulelist("['quality', 'host', 'annotation']")
        assert result == ["quality", "host", "annotation"]
    
    def test_parse_list_with_apostrophe(self):
        """Test parsing list containing apostrophe in string content - Bug #3 fix"""
        # This is the key test case for Bug #3 - strings with apostrophes
        result = _parse_modulelist("['quality', \"user's module\", 'annotation']")
        assert result == ["quality", "user's module", "annotation"]
    
    def test_parse_json_array(self):
        """Test parsing JSON array with double quotes"""
        result = _parse_modulelist('["quality", "host", "annotation"]')
        assert result == ["quality", "host", "annotation"]
    
    def test_parse_empty_list(self):
        """Test parsing empty list"""
        result = _parse_modulelist("[]")
        assert result == []
    
    def test_parse_none(self):
        """Test parsing None value"""
        result = _parse_modulelist(None)
        assert result == []
    
    def test_parse_empty_string(self):
        """Test parsing empty string"""
        result = _parse_modulelist("")
        assert result == []
    
    def test_parse_whitespace_string(self):
        """Test parsing whitespace-only string"""
        result = _parse_modulelist("   ")
        assert result == []
    
    def test_parse_invalid_json(self):
        """Test parsing invalid JSON falls back gracefully"""
        result = _parse_modulelist("not a list")
        assert result == []
    
    def test_parse_nested_quotes(self):
        """Test parsing list with nested quotes - Bug #3 fix verification"""
        result = _parse_modulelist("['module1', \"it's a test\", 'module3']")
        assert result == ["module1", "it's a test", "module3"]
    
    def test_parse_single_item_list(self):
        """Test parsing single item list"""
        result = _parse_modulelist("['quality']")
        assert result == ["quality"]
    
    def test_parse_tuple_like_list(self):
        """Test parsing tuple-like list"""
        result = _parse_modulelist("('quality', 'host')")
        assert result == ["quality", "host"]


class TestModuleCompleted:
    """Tests for Bug #6: Task status polling logic with data validation"""
    
    def test_module_completed_success_with_data(self):
        """Test module completed with valid data"""
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_status": "Success",
                    "result": {"data": "some_data"}
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is True
    
    def test_module_completed_success_empty_data(self):
        """Test module completed but data is empty - Bug #6 fix verification"""
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_status": "Success",
                    "result": None
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        # Should still return True for backward compatibility but log warning
        assert result is True
    
    def test_module_completed_failed(self):
        """Test module failed"""
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_status": "Failed",
                    "error": "Some error occurred"
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is False
    
    def test_module_completed_running(self):
        """Test module still running"""
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_status": "Running"
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is None
    
    def test_module_completed_not_found(self):
        """Test module not found in task_que"""
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_status": "Success"
                }
            ]
        }
        result = _module_completed(task_detail, "annotation")
        assert result is None
    
    def test_module_completed_empty_task_que(self):
        """Test empty task_que"""
        task_detail = {
            "task_que": []
        }
        result = _module_completed(task_detail, "quality")
        assert result is None
    
    def test_module_completed_missing_task_que(self):
        """Test missing task_que"""
        task_detail = {}
        result = _module_completed(task_detail, "quality")
        assert result is None
    
    def test_module_completed_invalid_task_detail(self):
        """Test invalid task_detail type"""
        result = _module_completed(None, "quality")
        assert result is None
        
        result = _module_completed("not a dict", "quality")
        assert result is None
    
    def test_module_completed_empty_module_name(self):
        """Test empty module name"""
        task_detail = {"task_que": []}
        result = _module_completed(task_detail, "")
        assert result is None
    
    def test_module_completed_case_insensitive(self):
        """Test case insensitive module matching"""
        task_detail = {
            "task_que": [
                {
                    "module": "Quality",
                    "module_status": "Success"
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is True
    
    def test_module_completed_alternative_status_keys(self):
        """Test alternative status keys (module_satus typo, status)"""
        # Test with typo key "module_satus"
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_satus": "Success"
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is True
        
        # Test with "status" key
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "status": "Completed"
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is True
    
    def test_module_completed_various_success_statuses(self):
        """Test various success status values"""
        success_statuses = ["COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"]
        
        for status in success_statuses:
            task_detail = {
                "task_que": [
                    {
                        "module": "quality",
                        "module_status": status
                    }
                ]
            }
            result = _module_completed(task_detail, "quality")
            assert result is True, f"Expected True for status '{status}'"
    
    def test_module_completed_with_uploadpath_data(self):
        """Test module completed with uploadpath data field"""
        task_detail = {
            "task_que": [
                {
                    "module": "quality",
                    "module_status": "Success",
                    "uploadpath": "/workspace/user_task/123/output/result/"
                }
            ]
        }
        result = _module_completed(task_detail, "quality")
        assert result is True


class TestDownloadPathReconstruction:
    """Tests for Bug #7: Download path reconstruction constants and mappings"""
    
    def test_result_kind_to_filename_mapping_exists(self):
        """Test that RESULT_KIND_TO_FILENAME mapping exists"""
        assert isinstance(RESULT_KIND_TO_FILENAME, dict)
        assert len(RESULT_KIND_TO_FILENAME) > 0
    
    def test_download_tsv_fallbacks_exists(self):
        """Test that DOWNLOAD_TSV_FALLBACKS mapping exists"""
        assert isinstance(DOWNLOAD_TSV_FALLBACKS, dict)
        assert len(DOWNLOAD_TSV_FALLBACKS) > 0
    
    def test_result_kind_to_filename_coverage(self):
        """Test that RESULT_KIND_TO_FILENAME covers common result kinds"""
        expected_kinds = ["phage", "proteins", "quality", "modules", "tree", "phagefasta"]
        for kind in expected_kinds:
            assert kind in RESULT_KIND_TO_FILENAME, f"Missing mapping for '{kind}'"
    
    def test_result_kind_to_filename_values(self):
        """Test that filename values are valid"""
        for kind, filename in RESULT_KIND_TO_FILENAME.items():
            assert isinstance(filename, str)
            assert len(filename) > 0
            # Filename should have an extension
            assert "." in filename
    
    def test_download_tsv_fallbacks_format(self):
        """Test that fallback paths are properly formatted"""
        for path, kind in DOWNLOAD_TSV_FALLBACKS.items():
            assert isinstance(path, str)
            assert isinstance(kind, str)
            assert path.startswith("/")
            assert ".tsv" in path

    def test_infer_result_kind_prefers_exact_filename(self):
        """Avoid partial-key collisions such as phagefasta -> phage."""
        assert _infer_result_kind_from_path("/output/result/phage.fasta") == "phagefasta"
        assert _infer_result_kind_from_path("/output/result/phage_detail.json") == "phage_detail"
        assert _infer_result_kind_from_path("/output/result/protein.tsv") == "proteins"

    def test_infer_result_kind_uses_fallback_when_unknown(self):
        """Return fallback kind when path cannot be inferred."""
        assert _infer_result_kind_from_path("/unknown/path/result.bin", fallback_kind="phage") == "phage"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

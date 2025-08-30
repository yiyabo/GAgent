"""File operation utilities for CLI."""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional


class FileUtils:
    """Utilities for file operations."""
    
    @staticmethod
    def ensure_utf8_encoding() -> None:
        """Ensure UTF-8 encoding for stdio to handle Chinese characters."""
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
        if hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8', errors='ignore')
    
    @staticmethod
    def open_in_editor(file_path: str) -> None:
        """Open a file in the user's preferred editor."""
        path = Path(file_path)
        
        # Try different editors in order of preference
        editors = [
            os.environ.get('EDITOR'),
            'code',      # VS Code
            'vim',       # Vim
            'nano',      # Nano
            'notepad',   # Windows Notepad
        ]
        
        for editor in editors:
            if not editor:
                continue
                
            try:
                subprocess.run([editor, str(path)], check=True)
                return
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
        
        print(f"⚠️  Could not open editor. Please manually edit: {path}")
    
    @staticmethod
    def read_file_safe(file_path: str) -> Optional[str]:
        """Safely read a file with UTF-8 encoding."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            print(f"❌ File not found: {file_path}")
            return None
        except Exception as e:
            print(f"❌ Error reading file {file_path}: {e}")
            return None
    
    @staticmethod
    def write_file_safe(file_path: str, content: str) -> bool:
        """Safely write content to a file with UTF-8 encoding."""
        try:
            # Ensure parent directory exists
            parent = os.path.dirname(file_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"❌ Error writing file {file_path}: {e}")
            return False
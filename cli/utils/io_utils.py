"""Input/Output utilities for CLI."""

import sys
from typing import List, Optional


class IOUtils:
    """Utilities for user input/output operations."""
    
    @staticmethod
    def safe_input(prompt: str) -> str:
        """Safely get user input with UTF-8 encoding."""
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n⚠️  Operation cancelled by user.")
            sys.exit(1)
        except UnicodeDecodeError:
            print("❌ Unicode decode error. Please check your terminal encoding.")
            return ""
    
    @staticmethod
    def confirm(message: str, default: bool = True) -> bool:
        """Ask user for yes/no confirmation."""
        suffix = " [Y/n]" if default else " [y/N]"
        response = IOUtils.safe_input(f"{message}{suffix}: ")
        
        if not response:
            return default
        
        return response.lower() in ['y', 'yes', '是', '是的']
    
    @staticmethod
    def select_from_list(items: List[str], prompt: str = "选择项目") -> Optional[int]:
        """Display a list and let user select an item by number."""
        if not items:
            print("❌ No items to select from.")
            return None
        
        print(f"\n{prompt}:")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item}")
        
        while True:
            try:
                choice = IOUtils.safe_input("请输入数字 (或按回车取消): ")
                if not choice:
                    return None
                
                index = int(choice) - 1
                if 0 <= index < len(items):
                    return index
                else:
                    print(f"❌ 请输入 1 到 {len(items)} 之间的数字")
            except ValueError:
                print("❌ 请输入有效的数字")
    
    @staticmethod
    def print_success(message: str) -> None:
        """Print a success message."""
        print(f"✅ {message}")
    
    @staticmethod
    def print_error(message: str) -> None:
        """Print an error message."""
        print(f"❌ {message}")
    
    @staticmethod
    def print_warning(message: str) -> None:
        """Print a warning message."""
        print(f"⚠️  {message}")
    
    @staticmethod
    def print_info(message: str) -> None:
        """Print an info message."""
        print(f"ℹ️  {message}")
    
    @staticmethod
    def print_section(title: str) -> None:
        """Print a section header."""
        print(f"\n=== {title} ===")
    
    @staticmethod
    def print_task_list(tasks: List[dict]) -> None:
        """Print a formatted list of tasks."""
        for task in tasks:
            task_id = task.get('id', 'N/A')
            name = task.get('name', 'No name')
            status = task.get('status', 'unknown')
            priority = task.get('priority', 'N/A')
            print(f"  [{task_id}] {name} (状态: {status}, 优先级: {priority})")
"""
Abstract interfaces for CLI commands following the Command Pattern.
"""

from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, Optional


class CLICommand(ABC):
    """Base interface for all CLI commands."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Command name for identification."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Command description for help text."""
        pass
    
    @abstractmethod
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Add command-specific arguments to the parser."""
        pass
    
    @abstractmethod
    def execute(self, args: Namespace) -> int:
        """Execute the command. Returns 0 for success, non-zero for failure."""
        pass


class ContextOptionsBuilder(ABC):
    """Interface for building context options from CLI arguments."""
    
    @abstractmethod
    def build_from_args(self, args: Namespace) -> Optional[Dict[str, Any]]:
        """Build context options dictionary from parsed arguments."""
        pass


class CLIApplication(ABC):
    """Interface for the main CLI application."""
    
    @abstractmethod
    def register_command(self, command: CLICommand) -> None:
        """Register a command with the application."""
        pass
    
    @abstractmethod
    def run(self, args: Optional[list] = None) -> int:
        """Run the CLI application."""
        pass
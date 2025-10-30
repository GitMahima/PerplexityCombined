"""
Matrix Testing CLI Launcher
Simple wrapper to run matrix testing without module import issues
"""
import sys
from pathlib import Path

# Add project root to sys.path so myQuant package can be imported
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Now import and run with proper package structure
if __name__ == "__main__":
    from myQuant.live.matrix_forward_test import main
    main()

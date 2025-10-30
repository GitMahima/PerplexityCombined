"""
fix_imports.py - Convert all non-relative imports to relative imports in myQuant package
"""
import re
from pathlib import Path

# Mapping of directory locations to their relative import prefixes
IMPORT_MAPPINGS = {
    'myQuant/core': {
        'from utils.': 'from ..utils.',
        'from core.': 'from .',
        'from config.': 'from ..config.',
        'from live.': 'from ..live.',
    },
    'myQuant/live': {
        'from utils.': 'from ..utils.',
        'from core.': 'from ..core.',
        'from config.': 'from ..config.',
        'from live.': 'from .',
    },
    'myQuant/utils': {
        'from utils.': 'from .',
        'from core.': 'from ..core.',
        'from config.': 'from ..config.',
        'from live.': 'from ..live.',
    },
}

def fix_file_imports(file_path: Path):
    """Fix imports in a single file based on its location."""
    # Determine which mapping to use
    relative_path = str(file_path).replace('\\', '/')
    
    mapping = None
    for dir_pattern, dir_mapping in IMPORT_MAPPINGS.items():
        if dir_pattern in relative_path:
            mapping = dir_mapping
            break
    
    if not mapping:
        return False
    
    # Read file
    content = file_path.read_text(encoding='utf-8')
    original_content = content
    
    # Apply replacements
    for old_import, new_import in mapping.items():
        content = content.replace(old_import, new_import)
    
    # Write back if changed
    if content != original_content:
        file_path.write_text(content, encoding='utf-8')
        print(f"✓ Fixed: {relative_path}")
        return True
    return False

def main():
    """Fix all Python files in myQuant package."""
    myquant_dir = Path('myQuant')
    
    if not myquant_dir.exists():
        print("ERROR: myQuant directory not found!")
        return
    
    # Find all Python files
    python_files = list(myquant_dir.rglob('*.py'))
    
    fixed_count = 0
    for py_file in python_files:
        if '__pycache__' in str(py_file):
            continue
        if fix_file_imports(py_file):
            fixed_count += 1
    
    print(f"\n✅ Fixed {fixed_count} files")

if __name__ == '__main__':
    main()

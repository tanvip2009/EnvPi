#!/usr/bin/env python3
"""
Docx to Markdown Converter - Wrapper
Uses WordToMarkdownConverter from word_to_markdown_converter to convert .docx to .md.
Usage: python docx_to_md_converter.py <document.docx> [-o output_dir]
"""

import sys
from pathlib import Path

# Use the existing word-to-markdown converter
from word_to_markdown_converter import WordToMarkdownConverter


def main():
    if len(sys.argv) < 2:
        print("Usage: python docx_to_md_converter.py <document.docx> [-o output_dir]")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_dir = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            output_dir = sys.argv[idx + 1]
    
    path = Path(input_path)
    if not path.exists():
        print(f"Error: Input path does not exist: {path}")
        sys.exit(1)
    
    converter = WordToMarkdownConverter(input_path, output_dir)
    if path.is_file():
        success = converter.convert_file(path)
        sys.exit(0 if success else 1)
    else:
        success_count, failed_count = converter.convert_directory()
        sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()

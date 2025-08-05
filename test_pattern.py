#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append('.')

from notebridge import matches_pattern, sync_rules

def test_pattern_matching():
    """测试模式匹配"""
    print("🔍 测试模式匹配...")
    
    print(f"同步规则: {sync_rules}")
    
    # 测试Readwise文件夹
    test_folders = [
        "Readwise",
        "微信读书", 
        "Todo",
        "未分类"
    ]
    
    print("\n📝 测试文件夹匹配:")
    for folder in test_folders:
        matches_obsidian_to_joplin = any(matches_pattern(folder, pattern) for pattern in sync_rules['obsidian_to_joplin_only'])
        matches_joplin_to_obsidian = any(matches_pattern(folder, pattern) for pattern in sync_rules['joplin_to_obsidian_only'])
        
        print(f"  {folder}:")
        print(f"    obsidian_to_joplin_only: {matches_obsidian_to_joplin}")
        print(f"    joplin_to_obsidian_only: {matches_joplin_to_obsidian}")

if __name__ == "__main__":
    test_pattern_matching() 
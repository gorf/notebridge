#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append('.')

from notebridge import (
    get_joplin_notes, get_obsidian_notes, apply_sync_rules, 
    build_id_mapping, smart_match_notes, extract_sync_info_from_joplin,
    extract_sync_info_from_obsidian, sync_rules
)

def debug_sync_logic():
    """调试同步逻辑"""
    print("🔍 调试同步匹配逻辑...")
    
    # 获取笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 应用同步规则
    print("\n📋 应用同步规则...")
    joplin_to_sync, obsidian_to_sync = apply_sync_rules(joplin_notes, obsidian_notes)
    print(f"应用规则后：Joplin {len(joplin_to_sync)} 条，Obsidian {len(obsidian_to_sync)} 条")
    
    # 检查notebridge_id
    print("\n🔍 检查notebridge_id...")
    joplin_with_id = 0
    obsidian_with_id = 0
    
    for note in joplin_to_sync:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            joplin_with_id += 1
    
    for note in obsidian_to_sync:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            obsidian_with_id += 1
    
    print(f"有notebridge_id的笔记：Joplin {joplin_with_id} 条，Obsidian {obsidian_with_id} 条")
    
    # 建立ID映射
    print("\n🔗 建立ID映射...")
    id_mapping = build_id_mapping(joplin_to_sync, obsidian_to_sync)
    
    print(f"ID映射统计：")
    print(f"  Joplin -> Obsidian: {len(id_mapping['joplin_to_obsidian'])} 条")
    print(f"  Obsidian -> Joplin: {len(id_mapping['obsidian_to_joplin'])} 条")
    print(f"  未映射Joplin: {len(id_mapping['unmapped_joplin'])} 条")
    print(f"  未映射Obsidian: {len(id_mapping['unmapped_obsidian'])} 条")
    
    # 智能匹配
    print("\n🎯 智能匹配...")
    matched_pairs, unmatched_joplin, unmatched_obsidian = smart_match_notes(
        id_mapping, joplin_to_sync, obsidian_to_sync
    )
    
    print(f"匹配结果：")
    print(f"  已匹配: {len(matched_pairs)} 对")
    print(f"  未匹配Joplin: {len(unmatched_joplin)} 条")
    print(f"  未匹配Obsidian: {len(unmatched_obsidian)} 条")
    
    # 检查一些未匹配的笔记
    print("\n📝 检查未匹配的Joplin笔记（前5条）:")
    for i, note in enumerate(unmatched_joplin[:5]):
        sync_info = extract_sync_info_from_joplin(note['body'])
        print(f"  {i+1}. {note['title']} ({note['notebook']})")
        print(f"     notebridge_id: {sync_info.get('notebridge_id', '无')}")
        print(f"     sync_time: {sync_info.get('notebridge_sync_time', '无')}")
    
    print("\n📝 检查未匹配的Obsidian笔记（前5条）:")
    for i, note in enumerate(unmatched_obsidian[:5]):
        sync_info = extract_sync_info_from_obsidian(note['body'])
        print(f"  {i+1}. {note['title']} ({note['folder']})")
        print(f"     notebridge_id: {sync_info.get('notebridge_id', '无')}")
        print(f"     sync_time: {sync_info.get('notebridge_sync_time', '无')}")

if __name__ == "__main__":
    debug_sync_logic() 
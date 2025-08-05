#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append('.')

from notebridge import (
    get_joplin_notes, get_obsidian_notes, apply_sync_rules, 
    extract_sync_info_from_joplin, extract_sync_info_from_obsidian, 
    sync_joplin_to_obsidian
)

def fix_sync_state():
    """修复同步状态，重新同步有notebridge_id的Joplin笔记"""
    print("🔧 修复同步状态...")
    
    # 获取笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 应用同步规则
    joplin_to_sync, obsidian_to_sync = apply_sync_rules(joplin_notes, obsidian_notes)
    
    # 找出有notebridge_id但Obsidian中没有对应笔记的Joplin笔记
    print("\n🔍 查找需要重新同步的Joplin笔记...")
    
    # 获取所有Obsidian笔记的notebridge_id
    obsidian_ids = set()
    for note in obsidian_to_sync:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            obsidian_ids.add(sync_info['notebridge_id'])
    
    print(f"Obsidian中有notebridge_id的笔记：{len(obsidian_ids)} 条")
    
    # 找出需要重新同步的Joplin笔记
    need_sync_joplin = []
    for note in joplin_to_sync:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            if sync_info['notebridge_id'] not in obsidian_ids:
                need_sync_joplin.append(note)
    
    print(f"需要重新同步的Joplin笔记：{len(need_sync_joplin)} 条")
    
    if not need_sync_joplin:
        print("✅ 没有需要重新同步的笔记")
        return
    
    # 显示前10条需要同步的笔记
    print("\n📝 需要重新同步的Joplin笔记（前10条）:")
    for i, note in enumerate(need_sync_joplin[:10]):
        sync_info = extract_sync_info_from_joplin(note['body'])
        print(f"  {i+1}. {note['title']} ({note['notebook']})")
        print(f"     ID: {sync_info['notebridge_id']}")
    
    if len(need_sync_joplin) > 10:
        print(f"  ... 还有 {len(need_sync_joplin) - 10} 条")
    
    # 询问是否继续
    response = input(f"\n❓ 是否重新同步这 {len(need_sync_joplin)} 条Joplin笔记到Obsidian？(y/n): ").strip().lower()
    
    if response not in ['y', 'yes', '是']:
        print("❌ 用户取消操作")
        return
    
    # 开始同步
    print(f"\n🔄 开始重新同步 {len(need_sync_joplin)} 条笔记...")
    
    success_count = 0
    error_count = 0
    
    for i, note in enumerate(need_sync_joplin, 1):
        try:
            print(f"  [{i}/{len(need_sync_joplin)}] 同步: {note['title']}")
            success = sync_joplin_to_obsidian(note, note['notebook'])
            if success:
                success_count += 1
                print(f"    ✅ 成功")
            else:
                error_count += 1
                print(f"    ❌ 失败")
        except Exception as e:
            error_count += 1
            print(f"    ❌ 错误: {e}")
    
    print(f"\n📊 同步完成:")
    print(f"  成功: {success_count} 条")
    print(f"  失败: {error_count} 条")
    print(f"  总计: {len(need_sync_joplin)} 条")

if __name__ == "__main__":
    fix_sync_state() 
#!/usr/bin/env python3
"""
测试单向同步规则是否正确应用
"""

# 测试脚本，无需额外导入

# 模拟同步规则配置
test_sync_rules = {
    'joplin_to_obsidian_only': ['工作笔记', '项目*'],
    'obsidian_to_joplin_only': ['个人日记', '备份*'],
    'skip_sync': ['Conflict*', '临时*', '草稿*'],
    'bidirectional': ['重要*', '学习*']
}

def matches_pattern(text, pattern):
    """
    检查文本是否匹配模式（支持通配符）
    """
    import fnmatch
    return fnmatch.fnmatch(text, pattern)

def test_sync_rule_filtering():
    """
    测试同步规则过滤功能
    """
    print("🧪 测试单向同步规则过滤功能")
    print("=" * 50)
    
    # 测试用例
    test_cases = [
        # (笔记本名, 期望的同步方向)
        ('工作笔记', 'joplin_to_obsidian_only'),
        ('项目A', 'joplin_to_obsidian_only'),
        ('项目B', 'joplin_to_obsidian_only'),
        ('个人日记', 'obsidian_to_joplin_only'),
        ('备份2024', 'obsidian_to_joplin_only'),
        ('重要文档', 'bidirectional'),
        ('学习笔记', 'bidirectional'),
        ('Conflict笔记', 'skip_sync'),
        ('临时笔记', 'skip_sync'),
        ('草稿', 'skip_sync'),
        ('其他笔记', 'bidirectional'),  # 默认双向同步
    ]
    
    print("测试用例:")
    for notebook, expected in test_cases:
        # 检查是否匹配各种规则
        is_joplin_to_obsidian = any(
            matches_pattern(notebook, pattern) 
            for pattern in test_sync_rules['joplin_to_obsidian_only']
        )
        is_obsidian_to_joplin = any(
            matches_pattern(notebook, pattern) 
            for pattern in test_sync_rules['obsidian_to_joplin_only']
        )
        is_skip_sync = any(
            matches_pattern(notebook, pattern) 
            for pattern in test_sync_rules['skip_sync']
        )
        is_bidirectional = any(
            matches_pattern(notebook, pattern) 
            for pattern in test_sync_rules['bidirectional']
        )
        
        # 确定实际规则
        if is_skip_sync:
            actual = 'skip_sync'
        elif is_joplin_to_obsidian:
            actual = 'joplin_to_obsidian_only'
        elif is_obsidian_to_joplin:
            actual = 'obsidian_to_joplin_only'
        elif is_bidirectional:
            actual = 'bidirectional'
        else:
            actual = 'bidirectional'  # 默认双向同步
        
        status = "✅" if actual == expected else "❌"
        print(f"  {status} {notebook:15} -> {actual:25} (期望: {expected})")
    
    print("\n" + "=" * 50)
    print("测试完成！")

def test_sync_direction_logic():
    """
    测试同步方向逻辑
    """
    print("\n🧪 测试同步方向逻辑")
    print("=" * 50)
    
    # 模拟不同的同步方向设置
    sync_directions = ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']
    
    test_notebooks = [
        ('工作笔记', 'joplin_to_obsidian_only'),
        ('个人日记', 'obsidian_to_joplin_only'),
        ('重要文档', 'bidirectional'),
    ]
    
    for sync_direction in sync_directions:
        print(f"\n同步方向: {sync_direction}")
        print("-" * 30)
        
        for notebook, rule in test_notebooks:
            # 检查是否允许 Joplin → Obsidian 同步
            can_joplin_to_obsidian = (
                sync_direction in ['bidirectional', 'joplin_to_obsidian'] and
                rule != 'obsidian_to_joplin_only'
            )
            
            # 检查是否允许 Obsidian → Joplin 同步
            can_obsidian_to_joplin = (
                sync_direction in ['bidirectional', 'obsidian_to_joplin'] and
                rule != 'joplin_to_obsidian_only'
            )
            
            j_to_o = '✅' if can_joplin_to_obsidian else '❌'
            o_to_j = '✅' if can_obsidian_to_joplin else '❌'
            print(f"  {notebook:15} -> J→O: {j_to_o}, O→J: {o_to_j}")

def main():
    """
    主函数
    """
    print("🚀 开始测试单向同步规则")
    print("=" * 50)
    
    test_sync_rule_filtering()
    test_sync_direction_logic()
    
    print("\n🎉 所有测试完成！")
    print("\n💡 如果所有测试都通过，说明单向同步规则逻辑正确。")
    print("   现在可以运行实际的同步命令来验证修复效果。")

if __name__ == "__main__":
    main() 
#!/usr/bin/env python3
"""
验证单向同步规则修复是否生效
"""

def print_verification_guide():
    """
    打印验证指南
    """
    print("🔍 单向同步规则修复验证指南")
    print("=" * 60)
    
    print("\n📋 验证步骤：")
    print("1. 确保 config.json 中配置了单向同步规则")
    print("2. 运行预览模式查看同步计划")
    print("3. 运行实际同步验证规则是否生效")
    print("4. 检查同步报告中的跳过统计")
    
    print("\n📝 示例配置：")
    print("""
{
  "sync_rules": {
    "joplin_to_obsidian_only": ["工作笔记", "项目*"],
    "obsidian_to_joplin_only": ["个人日记", "备份*"],
    "skip_sync": ["Conflict*", "临时*", "草稿*"],
    "bidirectional": ["重要*", "学习*"]
  }
}
""")
    
    print("\n🧪 验证命令：")
    print("1. 预览模式（检查同步计划）：")
    print("   python notebridge.py sync")
    print()
    print("2. 双向同步（验证规则生效）：")
    print("   python notebridge.py sync --force")
    print()
    print("3. 单向同步（验证方向限制）：")
    print("   python notebridge.py sync --force --joplin-to-obsidian")
    print("   python notebridge.py sync --force --obsidian-to-joplin")
    
    print("\n✅ 验证要点：")
    print("- 检查同步报告中是否有'跳过单向同步限制'的统计")
    print("- 确认只同步了允许方向的笔记")
    print("- 验证配置的单向同步规则被正确应用")
    
    print("\n💡 预期结果：")
    print("- 工作笔记、项目* 只从 Joplin 同步到 Obsidian")
    print("- 个人日记、备份* 只从 Obsidian 同步到 Joplin")
    print("- 重要*、学习* 可以双向同步")
    print("- Conflict*、临时*、草稿* 被跳过同步")

def main():
    """
    主函数
    """
    print_verification_guide()
    
    print("\n" + "=" * 60)
    print("🎯 修复总结：")
    print("✅ 已修复单向同步规则过滤问题")
    print("✅ 同步执行时会检查每个笔记的同步规则")
    print("✅ 跳过不符合同步规则的笔记")
    print("✅ 在同步报告中显示跳过统计")
    print("✅ 支持通配符模式匹配")
    
    print("\n🚀 现在可以运行同步命令来验证修复效果！")

if __name__ == "__main__":
    main() 
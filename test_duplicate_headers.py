#!/usr/bin/env python3
"""
测试重复头部修复功能
"""

import re
import yaml
from datetime import datetime

def generate_sync_info(source):
    """
    生成同步信息
    """
    return {
        'notebridge_id': 'test-id-12345',
        'notebridge_sync_time': datetime.now().isoformat(),
        'notebridge_source': source,
        'notebridge_version': '1'
    }

def clean_duplicate_sync_info(content):
    """
    清理笔记内容中的重复同步信息，只保留最新的一个
    增强版：能更好地处理HTML注释和YAML格式混合的情况
    """
    # 提取所有同步信息
    joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', content)
    joplin_times = re.findall(r'<!-- notebridge_sync_time: ([^>]+) -->', content)
    
    # 提取YAML中的同步信息
    yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', content)
    yaml_times = re.findall(r'notebridge_sync_time: \'?([^\'\n]+)\'?', content)
    
    # 合并所有ID和时间
    all_ids = joplin_ids + yaml_ids
    all_times = joplin_times + yaml_times
    
    if len(all_ids) <= 1:
        return content  # 没有重复，直接返回
    
    # 找到最新的同步信息
    latest_time = ''
    latest_id = ''
    for i, sync_time in enumerate(all_times):
        if sync_time > latest_time:
            latest_time = sync_time
            latest_id = all_ids[i]
    
    if not latest_id:
        return content  # 没有有效的时间信息，直接返回
    
    # 清理所有旧的同步信息
    # 清理HTML注释中的同步信息（更彻底）
    content = re.sub(r'<!-- notebridge_id: [a-f0-9-]+ -->\s*', '', content)
    content = re.sub(r'<!-- notebridge_sync_time: [^>]+ -->\s*', '', content)
    content = re.sub(r'<!-- notebridge_source: [^>]+ -->\s*', '', content)
    content = re.sub(r'<!-- notebridge_version: [^>]+ -->\s*', '', content)
    
    # 清理YAML中的同步信息（更彻底）
    content = re.sub(r'notebridge_id: [a-f0-9-]+\s*\n', '', content)
    content = re.sub(r'notebridge_sync_time: \'?[^\'\n]+\'?\s*\n', '', content)
    content = re.sub(r'notebridge_source: [^\n]+\s*\n', '', content)
    content = re.sub(r'notebridge_version: [^\n]+\s*\n', '', content)
    
    # 清理可能的空行和多余的换行
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    content = re.sub(r'^\s*\n', '', content)
    
    # 判断内容类型：如果包含YAML frontmatter，则按Obsidian格式处理
    has_yaml = bool(re.search(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL))
    
    if has_yaml:
        # Obsidian格式，添加到YAML中
        latest_sync_info = generate_sync_info('obsidian')
        latest_sync_info['notebridge_id'] = latest_id
        latest_sync_info['notebridge_sync_time'] = latest_time
        content = add_sync_info_to_obsidian_content(content, latest_sync_info)
    else:
        # Joplin格式，添加到HTML注释中
        latest_sync_info = generate_sync_info('joplin')
        latest_sync_info['notebridge_id'] = latest_id
        latest_sync_info['notebridge_sync_time'] = latest_time
        content = add_sync_info_to_joplin_content(content, latest_sync_info)
    
    return content

def add_sync_info_to_joplin_content(content, sync_info):
    """
    在 Joplin 笔记内容中添加同步信息（避免重复）
    """
    # 先清理已存在的同步信息
    cleaned_content = clean_duplicate_sync_info(content)
    
    # 添加新的同步信息
    sync_header = f"""<!-- notebridge_id: {sync_info['notebridge_id']} -->
<!-- notebridge_sync_time: {sync_info['notebridge_sync_time']} -->
<!-- notebridge_source: {sync_info['notebridge_source']} -->
<!-- notebridge_version: {sync_info['notebridge_version']} -->

"""
    return sync_header + cleaned_content

def add_sync_info_to_obsidian_content(content, sync_info):
    """
    在 Obsidian 笔记内容中添加同步信息（YAML frontmatter，避免重复）
    """
    # 先清理已存在的同步信息
    cleaned_content = clean_duplicate_sync_info(content)
    
    # 检查是否已有 frontmatter
    if cleaned_content.startswith('---'):
        # 已有 frontmatter，在其中添加同步信息
        yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', cleaned_content, re.DOTALL)
        if yaml_match:
            try:
                frontmatter = yaml.safe_load(yaml_match.group(1))
                # 确保 frontmatter 是字典类型
                if not isinstance(frontmatter, dict):
                    frontmatter = {}
                # 更新同步信息（覆盖已存在的）
                frontmatter.update(sync_info)
                new_frontmatter = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
                return f"---\n{new_frontmatter}---\n\n" + cleaned_content[yaml_match.end():]
            except yaml.YAMLError:
                pass
    
    # 没有 frontmatter 或解析失败，创建新的
    frontmatter = yaml.dump(sync_info, default_flow_style=False, allow_unicode=True)
    return f"---\n{frontmatter}---\n\n{cleaned_content}"

def test_duplicate_headers():
    """
    测试重复头部修复功能
    """
    print("🧪 测试重复头部修复功能")
    print("=" * 50)
    
    # 测试用例1：HTML注释和YAML格式混合的重复头部
    test_content_1 = """<!-- notebridge_id: d2448462-94f5-471c-92eb-bd5c490d01b0 -->
<!-- notebridge_sync_time: 2025-08-06T04:45:16.424778 -->
<!-- notebridge_source: obsidian -->
<!-- notebridge_version: 1 -->

---

notebridge_id: d2448462-94f5-471c-92eb-bd5c490d01b0
notebridge_sync_time: '2025-08-06T04:45:16.424778'
notebridge_source: obsidian
notebridge_version: 1

---

# 测试笔记

这是测试内容。
"""
    
    print("测试用例1：HTML注释和YAML格式混合的重复头部")
    print("原始内容：")
    print(test_content_1)
    print("\n修复后：")
    cleaned_1 = clean_duplicate_sync_info(test_content_1)
    print(cleaned_1)
    print("-" * 50)
    
    # 测试用例2：只有HTML注释格式的重复
    test_content_2 = """<!-- notebridge_id: old-id-123 -->
<!-- notebridge_sync_time: 2024-01-01T00:00:00 -->
<!-- notebridge_source: joplin -->
<!-- notebridge_version: 1 -->

<!-- notebridge_id: new-id-456 -->
<!-- notebridge_sync_time: 2024-12-01T12:00:00 -->
<!-- notebridge_source: obsidian -->
<!-- notebridge_version: 1 -->

# 测试笔记

这是测试内容。
"""
    
    print("测试用例2：只有HTML注释格式的重复")
    print("原始内容：")
    print(test_content_2)
    print("\n修复后：")
    cleaned_2 = clean_duplicate_sync_info(test_content_2)
    print(cleaned_2)
    print("-" * 50)
    
    # 测试用例3：只有YAML格式的重复
    test_content_3 = """---
notebridge_id: old-id-123
notebridge_sync_time: '2024-01-01T00:00:00'
notebridge_source: joplin
notebridge_version: 1
notebridge_id: new-id-456
notebridge_sync_time: '2024-12-01T12:00:00'
notebridge_source: obsidian
notebridge_version: 1
---

# 测试笔记

这是测试内容。
"""
    
    print("测试用例3：只有YAML格式的重复")
    print("原始内容：")
    print(test_content_3)
    print("\n修复后：")
    cleaned_3 = clean_duplicate_sync_info(test_content_3)
    print(cleaned_3)
    print("-" * 50)
    
    print("✅ 测试完成！")

def main():
    """
    主函数
    """
    test_duplicate_headers()
    
    print("\n💡 使用说明：")
    print("如果测试通过，可以运行以下命令修复实际的重复头部：")
    print("  python notebridge.py fix-duplicate-headers")

if __name__ == "__main__":
    main() 
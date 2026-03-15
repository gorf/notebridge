import os
import json
import requests
from pathlib import Path
import glob
import sys
import hashlib
from difflib import SequenceMatcher
from fuzzywuzzy import fuzz
import uuid
import re
from datetime import datetime
import yaml
from tqdm import tqdm
import time
import fnmatch
import functools

# 1. 读取配置文件 config.json
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

# 2. 获取 Joplin API 信息
joplin_api_base = config['joplin']['api_base']
joplin_token = config['joplin']['token']

# 3. 获取 Obsidian 笔记库路径
obsidian_vault_path = config['obsidian']['vault_path']

# 4. 获取同步规则
sync_rules = config.get('sync_rules', {
    'joplin_to_obsidian_only': [],
    'obsidian_to_joplin_only': [],
    'skip_sync': [],
    'bidirectional': []
})

print("\n==== notebridge 启动成功 ====")
print(f"Joplin API 地址: {joplin_api_base}")
print(f"Obsidian 笔记库路径: {obsidian_vault_path}\n")

# 同步方向配置
SYNC_DIRECTION = 'bidirectional'  # 'bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin'

def handle_file_errors(func):
    """
    装饰器：处理文件操作中的常见错误
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except FileNotFoundError as e:
            print(f"⚠️ 文件不存在: {e}")
            return None
        except PermissionError as e:
            print(f"⚠️ 权限错误: {e}")
            return None
        except UnicodeDecodeError as e:
            print(f"⚠️ 编码错误: {e}")
            return None
        except Exception as e:
            print(f"⚠️ 文件操作错误: {e}")
            return None
    return wrapper

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
    # 优先选择有同步时间的，如果都没有同步时间，则选择第一个ID
    latest_time = ''
    latest_id = ''
    latest_source = ''
    latest_version = '1'
    
    # 提取所有source和version信息
    all_sources = re.findall(r'<!-- notebridge_source: ([^>]+) -->', content)
    all_sources += re.findall(r'notebridge_source: \'?([^\'\n]+)\'?', content)
    all_versions = re.findall(r'<!-- notebridge_version: ([^>]+) -->', content)
    all_versions += re.findall(r'notebridge_version: \'?([^\'\n]+)\'?', content)
    
    # 找到有效的同步时间最新的那个
    for i in range(len(all_ids)):
        sync_time = all_times[i] if i < len(all_times) else ''
        if sync_time and sync_time.strip() and sync_time > latest_time:
            latest_time = sync_time
            latest_id = all_ids[i]
            latest_source = all_sources[i] if i < len(all_sources) else ''
            latest_version = all_versions[i] if i < len(all_versions) else '1'
    
    # 如果所有时间都是空的，选择第一个有效ID
    if not latest_id:
        latest_id = all_ids[0] if all_ids else ''
        latest_source = all_sources[0] if all_sources else ''
        latest_version = all_versions[0] if all_versions else '1'
    
    if not latest_id:
        return content  # 没有有效的ID，直接返回
    
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
        latest_sync_info = generate_sync_info(latest_source if latest_source else 'obsidian')
        latest_sync_info['notebridge_id'] = latest_id
        if latest_time and latest_time.strip():
            latest_sync_info['notebridge_sync_time'] = latest_time
        if latest_source and latest_source.strip():
            latest_sync_info['notebridge_source'] = latest_source
        if latest_version:
            latest_sync_info['notebridge_version'] = latest_version
        content = add_sync_info_to_obsidian_content(content, latest_sync_info)
    else:
        # Joplin格式，添加到HTML注释中
        latest_sync_info = generate_sync_info(latest_source if latest_source else 'joplin')
        latest_sync_info['notebridge_id'] = latest_id
        if latest_time and latest_time.strip():
            latest_sync_info['notebridge_sync_time'] = latest_time
        if latest_source and latest_source.strip():
            latest_sync_info['notebridge_source'] = latest_source
        if latest_version:
            latest_sync_info['notebridge_version'] = latest_version
        content = add_sync_info_to_joplin_content(content, latest_sync_info)
    
    return content

def extract_sync_info_from_joplin(note_body):
    """
    从 Joplin 笔记内容中提取同步信息（修复多ID问题）
    """
    # 先清理重复的同步信息
    cleaned_body = clean_duplicate_sync_info(note_body)
    
    sync_info = {
        'notebridge_id': '',
        'notebridge_sync_time': '',
        'notebridge_source': '',
        'notebridge_version': '1'  # 默认版本
    }
    
    # 查找同步信息注释
    id_match = re.search(r'<!-- notebridge_id: ([a-f0-9-]+) -->', cleaned_body)
    if id_match:
        sync_info['notebridge_id'] = id_match.group(1)
    
    time_match = re.search(r'<!-- notebridge_sync_time: ([^>]+) -->', cleaned_body)
    if time_match:
        sync_info['notebridge_sync_time'] = time_match.group(1)
    
    source_match = re.search(r'<!-- notebridge_source: ([^>]+) -->', cleaned_body)
    if source_match:
        sync_info['notebridge_source'] = source_match.group(1)
    
    version_match = re.search(r'<!-- notebridge_version: ([^>]+) -->', cleaned_body)
    if version_match:
        sync_info['notebridge_version'] = version_match.group(1)
    
    return sync_info

def extract_sync_info_from_obsidian(content):
    """
    从 Obsidian 笔记内容中提取同步信息（支持YAML和HTML注释格式）
    确保返回完整的字段，即使某些字段缺失也会有默认值
    修复：先清理重复的同步信息，避免提取失败
    """
    # 先清理重复的同步信息（和 Joplin 端保持一致）
    cleaned_content = clean_duplicate_sync_info(content)
    
    sync_info = {
        'notebridge_id': '',
        'notebridge_sync_time': '',
        'notebridge_source': '',
        'notebridge_version': '1'  # 默认版本
    }
    
    # 1. 查找 YAML frontmatter
    yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', cleaned_content, re.DOTALL)
    if yaml_match:
        yaml_content = yaml_match.group(1)
        try:
            yaml_data = yaml.safe_load(yaml_content)
            if yaml_data and isinstance(yaml_data, dict):
                if yaml_data.get('notebridge_id'):
                    sync_info['notebridge_id'] = yaml_data.get('notebridge_id', '')
                if yaml_data.get('notebridge_sync_time'):
                    sync_info['notebridge_sync_time'] = yaml_data.get('notebridge_sync_time', '')
                if yaml_data.get('notebridge_source'):
                    sync_info['notebridge_source'] = yaml_data.get('notebridge_source', '')
                if yaml_data.get('notebridge_version'):
                    sync_info['notebridge_version'] = str(yaml_data.get('notebridge_version', '1'))
        except Exception:
            pass
    
    # 2. 查找 HTML 注释格式的同步信息（如果YAML中没有）
    # 查找 notebridge_id
    if not sync_info['notebridge_id']:
        id_match = re.search(r'<!--\s*notebridge_id:\s*([a-f0-9\-]+)\s*-->', cleaned_content)
        if id_match:
            sync_info['notebridge_id'] = id_match.group(1)
    
    # 查找 notebridge_sync_time
    if not sync_info['notebridge_sync_time']:
        time_match = re.search(
            r'<!--\s*notebridge_sync_time:\s*([^>]+)\s*-->', cleaned_content
        )
        if time_match:
            sync_info['notebridge_sync_time'] = time_match.group(1).strip()
    
    # 查找 notebridge_source
    if not sync_info['notebridge_source']:
        source_match = re.search(r'<!--\s*notebridge_source:\s*(\w+)\s*-->', cleaned_content)
        if source_match:
            sync_info['notebridge_source'] = source_match.group(1)
    
    # 查找 notebridge_version
    if not sync_info['notebridge_version'] or sync_info['notebridge_version'] == '1':
        version_match = re.search(r'<!--\s*notebridge_version:\s*(\d+)\s*-->', cleaned_content)
        if version_match:
            sync_info['notebridge_version'] = version_match.group(1)
    
    return sync_info

def detect_notebook_from_content(note_title, note_body):
    """
    根据笔记内容智能判断应该属于哪个笔记本
    """
    # 关键词映射
    keyword_mapping = {
        'Excalidraw': ['excalidraw', 'drawing', 'diagram', 'sketch', 'chart', 'mindmap'],
        'Readwise': ['readwise', 'highlight', 'bookmark', 'article', 'reading'],
        '工作笔记': ['工作', '项目', '任务', '会议', '报告', '计划'],
        '学习笔记': ['学习', '教程', '课程', '知识', '概念', '理论'],
        '生活笔记': ['生活', '日常', '日记', '感悟', '心情'],
        '技术笔记': ['技术', '编程', '代码', '开发', '算法', '框架'],
        '金融笔记': ['金融', '投资', '股票', '基金', '理财', '经济'],
        '文学笔记': ['诗', '词', '文学', '小说', '散文', '作者'],
        '历史笔记': ['历史', '古代', '朝代', '人物', '事件'],
        '哲学笔记': ['哲学', '思想', '理论', '观点', '思考']
    }
    
    # 检查标题和内容中的关键词
    content_lower = (note_title + ' ' + note_body).lower()
    
    for notebook, keywords in keyword_mapping.items():
        for keyword in keywords:
            if keyword in content_lower:
                return notebook
    
    # 如果没有匹配，返回默认笔记本
    return '未分类'

def get_joplin_notes():
    """
    通过 Joplin Web API 获取所有笔记的标题、内容和笔记本信息（支持多级嵌套）
    自动过滤掉 skip_sync 中指定的笔记本
    """
    notes = []
    page = 1
    while True:
        url = f"{joplin_api_base}/notes?token={joplin_token}&fields=id,title,body,parent_id,user_updated_time&page={page}"
        resp = requests.get(url)
        data = resp.json()
        notes.extend(data.get('items', []))
        if data.get('has_more', False):
            page += 1
        else:
            break
    
    # 获取所有笔记本信息（支持多级嵌套）
    notebooks = {}
    notebook_parents = {}
    page = 1
    while True:
        url = f"{joplin_api_base}/folders?token={joplin_token}&fields=id,title,parent_id&page={page}"
        resp = requests.get(url)
        data = resp.json()
        for notebook in data.get('items', []):
            notebooks[notebook['id']] = notebook['title']
            notebook_parents[notebook['id']] = notebook.get('parent_id', '')
        if data.get('has_more', False):
            page += 1
        else:
            break
    
    # 构建完整的笔记本路径
    def get_full_notebook_path(notebook_id):
        """获取笔记本的完整路径（支持多级嵌套）"""
        if not notebook_id or notebook_id not in notebooks:
            return '未分类'
        
        path_parts = [notebooks[notebook_id]]
        current_id = notebook_parents.get(notebook_id, '')
        
        # 向上遍历父级笔记本，构建完整路径
        visited = {notebook_id}  # 防止循环引用
        while current_id and current_id in notebooks and current_id not in visited:
            visited.add(current_id)
            path_parts.insert(0, notebooks[current_id])
            current_id = notebook_parents.get(current_id, '')
        
        return '/'.join(path_parts)
    
    # 为每条笔记添加完整的笔记本路径，并过滤掉 skip_sync 中的笔记本
    filtered_notes = []
    skipped_count = 0
    skipped_format_count = 0
    
    # 需要排除的特殊文件格式（这些格式无法在 Obsidian 中添加同步信息）
    excluded_formats = ['.excalidraw', '.canvas', '.drawio', '.dio']
    
    for note in notes:
        notebook_id = note.get('parent_id', '')
        notebook_path = get_full_notebook_path(notebook_id)
        
        # 检查是否匹配 skip_sync 模式
        should_skip = False
        for pattern in sync_rules['skip_sync']:
            if matches_pattern(notebook_path, pattern):
                should_skip = True
                skipped_count += 1
                break
        
        if should_skip:
            continue  # 跳过这个笔记
        
        # 检查是否是特殊格式文件（无法添加同步信息）
        note_title = note.get('title', '')
        if any(note_title.lower().endswith(format_ext) for format_ext in excluded_formats):
            skipped_format_count += 1
            continue  # 跳过特殊格式文件
        
        note['notebook'] = notebook_path
        note['notebook_path'] = note['notebook'].split('/')
        filtered_notes.append(note)
    
    if skipped_count > 0:
        print(f"📝 已过滤掉 {skipped_count} 条来自 skip_sync 笔记本的笔记")
    if skipped_format_count > 0:
        print(f"📝 已过滤掉 {skipped_format_count} 条特殊格式文件（无法添加同步信息）")
    
    return filtered_notes

# 6. 读取 Obsidian 文件夹下的所有 Markdown 文件
def get_obsidian_notes():
    """
    读取 Obsidian 笔记库下所有 .md 文件的标题、内容和文件夹信息
    自动过滤掉 skip_sync 中指定的文件夹
    """
    notes = []
    md_files = glob.glob(os.path.join(obsidian_vault_path, '**', '*.md'), recursive=True)
    
    print(f"发现 {len(md_files)} 个 Markdown 文件，正在读取...")
    
    skipped_count = 0
    
    for file_path in md_files:
        try:
            # 检查文件是否仍然存在（可能在扫描过程中被删除）
            if not os.path.exists(file_path):
                print(f"⚠️ 文件不存在，跳过: {file_path}")
                continue
                
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 获取相对路径作为文件夹信息
            rel_path = os.path.relpath(file_path, obsidian_vault_path)
            folder = os.path.dirname(rel_path)
            if folder == '.':
                folder = '根目录'
            else:
                # 统一转换为正斜杠格式，确保在Joplin中创建多级笔记本
                folder = folder.replace('\\', '/')
            
            # 检查是否匹配 skip_sync 模式
            should_skip = False
            for pattern in sync_rules['skip_sync']:
                if matches_pattern(folder, pattern):
                    should_skip = True
                    skipped_count += 1
                    break
            
            if should_skip:
                continue  # 跳过这个文件
            
            title = Path(file_path).stem  # 文件名作为标题
            
            # 检查是否是特殊格式文件（无法添加同步信息）
            excluded_formats = ['.excalidraw', '.canvas', '.drawio', '.dio']
            if any(title.lower().endswith(format_ext) for format_ext in excluded_formats):
                continue  # 跳过特殊格式文件
            
            # 自动修复超长标题（超过250字符）
            if len(title) > 250:
                original_title = title
                # 生成简短标题（保留240字符，留10字符给扩展名等）
                new_title = title[:240].strip('。，！？、 ')
                
                print(f"  ⚠️  发现超长标题，正在自动修复...")
                print(f"     原标题: {original_title[:60]}...")
                print(f"     新标题: {new_title}")
                
                try:
                    # 将原标题添加到内容开头
                    # 检查是否已有YAML frontmatter
                    import re
                    yaml_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
                    if yaml_match:
                        frontmatter = yaml_match.group(0)
                        remaining_content = content[yaml_match.end():]
                    else:
                        frontmatter = ''
                        remaining_content = content
                    
                    # 检查内容开头是否已有这个标题
                    remaining_content = remaining_content.strip()
                    if not remaining_content.startswith(f"# {original_title}"):
                        # 添加原标题作为一级标题
                        new_content = f"{frontmatter}# {original_title}\n\n{remaining_content}"
                    else:
                        new_content = content
                    
                    # 生成新文件名
                    directory = os.path.dirname(file_path)
                    new_filename = f"{new_title}.md"
                    new_path = os.path.join(directory, new_filename)
                    
                    # 如果新文件已存在，添加数字后缀
                    counter = 1
                    while os.path.exists(new_path) and new_path != file_path:
                        new_filename = f"{new_title}_{counter}.md"
                        new_path = os.path.join(directory, new_filename)
                        counter += 1
                    
                    # 写入新文件
                    with open(new_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    
                    # 删除旧文件（如果不是同一个文件）
                    if new_path != file_path:
                        os.remove(file_path)
                        print(f"     ✅ 已重命名: {os.path.basename(file_path)} → {new_filename}")
                        file_path = new_path  # 更新路径
                    else:
                        print(f"     ✅ 已更新内容")
                    
                    title = new_title
                    content = new_content
                    
                except Exception as e:
                    print(f"     ⚠️  自动修复失败: {e}，使用原标题")
                    # 修复失败，使用原标题（会被后续的标题长度限制截断）
            
            notes.append({
                'path': file_path, 
                'title': title, 
                'body': content,
                'folder': folder
            })
            
        except FileNotFoundError:
            print(f"⚠️ 文件不存在，跳过: {file_path}")
            continue
        except PermissionError:
            print(f"⚠️ 无权限读取文件，跳过: {file_path}")
            continue
        except UnicodeDecodeError as e:
            print(f"⚠️ 文件编码错误，跳过: {file_path} - {e}")
            continue
        except Exception as e:
            print(f"⚠️ 读取文件时出错，跳过: {file_path} - {e}")
            continue
    
    print(f"成功读取 {len(notes)} 个文件")
    if skipped_count > 0:
        print(f"📝 已过滤掉 {skipped_count} 个来自 skip_sync 文件夹的文件")
    return notes

# 7. 根据同步规则过滤笔记
def matches_pattern(text, pattern):
    """
    检查文本是否匹配通配符模式
    支持 * 和 ? 通配符
    * 匹配任意数量的字符
    ? 匹配单个字符
    """
    return fnmatch.fnmatch(text, pattern)

def apply_sync_rules(joplin_notes, obsidian_notes):
    """
    根据配置的同步规则过滤笔记（支持通配符模式匹配）
    注意：skip_sync 的过滤已在读取时完成，这里只处理其他同步规则
    """
    joplin_to_sync = []
    obsidian_to_sync = []
    
    # 处理 Joplin 笔记（skip_sync 已在读取时过滤）
    for note in joplin_notes:
        notebook = note['notebook']
        
        if any(matches_pattern(notebook, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
            joplin_to_sync.append(note)  # 只同步到 Obsidian
        elif any(matches_pattern(notebook, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
            continue  # 只从 Obsidian 同步过来，不从这里同步出去
        else:
            joplin_to_sync.append(note)  # 默认双向同步
    
    # 处理 Obsidian 笔记（skip_sync 已在读取时过滤）
    for note in obsidian_notes:
        folder = note['folder']
        
        if any(matches_pattern(folder, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
            obsidian_to_sync.append(note)  # 只同步到 Joplin
        elif any(matches_pattern(folder, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
            continue  # 只从 Joplin 同步过来，不从这里同步出去
        else:
            obsidian_to_sync.append(note)  # 默认双向同步
    
    return joplin_to_sync, obsidian_to_sync

# 8. 查重功能
def is_empty_note(content):
    """
    判断笔记是否为空（去除空白字符后）
    """
    if not content:
        return True
    # 去除空白字符后检查是否为空
    stripped_content = re.sub(r'\s+', '', content)
    return len(stripped_content) == 0

def calculate_content_hash(content):
    """
    计算内容的哈希值，用于快速识别完全重复的内容
    计算前会清理同步信息，确保能匹配到已同步但缺少同步信息的笔记
    """
    # 清理同步信息再计算哈希
    cleaned = clean_duplicate_sync_info(content)
    return hashlib.md5(cleaned.encode('utf-8')).hexdigest()

def parse_sync_time_to_timestamp(sync_time_str):
    """
    将同步时间字符串转换为时间戳（毫秒）
    """
    if not sync_time_str:
        return 0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(sync_time_str.replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)  # 转换为毫秒
    except:
        return 0

def format_timestamp_for_debug(timestamp_ms):
    """
    格式化时间戳用于调试显示
    """
    if timestamp_ms == 0:
        return "未知"
    try:
        from datetime import datetime
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return f"时间戳: {timestamp_ms}"

def should_treat_as_deletion(note, sync_info, is_joplin_note=False):
    """
    判断笔记是否应该作为删除处理（而不是重新同步）
    
    通过比较编辑时间和同步时间来判断：
    - 如果编辑时间比同步时间早，说明同步后未再修改 → 视为删除
    - 如果编辑时间与同步时间相差不超过 SYNC_TIME_TOLERANCE_MS，视为时间偏差 → 仍视为未修改
    - 只有当编辑时间明显晚于同步时间（超出容差），才认为“用户在删除后又修改了”，需要重新同步
    
    参数:
        note: 笔记对象（Joplin或Obsidian）
        sync_info: 同步信息字典
        is_joplin_note: 是否为Joplin笔记（True）还是Obsidian笔记（False）
    
    返回:
        True: 应该作为删除处理
        False: 应该作为新笔记重新同步
    """
    sync_time_str = sync_info.get('notebridge_sync_time', '')
    
    # 获取编辑时间
    if is_joplin_note:
        edit_time = note.get('user_updated_time', 0)  # 已经是毫秒
    else:
        try:
            edit_time = int(os.path.getmtime(note['path']) * 1000)  # 转换为毫秒
        except:
            edit_time = 0
    
    # 获取同步时间戳
    sync_timestamp = parse_sync_time_to_timestamp(sync_time_str)
    
    # 如果缺少任何时间信息，保守处理为“未修改”，当作删除
    if sync_timestamp == 0 or edit_time == 0:
        return True
    
    # 计算时间差（正值表示编辑时间晚于同步时间）
    delta = edit_time - sync_timestamp
    
    # 如果编辑时间比同步时间晚，但差值在容差范围内，视为网络/写入延迟，不算用户修改
    if delta > 0 and delta <= SYNC_TIME_TOLERANCE_MS:
        return True
    
    # 若编辑时间远大于同步时间，说明用户确实做过修改，需要重新同步
    if delta > SYNC_TIME_TOLERANCE_MS:
        return False
    
    # delta <= 0 说明编辑时间不晚于同步时间 → 未修改
    return True

def calculate_similarity(text1, text2):
    """
    计算两段文本的相似度（0-1之间）
    """
    return SequenceMatcher(None, text1, text2).ratio()

def find_duplicates(joplin_notes, obsidian_notes):
    """
    查找 Joplin 和 Obsidian 之间的重复笔记
    """
    duplicates = {
        'exact_duplicates': [],      # 完全重复（标题和内容都相同）
        'title_similar': [],         # 标题相似
        'content_similar': [],       # 内容相似
        'content_hash_duplicates': [] # 内容哈希相同
    }
    
    print("正在扫描重复内容...")
    
    # 1. 基于内容哈希的完全重复检测（排除空笔记）
    joplin_hashes = {}
    obsidian_hashes = {}
    
    for note in joplin_notes:
        if not is_empty_note(note['body']):  # 排除空笔记
            content_hash = calculate_content_hash(note['body'])
            joplin_hashes[content_hash] = note
    
    for note in obsidian_notes:
        if not is_empty_note(note['body']):  # 排除空笔记
            content_hash = calculate_content_hash(note['body'])
            obsidian_hashes[content_hash] = note
            if content_hash in joplin_hashes:
                duplicates['content_hash_duplicates'].append({
                    'joplin': joplin_hashes[content_hash],
                    'obsidian': note,
                    'similarity': 1.0
                })
    
    # 2. 基于标题相似度的检测（排除空笔记）
    for j_note in joplin_notes:
        for o_note in obsidian_notes:
            # 排除空笔记
            if is_empty_note(j_note['body']) or is_empty_note(o_note['body']):
                continue
                
            title_similarity = fuzz.ratio(j_note['title'], o_note['title']) / 100.0
            
            if title_similarity >= 0.8:  # 标题相似度超过80%
                content_similarity = calculate_similarity(j_note['body'], o_note['body'])
                
                if content_similarity >= 0.9:  # 内容相似度超过90%
                    duplicates['exact_duplicates'].append({
                        'joplin': j_note,
                        'obsidian': o_note,
                        'title_similarity': title_similarity,
                        'content_similarity': content_similarity
                    })
                elif title_similarity >= 0.9:  # 标题相似度超过90%
                    duplicates['title_similar'].append({
                        'joplin': j_note,
                        'obsidian': o_note,
                        'title_similarity': title_similarity,
                        'content_similarity': content_similarity
                    })
                elif content_similarity >= 0.7:  # 内容相似度超过70%
                    duplicates['content_similar'].append({
                        'joplin': j_note,
                        'obsidian': o_note,
                        'title_similarity': title_similarity,
                        'content_similarity': content_similarity
                    })
    
    return duplicates

def print_duplicate_report(duplicates):
    """
    打印查重报告
    """
    print("\n" + "="*50)
    print("📊 查重报告")
    print("="*50)
    
    # 基于notebridge_id的重复
    if duplicates.get('id_duplicates'):
        print(f"\n🆔 基于ID的重复笔记：{len(duplicates['id_duplicates'])} 对")
        for i, dup in enumerate(duplicates['id_duplicates'][:5], 1):
            dup_type = dup.get('duplicate_type', 'unknown')
            if dup_type == 'joplin_internal':
                print(f"  {i}. Joplin内部重复: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
            elif dup_type == 'obsidian_internal':
                print(f"  {i}. Obsidian内部重复: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
            else:
                print(f"  {i}. ID重复: {dup['joplin']['title']} <-> {dup['obsidian']['title']}")
        if len(duplicates['id_duplicates']) > 5:
            print(f"  ... 还有 {len(duplicates['id_duplicates']) - 5} 对")
    
    # 同步时间冲突
    if duplicates.get('sync_time_conflicts'):
        print(f"\n⏰ 同步时间冲突：{len(duplicates['sync_time_conflicts'])} 对")
        for i, dup in enumerate(duplicates['sync_time_conflicts'][:5], 1):
            print(f"  {i}. Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
            print(f"     Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
            print(f"     Joplin时间: {dup.get('joplin_time', 'N/A')}")
            print(f"     Obsidian时间: {dup.get('obsidian_time', 'N/A')}")
            print(f"     时间差: {dup.get('time_diff', 'N/A')} 秒")
        if len(duplicates['sync_time_conflicts']) > 5:
            print(f"  ... 还有 {len(duplicates['sync_time_conflicts']) - 5} 对")
    
    total_duplicates = len(duplicates.get('id_duplicates', [])) + len(duplicates.get('sync_time_conflicts', []))
    
    print(f"\n📈 总计发现 {total_duplicates} 对重复/冲突笔记")
    if total_duplicates > 0:
        print(f"💡 建议：运行 'python notebridge.py interactive-clean' 进行交互式清理")
        print(f"  或者运行 'python notebridge.py clean-duplicates' 进行自动清理")
    else:
        print(f"✅ 没有发现重复问题")
    print("="*50)

# 9. 防重复同步机制
def generate_sync_info(source):
    """
    生成新的同步信息（修复时间戳问题）
    """
    # 确保使用正确的时间，避免未来时间戳
    current_time = datetime.now()
    
    # 如果时间戳是未来时间，使用当前时间
    if current_time.year > 2024:
        # 可能是系统时间设置错误，使用一个合理的默认时间
        current_time = datetime.now().replace(year=2024)
    
    return {
        'notebridge_id': str(uuid.uuid4()),
        'notebridge_sync_time': current_time.isoformat(),
        'notebridge_source': source,
        'notebridge_version': '1'
    }

def build_id_mapping(joplin_notes, obsidian_notes):
    """
    建立 ID 映射关系（以notebridge_id为准，不依赖文件名）
    """
    id_mapping = {
        'joplin_to_obsidian': {},  # notebridge_id -> obsidian_path
        'obsidian_to_joplin': {},  # notebridge_id -> joplin_id
        'unmapped_joplin': [],     # 没有ID的Joplin笔记
        'unmapped_obsidian': [],   # 没有ID的Obsidian笔记
        'joplin_by_id': {},        # notebridge_id -> joplin_note_object
        'obsidian_by_id': {}       # notebridge_id -> obsidian_note_object
    }
    
    # 处理 Joplin 笔记
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            notebridge_id = sync_info['notebridge_id']
            id_mapping['obsidian_to_joplin'][notebridge_id] = note['id']
            id_mapping['joplin_by_id'][notebridge_id] = note
        else:
            id_mapping['unmapped_joplin'].append(note)
    
    # 处理 Obsidian 笔记
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            notebridge_id = sync_info['notebridge_id']
            id_mapping['joplin_to_obsidian'][notebridge_id] = note['path']
            id_mapping['obsidian_by_id'][notebridge_id] = note
        else:
            id_mapping['unmapped_obsidian'].append(note)
    
    return id_mapping

def smart_match_notes(id_mapping, joplin_notes, obsidian_notes):
    """
    智能匹配笔记，避免重复（完全基于notebridge_id，不依赖文件名）
    考虑单向同步规则
    返回: (matched_pairs, unmatched_joplin, unmatched_obsidian, deleted_candidates)
    deleted_candidates: 可能已删除的笔记列表，格式为 [{'type': 'joplin_deleted', 'note': obsidian_note, 'notebridge_id': id}, ...]
    """
    matched_pairs = []
    unmatched_joplin = []
    unmatched_obsidian = []
    deleted_candidates = []  # 收集可能已删除的笔记
    
    # 加载上次同步状态
    previous_state = load_sync_state()
    previous_joplin_ids = set()
    previous_obsidian_ids = set()
    
    if previous_state:
        previous_joplin_ids = set(previous_state['joplin_notes'].keys())
        previous_obsidian_ids = set(previous_state['obsidian_notes'].keys())
    
    # 1. 通过notebridge_id直接匹配（这是最可靠的方式）
    for notebridge_id in id_mapping['joplin_to_obsidian']:
        if notebridge_id in id_mapping['obsidian_to_joplin']:
            # 直接从映射中获取笔记对象
            joplin_note = id_mapping['joplin_by_id'].get(notebridge_id)
            obsidian_note = id_mapping['obsidian_by_id'].get(notebridge_id)
            
            if joplin_note and obsidian_note:
                matched_pairs.append({
                    'joplin': joplin_note,
                    'obsidian': obsidian_note,
                    'notebridge_id': notebridge_id,
                    'match_type': 'id'
                })
                print(f"  ✅ ID匹配: {joplin_note['title']} <-> {obsidian_note['title']}")
            else:
                print(f"  ⚠️ ID匹配失败: {notebridge_id}")
    
    # 2. 处理单向同步的笔记
    # 对于obsidian_to_joplin_only的笔记，不需要在Joplin中找到对应项
    # 对于joplin_to_obsidian_only的笔记，不需要在Obsidian中找到对应项
    
    # 3. 对未匹配的笔记进行内容匹配，但排除已在上次同步中的笔记
    unmatched_joplin_ids = set()
    unmatched_obsidian_paths = set()
    
    # 处理有 ID 但只在一边存在的笔记
    # 注意：joplin_to_obsidian 存的是 Obsidian 中有 ID 的笔记
    # obsidian_to_joplin 存的是 Joplin 中有 ID 的笔记
    
    # 遍历所有 Obsidian 中有 ID 的笔记
    for notebridge_id in id_mapping['joplin_to_obsidian']:
        if notebridge_id not in id_mapping['obsidian_to_joplin']:
            # Obsidian 有此 ID，但 Joplin 没有
            # 检查这个 ID 是否在上次同步中存在于两边
            if previous_state and notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids:
                # 已经同步过，但 Joplin 端可能丢失了 ID（比如重复头部问题）
                # 尝试通过内容匹配找到对应的 Joplin 笔记
                obsidian_note = id_mapping['obsidian_by_id'].get(notebridge_id)
                if obsidian_note:
                    # 计算 Obsidian 笔记的内容哈希
                    obsidian_content_hash = calculate_content_hash(obsidian_note['body'])
                    # 在所有 Joplin 笔记中查找内容匹配的笔记（包括没有 ID 的）
                    found_match = False
                    for joplin_note in joplin_notes:
                        # 跳过已经有 ID 的笔记（它们应该已经匹配了）
                        joplin_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
                        if joplin_sync_info.get('notebridge_id'):
                            continue
                        # 计算内容哈希并比较
                        joplin_content_hash = calculate_content_hash(joplin_note['body'])
                        if joplin_content_hash == obsidian_content_hash:
                            # 找到匹配的笔记，创建匹配对
                            matched_pairs.append({
                                'joplin': joplin_note,
                                'obsidian': obsidian_note,
                                'notebridge_id': notebridge_id,
                                'match_type': 'content_hash_recovery',
                                'needs_sync_info_update': True  # 需要更新 Joplin 端的同步信息
                            })
                            print(f"  ✅ 通过内容匹配恢复: {joplin_note['title']} <-> {obsidian_note['title']} (ID: {notebridge_id[:8]}...)")
                            found_match = True
                            # 从待匹配列表中移除
                            if joplin_note['id'] in unmatched_joplin_ids:
                                unmatched_joplin_ids.discard(joplin_note['id'])
                            break
                    if not found_match:
                        folder = obsidian_note.get('folder', '根目录')
                        # 检查笔记来源
                        obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
                        source = obsidian_sync_info.get('notebridge_source', '')
                        
                        # 如果来源是joplin，说明原本是从Joplin同步过来的，现在Joplin端没有了
                        if source == 'joplin':
                            if should_treat_as_deletion(obsidian_note, obsidian_sync_info, is_joplin_note=False):
                                print(f"  ⚠️ 检测到可能已删除的笔记（Joplin 端可能已删除）: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                deleted_candidates.append({
                                    'type': 'joplin_deleted',
                                    'note': obsidian_note,
                                    'notebridge_id': notebridge_id,
                                    'title': obsidian_note.get('title', 'Unknown')
                                })
                            else:
                                print(f"  🔁 检测到 Obsidian 笔记在同步后有修改，将重新同步到 Joplin: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                unmatched_obsidian_paths.add(obsidian_note['path'])
                        elif source == 'obsidian':
                            # 如果来源是obsidian，说明原本是从Obsidian同步到Joplin的，现在Joplin端删除了
                            # 需要区分两种情况：
                            # 1. 从未同步过（没有 notebridge_sync_time）→ 应该作为新笔记同步
                            # 2. 之前已同步过（有 notebridge_sync_time）→ 需要判断是删除还是重新同步
                            sync_time = obsidian_sync_info.get('notebridge_sync_time', '')
                            if sync_time:
                                # 有同步记录，说明之前已经同步过，现在 Joplin 端没有了
                                # 使用 should_treat_as_deletion 判断是删除还是重新同步
                                if should_treat_as_deletion(obsidian_note, obsidian_sync_info, is_joplin_note=False):
                                    print(f"  ⚠️ 此笔记在同步后未被修改，视为 Joplin 端删除: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                    deleted_candidates.append({
                                        'type': 'joplin_deleted',
                                        'note': obsidian_note,
                                        'notebridge_id': notebridge_id,
                                        'title': obsidian_note.get('title', 'Unknown')
                                    })
                                else:
                                    print(f"  🔁 检测到 Joplin 端删除了来自 Obsidian 的笔记，但此笔记在 Obsidian 端已修改，将重新同步到 Joplin: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                    unmatched_obsidian_paths.add(obsidian_note['path'])
                            else:
                                # 没有同步记录，说明从未同步过，应该作为新笔记同步
                                print(f"  🔁 检测到未同步的 Obsidian 笔记，将同步到 Joplin: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                unmatched_obsidian_paths.add(obsidian_note['path'])
                        elif any(matches_pattern(folder, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
                            # 单向同步文件夹且来源不明确，允许重新同步
                            print(f"  🔁 检测到 Obsidian 单向同步笔记缺失，将重新同步到 Joplin: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                            unmatched_obsidian_paths.add(obsidian_note['path'])
                        else:
                            # 来源不明确，依据编辑时间与同步时间判断
                            if should_treat_as_deletion(obsidian_note, obsidian_sync_info, is_joplin_note=False):
                                print(f"  ⚠️ 检测到可能已删除的笔记（Joplin 端可能已删除）: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                deleted_candidates.append({
                                    'type': 'joplin_deleted',
                                    'note': obsidian_note,
                                    'notebridge_id': notebridge_id,
                                    'title': obsidian_note.get('title', 'Unknown')
                                })
                            else:
                                print(f"  🔁 检测到 Obsidian 笔记在同步后有修改，将重新同步到 Joplin: {obsidian_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                unmatched_obsidian_paths.add(obsidian_note['path'])
                continue
            # 这是新笔记或者第一次同步，加入待同步列表
            unmatched_obsidian_paths.add(id_mapping['joplin_to_obsidian'][notebridge_id])
    
    # 遍历所有 Joplin 中有 ID 的笔记
    for notebridge_id in id_mapping['obsidian_to_joplin']:
        if notebridge_id not in id_mapping['joplin_to_obsidian']:
            # Joplin 有此 ID，但 Obsidian 没有
            # 检查这个 ID 是否在上次同步中存在于两边
            if previous_state and notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids:
                # 已经同步过，但 Obsidian 端可能丢失了 ID（比如重复头部问题）
                # 尝试通过内容匹配找到对应的 Obsidian 笔记
                joplin_note = id_mapping['joplin_by_id'].get(notebridge_id)
                if joplin_note:
                    # 计算 Joplin 笔记的内容哈希
                    joplin_content_hash = calculate_content_hash(joplin_note['body'])
                    # 在所有 Obsidian 笔记中查找内容匹配的笔记（包括没有 ID 的）
                    found_match = False
                    for obsidian_note in obsidian_notes:
                        # 跳过已经有 ID 的笔记（它们应该已经匹配了）
                        obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
                        if obsidian_sync_info.get('notebridge_id'):
                            continue
                        # 计算内容哈希并比较
                        obsidian_content_hash = calculate_content_hash(obsidian_note['body'])
                        if obsidian_content_hash == joplin_content_hash:
                            # 找到匹配的笔记，创建匹配对
                            matched_pairs.append({
                                'joplin': joplin_note,
                                'obsidian': obsidian_note,
                                'notebridge_id': notebridge_id,
                                'match_type': 'content_hash_recovery',
                                'needs_sync_info_update': True  # 需要更新 Obsidian 端的同步信息
                            })
                            print(f"  ✅ 通过内容匹配恢复: {joplin_note['title']} <-> {obsidian_note['title']} (ID: {notebridge_id[:8]}...)")
                            found_match = True
                            # 从待匹配列表中移除
                            if obsidian_note['path'] in unmatched_obsidian_paths:
                                unmatched_obsidian_paths.discard(obsidian_note['path'])
                            if joplin_note['id'] in unmatched_joplin_ids:
                                unmatched_joplin_ids.discard(joplin_note['id'])
                            break
                    if not found_match:
                        notebook = joplin_note.get('notebook', '未分类')
                        # 检查笔记来源
                        joplin_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
                        source = joplin_sync_info.get('notebridge_source', '')
                        
                        # 重要：即使时间戳变了，也要检查内容是否真的变了
                        # 通过内容哈希在 Obsidian 中查找，如果找到且内容相同，说明只是时间戳被更新，内容没变
                        joplin_content_hash = calculate_content_hash(joplin_note['body'])
                        content_match_found = False
                        for obsidian_note in obsidian_notes:
                            obsidian_content_hash = calculate_content_hash(obsidian_note['body'])
                            if obsidian_content_hash == joplin_content_hash:
                                # 找到内容相同的笔记，说明只是时间戳变了，内容没变
                                # 更新 Obsidian 端的同步信息即可，不需要重新同步
                                obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
                                if not obsidian_sync_info.get('notebridge_id'):
                                    # Obsidian 端缺少 ID，需要更新
                                    matched_pairs.append({
                                        'joplin': joplin_note,
                                        'obsidian': obsidian_note,
                                        'notebridge_id': notebridge_id,
                                        'match_type': 'content_hash_recovery',
                                        'needs_sync_info_update': True
                                    })
                                    print(f"  ✅ 通过内容匹配恢复（时间戳变化但内容未变）: {joplin_note['title']} <-> {obsidian_note['title']} (ID: {notebridge_id[:8]}...)")
                                else:
                                    # Obsidian 端已有 ID，说明只是时间戳不同，跳过
                                    print(f"  ⏭️ 跳过: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...) - 时间戳变化但内容未变")
                                content_match_found = True
                                break
                        
                        if content_match_found:
                            continue  # 已通过内容匹配处理，跳过后续逻辑
                        
                        # 如果来源是obsidian，说明原本是从Obsidian同步过来的，现在Obsidian端没有了
                        if source == 'obsidian':
                            # 需要区分两种情况：
                            # 1. 从未同步过（没有 notebridge_sync_time）→ 应该作为新笔记同步
                            # 2. 之前已同步过（有 notebridge_sync_time）→ 需要判断是删除还是重新同步
                            sync_time = joplin_sync_info.get('notebridge_sync_time', '')
                            if sync_time:
                                # 有同步记录，说明之前已经同步过，现在 Obsidian 端没有了
                                # 使用 should_treat_as_deletion 判断是删除还是重新同步
                                if should_treat_as_deletion(joplin_note, joplin_sync_info, is_joplin_note=True):
                                    print(f"  ⚠️ 检测到可能已删除的笔记（Obsidian 端可能已删除）: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                    deleted_candidates.append({
                                        'type': 'obsidian_deleted',
                                        'note': joplin_note,
                                        'notebridge_id': notebridge_id,
                                        'title': joplin_note.get('title', 'Unknown')
                                    })
                                else:
                                    # 获取时间信息用于调试
                                    edit_time = joplin_note.get('user_updated_time', 0)
                                    sync_timestamp = parse_sync_time_to_timestamp(joplin_sync_info.get('notebridge_sync_time', ''))
                                    edit_time_str = format_timestamp_for_debug(edit_time)
                                    sync_time_str = format_timestamp_for_debug(sync_timestamp)
                                    delta_ms = edit_time - sync_timestamp
                                    delta_str = f"{delta_ms / 1000:.1f}秒" if delta_ms > 0 else f"{abs(delta_ms) / 1000:.1f}秒（早于）"
                                    print(f"  🔁 检测到 Joplin 笔记在同步后有修改，将重新同步到 Obsidian: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                    print(f"     编辑时间: {edit_time_str}, 同步时间: {sync_time_str}, 时间差: {delta_str}")
                                    unmatched_joplin_ids.add(joplin_note['id'])
                            else:
                                # 没有同步记录，说明从未同步过，应该作为新笔记同步
                                print(f"  🔁 检测到未同步的 Obsidian 来源笔记，将同步到 Obsidian: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                unmatched_joplin_ids.add(joplin_note['id'])
                        elif any(matches_pattern(notebook, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
                            # 单向同步笔记本且来源不明确，允许重新同步
                            print(f"  🔁 检测到 Joplin 单向同步笔记缺失，将重新同步到 Obsidian: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                            unmatched_joplin_ids.add(joplin_note['id'])
                        else:
                            # 来源不明确，先检查内容是否真的变了
                            # 通过内容哈希在 Obsidian 中查找，如果找到且内容相同，说明只是时间戳被更新
                            joplin_content_hash = calculate_content_hash(joplin_note['body'])
                            content_match_found = False
                            for obsidian_note in obsidian_notes:
                                obsidian_content_hash = calculate_content_hash(obsidian_note['body'])
                                if obsidian_content_hash == joplin_content_hash:
                                    # 找到内容相同的笔记，说明只是时间戳变了，内容没变
                                    obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
                                    if not obsidian_sync_info.get('notebridge_id'):
                                        # Obsidian 端缺少 ID，需要更新
                                        matched_pairs.append({
                                            'joplin': joplin_note,
                                            'obsidian': obsidian_note,
                                            'notebridge_id': notebridge_id,
                                            'match_type': 'content_hash_recovery',
                                            'needs_sync_info_update': True
                                        })
                                        print(f"  ✅ 通过内容匹配恢复（时间戳变化但内容未变）: {joplin_note['title']} <-> {obsidian_note['title']} (ID: {notebridge_id[:8]}...)")
                                    else:
                                        # Obsidian 端已有 ID，说明只是时间戳不同，跳过
                                        print(f"  ⏭️ 跳过: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...) - 时间戳变化但内容未变")
                                    content_match_found = True
                                    break
                            
                            if content_match_found:
                                continue  # 已通过内容匹配处理，跳过后续逻辑
                            
                            # 如果内容确实变了，依据编辑时间与同步时间判断
                            if should_treat_as_deletion(joplin_note, joplin_sync_info, is_joplin_note=True):
                                print(f"  ⚠️ 检测到可能已删除的笔记（Obsidian 端可能已删除）: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                deleted_candidates.append({
                                    'type': 'obsidian_deleted',
                                    'note': joplin_note,
                                    'notebridge_id': notebridge_id,
                                    'title': joplin_note.get('title', 'Unknown')
                                })
                            else:
                                # 获取时间信息用于调试
                                edit_time = joplin_note.get('user_updated_time', 0)
                                sync_timestamp = parse_sync_time_to_timestamp(joplin_sync_info.get('notebridge_sync_time', ''))
                                edit_time_str = format_timestamp_for_debug(edit_time)
                                sync_time_str = format_timestamp_for_debug(sync_timestamp)
                                delta_ms = edit_time - sync_timestamp
                                delta_str = f"{delta_ms / 1000:.1f}秒" if delta_ms > 0 else f"{abs(delta_ms) / 1000:.1f}秒（早于）"
                                print(f"  🔁 检测到 Joplin 笔记在同步后有修改，将重新同步到 Obsidian: {joplin_note.get('title', 'Unknown')} (ID: {notebridge_id[:8]}...)")
                                print(f"     编辑时间: {edit_time_str}, 同步时间: {sync_time_str}, 时间差: {delta_str}")
                                unmatched_joplin_ids.add(joplin_note['id'])
                continue
            # 这是新笔记或者第一次同步，加入待同步列表
            unmatched_joplin_ids.add(id_mapping['obsidian_to_joplin'][notebridge_id])
    
    # 添加完全没有ID的笔记，但排除已在上次同步中的
    for note in id_mapping['unmapped_joplin']:
        # 检查这个笔记是否已经在上次同步中
        note_sync_info = extract_sync_info_from_joplin(note['body'])
        if note_sync_info.get('notebridge_id') not in previous_joplin_ids:
            unmatched_joplin_ids.add(note['id'])
    
    for note in id_mapping['unmapped_obsidian']:
        # 检查这个笔记是否已经在上次同步中
        note_sync_info = extract_sync_info_from_obsidian(note['body'])
        if note_sync_info.get('notebridge_id') not in previous_obsidian_ids:
            unmatched_obsidian_paths.add(note['path'])
    
    # 内容匹配（基于哈希，排除空笔记）
    joplin_hash_map = {}
    obsidian_hash_map = {}
    
    for note in joplin_notes:
        if note['id'] in unmatched_joplin_ids and not is_empty_note(note['body']):
            content_hash = calculate_content_hash(note['body'])
            joplin_hash_map[content_hash] = note
    
    for note in obsidian_notes:
        if note['path'] in unmatched_obsidian_paths and not is_empty_note(note['body']):
            content_hash = calculate_content_hash(note['body'])
            obsidian_hash_map[content_hash] = note
            if content_hash in joplin_hash_map:
                # 找到内容相同的笔记
                joplin_note = joplin_hash_map[content_hash]
                
                # 重要：使用已有的 notebridge_id（优先使用 Joplin 端的）
                joplin_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
                obsidian_sync_info = extract_sync_info_from_obsidian(note['body'])
                
                # 优先使用已有的 ID，如果两边都有就用 Joplin 的，如果都没有就生成新的
                if joplin_sync_info.get('notebridge_id'):
                    notebridge_id = joplin_sync_info['notebridge_id']
                elif obsidian_sync_info.get('notebridge_id'):
                    notebridge_id = obsidian_sync_info['notebridge_id']
                else:
                    notebridge_id = generate_sync_info('joplin')['notebridge_id']
                
                matched_pairs.append({
                    'joplin': joplin_note,
                    'obsidian': note,
                    'notebridge_id': notebridge_id,
                    'match_type': 'content_hash',
                    'needs_sync_info_update': not (joplin_sync_info.get('notebridge_id') and obsidian_sync_info.get('notebridge_id'))
                })
                unmatched_joplin_ids.discard(joplin_note['id'])
                unmatched_obsidian_paths.discard(note['path'])
    
    # 收集最终未匹配的笔记，但排除单向同步的笔记
    for note in joplin_notes:
        if note['id'] in unmatched_joplin_ids:
            # 检查是否是单向同步的笔记
            notebook = note['notebook']
            if any(matches_pattern(notebook, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
                # 这是只从Obsidian同步到Joplin的笔记，不应该出现在未匹配列表中
                continue
            unmatched_joplin.append(note)
    
    for note in obsidian_notes:
        if note['path'] in unmatched_obsidian_paths:
            # 检查是否是单向同步的笔记
            folder = note['folder']
            if any(matches_pattern(folder, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
                # 这是只从Joplin同步到Obsidian的笔记，不应该出现在未匹配列表中
                continue
            unmatched_obsidian.append(note)
    
    return matched_pairs, unmatched_joplin, unmatched_obsidian, deleted_candidates

def print_sync_plan(matched_pairs, unmatched_joplin, unmatched_obsidian):
    """
    打印同步计划
    """
    print("\n" + "="*50)
    print("🔄 智能同步计划")
    print("="*50)
    
    print(f"\n✅ 已匹配的笔记对：{len(matched_pairs)} 对")
    for i, pair in enumerate(matched_pairs[:5], 1):
        print(f"  {i}. Joplin: {pair['joplin']['title']} ({pair['joplin']['notebook']})")
        print(f"     Obsidian: {pair['obsidian']['title']} ({pair['obsidian']['folder']})")
        print(f"     匹配方式: {pair['match_type']}")
    
    print(f"\n📝 需要同步到 Obsidian 的新笔记：{len(unmatched_joplin)} 条")
    for i, note in enumerate(unmatched_joplin[:5], 1):
        print(f"  {i}. {note['title']} ({note['notebook']})")
    
    print(f"\n📄 需要同步到 Joplin 的新笔记：{len(unmatched_obsidian)} 条")
    for i, note in enumerate(unmatched_obsidian[:5], 1):
        print(f"  {i}. {note['title']} ({note['folder']})")
    
    print("\n💡 防重复机制已启用：")
    print("  - 通过唯一ID避免重复同步")
    print("  - 通过内容哈希匹配相同笔记")
    print("  - 智能分配新ID给未匹配笔记")
    print("  - 自动排除空笔记，避免无效匹配")
    print("="*50)

# 10. 实际同步功能
def add_sync_info_to_joplin_content(content, sync_info):
    """
    在 Joplin 笔记内容中添加同步信息（彻底避免重复）
    """
    # 彻底清理所有已存在的同步信息（包括HTML注释和YAML格式）
    cleaned_content = clean_duplicate_sync_info(content)
    
    # 再次验证清理结果，确保没有任何同步信息残留
    # 检查是否还有HTML注释格式的同步信息
    if re.search(r'<!-- notebridge_', cleaned_content):
        # 如果还有残留，强制清理
        cleaned_content = re.sub(r'<!--\s*notebridge_[^>]+\s*-->\s*', '', cleaned_content)
    
    # 清理可能残留的单独的 --> 或 <!--
    cleaned_content = re.sub(r'^-->\s*$', '', cleaned_content, flags=re.MULTILINE)
    cleaned_content = re.sub(r'^<!--\s*$', '', cleaned_content, flags=re.MULTILINE)
    cleaned_content = re.sub(r'^\s*-->\s*\n', '', cleaned_content, flags=re.MULTILINE)
    cleaned_content = re.sub(r'^\s*<!--\s*\n', '', cleaned_content, flags=re.MULTILINE)
    
    # 检查是否还有YAML格式的同步信息
    if re.search(r'notebridge_', cleaned_content):
        # 如果还有残留，需要更仔细地处理YAML
        yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', cleaned_content, re.DOTALL)
        if yaml_match:
            yaml_content = yaml_match.group(1)
            # 移除所有notebridge相关的行
            yaml_lines = yaml_content.split('\n')
            filtered_lines = [line for line in yaml_lines if not line.strip().startswith('notebridge_')]
            if filtered_lines:
                new_yaml_content = '\n'.join(filtered_lines)
                cleaned_content = f"---\n{new_yaml_content}\n---\n\n" + cleaned_content[yaml_match.end():]
            else:
                # 如果YAML为空，移除整个frontmatter
                cleaned_content = cleaned_content[yaml_match.end():]
    
    # 添加新的同步信息
    sync_header = f"""<!-- notebridge_id: {sync_info['notebridge_id']} -->
<!-- notebridge_sync_time: {sync_info['notebridge_sync_time']} -->
<!-- notebridge_source: {sync_info['notebridge_source']} -->
<!-- notebridge_version: {sync_info['notebridge_version']} -->

"""
    return sync_header + cleaned_content

def add_sync_info_to_obsidian_content(content, sync_info):
    """
    在 Obsidian 笔记内容中添加同步信息（YAML frontmatter，彻底避免重复）
    """
    # 彻底清理所有已存在的同步信息（包括HTML注释和YAML格式）
    cleaned_content = clean_duplicate_sync_info(content)
    
    # 再次验证清理结果，确保没有任何同步信息残留
    # 检查是否还有HTML注释格式的同步信息
    if re.search(r'<!-- notebridge_', cleaned_content):
        # 如果还有残留，强制清理
        cleaned_content = re.sub(r'<!--\s*notebridge_[^>]+\s*-->\s*', '', cleaned_content)
    
    # 清理可能残留的单独的 --> 或 <!--
    cleaned_content = re.sub(r'^-->\s*$', '', cleaned_content, flags=re.MULTILINE)
    cleaned_content = re.sub(r'^<!--\s*$', '', cleaned_content, flags=re.MULTILINE)
    cleaned_content = re.sub(r'^\s*-->\s*\n', '', cleaned_content, flags=re.MULTILINE)
    cleaned_content = re.sub(r'^\s*<!--\s*\n', '', cleaned_content, flags=re.MULTILINE)
    
    # 检查是否还有YAML格式的同步信息
    if re.search(r'notebridge_', cleaned_content):
        # 如果还有残留，需要更仔细地处理YAML
        yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', cleaned_content, re.DOTALL)
        if yaml_match:
            yaml_content = yaml_match.group(1)
            # 移除所有notebridge相关的行
            yaml_lines = yaml_content.split('\n')
            filtered_lines = [line for line in yaml_lines if not line.strip().startswith('notebridge_')]
            if filtered_lines:
                new_yaml_content = '\n'.join(filtered_lines)
                cleaned_content = f"---\n{new_yaml_content}\n---\n\n" + cleaned_content[yaml_match.end():]
            else:
                # 如果YAML为空，移除整个frontmatter
                cleaned_content = cleaned_content[yaml_match.end():]
    
    # 检查是否已有 frontmatter
    if cleaned_content.startswith('---'):
        # 已有 frontmatter，在其中添加同步信息
        yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', cleaned_content, re.DOTALL)
        if yaml_match:
            try:
                yaml_content = yaml_match.group(1)
                frontmatter = yaml.safe_load(yaml_content) if yaml_content.strip() else {}
                # 确保 frontmatter 是字典类型
                if not isinstance(frontmatter, dict):
                    frontmatter = {}
                # 更新同步信息（覆盖已存在的）
                frontmatter.update(sync_info)
                new_frontmatter = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
                return f"---\n{new_frontmatter}---\n\n" + cleaned_content[yaml_match.end():]
            except yaml.YAMLError:
                # 如果YAML解析失败，创建新的
                pass
    
    # 没有 frontmatter 或解析失败，创建新的
    frontmatter = yaml.dump(sync_info, default_flow_style=False, allow_unicode=True)
    return f"---\n{frontmatter}---\n\n{cleaned_content}"

# 附件目录
OBSIDIAN_ATTACHMENT_DIR = os.path.join(obsidian_vault_path, 'attachments')
os.makedirs(OBSIDIAN_ATTACHMENT_DIR, exist_ok=True)

def sanitize_filename(filename, max_length=250):
    """
    清理文件名/文件夹名/笔记本名，移除或替换不允许的字符，限制文件名长度
    注意：这是文件名本身的长度限制，不包括路径
    默认最大长度为250字符（NTFS文件系统支持最长255字符的文件名）
    """
    import re
    
    # 首先处理控制字符（制表符、换行符、回车符等）
    filename = re.sub(r'[\t\n\r]', ' ', filename)
    
    # Windows 不允许的字符：< > : " | ? * \ /
    # 以及其他可能导致问题的字符
    invalid_chars = r'[<>:"|?*\\/{}[\]()\'`~!@#$%^&=;,，。、；：""''（）【】《》]'
    filename = re.sub(invalid_chars, '_', filename)
    
    # 移除或替换其他可能导致问题的字符
    filename = re.sub(r'[^\w\s\-_.]', '_', filename)
    
    # 将多个连续的空格或下划线替换为单个下划线
    filename = re.sub(r'[\s_]+', '_', filename)
    
    # 移除开头和结尾的空格、点、下划线
    filename = filename.strip(' ._')
    
    # 如果文件名为空，使用默认名称
    if not filename:
        filename = 'untitled'
    
    # 限制文件名长度（保留扩展名）
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        max_name_length = max_length - len(ext)
        if max_name_length > 0:
            filename = name[:max_name_length] + ext
        else:
            filename = 'untitled' + ext
    
    return filename

def ensure_path_length_limit(base_path, filename, max_path_length=260):
    """
    确保完整路径不超过Windows路径长度限制（默认260字符）
    如果路径太长，会自动截短文件名
    
    参数:
        base_path: 基础路径（不包括文件名）
        filename: 文件名（包括扩展名）
        max_path_length: 最大路径长度（默认260字符，Windows MAX_PATH限制）
    
    返回:
        调整后的文件名
    """
    # 构建完整路径
    full_path = os.path.join(base_path, filename)
    
    # 如果完整路径长度在限制内，直接返回
    if len(full_path) <= max_path_length:
        return filename
    
    # 计算需要截短的长度
    # 路径分隔符长度 + 基础路径长度 + 扩展名长度
    path_sep_len = len(os.sep)
    base_path_len = len(base_path)
    name, ext = os.path.splitext(filename)
    ext_len = len(ext)
    
    # 计算文件名可以使用的最大长度
    max_filename_len = max_path_length - base_path_len - path_sep_len - ext_len
    
    # 如果计算出的长度太小（小于10），至少保留10字符
    if max_filename_len < 10:
        max_filename_len = 10
    
    # 截短文件名
    truncated_name = name[:max_filename_len]
    return truncated_name + ext

def get_unique_filename(base_path):
    """
    确保文件名唯一，如果存在则添加数字后缀
    """
    if not os.path.exists(base_path):
        return base_path
    
    name, ext = os.path.splitext(base_path)
    counter = 1
    while True:
        new_path = f"{name}_{counter}{ext}"
        if not os.path.exists(new_path):
            return new_path
        counter += 1

def get_joplin_note_tags(note_id):
    """
    通过 Joplin API 获取某条笔记的所有标签标题。
    API: GET /notes/:id/tags（分页）
    返回标签标题列表，失败时返回空列表。
    """
    tag_titles = []
    page = 1
    try:
        while True:
            url = f"{joplin_api_base}/notes/{note_id}/tags?token={joplin_token}&fields=title&page={page}"
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                return tag_titles
            data = resp.json()
            for item in data.get('items', []):
                title = item.get('title', '').strip()
                if title:
                    tag_titles.append(title)
            if not data.get('has_more', False):
                break
            page += 1
    except Exception:
        pass
    return tag_titles


def extract_obsidian_tags(content):
    """
    从 Obsidian 笔记内容中提取标签：YAML frontmatter 的 tags 字段 + 正文中的 #标签。
    返回去重后的标签标题列表。
    """
    tags = []
    # 1. 从 frontmatter 取 tags（支持 tags: [a, b] 或 tags: a）
    yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if yaml_match:
        try:
            yaml_content = yaml_match.group(1)
            data = yaml.safe_load(yaml_content)
            if isinstance(data, dict) and data.get('tags'):
                t = data['tags']
                if isinstance(t, list):
                    tags.extend(str(x).strip() for x in t if x)
                else:
                    tags.append(str(t).strip())
        except yaml.YAMLError:
            pass
    # 2. 从正文取 #标签（Obsidian 风格，避免匹配 URL 中的 #）
    # 匹配行内或行尾的 #中文/英文/数字 标签，且 # 前为非字母数字
    inline = re.findall(r'(?<![a-zA-Z0-9#])#([^\s#\[\]|]+)', content)
    tags.extend(x.strip() for x in inline if x.strip())
    # 去重并保持顺序
    seen = set()
    out = []
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def get_or_create_joplin_tag(title):
    """
    按标题查找 Joplin 标签，不存在则创建。返回标签 ID，失败返回 None。
    """
    if not title or not title.strip():
        return None
    title = title.strip()
    # 查找：GET /search?query=title&type=tag
    try:
        url = f"{joplin_api_base}/search?query={requests.utils.quote(title)}&type=tag&token={joplin_token}&fields=id,title"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('items', []):
                if item.get('title', '').strip() == title:
                    return item.get('id')
        # 未找到则创建：POST /tags
        create_url = f"{joplin_api_base}/tags?token={joplin_token}"
        resp = requests.post(create_url, json={'title': title}, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('id')
    except Exception:
        pass
    return None


def attach_joplin_tag_to_note(tag_id, note_id):
    """将 Joplin 标签关联到笔记。POST /tags/:id/notes"""
    try:
        url = f"{joplin_api_base}/tags/{tag_id}/notes?token={joplin_token}"
        resp = requests.post(url, json={'id': note_id}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def sync_obsidian_tags_to_joplin_note(obsidian_body, joplin_note_id):
    """
    把 Obsidian 笔记中的标签同步到 Joplin 笔记（创建/关联标签）。
    """
    tag_titles = extract_obsidian_tags(obsidian_body)
    for t in tag_titles:
        tag_id = get_or_create_joplin_tag(t)
        if tag_id and attach_joplin_tag_to_note(tag_id, joplin_note_id):
            pass  # 可选：print(f"    已同步标签: {t}")
        elif t:
            print(f"    ⚠️ 标签同步失败: {t}")


def extract_joplin_resource_ids(content):
    """
    提取Joplin笔记正文中所有资源ID（支持markdown和HTML格式）
    返回资源ID列表。Joplin 资源 ID 为 32 位十六进制，兼容大小写。
    """
    resource_ids = []
    
    # 1. 提取markdown格式的资源：![xxx](:/资源ID) 或 ![](:/资源ID)
    # 注意：.*? 是非贪婪匹配，\[和\]需要转义；[a-fA-F0-9] 兼容大小写
    markdown_ids = re.findall(r'!\[[^\]]*\]\(:\/([a-fA-F0-9]+)\)', content)
    resource_ids.extend(markdown_ids)
    
    # 2. 提取HTML格式的资源：<img src=":/资源ID"/>
    html_ids = re.findall(r'<img[^>]*src=["\']?:\/([a-fA-F0-9]+)["\']?[^>]*>', content)
    resource_ids.extend(html_ids)
    
    # 去重并统一为小写（Joplin API 使用小写）
    return list(set(rid.lower() for rid in resource_ids))

def download_joplin_resource(resource_id):
    """
    通过Joplin API下载资源文件，返回本地文件路径和原始文件名。
    失败时打印明确错误信息，便于排查「附件同步失败」问题。
    """
    # 统一为小写（API 使用小写 ID）
    resource_id = resource_id.lower()
    # 获取资源元数据，获取文件名和MIME类型
    meta_url = f"{joplin_api_base}/resources/{resource_id}?token={joplin_token}"
    try:
        resp = requests.get(meta_url, timeout=30)
    except Exception as e:
        print(f"    ⚠️ 附件 {resource_id[:8]}... 获取元数据失败: {e}")
        return None, None
    if resp.status_code != 200:
        print(f"    ⚠️ 附件 {resource_id[:8]}... 元数据请求失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return None, None
    meta = resp.json()
    original_filename = meta.get('title') or (resource_id + '.bin')
    
    # 清理文件名
    safe_filename = sanitize_filename(original_filename)
    
    # 下载文件内容
    file_url = f"{joplin_api_base}/resources/{resource_id}/file?token={joplin_token}"
    try:
        resp = requests.get(file_url, timeout=60)
    except Exception as e:
        print(f"    ⚠️ 附件 {resource_id[:8]}... 下载失败: {e}")
        return None, None
    if resp.status_code != 200:
        print(f"    ⚠️ 附件 {resource_id[:8]}... 文件下载失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return None, None
    
    # 确保附件目录存在
    os.makedirs(OBSIDIAN_ATTACHMENT_DIR, exist_ok=True)
    # 确保文件名唯一
    local_path = os.path.join(OBSIDIAN_ATTACHMENT_DIR, safe_filename)
    unique_local_path = get_unique_filename(local_path)
    unique_filename = os.path.basename(unique_local_path)
    
    # 保存到attachments目录
    try:
        with open(unique_local_path, 'wb') as f:
            f.write(resp.content)
    except Exception as e:
        print(f"    ⚠️ 附件 {resource_id[:8]}... 写入本地失败: {e}")
        return None, None
    return unique_local_path, unique_filename

def replace_joplin_resource_links(content, resource_map):
    """
    替换Joplin笔记中的资源引用为Obsidian本地路径
    支持markdown和HTML格式
    resource_map: {resource_id: filename}
    """
    # 1. 替换markdown格式：![xxx](:/资源ID) -> ![](attachments/文件名)
    def repl_markdown(match):
        resource_id = match.group(1).lower()
        filename = resource_map.get(resource_id, resource_id)
        return f'![](attachments/{filename})'
    content = re.sub(r'!\[[^\]]*\]\(:\/([a-fA-F0-9]+)\)', repl_markdown, content)
    
    # 2. 替换HTML格式：<img src=":/资源ID"/> -> ![](attachments/文件名)
    def repl_html(match):
        resource_id = match.group(1).lower()
        filename = resource_map.get(resource_id, resource_id)
        # 提取width和height属性（如果有）
        full_match = match.group(0)
        width_match = re.search(r'width=["\']?(\d+)["\']?', full_match)
        height_match = re.search(r'height=["\']?(\d+)["\']?', full_match)
        
        # 转换为markdown格式（Obsidian支持）
        # 如果有宽高信息，可以添加到图片下方的注释中
        if width_match or height_match:
            size_info = f" <!-- 原始尺寸: "
            if width_match:
                size_info += f"{width_match.group(1)}px"
            if height_match:
                size_info += f" x {height_match.group(1)}px"
            size_info += " -->"
            return f'![](attachments/{filename}){size_info}'
        else:
            return f'![](attachments/{filename})'
    
    content = re.sub(r'<img[^>]*src=["\']?:\/([a-fA-F0-9]+)["\']?[^>]*>', repl_html, content)
    
    return content

def sync_joplin_to_obsidian(joplin_note, obsidian_folder='根目录'):
    """
    将 Joplin 笔记同步到 Obsidian（支持多级文件夹+附件）
    """
    try:
        # 先自动清理重复头部（确保提取到正确的同步信息）
        cleaned_joplin_body = clean_duplicate_sync_info(joplin_note['body'])
        
        # 检查是否已有同步信息，如果有就不重新生成ID，但要更新时间
        existing_sync_info = extract_sync_info_from_joplin(cleaned_joplin_body)
        if existing_sync_info.get('notebridge_id'):
            # 使用现有的同步ID，但更新同步时间（重新同步时应该更新时间）
            sync_info = generate_sync_info('joplin')
            sync_info['notebridge_id'] = existing_sync_info['notebridge_id']
            # 保留来源信息
            if existing_sync_info.get('notebridge_source'):
                sync_info['notebridge_source'] = existing_sync_info['notebridge_source']
            # 注意：不保留旧的同步时间，使用新生成的当前时间
            # 从 Joplin 获取该笔记的标签，写入 Obsidian frontmatter
            joplin_tags = get_joplin_note_tags(joplin_note['id'])
            if joplin_tags:
                sync_info['tags'] = joplin_tags
            # 移除Joplin的HTML注释格式，准备转换为Obsidian的YAML格式
            content = cleaned_joplin_body
            # 清理HTML注释格式的同步信息（更彻底）
            content = re.sub(r'<!--\s*notebridge_id:\s*[a-f0-9-]+\s*-->\s*', '', content)
            content = re.sub(r'<!--\s*notebridge_sync_time:\s*[^>]+\s*-->\s*', '', content)
            content = re.sub(r'<!--\s*notebridge_source:\s*[^>]+\s*-->\s*', '', content)
            content = re.sub(r'<!--\s*notebridge_version:\s*[^>]+\s*-->\s*', '', content)
            # 清理可能残留的单独的 --> 或 <!--
            content = re.sub(r'-->\s*', '', content)
            content = re.sub(r'<!--\s*', '', content)
            # 清理多余的空行
            content = re.sub(r'^\s*\n+', '', content)
            content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
            # 添加Obsidian格式的同步信息（YAML frontmatter，含标签）
            content = add_sync_info_to_obsidian_content(content, sync_info)
        else:
            # 只有没有同步信息的笔记才生成新的
            sync_info = generate_sync_info('joplin')
            # 从 Joplin 获取该笔记的标签，写入 Obsidian frontmatter
            joplin_tags = get_joplin_note_tags(joplin_note['id'])
            if joplin_tags:
                sync_info['tags'] = joplin_tags
            # 直接使用Obsidian格式（使用清理后的内容）
            content = add_sync_info_to_obsidian_content(cleaned_joplin_body, sync_info)
        
        # 附件处理：提取资源ID，下载资源，替换链接
        resource_ids = extract_joplin_resource_ids(content)
        resource_map = {}
        
        if resource_ids:
            print(f"    处理 {len(resource_ids)} 个附件...")
            for resource_id in resource_ids:
                local_path, filename = download_joplin_resource(resource_id)
                if local_path and filename:
                    resource_map[resource_id] = filename
            
            # 替换内容中的资源链接
            content = replace_joplin_resource_links(content, resource_map)
        
        # 清理文件名（文件名长度限制）
        safe_title = sanitize_filename(joplin_note['title'])
        
        # 构建文件路径
        if obsidian_folder == '根目录':
            base_path = obsidian_vault_path
            filename = f"{safe_title}.md"
            # 确保完整路径不超过260字符限制
            filename = ensure_path_length_limit(base_path, filename)
            file_path = os.path.join(base_path, filename)
        else:
            # 清理文件夹路径 - 先替换反斜杠为正斜杠，再分割
            obsidian_folder_clean = obsidian_folder.replace('\\', '/')
            safe_folder_parts = [sanitize_filename(part) for part in obsidian_folder_clean.split('/')]
            folder_path = os.path.join(obsidian_vault_path, *safe_folder_parts)
            os.makedirs(folder_path, exist_ok=True)
            filename = f"{safe_title}.md"
            # 确保完整路径不超过260字符限制
            filename = ensure_path_length_limit(folder_path, filename)
            file_path = os.path.join(folder_path, filename)
        
        # 智能处理重名文件：检查notebridge_id匹配
        final_file_path = file_path
        if os.path.exists(file_path):
            # 读取现有文件内容，检查notebridge_id
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
                existing_sync_info = extract_sync_info_from_obsidian(existing_content)
                
                if existing_sync_info.get('notebridge_id') == sync_info.get('notebridge_id'):
                    # notebridge_id匹配，直接覆盖
                    final_file_path = file_path
                else:
                    # notebridge_id不匹配，生成新文件名
                    name, ext = os.path.splitext(file_path)
                    # 使用notebridge_id的前8位作为后缀
                    id_suffix = sync_info.get('notebridge_id', '')[:8]
                    final_file_path = f"{name}_{id_suffix}{ext}"
            except Exception:
                # 如果读取失败，使用默认的唯一文件名
                final_file_path = get_unique_filename(file_path)
        else:
            # 文件不存在，直接使用
            final_file_path = file_path
        
        # 写入文件到Obsidian
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(final_file_path), exist_ok=True)
            
            with open(final_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 重要：回写同步信息到Joplin端，确保Joplin也有同步信息
            # 这样下次同步时就能识别这条笔记已经同步过了
            # 无论是否有同步信息，都要确保Joplin端有正确的同步信息
            # 使用清理后的内容，避免重复头部问题
            joplin_content_with_sync = add_sync_info_to_joplin_content(
                cleaned_joplin_body, 
                sync_info
            )
            # 更新Joplin笔记
            success, error = update_joplin_note(joplin_note['id'], joplin_content_with_sync)
            if not success:
                print(f"    ⚠️ 回写Joplin同步信息失败: {error}")
            else:
                print(f"    ✅ 已回写同步信息到Joplin（ID: {sync_info['notebridge_id'][:8]}...）")
            
            return True, final_file_path
        except PermissionError:
            return False, "无权限写入文件"
        except Exception as e:
            return False, f"写入文件失败: {e}"
        
    except Exception as e:
        return False, str(e)

# 全局缓存，避免重复获取笔记本信息
_joplin_notebooks_cache = None
_joplin_notebooks_cache_time = None

def get_all_joplin_notebooks():
    """
    获取所有 Joplin 笔记本（带缓存）
    """
    global _joplin_notebooks_cache, _joplin_notebooks_cache_time
    
    # 缓存 30 秒
    current_time = datetime.now()
    if (_joplin_notebooks_cache is not None and 
        _joplin_notebooks_cache_time is not None and
        (current_time - _joplin_notebooks_cache_time).seconds < 30):
        return _joplin_notebooks_cache
    
    print("正在获取 Joplin 笔记本信息...")
    all_notebooks = {}
    page = 1
    max_pages = 100  # 防止无限循环
    
    try:
        while page <= max_pages:
            url = f"{joplin_api_base}/folders?token={joplin_token}&fields=id,title,parent_id&page={page}"
            print(f"  正在获取第 {page} 页...")
            
            # 添加超时设置
            resp = requests.get(url, timeout=10)
            
            if resp.status_code != 200:
                print(f"  ❌ API 调用失败: {resp.status_code} - {resp.text}")
                break
            
            try:
                data = resp.json()
            except Exception as e:
                print(f"  ❌ JSON 解析失败: {e}")
                break
            
            items = data.get('items', [])
            if not items:
                print(f"  第 {page} 页没有数据")
                break
                
            print(f"  第 {page} 页获取到 {len(items)} 个笔记本")
            
            for folder in items:
                # 使用ID作为key，避免同名笔记本冲突
                folder_id = folder['id']
                all_notebooks[folder_id] = {
                    'id': folder_id,
                    'title': folder['title'],
                    'parent_id': folder.get('parent_id', '')
                }
            
            if not data.get('has_more', False):
                print(f"  已获取所有页面，共 {len(all_notebooks)} 个笔记本")
                break
            page += 1
        
        if page > max_pages:
            print(f"  ⚠️ 达到最大页数限制 ({max_pages})，可能数据不完整")
        
    except requests.exceptions.Timeout:
        print("  ❌ 请求超时，请检查 Joplin 是否正常运行")
        return {}
    except requests.exceptions.ConnectionError:
        print("  ❌ 连接失败，请检查 Joplin Web Clipper 是否开启")
        return {}
    except Exception as e:
        print(f"  ❌ 获取笔记本信息时出错: {e}")
        return {}
    
    _joplin_notebooks_cache = all_notebooks
    _joplin_notebooks_cache_time = current_time
    return all_notebooks

def get_or_create_joplin_notebook(notebook_path):
    """
    获取或创建 Joplin 笔记本（支持多级嵌套，优化版本，目录名安全）
    """
    if not notebook_path or notebook_path == '根目录':
        return None, None  # 根目录笔记本
    
    # 分割路径，并对每一级都sanitize
    path_parts = [sanitize_filename(part) for part in notebook_path.split('/') if part]
    
    # 获取所有现有笔记本（使用缓存）
    all_notebooks = get_all_joplin_notebooks()
    
    # 构建笔记本ID到完整路径的映射
    def build_notebook_path(notebook_id, notebooks_dict):
        """递归构建笔记本的完整路径"""
        for nid, info in notebooks_dict.items():
            if info['id'] == notebook_id:
                if info['parent_id']:
                    parent_path = build_notebook_path(info['parent_id'], notebooks_dict)
                    return f"{parent_path}/{info['title']}" if parent_path else info['title']
                else:
                    return info['title']
        return None
    
    # 逐级创建或获取笔记本
    current_parent_id = None
    
    for i, folder_name in enumerate(path_parts):
        # 构建当前应该的完整路径
        target_path = '/'.join(path_parts[:i+1])
        
        # 在现有笔记本中查找匹配的
        found_id = None
        for notebook_id, info in all_notebooks.items():
            # 检查标题和父级是否匹配
            if info['title'] == folder_name and info['parent_id'] == (current_parent_id or ''):
                found_id = notebook_id
                break
        
        if found_id:
            # 找到了，使用现有的
            current_parent_id = found_id
        else:
            # 没找到，需要创建
            try:
                create_url = f"{joplin_api_base}/folders?token={joplin_token}"
                create_data = {
                    'title': folder_name,
                    'parent_id': current_parent_id or ''
                }
                resp = requests.post(create_url, json=create_data, timeout=10)
                if resp.status_code == 200:
                    new_id = resp.json()['id']
                    # 保存父级ID用于缓存
                    parent_for_cache = current_parent_id or ''
                    # 更新当前父级为新创建的笔记本
                    current_parent_id = new_id
                    # 更新缓存
                    all_notebooks[new_id] = {
                        'id': new_id,
                        'title': folder_name,
                        'parent_id': parent_for_cache
                    }
                    if _joplin_notebooks_cache is not None:
                        _joplin_notebooks_cache[new_id] = {
                            'id': new_id,
                            'title': folder_name,
                            'parent_id': parent_for_cache
                        }
                else:
                    return None, f"创建笔记本失败: {resp.status_code} - {resp.text}"
            except requests.exceptions.Timeout:
                return None, f"创建笔记本超时: {folder_name}"
            except requests.exceptions.ConnectionError:
                return None, f"连接失败，请检查 Joplin 是否正常运行"
            except Exception as e:
                return None, f"创建笔记本时出错: {e}"
    
    return current_parent_id, None

def sync_obsidian_to_joplin(obsidian_note, joplin_notebook='未分类'):
    """
    将 Obsidian 笔记同步到 Joplin（支持多级笔记本）
    """
    try:
        # 先自动清理重复头部（确保提取到正确的同步信息）
        cleaned_obsidian_body = clean_duplicate_sync_info(obsidian_note['body'])
        
        # 检查是否已有同步信息，如果有就不重新生成
        existing_sync_info = extract_sync_info_from_obsidian(cleaned_obsidian_body)
        if existing_sync_info.get('notebridge_id'):
            sync_info = existing_sync_info
            content = cleaned_obsidian_body  # 使用清理后的内容
        else:
            # 只有没有同步信息的笔记才生成新的
            sync_info = generate_sync_info('obsidian')
            content = add_sync_info_to_obsidian_content(cleaned_obsidian_body, sync_info)
        
        # 创建 Joplin 内容（使用清理后的内容）
        joplin_content = add_sync_info_to_joplin_content(content, sync_info)
        
        # 获取或创建笔记本（支持多级）
        notebook_id, error = get_or_create_joplin_notebook(joplin_notebook)
        if error:
            return False, error
        
        # 创建笔记
        create_url = f"{joplin_api_base}/notes?token={joplin_token}"
        note_data = {
            'title': obsidian_note['title'],
            'body': joplin_content,
            'parent_id': notebook_id or ''
        }
        resp = requests.post(create_url, json=note_data)
        
        if resp.status_code == 200:
            new_note_id = resp.json()['id']
            # 将 Obsidian 笔记中的标签同步到 Joplin
            sync_obsidian_tags_to_joplin_note(obsidian_note['body'], new_note_id)
            # 重要：回写同步信息到Obsidian端，确保Obsidian也有同步信息（YAML格式）
            # 无论是否有同步信息，都回写以确保格式正确（使用清理后的内容）
            try:
                # 检查路径长度
                path_length = len(obsidian_note['path'])
                if path_length > 250:
                    print(f"    ⚠️ 警告：路径过长（{path_length} 字符），将使用长路径支持")
                
                # 确保目录存在
                os.makedirs(os.path.dirname(obsidian_note['path']), exist_ok=True)
                
                # 使用长路径安全版本
                safe_path = get_long_path_safe(obsidian_note['path'])
                
                # 确保内容包含正确的同步信息（YAML格式）
                obsidian_content_with_sync = add_sync_info_to_obsidian_content(cleaned_obsidian_body, sync_info)
                
                with open(safe_path, 'w', encoding='utf-8') as f:
                    f.write(obsidian_content_with_sync)  # 使用包含正确同步信息的内容
                
                # 验证写入
                if os.path.exists(safe_path):
                    with open(safe_path, 'r', encoding='utf-8') as f:
                        verify = f.read()
                    if 'notebridge_id' in verify:
                        print(f"    ✅ 已回写同步信息到 Obsidian（ID: {sync_info['notebridge_id'][:8]}...）")
                    else:
                        print(f"    ⚠️ 写入成功但验证失败：同步信息未找到")
                else:
                    print(f"    ❌ 文件写入失败：文件不存在")
            except Exception as e:
                print(f"    ❌ 回写Obsidian同步信息失败: {e}")
                print(f"    文件路径长度: {len(obsidian_note['path'])}")
                print(f"    文件路径: {obsidian_note['path']}")
            
            return True, new_note_id
        else:
            return False, f"创建笔记失败: {resp.text}"
            
    except Exception as e:
        return False, str(e)

def sync_obsidian_to_joplin_with_notebook_id(obsidian_note, notebook_id):
    """
    将 Obsidian 笔记同步到 Joplin（使用已知的笔记本ID，避免重复创建）
    支持超时重试和延迟，提升大批量同步稳定性
    并输出详细日志
    """
    max_retries = 3  # 增加重试次数
    # 根据内容大小动态调整超时时间
    content_size = len(obsidian_note.get('body', ''))
    # 基础超时60秒，每1MB内容增加10秒，最大120秒
    timeout = min(60 + (content_size // 1024 // 1024) * 10, 120)
    
    for attempt in range(max_retries + 1):
        start_time = time.time()
        try:
            print(f"[同步] 开始同步笔记: {obsidian_note['title']} (第{attempt+1}次尝试，超时设置: {timeout}秒)")
            # 检查是否已有同步信息，如果有就不重新生成ID，但要更新时间
            existing_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
            if existing_sync_info.get('notebridge_id'):
                # 使用现有的同步ID，但更新同步时间（重新同步时应该更新时间）
                sync_info = generate_sync_info('obsidian')
                sync_info['notebridge_id'] = existing_sync_info['notebridge_id']
                # 保留来源信息
                if existing_sync_info.get('notebridge_source'):
                    sync_info['notebridge_source'] = existing_sync_info['notebridge_source']
                # 注意：不保留旧的同步时间，使用新生成的当前时间
                # 移除Obsidian的YAML格式，准备转换为Joplin的HTML注释格式
                content = obsidian_note['body']
                # 清理YAML frontmatter中的同步信息
                yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
                if yaml_match:
                    yaml_content = yaml_match.group(1)
                    # 移除所有notebridge相关的行
                    yaml_lines = yaml_content.split('\n')
                    filtered_lines = [line for line in yaml_lines 
                                     if not line.strip().startswith('notebridge_')]
                    if filtered_lines:
                        new_yaml_content = '\n'.join(filtered_lines)
                        content = f"---\n{new_yaml_content}\n---\n\n" + content[yaml_match.end():]
                    else:
                        # 如果YAML为空，移除整个frontmatter
                        content = content[yaml_match.end():]
                # 清理HTML注释格式的同步信息（如果有）
                content = re.sub(r'<!-- notebridge_[^>]+ -->\s*', '', content)
                content = re.sub(r'^\s*\n', '', content)
            else:
                # 只有没有同步信息的笔记才生成新的
                sync_info = generate_sync_info('obsidian')
                content = obsidian_note['body']
            
            # 创建 Joplin 内容（使用HTML注释格式）
            joplin_content = add_sync_info_to_joplin_content(content, sync_info)
            # 创建笔记（使用已知的笔记本ID）
            create_url = f"{joplin_api_base}/notes?token={joplin_token}"
            note_data = {
                'title': obsidian_note['title'],  # 不要清理标题！Joplin 支持任何字符
                'body': joplin_content,
                'parent_id': notebook_id or ''
            }
            # 使用动态超时时间
            resp = requests.post(create_url, json=note_data, timeout=timeout)
            time.sleep(0.2)  # 每次创建后延迟，缓解Joplin压力
            end_time = time.time()
            duration = end_time - start_time
            if resp.status_code == 200:
                print(f"[同步] 成功: {obsidian_note['title']}，耗时 {duration:.2f} 秒")
                print(f"[同步] 检查是否需要回写同步信息...")
                print(f"[同步] existing_sync_info: {existing_sync_info}")
                
                # 重要：回写同步信息到Obsidian端，确保Obsidian也有同步信息（YAML格式）
                # 无论是否已有同步信息，都需要回写以更新同步时间
                need_writeback = False
                writeback_reason = ""
                
                if not existing_sync_info.get('notebridge_id'):
                    need_writeback = True
                    writeback_reason = "Obsidian 端没有同步信息"
                elif existing_sync_info.get('notebridge_sync_time') != sync_info.get('notebridge_sync_time'):
                    need_writeback = True
                    writeback_reason = "需要更新同步时间"
                
                if need_writeback:
                    print(f"[同步] ✓ 需要回写（{writeback_reason}）")
                    print(f"[同步] 准备回写同步信息到 Obsidian...")
                    print(f"[同步] 同步 ID: {sync_info['notebridge_id']}")
                    print(f"[同步] 同步时间: {sync_info['notebridge_sync_time']}")
                    
                    # 检查路径长度
                    path_length = len(obsidian_note['path'])
                    print(f"[同步] 文件路径长度: {path_length} 字符")
                    if path_length > 250:
                        print(f"[同步] ⚠️ 警告：路径过长（{path_length} > 250），可能导致写入失败")
                        print(f"[同步] 💡 解决方案：")
                        print(f"[同步]    1. 启用 Windows 长路径支持（需要管理员权限）")
                        print(f"[同步]    2. 缩短文件名或移动到更短的路径")
                        print(f"[同步]    3. 使用 \\\\?\\ 前缀绕过路径限制")
                    
                    obsidian_content_with_sync = add_sync_info_to_obsidian_content(
                        obsidian_note['body'], 
                        sync_info
                    )
                    # 更新Obsidian笔记
                    try:
                        print(f"[同步] 写入文件: {obsidian_note['path'][:100]}...")
                        
                        # 确保目录存在
                        os.makedirs(os.path.dirname(obsidian_note['path']), exist_ok=True)
                        
                        # 使用长路径安全版本
                        safe_path = get_long_path_safe(obsidian_note['path'])
                        
                        with open(safe_path, 'w', encoding='utf-8') as f:
                            f.write(obsidian_content_with_sync)
                        
                        # 验证写入是否成功
                        if os.path.exists(safe_path):
                            with open(safe_path, 'r', encoding='utf-8') as f:
                                verify_content = f.read()
                            if 'notebridge_id' in verify_content:
                                print(f"[同步] ✅ 回写 Obsidian 同步信息成功！")
                            else:
                                print(f"[同步] ⚠️ 写入成功但验证失败：同步信息未找到")
                        else:
                            print(f"[同步] ❌ 文件写入失败：文件不存在")
                    except Exception as e:
                        print(f"[同步] ❌ 回写Obsidian同步信息失败: {e}")
                        print(f"[同步] 文件路径: {obsidian_note['path']}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"[同步] Obsidian 端同步信息已是最新，无需回写")
                
                # 将 Obsidian 笔记中的标签同步到 Joplin
                new_note_id = resp.json()['id']
                sync_obsidian_tags_to_joplin_note(obsidian_note['body'], new_note_id)
                return True, new_note_id
            else:
                print(f"[同步] 失败: {obsidian_note['title']}，状态码: {resp.status_code}，耗时 {duration:.2f} 秒")
                if attempt < max_retries and resp.status_code in [408, 504]:
                    print(f"[同步] 第{attempt+1}次失败，准备重试...")
                    continue
                return False, f"创建笔记失败: {resp.status_code} - {resp.text}"
        except requests.exceptions.Timeout as e:
            end_time = time.time()
            duration = end_time - start_time
            print(f"[同步] 超时: {obsidian_note['title']}，耗时 {duration:.2f} 秒，异常: {e}")
            if attempt < max_retries:
                # 重试前等待，避免立即重试导致Joplin压力过大
                wait_time = (attempt + 1) * 2  # 递增等待时间：2秒、4秒、6秒
                print(f"[同步] 第{attempt+1}次超时，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                # 每次重试时增加超时时间
                timeout = min(timeout + 20, 180)  # 每次增加20秒，最大180秒
                continue
            return False, f"创建笔记超时: {obsidian_note['title']} (已重试{max_retries}次)"
        except requests.exceptions.ConnectionError as e:
            end_time = time.time()
            duration = end_time - start_time
            print(f"[同步] 连接失败: {obsidian_note['title']}，耗时 {duration:.2f} 秒，异常: {e}")
            if attempt < max_retries:
                wait_time = (attempt + 1) * 2
                print(f"[同步] 第{attempt+1}次连接失败，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                continue
            return False, "连接失败，请检查 Joplin 是否正常运行"
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            print(f"[同步] 异常: {obsidian_note['title']}，耗时 {duration:.2f} 秒，异常: {e}")
            return False, str(e)
    print(f"[同步] 多次重试后依然失败: {obsidian_note['title']}")
    return False, f"多次重试后依然失败: {obsidian_note['title']}"

def update_joplin_note(joplin_note_id, new_content):
    """
    更新 Joplin 笔记内容
    """
    try:
        url = f"{joplin_api_base}/notes/{joplin_note_id}?token={joplin_token}"
        data = {'body': new_content}
        resp = requests.put(url, json=data)
        return resp.status_code == 200, resp.text if resp.status_code != 200 else None
    except Exception as e:
        return False, str(e)

def get_long_path_safe(path):
    """
    获取支持 Windows 长路径的路径（如果需要）
    """
    # 如果路径超过 260 字符且在 Windows 上，使用 \\?\ 前缀
    if len(path) > 250 and os.name == 'nt':
        # 转换为绝对路径
        abs_path = os.path.abspath(path)
        # 添加 \\?\ 前缀（如果还没有）
        if not abs_path.startswith('\\\\?\\'):
            return f'\\\\?\\{abs_path}'
    return path

def update_obsidian_note(file_path, new_content):
    """
    更新 Obsidian 笔记内容（带重复头部检查，支持长路径）
    """
    try:
        # 检查文件是否存在
        if not os.path.exists(file_path):
            return False, "文件不存在"
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 在写入前检查并修复重复头部
        cleaned_content = check_and_fix_sync_headers(new_content, os.path.basename(file_path))
        
        # 使用长路径安全版本
        safe_path = get_long_path_safe(file_path)
        
        with open(safe_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)
        return True, None
    except FileNotFoundError:
        return False, "文件不存在"
    except PermissionError:
        return False, "无权限写入文件"
    except Exception as e:
        return False, f"写入失败: {e} (路径长度: {len(file_path)})"

# 同步状态缓存文件
SYNC_CACHE_FILE = '.sync_cache.json'

# 用于判断“是否重新同步”还是“视为删除”的时间容差（毫秒）
# 在同一时刻写入的同步信息与实际编辑时间可能存在毫秒级或秒级的偏差
# 只要编辑时间比同步时间晚不超过阈值，就认为笔记在同步后没有实际修改
SYNC_TIME_TOLERANCE_MS = 60_000  # 默认 60 秒

def save_sync_state(joplin_notes, obsidian_notes):
    """
    保存当前同步状态到缓存文件
    """
    sync_state = {
        'timestamp': datetime.now().isoformat(),
        'joplin_notes': {},
        'obsidian_notes': {}
    }
    
    # 保存 Joplin 笔记状态
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            sync_state['joplin_notes'][sync_info['notebridge_id']] = {
                'id': note['id'],
                'title': note['title'],
                'notebook': note.get('notebook', '未分类'),
                'path': f"{note.get('notebook', '未分类')}/{note['title']}"
            }
    
    # 保存 Obsidian 笔记状态
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            sync_state['obsidian_notes'][sync_info['notebridge_id']] = {
                'path': note['path'],
                'title': note['title'],
                'folder': note.get('folder', '根目录')
            }
    
    try:
        with open(SYNC_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(sync_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存同步状态失败: {e}")

def load_sync_state():
    """
    从缓存文件加载上次同步状态
    """
    try:
        if os.path.exists(SYNC_CACHE_FILE):
            with open(SYNC_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ 加载同步状态失败: {e}")
    return None

def detect_deletions(current_joplin_notes, current_obsidian_notes):
    """
    检测删除的项目，并补全obsidian_deletions的id字段。
    只有有id的Joplin笔记才计入删除列表，没有id的笔记不计入删除。
    """
    previous_state = load_sync_state()
    if not previous_state:
        return {'joplin_deletions': [], 'obsidian_deletions': []}
    
    # 构建当前状态
    current_joplin_ids = set()
    current_obsidian_ids = set()
    joplin_id_map = {}  # notebridge_id -> joplin_note_id
    
    for note in current_joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            current_joplin_ids.add(sync_info['notebridge_id'])
            joplin_id_map[sync_info['notebridge_id']] = note['id']
    
    for note in current_obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            current_obsidian_ids.add(sync_info['notebridge_id'])
    
    # 检测删除
    joplin_deletions = []
    obsidian_deletions = []
    
    # 检测 Joplin 中删除的笔记（需要在 Obsidian 中删除）
    for note_id, note_info in previous_state['joplin_notes'].items():
        if note_id not in current_joplin_ids:
            joplin_deletions.append(note_info)
    
    # 检测 Obsidian 中删除的文件（需要在 Joplin 中删除）
    for note_id, note_info in previous_state['obsidian_notes'].items():
        if note_id not in current_obsidian_ids:
            # 只补全有id的，没id的直接跳过（不计入删除）
            joplin_note_id = joplin_id_map.get(note_id)
            if joplin_note_id:
                note_info = dict(note_info)  # 拷贝，避免污染原数据
                note_info['id'] = joplin_note_id
                obsidian_deletions.append(note_info)
            # 没有id的笔记不加入obsidian_deletions，留给后续同步处理
    
    return {
        'joplin_deletions': joplin_deletions,
        'obsidian_deletions': obsidian_deletions
    }

def detect_moves(current_joplin_notes, current_obsidian_notes):
    """
    检测笔记移动（笔记本/文件夹变化）
    """
    previous_state = load_sync_state()
    if not previous_state:
        return {'joplin_moves': [], 'obsidian_moves': []}
    
    # 构建当前状态的映射
    current_joplin_map = {}  # notebridge_id -> note info
    current_obsidian_map = {}  # notebridge_id -> note info
    
    for note in current_joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        notebridge_id = sync_info.get('notebridge_id')
        if notebridge_id:
            current_joplin_map[notebridge_id] = {
                'id': note['id'],
                'title': note['title'],
                'notebook': note.get('notebook', '未分类'),
                'path': f"{note.get('notebook', '未分类')}/{note['title']}"
            }
    
    for note in current_obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        notebridge_id = sync_info.get('notebridge_id')
        if notebridge_id:
            current_obsidian_map[notebridge_id] = {
                'path': note['path'],
                'title': note['title'],
                'folder': note.get('folder', '根目录')
            }
    
    # 检测移动
    joplin_moves = []
    obsidian_moves = []
    
    # 检测 Joplin 中移动的笔记（需要在 Obsidian 中移动）
    for note_id, prev_info in previous_state['joplin_notes'].items():
        if note_id in current_joplin_map:
            curr_info = current_joplin_map[note_id]
            # 比较笔记本路径是否变化
            if prev_info.get('notebook') != curr_info.get('notebook'):
                joplin_moves.append({
                    'notebridge_id': note_id,
                    'title': curr_info['title'],
                    'old_notebook': prev_info.get('notebook', '未分类'),
                    'new_notebook': curr_info.get('notebook', '未分类'),
                    'joplin_id': curr_info['id']
                })
    
    # 检测 Obsidian 中移动的文件（需要在 Joplin 中移动）
    for note_id, prev_info in previous_state['obsidian_notes'].items():
        if note_id in current_obsidian_map:
            curr_info = current_obsidian_map[note_id]
            # 比较文件夹路径是否变化
            if prev_info.get('folder') != curr_info.get('folder'):
                # 获取对应的 Joplin 笔记 ID
                joplin_id = current_joplin_map.get(note_id, {}).get('id')
                if joplin_id:
                    obsidian_moves.append({
                        'notebridge_id': note_id,
                        'title': curr_info['title'],
                        'old_folder': prev_info.get('folder', '根目录'),
                        'new_folder': curr_info.get('folder', '根目录'),
                        'joplin_id': joplin_id,
                        'obsidian_path': curr_info['path']
                    })
    
    return {
        'joplin_moves': joplin_moves,
        'obsidian_moves': obsidian_moves
    }

def print_move_preview(moves):
    """
    打印移动预览
    """
    if not moves['joplin_moves'] and not moves['obsidian_moves']:
        return False
    
    print("\n" + "="*50)
    print("📦 移动同步预览")
    print("="*50)
    
    if moves['joplin_moves']:
        print(f"\n📝 Joplin → Obsidian: {len(moves['joplin_moves'])} 个文件将被移动")
        for i, item in enumerate(moves['joplin_moves'][:5], 1):
            print(f"  {i}. {item['title']}")
            print(f"     从: {item['old_notebook']}")
            print(f"     到: {item['new_notebook']}")
        if len(moves['joplin_moves']) > 5:
            print(f"  ... 还有 {len(moves['joplin_moves']) - 5} 个")
    
    if moves['obsidian_moves']:
        print(f"\n📄 Obsidian → Joplin: {len(moves['obsidian_moves'])} 个笔记将被移动")
        for i, item in enumerate(moves['obsidian_moves'][:5], 1):
            print(f"  {i}. {item['title']}")
            print(f"     从: {item['old_folder']}")
            print(f"     到: {item['new_folder']}")
        if len(moves['obsidian_moves']) > 5:
            print(f"  ... 还有 {len(moves['obsidian_moves']) - 5} 个")
    
    return True

def confirm_moves():
    """
    确认移动操作
    """
    while True:
        response = input("\n❓ 是否继续移动同步？ (y/n): ").strip().lower()
        if response in ['y', 'yes', '是']:
            return True
        elif response in ['n', 'no', '否']:
            return False
        else:
            print("请输入 y 或 n")

def print_deletion_preview(deletions):
    """
    打印删除预览
    """
    if not deletions['joplin_deletions'] and not deletions['obsidian_deletions']:
        return False
    
    print("\n" + "="*50)
    print("🗑️ 删除同步预览")
    print("="*50)
    
    if deletions['joplin_deletions']:
        print(f"\n📝 Joplin → Obsidian: {len(deletions['joplin_deletions'])} 个文件将被删除")
        for i, item in enumerate(deletions['joplin_deletions'][:5], 1):
            print(f"  {i}. {item['title']} ({item['notebook']})")
        if len(deletions['joplin_deletions']) > 5:
            print(f"  ... 还有 {len(deletions['joplin_deletions']) - 5} 个")
    
    if deletions['obsidian_deletions']:
        print(f"\n📄 Obsidian → Joplin: {len(deletions['obsidian_deletions'])} 个笔记将被删除")
        for i, item in enumerate(deletions['obsidian_deletions'][:5], 1):
            print(f"  {i}. {item['title']} ({item['folder']})")
        if len(deletions['obsidian_deletions']) > 5:
            print(f"  ... 还有 {len(deletions['obsidian_deletions']) - 5} 个")
    
    return True

def confirm_deletions():
    """
    确认删除操作
    """
    while True:
        response = input("\n❓ 是否继续删除同步？ (y/n): ").strip().lower()
        if response in ['y', 'yes', '是']:
            return True
        elif response in ['n', 'no', '否']:
            return False
        else:
            print("请输入 y 或 n")

def move_obsidian_file(old_path, new_folder):
    """
    移动 Obsidian 文件到新文件夹（支持多级文件夹）
    """
    try:
        # 检查源文件是否存在
        if not os.path.exists(old_path):
            return False, "源文件不存在"
        
        # 构建新路径
        filename = os.path.basename(old_path)
        if new_folder == '根目录':
            base_path = obsidian_vault_path
            # 确保完整路径不超过260字符限制
            filename = ensure_path_length_limit(base_path, filename)
            new_path = os.path.join(base_path, filename)
        else:
            # 清理文件夹路径 - 确保正确处理多级文件夹
            new_folder_clean = new_folder.replace('\\', '/')
            # 对每个路径部分进行清理，但保持层级结构
            safe_folder_parts = [sanitize_filename(part) for part in new_folder_clean.split('/') if part]
            new_dir = os.path.join(obsidian_vault_path, *safe_folder_parts)
            # 创建目标文件夹
            os.makedirs(new_dir, exist_ok=True)
            # 确保完整路径不超过260字符限制
            filename = ensure_path_length_limit(new_dir, filename)
            new_path = os.path.join(new_dir, filename)
        
        # 如果新路径已存在，生成唯一文件名
        new_path = get_unique_filename(new_path)
        
        # 移动文件
        os.rename(old_path, new_path)
        
        # 删除空的源文件夹
        old_dir = os.path.dirname(old_path)
        try:
            if old_dir != obsidian_vault_path and not os.listdir(old_dir):
                os.rmdir(old_dir)
        except:
            pass  # 忽略删除文件夹的错误
        
        return True, new_path
    except FileNotFoundError:
        return False, "文件不存在"
    except PermissionError:
        return False, "无权限操作文件"
    except Exception as e:
        return False, str(e)

def safe_delete_obsidian_file(file_path):
    """
    安全删除 Obsidian 文件（移动到回收站）
    """
    try:
        # 检查文件是否存在
        if not os.path.exists(file_path):
            return False, "文件不存在"
        
        # 创建回收站目录
        trash_dir = os.path.join(obsidian_vault_path, '已删除')
        os.makedirs(trash_dir, exist_ok=True)
        
        # 生成唯一文件名
        filename = os.path.basename(file_path)
        trash_path = os.path.join(trash_dir, filename)
        unique_trash_path = get_unique_filename(trash_path)
        
        # 移动文件到回收站
        os.rename(file_path, unique_trash_path)
        return True, unique_trash_path
    except FileNotFoundError:
        return False, "文件不存在"
    except PermissionError:
        return False, "无权限操作文件"
    except Exception as e:
        return False, str(e)

def move_joplin_note(note_id, new_notebook_path):
    """
    移动 Joplin 笔记到新笔记本
    """
    try:
        # 获取或创建目标笔记本
        new_notebook_id, error = get_or_create_joplin_notebook(new_notebook_path)
        if error:
            return False, f"创建笔记本失败: {error}"
        
        # 移动笔记
        url = f"{joplin_api_base}/notes/{note_id}?token={joplin_token}"
        data = {'parent_id': new_notebook_id or ''}
        resp = requests.put(url, json=data, timeout=10)
        
        if resp.status_code == 200:
            return True, None
        else:
            return False, f"移动笔记失败: {resp.status_code} - {resp.text}"
    except Exception as e:
        return False, str(e)

def safe_delete_joplin_note(note_id):
    """
    安全删除 Joplin 笔记（移动到回收站笔记本）
    """
    try:
        # 创建或获取回收站笔记本
        trash_notebook_id, error = get_or_create_joplin_notebook('已删除')
        if error:
            return False, f"创建回收站失败: {error}"
        
        # 移动笔记到回收站
        url = f"{joplin_api_base}/notes/{note_id}?token={joplin_token}"
        data = {'parent_id': trash_notebook_id}
        resp = requests.put(url, json=data, timeout=10)
        
        if resp.status_code == 200:
            return True, None
        else:
            return False, f"移动笔记失败: {resp.status_code} - {resp.text}"
    except Exception as e:
        return False, str(e)

def perform_deletion_sync(deletions):
    """
    执行删除同步，删除Joplin笔记时只对有id的笔记执行删除
    """
    deletion_results = {
        'success': [],
        'failed': []
    }
    
    print("\n🗑️ 开始执行删除同步...")
    
    # 删除 Obsidian 文件
    if deletions['joplin_deletions']:
        print(f"\n📝 删除 {len(deletions['joplin_deletions'])} 个 Obsidian 文件...")
        
        # 获取当前所有 Obsidian 笔记，用于通过 notebridge_id 查找文件路径
        current_obsidian_notes = get_obsidian_notes()
        obsidian_id_to_path = {}
        
        for note in current_obsidian_notes:
            sync_info = extract_sync_info_from_obsidian(note['body'])
            if sync_info.get('notebridge_id'):
                obsidian_id_to_path[sync_info['notebridge_id']] = note['path']
        
        for item in tqdm(deletions['joplin_deletions'], desc="删除 Obsidian 文件"):
            # 通过 notebridge_id 查找文件路径（这是最可靠的方式）
            notebridge_id = item.get('notebridge_id')
            if notebridge_id and notebridge_id in obsidian_id_to_path:
                file_path = obsidian_id_to_path[notebridge_id]
                if os.path.exists(file_path):
                    success, result = safe_delete_obsidian_file(file_path)
                    if success:
                        deletion_results['success'].append(f"删除 Obsidian: {item['title']}")
                    else:
                        deletion_results['failed'].append(f"删除 Obsidian: {item['title']} - {result}")
                else:
                    deletion_results['failed'].append(f"删除 Obsidian: {item['title']} - 文件不存在")
            else:
                # 如果找不到 notebridge_id，回退到文件名匹配（兼容旧版本）
                safe_title = sanitize_filename(item['title'])
                if item['notebook'] == '未分类':
                    base_path = obsidian_vault_path
                    filename = f"{safe_title}.md"
                    # 确保完整路径不超过260字符限制
                    filename = ensure_path_length_limit(base_path, filename)
                    file_path = os.path.join(base_path, filename)
                else:
                    notebook_path = item['notebook'].replace('\\', '/')
                    safe_folder_parts = [sanitize_filename(part) for part in notebook_path.split('/')]
                    folder_path = os.path.join(obsidian_vault_path, *safe_folder_parts)
                    filename = f"{safe_title}.md"
                    # 确保完整路径不超过260字符限制
                    filename = ensure_path_length_limit(folder_path, filename)
                    file_path = os.path.join(folder_path, filename)
                
                if os.path.exists(file_path):
                    success, result = safe_delete_obsidian_file(file_path)
                    if success:
                        deletion_results['success'].append(f"删除 Obsidian: {item['title']} (文件名匹配)")
                    else:
                        deletion_results['failed'].append(f"删除 Obsidian: {item['title']} - {result}")
                else:
                    deletion_results['failed'].append(f"删除 Obsidian: {item['title']} - 文件不存在")
    
    # 删除 Joplin 笔记
    if deletions['obsidian_deletions']:
        print(f"\n📄 删除 {len(deletions['obsidian_deletions'])} 个 Joplin 笔记...")
        for item in tqdm(deletions['obsidian_deletions'], desc="删除 Joplin 笔记"):
            note_id = item.get('id')
            if not note_id:
                # 理论上不会出现，因为detect_deletions已过滤
                continue
            success, result = safe_delete_joplin_note(note_id)
            if success:
                deletion_results['success'].append(f"删除 Joplin: {item['title']}")
            else:
                deletion_results['failed'].append(f"删除 Joplin: {item['title']} - {result}")
    
    return deletion_results

def perform_move_sync(moves):
    """
    执行移动同步
    """
    move_results = {
        'success': [],
        'failed': []
    }
    
    print("\n📦 开始执行移动同步...")
    
    # 移动 Obsidian 文件（Joplin → Obsidian）
    if moves['joplin_moves']:
        print(f"\n📝 移动 {len(moves['joplin_moves'])} 个 Obsidian 文件...")
        
        # 获取当前所有 Obsidian 笔记，用于通过 notebridge_id 查找文件路径
        current_obsidian_notes = get_obsidian_notes()
        obsidian_id_to_path = {}
        
        for note in current_obsidian_notes:
            sync_info = extract_sync_info_from_obsidian(note['body'])
            if sync_info.get('notebridge_id'):
                obsidian_id_to_path[sync_info['notebridge_id']] = note['path']
        
        for item in tqdm(moves['joplin_moves'], desc="移动 Obsidian 文件"):
            notebridge_id = item.get('notebridge_id')
            new_notebook = item.get('new_notebook', '未分类')
            
            # 通过 notebridge_id 查找文件路径
            if notebridge_id and notebridge_id in obsidian_id_to_path:
                old_path = obsidian_id_to_path[notebridge_id]
                if os.path.exists(old_path):
                    success, result = move_obsidian_file(old_path, new_notebook)
                    if success:
                        move_results['success'].append(
                            f"移动 Obsidian: {item['title']} → {new_notebook}"
                        )
                    else:
                        move_results['failed'].append(
                            f"移动 Obsidian: {item['title']} - {result}"
                        )
                else:
                    move_results['failed'].append(
                        f"移动 Obsidian: {item['title']} - 文件不存在"
                    )
            else:
                move_results['failed'].append(
                    f"移动 Obsidian: {item['title']} - 找不到文件"
                )
    
    # 移动 Joplin 笔记（Obsidian → Joplin）
    if moves['obsidian_moves']:
        print(f"\n📄 移动 {len(moves['obsidian_moves'])} 个 Joplin 笔记...")
        for item in tqdm(moves['obsidian_moves'], desc="移动 Joplin 笔记"):
            joplin_id = item.get('joplin_id')
            new_folder = item.get('new_folder', '根目录')
            
            if joplin_id:
                success, result = move_joplin_note(joplin_id, new_folder)
                if success:
                    move_results['success'].append(
                        f"移动 Joplin: {item['title']} → {new_folder}"
                    )
                else:
                    move_results['failed'].append(
                        f"移动 Joplin: {item['title']} - {result}"
                    )
            else:
                move_results['failed'].append(
                    f"移动 Joplin: {item['title']} - 找不到笔记ID"
                )
    
    return move_results

def perform_sync(matched_pairs, unmatched_joplin, unmatched_obsidian):
    """
    执行实际同步操作（包含删除同步+方向控制）
    """
    sync_results = {
        'success': [],
        'failed': [],
        'updated': [],
        'created': [],
        'deleted': []
    }
    
    print("\n🚀 开始执行同步...")
    print(f"📡 同步方向: {SYNC_DIRECTION}")
    
    # 检测删除
    current_joplin_notes = get_joplin_notes()
    current_obsidian_notes = get_obsidian_notes()
    deletions = detect_deletions(current_joplin_notes, current_obsidian_notes)
    
    # 显示删除预览并确认
    if print_deletion_preview(deletions):
        if confirm_deletions():
            deletion_results = perform_deletion_sync(deletions)
            sync_results['deleted'].extend(deletion_results['success'])
            sync_results['failed'].extend(deletion_results['failed'])
        else:
            print("❌ 用户取消删除同步")
    
    # 检测移动
    moves = detect_moves(current_joplin_notes, current_obsidian_notes)
    
    # 显示移动预览并确认
    if print_move_preview(moves):
        if confirm_moves():
            move_results = perform_move_sync(moves)
            sync_results['success'].extend(move_results['success'])
            sync_results['failed'].extend(move_results['failed'])
        else:
            print("❌ 用户取消移动同步")
    
    # 1. 更新已匹配的笔记对（根据同步方向）
    if matched_pairs and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']:
        print(f"\n📝 更新 {len(matched_pairs)} 对已匹配笔记...")
        for pair in tqdm(matched_pairs, desc="更新匹配笔记"):
            joplin_note = pair['joplin']
            obsidian_note = pair['obsidian']
            
            # 比较内容，决定是否需要更新
            joplin_content = joplin_note['body']
            obsidian_content = obsidian_note['body']
            
            # 提取同步信息
            joplin_sync_info = extract_sync_info_from_joplin(joplin_content)
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_content)
            
            # 关键：比较实际修改时间和上次同步时间
            # 只有当实际修改时间 > 上次同步时间时，才说明用户真正修改了笔记
            joplin_updated_time = joplin_note.get('user_updated_time', 0)  # 毫秒
            joplin_sync_time = joplin_sync_info.get('notebridge_sync_time', '')
            
            # 获取 Obsidian 文件的修改时间
            obsidian_file_path = obsidian_note['path']
            try:
                obsidian_mtime = os.path.getmtime(obsidian_file_path) * 1000  # 转换为毫秒
            except:
                obsidian_mtime = 0
            obsidian_sync_time = obsidian_sync_info.get('notebridge_sync_time', '')
            
            # 转换同步时间为时间戳（ISO格式 -> Unix timestamp）
            def parse_sync_time(sync_time_str):
                if not sync_time_str:
                    return 0
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(sync_time_str.replace('Z', '+00:00'))
                    return int(dt.timestamp() * 1000)  # 转换为毫秒
                except:
                    return 0
            
            joplin_sync_timestamp = parse_sync_time(joplin_sync_time)
            obsidian_sync_timestamp = parse_sync_time(obsidian_sync_time)
            
            # 判断哪一端有真正的修改
            joplin_has_changes = joplin_updated_time > joplin_sync_timestamp
            obsidian_has_changes = obsidian_mtime > obsidian_sync_timestamp
            
            if joplin_has_changes and not obsidian_has_changes and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
                # 只有 Joplin 端有修改，同步到 Obsidian
                # 需要更新同步时间
                new_sync_info = generate_sync_info(joplin_sync_info.get('notebridge_source', 'joplin'))
                new_sync_info['notebridge_id'] = joplin_sync_info.get('notebridge_id', new_sync_info['notebridge_id'])
                
                # 清理并添加新的同步信息
                cleaned_joplin = clean_duplicate_sync_info(joplin_content)
                updated_joplin_content = add_sync_info_to_joplin_content(cleaned_joplin, new_sync_info)
                cleaned_obsidian = clean_duplicate_sync_info(obsidian_content)
                updated_obsidian_content = add_sync_info_to_obsidian_content(cleaned_joplin, new_sync_info)
                
                success, result = update_obsidian_note(obsidian_note['path'], updated_obsidian_content)
                if success:
                    # 同时更新 Joplin 端的同步时间
                    update_joplin_note(joplin_note['id'], updated_joplin_content)
                    sync_results['updated'].append(f"Joplin → Obsidian: {joplin_note['title']}")
                else:
                    sync_results['failed'].append(f"Joplin → Obsidian: {joplin_note['title']} - {result}")
            elif obsidian_has_changes and not joplin_has_changes and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
                # 只有 Obsidian 端有修改，同步到 Joplin
                # 需要更新同步时间
                new_sync_info = generate_sync_info(obsidian_sync_info.get('notebridge_source', 'obsidian'))
                new_sync_info['notebridge_id'] = obsidian_sync_info.get('notebridge_id', new_sync_info['notebridge_id'])
                
                # 清理并添加新的同步信息
                cleaned_obsidian = clean_duplicate_sync_info(obsidian_content)
                updated_obsidian_content = add_sync_info_to_obsidian_content(cleaned_obsidian, new_sync_info)
                cleaned_joplin = clean_duplicate_sync_info(joplin_content)
                updated_joplin_content = add_sync_info_to_joplin_content(cleaned_obsidian, new_sync_info)
                
                success, result = update_joplin_note(joplin_note['id'], updated_joplin_content)
                if success:
                    # 同时更新 Obsidian 端的同步时间
                    update_obsidian_note(obsidian_note['path'], updated_obsidian_content)
                    sync_results['updated'].append(f"Obsidian → Joplin: {obsidian_note['title']}")
                else:
                    sync_results['failed'].append(f"Obsidian → Joplin: {obsidian_note['title']} - {result}")
            elif joplin_has_changes and obsidian_has_changes:
                # 两端都有修改，需要手动解决冲突
                print(f"\n⚠️ 冲突: {joplin_note['title']} 两端都有修改，跳过")
                sync_results['failed'].append(f"冲突: {joplin_note['title']} - 两端都有修改")
    
    # 2. 同步新笔记到 Obsidian（根据同步方向）
    if unmatched_joplin and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
        print(f"\n📝 同步 {len(unmatched_joplin)} 条新笔记到 Obsidian...")
        for note in tqdm(unmatched_joplin, desc="Joplin → Obsidian"):
            # 使用完整的笔记本路径
            notebook_path = note.get('notebook', '未分类')
            success, result = sync_joplin_to_obsidian(note, notebook_path)
            if success:
                sync_results['created'].append(f"Joplin → Obsidian: {note['title']} ({notebook_path})")
            else:
                sync_results['failed'].append(f"Joplin → Obsidian: {note['title']} ({notebook_path}) - {result}")
    
    # 3. 同步新笔记到 Joplin（根据同步方向）
    if unmatched_obsidian and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
        print(f"\n📄 同步 {len(unmatched_obsidian)} 条新笔记到 Joplin...")
        
        # 按文件夹分组，减少重复的笔记本创建操作
        notes_by_folder = {}
        for note in unmatched_obsidian:
            folder_path = note.get('folder', '根目录')
            if folder_path not in notes_by_folder:
                notes_by_folder[folder_path] = []
            notes_by_folder[folder_path].append(note)
        
        print(f"  共需要处理 {len(notes_by_folder)} 个文件夹")
        
        # 按文件夹批量处理
        for folder_path, notes in tqdm(notes_by_folder.items(), desc="处理文件夹"):
            print(f"    正在处理文件夹: {folder_path} ({len(notes)} 条笔记)")
            
            # 预先创建笔记本（只创建一次）
            notebook_id, error = get_or_create_joplin_notebook(folder_path)
            if error:
                print(f"    ❌ 创建笔记本失败: {error}")
                for note in notes:
                    sync_results['failed'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {error}")
                continue
            
            print(f"    ✅ 笔记本准备就绪，开始创建笔记...")
            
            # 批量创建笔记
            folder_start = time.time()
            folder_durations = []
            for note in notes:
                note_start = time.time()
                success, result = sync_obsidian_to_joplin_with_notebook_id(note, notebook_id)
                note_end = time.time()
                folder_durations.append(note_end - note_start)
                if success:
                    sync_results['created'].append(f"Obsidian → Joplin: {note['title']} ({folder_path})")
                else:
                    sync_results['failed'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {result}")
                    print(f"    ❌ 创建笔记失败: {note['title']} - {result}")
            folder_end = time.time()
            avg_time = sum(folder_durations) / len(folder_durations) if folder_durations else 0
            print(f"    ✅ 文件夹 {folder_path} 处理完成，总耗时 {folder_end - folder_start:.2f} 秒，平均每条 {avg_time:.2f} 秒")
    
    # 保存当前同步状态
    save_sync_state(current_joplin_notes, current_obsidian_notes)
    
    return sync_results

def print_sync_results(sync_results):
    """
    打印同步结果（包含删除结果）
    """
    print("\n" + "="*50)
    print("📊 同步结果报告")
    print("="*50)
    
    print(f"\n✅ 成功创建: {len(sync_results['created'])} 条")
    for item in sync_results['created'][:10]:
        print(f"  ✓ {item}")
    if len(sync_results['created']) > 10:
        print(f"  ... 还有 {len(sync_results['created']) - 10} 条")
    
    print(f"\n🔄 成功更新: {len(sync_results['updated'])} 条")
    for item in sync_results['updated'][:10]:
        print(f"  ✓ {item}")
    if len(sync_results['updated']) > 10:
        print(f"  ... 还有 {len(sync_results['updated']) - 10} 条")
    
    if sync_results['deleted']:
        print(f"\n🗑️ 成功删除: {len(sync_results['deleted'])} 条")
        for item in sync_results['deleted'][:10]:
            print(f"  ✓ {item}")
        if len(sync_results['deleted']) > 10:
            print(f"  ... 还有 {len(sync_results['deleted']) - 10} 条")
    
    print(f"\n❌ 同步失败: {len(sync_results['failed'])} 条")
    for item in sync_results['failed'][:5]:
        print(f"  ✗ {item}")
    if len(sync_results['failed']) > 5:
        print(f"  ... 还有 {len(sync_results['failed']) - 5} 条")
    
    total_success = len(sync_results['created']) + len(sync_results['updated']) + len(sync_results['deleted'])
    total_operations = total_success + len(sync_results['failed'])
    
    if total_operations > 0:
        success_rate = (total_success / total_operations) * 100
        print(f"\n📈 同步成功率: {success_rate:.1f}% ({total_success}/{total_operations})")
    
    print("="*50)

# 补全附件功能：可单独运行，扫描所有已同步的Obsidian笔记，补全缺失附件

def deduplicate_same_content_different_ids():
    """
    去重：处理内容相同但ID不同的笔记
    
    通过内容哈希找到内容相同的笔记，然后统一ID或删除重复
    """
    print("\n🔍 启动内容相同但ID不同的去重功能...")
    print("💡 此功能会找到内容完全相同但ID不同的笔记，并统一它们的ID")
    
    # 获取所有笔记
    print("\n正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 第一步：检测同侧ID重复（同一个ID在同侧有多个笔记）
    print("\n🔍 第一步：检测同侧ID重复...")
    id_duplicates = []  # 同侧ID重复的组
    
    # Joplin内部ID重复
    joplin_by_id = {}
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        notebridge_id = sync_info.get('notebridge_id', '')
        if notebridge_id:
            if notebridge_id in joplin_by_id:
                # 发现Joplin内部ID重复
                if notebridge_id not in [dup['id'] for dup in id_duplicates]:
                    id_duplicates.append({
                        'id': notebridge_id,
                        'type': 'joplin',
                        'notes': [joplin_by_id[notebridge_id], note]
                    })
                else:
                    # 添加到已有组
                    for dup in id_duplicates:
                        if dup['id'] == notebridge_id and dup['type'] == 'joplin':
                            dup['notes'].append(note)
                            break
            else:
                joplin_by_id[notebridge_id] = note
    
    # Obsidian内部ID重复
    obsidian_by_id = {}
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        notebridge_id = sync_info.get('notebridge_id', '')
        if notebridge_id:
            if notebridge_id in obsidian_by_id:
                # 发现Obsidian内部ID重复
                if notebridge_id not in [dup['id'] for dup in id_duplicates if dup['type'] == 'obsidian']:
                    id_duplicates.append({
                        'id': notebridge_id,
                        'type': 'obsidian',
                        'notes': [obsidian_by_id[notebridge_id], note]
                    })
                else:
                    # 添加到已有组
                    for dup in id_duplicates:
                        if dup['id'] == notebridge_id and dup['type'] == 'obsidian':
                            dup['notes'].append(note)
                            break
            else:
                obsidian_by_id[notebridge_id] = note
    
    # 第二步：检测内容相同但ID不同的组
    print("🔍 第二步：检测内容相同但ID不同的笔记...")
    content_hash_groups = {}  # content_hash -> [notes]
    
    # 处理Joplin笔记
    for note in tqdm(joplin_notes, desc="处理 Joplin 笔记"):
        # 清理同步信息后计算哈希
        cleaned_content = clean_duplicate_sync_info(note['body'])
        content_hash = calculate_content_hash(cleaned_content)
        
        sync_info = extract_sync_info_from_joplin(note['body'])
        notebridge_id = sync_info.get('notebridge_id', '')
        
        if content_hash not in content_hash_groups:
            content_hash_groups[content_hash] = []
        
        content_hash_groups[content_hash].append({
            'type': 'joplin',
            'note': note,
            'notebridge_id': notebridge_id,
            'title': note.get('title', 'Unknown'),
            'notebook': note.get('notebook', '未分类'),
            'updated_time': note.get('user_updated_time', 0)
        })
    
    # 处理Obsidian笔记
    for note in tqdm(obsidian_notes, desc="处理 Obsidian 笔记"):
        # 清理同步信息后计算哈希
        cleaned_content = clean_duplicate_sync_info(note['body'])
        content_hash = calculate_content_hash(cleaned_content)
        
        sync_info = extract_sync_info_from_obsidian(note['body'])
        notebridge_id = sync_info.get('notebridge_id', '')
        
        if content_hash not in content_hash_groups:
            content_hash_groups[content_hash] = []
        
        try:
            updated_time = int(os.path.getmtime(note['path']) * 1000)
        except:
            updated_time = 0
        
        content_hash_groups[content_hash].append({
            'type': 'obsidian',
            'note': note,
            'notebridge_id': notebridge_id,
            'title': note.get('title', 'Unknown'),
            'folder': note.get('folder', '根目录'),
            'path': note['path'],
            'updated_time': updated_time
        })
    
    # 找出内容相同但ID不同的组
    content_duplicates = []
    for content_hash, notes in content_hash_groups.items():
        if len(notes) < 2:
            continue  # 只有一条笔记，不是重复
        
        # 检查是否有不同的ID
        ids = set()
        for note_info in notes:
            if note_info['notebridge_id']:
                ids.add(note_info['notebridge_id'])
        
        if len(ids) > 1 or (len(ids) == 1 and len(notes) > len(ids)):
            # 有多个不同的ID，或者有些笔记没有ID
            content_duplicates.append({
                'content_hash': content_hash,
                'notes': notes,
                'ids': list(ids)
            })
    
    # 合并两种重复类型
    duplicates = {
        'id_duplicates': id_duplicates,  # 同侧ID重复
        'content_duplicates': content_duplicates  # 内容相同但ID不同
    }
    
    total_duplicates = len(id_duplicates) + len(content_duplicates)
    
    if total_duplicates == 0:
        print("\n✅ 没有发现重复笔记！")
        return
    
    print(f"\n📊 发现重复笔记统计：")
    print(f"   同侧ID重复: {len(id_duplicates)} 组")
    print(f"   内容相同但ID不同: {len(content_duplicates)} 组")
    print(f"   总计: {total_duplicates} 组")
    
    # 统计信息
    total_id_dup_notes = sum(len(dup['notes']) for dup in id_duplicates)
    total_content_dup_notes = sum(len(dup['notes']) for dup in content_duplicates)
    total_duplicate_notes = total_id_dup_notes + total_content_dup_notes
    print(f"   涉及 {total_duplicate_notes} 条笔记")
    
    # 询问处理方式
    print("\n请选择处理方式：")
    print("  1. 手动逐条确认（推荐，可以精确控制）")
    print("  2. 自动处理（保留最早的ID，统一其他笔记）")
    print("  3. 仅预览，不处理")
    
    choice = input("\n请输入选项 [1/2/3]: ").strip()
    
    if choice == '3':
        print("\n📋 预览模式，仅显示重复笔记，不进行任何操作")
        
        # 显示同侧ID重复
        if id_duplicates:
            print(f"\n【同侧ID重复】共 {len(id_duplicates)} 组")
            for i, dup in enumerate(id_duplicates[:5], 1):
                print(f"\n[{i}] ID: {dup['id'][:8]}... ({dup['type']} 内部重复)")
                print(f"    涉及 {len(dup['notes'])} 条笔记")
                for j, note in enumerate(dup['notes'], 1):
                    if dup['type'] == 'joplin':
                        print(f"      {j}. Joplin: {note.get('title', 'Unknown')[:50]} ({note.get('notebook', '未分类')})")
                    else:
                        print(f"      {j}. Obsidian: {note.get('title', 'Unknown')[:50]} ({note.get('folder', '根目录')})")
            if len(id_duplicates) > 5:
                print(f"\n  ... 还有 {len(id_duplicates) - 5} 组同侧ID重复")
        
        # 显示内容相同但ID不同
        if content_duplicates:
            print(f"\n【内容相同但ID不同】共 {len(content_duplicates)} 组")
            for i, dup in enumerate(content_duplicates[:5], 1):
                print(f"\n[{i}] 内容哈希: {dup['content_hash'][:16]}...")
                print(f"    涉及 {len(dup['notes'])} 条笔记，{len(dup['ids'])} 个不同ID")
                for j, note_info in enumerate(dup['notes'], 1):
                    id_str = note_info['notebridge_id'][:8] + '...' if note_info['notebridge_id'] else '无ID'
                    if note_info['type'] == 'joplin':
                        print(f"      {j}. Joplin: {note_info['title'][:50]} ({note_info['notebook']}) - ID: {id_str}")
                    else:
                        print(f"      {j}. Obsidian: {note_info['title'][:50]} ({note_info['folder']}) - ID: {id_str}")
            if len(content_duplicates) > 5:
                print(f"\n  ... 还有 {len(content_duplicates) - 5} 组内容相同但ID不同")
        return
    
    if choice == '2':
        # 自动处理：保留最早的ID
        print("\n🤖 自动处理模式")
        processed_count = 0
        unified_count = 0
        deleted_count = 0
        
        # 先处理同侧ID重复（需要删除重复的笔记）
        if id_duplicates:
            print(f"\n处理同侧ID重复 ({len(id_duplicates)} 组)...")
            for dup in tqdm(id_duplicates, desc="处理同侧ID重复"):
                notes = dup['notes']
                
                # 找出更新时间最早的笔记保留，删除其他的
                best_note = None
                earliest_time = float('inf')
                
                for note in notes:
                    if dup['type'] == 'joplin':
                        updated_time = note.get('user_updated_time', 0)
                    else:
                        try:
                            updated_time = int(os.path.getmtime(note['path']) * 1000)
                        except:
                            updated_time = 0
                    
                    if updated_time < earliest_time:
                        best_note = note
                        earliest_time = updated_time
                
                if not best_note:
                    continue
                
                # 删除其他笔记
                for note in notes:
                    if note == best_note:
                        continue
                    
                    if dup['type'] == 'joplin':
                        success = safe_delete_joplin_note(note['id'])
                        if success:
                            deleted_count += 1
                    else:
                        success = safe_delete_obsidian_file(note['path'])
                        if success:
                            deleted_count += 1
                
                processed_count += 1
        
        # 再处理内容相同但ID不同（需要统一ID）
        if content_duplicates:
            print(f"\n处理内容相同但ID不同 ({len(content_duplicates)} 组)...")
            for dup in tqdm(content_duplicates, desc="处理内容相同但ID不同"):
                notes = dup['notes']
                
                # 找出最早的ID（优先选择有ID的，然后选择更新时间最早的）
                best_id = None
                best_note = None
                earliest_time = float('inf')
                
                for note_info in notes:
                    if note_info['notebridge_id']:
                        # 优先选择有ID的
                        if best_id is None or note_info['updated_time'] < earliest_time:
                            best_id = note_info['notebridge_id']
                            best_note = note_info
                            earliest_time = note_info['updated_time']
                
                if not best_id:
                    # 如果没有ID，选择更新时间最早的
                    for note_info in notes:
                        if note_info['updated_time'] < earliest_time:
                            best_note = note_info
                            earliest_time = note_info['updated_time']
                
                if not best_note:
                    continue
                
                # 获取保留笔记的同步信息
                if best_note['type'] == 'joplin':
                    best_sync_info = extract_sync_info_from_joplin(best_note['note']['body'])
                else:
                    best_sync_info = extract_sync_info_from_obsidian(best_note['note']['body'])
                
                # 统一所有笔记的ID
                sync_info = {
                    'notebridge_id': best_id if best_id else generate_sync_info('joplin' if best_note['type'] == 'joplin' else 'obsidian')['notebridge_id'],
                    'notebridge_source': best_sync_info.get('notebridge_source', 'joplin' if best_note['type'] == 'joplin' else 'obsidian'),
                    'notebridge_sync_time': datetime.now().isoformat(),
                    'notebridge_version': '1'
                }
                
                for note_info in notes:
                    if note_info == best_note:
                        continue  # 跳过保留的笔记
                    
                    if note_info['type'] == 'joplin':
                        # 更新Joplin笔记
                        cleaned_body = clean_duplicate_sync_info(note_info['note']['body'])
                        new_content = add_sync_info_to_joplin_content(cleaned_body, sync_info)
                        success, result = update_joplin_note(note_info['note']['id'], new_content)
                        if success:
                            unified_count += 1
                    else:
                        # 更新Obsidian笔记
                        try:
                            with open(note_info['path'], 'r', encoding='utf-8') as f:
                                content = f.read()
                            cleaned_content = clean_duplicate_sync_info(content)
                            new_content = add_sync_info_to_obsidian_content(cleaned_content, sync_info)
                            with open(note_info['path'], 'w', encoding='utf-8') as f:
                                f.write(new_content)
                            unified_count += 1
                        except Exception as e:
                            print(f"  ⚠️ 更新 Obsidian 笔记失败: {note_info['title']} - {e}")
                
                processed_count += 1
        
        print(f"\n✅ 自动处理完成！")
        print(f"   处理了 {processed_count} 组重复笔记")
        print(f"   删除了 {deleted_count} 条重复笔记（同侧ID重复）")
        print(f"   统一了 {unified_count} 条笔记的ID（内容相同但ID不同）")
        return
    
    if choice == '1':
        # 手动逐条确认
        print("\n✋ 手动确认模式：逐条处理重复笔记")
        processed_count = 0
        unified_count = 0
        deleted_count = 0
        skipped_count = 0
        
        all_duplicates = []
        # 添加同侧ID重复
        for dup in id_duplicates:
            all_duplicates.append({
                'type': 'id_duplicate',
                'data': dup
            })
        # 添加内容相同但ID不同
        for dup in content_duplicates:
            all_duplicates.append({
                'type': 'content_duplicate',
                'data': dup
            })
        
        for i, dup_item in enumerate(all_duplicates, 1):
            dup_type = dup_item['type']
            dup = dup_item['data']
            
            print(f"\n\n{'='*60}")
            if dup_type == 'id_duplicate':
                print(f"[{i}/{len(all_duplicates)}] 同侧ID重复 ({dup['type']} 内部)")
                print(f"{'='*60}")
                print(f"ID: {dup['id'][:8]}...")
                print(f"涉及 {len(dup['notes'])} 条笔记（同一个ID在同侧有多个笔记）\n")
                notes = dup['notes']
            else:
                print(f"[{i}/{len(all_duplicates)}] 内容相同但ID不同")
                print(f"{'='*60}")
                print(f"内容哈希: {dup['content_hash'][:16]}...")
                print(f"涉及 {len(dup['notes'])} 条笔记，{len(dup['ids'])} 个不同ID\n")
                notes = dup['notes']
            
            # 显示所有笔记
            for j, note in enumerate(notes, 1):
                if dup_type == 'id_duplicate':
                    # 同侧ID重复，notes是原始note对象
                    if dup['type'] == 'joplin':
                        updated_time = note.get('user_updated_time', 0)
                        updated_str = datetime.fromtimestamp(updated_time / 1000).strftime('%Y-%m-%d %H:%M:%S') if updated_time > 0 else '未知'
                        print(f"  {j}. Joplin: {note.get('title', 'Unknown')[:60]}")
                        print(f"     笔记本: {note.get('notebook', '未分类')}")
                        print(f"     更新时间: {updated_str}")
                    else:
                        try:
                            updated_time = int(os.path.getmtime(note['path']) * 1000)
                            updated_str = datetime.fromtimestamp(updated_time / 1000).strftime('%Y-%m-%d %H:%M:%S') if updated_time > 0 else '未知'
                        except:
                            updated_str = '未知'
                        print(f"  {j}. Obsidian: {note.get('title', 'Unknown')[:60]}")
                        print(f"     文件夹: {note.get('folder', '根目录')}")
                        print(f"     更新时间: {updated_str}")
                else:
                    # 内容相同但ID不同，notes是note_info对象
                    note_info = note
                    id_str = note_info['notebridge_id'][:8] + '...' if note_info['notebridge_id'] else '无ID'
                    updated_str = datetime.fromtimestamp(note_info['updated_time'] / 1000).strftime('%Y-%m-%d %H:%M:%S') if note_info['updated_time'] > 0 else '未知'
                    
                    if note_info['type'] == 'joplin':
                        print(f"  {j}. Joplin: {note_info['title'][:60]}")
                        print(f"     笔记本: {note_info['notebook']}")
                        print(f"     ID: {id_str}")
                        print(f"     更新时间: {updated_str}")
                    else:
                        print(f"  {j}. Obsidian: {note_info['title'][:60]}")
                        print(f"     文件夹: {note_info['folder']}")
                        print(f"     ID: {id_str}")
                        print(f"     更新时间: {updated_str}")
                print()
            
            # 询问处理方式
            if dup_type == 'id_duplicate':
                print("请选择处理方式：")
                print("  [1-{}] 保留对应编号的笔记，删除其他笔记".format(len(notes)))
                print("  [s] 跳过此组")
                print("  [q] 退出")
            else:
                print("请选择处理方式：")
                print("  [1-{}] 保留对应编号的笔记ID，统一其他笔记".format(len(notes)))
                print("  [s] 跳过此组")
                print("  [q] 退出")
            
            user_choice = input("\n请输入选项: ").strip().lower()
            
            if user_choice == 'q':
                print("\n❌ 用户取消操作")
                break
            elif user_choice == 's':
                skipped_count += 1
                continue
            elif user_choice.isdigit() and 1 <= int(user_choice) <= len(notes):
                keep_index = int(user_choice) - 1
                keep_note = notes[keep_index]
                
                if dup_type == 'id_duplicate':
                    # 同侧ID重复：删除其他笔记
                    for j, note in enumerate(notes):
                        if j == keep_index:
                            continue  # 跳过保留的笔记
                        
                        if dup['type'] == 'joplin':
                            success = safe_delete_joplin_note(note['id'])
                            if success:
                                deleted_count += 1
                                print(f"  ✅ 已删除 Joplin 笔记: {note.get('title', 'Unknown')[:50]}...")
                            else:
                                print(f"  ❌ 删除失败: {note.get('title', 'Unknown')[:50]}...")
                        else:
                            success = safe_delete_obsidian_file(note['path'])
                            if success:
                                deleted_count += 1
                                print(f"  ✅ 已删除 Obsidian 笔记: {note.get('title', 'Unknown')[:50]}...")
                            else:
                                print(f"  ❌ 删除失败: {note.get('title', 'Unknown')[:50]}...")
                    
                    processed_count += 1
                else:
                    # 内容相同但ID不同：统一ID
                    note_info = keep_note
                    
                    # 确定要保留的ID
                    if note_info['notebridge_id']:
                        keep_id = note_info['notebridge_id']
                    else:
                        # 如果没有ID，生成新的
                        keep_id = generate_sync_info('joplin' if note_info['type'] == 'joplin' else 'obsidian')['notebridge_id']
                    
                    # 获取保留笔记的同步信息
                    if note_info['type'] == 'joplin':
                        keep_sync_info = extract_sync_info_from_joplin(note_info['note']['body'])
                    else:
                        keep_sync_info = extract_sync_info_from_obsidian(note_info['note']['body'])
                    
                    # 更新同步信息
                    sync_info = {
                        'notebridge_id': keep_id,
                        'notebridge_source': keep_sync_info.get('notebridge_source', 'joplin' if note_info['type'] == 'joplin' else 'obsidian'),
                        'notebridge_sync_time': datetime.now().isoformat(),
                        'notebridge_version': '1'
                    }
                    
                    # 统一其他笔记的ID
                    for j, note_info_item in enumerate(notes):
                        if j == keep_index:
                            continue  # 跳过保留的笔记
                        
                        if note_info_item['type'] == 'joplin':
                            # 更新Joplin笔记
                            cleaned_body = clean_duplicate_sync_info(note_info_item['note']['body'])
                            new_content = add_sync_info_to_joplin_content(cleaned_body, sync_info)
                            success, result = update_joplin_note(note_info_item['note']['id'], new_content)
                            if success:
                                unified_count += 1
                                print(f"  ✅ 已统一 Joplin 笔记: {note_info_item['title'][:50]}...")
                            else:
                                print(f"  ❌ 统一失败: {note_info_item['title'][:50]}... - {result}")
                        else:
                            # 更新Obsidian笔记
                            try:
                                with open(note_info_item['path'], 'r', encoding='utf-8') as f:
                                    content = f.read()
                                cleaned_content = clean_duplicate_sync_info(content)
                                new_content = add_sync_info_to_obsidian_content(cleaned_content, sync_info)
                                with open(note_info_item['path'], 'w', encoding='utf-8') as f:
                                    f.write(new_content)
                                unified_count += 1
                                print(f"  ✅ 已统一 Obsidian 笔记: {note_info_item['title'][:50]}...")
                            except Exception as e:
                                print(f"  ❌ 统一失败: {note_info_item['title'][:50]}... - {e}")
                    
                    processed_count += 1
            else:
                print("  ⚠️ 无效选项，跳过")
                skipped_count += 1
        
        print(f"\n✅ 手动处理完成！")
        print(f"   处理了 {processed_count} 组重复笔记")
        print(f"   统一了 {unified_count} 条笔记的ID")
        print(f"   跳过了 {skipped_count} 组")
        return
    
    print("\n❌ 无效选项")

def fix_obsidian_attachments():
    """
    扫描Obsidian所有笔记，补全Joplin资源附件
    """
    print("\n🔍 开始补全 Obsidian 附件...")
    notes = get_obsidian_notes()
    fixed_count = 0
    for note in tqdm(notes, desc="补全附件"):
        # 查找所有未下载的资源ID
        resource_ids = extract_joplin_resource_ids(note['body'])
        resource_map = {}
        for rid in resource_ids:
            local_path, filename = download_joplin_resource(rid)
            if filename:
                resource_map[rid] = filename
        if resource_map:
            # 替换链接并保存
            new_content = replace_joplin_resource_links(note['body'], resource_map)
            with open(note['path'], 'w', encoding='utf-8') as f:
                f.write(new_content)
            fixed_count += 1
    print(f"✅ 附件补全完成，共处理 {fixed_count} 个笔记。\n")

def clean_duplicate_sync_info_keep_oldest(content):
    """
    清理笔记内容中的重复同步信息，强制重新生成干净的ID
    """
    # 强制清理所有同步信息（无论多少个）
    # 清理HTML注释中的同步信息
    content = re.sub(r'<!-- notebridge_id: [a-f0-9-]+ -->\s*', '', content)
    content = re.sub(r'<!-- notebridge_sync_time: [^>]+ -->\s*', '', content)
    content = re.sub(r'<!-- notebridge_source: [^>]+ -->\s*', '', content)
    content = re.sub(r'<!-- notebridge_version: [^>]+ -->\s*', '', content)
    
    # 清理YAML中的同步信息
    content = re.sub(r'notebridge_id: [a-f0-9-]+\s*\n', '', content)
    content = re.sub(r'notebridge_sync_time: \'?[^\'\n]+\'?\s*\n', '', content)
    content = re.sub(r'notebridge_source: [^\n]+\s*\n', '', content)
    content = re.sub(r'notebridge_version: [^\n]+\s*\n', '', content)
    
    # 清理空的YAML frontmatter
    content = re.sub(r'^---\s*\n\s*---\s*\n', '', content, flags=re.MULTILINE)
    
    # 重新生成干净的同步信息
    # 判断内容类型：如果包含YAML frontmatter，则按Obsidian格式处理
    has_yaml = bool(re.search(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL))
    
    if has_yaml:
        # Obsidian格式，添加到YAML中
        new_sync_info = generate_sync_info('obsidian')
        content = add_sync_info_to_obsidian_content(content, new_sync_info)
    else:
        # Joplin格式，添加到HTML注释中
        new_sync_info = generate_sync_info('joplin')
        content = add_sync_info_to_joplin_content(content, new_sync_info)
    
    return content

def find_and_remove_duplicates():
    """
    查找并删除重复笔记，清理同步ID
    """
    print("\n🧹 启动自动清理模式...")
    
    # 获取所有笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 记录当前两端持有的 notebridge_id，用于检测缺失笔记
    current_joplin_ids = set()
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            current_joplin_ids.add(sync_info['notebridge_id'])
    
    current_obsidian_ids = set()
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            current_obsidian_ids.add(sync_info['notebridge_id'])
    
    # 记录当前两端持有的 notebridge_id，用于检测缺失笔记
    current_joplin_ids = set()
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            current_joplin_ids.add(sync_info['notebridge_id'])
    
    current_obsidian_ids = set()
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            current_obsidian_ids.add(sync_info['notebridge_id'])
    
    # 1. 清理同步ID
    print("\n🔧 清理同步ID...")
    cleaned_count = 0
    
    # 清理 Joplin 笔记的同步ID
    for note in tqdm(joplin_notes, desc="清理 Joplin 同步ID"):
        original_body = note['body']
        cleaned_body = clean_duplicate_sync_info_keep_oldest(original_body)
        if cleaned_body != original_body:
            success, result = update_joplin_note(note['id'], cleaned_body)
            if success:
                cleaned_count += 1
            else:
                print(f"❌ 清理 Joplin 笔记失败: {note['title']} - {result}")
    
    # 清理 Obsidian 笔记的同步ID
    for note in tqdm(obsidian_notes, desc="清理 Obsidian 同步ID"):
        try:
            with open(note['path'], 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            cleaned_content = clean_duplicate_sync_info_keep_oldest(original_content)
            if cleaned_content != original_content:
                with open(note['path'], 'w', encoding='utf-8') as f:
                    f.write(cleaned_content)
                cleaned_count += 1
        except Exception as e:
            print(f"❌ 清理 Obsidian 笔记失败: {note['title']} - {e}")
    
    print(f"✅ 同步ID清理完成，共清理 {cleaned_count} 条笔记")
    
    # 2. 查找重复笔记
    print("\n🔍 查找重复笔记...")
    
    # 使用新的优化查重功能
    duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
    
    # 3. 删除重复笔记
    print("\n🗑️ 删除重复笔记...")
    deleted_count = 0
    
    # 删除基于notebridge_id的重复
    if duplicates.get('id_duplicates'):
        print(f"处理 {len(duplicates['id_duplicates'])} 对基于ID的重复...")
        for dup in duplicates['id_duplicates']:
            dup_type = dup.get('duplicate_type', 'unknown')
            if dup_type == 'joplin_internal':
                # Joplin内部重复，删除多余的
                success, result = safe_delete_joplin_note(dup['obsidian']['id'])
                if success:
                    deleted_count += 1
                    print(f"  ✅ 删除 Joplin 重复: {dup['obsidian']['title']}")
            elif dup_type == 'obsidian_internal':
                # Obsidian内部重复，删除多余的
                success = safe_delete_obsidian_file(dup['obsidian']['path'])
                if success:
                    deleted_count += 1
                    print(f"  ✅ 删除 Obsidian 重复: {dup['obsidian']['title']}")
    
    # 删除内容哈希相同的重复（保留Joplin版本）
    if duplicates.get('content_hash_duplicates'):
        print(f"处理 {len(duplicates['content_hash_duplicates'])} 对内容哈希相同的重复...")
        for dup in duplicates['content_hash_duplicates']:
            # 保留Joplin版本，删除Obsidian版本
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                deleted_count += 1
                print(f"  ✅ 删除 Obsidian 重复: {dup['obsidian']['title']}")
    
    # 删除标题和内容都相似的重复（保留Joplin版本）
    if duplicates.get('exact_duplicates'):
        print(f"处理 {len(duplicates['exact_duplicates'])} 对标题和内容都相似的重复...")
        for dup in duplicates['exact_duplicates']:
            # 保留Joplin版本，删除Obsidian版本
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                deleted_count += 1
                print(f"  ✅ 删除 Obsidian 重复: {dup['obsidian']['title']}")
    
    print(f"\n✅ 清理完成！")
    print(f"📊 统计结果:")
    print(f"  - 同步ID清理: {cleaned_count} 条笔记")
    print(f"  - 重复笔记删除: {deleted_count} 条笔记")
    
    return cleaned_count, deleted_count

def validate_note_content(content, title):
    """
    验证笔记内容是否安全，过滤可能导致Joplin卡死的内容
    """
    # 检查内容长度
    if len(content) > 1000000:  # 1MB限制
        return False, f"内容过长 ({len(content)} 字符)"
    
    # 检查是否有过多的同步ID（可能导致解析问题）
    id_count = len(re.findall(r'notebridge_id:', content))
    if id_count > 5:
        return False, f"同步ID过多 ({id_count} 个)"
    
    # 检查是否有过多的HTML注释
    comment_count = len(re.findall(r'<!--.*?-->', content, re.DOTALL))
    if comment_count > 20:
        return False, f"HTML注释过多 ({comment_count} 个)"
    
    # 检查是否有异常大的图片链接
    large_image_count = len(re.findall(r'!\[.*?\]\(.*?\.(jpg|jpeg|png|gif|bmp|webp).*?\)', content, re.IGNORECASE))
    if large_image_count > 50:
        return False, f"图片链接过多 ({large_image_count} 个)"
    
    # 检查网络图片链接数量（不跳过，只记录）
    network_image_count = len(re.findall(r'!\[.*?\]\(https?://.*?\)', content))
    if network_image_count > 10:
        return False, f"网络图片链接过多 ({network_image_count} 个)"
    
    # 检查是否有可疑的特殊字符
    suspicious_chars = re.findall(r'[^\x00-\x7F\u4e00-\u9fff\s\.,!?;:()\[\]{}"\'-]', content)
    if len(suspicious_chars) > 100:
        return False, f"包含过多特殊字符 ({len(suspicious_chars)} 个)"
    
    return True, "内容验证通过"

def clean_content_for_joplin(content):
    """
    清理内容，使其适合Joplin处理
    """
    # 1. 保留网络图片链接，不做替换（Joplin会忽略无法访问的图片）
    # 只做基本的内容清理
    
    # 2. 清理可能的特殊字符
    content = content.replace('\ufeff', '')  # 移除BOM
    content = content.replace('\u200b', '')  # 移除零宽空格
    
    # 3. 确保换行符统一
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    # 4. 清理多余的空行
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    
    return content

def safe_sync_obsidian_to_joplin_with_retry(obsidian_note, notebook_id, max_retries=2, timeout=30):
    """
    安全同步Obsidian笔记到Joplin，带重试和跳过机制
    """
    title = obsidian_note['title']
    
    # 1. 内容验证
    try:
        with open(obsidian_note['path'], 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return False, f"读取文件失败: {e}"
    
    # 验证内容
    is_valid, validation_msg = validate_note_content(content, title)
    if not is_valid:
        return False, f"内容验证失败: {validation_msg}"
    
    # 2. 强制清理所有同步信息，重新生成干净的ID
    try:
        cleaned_content = clean_duplicate_sync_info_keep_oldest(content)
        print(f"[清理] {title}: 强制清理所有同步ID，重新生成干净ID")
    except Exception as e:
        return False, f"清理同步信息失败: {e}"
    
    # 3. 清理内容，使其适合Joplin处理
    try:
        joplin_safe_content = clean_content_for_joplin(cleaned_content)
        if joplin_safe_content != cleaned_content:
            print(f"[清理] {title}: 清理特殊字符和格式")
    except Exception as e:
        print(f"[警告] {title}: 内容清理失败，使用原始内容: {e}")
        joplin_safe_content = cleaned_content
    
    # 4. 尝试同步（带重试）
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[同步] 开始同步笔记: {title} (第{attempt}次尝试)")
            start_time = time.time()
            
            # 设置更短的超时时间
            session = requests.Session()
            session.timeout = timeout
            
            # 准备请求数据
            note_data = {
                'title': title,
                'body': joplin_safe_content,
                'parent_id': notebook_id
            }
            
            # 发送请求
            url = f"{joplin_api_base}/notes?token={joplin_token}"
            response = session.post(url, json=note_data)
            
            end_time = time.time()
            duration = end_time - start_time
            
            if response.status_code == 200:
                print(f"[同步] 成功: {title}，耗时 {duration:.2f} 秒")
                return True, "同步成功"
            else:
                error_msg = f"API错误: {response.status_code} - {response.text}"
                print(f"[同步] 失败: {title}，耗时 {duration:.2f} 秒，{error_msg}")
                if attempt < max_retries:
                    print(f"[同步] 第{attempt}次失败，准备重试...")
                    time.sleep(1)  # 短暂等待
                    continue
                else:
                    return False, error_msg
                    
        except requests.exceptions.Timeout:
            end_time = time.time()
            duration = end_time - start_time
            print(f"[同步] 超时: {title}，耗时 {duration:.2f} 秒")
            if attempt < max_retries:
                print(f"[同步] 第{attempt}次超时，准备重试...")
                time.sleep(2)  # 超时后等待更长时间
                continue
            else:
                return False, f"创建笔记超时: {title}"
                
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            print(f"[同步] 异常: {title}，耗时 {duration:.2f} 秒，异常: {e}")
            if attempt < max_retries:
                print(f"[同步] 第{attempt}次异常，准备重试...")
                time.sleep(1)
                continue
            else:
                return False, f"同步异常: {e}"
    
    return False, f"重试{max_retries}次后仍然失败"

def perform_sync_with_skip(matched_pairs, unmatched_joplin, unmatched_obsidian):
    """
    执行同步操作（包含跳过问题笔记的机制，动态更新同步状态）
    """
    sync_results = {
        'success': [],
        'failed': [],
        'updated': [],
        'created': [],
        'deleted': [],
        'skipped': []  # 新增：跳过的笔记
    }
    
    print("\n🚀 开始执行同步...")
    print(f"📡 同步方向: {SYNC_DIRECTION}")
    
    # 检测删除
    current_joplin_notes = get_joplin_notes()
    current_obsidian_notes = get_obsidian_notes()
    deletions = detect_deletions(current_joplin_notes, current_obsidian_notes)
    
    # 显示删除预览并确认
    if print_deletion_preview(deletions):
        if confirm_deletions():
            deletion_results = perform_deletion_sync(deletions)
            sync_results['deleted'].extend(deletion_results['success'])
            sync_results['failed'].extend(deletion_results['failed'])
        else:
            print("❌ 用户取消删除同步")
    
    # 检测移动
    moves = detect_moves(current_joplin_notes, current_obsidian_notes)
    
    # 显示移动预览并确认
    if print_move_preview(moves):
        if confirm_moves():
            move_results = perform_move_sync(moves)
            sync_results['success'].extend(move_results['success'])
            sync_results['failed'].extend(move_results['failed'])
        else:
            print("❌ 用户取消移动同步")
    
    # 动态同步状态：在同步过程中实时更新
    dynamic_sync_state = {
        'joplin_notes': {},
        'obsidian_notes': {}
    }
    
    # 1. 更新已匹配的笔记对（根据同步方向）
    if matched_pairs and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']:
        print(f"\n📝 更新 {len(matched_pairs)} 对已匹配笔记...")
        
        # 定义时间解析函数
        def parse_sync_time(sync_time_str):
            if not sync_time_str:
                return 0
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(sync_time_str.replace('Z', '+00:00'))
                return int(dt.timestamp() * 1000)
            except:
                return 0
        
        for pair in tqdm(matched_pairs, desc="更新匹配笔记"):
            joplin_note = pair['joplin']
            obsidian_note = pair['obsidian']
            
            # 比较内容，决定是否需要更新
            joplin_content = joplin_note['body']
            obsidian_content = obsidian_note['body']
            
            # 提取同步信息
            joplin_sync_info = extract_sync_info_from_joplin(joplin_content)
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_content)
            
            # 如果是通过内容匹配的，且某一端缺少同步信息，先补充同步信息
            if pair.get('needs_sync_info_update', False):
                notebridge_id = pair.get('notebridge_id', '')
                
                # 补充 Obsidian 端的同步信息
                if notebridge_id and not obsidian_sync_info.get('notebridge_id'):
                    # 使用 Joplin 端已有的同步信息
                    sync_info_to_add = {
                        'notebridge_id': notebridge_id,
                        'notebridge_sync_time': joplin_sync_info.get('notebridge_sync_time', datetime.now().isoformat()),
                        'notebridge_source': joplin_sync_info.get('notebridge_source', 'joplin'),
                        'notebridge_version': joplin_sync_info.get('notebridge_version', '1')
                    }
                    new_content = add_sync_info_to_obsidian_content(obsidian_content, sync_info_to_add)
                    success, error = update_obsidian_note(obsidian_note['path'], new_content)
                    if success:
                        obsidian_content = new_content
                        obsidian_sync_info = sync_info_to_add
                        print(f"  🔧 已补充 Obsidian 同步信息: {obsidian_note['title'][:40]}...")
                
                # 补充 Joplin 端的同步信息
                if notebridge_id and not joplin_sync_info.get('notebridge_id'):
                    sync_info_to_add = {
                        'notebridge_id': notebridge_id,
                        'notebridge_sync_time': obsidian_sync_info.get('notebridge_sync_time', datetime.now().isoformat()),
                        'notebridge_source': obsidian_sync_info.get('notebridge_source', 'obsidian'),
                        'notebridge_version': obsidian_sync_info.get('notebridge_version', '1')
                    }
                    new_content = add_sync_info_to_joplin_content(joplin_content, sync_info_to_add)
                    success, error = update_joplin_note(joplin_note['id'], new_content)
                    if success:
                        joplin_content = new_content
                        joplin_sync_info = sync_info_to_add
                        print(f"  🔧 已补充 Joplin 同步信息: {joplin_note['title'][:40]}...")
            
            # 获取实际修改时间
            joplin_updated_time = joplin_note.get('user_updated_time', 0)
            obsidian_file_path = obsidian_note['path']
            try:
                obsidian_mtime = os.path.getmtime(obsidian_file_path) * 1000
            except:
                obsidian_mtime = 0
            
            # 获取上次同步时间
            joplin_sync_time = joplin_sync_info.get('notebridge_sync_time', '')
            obsidian_sync_time = obsidian_sync_info.get('notebridge_sync_time', '')
            joplin_sync_timestamp = parse_sync_time(joplin_sync_time)
            obsidian_sync_timestamp = parse_sync_time(obsidian_sync_time)
            
            # 判断哪一端有真正的修改
            joplin_has_changes = joplin_updated_time > joplin_sync_timestamp
            obsidian_has_changes = obsidian_mtime > obsidian_sync_timestamp
            
            if joplin_has_changes and not obsidian_has_changes and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
                # 只有 Joplin 端有修改，同步到 Obsidian
                # 需要更新同步时间
                new_sync_info = generate_sync_info(joplin_sync_info.get('notebridge_source', 'joplin'))
                new_sync_info['notebridge_id'] = joplin_sync_info.get('notebridge_id', new_sync_info['notebridge_id'])
                
                # 清理并添加新的同步信息
                cleaned_joplin = clean_duplicate_sync_info(joplin_content)
                updated_joplin_content = add_sync_info_to_joplin_content(cleaned_joplin, new_sync_info)
                cleaned_obsidian = clean_duplicate_sync_info(obsidian_content)
                updated_obsidian_content = add_sync_info_to_obsidian_content(cleaned_joplin, new_sync_info)
                
                success, result = update_obsidian_note(obsidian_note['path'], updated_obsidian_content)
                if success:
                    # 同时更新 Joplin 端的同步时间
                    update_joplin_note(joplin_note['id'], updated_joplin_content)
                    sync_results['updated'].append(f"Joplin → Obsidian: {joplin_note['title']}")
                    # 更新动态同步状态
                    notebridge_id = joplin_sync_info.get('notebridge_id')
                    if notebridge_id:
                        dynamic_sync_state['joplin_notes'][notebridge_id] = {
                            'id': joplin_note['id'],
                            'title': joplin_note['title'],
                            'notebook': joplin_note.get('notebook', '未分类'),
                            'path': f"{joplin_note.get('notebook', '未分类')}/{joplin_note['title']}"
                        }
                        dynamic_sync_state['obsidian_notes'][notebridge_id] = {
                            'path': obsidian_note['path'],
                            'title': obsidian_note['title'],
                            'folder': obsidian_note.get('folder', '根目录')
                        }
                else:
                    sync_results['failed'].append(f"Joplin → Obsidian: {joplin_note['title']} - {result}")
            elif obsidian_has_changes and not joplin_has_changes and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
                # 只有 Obsidian 端有修改，同步到 Joplin
                # 需要更新同步时间
                new_sync_info = generate_sync_info(obsidian_sync_info.get('notebridge_source', 'obsidian'))
                new_sync_info['notebridge_id'] = obsidian_sync_info.get('notebridge_id', new_sync_info['notebridge_id'])
                
                # 清理并添加新的同步信息
                cleaned_obsidian = clean_duplicate_sync_info(obsidian_content)
                updated_obsidian_content = add_sync_info_to_obsidian_content(cleaned_obsidian, new_sync_info)
                cleaned_joplin = clean_duplicate_sync_info(joplin_content)
                updated_joplin_content = add_sync_info_to_joplin_content(cleaned_obsidian, new_sync_info)
                
                success, result = update_joplin_note(joplin_note['id'], updated_joplin_content)
                if success:
                    # 同时更新 Obsidian 端的同步时间
                    update_obsidian_note(obsidian_note['path'], updated_obsidian_content)
                    sync_results['updated'].append(f"Obsidian → Joplin: {obsidian_note['title']}")
                    # 更新动态同步状态
                    notebridge_id = obsidian_sync_info.get('notebridge_id')
                    if notebridge_id:
                        dynamic_sync_state['joplin_notes'][notebridge_id] = {
                            'id': joplin_note['id'],
                            'title': joplin_note['title'],
                            'notebook': joplin_note.get('notebook', '未分类'),
                            'path': f"{joplin_note.get('notebook', '未分类')}/{joplin_note['title']}"
                        }
                        dynamic_sync_state['obsidian_notes'][notebridge_id] = {
                            'path': obsidian_note['path'],
                            'title': obsidian_note['title'],
                            'folder': obsidian_note.get('folder', '根目录')
                        }
                else:
                    sync_results['failed'].append(f"Obsidian → Joplin: {obsidian_note['title']} - {result}")
            elif joplin_has_changes and obsidian_has_changes:
                # 两端都有修改，需要手动解决冲突
                print(f"\n⚠️ 冲突: {joplin_note['title']} 两端都有修改，跳过")
                sync_results['failed'].append(f"冲突: {joplin_note['title']} - 两端都有修改")
    
    # 2. 同步新笔记到 Obsidian（根据同步方向）
    if unmatched_joplin and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
        print(f"\n📝 同步 {len(unmatched_joplin)} 条新笔记到 Obsidian...")
        for note in tqdm(unmatched_joplin, desc="Joplin → Obsidian"):
            # 使用完整的笔记本路径
            notebook_path = note.get('notebook', '未分类')
            success, result = sync_joplin_to_obsidian(note, notebook_path)
            if success:
                sync_results['created'].append(f"Joplin → Obsidian: {note['title']} ({notebook_path})")
                # 更新动态同步状态
                sync_info = extract_sync_info_from_joplin(note['body'])
                notebridge_id = sync_info.get('notebridge_id')
                if notebridge_id:
                    dynamic_sync_state['joplin_notes'][notebridge_id] = {
                        'id': note['id'],
                        'title': note['title'],
                        'notebook': note.get('notebook', '未分类'),
                        'path': f"{note.get('notebook', '未分类')}/{note['title']}"
                    }
                    # 这里需要获取实际的文件路径，暂时用占位符
                    dynamic_sync_state['obsidian_notes'][notebridge_id] = {
                        'path': result,  # sync_joplin_to_obsidian 返回的是文件路径
                        'title': note['title'],
                        'folder': notebook_path
                    }
            else:
                sync_results['failed'].append(f"Joplin → Obsidian: {note['title']} ({notebook_path}) - {result}")
    
    # 3. 同步新笔记到 Joplin（根据同步方向，带跳过机制）
    if unmatched_obsidian and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
        print(f"\n📄 同步 {len(unmatched_obsidian)} 条新笔记到 Joplin...")
        
        # 按文件夹分组，减少重复的笔记本创建操作
        notes_by_folder = {}
        for note in unmatched_obsidian:
            folder_path = note.get('folder', '根目录')
            if folder_path not in notes_by_folder:
                notes_by_folder[folder_path] = []
            notes_by_folder[folder_path].append(note)
        
        print(f"  共需要处理 {len(notes_by_folder)} 个文件夹")
        
        # 按文件夹批量处理
        for folder_path, notes in tqdm(notes_by_folder.items(), desc="处理文件夹"):
            print(f"    正在处理文件夹: {folder_path} ({len(notes)} 条笔记)")
            
            # 预先创建笔记本（只创建一次）
            notebook_id, error = get_or_create_joplin_notebook(folder_path)
            if error:
                print(f"    ❌ 创建笔记本失败: {error}")
                for note in notes:
                    sync_results['failed'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {error}")
                continue
            
            print(f"    ✅ 笔记本准备就绪，开始创建笔记...")
            
            # 批量创建笔记（带跳过机制）
            folder_start = time.time()
            success_count = 0
            skip_count = 0
            
            for note in notes:
                success, result = safe_sync_obsidian_to_joplin_with_retry(note, notebook_id)
                if success:
                    sync_results['created'].append(f"Obsidian → Joplin: {note['title']} ({folder_path})")
                    success_count += 1
                    # 更新动态同步状态
                    sync_info = extract_sync_info_from_obsidian(note['body'])
                    notebridge_id = sync_info.get('notebridge_id')
                    if notebridge_id:
                        dynamic_sync_state['obsidian_notes'][notebridge_id] = {
                            'path': note['path'],
                            'title': note['title'],
                            'folder': note.get('folder', '根目录')
                        }
                        dynamic_sync_state['joplin_notes'][notebridge_id] = {
                            'id': result,  # safe_sync_obsidian_to_joplin_with_retry 返回的是笔记ID
                            'title': note['title'],
                            'notebook': folder_path,
                            'path': f"{folder_path}/{note['title']}"
                        }
                else:
                    # 判断是否应该跳过
                    if "超时" in result or "内容验证失败" in result:
                        sync_results['skipped'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {result}")
                        skip_count += 1
                        print(f"    ⚠️ 跳过问题笔记: {note['title']} - {result}")
                    else:
                        sync_results['failed'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {result}")
                        print(f"    ❌ 创建笔记失败: {note['title']} - {result}")
            
            folder_end = time.time()
            print(f"    ✅ 文件夹 {folder_path} 处理完成，总耗时 {folder_end - folder_start:.2f} 秒")
            print(f"      成功: {success_count} 条，跳过: {skip_count} 条，失败: {len(notes) - success_count - skip_count} 条")
    
    # 保存动态同步状态（包含所有已同步的笔记）
    try:
        final_sync_state = {
            'timestamp': datetime.now().isoformat(),
            'joplin_notes': dynamic_sync_state['joplin_notes'],
            'obsidian_notes': dynamic_sync_state['obsidian_notes']
        }
        with open(SYNC_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_sync_state, f, ensure_ascii=False, indent=2)
        print(f"\n💾 同步状态已保存，包含 {len(dynamic_sync_state['joplin_notes'])} 条 Joplin 笔记，{len(dynamic_sync_state['obsidian_notes'])} 条 Obsidian 笔记")
    except Exception as e:
        print(f"⚠️ 保存同步状态失败: {e}")
    
    return sync_results

def print_sync_results_with_skip(sync_results):
    """
    打印同步结果（包含跳过的笔记）
    """
    print("\n" + "="*50)
    print("📊 同步结果统计")
    print("="*50)
    
    if sync_results['created']:
        print(f"\n✅ 成功创建: {len(sync_results['created'])} 条")
        for item in sync_results['created'][:5]:  # 只显示前5条
            print(f"  • {item}")
        if len(sync_results['created']) > 5:
            print(f"  ... 还有 {len(sync_results['created']) - 5} 条")
    
    if sync_results['updated']:
        print(f"\n🔄 成功更新: {len(sync_results['updated'])} 条")
        for item in sync_results['updated'][:5]:
            print(f"  • {item}")
        if len(sync_results['updated']) > 5:
            print(f"  ... 还有 {len(sync_results['updated']) - 5} 条")
    
    if sync_results['deleted']:
        print(f"\n🗑️ 成功删除: {len(sync_results['deleted'])} 条")
        for item in sync_results['deleted'][:5]:
            print(f"  • {item}")
        if len(sync_results['deleted']) > 5:
            print(f"  ... 还有 {len(sync_results['deleted']) - 5} 条")
    
    if sync_results['skipped']:
        print(f"\n⚠️ 跳过笔记: {len(sync_results['skipped'])} 条")
        for item in sync_results['skipped'][:10]:  # 显示更多跳过的笔记
            print(f"  • {item}")
        if len(sync_results['skipped']) > 10:
            print(f"  ... 还有 {len(sync_results['skipped']) - 10} 条")
    
    if sync_results['failed']:
        print(f"\n❌ 同步失败: {len(sync_results['failed'])} 条")
        for item in sync_results['failed'][:10]:
            print(f"  • {item}")
        if len(sync_results['failed']) > 10:
            print(f"  ... 还有 {len(sync_results['failed']) - 10} 条")
    
    # 总结
    total_processed = len(sync_results['created']) + len(sync_results['updated']) + len(sync_results['deleted'])
    total_issues = len(sync_results['failed']) + len(sync_results['skipped'])
    
    print(f"\n📈 总结:")
    print(f"  • 总处理: {total_processed} 条")
    print(f"  • 成功: {total_processed} 条")
    print(f"  • 跳过: {len(sync_results['skipped'])} 条")
    print(f"  • 失败: {len(sync_results['failed'])} 条")
    
    if sync_results['skipped']:
        print(f"\n💡 提示: 有 {len(sync_results['skipped'])} 条笔记被跳过，可能是内容有问题导致Joplin卡死。")
        print("   建议运行 'python notebridge.py clean-duplicates' 清理重复笔记后再试。")

def preprocess_content_for_comparison(content):
    """
    预处理内容用于相似度比较
    - 去除markdown语法
    - 去除HTML标签
    - 标准化空白字符
    - 去除链接和图片引用
    - 更彻底地去除头部信息
    """
    if not content:
        return ""
    
    # 去除HTML注释（同步信息）
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    
    # 去除YAML frontmatter（更彻底的匹配）
    content = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
    content = re.sub(r'^---\s*\n.*?\n---\s*$', '', content, flags=re.DOTALL)
    
    # 去除Joplin同步信息块
    content = re.sub(r'<!--\s*notebridge_sync_info.*?-->', '', content, flags=re.DOTALL)
    
    # 去除Obsidian同步信息块
    content = re.sub(r'<!--\s*notebridge_sync_info.*?-->', '', content, flags=re.DOTALL)
    
    # 去除markdown链接 [text](url) -> text
    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
    
    # 去除markdown图片 ![alt](url) -> alt
    content = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', content)
    
    # 去除HTML标签
    content = re.sub(r'<[^>]+>', '', content)
    
    # 去除markdown语法标记（更彻底）
    content = re.sub(r'[*_`~#]+', '', content)  # 去除粗体、斜体、代码等标记
    content = re.sub(r'^#{1,6}\s+', '', content, flags=re.MULTILINE)  # 去除标题标记
    content = re.sub(r'^\s*[-*+]\s+', '', content, flags=re.MULTILINE)  # 去除列表标记
    content = re.sub(r'^\s*\d+\.\s+', '', content, flags=re.MULTILINE)  # 去除数字列表标记
    content = re.sub(r'^\s*>\s+', '', content, flags=re.MULTILINE)  # 去除引用标记
    content = re.sub(r'^\s*`{3,}.*$', '', content, flags=re.MULTILINE)  # 去除代码块标记
    
    # 去除表格标记
    content = re.sub(r'^\s*\|.*\|.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*[-|:]+\s*$', '', content, flags=re.MULTILINE)
    
    # 去除空行和多余空白
    content = re.sub(r'\n\s*\n', '\n', content)  # 多个空行合并为一个
    content = re.sub(r'[ \t]+', ' ', content)  # 多个空格合并为一个
    content = re.sub(r'\n\s+', '\n', content)  # 行首空白
    content = re.sub(r'\s+\n', '\n', content)  # 行尾空白
    
    # 去除首尾空白和空行
    content = content.strip()
    
    return content

# 添加缓存机制
_content_cache = {}

def get_cached_content_hash(content):
    """
    获取内容的缓存哈希值，避免重复计算
    """
    if content in _content_cache:
        return _content_cache[content]
    
    processed_content = preprocess_content_for_comparison(content)
    content_hash = calculate_content_hash(processed_content)
    _content_cache[content] = content_hash
    return content_hash

def clear_content_cache():
    """
    清空内容缓存
    """
    global _content_cache
    _content_cache.clear()

def find_duplicates_optimized(joplin_notes, obsidian_notes):
    """
    优化版查重功能（优先使用notebridge_id，性能更好，更准确）
    """
    duplicates = {
        'exact_duplicates': [],      # 完全重复（内容哈希相同）
        'title_similar': [],         # 标题相似
        'content_similar': [],       # 内容相似
        'content_hash_duplicates': [], # 内容哈希相同
        'id_duplicates': []          # 基于notebridge_id的重复
    }
    
    print("正在扫描重复内容（优化版）...")
    
    # 0. 首先基于 notebridge_id 检测重复（这是最可靠的）
    joplin_by_id = {}
    obsidian_by_id = {}
    
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            notebridge_id = sync_info['notebridge_id']
            if notebridge_id in joplin_by_id:
                # 发现Joplin内部重复
                duplicates['id_duplicates'].append({
                    'joplin': joplin_by_id[notebridge_id],
                    'obsidian': note,
                    'similarity': 1.0,
                    'duplicate_type': 'joplin_internal'
                })
            else:
                joplin_by_id[notebridge_id] = note
    
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            notebridge_id = sync_info['notebridge_id']
            if notebridge_id in obsidian_by_id:
                # 发现Obsidian内部重复
                duplicates['id_duplicates'].append({
                    'joplin': obsidian_by_id[notebridge_id],
                    'obsidian': note,
                    'similarity': 1.0,
                    'duplicate_type': 'obsidian_internal'
                })
            else:
                obsidian_by_id[notebridge_id] = note
    
    # 1. 基于内容哈希的完全重复检测（排除空笔记）
    joplin_hashes = {}
    obsidian_hashes = {}
    
    # 预处理并计算哈希（使用缓存）
    for note in joplin_notes:
        if not is_empty_note(note['body']):
            content_hash = get_cached_content_hash(note['body'])
            if content_hash:  # 确保预处理后不为空
                joplin_hashes[content_hash] = note
    
    for note in obsidian_notes:
        if not is_empty_note(note['body']):
            content_hash = get_cached_content_hash(note['body'])
            if content_hash:  # 确保预处理后不为空
                obsidian_hashes[content_hash] = note
                if content_hash in joplin_hashes:
                    # 检查是否已经有notebridge_id匹配
                    joplin_note = joplin_hashes[content_hash]
                    joplin_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
                    obsidian_sync_info = extract_sync_info_from_obsidian(note['body'])
                    
                    if (joplin_sync_info.get('notebridge_id') and 
                        obsidian_sync_info.get('notebridge_id') and
                        joplin_sync_info['notebridge_id'] == obsidian_sync_info['notebridge_id']):
                        # 这是同一个笔记，不需要标记为重复
                        continue
                    
                    duplicates['content_hash_duplicates'].append({
                        'joplin': joplin_hashes[content_hash],
                        'obsidian': note,
                        'similarity': 1.0
                    })
    
    # 2. 基于标题的快速预筛选（减少比较次数）
    joplin_by_title = {}
    obsidian_by_title = {}
    
    for note in joplin_notes:
        if not is_empty_note(note['body']):
            title_key = note['title'].lower().strip()
            if title_key not in joplin_by_title:
                joplin_by_title[title_key] = []
            joplin_by_title[title_key].append(note)
    
    for note in obsidian_notes:
        if not is_empty_note(note['body']):
            title_key = note['title'].lower().strip()
            if title_key not in obsidian_by_title:
                obsidian_by_title[title_key] = []
            obsidian_by_title[title_key].append(note)
    
    # 3. 智能相似度检测（只比较标题相似的笔记）
    processed_joplin = set()  # 避免重复处理
    processed_obsidian = set()
    
    for j_title, j_notes in joplin_by_title.items():
        for j_note in j_notes:
            if j_note['id'] in processed_joplin:
                continue
                
            # 找到标题相似的Obsidian笔记
            similar_obsidian_notes = []
            for o_title, o_notes in obsidian_by_title.items():
                title_similarity = fuzz.ratio(j_title, o_title) / 100.0
                if title_similarity >= 0.6:  # 降低标题相似度阈值，提高召回率
                    for o_note in o_notes:
                        if o_note['path'] not in processed_obsidian:
                            similar_obsidian_notes.append((o_note, title_similarity))
            
            # 对标题相似的笔记进行内容比较
            for o_note, title_similarity in similar_obsidian_notes:
                if o_note['path'] in processed_obsidian:
                    continue
                
                # 预处理内容
                j_content = preprocess_content_for_comparison(j_note['body'])
                o_content = preprocess_content_for_comparison(o_note['body'])
                
                if not j_content or not o_content:
                    continue
                
                content_similarity = calculate_similarity(j_content, o_content)
                
                # 根据相似度分类
                if content_similarity >= 0.9 and title_similarity >= 0.8:
                    duplicates['exact_duplicates'].append({
                        'joplin': j_note,
                        'obsidian': o_note,
                        'title_similarity': title_similarity,
                        'content_similarity': content_similarity
                    })
                    processed_joplin.add(j_note['id'])
                    processed_obsidian.add(o_note['path'])
                elif title_similarity >= 0.9:
                    duplicates['title_similar'].append({
                        'joplin': j_note,
                        'obsidian': o_note,
                        'title_similarity': title_similarity,
                        'content_similarity': content_similarity
                    })
                elif content_similarity >= 0.7:
                    duplicates['content_similar'].append({
                        'joplin': j_note,
                        'obsidian': o_note,
                        'title_similarity': title_similarity,
                        'content_similarity': content_similarity
                    })
    
    return duplicates

def interactive_clean_duplicates():
    """
    交互式清理重复笔记，让用户选择保留哪个版本
    """
    print("\n🔍 启动交互式重复笔记清理...")
    
    # 获取笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 使用超快速查重算法
    duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
    
    # 打印查重报告
    print_duplicate_report(duplicates)
    
    total_duplicates = len(duplicates.get('id_duplicates', [])) + len(duplicates.get('sync_time_conflicts', []))
    
    if total_duplicates == 0:
        print("\n✅ 没有发现重复笔记！")
        return
    
    print(f"\n💡 发现 {total_duplicates} 对重复/冲突笔记")
    print("请选择清理策略：")
    print("1. 自动清理同步时间冲突（保留Joplin版本）")
    print("2. 自动清理同步时间冲突（保留Obsidian版本）")
    print("3. 交互式清理（逐个选择）")
    print("4. 跳过清理")
    
    choice = input("\n请输入选择 (1-4): ").strip()
    
    if choice == "1":
        auto_clean_sync_conflicts(duplicates, keep_joplin=True)
    elif choice == "2":
        auto_clean_sync_conflicts(duplicates, keep_joplin=False)
    elif choice == "3":
        interactive_clean_sync_conflicts(duplicates)
    else:
        print("跳过清理。")

def auto_clean_sync_conflicts(duplicates, keep_joplin=True):
    """
    自动清理同步时间冲突
    keep_joplin: True保留Joplin版本，False保留Obsidian版本
    """
    print(f"\n🤖 开始自动清理同步时间冲突（保留{'Joplin' if keep_joplin else 'Obsidian'}版本）...")
    
    cleaned_count = 0
    
    # 清理同步时间冲突
    for dup in duplicates.get('sync_time_conflicts', []):
        if keep_joplin:
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Obsidian: {dup['obsidian']['title']}")
        else:
            success = safe_delete_joplin_note(dup['joplin']['id'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Joplin: {dup['joplin']['title']}")
    
    # 清理ID重复
    for dup in duplicates.get('id_duplicates', []):
        if keep_joplin:
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Obsidian: {dup['obsidian']['title']}")
        else:
            success = safe_delete_joplin_note(dup['joplin']['id'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Joplin: {dup['joplin']['title']}")
    
    print(f"\n✅ 自动清理完成，共清理 {cleaned_count} 对重复笔记")

def interactive_clean_sync_conflicts(duplicates):
    """
    交互式清理同步时间冲突
    """
    print(f"\n🎯 开始交互式清理同步时间冲突...")
    
    all_conflicts = []
    all_conflicts.extend(duplicates.get('sync_time_conflicts', []))
    all_conflicts.extend(duplicates.get('id_duplicates', []))
    
    cleaned_count = 0
    
    for i, dup in enumerate(all_conflicts, 1):
        print(f"\n--- 第 {i}/{len(all_conflicts)} 对冲突笔记 ---")
        print(f"Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
        print(f"Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
        
        if 'joplin_time' in dup:
            print(f"Joplin时间: {dup['joplin_time']}")
            print(f"Obsidian时间: {dup['obsidian_time']}")
            print(f"时间差: {dup['time_diff']} 秒")
        
        print("\n选择操作：")
        print("1. 保留 Joplin 版本，删除 Obsidian")
        print("2. 保留 Obsidian 版本，删除 Joplin")
        print("3. 跳过这对笔记")
        print("4. 查看详细内容对比")
        
        choice = input("请输入选择 (1-4): ").strip()
        
        if choice == "1":
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                cleaned_count += 1
                print("  ✅ 已删除 Obsidian 版本")
        elif choice == "2":
            success = safe_delete_joplin_note(dup['joplin']['id'])
            if success:
                cleaned_count += 1
                print("  ✅ 已删除 Joplin 版本")
        elif choice == "3":
            print("  ⏭️  跳过这对笔记")
        elif choice == "4":
            show_content_comparison(dup)
            print("\n请重新选择操作：")
            continue
        else:
            print("  ❌ 无效选择，跳过")
    
    print(f"\n✅ 交互式清理完成，共清理 {cleaned_count} 对冲突笔记")

def auto_clean_duplicates(duplicates, keep_joplin=True):
    """
    自动清理重复笔记
    keep_joplin: True保留Joplin版本，False保留Obsidian版本
    """
    print(f"\n🤖 开始自动清理（保留{'Joplin' if keep_joplin else 'Obsidian'}版本）...")
    
    cleaned_count = 0
    
    # 清理内容哈希相同的重复
    for dup in duplicates['content_hash_duplicates']:
        if keep_joplin:
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Obsidian: {dup['obsidian']['title']}")
        else:
            success = safe_delete_joplin_note(dup['joplin']['id'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Joplin: {dup['joplin']['title']}")
    
    # 清理标题和内容都相似的重复
    for dup in duplicates['exact_duplicates']:
        if keep_joplin:
            success = safe_delete_obsidian_file(dup['obsidian']['path'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Obsidian: {dup['obsidian']['title']}")
        else:
            success = safe_delete_joplin_note(dup['joplin']['id'])
            if success:
                cleaned_count += 1
                print(f"  ✅ 删除 Joplin: {dup['joplin']['title']}")
    
    print(f"\n✅ 自动清理完成，共清理 {cleaned_count} 对重复笔记")

def manual_clean_duplicates():
    """
    查重后手动逐条确认清理重复笔记
    直接进入逐条确认模式，不需要先选择策略
    """
    print("\n🔍 启动手动逐条确认清理重复笔记...")
    
    # 获取笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 使用优化版查重算法检测内容重复
    print("\n🔍 正在检测重复笔记（内容重复）...")
    duplicates = find_duplicates_optimized(joplin_notes, obsidian_notes)
    
    # 补充检测同步时间冲突
    print("🔍 正在检测同步时间冲突...")
    ultra_fast_duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
    
    # 合并结果
    duplicates['sync_time_conflicts'] = ultra_fast_duplicates.get('sync_time_conflicts', [])
    # 合并ID重复（去重）
    existing_ids = {dup.get('joplin', {}).get('id') for dup in duplicates.get('id_duplicates', [])}
    for dup in ultra_fast_duplicates.get('id_duplicates', []):
        if dup.get('joplin', {}).get('id') not in existing_ids:
            duplicates['id_duplicates'].append(dup)
    
    # 统计所有重复笔记
    all_duplicates = []
    all_duplicates.extend(duplicates.get('id_duplicates', []))
    all_duplicates.extend(duplicates.get('content_hash_duplicates', []))
    all_duplicates.extend(duplicates.get('exact_duplicates', []))
    all_duplicates.extend(duplicates.get('title_similar', []))
    all_duplicates.extend(duplicates.get('content_similar', []))
    all_duplicates.extend(duplicates.get('sync_time_conflicts', []))
    
    if not all_duplicates:
        print("\n✅ 没有发现重复笔记！")
        return
    
    print(f"\n📊 查重结果统计：")
    print(f"  ID重复: {len(duplicates.get('id_duplicates', []))} 对")
    print(f"  内容哈希重复: {len(duplicates.get('content_hash_duplicates', []))} 对")
    print(f"  完全重复: {len(duplicates.get('exact_duplicates', []))} 对")
    print(f"  标题相似: {len(duplicates.get('title_similar', []))} 对")
    print(f"  内容相似: {len(duplicates.get('content_similar', []))} 对")
    print(f"  同步时间冲突: {len(duplicates.get('sync_time_conflicts', []))} 对")
    print(f"  总计: {len(all_duplicates)} 对重复笔记")
    
    print(f"\n💡 将逐条显示重复笔记，请手动确认保留哪个版本")
    input("按 Enter 键开始...")
    
    # 调用逐步清理函数
    interactive_clean_duplicates_step_by_step(duplicates)

def interactive_clean_duplicates_step_by_step(duplicates):
    """
    交互式逐步清理重复笔记
    """
    print(f"\n🎯 开始交互式清理...")
    
    all_duplicates = []
    all_duplicates.extend(duplicates.get('id_duplicates', []))
    all_duplicates.extend(duplicates.get('content_hash_duplicates', []))
    all_duplicates.extend(duplicates.get('exact_duplicates', []))
    all_duplicates.extend(duplicates.get('title_similar', []))
    all_duplicates.extend(duplicates.get('content_similar', []))
    all_duplicates.extend(duplicates.get('sync_time_conflicts', []))
    
    cleaned_count = 0
    
    for i, dup in enumerate(all_duplicates, 1):
        print(f"\n{'='*60}")
        print(f"第 {i}/{len(all_duplicates)} 对重复笔记")
        print(f"{'='*60}")
        
        # 显示重复类型
        if 'duplicate_type' in dup:
            dup_type = dup['duplicate_type']
            if dup_type == 'joplin_internal':
                print(f"⚠️  类型: Joplin 内部重复（相同ID）")
            elif dup_type == 'obsidian_internal':
                print(f"⚠️  类型: Obsidian 内部重复（相同ID）")
        elif 'similarity' in dup and dup.get('similarity') == 1.0:
            print(f"⚠️  类型: 内容哈希完全重复")
        elif 'title_similarity' in dup:
            if dup.get('content_similarity', 0) >= 0.9:
                print(f"⚠️  类型: 完全重复（标题和内容高度相似）")
            elif dup.get('title_similarity', 0) >= 0.9:
                print(f"⚠️  类型: 标题高度相似")
            else:
                print(f"⚠️  类型: 内容相似")
        elif 'joplin_time' in dup:
            print(f"⚠️  类型: 同步时间冲突")
        
        # 根据重复类型显示不同的信息
        dup_type = dup.get('duplicate_type', '')
        
        if dup_type == 'joplin_internal':
            # Joplin 内部重复：两个都是 Joplin 笔记
            print(f"\n📝 Joplin 笔记 1:")
            print(f"   标题: {dup['joplin']['title']}")
            print(f"   笔记本: {dup['joplin']['notebook']}")
            print(f"   ID: {dup['joplin']['id']}")
            
            print(f"\n📝 Joplin 笔记 2 (重复):")
            print(f"   标题: {dup['obsidian']['title']}")
            print(f"   笔记本: {dup['obsidian'].get('notebook', '未知')}")
            print(f"   ID: {dup['obsidian']['id']}")
        elif dup_type == 'obsidian_internal':
            # Obsidian 内部重复：两个都是 Obsidian 笔记
            print(f"\n📝 Obsidian 笔记 1:")
            print(f"   标题: {dup['joplin']['title']}")
            print(f"   文件夹: {dup['joplin'].get('folder', '未知')}")
            print(f"   路径: {dup['joplin'].get('path', '未知')}")
            
            print(f"\n📝 Obsidian 笔记 2 (重复):")
            print(f"   标题: {dup['obsidian']['title']}")
            print(f"   文件夹: {dup['obsidian'].get('folder', '未知')}")
            print(f"   路径: {dup['obsidian'].get('path', '未知')}")
        else:
            # 跨端重复：一个是 Joplin，一个是 Obsidian
            print(f"\n📝 Joplin 笔记:")
            print(f"   标题: {dup['joplin']['title']}")
            print(f"   笔记本: {dup['joplin'].get('notebook', '未知')}")
            if 'joplin_time' in dup:
                print(f"   同步时间: {dup.get('joplin_time', '未知')}")
            
            print(f"\n📝 Obsidian 笔记:")
            print(f"   标题: {dup['obsidian']['title']}")
            print(f"   文件夹: {dup['obsidian'].get('folder', '未知')}")
            if 'obsidian_time' in dup:
                print(f"   同步时间: {dup.get('obsidian_time', '未知')}")
        
        if 'title_similarity' in dup:
            print(f"\n📊 相似度:")
            print(f"   标题相似度: {dup['title_similarity']:.1%}")
            print(f"   内容相似度: {dup['content_similarity']:.1%}")
        elif 'time_diff' in dup:
            print(f"\n📊 时间差: {dup['time_diff']} 秒")
        
        # 根据重复类型显示不同的操作选项
        if dup_type == 'joplin_internal':
            print("\n选择操作：")
            print("1. 保留第一个 Joplin 笔记，删除第二个")
            print("2. 保留第二个 Joplin 笔记，删除第一个")
            print("3. 跳过这对笔记")
            print("4. 查看详细内容对比")
            
            choice = input("请输入选择 (1-4): ").strip()
            
            if choice == "1":
                success = safe_delete_joplin_note(dup['obsidian']['id'])  # 删除第二个
                if success:
                    cleaned_count += 1
                    print("  ✅ 已删除第二个 Joplin 笔记")
            elif choice == "2":
                success = safe_delete_joplin_note(dup['joplin']['id'])  # 删除第一个
                if success:
                    cleaned_count += 1
                    print("  ✅ 已删除第一个 Joplin 笔记")
            elif choice == "4":
                show_content_comparison(dup)
                # 重新选择
                choice = input("请重新选择 (1-3): ").strip()
                if choice == "1":
                    success = safe_delete_joplin_note(dup['obsidian']['id'])
                    if success:
                        cleaned_count += 1
                        print("  ✅ 已删除第二个 Joplin 笔记")
                elif choice == "2":
                    success = safe_delete_joplin_note(dup['joplin']['id'])
                    if success:
                        cleaned_count += 1
                        print("  ✅ 已删除第一个 Joplin 笔记")
            else:
                print("  ⏭️ 跳过这对笔记")
        elif dup_type == 'obsidian_internal':
            print("\n选择操作：")
            print("1. 保留第一个 Obsidian 笔记，删除第二个")
            print("2. 保留第二个 Obsidian 笔记，删除第一个")
            print("3. 跳过这对笔记")
            print("4. 查看详细内容对比")
            
            choice = input("请输入选择 (1-4): ").strip()
            
            if choice == "1":
                success = safe_delete_obsidian_file(dup['obsidian']['path'])  # 删除第二个
                if success:
                    cleaned_count += 1
                    print("  ✅ 已删除第二个 Obsidian 笔记")
            elif choice == "2":
                success = safe_delete_obsidian_file(dup['joplin']['path'])  # 删除第一个
                if success:
                    cleaned_count += 1
                    print("  ✅ 已删除第一个 Obsidian 笔记")
            elif choice == "4":
                show_content_comparison(dup)
                # 重新选择
                choice = input("请重新选择 (1-3): ").strip()
                if choice == "1":
                    success = safe_delete_obsidian_file(dup['obsidian']['path'])
                    if success:
                        cleaned_count += 1
                        print("  ✅ 已删除第二个 Obsidian 笔记")
                elif choice == "2":
                    success = safe_delete_obsidian_file(dup['joplin']['path'])
                    if success:
                        cleaned_count += 1
                        print("  ✅ 已删除第一个 Obsidian 笔记")
            else:
                print("  ⏭️ 跳过这对笔记")
        else:
            # 跨端重复：一个是 Joplin，一个是 Obsidian
            print("\n选择操作：")
            print("1. 保留 Joplin 版本，删除 Obsidian")
            print("2. 保留 Obsidian 版本，删除 Joplin")
            print("3. 跳过这对笔记")
            print("4. 查看详细内容对比")
            
            choice = input("请输入选择 (1-4): ").strip()
            
            if choice == "1":
                success = safe_delete_obsidian_file(dup['obsidian']['path'])
                if success:
                    cleaned_count += 1
                    print("  ✅ 已删除 Obsidian 版本")
            elif choice == "2":
                success = safe_delete_joplin_note(dup['joplin']['id'])
                if success:
                    cleaned_count += 1
                    print("  ✅ 已删除 Joplin 版本")
            elif choice == "4":
                show_content_comparison(dup)
                # 重新选择
                choice = input("请重新选择 (1-3): ").strip()
                if choice == "1":
                    success = safe_delete_obsidian_file(dup['obsidian']['path'])
                    if success:
                        cleaned_count += 1
                        print("  ✅ 已删除 Obsidian 版本")
                elif choice == "2":
                    success = safe_delete_joplin_note(dup['joplin']['id'])
                    if success:
                        cleaned_count += 1
                        print("  ✅ 已删除 Joplin 版本")
            else:
                print("  ⏭️ 跳过这对笔记")
    
    print(f"\n✅ 交互式清理完成，共清理 {cleaned_count} 对重复笔记")

def show_content_comparison(dup):
    """
    显示内容对比
    """
    print("\n📄 内容对比：")
    print("="*50)
    
    dup_type = dup.get('duplicate_type', '')
    
    if dup_type == 'joplin_internal':
        # Joplin 内部重复：两个都是 Joplin 笔记
        j1_content = dup['joplin']['body'][:200] + "..." if len(dup['joplin']['body']) > 200 else dup['joplin']['body']
        j2_content = dup['obsidian']['body'][:200] + "..." if len(dup['obsidian']['body']) > 200 else dup['obsidian']['body']
        
        print("Joplin 笔记 1 内容预览：")
        print(j1_content)
        print("\nJoplin 笔记 2 内容预览：")
        print(j2_content)
    elif dup_type == 'obsidian_internal':
        # Obsidian 内部重复：两个都是 Obsidian 笔记
        o1_content = dup['joplin']['body'][:200] + "..." if len(dup['joplin']['body']) > 200 else dup['joplin']['body']
        o2_content = dup['obsidian']['body'][:200] + "..." if len(dup['obsidian']['body']) > 200 else dup['obsidian']['body']
        
        print("Obsidian 笔记 1 内容预览：")
        print(o1_content)
        print("\nObsidian 笔记 2 内容预览：")
        print(o2_content)
    else:
        # 跨端重复：一个是 Joplin，一个是 Obsidian
        j_content = dup['joplin']['body'][:200] + "..." if len(dup['joplin']['body']) > 200 else dup['joplin']['body']
        o_content = dup['obsidian']['body'][:200] + "..." if len(dup['obsidian']['body']) > 200 else dup['obsidian']['body']
        
        print("Joplin 内容预览：")
        print(j_content)
        print("\nObsidian 内容预览：")
        print(o_content)
    print("="*50)

def find_duplicates_ultra_fast(joplin_notes, obsidian_notes):
    """
    简化版重复检测算法（只检查ID重复和修改时间冲突）
    专注于同步相关的重复问题，性能更优
    """
    duplicates = {
        'id_duplicates': [],         # 基于notebridge_id的重复
        'sync_time_conflicts': []    # 同步时间冲突
    }
    
    print("🚀 正在使用简化算法扫描重复内容...")
    start_time = time.time()
    
    # 第一层：基于 notebridge_id 的快速检测
    print("  第1层：基于ID的快速检测...")
    joplin_by_id = {}
    obsidian_by_id = {}
    
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            notebridge_id = sync_info['notebridge_id']
            if notebridge_id in joplin_by_id:
                duplicates['id_duplicates'].append({
                    'joplin': joplin_by_id[notebridge_id],
                    'obsidian': note,
                    'similarity': 1.0,
                    'duplicate_type': 'joplin_internal'
                })
            else:
                joplin_by_id[notebridge_id] = note
    
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            notebridge_id = sync_info['notebridge_id']
            if notebridge_id in obsidian_by_id:
                duplicates['id_duplicates'].append({
                    'joplin': obsidian_by_id[notebridge_id],
                    'obsidian': note,
                    'similarity': 1.0,
                    'duplicate_type': 'obsidian_internal'
                })
            else:
                obsidian_by_id[notebridge_id] = note
    
    # 第二层：检查同步时间冲突
    print("  第2层：检查同步时间冲突...")
    for joplin_id, joplin_note in joplin_by_id.items():
        if joplin_id in obsidian_by_id:
            obsidian_note = obsidian_by_id[joplin_id]
            
            # 提取同步时间
            joplin_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
            
            joplin_time = joplin_sync_info.get('notebridge_sync_time', '')
            obsidian_time = obsidian_sync_info.get('notebridge_sync_time', '')
            
            # 如果两个版本都有同步时间，检查是否冲突
            if joplin_time and obsidian_time:
                try:
                    # 解析时间字符串
                    j_time = datetime.fromisoformat(joplin_time.replace('Z', '+00:00'))
                    o_time = datetime.fromisoformat(obsidian_time.replace('Z', '+00:00'))
                    
                    # 如果时间差小于1秒，可能是冲突
                    time_diff = abs((j_time - o_time).total_seconds())
                    if time_diff < 1:
                        duplicates['sync_time_conflicts'].append({
                            'joplin': joplin_note,
                            'obsidian': obsidian_note,
                            'joplin_time': joplin_time,
                            'obsidian_time': obsidian_time,
                            'time_diff': time_diff
                        })
                except Exception as e:
                    # 时间解析失败，跳过
                    continue
    
    end_time = time.time()
    detection_time = end_time - start_time
    print(f"✅ 检测完成，耗时 {detection_time:.2f} 秒")
    
    # 打印简化统计信息
    print_simplified_detection_statistics(joplin_notes, obsidian_notes, duplicates, detection_time)
    
    return duplicates

def calculate_similarity_advanced(text1, text2):
    """
    高级相似度计算，专门处理去掉头部信息后的内容比较
    """
    if not text1 or not text2:
        return 0.0
    
    # 基础相似度
    base_similarity = SequenceMatcher(None, text1, text2).ratio()
    
    # 如果基础相似度很高，直接返回
    if base_similarity >= 0.95:
        return base_similarity
    
    # 计算核心内容相似度（去除开头和结尾的空白）
    def get_core_content(text):
        lines = text.split('\n')
        # 去除开头和结尾的空行
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return '\n'.join(lines)
    
    core1 = get_core_content(text1)
    core2 = get_core_content(text2)
    
    if core1 and core2:
        core_similarity = SequenceMatcher(None, core1, core2).ratio()
        # 取基础相似度和核心相似度的最大值
        return max(base_similarity, core_similarity)
    
    return base_similarity

def detect_content_duplicates_without_headers(joplin_notes, obsidian_notes):
    """
    专门检测去掉头部信息后内容相同的重复笔记
    """
    print("🔍 检测去掉头部信息后的内容重复...")
    
    duplicates = []
    processed_joplin = set()
    processed_obsidian = set()
    
    for j_note in joplin_notes:
        if j_note['id'] in processed_joplin or is_empty_note(j_note['body']):
            continue
            
        j_content = preprocess_content_for_comparison(j_note['body'])
        if not j_content:
            continue
        
        for o_note in obsidian_notes:
            if o_note['path'] in processed_obsidian or is_empty_note(o_note['body']):
                continue
                
            o_content = preprocess_content_for_comparison(o_note['body'])
            if not o_content:
                continue
            
            # 使用高级相似度计算
            similarity = calculate_similarity_advanced(j_content, o_content)
            
            if similarity >= 0.95:  # 高相似度阈值
                # 检查是否已经有notebridge_id匹配
                j_sync_info = extract_sync_info_from_joplin(j_note['body'])
                o_sync_info = extract_sync_info_from_obsidian(o_note['body'])
                
                if (j_sync_info.get('notebridge_id') and 
                    o_sync_info.get('notebridge_id') and
                    j_sync_info['notebridge_id'] == o_sync_info['notebridge_id']):
                    # 这是同一个笔记，跳过
                    continue
                
                duplicates.append({
                    'joplin': j_note,
                    'obsidian': o_note,
                    'similarity': similarity,
                    'type': 'content_without_headers'
                })
                processed_joplin.add(j_note['id'])
                processed_obsidian.add(o_note['path'])
                break
    
    print(f"  发现 {len(duplicates)} 对去掉头部信息后内容相同的重复")
    return duplicates

def print_simplified_detection_statistics(joplin_notes, obsidian_notes, duplicates, detection_time):
    """
    打印简化版检测统计信息
    """
    print("\n" + "="*60)
    print("📊 简化检测统计报告")
    print("="*60)
    
    # 基础统计
    print(f"📝 笔记总数：")
    print(f"  Joplin: {len(joplin_notes)} 条")
    print(f"  Obsidian: {len(obsidian_notes)} 条")
    
    # 重复统计
    total_duplicates = 0
    print(f"\n🔍 重复检测结果：")
    
    if duplicates.get('id_duplicates'):
        count = len(duplicates['id_duplicates'])
        total_duplicates += count
        print(f"  🚨 ID重复: {count} 对（最严重）")
    
    if duplicates.get('sync_time_conflicts'):
        count = len(duplicates['sync_time_conflicts'])
        total_duplicates += count
        print(f"  ⚠️  同步时间冲突: {count} 对")
    
    if total_duplicates == 0:
        print("  ✅ 没有发现重复问题")
    
    print(f"\n⏱️  检测耗时: {detection_time:.2f} 秒")

def print_detection_statistics(joplin_notes, obsidian_notes, duplicates, detection_time):
    """
    打印检测统计信息
    """
    print("\n" + "="*60)
    print("📊 检测统计报告")
    print("="*60)
    
    # 基础统计
    print(f"📝 笔记总数：")
    print(f"  Joplin: {len(joplin_notes)} 条")
    print(f"  Obsidian: {len(obsidian_notes)} 条")
    print(f"  总计: {len(joplin_notes) + len(obsidian_notes)} 条")
    
    # 性能统计
    print(f"\n⚡ 性能统计：")
    print(f"  检测耗时: {detection_time:.2f} 秒")
    print(f"  平均速度: {(len(joplin_notes) + len(obsidian_notes)) / detection_time:.1f} 条/秒")
    
    # 重复统计
    print(f"\n🔍 重复检测结果：")
    id_dups = len(duplicates.get('id_duplicates', []))
    hash_dups = len(duplicates.get('content_hash_duplicates', []))
    exact_dups = len(duplicates.get('exact_duplicates', []))
    title_dups = len(duplicates.get('title_similar', []))
    content_dups = len(duplicates.get('content_similar', []))
    header_dups = len(duplicates.get('content_without_headers', []))
    
    total_dups = id_dups + hash_dups + exact_dups + title_dups + content_dups + header_dups
    
    print(f"  ID重复: {id_dups} 对")
    print(f"  内容哈希重复: {hash_dups} 对")
    print(f"  完全重复: {exact_dups} 对")
    print(f"  标题相似: {title_dups} 对")
    print(f"  内容相似: {content_dups} 对")
    print(f"  去头部后重复: {header_dups} 对")
    print(f"  总计重复: {total_dups} 对")
    
    # 重复率统计
    total_notes = len(joplin_notes) + len(obsidian_notes)
    if total_notes > 0:
        duplicate_rate = (total_dups * 2) / total_notes * 100
        print(f"\n📈 重复率: {duplicate_rate:.1f}%")
        
        if duplicate_rate > 20:
            print("⚠️  重复率较高，建议及时清理")
        elif duplicate_rate > 10:
            print("💡 重复率中等，建议适当清理")
        else:
            print("✅ 重复率较低，状态良好")
    
    # 缓存统计
    cache_size = len(_content_cache)
    print(f"\n💾 缓存统计：")
    print(f"  缓存条目: {cache_size} 个")
    if cache_size > 0:
        print(f"  缓存命中率: 高（避免重复计算）")
    
    print("="*60)

def quick_duplicate_test():
    """
    快速重复检测测试，用于验证新算法的性能
    """
    print("🧪 开始快速重复检测测试...")
    
    # 获取笔记
    print("正在获取笔记数据...")
    joplin_notes = get_joplin_notes()
    obsidian_notes = get_obsidian_notes()
    
    print(f"获取到 {len(joplin_notes)} 条 Joplin 笔记，{len(obsidian_notes)} 条 Obsidian 笔记")
    
    # 测试旧算法
    print("\n📊 测试旧算法性能...")
    start_time = time.time()
    old_duplicates = find_duplicates_optimized(joplin_notes, obsidian_notes)
    old_time = time.time() - start_time
    
    # 清空缓存
    clear_content_cache()
    
    # 测试新算法
    print("\n📊 测试新算法性能...")
    start_time = time.time()
    new_duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
    new_time = time.time() - start_time
    
    # 性能对比
    print("\n" + "="*60)
    print("🏁 性能对比结果")
    print("="*60)
    print(f"旧算法耗时: {old_time:.2f} 秒")
    print(f"新算法耗时: {new_time:.2f} 秒")
    
    if new_time > 0:
        speedup = old_time / new_time
        print(f"性能提升: {speedup:.1f}x")
        
        if speedup >= 2:
            print("🎉 性能提升显著！")
        elif speedup >= 1.5:
            print("👍 性能有所提升")
        else:
            print("📈 性能略有提升")
    
    # 检测结果对比
    print(f"\n🔍 检测结果对比：")
    
    def count_duplicates(duplicates):
        return (len(duplicates.get('id_duplicates', [])) +
                len(duplicates.get('content_hash_duplicates', [])) +
                len(duplicates.get('exact_duplicates', [])) +
                len(duplicates.get('title_similar', [])) +
                len(duplicates.get('content_similar', [])) +
                len(duplicates.get('content_without_headers', [])))
    
    old_count = count_duplicates(old_duplicates)
    new_count = count_duplicates(new_duplicates)
    
    print(f"旧算法检测到: {old_count} 对重复")
    print(f"新算法检测到: {new_count} 对重复")
    
    if new_count > old_count:
        print(f"🎯 新算法多检测到 {new_count - old_count} 对重复（更准确）")
    elif new_count < old_count:
        print(f"⚠️  新算法少检测到 {old_count - new_count} 对重复")
    else:
        print("✅ 检测结果一致")
    
    print("="*60)

def find_title_similar_notes_only(joplin_notes, obsidian_notes, similarity_threshold=0.7):
    """
    只检测标题相似的笔记，让用户手工决定
    similarity_threshold: 标题相似度阈值，默认0.7（70%）
    """
    print(f"🔍 正在检测标题相似度 ≥ {similarity_threshold*100:.0f}% 的笔记...")
    start_time = time.time()
    
    similar_notes = []
    processed_pairs = set()  # 避免重复检测同一对笔记
    
    # 按标题长度分组，只比较长度相近的标题
    joplin_by_length = {}
    obsidian_by_length = {}
    
    for note in joplin_notes:
        title_length = len(note['title'])
        if title_length not in joplin_by_length:
            joplin_by_length[title_length] = []
        joplin_by_length[title_length].append(note)
    
    for note in obsidian_notes:
        title_length = len(note['title'])
        if title_length not in obsidian_by_length:
            obsidian_by_length[title_length] = []
        obsidian_by_length[title_length].append(note)
    
    # 只比较标题长度相近的笔记（±5个字符）
    for j_length, j_notes in joplin_by_length.items():
        for j_note in j_notes:
            # 找到标题长度相近的Obsidian笔记
            similar_obsidian_notes = []
            for o_length in range(max(1, j_length - 5), j_length + 6):
                if o_length in obsidian_by_length:
                    for o_note in obsidian_by_length[o_length]:
                        # 避免重复检测
                        pair_key = (j_note['id'], o_note['path'])
                        if pair_key in processed_pairs:
                            continue
                        
                        # 计算标题相似度
                        title_similarity = fuzz.ratio(j_note['title'], o_note['title']) / 100.0
                        
                        if title_similarity >= similarity_threshold:
                            similar_obsidian_notes.append((o_note, title_similarity))
                            processed_pairs.add(pair_key)
            
            # 如果找到相似的笔记，添加到结果中
            for o_note, title_similarity in similar_obsidian_notes:
                similar_notes.append({
                    'joplin': j_note,
                    'obsidian': o_note,
                    'title_similarity': title_similarity,
                    'joplin_title': j_note['title'],
                    'obsidian_title': o_note['title'],
                    'joplin_notebook': j_note.get('notebook', '未知'),
                    'obsidian_folder': o_note.get('folder', '未知')
                })
    
    end_time = time.time()
    detection_time = end_time - start_time
    
    print(f"✅ 检测完成，耗时 {detection_time:.2f} 秒")
    print(f"📊 发现 {len(similar_notes)} 对标题相似的笔记")
    
    return similar_notes

def interactive_title_similarity_check(similar_notes):
    """
    交互式检查标题相似的笔记，让用户手工决定
    """
    if not similar_notes:
        print("✅ 没有发现标题相似的笔记！")
        return
    
    print(f"\n🎯 开始交互式检查 {len(similar_notes)} 对标题相似的笔记...")
    print("="*80)
    
    # 按相似度排序，相似度高的优先显示
    similar_notes.sort(key=lambda x: x['title_similarity'], reverse=True)
    
    duplicates_to_clean = []
    skipped_pairs = []
    
    for i, pair in enumerate(similar_notes, 1):
        print(f"\n--- 第 {i}/{len(similar_notes)} 对 ---")
        print(f"📝 Joplin: {pair['joplin_title']}")
        print(f"   笔记本: {pair['joplin_notebook']}")
        print(f"📝 Obsidian: {pair['obsidian_title']}")
        print(f"   文件夹: {pair['obsidian_folder']}")
        print(f"🎯 标题相似度: {pair['title_similarity']:.1%}")
        
        # 显示内容预览
        j_content = pair['joplin']['body'][:200] + "..." if len(pair['joplin']['body']) > 200 else pair['joplin']['body']
        o_content = pair['obsidian']['body'][:200] + "..." if len(pair['obsidian']['body']) > 200 else pair['obsidian']['body']
        
        print(f"\n📄 Joplin内容预览:")
        print(f"   {j_content}")
        print(f"\n📄 Obsidian内容预览:")
        print(f"   {o_content}")
        
        print(f"\n选择操作：")
        print("1. 这是重复笔记，删除Obsidian版本")
        print("2. 这是重复笔记，删除Joplin版本")
        print("3. 这不是重复，跳过")
        print("4. 查看完整内容对比")
        print("5. 退出检查")
        
        while True:
            choice = input("\n请输入选择 (1-5): ").strip()
            
            if choice == "1":
                duplicates_to_clean.append({
                    'joplin': pair['joplin'],
                    'obsidian': pair['obsidian'],
                    'action': 'delete_obsidian',
                    'reason': '用户确认重复'
                })
                print("✅ 标记为重复，将删除Obsidian版本")
                break
            elif choice == "2":
                duplicates_to_clean.append({
                    'joplin': pair['joplin'],
                    'obsidian': pair['obsidian'],
                    'action': 'delete_joplin',
                    'reason': '用户确认重复'
                })
                print("✅ 标记为重复，将删除Joplin版本")
                break
            elif choice == "3":
                skipped_pairs.append(pair)
                print("⏭️  跳过，标记为非重复")
                break
            elif choice == "4":
                show_detailed_comparison(pair)
                print("\n请重新选择操作：")
                continue
            elif choice == "5":
                print("👋 退出检查")
                return duplicates_to_clean, skipped_pairs
            else:
                print("❌ 无效选择，请输入 1-5")
                continue
    
    print(f"\n📊 检查完成！")
    print(f"  标记为重复: {len(duplicates_to_clean)} 对")
    print(f"  标记为非重复: {len(skipped_pairs)} 对")
    
    return duplicates_to_clean, skipped_pairs

def show_detailed_comparison(pair):
    """
    显示详细的笔记内容对比
    """
    print("\n" + "="*80)
    print("📋 详细内容对比")
    print("="*80)
    
    print(f"\n📝 Joplin笔记:")
    print(f"标题: {pair['joplin_title']}")
    print(f"笔记本: {pair['joplin_notebook']}")
    print(f"内容长度: {len(pair['joplin']['body'])} 字符")
    print("-" * 40)
    print(pair['joplin']['body'])
    
    print(f"\n📝 Obsidian笔记:")
    print(f"标题: {pair['obsidian_title']}")
    print(f"文件夹: {pair['obsidian_folder']}")
    print(f"内容长度: {len(pair['obsidian']['body'])} 字符")
    print("-" * 40)
    print(pair['obsidian']['body'])
    
    print("="*80)

def execute_title_similarity_cleanup(duplicates_to_clean):
    """
    执行标题相似度清理
    """
    if not duplicates_to_clean:
        print("✅ 没有需要清理的重复笔记")
        return
    
    print(f"\n🧹 开始清理 {len(duplicates_to_clean)} 对重复笔记...")
    
    success_count = 0
    failed_count = 0
    
    for dup in duplicates_to_clean:
        try:
            if dup['action'] == 'delete_obsidian':
                success = safe_delete_obsidian_file(dup['obsidian']['path'])
                if success:
                    print(f"  ✅ 删除 Obsidian: {dup['obsidian_title']}")
                    success_count += 1
                else:
                    print(f"  ❌ 删除失败 Obsidian: {dup['obsidian_title']}")
                    failed_count += 1
            elif dup['action'] == 'delete_joplin':
                success = safe_delete_joplin_note(dup['joplin']['id'])
                if success:
                    print(f"  ✅ 删除 Joplin: {dup['joplin_title']}")
                    success_count += 1
                else:
                    print(f"  ❌ 删除失败 Joplin: {dup['joplin_title']}")
                    failed_count += 1
        except Exception as e:
            print(f"  ❌ 清理出错: {e}")
            failed_count += 1
    
    print(f"\n📊 清理完成！")
    print(f"  成功: {success_count} 个")
    print(f"  失败: {failed_count} 个")

def quick_title_similarity_check():
    """
    快速标题相似度检测主函数
    """
    print("🎯 启动快速标题相似度检测模式...")
    print("📝 此模式只检测标题相似的笔记，让你手工决定哪些是重复的")
    
    # 获取笔记
    print("\n正在获取笔记数据...")
    joplin_notes = get_joplin_notes()
    obsidian_notes = get_obsidian_notes()
    
    print(f"获取到 {len(joplin_notes)} 条 Joplin 笔记，{len(obsidian_notes)} 条 Obsidian 笔记")
    
    # 询问相似度阈值
    print(f"\n🔧 设置检测参数：")
    print("标题相似度阈值（建议70%-90%）：")
    print("  70% - 检测更多可能的重复（包括部分相似）")
    print("  80% - 平衡检测数量和准确性")
    print("  90% - 只检测高度相似的标题")
    
    while True:
        try:
            threshold_input = input("\n请输入相似度阈值 (70-90，默认80): ").strip()
            if not threshold_input:
                similarity_threshold = 0.8
                break
            similarity_threshold = int(threshold_input) / 100.0
            if 0.7 <= similarity_threshold <= 0.9:
                break
            else:
                print("❌ 请输入70-90之间的数字")
        except ValueError:
            print("❌ 请输入有效的数字")
    
    print(f"✅ 设置相似度阈值为 {similarity_threshold*100:.0f}%")
    
    # 检测标题相似的笔记
    similar_notes = find_title_similar_notes_only(joplin_notes, obsidian_notes, similarity_threshold)
    
    if not similar_notes:
        print("✅ 没有发现标题相似的笔记！")
        return
    
    # 交互式检查
    duplicates_to_clean, skipped_pairs = interactive_title_similarity_check(similar_notes)
    
    if not duplicates_to_clean:
        print("✅ 没有标记为重复的笔记")
        return
    
    # 确认清理
    print(f"\n⚠️  确认清理 {len(duplicates_to_clean)} 对重复笔记？")
    print("这将永久删除选中的笔记，无法恢复！")
    
    confirm = input("输入 'yes' 确认清理，其他任意键取消: ").strip().lower()
    
    if confirm == 'yes':
        execute_title_similarity_cleanup(duplicates_to_clean)
    else:
        print("❌ 取消清理操作")

def find_joplin_imported_notes_in_obsidian(obsidian_notes):
    """
    检测Obsidian中来自Joplin的笔记
    """
    print("🔍 正在检测Obsidian中来自Joplin的笔记...")
    
    joplin_imported_notes = []
    other_notes = []
    
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        
        if sync_info.get('notebridge_source') == 'joplin':
            joplin_imported_notes.append({
                'note': note,
                'sync_info': sync_info,
                'notebridge_id': sync_info.get('notebridge_id'),
                'sync_time': sync_info.get('sync_time'),
                'version': sync_info.get('version', '1')
            })
        else:
            other_notes.append(note)
    
    print(f"📊 检测结果：")
    print(f"  来自Joplin的笔记: {len(joplin_imported_notes)} 条")
    print(f"  其他笔记: {len(other_notes)} 条")
    
    return joplin_imported_notes, other_notes

def check_note_modification_status(joplin_imported_notes, joplin_notes):
    """
    检查笔记是否在Obsidian中被修改过
    """
    print("🔍 正在检查笔记修改状态...")
    
    # 建立Joplin笔记的映射
    joplin_by_id = {}
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            joplin_by_id[sync_info['notebridge_id']] = note
    
    unmodified_notes = []
    modified_notes = []
    orphaned_notes = []  # 在Obsidian中但Joplin中不存在的笔记
    
    for obsidian_note_info in joplin_imported_notes:
        notebridge_id = obsidian_note_info['notebridge_id']
        obsidian_note = obsidian_note_info['note']
        
        if notebridge_id in joplin_by_id:
            # 找到对应的Joplin笔记，比较内容
            joplin_note = joplin_by_id[notebridge_id]
            
            # 预处理内容进行比较
            j_content = preprocess_content_for_comparison(joplin_note['body'])
            o_content = preprocess_content_for_comparison(obsidian_note['body'])
            
            # 计算相似度
            similarity = calculate_similarity_advanced(j_content, o_content)
            
            if similarity >= 0.95:  # 95%以上相似认为是未修改
                unmodified_notes.append({
                    'obsidian_note': obsidian_note,
                    'joplin_note': joplin_note,
                    'similarity': similarity,
                    'sync_info': obsidian_note_info['sync_info']
                })
            else:
                modified_notes.append({
                    'obsidian_note': obsidian_note,
                    'joplin_note': joplin_note,
                    'similarity': similarity,
                    'sync_info': obsidian_note_info['sync_info']
                })
        else:
            # 在Obsidian中存在但在Joplin中不存在
            orphaned_notes.append(obsidian_note_info)
    
    print(f"📊 修改状态检查结果：")
    print(f"  未修改的笔记: {len(unmodified_notes)} 条")
    print(f"  已修改的笔记: {len(modified_notes)} 条")
    print(f"  孤立笔记（Joplin中已删除）: {len(orphaned_notes)} 条")
    
    return unmodified_notes, modified_notes, orphaned_notes

def interactive_clean_joplin_imported_notes():
    """
    交互式清理Obsidian中来自Joplin的笔记
    """
    print("🧹 启动Obsidian中Joplin导入笔记清理模式...")
    print("📝 此功能将检测并清理Obsidian中来自Joplin的笔记")
    
    # 获取笔记
    print("\n正在获取笔记数据...")
    joplin_notes = get_joplin_notes()
    obsidian_notes = get_obsidian_notes()
    
    print(f"获取到 {len(joplin_notes)} 条 Joplin 笔记，{len(obsidian_notes)} 条 Obsidian 笔记")
    
    # 检测来自Joplin的笔记
    joplin_imported_notes, other_notes = find_joplin_imported_notes_in_obsidian(obsidian_notes)
    
    if not joplin_imported_notes:
        print("✅ Obsidian中没有发现来自Joplin的笔记！")
        return
    
    # 检查修改状态
    unmodified_notes, modified_notes, orphaned_notes = check_note_modification_status(
        joplin_imported_notes, joplin_notes
    )
    
    print(f"\n📋 清理选项：")
    print("1. 删除所有来自Joplin的笔记（包括已修改的）")
    print("2. 只删除未修改的笔记（推荐）")
    print("3. 只删除孤立的笔记（Joplin中已删除的）")
    print("4. 查看详细列表后选择")
    print("5. 取消操作")
    
    while True:
        choice = input("\n请输入选择 (1-5): ").strip()
        
        if choice == "1":
            notes_to_delete = joplin_imported_notes
            print(f"⚠️  将删除所有 {len(notes_to_delete)} 条来自Joplin的笔记")
            break
        elif choice == "2":
            notes_to_delete = [item['obsidian_note'] for item in unmodified_notes]
            print(f"✅ 将删除 {len(notes_to_delete)} 条未修改的笔记")
            break
        elif choice == "3":
            notes_to_delete = [item['note'] for item in orphaned_notes]
            print(f"🗑️  将删除 {len(notes_to_delete)} 条孤立的笔记")
            break
        elif choice == "4":
            show_detailed_imported_notes_list(unmodified_notes, modified_notes, orphaned_notes)
            print("\n请重新选择操作：")
            continue
        elif choice == "5":
            print("❌ 取消操作")
            return
        else:
            print("❌ 无效选择，请输入 1-5")
            continue
    
    if not notes_to_delete:
        print("✅ 没有需要删除的笔记")
        return
    
    # 确认删除
    print(f"\n⚠️  确认删除 {len(notes_to_delete)} 条笔记？")
    print("这将永久删除选中的笔记，无法恢复！")
    
    confirm = input("输入 'yes' 确认删除，其他任意键取消: ").strip().lower()
    
    if confirm == 'yes':
        execute_bulk_delete(notes_to_delete)
    else:
        print("❌ 取消删除操作")

def show_detailed_imported_notes_list(unmodified_notes, modified_notes, orphaned_notes):
    """
    显示详细的导入笔记列表
    """
    print("\n" + "="*80)
    print("📋 详细笔记列表")
    print("="*80)
    
    if unmodified_notes:
        print(f"\n📝 未修改的笔记 ({len(unmodified_notes)} 条):")
        for i, item in enumerate(unmodified_notes[:10], 1):
            print(f"  {i}. {item['obsidian_note']['title']} (相似度: {item['similarity']:.1%})")
        if len(unmodified_notes) > 10:
            print(f"  ... 还有 {len(unmodified_notes) - 10} 条")
    
    if modified_notes:
        print(f"\n📝 已修改的笔记 ({len(modified_notes)} 条):")
        for i, item in enumerate(modified_notes[:10], 1):
            print(f"  {i}. {item['obsidian_note']['title']} (相似度: {item['similarity']:.1%})")
        if len(modified_notes) > 10:
            print(f"  ... 还有 {len(modified_notes) - 10} 条")
    
    if orphaned_notes:
        print(f"\n📝 孤立的笔记 ({len(orphaned_notes)} 条):")
        for i, item in enumerate(orphaned_notes[:10], 1):
            print(f"  {i}. {item['note']['title']}")
        if len(orphaned_notes) > 10:
            print(f"  ... 还有 {len(orphaned_notes) - 10} 条")
    
    print("="*80)

def execute_bulk_delete(notes_to_delete):
    """
    执行批量删除
    """
    print(f"\n🧹 开始删除 {len(notes_to_delete)} 条笔记...")
    
    success_count = 0
    failed_count = 0
    
    for i, note in enumerate(notes_to_delete, 1):
        try:
            print(f"  正在删除 ({i}/{len(notes_to_delete)}): {note['title']}")
            success = safe_delete_obsidian_file(note['path'])
            
            if success:
                success_count += 1
                print(f"    ✅ 删除成功")
            else:
                failed_count += 1
                print(f"    ❌ 删除失败")
                
        except Exception as e:
            failed_count += 1
            print(f"    ❌ 删除出错: {e}")
    
    print(f"\n📊 删除完成！")
    print(f"  成功: {success_count} 条")
    print(f"  失败: {failed_count} 条")
    
    if success_count > 0:
        print(f"\n💡 建议：")
        print(f"  现在可以运行 'python notebridge.py sync --force' 重新同步所有笔记")

def debug_sync_info_extraction():
    """
    调试同步信息提取功能
    """
    print("🔍 调试同步信息提取功能...")
    
    # 获取一些Obsidian笔记样本
    obsidian_notes = get_obsidian_notes()
    
    print(f"获取到 {len(obsidian_notes)} 条 Obsidian 笔记")
    
    # 检查前10条笔记的同步信息
    joplin_imported_count = 0
    other_count = 0
    
    for i, note in enumerate(obsidian_notes[:10], 1):
        print(f"\n--- 第 {i} 条笔记 ---")
        print(f"标题: {note['title']}")
        print(f"路径: {note['path']}")
        
        # 提取同步信息
        sync_info = extract_sync_info_from_obsidian(note['body'])
        print(f"同步信息: {sync_info}")
        
        # 检查是否包含同步信息
        if sync_info.get('notebridge_source') == 'joplin':
            joplin_imported_count += 1
            print("✅ 检测到来自Joplin的笔记")
        else:
            other_count += 1
            print("❌ 未检测到同步信息或来源不是Joplin")
        
        # 显示内容的前200个字符
        content_preview = note['body'][:200] + "..." if len(note['body']) > 200 else note['body']
        print(f"内容预览: {content_preview}")
    
    print(f"\n📊 前10条笔记统计：")
    print(f"  来自Joplin: {joplin_imported_count} 条")
    print(f"  其他: {other_count} 条")
    
    # 搜索包含同步信息的笔记
    print(f"\n🔍 搜索包含同步信息的笔记...")
    sync_info_notes = []
    
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info:  # 有任何同步信息
            sync_info_notes.append({
                'note': note,
                'sync_info': sync_info
            })
    
    print(f"找到 {len(sync_info_notes)} 条包含同步信息的笔记")
    
    if sync_info_notes:
        print(f"\n📋 同步信息示例：")
        for i, item in enumerate(sync_info_notes[:5], 1):
            print(f"  {i}. {item['note']['title']}")
            print(f"     同步信息: {item['sync_info']}")
    
    # 搜索包含"notebridge_source"的笔记
    print(f"\n🔍 搜索包含'notebridge_source'的笔记...")
    source_notes = []
    
    for note in obsidian_notes:
        if 'notebridge_source' in note['body']:
            source_notes.append(note)
    
    print(f"找到 {len(source_notes)} 条包含'notebridge_source'的笔记")
    
    if source_notes:
        print(f"\n📋 包含'notebridge_source'的笔记示例：")
        for i, note in enumerate(source_notes[:3], 1):
            print(f"  {i}. {note['title']}")
            # 显示包含notebridge_source的行
            lines = note['body'].split('\n')
            for line in lines:
                if 'notebridge_source' in line:
                    print(f"     行: {line.strip()}")
                    break

def clean_unmodified_joplin_imports():
    """
    清理Obsidian中来自Joplin且未修改过的笔记
    通过比较notebridge_sync_time和文件修改时间来判断
    """
    print("🧹 清理Obsidian中未修改的Joplin导入笔记...")
    
    # 获取笔记
    print("正在获取Obsidian笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"获取到 {len(obsidian_notes)} 条 Obsidian 笔记")
    
    # 检测来自Joplin的笔记
    joplin_imported_notes = []
    other_notes = []
    
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        
        if sync_info.get('notebridge_source') == 'joplin':
            joplin_imported_notes.append({
                'note': note,
                'sync_info': sync_info,
                'sync_time': sync_info.get('notebridge_sync_time'),
                'file_path': note['path']
            })
        else:
            other_notes.append(note)
    
    print(f"📊 检测结果：")
    print(f"  来自Joplin的笔记: {len(joplin_imported_notes)} 条")
    print(f"  其他笔记: {len(other_notes)} 条")
    
    if not joplin_imported_notes:
        print("✅ 没有发现来自Joplin的笔记！")
        return
    
    # 检查哪些笔记未修改过
    unmodified_notes = []
    modified_notes = []
    
    for note_info in joplin_imported_notes:
        note = note_info['note']
        sync_time_str = note_info['sync_time']
        file_path = note_info['file_path']
        
        if not sync_time_str:
            # 没有同步时间，跳过
            continue
        
        try:
            # 解析同步时间
            sync_time = datetime.fromisoformat(sync_time_str.replace('Z', '+00:00'))
            
            # 获取文件修改时间
            file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            
            # 如果文件修改时间在同步时间之后，说明被修改过
            if file_mtime > sync_time:
                modified_notes.append(note_info)
            else:
                unmodified_notes.append(note_info)
                
        except Exception as e:
            print(f"⚠️  处理笔记 '{note['title']}' 时出错: {e}")
            # 出错时保守处理，不删除
            modified_notes.append(note_info)
    
    print(f"\n📊 修改状态检查结果：")
    print(f"  未修改的笔记: {len(unmodified_notes)} 条")
    print(f"  已修改的笔记: {len(modified_notes)} 条")
    
    if not unmodified_notes:
        print("✅ 没有发现未修改的Joplin导入笔记！")
        return
    
    # 显示要删除的笔记列表
    print(f"\n📋 将要删除的未修改笔记（前10条）：")
    for i, note_info in enumerate(unmodified_notes[:10], 1):
        note = note_info['note']
        sync_time = note_info['sync_time']
        print(f"  {i}. {note['title']} (同步时间: {sync_time})")
    
    if len(unmodified_notes) > 10:
        print(f"  ... 还有 {len(unmodified_notes) - 10} 条")
    
    # 确认删除
    print(f"\n⚠️  确认删除 {len(unmodified_notes)} 条未修改的Joplin导入笔记？")
    print("这将永久删除选中的笔记，无法恢复！")
    
    confirm = input("输入 'yes' 确认删除，其他任意键取消: ").strip().lower()
    
    if confirm == 'yes':
        execute_bulk_delete([note_info['note'] for note_info in unmodified_notes])
    else:
        print("❌ 取消删除操作")

def check_and_fix_sync_headers(content, note_title=""):
    """
    检查并修复单个笔记的重复同步头部（预防性检查）
    """
    # 检查是否有重复的同步信息
    joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', content)
    yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', content)
    
    # 如果发现重复，立即修复
    if len(joplin_ids) + len(yaml_ids) > 1:
        if note_title:
            print(f"  🔧 检测到重复头部，正在修复: {note_title}")
        return clean_duplicate_sync_info(content)
    
    return content

def fix_duplicate_sync_headers():
    """
    修复重复的同步信息头部（增强版）
    专门处理HTML注释和YAML格式混合的重复头部问题
    """
    print("\n🔧 开始修复重复的同步信息头部...")
    
    # 获取所有笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 修复 Joplin 笔记
    print("\n🔧 修复 Joplin 笔记中的重复头部...")
    fixed_joplin_count = 0
    
    for note in tqdm(joplin_notes, desc="修复 Joplin 笔记"):
        original_body = note['body']
        cleaned_body = clean_duplicate_sync_info(original_body)
        
        if cleaned_body != original_body:
            success, result = update_joplin_note(note['id'], cleaned_body)
            if success:
                fixed_joplin_count += 1
                print(f"  ✅ 修复: {note['title']}")
            else:
                print(f"❌ 修复 Joplin 笔记失败: {note['title']} - {result}")
    
    # 修复 Obsidian 笔记
    print("\n🔧 修复 Obsidian 笔记中的重复头部...")
    fixed_obsidian_count = 0
    
    for note in tqdm(obsidian_notes, desc="修复 Obsidian 笔记"):
        try:
            with open(note['path'], 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            cleaned_content = clean_duplicate_sync_info(original_content)
            if cleaned_content != original_content:
                with open(note['path'], 'w', encoding='utf-8') as f:
                    f.write(cleaned_content)
                fixed_obsidian_count += 1
                print(f"  ✅ 修复: {note['title']}")
        except Exception as e:
            print(f"❌ 修复 Obsidian 笔记失败: {note['title']} - {e}")
    
    print(f"\n✅ 修复完成！")
    print(f"  修复 Joplin 笔记: {fixed_joplin_count} 条")
    print(f"  修复 Obsidian 笔记: {fixed_obsidian_count} 条")
    print(f"  总计修复: {fixed_joplin_count + fixed_obsidian_count} 条")
    
    if fixed_joplin_count + fixed_obsidian_count > 0:
        print(f"\n💡 修复说明：")
        print(f"  - 清理了重复的HTML注释格式同步信息")
        print(f"  - 清理了重复的YAML格式同步信息")
        print(f"  - 保留了最新的同步信息")
        print(f"  - 统一了同步信息格式")
    else:
        print(f"\n✅ 没有发现需要修复的重复头部！")

def manual_confirm_sync():
    """
    手工确认模式同步：每条笔记同步前都需要人工确认
    这样可以确保不会出现重复头部等问题
    """
    print("\n🔄 启动手工确认模式同步...")
    print(f"📡 同步方向: {SYNC_DIRECTION}")
    print("💡 每条笔记同步前都会显示详情，需要您确认")
    
    # 获取笔记
    print("\n正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 记录当前两端持有的 notebridge_id，用于检测缺失笔记
    current_joplin_ids = set()
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            current_joplin_ids.add(sync_info['notebridge_id'])
    
    current_obsidian_ids = set()
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            current_obsidian_ids.add(sync_info['notebridge_id'])
    
    # 应用同步规则
    joplin_to_sync, obsidian_to_sync = apply_sync_rules(joplin_notes, obsidian_notes)
    
    # 建立ID映射
    print("正在建立ID映射关系...")
    id_mapping = build_id_mapping(joplin_to_sync, obsidian_to_sync)
    
    # 智能匹配笔记
    matched_pairs, unmatched_joplin, unmatched_obsidian, deleted_candidates = smart_match_notes(
        id_mapping, joplin_to_sync, obsidian_to_sync
    )
    
    # 检测移动的笔记
    print("\n🔍 检测笔记移动...")
    moves = detect_moves(joplin_notes, obsidian_notes)
    all_moves = moves['joplin_moves'] + moves['obsidian_moves']
    
    # 统计信息
    print(f"\n📊 同步统计:")
    print(f"  已匹配的笔记对: {len(matched_pairs)} 对")
    print(f"  需要同步到 Obsidian 的新笔记: {len(unmatched_joplin)} 条")
    print(f"  需要同步到 Joplin 的新笔记: {len(unmatched_obsidian)} 条")
    print(f"  可能已删除的笔记: {len(deleted_candidates)} 条")
    print(f"  需要同步的移动: {len(all_moves)} 条")
    
    # 同步结果
    sync_results = {
        'confirmed': 0,
        'skipped': 0,
        'success': 0,
        'failed': 0,
        'details': []
    }
    
    # 加载上次同步状态（用于检查是否重复同步）
    previous_state = load_sync_state()
    previous_joplin_ids = set()
    previous_obsidian_ids = set()
    
    if previous_state:
        previous_joplin_ids = set(previous_state['joplin_notes'].keys())
        previous_obsidian_ids = set(previous_state['obsidian_notes'].keys())
        print(f"\n📋 已加载上次同步状态: {len(previous_joplin_ids)} 条 Joplin 笔记, {len(previous_obsidian_ids)} 条 Obsidian 笔记")
    
    # 建立内容哈希索引（用于快速查找已同步但缺少同步信息的笔记）
    print("\n🔍 建立内容索引...")
    joplin_content_hash_map = {}
    obsidian_content_hash_map = {}
    
    for j_note in joplin_to_sync:
        j_hash = calculate_content_hash(j_note['body'])
        joplin_content_hash_map[j_hash] = j_note
    
    for o_note in obsidian_to_sync:
        o_hash = calculate_content_hash(o_note['body'])
        obsidian_content_hash_map[o_hash] = o_note
    
    print(f"   Joplin 索引: {len(joplin_content_hash_map)} 条")
    print(f"   Obsidian 索引: {len(obsidian_content_hash_map)} 条")
    
    # 1. 处理已匹配的笔记对
    if matched_pairs and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']:
        print(f"\n\n{'='*60}")
        print("📝 开始处理已匹配的笔记对")
        print(f"{'='*60}")
        
        for i, pair in enumerate(matched_pairs, 1):
            joplin_note = pair['joplin']
            obsidian_note = pair['obsidian']
            
            print(f"\n\n[{i}/{len(matched_pairs)}] 笔记对:")
            print(f"  Joplin: {joplin_note['title']} ({joplin_note['notebook']})")
            print(f"  Obsidian: {obsidian_note['title']} ({obsidian_note['folder']})")
            
            # 提取同步信息
            joplin_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
            
            joplin_time = joplin_sync_info.get('notebridge_sync_time', '')
            obsidian_time = obsidian_sync_info.get('notebridge_sync_time', '')
            
            # 检查同步信息
            print(f"\n  同步信息:")
            print(f"    Joplin 最后同步: {joplin_time if joplin_time else '未同步'}")
            print(f"    Obsidian 最后同步: {obsidian_time if obsidian_time else '未同步'}")
            
            # 解析同步时间为datetime对象以便比较
            def parse_sync_time_to_datetime(sync_time_str):
                """将同步时间字符串转换为datetime对象"""
                if not sync_time_str:
                    return None
                # 如果已经是datetime对象，直接返回
                if isinstance(sync_time_str, datetime):
                    return sync_time_str
                try:
                    # 处理ISO格式字符串，支持带或不带时区
                    time_str = sync_time_str.replace('Z', '+00:00')
                    return datetime.fromisoformat(time_str)
                except (ValueError, AttributeError):
                    return None
            
            joplin_time_dt = parse_sync_time_to_datetime(joplin_time)
            obsidian_time_dt = parse_sync_time_to_datetime(obsidian_time)
            
            # 检查是否有重复头部
            joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', joplin_note['body'])
            joplin_yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', joplin_note['body'])
            obsidian_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', obsidian_note['body'])
            obsidian_yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', obsidian_note['body'])
            
            if len(joplin_ids) + len(joplin_yaml_ids) > 1:
                print(f"  ⚠️ Joplin 笔记有重复头部！")
            if len(obsidian_ids) + len(obsidian_yaml_ids) > 1:
                print(f"  ⚠️ Obsidian 笔记有重复头部！")
            
            # 检查同步规则，确保符合配置
            joplin_notebook = joplin_note['notebook']
            obsidian_folder = obsidian_note['folder']
            
            # 检查是否允许 Joplin → Obsidian 同步
            can_joplin_to_obsidian = (
                SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian'] and
                not any(matches_pattern(joplin_notebook, pattern) for pattern in sync_rules['obsidian_to_joplin_only'])
            )
            
            # 检查是否允许 Obsidian → Joplin 同步
            can_obsidian_to_joplin = (
                SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin'] and
                not any(matches_pattern(obsidian_folder, pattern) for pattern in sync_rules['joplin_to_obsidian_only'])
            )
            
            # 如果两个方向都不允许，自动跳过
            if not can_joplin_to_obsidian and not can_obsidian_to_joplin:
                print(f"\n  ⏭️ 自动跳过: 不符合同步规则")
                print(f"     Joplin笔记本: {joplin_notebook}")
                print(f"     Obsidian文件夹: {obsidian_folder}")
                sync_results['skipped'] += 1
                continue
            
            # 检查笔记来源，避免不必要的反向同步
            joplin_source = joplin_sync_info.get('notebridge_source', '')
            obsidian_source = obsidian_sync_info.get('notebridge_source', '')
            
            # 判断同步方向
            sync_direction = None
            warning_message = ""
            
            # 比较时间（处理None值的情况）
            if joplin_time_dt and obsidian_time_dt:
                joplin_newer = joplin_time_dt > obsidian_time_dt
                times_equal = joplin_time_dt == obsidian_time_dt
            elif joplin_time_dt and not obsidian_time_dt:
                joplin_newer = True
                times_equal = False
            elif not joplin_time_dt and obsidian_time_dt:
                joplin_newer = False
                times_equal = False
            else:
                # 两者都没有时间，无法比较，默认不同步
                joplin_newer = False
                times_equal = True
            
            if joplin_newer and can_joplin_to_obsidian:
                # 检查是否是反向同步（Obsidian → Joplin → Obsidian）
                if joplin_source == 'obsidian':
                    # 检查在Joplin端是否真的做了修改
                    # 如果同步时间相同或相近（差距小于1秒），说明没有修改，只是同步过来的
                    if times_equal:
                        print(f"\n  ⏭️ 自动跳过: 此笔记来自 Obsidian 且未在 Joplin 端修改")
                        sync_direction = None  # 不同步
                    else:
                        # 时间不同，说明在Joplin端做了修改，可以同步
                        sync_direction = 'joplin_to_obsidian'
                        print(f"\n  📌 建议: Joplin → Obsidian (在 Joplin 端有修改)")
                else:
                    sync_direction = 'joplin_to_obsidian'
                    print(f"\n  📌 建议: Joplin → Obsidian (Joplin 更新)")
            elif not joplin_newer and not times_equal and can_obsidian_to_joplin:
                # 检查是否是反向同步（Joplin → Obsidian → Joplin）
                if obsidian_source == 'joplin':
                    # 检查在Obsidian端是否真的做了修改
                    if times_equal:
                        print(f"\n  ⏭️ 自动跳过: 此笔记来自 Joplin 且未在 Obsidian 端修改")
                        sync_direction = None  # 不同步
                    else:
                        # 时间不同，说明在Obsidian端做了修改，可以同步
                        sync_direction = 'obsidian_to_joplin'
                        print(f"\n  📌 建议: Obsidian → Joplin (在 Obsidian 端有修改)")
                else:
                    sync_direction = 'obsidian_to_joplin'
                    print(f"\n  📌 建议: Obsidian → Joplin (Obsidian 更新)")
            else:
                print(f"\n  📌 两边内容相同，无需同步")
            
            if sync_direction:
                # 询问是否同步
                choice = input(f"\n  是否执行此同步？ [y/n/q(退出)/s(跳过所有)]: ").strip().lower()
                
                if choice == 'q':
                    print("\n❌ 用户取消同步")
                    break
                elif choice == 's':
                    print("\n⏭️ 跳过剩余所有笔记")
                    sync_results['skipped'] += len(matched_pairs) - i + 1
                    break
                elif choice == 'y':
                    sync_results['confirmed'] += 1
                    
                    # 执行同步
                    if sync_direction == 'joplin_to_obsidian':
                        # 先检查并修复重复头部
                        cleaned_joplin = check_and_fix_sync_headers(joplin_note['body'], joplin_note['title'])
                        
                        # 更新同步时间
                        new_sync_info = generate_sync_info(joplin_sync_info.get('notebridge_source', 'joplin'))
                        new_sync_info['notebridge_id'] = joplin_sync_info.get('notebridge_id', new_sync_info['notebridge_id'])
                        
                        # 清理并添加新的同步信息
                        cleaned_content = clean_duplicate_sync_info(cleaned_joplin)
                        updated_joplin_content = add_sync_info_to_joplin_content(cleaned_content, new_sync_info)
                        updated_obsidian_content = add_sync_info_to_obsidian_content(cleaned_content, new_sync_info)
                        
                        success, result = update_obsidian_note(obsidian_note['path'], updated_obsidian_content)
                        if success:
                            # 同时更新 Joplin 端的同步时间
                            update_joplin_note(joplin_note['id'], updated_joplin_content)
                            sync_results['success'] += 1
                            sync_results['details'].append(f"✅ Joplin → Obsidian: {joplin_note['title']}")
                            print(f"  ✅ 同步成功")
                        else:
                            sync_results['failed'] += 1
                            sync_results['details'].append(f"❌ Joplin → Obsidian: {joplin_note['title']} - {result}")
                            print(f"  ❌ 同步失败: {result}")
                    else:  # obsidian_to_joplin
                        # 先检查并修复重复头部
                        cleaned_obsidian = check_and_fix_sync_headers(obsidian_note['body'], obsidian_note['title'])
                        
                        # 更新同步时间
                        new_sync_info = generate_sync_info(obsidian_sync_info.get('notebridge_source', 'obsidian'))
                        new_sync_info['notebridge_id'] = obsidian_sync_info.get('notebridge_id', new_sync_info['notebridge_id'])
                        
                        # 清理并添加新的同步信息
                        cleaned_content = clean_duplicate_sync_info(cleaned_obsidian)
                        updated_obsidian_content = add_sync_info_to_obsidian_content(cleaned_content, new_sync_info)
                        updated_joplin_content = add_sync_info_to_joplin_content(cleaned_content, new_sync_info)
                        
                        success, result = update_joplin_note(joplin_note['id'], updated_joplin_content)
                        if success:
                            # 同时更新 Obsidian 端的同步时间
                            update_obsidian_note(obsidian_note['path'], updated_obsidian_content)
                            sync_results['success'] += 1
                            sync_results['details'].append(f"✅ Obsidian → Joplin: {obsidian_note['title']}")
                            print(f"  ✅ 同步成功")
                        else:
                            sync_results['failed'] += 1
                            sync_results['details'].append(f"❌ Obsidian → Joplin: {obsidian_note['title']} - {result}")
                            print(f"  ❌ 同步失败: {result}")
                else:
                    sync_results['skipped'] += 1
                    print(f"  ⏭️ 跳过")
            else:
                sync_results['skipped'] += 1
    
    # 2. 处理新笔记到 Obsidian
    if unmatched_joplin and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
        print(f"\n\n{'='*60}")
        print("📝 开始处理需要同步到 Obsidian 的新笔记")
        print(f"{'='*60}")
        
        for i, note in enumerate(unmatched_joplin, 1):
            # 检查笔记是否有效（标题不为空且内容不为空）
            if not note.get('title') or not note.get('title').strip():
                print(f"\n\n[{i}/{len(unmatched_joplin)}] 新笔记:")
                print(f"  ⏭️ 自动跳过: 空标题笔记（可能已删除或无效）")
                sync_results['skipped'] += 1
                continue
            
            # 检查内容是否为空
            if is_empty_note(note.get('body', '')):
                print(f"\n\n[{i}/{len(unmatched_joplin)}] 新笔记:")
                print(f"  标题: {note['title']}")
                print(f"  ⏭️ 自动跳过: 空内容笔记")
                sync_results['skipped'] += 1
                continue
            
            print(f"\n\n[{i}/{len(unmatched_joplin)}] 新笔记:")
            print(f"  标题: {note['title']}")
            print(f"  笔记本: {note['notebook']}")
            print(f"  内容预览: {note['body'][:100]}...")
            
            # 检查是否有重复头部
            joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', note['body'])
            joplin_yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', note['body'])
            
            has_duplicate_headers = len(joplin_ids) + len(joplin_yaml_ids) > 1
            if has_duplicate_headers:
                print(f"  ⚠️ 发现重复头部！")
            
            # 检查同步规则
            notebook_path = note.get('notebook', '未分类')
            
            # 检查是否允许 Joplin → Obsidian 同步
            if any(matches_pattern(notebook_path, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
                print(f"  ⏭️ 自动跳过: 不符合同步规则（{notebook_path} 只允许 Obsidian → Joplin）")
                sync_results['skipped'] += 1
                continue
            
            # 检查笔记来源，如果来自Obsidian且未修改，自动跳过
            # 如果检测到重复头部，先提取 ID（在清理之前）
            notebridge_id = None
            if has_duplicate_headers:
                # 从原始内容中提取所有 ID，选择第一个（通常是最新的）
                all_joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', note['body'])
                all_yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', note['body'])
                all_ids = all_joplin_ids + all_yaml_ids
                if all_ids:
                    # 使用第一个 ID（通常是最新的）
                    notebridge_id = all_ids[0]
                    print(f"  📝 从重复头部中提取到 ID: {notebridge_id[:8]}...")
            
            # 先清理重复头部，确保提取到正确的同步信息
            cleaned_joplin_body = clean_duplicate_sync_info(note['body'])
            sync_info = extract_sync_info_from_joplin(cleaned_joplin_body)
            source = sync_info.get('notebridge_source', '')
            
            # 如果清理后没有提取到 ID，使用之前提取的 ID
            if not sync_info.get('notebridge_id') and notebridge_id:
                sync_info['notebridge_id'] = notebridge_id
                notebridge_id = sync_info['notebridge_id']
            else:
                notebridge_id = sync_info.get('notebridge_id', '')
            
            # 如果检测到重复头部，自动修复并尝试通过 ID 或内容匹配找到对应笔记
            if has_duplicate_headers:
                print(f"  🔧 检测到重复头部，正在尝试恢复匹配关系...")
                
                o_note = None
                found_by_id = False
                
                # 1. 如果有 ID，先尝试通过 ID 在 Obsidian 中查找
                if notebridge_id:
                    if notebridge_id in id_mapping['obsidian_by_id']:
                        o_note = id_mapping['obsidian_by_id'][notebridge_id]
                        found_by_id = True
                        print(f"  ✅ 通过 ID 找到对应的 Obsidian 笔记: {o_note['title'][:50]}...")
                    else:
                        print(f"  🔍 ID {notebridge_id[:8]}... 在 Obsidian 中未找到，尝试通过内容匹配...")
                else:
                    print(f"  🔍 未提取到 ID，尝试通过内容匹配...")
                
                # 2. 如果通过 ID 找不到，尝试通过内容匹配查找
                if not o_note:
                    joplin_content_hash = calculate_content_hash(cleaned_joplin_body)
                    
                    if joplin_content_hash in obsidian_content_hash_map:
                        o_note = obsidian_content_hash_map[joplin_content_hash]
                        print(f"  ✅ 通过内容匹配找到对应的 Obsidian 笔记: {o_note['title'][:50]}...")
                
                # 如果找到了对应的笔记，自动修复并更新同步信息
                if o_note:
                    print(f"  🔧 自动修复重复头部并更新同步信息")
                    
                    # 如果清理后没有提取到 ID，使用找到的 Obsidian 笔记的 ID 或生成新的
                    if not notebridge_id:
                        obsidian_sync_info = extract_sync_info_from_obsidian(o_note['body'])
                        if obsidian_sync_info.get('notebridge_id'):
                            notebridge_id = obsidian_sync_info['notebridge_id']
                            sync_info['notebridge_id'] = notebridge_id
                            print(f"  📝 使用 Obsidian 端的 ID: {notebridge_id[:8]}...")
                        else:
                            # 生成新的同步信息
                            sync_info = generate_sync_info('joplin')
                            notebridge_id = sync_info['notebridge_id']
                            print(f"  📝 生成新的同步 ID: {notebridge_id[:8]}...")
                    
                    # 修复 Joplin 端的重复头部
                    note['body'] = cleaned_joplin_body
                    
                    # 修复 Obsidian 端的重复头部并更新同步信息
                    cleaned_obsidian_body = clean_duplicate_sync_info(o_note['body'])
                    obsidian_content_with_sync = add_sync_info_to_obsidian_content(cleaned_obsidian_body, sync_info)
                    success, result = update_obsidian_note(o_note['path'], obsidian_content_with_sync)
                    
                    if success:
                        # 同时更新 Joplin 端，确保两边都有正确的同步信息
                        joplin_content_with_sync = add_sync_info_to_joplin_content(cleaned_joplin_body, sync_info)
                        update_joplin_note(note['id'], joplin_content_with_sync)
                        print(f"  ✅ 已修复重复头部并更新同步信息")
                        sync_results['skipped'] += 1
                        continue
                    else:
                        print(f"  ⚠️ 修复 Obsidian 端失败: {result}")
                else:
                    print(f"  ⚠️ 未找到对应的 Obsidian 笔记，可能需要手动处理")
            
            force_sync_to_obsidian = False
            if notebridge_id and notebridge_id not in current_obsidian_ids:
                # Obsidian端没有这个ID，需要判断是删除还是重新同步
                sync_time = sync_info.get('notebridge_sync_time', '')
                
                # 先检查上次同步状态
                was_synced_before = previous_state and notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids
                
                if sync_time or was_synced_before:
                    # 有同步记录，说明之前已经同步过，现在 Obsidian 端没有了
                    # 应该作为删除候选，而不是重新同步
                    print(f"  📌 判断依据:")
                    if sync_time:
                        print(f"     - 有同步时间记录: {sync_time}")
                    if was_synced_before:
                        print(f"     - 上次同步状态: 两端都存在")
                    print(f"     - 判断结果: Obsidian 端已删除")
                    
                    # 添加到删除候选列表
                    if not any(c.get('notebridge_id') == notebridge_id for c in deleted_candidates):
                        deleted_candidates.append({
                            'type': 'obsidian_deleted',
                            'note': note,
                            'notebridge_id': notebridge_id,
                            'title': note.get('title', 'Unknown')
                        })
                        print(f"  ➕ 已添加到删除候选列表，将在删除处理阶段询问")
                    
                    sync_results['skipped'] += 1
                    continue
                else:
                    # 没有同步记录，说明从未同步过，应该作为新笔记同步
                    force_sync_to_obsidian = True
                    print(f"  🔁 检测到 Obsidian 端缺失此 ID，允许同步（ID: {notebridge_id[:8]}...）")
            
            # 检查是否是已经同步过的笔记（避免重复同步）
            if not force_sync_to_obsidian and notebridge_id and previous_state:
                # 如果这个 ID 在上次同步中同时存在于两边，说明已经同步过了
                if notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids:
                    print(f"  ⏭️ 自动跳过: 已同步过的笔记（ID: {notebridge_id[:8]}...）")
                    sync_results['skipped'] += 1
                    continue
            
            # 如果笔记来自Obsidian，说明是之前从Obsidian同步过来的
            # 这种情况下不应该再同步回Obsidian（除非在Joplin端做了修改）
            # 但对于未匹配的新笔记，我们无法比较时间戳，所以自动跳过
            if source == 'obsidian' and not force_sync_to_obsidian:
                print(f"  ⏭️ 自动跳过: 此笔记来自 Obsidian，避免反向同步")
                sync_results['skipped'] += 1
                continue
            
            # 尝试通过内容在 Obsidian 中查找（补充检查，使用索引）
            content_hash = calculate_content_hash(cleaned_joplin_body)
            
            if content_hash in obsidian_content_hash_map:
                # 找到内容完全相同的笔记
                o_note = obsidian_content_hash_map[content_hash]
                print(f"  ✅ 通过内容匹配找到对应的 Obsidian 笔记: {o_note['title'][:50]}...")
                
                # 如果 Joplin 端有 ID，更新 Obsidian 端的同步信息
                if notebridge_id:
                    print(f"  🔧 正在更新 Obsidian 端的同步信息（ID: {notebridge_id[:8]}...）")
                    cleaned_obsidian_body = clean_duplicate_sync_info(o_note['body'])
                    obsidian_content_with_sync = add_sync_info_to_obsidian_content(cleaned_obsidian_body, sync_info)
                    success, result = update_obsidian_note(o_note['path'], obsidian_content_with_sync)
                    
                    if success:
                        # 同时更新 Joplin 端，确保两边都有正确的同步信息
                        joplin_content_with_sync = add_sync_info_to_joplin_content(cleaned_joplin_body, sync_info)
                        update_joplin_note(note['id'], joplin_content_with_sync)
                        print(f"  ✅ 已更新同步信息，笔记已匹配")
                        sync_results['skipped'] += 1
                        continue
                    else:
                        print(f"  ⚠️ 更新 Obsidian 端失败: {result}")
                else:
                    print(f"  ⏭️ 自动跳过: 在 Obsidian 中找到内容相同的笔记")
                    print(f"     Obsidian 文件: {o_note['title'][:50]}...")
                    print(f"     提示: 可运行 python add_missing_sync_info.py 批量补充同步信息")
                    sync_results['skipped'] += 1
                    continue
            
            # 询问是否同步
            choice = input(f"\n  是否同步到 Obsidian？ [y/n/q(退出)/s(跳过所有)]: ").strip().lower()
            
            if choice == 'q':
                print("\n❌ 用户取消同步")
                break
            elif choice == 's':
                print("\n⏭️ 跳过剩余所有笔记")
                sync_results['skipped'] += len(unmatched_joplin) - i + 1
                break
            elif choice == 'y':
                sync_results['confirmed'] += 1
                
                # 执行同步
                notebook_path = note.get('notebook', '未分类')
                # 先检查并修复重复头部
                cleaned_content = check_and_fix_sync_headers(note['body'], note['title'])
                note['body'] = cleaned_content
                success, result = sync_joplin_to_obsidian(note, notebook_path)
                if success:
                    sync_results['success'] += 1
                    sync_results['details'].append(f"✅ 新建 Joplin → Obsidian: {note['title']}")
                    print(f"  ✅ 同步成功")
                else:
                    sync_results['failed'] += 1
                    sync_results['details'].append(f"❌ 新建 Joplin → Obsidian: {note['title']} - {result}")
                    print(f"  ❌ 同步失败: {result}")
            else:
                sync_results['skipped'] += 1
                print(f"  ⏭️ 跳过")
    
    # 3. 处理新笔记到 Joplin
    if unmatched_obsidian and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
        print(f"\n\n{'='*60}")
        print("📝 开始处理需要同步到 Joplin 的新笔记")
        print(f"{'='*60}")
        
        for i, note in enumerate(unmatched_obsidian, 1):
            # 检查笔记是否有效（标题不为空且内容不为空）
            if not note.get('title') or not note.get('title').strip():
                print(f"\n\n[{i}/{len(unmatched_obsidian)}] 新笔记:")
                print(f"  ⏭️ 自动跳过: 空标题笔记（可能已删除或无效）")
                sync_results['skipped'] += 1
                continue
            
            # 检查内容是否为空
            if is_empty_note(note.get('body', '')):
                print(f"\n\n[{i}/{len(unmatched_obsidian)}] 新笔记:")
                print(f"  标题: {note['title']}")
                print(f"  ⏭️ 自动跳过: 空内容笔记")
                sync_results['skipped'] += 1
                continue
            
            print(f"\n\n[{i}/{len(unmatched_obsidian)}] 新笔记:")
            print(f"  标题: {note['title']}")
            print(f"  文件夹: {note['folder']}")
            print(f"  内容预览: {note['body'][:100]}...")
            
            # 检查是否有重复头部
            obsidian_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', note['body'])
            obsidian_yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', note['body'])
            
            has_duplicate_headers = len(obsidian_ids) + len(obsidian_yaml_ids) > 1
            if has_duplicate_headers:
                print(f"  ⚠️ 发现重复头部！")
            
            # 检查同步规则
            folder_path = note.get('folder', '根目录')
            
            # 检查是否允许 Obsidian → Joplin 同步
            if any(matches_pattern(folder_path, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
                print(f"  ⏭️ 自动跳过: 不符合同步规则（{folder_path} 只允许 Joplin → Obsidian）")
                sync_results['skipped'] += 1
                continue
            
            # 检查笔记来源，如果来自Joplin且未修改，自动跳过
            sync_info = extract_sync_info_from_obsidian(note['body'])
            source = sync_info.get('notebridge_source', '')
            notebridge_id = sync_info.get('notebridge_id', '')
            
            # 重要检查：如果笔记有 notebridge_id，说明它已经被处理过了
            # 不应该作为"新笔记"重复同步
            force_sync = False
            if notebridge_id:
                if notebridge_id not in current_joplin_ids:
                    # Joplin端没有这个ID，需要判断是删除还是重新同步
                    if source == 'joplin':
                        # 如果来源是joplin，说明原本是从Joplin同步过来的，现在Joplin端没有了
                        # 这应该是删除候选，而不是新笔记
                        print(f"  ⚠️ 检测到 Joplin 端已删除此笔记（ID: {notebridge_id[:8]}...）")
                        print(f"  💡 提示: 此笔记将在删除处理阶段询问是否删除 Obsidian 端")
                        sync_results['skipped'] += 1
                        continue
                    elif source == 'obsidian':
                        # 如果来源是obsidian，说明原本是从Obsidian同步到Joplin的，现在Joplin端删除了
                        # 需要区分两种情况：
                        # 1. 从未同步过（没有 notebridge_sync_time）→ 应该作为新笔记同步
                        # 2. 之前已同步过（有 notebridge_sync_time）→ 需要判断是删除还是重新同步
                        sync_time = sync_info.get('notebridge_sync_time', '')
                        if sync_time:
                            # 有同步记录，说明之前已经同步过，现在 Joplin 端没有了
                            # 使用 should_treat_as_deletion 判断是删除还是重新同步
                            if should_treat_as_deletion(note, sync_info, is_joplin_note=False):
                                # 应该作为删除处理，等待删除阶段处理
                                print(f"  ⚠️ 检测到 Joplin 端删除了来自 Obsidian 的笔记，且 Obsidian 端未修改，视为删除（ID: {notebridge_id[:8]}...）")
                                print(f"  💡 提示: 此笔记将在删除处理阶段询问是否删除 Obsidian 端")
                                sync_results['skipped'] += 1
                                continue
                            else:
                                # Obsidian 端有修改，应该重新同步
                                force_sync = True
                                print(f"  🔁 检测到 Joplin 端删除了来自 Obsidian 的笔记，但 Obsidian 端已修改，将重新同步（ID: {notebridge_id[:8]}...）")
                        else:
                            # 没有同步记录，说明从未同步过，应该作为新笔记同步
                            force_sync = True
                            print(f"  🔁 检测到未同步的 Obsidian 笔记，将同步到 Joplin（ID: {notebridge_id[:8]}...）")
                    else:
                        # 来源不明确或没有来源，需要判断是删除还是新笔记
                        sync_time = sync_info.get('notebridge_sync_time', '')
                        was_synced_before = previous_state and notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids
                        
                        if sync_time or was_synced_before:
                            # 有同步记录，说明之前已经同步过，现在 Joplin 端没有了
                            print(f"  📌 判断依据:")
                            if sync_time:
                                print(f"     - 有同步时间记录: {sync_time}")
                            if was_synced_before:
                                print(f"     - 上次同步状态: 两端都存在")
                            print(f"     - 判断结果: Joplin 端已删除")
                            
                            # 添加到删除候选列表
                            if not any(c.get('notebridge_id') == notebridge_id for c in deleted_candidates):
                                deleted_candidates.append({
                                    'type': 'joplin_deleted',
                                    'note': note,
                                    'notebridge_id': notebridge_id,
                                    'title': note.get('title', 'Unknown')
                                })
                                print(f"  ➕ 已添加到删除候选列表，将在删除处理阶段询问")
                            
                            sync_results['skipped'] += 1
                            continue
                        else:
                            # 没有同步记录，说明从未同步过，应该作为新笔记同步
                            force_sync = True
                            print(f"  🔁 检测到 Joplin 端缺失此 ID，允许同步（ID: {notebridge_id[:8]}...）")
                else:
                    # 检查是否在上次同步状态中
                    if previous_state and notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids:
                        print(f"  ⏭️ 自动跳过: 已同步过的笔记（ID: {notebridge_id[:8]}...）")
                        sync_results['skipped'] += 1
                        continue
                    # 如果笔记来自 Obsidian 并且有 ID，说明是 Obsidian 端的笔记
                    # 它可能已经同步到 Joplin 但被删除了，或者是在匹配阶段没有找到对应项
                    # 无论哪种情况，都不应该作为新笔记重复同步
                    elif source == 'obsidian' and not force_sync:
                        print(f"  ⏭️ 自动跳过: Obsidian 来源的笔记且已有 ID（ID: {notebridge_id[:8]}...）")
                        print(f"     如需重新同步，请删除该笔记的同步信息或查看 Joplin 端是否已存在")
                        sync_results['skipped'] += 1
                        continue
            
            # 如果笔记来自Joplin，说明是之前从Joplin同步过来的
            # 这种情况下不应该再同步回Joplin（除非在Obsidian端做了修改）
            # 但对于未匹配的新笔记，我们无法比较时间戳，所以自动跳过
            if source == 'joplin' and not force_sync:
                print(f"  ⏭️ 自动跳过: 此笔记来自 Joplin，避免反向同步")
                sync_results['skipped'] += 1
                continue
            
            # 尝试通过内容在 Joplin 中查找（补充检查，使用索引）
            print(f"  🔍 计算内容哈希...")
            content_hash = calculate_content_hash(note['body'])
            print(f"     内容哈希: {content_hash[:16]}...")
            print(f"     索引中的笔记数: {len(joplin_content_hash_map)}")
            
            if content_hash in joplin_content_hash_map:
                # 找到内容完全相同的笔记
                j_note = joplin_content_hash_map[content_hash]
                j_sync_info = extract_sync_info_from_joplin(j_note['body'])
                print(f"  ✅ 找到内容匹配！")
                print(f"     Joplin 标题: {j_note['title'][:50]}...")
                if j_sync_info.get('notebridge_id'):
                    print(f"     ID: {j_sync_info['notebridge_id'][:8]}...")
                    print(f"  ⏭️ 自动跳过: 在 Joplin 中找到内容相同的笔记")
                    print(f"     💡 提示: 可运行 python add_missing_sync_info.py 批量补充同步信息")
                    sync_results['skipped'] += 1
                    continue
                else:
                    print(f"     ⚠️ Joplin 端也没有同步 ID")
            else:
                print(f"  ❌ 在 Joplin 索引中未找到匹配")
            
            # 询问是否同步
            choice = input(f"\n  是否同步到 Joplin？ [y/n/q(退出)/s(跳过所有)]: ").strip().lower()
            
            if choice == 'q':
                print("\n❌ 用户取消同步")
                break
            elif choice == 's':
                print("\n⏭️ 跳过剩余所有笔记")
                sync_results['skipped'] += len(unmatched_obsidian) - i + 1
                break
            elif choice == 'y':
                sync_results['confirmed'] += 1
                
                # 执行同步
                folder_path = note.get('folder', '根目录')
                # 先检查并修复重复头部
                cleaned_content = check_and_fix_sync_headers(note['body'], note['title'])
                note['body'] = cleaned_content
                notebook_id, error = get_or_create_joplin_notebook(folder_path)
                if error:
                    sync_results['failed'] += 1
                    sync_results['details'].append(f"❌ 新建 Obsidian → Joplin: {note['title']} - {error}")
                    print(f"  ❌ 同步失败: {error}")
                else:
                    success, result = sync_obsidian_to_joplin_with_notebook_id(note, notebook_id)
                    if success:
                        sync_results['success'] += 1
                        sync_results['details'].append(f"✅ 新建 Obsidian → Joplin: {note['title']}")
                        print(f"  ✅ 同步成功")
                    else:
                        sync_results['failed'] += 1
                        sync_results['details'].append(f"❌ 新建 Obsidian → Joplin: {note['title']} - {result}")
                        print(f"  ❌ 同步失败: {result}")
            else:
                sync_results['skipped'] += 1
                print(f"  ⏭️ 跳过")
    
    # 打印总结
    print(f"\n\n{'='*60}")
    print("📊 手工确认同步结果")
    print(f"{'='*60}")
    print(f"\n✅ 确认同步: {sync_results['confirmed']} 条")
    print(f"✅ 成功: {sync_results['success']} 条")
    print(f"⏭️ 跳过: {sync_results['skipped']} 条")
    print(f"❌ 失败: {sync_results['failed']} 条")
    
    if sync_results['details']:
        print(f"\n详细结果:")
        for detail in sync_results['details'][:20]:
            print(f"  {detail}")
        if len(sync_results['details']) > 20:
            print(f"  ... 还有 {len(sync_results['details']) - 20} 条")
    
    # 3. 处理可能已删除的笔记
    if deleted_candidates:
        print(f"\n\n{'='*60}")
        print("🗑️ 开始处理可能已删除的笔记")
        print(f"{'='*60}")
        
        for i, candidate in enumerate(deleted_candidates, 1):
            print(f"\n\n[{i}/{len(deleted_candidates)}] 可能已删除的笔记:")
            
            if candidate['type'] == 'joplin_deleted':
                note = candidate['note']
                print(f"  类型: Obsidian 笔记（Joplin 端可能已删除）")
                print(f"  标题: {note['title']}")
                print(f"  文件夹: {note.get('folder', '根目录')}")
                print(f"  路径: {note['path']}")
                print(f"  ID: {candidate['notebridge_id'][:8]}...")
                
                # 显示判断依据
                sync_info = extract_sync_info_from_obsidian(note['body'])
                print(f"\n  📊 判断依据:")
                print(f"    - 笔记来源: {sync_info.get('notebridge_source', '未知')}")
                if sync_info.get('notebridge_sync_time'):
                    print(f"    - 最后同步: {sync_info['notebridge_sync_time']}")
                else:
                    print(f"    - 最后同步: 从未同步")
                
                # 检查是否在上次同步状态中存在
                if previous_state:
                    notebridge_id = candidate['notebridge_id']
                    was_synced = notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids
                    if was_synced:
                        print(f"    - 上次同步: ✅ 两端都存在")
                        print(f"    - 判断结果: 📌 Joplin 端已删除（置信度高）")
                    else:
                        print(f"    - 上次同步: ⚠️ 状态不明")
                        print(f"    - 判断结果: ❓ 无法确定是删除还是新增")

                folder = note.get('folder', '根目录')
                if any(matches_pattern(folder, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
                    print(f"  ⏭️ 自动跳过: {folder} 配置为仅 Joplin → Obsidian，同步方向不允许删除 Obsidian 笔记")
                    sync_results['skipped'] += 1
                    continue
                
                print(f"\n  请选择操作:")
                print(f"    1. 删除 Obsidian 端（保持与 Joplin 同步）")
                print(f"    2. 复制到 Joplin 端（恢复此笔记）")
                print(f"    3. 跳过此笔记")
                print(f"    q. 退出")
                print(f"    s. 跳过所有剩余删除操作")
                
                choice = input(f"\n  请输入选项 [1/2/3/q/s]: ").strip().lower()
                
                if choice == 'q':
                    print("\n❌ 用户取消删除操作")
                    break
                elif choice == 's':
                    print("\n⏭️ 跳过剩余所有删除操作")
                    break
                elif choice == '1':
                    try:
                        success = safe_delete_obsidian_file(note['path'])
                        if success:
                            print(f"  ✅ 已删除 Obsidian 笔记: {note['title']}")
                            sync_results['success'] += 1
                            sync_results['details'].append(f"删除 Obsidian 笔记: {note['title']}")
                        else:
                            print(f"  ❌ 删除失败: {note['title']}")
                            sync_results['failed'] += 1
                    except Exception as e:
                        print(f"  ❌ 删除时出错: {e}")
                        sync_results['failed'] += 1
                elif choice == '2':
                    # 复制到 Joplin
                    try:
                        folder_path = note.get('folder', '根目录')
                        notebook_id, error = get_or_create_joplin_notebook(folder_path)
                        if error:
                            print(f"  ❌ 创建笔记本失败: {error}")
                            sync_results['failed'] += 1
                        else:
                            success, result = sync_obsidian_to_joplin_with_notebook_id(note, notebook_id)
                            if success:
                                print(f"  ✅ 已复制到 Joplin: {note['title']}")
                                sync_results['success'] += 1
                                sync_results['details'].append(f"复制 Obsidian → Joplin: {note['title']}")
                            else:
                                print(f"  ❌ 复制失败: {result}")
                                sync_results['failed'] += 1
                    except Exception as e:
                        print(f"  ❌ 复制时出错: {e}")
                        sync_results['failed'] += 1
                else:
                    print(f"  ⏭️ 跳过")
                    sync_results['skipped'] += 1
                    
            elif candidate['type'] == 'obsidian_deleted':
                note = candidate['note']
                print(f"  类型: Joplin 笔记（Obsidian 端可能已删除）")
                print(f"  标题: {note['title']}")
                print(f"  笔记本: {note.get('notebook', '未分类')}")
                print(f"  ID: {candidate['notebridge_id'][:8]}...")
                
                # 显示判断依据
                sync_info = extract_sync_info_from_joplin(note['body'])
                print(f"\n  📊 判断依据:")
                print(f"    - 笔记来源: {sync_info.get('notebridge_source', '未知')}")
                if sync_info.get('notebridge_sync_time'):
                    print(f"    - 最后同步: {sync_info['notebridge_sync_time']}")
                else:
                    print(f"    - 最后同步: 从未同步")
                
                # 检查是否在上次同步状态中存在
                if previous_state:
                    notebridge_id = candidate['notebridge_id']
                    was_synced = notebridge_id in previous_joplin_ids and notebridge_id in previous_obsidian_ids
                    if was_synced:
                        print(f"    - 上次同步: ✅ 两端都存在")
                        print(f"    - 判断结果: 📌 Obsidian 端已删除（置信度高）")
                    else:
                        print(f"    - 上次同步: ⚠️ 状态不明")
                        print(f"    - 判断结果: ❓ 无法确定是删除还是新增")

                notebook = note.get('notebook', '未分类')
                if any(matches_pattern(notebook, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
                    print(f"  ⏭️ 自动跳过: {notebook} 配置为仅 Obsidian → Joplin，不删除 Joplin 笔记")
                    sync_results['skipped'] += 1
                    continue
                
                print(f"\n  请选择操作:")
                print(f"    1. 删除 Joplin 端（保持与 Obsidian 同步）")
                print(f"    2. 复制到 Obsidian 端（恢复此笔记）")
                print(f"    3. 跳过此笔记")
                print(f"    q. 退出")
                print(f"    s. 跳过所有剩余删除操作")
                
                choice = input(f"\n  请输入选项 [1/2/3/q/s]: ").strip().lower()
                
                if choice == 'q':
                    print("\n❌ 用户取消删除操作")
                    break
                elif choice == 's':
                    print("\n⏭️ 跳过剩余所有删除操作")
                    break
                elif choice == '1':
                    try:
                        success = safe_delete_joplin_note(note['id'])
                        if success:
                            print(f"  ✅ 已删除 Joplin 笔记: {note['title']}")
                            sync_results['success'] += 1
                            sync_results['details'].append(f"删除 Joplin 笔记: {note['title']}")
                        else:
                            print(f"  ❌ 删除失败: {note['title']}")
                            sync_results['failed'] += 1
                    except Exception as e:
                        print(f"  ❌ 删除时出错: {e}")
                        sync_results['failed'] += 1
                elif choice == '2':
                    # 复制到 Obsidian
                    try:
                        notebook_path = note.get('notebook', '未分类')
                        success, result = sync_joplin_to_obsidian(note, notebook_path)
                        if success:
                            print(f"  ✅ 已复制到 Obsidian: {note['title']}")
                            sync_results['success'] += 1
                            sync_results['details'].append(f"复制 Joplin → Obsidian: {note['title']}")
                        else:
                            print(f"  ❌ 复制失败: {result}")
                            sync_results['failed'] += 1
                    except Exception as e:
                        print(f"  ❌ 复制时出错: {e}")
                        sync_results['failed'] += 1
                else:
                    print(f"  ⏭️ 跳过")
                    sync_results['skipped'] += 1
    
    # 4. 处理移动的笔记
    if all_moves:
        print(f"\n\n{'='*60}")
        print("📦 开始处理笔记移动")
        print(f"{'='*60}")
        
        # 获取当前所有 Obsidian 笔记，用于通过 notebridge_id 查找文件路径
        current_obsidian_notes_for_moves = get_obsidian_notes()
        obsidian_id_to_path = {}
        for note in current_obsidian_notes_for_moves:
            sync_info = extract_sync_info_from_obsidian(note['body'])
            if sync_info.get('notebridge_id'):
                obsidian_id_to_path[sync_info['notebridge_id']] = note['path']
        
        for i, move_item in enumerate(all_moves, 1):
            print(f"\n\n[{i}/{len(all_moves)}] 笔记移动:")
            
            if 'old_notebook' in move_item:  # Joplin 移动（需要在 Obsidian 中移动）
                print(f"  类型: Joplin 笔记移动（需要在 Obsidian 中同步移动）")
                print(f"  标题: {move_item['title']}")
                print(f"  从: {move_item['old_notebook']}")
                print(f"  到: {move_item['new_notebook']}")
                print(f"  ID: {move_item['notebridge_id'][:8]}...")
                
                choice = input(f"\n  是否同步此移动到 Obsidian？ [y/n/q(退出)/s(跳过所有)]: ").strip().lower()
                
                if choice == 'q':
                    print("\n❌ 用户取消移动操作")
                    break
                elif choice == 's':
                    print("\n⏭️ 跳过剩余所有移动操作")
                    break
                elif choice == 'y':
                    try:
                        # 通过 notebridge_id 查找文件路径
                        notebridge_id = move_item.get('notebridge_id')
                        new_notebook = move_item.get('new_notebook', '未分类')
                        
                        if notebridge_id and notebridge_id in obsidian_id_to_path:
                            old_path = obsidian_id_to_path[notebridge_id]
                            if os.path.exists(old_path):
                                success, result = move_obsidian_file(old_path, new_notebook)
                                if success:
                                    print(f"  ✅ 已移动 Obsidian 文件: {move_item['title']} → {new_notebook}")
                                    sync_results['success'] += 1
                                    sync_results['details'].append(f"移动 Obsidian: {move_item['title']} → {new_notebook}")
                                else:
                                    print(f"  ❌ 移动失败: {result}")
                                    sync_results['failed'] += 1
                            else:
                                print(f"  ❌ 文件不存在: {old_path}")
                                sync_results['failed'] += 1
                        else:
                            print(f"  ❌ 找不到对应的 Obsidian 文件")
                            sync_results['failed'] += 1
                    except Exception as e:
                        print(f"  ❌ 移动时出错: {e}")
                        sync_results['failed'] += 1
                else:
                    print(f"  ⏭️ 跳过移动")
                    sync_results['skipped'] += 1
                    
            elif 'old_folder' in move_item:  # Obsidian 移动（需要在 Joplin 中移动）
                print(f"  类型: Obsidian 笔记移动（需要在 Joplin 中同步移动）")
                print(f"  标题: {move_item['title']}")
                print(f"  从: {move_item['old_folder']}")
                print(f"  到: {move_item['new_folder']}")
                print(f"  ID: {move_item['notebridge_id'][:8]}...")
                
                choice = input(f"\n  是否同步此移动到 Joplin？ [y/n/q(退出)/s(跳过所有)]: ").strip().lower()
                
                if choice == 'q':
                    print("\n❌ 用户取消移动操作")
                    break
                elif choice == 's':
                    print("\n⏭️ 跳过剩余所有移动操作")
                    break
                elif choice == 'y':
                    try:
                        joplin_id = move_item.get('joplin_id')
                        new_folder = move_item.get('new_folder', '根目录')
                        
                        if joplin_id:
                            success, result = move_joplin_note(joplin_id, new_folder)
                            if success:
                                print(f"  ✅ 已移动 Joplin 笔记: {move_item['title']} → {new_folder}")
                                sync_results['success'] += 1
                                sync_results['details'].append(f"移动 Joplin: {move_item['title']} → {new_folder}")
                            else:
                                print(f"  ❌ 移动失败: {result}")
                                sync_results['failed'] += 1
                        else:
                            print(f"  ❌ 找不到 Joplin 笔记 ID")
                            sync_results['failed'] += 1
                    except Exception as e:
                        print(f"  ❌ 移动时出错: {e}")
                        sync_results['failed'] += 1
                else:
                    print(f"  ⏭️ 跳过移动")
                    sync_results['skipped'] += 1
    
    print(f"\n💡 提示:")
    print(f"  - 所有同步的内容都已经过重复头部检查和修复")
    print(f"  - 如果发现问题，可以运行: python notebridge.py fix-duplicate-headers")

def prevent_duplicate_headers():
    """
    预防性检查重复头部，在同步前自动检测和修复
    """
    print("\n🛡️ 启动预防性重复头部检查...")
    
    # 获取所有笔记
    print("正在获取 Joplin 笔记...")
    joplin_notes = get_joplin_notes()
    print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。")
    
    print("正在获取 Obsidian 笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。")
    
    # 检查 Joplin 笔记
    print("\n🔍 检查 Joplin 笔记中的重复头部...")
    joplin_issues = 0
    
    for note in tqdm(joplin_notes, desc="检查 Joplin 笔记"):
        joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', note['body'])
        yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', note['body'])
        
        if len(joplin_ids) + len(yaml_ids) > 1:
            joplin_issues += 1
            print(f"  ⚠️ 发现重复头部: {note['title']}")
    
    # 检查 Obsidian 笔记
    print("\n🔍 检查 Obsidian 笔记中的重复头部...")
    obsidian_issues = 0
    
    for note in tqdm(obsidian_notes, desc="检查 Obsidian 笔记"):
        try:
            with open(note['path'], 'r', encoding='utf-8') as f:
                content = f.read()
            
            joplin_ids = re.findall(r'<!-- notebridge_id: ([a-f0-9-]+) -->', content)
            yaml_ids = re.findall(r'notebridge_id: ([a-f0-9-]+)', content)
            
            if len(joplin_ids) + len(yaml_ids) > 1:
                obsidian_issues += 1
                print(f"  ⚠️ 发现重复头部: {note['title']}")
        except Exception as e:
            print(f"  ❌ 检查失败: {note['title']} - {e}")
    
    # 总结
    total_issues = joplin_issues + obsidian_issues
    
    print(f"\n📊 检查结果:")
    print(f"  Joplin 笔记问题: {joplin_issues} 条")
    print(f"  Obsidian 笔记问题: {obsidian_issues} 条")
    print(f"  总计问题: {total_issues} 条")
    
    if total_issues > 0:
        print(f"\n🔧 发现问题！建议运行以下命令修复:")
        print(f"  python notebridge.py fix-duplicate-headers")
    else:
        print(f"\n✅ 没有发现重复头部问题！")
        print(f"💡 建议定期运行此命令进行预防性检查")
    
    print(f"\n💡 预防建议:")
    print(f"  - 定期运行此命令检查重复头部")
    print(f"  - 在同步前运行此命令")
    print(f"  - 如果发现问题，及时运行修复命令")

def clean_all_joplin_imports():
    """
    删除所有来自Joplin的笔记（不管是否修改过）
    """
    print("🧹 删除所有来自Joplin的笔记...")
    print("⚠️  这将删除所有带有 notebridge_source: joplin 标记的笔记！")
    
    # 获取笔记
    print("正在获取Obsidian笔记...")
    obsidian_notes = get_obsidian_notes()
    print(f"获取到 {len(obsidian_notes)} 条 Obsidian 笔记")
    
    # 检测来自Joplin的笔记
    joplin_imported_notes = []
    other_notes = []
    
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        
        if sync_info.get('notebridge_source') == 'joplin':
            joplin_imported_notes.append(note)
        else:
            other_notes.append(note)
    
    print(f"📊 检测结果：")
    print(f"  来自Joplin的笔记: {len(joplin_imported_notes)} 条")
    print(f"  其他笔记: {len(other_notes)} 条")
    
    if not joplin_imported_notes:
        print("✅ 没有发现来自Joplin的笔记！")
        return
    
    # 显示要删除的笔记统计
    print(f"\n📋 将要删除的笔记统计：")
    
    # 按文件夹统计
    folder_stats = {}
    for note in joplin_imported_notes:
        folder = note.get('folder', '根目录')
        if folder not in folder_stats:
            folder_stats[folder] = 0
        folder_stats[folder] += 1
    
    print(f"  按文件夹分布：")
    for folder, count in sorted(folder_stats.items()):
        print(f"    {folder}: {count} 条")
    
    # 显示前10条要删除的笔记
    print(f"\n📋 将要删除的笔记（前10条）：")
    for i, note in enumerate(joplin_imported_notes[:10], 1):
        print(f"  {i}. {note['title']} ({note.get('folder', '根目录')})")
    
    if len(joplin_imported_notes) > 10:
        print(f"  ... 还有 {len(joplin_imported_notes) - 10} 条")
    
    # 确认删除
    print(f"\n⚠️  ⚠️  ⚠️  危险操作警告 ⚠️  ⚠️  ⚠️")
    print(f"确认删除所有 {len(joplin_imported_notes)} 条来自Joplin的笔记？")
    print(f"这将永久删除选中的笔记，无法恢复！")
    print(f"删除后，你的Obsidian中将只剩下 {len(other_notes)} 条其他笔记")
    
    confirm = input("\n输入 'DELETE ALL' 确认删除，其他任意键取消: ").strip()
    
    if confirm == 'DELETE ALL':
        print(f"\n🧹 开始删除 {len(joplin_imported_notes)} 条来自Joplin的笔记...")
        execute_bulk_delete(joplin_imported_notes)
        
        print(f"\n🎉 清理完成！")
        print(f"现在你的Obsidian中还有 {len(other_notes)} 条其他笔记")
        print(f"\n💡 建议：")
        print(f"  现在可以运行 'python notebridge.py sync --force' 重新同步所有笔记")
    else:
        print("❌ 取消删除操作")

def confirm_sync_with_duplicates(duplicates, matched_pairs, unmatched_joplin, unmatched_obsidian):
    """
    显示查重结果和同步计划，获取用户确认
    """
    print("\n" + "="*80)
    print("🔍 同步前查重检测结果")
    print("="*80)
    
    # 统计重复情况
    total_duplicates = 0
    for category, items in duplicates.items():
        if items:
            print(f"\n📊 {category}: {len(items)} 对重复")
            total_duplicates += len(items)
    
    if total_duplicates == 0:
        print("\n✅ 没有发现重复笔记！")
    else:
        print(f"\n⚠️  发现 {total_duplicates} 对重复笔记")
        
        # 显示重复类型
        if duplicates.get('id_duplicates'):
            print("🚨 发现基于同步ID的重复（最严重）")
        if duplicates.get('sync_time_conflicts'):
            print("⚠️  发现同步时间冲突")
    
    # 显示同步计划
    print("\n" + "="*80)
    print("📋 同步计划预览")
    print("="*80)
    
    print_sync_plan_with_duplicates(matched_pairs, unmatched_joplin, unmatched_obsidian, duplicates)
    
    # 用户确认
    print("\n" + "="*80)
    print("❓ 请选择操作")
    print("="*80)
    
    if total_duplicates > 0:
        print("1. 继续同步（跳过重复笔记）")
        print("2. 先清理重复笔记再同步")
        print("3. 查看详细重复信息")
        print("4. 取消同步")
        
        while True:
            choice = input("\n请输入选择 (1-4): ").strip()
            
            if choice == "1":
                return "continue_skip_duplicates"
            elif choice == "2":
                return "clean_duplicates_first"
            elif choice == "3":
                print_detailed_duplicate_info(duplicates)
                print("\n请重新选择操作：")
                continue
            elif choice == "4":
                return "cancel"
            else:
                print("❌ 无效选择，请输入 1-4")
                continue
    else:
        print("1. 继续同步")
        print("2. 取消同步")
        
        while True:
            choice = input("\n请输入选择 (1-2): ").strip()
            
            if choice == "1":
                return "continue"
            elif choice == "2":
                return "cancel"
            else:
                print("❌ 无效选择，请输入 1-2")
                continue

def print_sync_plan_with_duplicates(matched_pairs, unmatched_joplin, unmatched_obsidian, duplicates):
    """
    显示包含重复信息的同步计划
    """
    print(f"\n📝 已匹配笔记对: {len(matched_pairs)} 对")
    
    # 检查匹配的笔记对中是否有重复
    duplicate_matched = 0
    for pair in matched_pairs:
        joplin_note = pair['joplin']
        obsidian_note = pair['obsidian']
        
        # 检查是否在重复列表中
        is_duplicate = False
        for category, items in duplicates.items():
            for item in items:
                if (item.get('joplin', {}).get('id') == joplin_note['id'] and 
                    item.get('obsidian', {}).get('path') == obsidian_note['path']):
                    is_duplicate = True
                    break
            if is_duplicate:
                break
        
        if is_duplicate:
            duplicate_matched += 1
            print(f"  ⚠️  {joplin_note['title']} <-> {obsidian_note['title']} (重复)")
        else:
            print(f"  ✅ {joplin_note['title']} <-> {obsidian_note['title']}")
    
    if duplicate_matched > 0:
        print(f"  📊 其中 {duplicate_matched} 对存在重复问题")
    
    print(f"\n📄 未匹配的 Joplin 笔记: {len(unmatched_joplin)} 条")
    
    # 检查未匹配的Joplin笔记是否与Obsidian笔记重复
    duplicate_unmatched_joplin = 0
    for note in unmatched_joplin[:10]:  # 只显示前10条
        is_duplicate = False
        for category, items in duplicates.items():
            for item in items:
                if item.get('joplin', {}).get('id') == note['id']:
                    is_duplicate = True
                    break
            if is_duplicate:
                break
        
        if is_duplicate:
            duplicate_unmatched_joplin += 1
            print(f"  ⚠️  {note['title']} (与Obsidian笔记重复)")
        else:
            print(f"  ➕ {note['title']}")
    
    if len(unmatched_joplin) > 10:
        print(f"  ... 还有 {len(unmatched_joplin) - 10} 条")
    
    if duplicate_unmatched_joplin > 0:
        print(f"  📊 其中 {duplicate_unmatched_joplin} 条与Obsidian笔记重复")
    
    print(f"\n📝 未匹配的 Obsidian 笔记: {len(unmatched_obsidian)} 条")
    
    # 检查未匹配的Obsidian笔记是否与Joplin笔记重复
    duplicate_unmatched_obsidian = 0
    for note in unmatched_obsidian[:10]:  # 只显示前10条
        is_duplicate = False
        for category, items in duplicates.items():
            for item in items:
                if item.get('obsidian', {}).get('path') == note['path']:
                    is_duplicate = True
                    break
            if is_duplicate:
                break
        
        if is_duplicate:
            duplicate_unmatched_obsidian += 1
            print(f"  ⚠️  {note['title']} (与Joplin笔记重复)")
        else:
            print(f"  ➕ {note['title']}")
    
    if len(unmatched_obsidian) > 10:
        print(f"  ... 还有 {len(unmatched_obsidian) - 10} 条")
    
    if duplicate_unmatched_obsidian > 0:
        print(f"  📊 其中 {duplicate_unmatched_obsidian} 条与Joplin笔记重复")

def print_detailed_duplicate_info(duplicates):
    """
    显示详细的重复信息
    """
    print("\n" + "="*80)
    print("📋 详细重复信息")
    print("="*80)
    
    for category, items in duplicates.items():
        if not items:
            continue
            
        print(f"\n🔍 {category}:")
        for i, item in enumerate(items[:5], 1):  # 只显示前5个
            joplin_note = item.get('joplin', {})
            obsidian_note = item.get('obsidian', {})
            
            print(f"  {i}. Joplin: {joplin_note.get('title', 'N/A')}")
            print(f"     Obsidian: {obsidian_note.get('title', 'N/A')}")
            
            if 'similarity' in item:
                print(f"     相似度: {item['similarity']:.1%}")
            if 'title_similarity' in item:
                print(f"     标题相似度: {item['title_similarity']:.1%}")
            if 'content_similarity' in item:
                print(f"     内容相似度: {item['content_similarity']:.1%}")
        
        if len(items) > 5:
            print(f"  ... 还有 {len(items) - 5} 个")

def perform_sync_with_duplicate_handling(matched_pairs, unmatched_joplin, unmatched_obsidian, duplicates):
    """
    执行同步时处理重复笔记
    """
    sync_results = {
        'success': [],
        'failed': [],
        'updated': [],
        'created': [],
        'deleted': [],
        'skipped_duplicates': []  # 新增：跳过的重复笔记
    }
    
    print("\n🚀 开始执行同步（跳过重复笔记）...")
    print(f"📡 同步方向: {SYNC_DIRECTION}")
    
    # 创建重复笔记的集合，用于快速查找
    duplicate_joplin_ids = set()
    duplicate_obsidian_paths = set()
    
    for category, items in duplicates.items():
        for item in items:
            if 'joplin' in item:
                duplicate_joplin_ids.add(item['joplin'].get('id'))
            if 'obsidian' in item:
                duplicate_obsidian_paths.add(item['obsidian'].get('path'))
    
    # 检测删除
    current_joplin_notes = get_joplin_notes()
    current_obsidian_notes = get_obsidian_notes()
    deletions = detect_deletions(current_joplin_notes, current_obsidian_notes)
    
    # 显示删除预览并确认
    if print_deletion_preview(deletions):
        if confirm_deletions():
            deletion_results = perform_deletion_sync(deletions)
            sync_results['deleted'].extend(deletion_results['success'])
            sync_results['failed'].extend(deletion_results['failed'])
        else:
            print("❌ 用户取消删除同步")
    
    # 检测移动
    moves = detect_moves(current_joplin_notes, current_obsidian_notes)
    
    # 显示移动预览并确认
    if print_move_preview(moves):
        if confirm_moves():
            move_results = perform_move_sync(moves)
            sync_results['success'].extend(move_results['success'])
            sync_results['failed'].extend(move_results['failed'])
        else:
            print("❌ 用户取消移动同步")
    
    # 1. 更新已匹配的笔记对（跳过重复的）
    if matched_pairs and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']:
        print(f"\n📝 更新 {len(matched_pairs)} 对已匹配笔记...")
        skipped_count = 0
        sync_rule_skipped_count = 0
        
        for pair in tqdm(matched_pairs, desc="更新匹配笔记"):
            joplin_note = pair['joplin']
            obsidian_note = pair['obsidian']
            
            # 检查是否为重复笔记
            if (joplin_note['id'] in duplicate_joplin_ids or 
                obsidian_note['path'] in duplicate_obsidian_paths):
                sync_results['skipped_duplicates'].append(f"跳过重复: {joplin_note['title']} <-> {obsidian_note['title']}")
                skipped_count += 1
                continue
            
            # 检查同步规则
            joplin_notebook = joplin_note['notebook']
            obsidian_folder = obsidian_note['folder']
            
            # 检查是否允许 Joplin → Obsidian 同步
            can_joplin_to_obsidian = (
                SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian'] and
                not any(matches_pattern(joplin_notebook, pattern) for pattern in sync_rules['obsidian_to_joplin_only'])
            )
            
            # 检查是否允许 Obsidian → Joplin 同步
            can_obsidian_to_joplin = (
                SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin'] and
                not any(matches_pattern(obsidian_folder, pattern) for pattern in sync_rules['joplin_to_obsidian_only'])
            )
            
            # 如果两个方向都不允许，跳过这个笔记对
            if not can_joplin_to_obsidian and not can_obsidian_to_joplin:
                sync_results['skipped_duplicates'].append(f"跳过单向同步限制: {joplin_note['title']} <-> {obsidian_note['title']}")
                sync_rule_skipped_count += 1
                continue
            
            # 正常同步逻辑
            joplin_content = joplin_note['body']
            obsidian_content = obsidian_note['body']
            
            joplin_sync_info = extract_sync_info_from_joplin(joplin_content)
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_content)
            
            # 使用实际的修改时间
            joplin_updated_time = joplin_note.get('user_updated_time', 0)
            obsidian_file_path = obsidian_note['path']
            try:
                obsidian_mtime = os.path.getmtime(obsidian_file_path) * 1000
            except:
                obsidian_mtime = 0
            
            # 获取上次同步时间
            joplin_sync_time = joplin_sync_info.get('notebridge_sync_time', '')
            obsidian_sync_time = obsidian_sync_info.get('notebridge_sync_time', '')
            
            # 时间解析函数
            def parse_sync_time(sync_time_str):
                if not sync_time_str:
                    return 0
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(sync_time_str.replace('Z', '+00:00'))
                    return int(dt.timestamp() * 1000)
                except:
                    return 0
            
            joplin_sync_timestamp = parse_sync_time(joplin_sync_time)
            obsidian_sync_timestamp = parse_sync_time(obsidian_sync_time)
            
            # 判断哪一端有真正的修改
            joplin_has_changes = joplin_updated_time > joplin_sync_timestamp
            obsidian_has_changes = obsidian_mtime > obsidian_sync_timestamp
            
            joplin_source = joplin_sync_info.get('notebridge_source', '')
            obsidian_source = obsidian_sync_info.get('notebridge_source', '')
            
            if joplin_has_changes and not obsidian_has_changes and can_joplin_to_obsidian:
                # 只有 Joplin 端有修改，同步到 Obsidian
                # 转换为Obsidian格式
                obsidian_formatted_content = joplin_content
                # 移除HTML注释格式
                obsidian_formatted_content = re.sub(r'<!-- notebridge_[^>]+ -->\s*', '', obsidian_formatted_content)
                # 添加YAML格式
                obsidian_formatted_content = add_sync_info_to_obsidian_content(obsidian_formatted_content, joplin_sync_info)
                
                success, result = update_obsidian_note(obsidian_note['path'], obsidian_formatted_content)
                if success:
                    sync_results['updated'].append(f"Joplin → Obsidian: {joplin_note['title']}")
                    # 回写同步信息到Joplin端
                    if not joplin_sync_info.get('notebridge_id'):
                        joplin_with_sync = add_sync_info_to_joplin_content(joplin_note['body'], joplin_sync_info)
                        update_joplin_note(joplin_note['id'], joplin_with_sync)
                else:
                    sync_results['failed'].append(f"Joplin → Obsidian: {joplin_note['title']} - {result}")
            elif obsidian_has_changes and not joplin_has_changes and can_obsidian_to_joplin:
                # 只有 Obsidian 端有修改，同步到 Joplin
                # 转换为Joplin格式
                joplin_formatted_content = obsidian_content
                # 移除YAML frontmatter中的同步信息
                yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', joplin_formatted_content, re.DOTALL)
                if yaml_match:
                    yaml_content = yaml_match.group(1)
                    yaml_lines = yaml_content.split('\n')
                    filtered_lines = [line for line in yaml_lines if not line.strip().startswith('notebridge_')]
                    if filtered_lines:
                        new_yaml_content = '\n'.join(filtered_lines)
                        joplin_formatted_content = f"---\n{new_yaml_content}\n---\n\n" + joplin_formatted_content[yaml_match.end():]
                    else:
                        joplin_formatted_content = joplin_formatted_content[yaml_match.end():]
                # 添加HTML注释格式
                joplin_formatted_content = add_sync_info_to_joplin_content(joplin_formatted_content, obsidian_sync_info)
                
                success, result = update_joplin_note(joplin_note['id'], joplin_formatted_content)
                if success:
                    sync_results['updated'].append(f"Obsidian → Joplin: {obsidian_note['title']}")
                    # 回写同步信息到Obsidian端（确保是YAML格式）
                    if not obsidian_sync_info.get('notebridge_id'):
                        obs_with_sync = add_sync_info_to_obsidian_content(obsidian_note['body'], obsidian_sync_info)
                        update_obsidian_note(obsidian_note['path'], obs_with_sync)
                else:
                    sync_results['failed'].append(f"Obsidian → Joplin: {obsidian_note['title']} - {result}")
            elif joplin_has_changes and obsidian_has_changes:
                # 两端都有修改，需要手动解决冲突
                print(f"\n⚠️ 冲突: {joplin_note['title']} 两端都有修改，跳过")
                sync_results['failed'].append(f"冲突: {joplin_note['title']} - 两端都有修改")
        
        if skipped_count > 0:
            print(f"  ⏭️  跳过了 {skipped_count} 对重复笔记")
        if sync_rule_skipped_count > 0:
            print(f"  ⏭️  跳过了 {sync_rule_skipped_count} 对单向同步限制笔记")
    
    # 2. 同步新笔记到 Obsidian（跳过重复的）
    if unmatched_joplin and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
        print(f"\n📝 同步 {len(unmatched_joplin)} 条新笔记到 Obsidian...")
        skipped_count = 0
        sync_rule_skipped_count = 0
        
        for note in tqdm(unmatched_joplin, desc="Joplin → Obsidian"):
            # 检查笔记是否有效
            if not note.get('title') or not note.get('title').strip():
                sync_results['skipped_duplicates'].append(f"跳过空标题: Joplin (可能已删除)")
                skipped_count += 1
                continue
            
            if is_empty_note(note.get('body', '')):
                sync_results['skipped_duplicates'].append(f"跳过空内容: Joplin {note['title']}")
                skipped_count += 1
                continue
            
            if note['id'] in duplicate_joplin_ids:
                sync_results['skipped_duplicates'].append(f"跳过重复: Joplin {note['title']}")
                skipped_count += 1
                continue
            
            # 检查同步规则
            notebook_path = note.get('notebook', '未分类')
            
            # 检查是否允许 Joplin → Obsidian 同步
            if any(matches_pattern(notebook_path, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
                sync_results['skipped_duplicates'].append(f"跳过单向同步限制: Joplin {note['title']} ({notebook_path})")
                sync_rule_skipped_count += 1
                continue
            
            # 检查笔记来源，避免反向同步
            sync_info = extract_sync_info_from_joplin(note['body'])
            source = sync_info.get('notebridge_source', '')
            
            if source == 'obsidian':
                # 笔记来自Obsidian，不应该再同步回Obsidian
                sync_results['skipped_duplicates'].append(f"跳过反向同步: Joplin {note['title']} (来自 Obsidian)")
                sync_rule_skipped_count += 1
                continue
            
            success, result = sync_joplin_to_obsidian(note, notebook_path)
            if success:
                sync_results['created'].append(f"Joplin → Obsidian: {note['title']} ({notebook_path})")
            else:
                sync_results['failed'].append(f"Joplin → Obsidian: {note['title']} ({notebook_path}) - {result}")
        
        if skipped_count > 0:
            print(f"  ⏭️  跳过了 {skipped_count} 条重复笔记")
        if sync_rule_skipped_count > 0:
            print(f"  ⏭️  跳过了 {sync_rule_skipped_count} 条单向同步限制笔记")
    
    # 3. 同步新笔记到 Joplin（跳过重复的）
    if unmatched_obsidian and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
        print(f"\n📄 同步 {len(unmatched_obsidian)} 条新笔记到 Joplin...")
        skipped_count = 0
        sync_rule_skipped_count = 0
        
        # 按文件夹分组
        notes_by_folder = {}
        for note in unmatched_obsidian:
            # 检查笔记是否有效
            if not note.get('title') or not note.get('title').strip():
                sync_results['skipped_duplicates'].append(f"跳过空标题: Obsidian (可能已删除)")
                skipped_count += 1
                continue
            
            if is_empty_note(note.get('body', '')):
                sync_results['skipped_duplicates'].append(f"跳过空内容: Obsidian {note['title']}")
                skipped_count += 1
                continue
            
            if note['path'] in duplicate_obsidian_paths:
                sync_results['skipped_duplicates'].append(f"跳过重复: Obsidian {note['title']}")
                skipped_count += 1
                continue
            
            # 检查同步规则
            folder_path = note.get('folder', '根目录')
            
            # 检查是否允许 Obsidian → Joplin 同步
            if any(matches_pattern(folder_path, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
                sync_results['skipped_duplicates'].append(f"跳过单向同步限制: Obsidian {note['title']} ({folder_path})")
                sync_rule_skipped_count += 1
                continue
            
            # 检查笔记来源，避免反向同步
            sync_info = extract_sync_info_from_obsidian(note['body'])
            source = sync_info.get('notebridge_source', '')
            
            if source == 'joplin':
                # 笔记来自Joplin，不应该再同步回Joplin
                sync_results['skipped_duplicates'].append(f"跳过反向同步: Obsidian {note['title']} (来自 Joplin)")
                sync_rule_skipped_count += 1
                continue
            
            if folder_path not in notes_by_folder:
                notes_by_folder[folder_path] = []
            notes_by_folder[folder_path].append(note)
        
        print(f"  共需要处理 {len(notes_by_folder)} 个文件夹")
        
        for folder_path, notes in tqdm(notes_by_folder.items(), desc="处理文件夹"):
            print(f"    正在处理文件夹: {folder_path} ({len(notes)} 条笔记)")
            
            notebook_id, error = get_or_create_joplin_notebook(folder_path)
            if error:
                print(f"    ❌ 创建笔记本失败: {error}")
                for note in notes:
                    sync_results['failed'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {error}")
                continue
            
            print(f"    ✅ 笔记本准备就绪，开始创建笔记...")
            
            for note in notes:
                success, result = sync_obsidian_to_joplin_with_notebook_id(note, notebook_id)
                if success:
                    sync_results['created'].append(f"Obsidian → Joplin: {note['title']} ({folder_path})")
                else:
                    sync_results['failed'].append(f"Obsidian → Joplin: {note['title']} ({folder_path}) - {result}")
        
        if skipped_count > 0:
            print(f"  ⏭️  跳过了 {skipped_count} 条重复笔记")
        if sync_rule_skipped_count > 0:
            print(f"  ⏭️  跳过了 {sync_rule_skipped_count} 条单向同步限制笔记")
    
    # 保存当前同步状态
    save_sync_state(current_joplin_notes, current_obsidian_notes)
    
    return sync_results

def print_sync_results_with_duplicates(sync_results):
    """
    打印包含重复处理结果的同步报告
    """
    print("\n" + "="*50)
    print("📊 同步结果报告（含重复处理）")
    print("="*50)
    
    print(f"\n✅ 成功创建: {len(sync_results['created'])} 条")
    for item in sync_results['created'][:10]:
        print(f"  ✓ {item}")
    if len(sync_results['created']) > 10:
        print(f"  ... 还有 {len(sync_results['created']) - 10} 条")
    
    print(f"\n🔄 成功更新: {len(sync_results['updated'])} 条")
    for item in sync_results['updated'][:10]:
        print(f"  ✓ {item}")
    if len(sync_results['updated']) > 10:
        print(f"  ... 还有 {len(sync_results['updated']) - 10} 条")
    
    print(f"\n🗑️  成功删除: {len(sync_results['deleted'])} 条")
    for item in sync_results['deleted'][:10]:
        print(f"  ✓ {item}")
    if len(sync_results['deleted']) > 10:
        print(f"  ... 还有 {len(sync_results['deleted']) - 10} 条")
    
    print(f"\n⏭️  跳过重复: {len(sync_results['skipped_duplicates'])} 条")
    for item in sync_results['skipped_duplicates'][:10]:
        print(f"  ⏭️  {item}")
    if len(sync_results['skipped_duplicates']) > 10:
        print(f"  ... 还有 {len(sync_results['skipped_duplicates']) - 10} 条")
    
    print(f"\n❌ 失败: {len(sync_results['failed'])} 条")
    for item in sync_results['failed'][:10]:
        print(f"  ✗ {item}")
    if len(sync_results['failed']) > 10:
        print(f"  ... 还有 {len(sync_results['failed']) - 10} 条")
    
    # 统计总结
    total_processed = (len(sync_results['created']) + len(sync_results['updated']) + 
                      len(sync_results['deleted']) + len(sync_results['skipped_duplicates']))
    total_operations = total_processed + len(sync_results['failed'])
    
    print(f"\n📈 总结:")
    print(f"  总操作数: {total_operations}")
    print(f"  成功处理: {total_processed}")
    print(f"  失败: {len(sync_results['failed'])}")
    print(f"  成功率: {total_processed/total_operations*100:.1f}%" if total_operations > 0 else "成功率: 0%")
    
    if sync_results['skipped_duplicates']:
        print(f"\n💡 提示: 有 {len(sync_results['skipped_duplicates'])} 条重复笔记被跳过")
        print("   如需处理重复笔记，请运行: python notebridge.py interactive-clean")

if __name__ == "__main__":
    # 检查命令行参数
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "sync":
            # 检查同步方向参数（检查所有参数）
            if "--joplin-to-obsidian" in sys.argv:
                SYNC_DIRECTION = 'joplin_to_obsidian'
            elif "--obsidian-to-joplin" in sys.argv:
                SYNC_DIRECTION = 'obsidian_to_joplin'
            elif "--bidirectional" in sys.argv:
                SYNC_DIRECTION = 'bidirectional'
            
            # 检查是否强制同步
            force_sync = "--force" in sys.argv
            
            if not force_sync:
                # 智能预览模式（集成查重检测）
                print("\n🔄 启动智能同步模式（含查重检测）...")
                
                # 获取笔记
                print("正在获取 Joplin 笔记...")
                joplin_notes = get_joplin_notes()
                print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。\n")
                
                print("正在获取 Obsidian 笔记...")
                obsidian_notes = get_obsidian_notes()
                print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。\n")
                
                # 应用同步规则
                joplin_to_sync, obsidian_to_sync = apply_sync_rules(joplin_notes, obsidian_notes)
                
                # 建立ID映射
                print("正在建立ID映射关系...")
                id_mapping = build_id_mapping(joplin_to_sync, obsidian_to_sync)
                
                # 智能匹配笔记
                matched_pairs, unmatched_joplin, unmatched_obsidian, deleted_candidates = smart_match_notes(
                    id_mapping, joplin_to_sync, obsidian_to_sync
                )
                
                # 运行查重检测
                print("正在运行查重检测...")
                duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
                
                # 显示查重结果和同步计划，获取用户确认
                user_choice = confirm_sync_with_duplicates(
                    duplicates, matched_pairs, unmatched_joplin, unmatched_obsidian
                )
                
                if user_choice == "cancel":
                    print("❌ 用户取消同步")
                elif user_choice == "clean_duplicates_first":
                    print("\n🧹 启动重复清理模式...")
                    interactive_clean_duplicates()
                    print("\n💡 重复清理完成。如需同步，请重新运行:")
                    print("  python notebridge.py sync --force")
                elif user_choice in ["continue", "continue_skip_duplicates"]:
                    print("\n💡 要执行实际同步，请运行:")
                    print("  python notebridge.py sync --force                    # 双向同步")
                    print("  python notebridge.py sync --force --joplin-to-obsidian  # 仅 Joplin → Obsidian")
                    print("  python notebridge.py sync --force --obsidian-to-joplin  # 仅 Obsidian → Joplin")
                
            else:
                # 执行实际同步（含查重检测和人工确认）
                print("\n🔄 启动智能同步模式（含查重检测）...")
                
                # 获取笔记
                print("正在获取 Joplin 笔记...")
                joplin_notes = get_joplin_notes()
                print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。\n")
                
                print("正在获取 Obsidian 笔记...")
                obsidian_notes = get_obsidian_notes()
                print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。\n")
                
                # 应用同步规则
                joplin_to_sync, obsidian_to_sync = apply_sync_rules(joplin_notes, obsidian_notes)
                
                # 建立ID映射
                print("正在建立ID映射关系...")
                id_mapping = build_id_mapping(joplin_to_sync, obsidian_to_sync)
                
                # 智能匹配笔记
                matched_pairs, unmatched_joplin, unmatched_obsidian, deleted_candidates = smart_match_notes(
                    id_mapping, joplin_to_sync, obsidian_to_sync
                )
                
                # 运行查重检测
                print("正在运行查重检测...")
                duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
                
                # 显示查重结果和同步计划，获取用户确认
                user_choice = confirm_sync_with_duplicates(
                    duplicates, matched_pairs, unmatched_joplin, unmatched_obsidian
                )
                
                if user_choice == "cancel":
                    print("❌ 用户取消同步")
                    sys.exit(0)
                elif user_choice == "clean_duplicates_first":
                    print("\n🧹 启动重复清理模式...")
                    interactive_clean_duplicates()
                    print("\n💡 重复清理完成。如需同步，请重新运行:")
                    print("  python notebridge.py sync --force")
                    sys.exit(0)
                elif user_choice in ["continue", "continue_skip_duplicates"]:
                    # 执行同步
                    sync_results = perform_sync_with_duplicate_handling(
                        matched_pairs, unmatched_joplin, unmatched_obsidian, duplicates
                    )
                    
                    # 打印结果
                    print_sync_results_with_duplicates(sync_results)
            
        elif command == "check-duplicates":
            # 查重模式
            print("\n🔍 启动查重模式...")
            
            # 获取笔记
            print("正在获取 Joplin 笔记...")
            joplin_notes = get_joplin_notes()
            print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。\n")
            
            print("正在获取 Obsidian 笔记...")
            obsidian_notes = get_obsidian_notes()
            print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。\n")
            
            # 使用优化版查重
            duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
            
            # 打印查重报告
            print_duplicate_report(duplicates)
            
        elif command == "interactive-clean":
            # 交互式清理重复笔记
            interactive_clean_duplicates()
            
        elif command == "clean-duplicates":
            # 自动清理重复笔记和同步ID
            find_and_remove_duplicates()
            sys.exit(0)
            
        elif command == "deduplicate-content":
            # 去重：内容相同但ID不同的笔记
            deduplicate_same_content_different_ids()
            sys.exit(0)
            
        elif command == "fix-attachments":
            fix_obsidian_attachments()
            sys.exit(0)
            sys.exit(0)
            
        elif command == "test-duplicates":
            # 快速重复检测测试
            quick_duplicate_test()
            sys.exit(0)
            
        elif command == "quick-title-check":
            # 快速标题相似度检测
            quick_title_similarity_check()
            sys.exit(0)
            
        elif command == "clean-joplin-imports":
            # 清理Obsidian中来自Joplin的笔记
            interactive_clean_joplin_imported_notes()
            sys.exit(0)
            
        elif command == "debug-sync":
            # 调试同步信息提取
            debug_sync_info_extraction()
            sys.exit(0)
            
        elif command == "clean-unmodified":
            # 清理未修改的Joplin导入笔记
            clean_unmodified_joplin_imports()
            sys.exit(0)
            
        elif command == "clean-all-joplin":
            # 删除所有来自Joplin的笔记
            clean_all_joplin_imports()
            sys.exit(0)
            
        elif command == "fix-duplicate-headers":
            # 修复重复的同步信息头部
            fix_duplicate_sync_headers()
            sys.exit(0)
            
        elif command == "prevent-duplicate-headers":
            # 预防性检查重复头部
            prevent_duplicate_headers()
            sys.exit(0)
            
        elif command == "manual-clean-duplicates":
            # 查重后手动逐条确认清理重复笔记
            manual_clean_duplicates()
            sys.exit(0)
            
        elif command == "sync-manual":
            # 手工确认模式同步
            # 检查同步方向参数
            if "--joplin-to-obsidian" in sys.argv:
                SYNC_DIRECTION = 'joplin_to_obsidian'
            elif "--obsidian-to-joplin" in sys.argv:
                SYNC_DIRECTION = 'obsidian_to_joplin'
            elif "--bidirectional" in sys.argv:
                SYNC_DIRECTION = 'bidirectional'
            
            manual_confirm_sync()
            sys.exit(0)
        
        else:
            print(f"❌ 未知命令: {command}")
            print("\n📖 使用方法:")
            print("  python notebridge.py sync         # 智能同步预览（含查重检测）")
            print("  python notebridge.py sync --force # 执行实际同步（含查重确认）")
            print("  python notebridge.py sync --force --joplin-to-obsidian  # 仅 Joplin → Obsidian")
            print("  python notebridge.py sync --force --obsidian-to-joplin  # 仅 Obsidian → Joplin")
            print("  python notebridge.py sync-manual  # 手工确认模式同步（推荐，防止重复头部）")
            print("  python notebridge.py sync-manual --joplin-to-obsidian  # 手工确认单向同步")
            print("  python notebridge.py check-duplicates # 查重模式（超快速版）")
            print("  python notebridge.py quick-title-check # 快速标题相似度检测（推荐）")
            print("  python notebridge.py clean-joplin-imports # 清理Obsidian中来自Joplin的笔记")
            print("  python notebridge.py clean-unmodified    # 清理未修改的Joplin导入笔记")
            print("  python notebridge.py clean-all-joplin    # 删除所有来自Joplin的笔记（彻底清理）")
            print("  python notebridge.py fix-duplicate-headers # 修复重复的同步信息头部")
            print("  python notebridge.py prevent-duplicate-headers # 预防性检查重复头部")
            print("  python notebridge.py test-duplicates  # 性能测试对比")
            print("  python notebridge.py interactive-clean # 交互式清理重复笔记")
            print("  python notebridge.py manual-clean-duplicates # 查重后手动逐条确认清理")
            print("  python notebridge.py clean-duplicates # 自动清理重复笔记和同步ID")
            print("  python notebridge.py deduplicate-content # 去重：内容相同但ID不同的笔记")
            print("  python notebridge.py fix-attachments # 补全 Obsidian 附件")
    else:
        # 智能同步预览模式（集成查重检测）
        print("\n🔄 启动智能同步模式（含查重检测）...")
        
        # 获取笔记
        print("正在获取 Joplin 笔记...")
        joplin_notes = get_joplin_notes()
        print(f"共获取到 {len(joplin_notes)} 条 Joplin 笔记。\n")
        
        print("正在获取 Obsidian 笔记...")
        obsidian_notes = get_obsidian_notes()
        print(f"共获取到 {len(obsidian_notes)} 条 Obsidian 笔记。\n")
        
        # 应用同步规则
        joplin_to_sync, obsidian_to_sync = apply_sync_rules(joplin_notes, obsidian_notes)
        
        # 建立ID映射
        print("正在建立ID映射关系...")
        id_mapping = build_id_mapping(joplin_to_sync, obsidian_to_sync)
        
        # 智能匹配笔记
        matched_pairs, unmatched_joplin, unmatched_obsidian = smart_match_notes(
            id_mapping, joplin_to_sync, obsidian_to_sync
        )
        
        # 运行查重检测
        print("正在运行查重检测...")
        duplicates = find_duplicates_ultra_fast(joplin_notes, obsidian_notes)
        
        # 显示查重结果和同步计划，获取用户确认
        user_choice = confirm_sync_with_duplicates(
            duplicates, matched_pairs, unmatched_joplin, unmatched_obsidian
        )
        
        if user_choice == "cancel":
            print("❌ 用户取消同步")
        elif user_choice == "clean_duplicates_first":
            print("\n🧹 启动重复清理模式...")
            interactive_clean_duplicates()
            print("\n💡 重复清理完成。如需同步，请重新运行:")
            print("  python notebridge.py sync --force")
        elif user_choice in ["continue", "continue_skip_duplicates"]:
            print("\n💡 要执行实际同步，请运行:")
            print("  python notebridge.py sync --force                    # 双向同步")
            print("  python notebridge.py sync --force --joplin-to-obsidian  # 仅 Joplin → Obsidian")
            print("  python notebridge.py sync --force --obsidian-to-joplin  # 仅 Obsidian → Joplin")
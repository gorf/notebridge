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
    
    # 添加最新的同步信息
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

def extract_sync_info_from_joplin(note_body):
    """
    从 Joplin 笔记内容中提取同步信息（修复多ID问题）
    """
    # 先清理重复的同步信息
    cleaned_body = clean_duplicate_sync_info(note_body)
    
    sync_info = {}
    
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
    
    return sync_info

def extract_sync_info_from_obsidian(content):
    """
    从 Obsidian 笔记内容中提取同步信息（修复多ID问题）
    """
    # 先清理重复的同步信息
    cleaned_content = clean_duplicate_sync_info(content)
    
    sync_info = {}
    
    # 查找 YAML frontmatter
    yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', cleaned_content, re.DOTALL)
    if yaml_match:
        yaml_content = yaml_match.group(1)
        try:
            yaml_data = yaml.safe_load(yaml_content)
            if yaml_data:
                sync_info['notebridge_id'] = yaml_data.get('notebridge_id', '')
                sync_info['notebridge_sync_time'] = yaml_data.get('notebridge_sync_time', '')
                sync_info['notebridge_source'] = yaml_data.get('notebridge_source', '')
        except:
            pass
    
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
    """
    notes = []
    page = 1
    while True:
        url = f"{joplin_api_base}/notes?token={joplin_token}&fields=id,title,body,parent_id&page={page}"
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
    
    # 为每条笔记添加完整的笔记本路径（保持原有分类）
    for note in notes:
        notebook_id = note.get('parent_id', '')
        note['notebook'] = get_full_notebook_path(notebook_id)
        note['notebook_path'] = note['notebook'].split('/')
    
    return notes

# 6. 读取 Obsidian 文件夹下的所有 Markdown 文件
def get_obsidian_notes():
    """
    读取 Obsidian 笔记库下所有 .md 文件的标题、内容和文件夹信息
    """
    notes = []
    md_files = glob.glob(os.path.join(obsidian_vault_path, '**', '*.md'), recursive=True)
    
    print(f"发现 {len(md_files)} 个 Markdown 文件，正在读取...")
    
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
            
            title = Path(file_path).stem  # 文件名作为标题
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
    """
    joplin_to_sync = []
    obsidian_to_sync = []
    
    # 处理 Joplin 笔记
    for note in joplin_notes:
        notebook = note['notebook']
        
        # 检查是否匹配跳过同步的模式
        should_skip = False
        for pattern in sync_rules['skip_sync']:
            if matches_pattern(notebook, pattern):
                should_skip = True
                break
        
        if should_skip:
            continue  # 跳过不同步的笔记本
        elif any(matches_pattern(notebook, pattern) for pattern in sync_rules['joplin_to_obsidian_only']):
            joplin_to_sync.append(note)  # 只同步到 Obsidian
        elif any(matches_pattern(notebook, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
            continue  # 只从 Obsidian 同步过来，不从这里同步出去
        else:
            joplin_to_sync.append(note)  # 默认双向同步
    
    # 处理 Obsidian 笔记
    for note in obsidian_notes:
        folder = note['folder']
        
        # 检查是否匹配跳过同步的模式
        should_skip = False
        for pattern in sync_rules['skip_sync']:
            if matches_pattern(folder, pattern):
                should_skip = True
                break
        
        if should_skip:
            continue  # 跳过不同步的文件夹
        elif any(matches_pattern(folder, pattern) for pattern in sync_rules['obsidian_to_joplin_only']):
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
    """
    return hashlib.md5(content.encode('utf-8')).hexdigest()

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
    
    print(f"\n🔍 完全重复的笔记（内容哈希相同）：{len(duplicates['content_hash_duplicates'])} 对")
    for i, dup in enumerate(duplicates['content_hash_duplicates'][:5], 1):
        print(f"  {i}. Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
        print(f"     Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
    
    print(f"\n📝 标题和内容都相似的笔记：{len(duplicates['exact_duplicates'])} 对")
    for i, dup in enumerate(duplicates['exact_duplicates'][:5], 1):
        print(f"  {i}. Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
        print(f"     Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
        print(f"     相似度: 标题{dup['title_similarity']:.1%}, 内容{dup['content_similarity']:.1%}")
    
    print(f"\n📋 标题相似的笔记：{len(duplicates['title_similar'])} 对")
    for i, dup in enumerate(duplicates['title_similar'][:5], 1):
        print(f"  {i}. Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
        print(f"     Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
        print(f"     相似度: 标题{dup['title_similarity']:.1%}, 内容{dup['content_similarity']:.1%}")
    
    print(f"\n📄 内容相似的笔记：{len(duplicates['content_similar'])} 对")
    for i, dup in enumerate(duplicates['content_similar'][:5], 1):
        print(f"  {i}. Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
        print(f"     Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
        print(f"     相似度: 标题{dup['title_similarity']:.1%}, 内容{dup['content_similarity']:.1%}")
    
    total_duplicates = (len(duplicates['content_hash_duplicates']) + 
                       len(duplicates['exact_duplicates']) + 
                       len(duplicates['title_similar']) + 
                       len(duplicates['content_similar']))
    
    print(f"\n📈 总计发现 {total_duplicates} 对重复/相似笔记")
    print("="*50)

# 9. 防重复同步机制
def generate_sync_info(source):
    """
    生成新的同步信息
    """
    return {
        'notebridge_id': str(uuid.uuid4()),
        'notebridge_sync_time': datetime.now().isoformat(),
        'notebridge_source': source,
        'notebridge_version': '1'
    }

def build_id_mapping(joplin_notes, obsidian_notes):
    """
    建立 ID 映射关系
    """
    id_mapping = {
        'joplin_to_obsidian': {},  # notebridge_id -> obsidian_path
        'obsidian_to_joplin': {},  # notebridge_id -> joplin_id
        'unmapped_joplin': [],     # 没有ID的Joplin笔记
        'unmapped_obsidian': []    # 没有ID的Obsidian笔记
    }
    
    # 处理 Joplin 笔记
    for note in joplin_notes:
        sync_info = extract_sync_info_from_joplin(note['body'])
        if sync_info.get('notebridge_id'):
            id_mapping['obsidian_to_joplin'][sync_info['notebridge_id']] = note['id']
        else:
            id_mapping['unmapped_joplin'].append(note)
    
    # 处理 Obsidian 笔记
    for note in obsidian_notes:
        sync_info = extract_sync_info_from_obsidian(note['body'])
        if sync_info.get('notebridge_id'):
            id_mapping['joplin_to_obsidian'][sync_info['notebridge_id']] = note['path']
        else:
            id_mapping['unmapped_obsidian'].append(note)
    
    return id_mapping

def smart_match_notes(id_mapping, joplin_notes, obsidian_notes):
    """
    智能匹配笔记，避免重复（考虑上次同步状态）
    """
    matched_pairs = []
    unmatched_joplin = []
    unmatched_obsidian = []
    
    # 加载上次同步状态
    previous_state = load_sync_state()
    previous_joplin_ids = set()
    previous_obsidian_ids = set()
    
    if previous_state:
        previous_joplin_ids = set(previous_state['joplin_notes'].keys())
        previous_obsidian_ids = set(previous_state['obsidian_notes'].keys())
    
    # 1. 通过ID直接匹配
    for notebridge_id in id_mapping['joplin_to_obsidian']:
        if notebridge_id in id_mapping['obsidian_to_joplin']:
            joplin_id = id_mapping['obsidian_to_joplin'][notebridge_id]
            obsidian_path = id_mapping['joplin_to_obsidian'][notebridge_id]
            
            # 找到对应的笔记对象
            joplin_note = next((n for n in joplin_notes if n['id'] == joplin_id), None)
            obsidian_note = next((n for n in obsidian_notes if n['path'] == obsidian_path), None)
            
            if joplin_note and obsidian_note:
                matched_pairs.append({
                    'joplin': joplin_note,
                    'obsidian': obsidian_note,
                    'notebridge_id': notebridge_id,
                    'match_type': 'id'
                })
    
    # 2. 对未匹配的笔记进行内容匹配，但排除已在上次同步中的笔记
    unmatched_joplin_ids = set()
    unmatched_obsidian_paths = set()
    
    for notebridge_id in id_mapping['joplin_to_obsidian']:
        if notebridge_id not in id_mapping['obsidian_to_joplin']:
            unmatched_obsidian_paths.add(id_mapping['joplin_to_obsidian'][notebridge_id])
    
    for notebridge_id in id_mapping['obsidian_to_joplin']:
        if notebridge_id not in id_mapping['joplin_to_obsidian']:
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
                matched_pairs.append({
                    'joplin': joplin_hash_map[content_hash],
                    'obsidian': note,
                    'notebridge_id': generate_sync_info('joplin')['notebridge_id'],
                    'match_type': 'content_hash'
                })
                unmatched_joplin_ids.discard(joplin_hash_map[content_hash]['id'])
                unmatched_obsidian_paths.discard(note['path'])
    
    # 收集最终未匹配的笔记
    for note in joplin_notes:
        if note['id'] in unmatched_joplin_ids:
            unmatched_joplin.append(note)
    
    for note in obsidian_notes:
        if note['path'] in unmatched_obsidian_paths:
            unmatched_obsidian.append(note)
    
    return matched_pairs, unmatched_joplin, unmatched_obsidian

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
    在 Joplin 笔记内容中添加同步信息
    """
    sync_header = f"""<!-- notebridge_id: {sync_info['notebridge_id']} -->
<!-- notebridge_sync_time: {sync_info['notebridge_sync_time']} -->
<!-- notebridge_source: {sync_info['notebridge_source']} -->
<!-- notebridge_version: {sync_info['notebridge_version']} -->

"""
    return sync_header + content

def add_sync_info_to_obsidian_content(content, sync_info):
    """
    在 Obsidian 笔记内容中添加同步信息（YAML frontmatter）
    """
    # 检查是否已有 frontmatter
    if content.startswith('---'):
        # 已有 frontmatter，在其中添加同步信息
        yaml_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if yaml_match:
            try:
                frontmatter = yaml.safe_load(yaml_match.group(1))
                # 确保 frontmatter 是字典类型
                if not isinstance(frontmatter, dict):
                    frontmatter = {}
                frontmatter.update(sync_info)
                new_frontmatter = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
                return f"---\n{new_frontmatter}---\n\n" + content[yaml_match.end():]
            except yaml.YAMLError:
                pass
    
    # 没有 frontmatter 或解析失败，创建新的
    frontmatter = yaml.dump(sync_info, default_flow_style=False, allow_unicode=True)
    return f"---\n{frontmatter}---\n\n{content}"

# 附件目录
OBSIDIAN_ATTACHMENT_DIR = os.path.join(obsidian_vault_path, 'attachments')
os.makedirs(OBSIDIAN_ATTACHMENT_DIR, exist_ok=True)

def sanitize_filename(filename, max_length=100):
    """
    清理文件名/文件夹名/笔记本名，移除或替换不允许的字符，限制长度
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
    
    # 限制长度（保留扩展名）
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        max_name_length = max_length - len(ext)
        if max_name_length > 0:
            filename = name[:max_name_length] + ext
        else:
            filename = 'untitled' + ext
    
    return filename

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

def extract_joplin_resource_ids(content):
    """
    提取Joplin笔记正文中所有资源ID（如 :/resourceid）
    返回资源ID列表
    """
    return re.findall(r'\!\[.*?\]\(:\/([a-f0-9]+)\)', content)

def download_joplin_resource(resource_id):
    """
    通过Joplin API下载资源文件，返回本地文件路径和原始文件名
    """
    # 获取资源元数据，获取文件名和MIME类型
    meta_url = f"{joplin_api_base}/resources/{resource_id}?token={joplin_token}"
    resp = requests.get(meta_url)
    if resp.status_code != 200:
        return None, None
    meta = resp.json()
    original_filename = meta.get('title') or (resource_id + '.bin')
    
    # 清理文件名
    safe_filename = sanitize_filename(original_filename)
    
    # 下载文件内容
    file_url = f"{joplin_api_base}/resources/{resource_id}/file?token={joplin_token}"
    resp = requests.get(file_url)
    if resp.status_code != 200:
        return None, None
    
    # 确保文件名唯一
    local_path = os.path.join(OBSIDIAN_ATTACHMENT_DIR, safe_filename)
    unique_local_path = get_unique_filename(local_path)
    unique_filename = os.path.basename(unique_local_path)
    
    # 保存到attachments目录
    with open(unique_local_path, 'wb') as f:
        f.write(resp.content)
    return unique_local_path, unique_filename

def replace_joplin_resource_links(content, resource_map):
    """
    替换Joplin笔记中的资源引用为Obsidian本地路径
    resource_map: {resource_id: filename}
    """
    def repl(match):
        resource_id = match.group(1)
        filename = resource_map.get(resource_id, resource_id)
        return f'![](attachments/{filename})'
    return re.sub(r'!\[.*?\]\(:\/([a-f0-9]+)\)', repl, content)

def sync_joplin_to_obsidian(joplin_note, obsidian_folder='根目录'):
    """
    将 Joplin 笔记同步到 Obsidian（支持多级文件夹+附件）
    """
    try:
        # 检查是否已有同步信息，如果有就不重新生成
        existing_sync_info = extract_sync_info_from_joplin(joplin_note['body'])
        if existing_sync_info.get('notebridge_id'):
            sync_info = existing_sync_info
            content = joplin_note['body']  # 保持原有内容
        else:
            # 只有没有同步信息的笔记才生成新的
            sync_info = generate_sync_info('joplin')
            content = add_sync_info_to_joplin_content(joplin_note['body'], sync_info)
        
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
        
        # 清理文件名
        safe_title = sanitize_filename(joplin_note['title'])
        
        # 构建文件路径
        if obsidian_folder == '根目录':
            file_path = os.path.join(obsidian_vault_path, f"{safe_title}.md")
        else:
            # 清理文件夹路径 - 先替换反斜杠为正斜杠，再分割
            obsidian_folder_clean = obsidian_folder.replace('\\', '/')
            safe_folder_parts = [sanitize_filename(part) for part in obsidian_folder_clean.split('/')]
            folder_path = os.path.join(obsidian_vault_path, *safe_folder_parts)
            os.makedirs(folder_path, exist_ok=True)
            file_path = os.path.join(folder_path, f"{safe_title}.md")
        
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
        
        # 写入文件
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(final_file_path), exist_ok=True)
            
            with open(final_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
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
                all_notebooks[folder['title']] = {
                    'id': folder['id'],
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
    # 逐级创建或获取笔记本
    current_parent_id = None
    current_path = []
    for folder_name in path_parts:
        current_path.append(folder_name)
        full_path = '/'.join(current_path)
        # 检查是否已存在（用完整路径做key，防止同名不同层级冲突）
        found = None
        for k, v in all_notebooks.items():
            if k == full_path:
                found = v
                break
        if found:
            current_parent_id = found['id']
        else:
            try:
                create_url = f"{joplin_api_base}/folders?token={joplin_token}"
                create_data = {
                    'title': folder_name,
                    'parent_id': current_parent_id or ''
                }
                resp = requests.post(create_url, json=create_data, timeout=10)
                if resp.status_code == 200:
                    current_parent_id = resp.json()['id']
                    # 用完整路径做key
                    all_notebooks[full_path] = {
                        'id': current_parent_id,
                        'parent_id': current_parent_id
                    }
                    _joplin_notebooks_cache[full_path] = {
                        'id': current_parent_id,
                        'parent_id': current_parent_id
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
        # 检查是否已有同步信息，如果有就不重新生成
        existing_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
        if existing_sync_info.get('notebridge_id'):
            sync_info = existing_sync_info
            content = obsidian_note['body']  # 保持原有内容
        else:
            # 只有没有同步信息的笔记才生成新的
            sync_info = generate_sync_info('obsidian')
            content = add_sync_info_to_obsidian_content(obsidian_note['body'], sync_info)
        
        # 创建 Joplin 内容
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
            return True, resp.json()['id']
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
    max_retries = 2
    for attempt in range(max_retries + 1):
        start_time = time.time()
        try:
            print(f"[同步] 开始同步笔记: {obsidian_note['title']} (第{attempt+1}次尝试)")
            # 检查是否已有同步信息，如果有就不重新生成
            existing_sync_info = extract_sync_info_from_obsidian(obsidian_note['body'])
            if existing_sync_info.get('notebridge_id'):
                sync_info = existing_sync_info
                content = obsidian_note['body']  # 保持原有内容
            else:
                # 只有没有同步信息的笔记才生成新的
                sync_info = generate_sync_info('obsidian')
                content = add_sync_info_to_obsidian_content(obsidian_note['body'], sync_info)
            # 创建 Joplin 内容
            joplin_content = add_sync_info_to_joplin_content(content, sync_info)
            # 创建笔记（使用已知的笔记本ID）
            create_url = f"{joplin_api_base}/notes?token={joplin_token}"
            note_data = {
                'title': sanitize_filename(obsidian_note['title']),
                'body': joplin_content,
                'parent_id': notebook_id or ''
            }
            # 超时时间提升到30秒
            resp = requests.post(create_url, json=note_data, timeout=30)
            time.sleep(0.2)  # 每次创建后延迟，缓解Joplin压力
            end_time = time.time()
            duration = end_time - start_time
            if resp.status_code == 200:
                print(f"[同步] 成功: {obsidian_note['title']}，耗时 {duration:.2f} 秒")
                return True, resp.json()['id']
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
                print(f"[同步] 第{attempt+1}次超时，准备重试...")
                continue
            return False, f"创建笔记超时: {obsidian_note['title']}"
        except requests.exceptions.ConnectionError as e:
            end_time = time.time()
            duration = end_time - start_time
            print(f"[同步] 连接失败: {obsidian_note['title']}，耗时 {duration:.2f} 秒，异常: {e}")
            if attempt < max_retries:
                print(f"[同步] 第{attempt+1}次连接失败，准备重试...")
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

def update_obsidian_note(file_path, new_content):
    """
    更新 Obsidian 笔记内容
    """
    try:
        # 检查文件是否存在
        if not os.path.exists(file_path):
            return False, "文件不存在"
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True, None
    except FileNotFoundError:
        return False, "文件不存在"
    except PermissionError:
        return False, "无权限写入文件"
    except Exception as e:
        return False, str(e)

# 同步状态缓存文件
SYNC_CACHE_FILE = '.sync_cache.json'

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
        for item in tqdm(deletions['joplin_deletions'], desc="删除 Obsidian 文件"):
            # 构建文件路径（使用sanitize_filename处理文件名）
            safe_title = sanitize_filename(item['title'])
            if item['notebook'] == '未分类':
                file_path = os.path.join(obsidian_vault_path, f"{safe_title}.md")
            else:
                # 处理文件夹路径 - 先替换反斜杠为正斜杠，再分割
                notebook_path = item['notebook'].replace('\\', '/')
                safe_folder_parts = [sanitize_filename(part) for part in notebook_path.split('/')]
                folder_path = os.path.join(obsidian_vault_path, *safe_folder_parts)
                file_path = os.path.join(folder_path, f"{safe_title}.md")
            
            if os.path.exists(file_path):
                success, result = safe_delete_obsidian_file(file_path)
                if success:
                    deletion_results['success'].append(f"删除 Obsidian: {item['title']}")
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
    
    # 1. 更新已匹配的笔记对（根据同步方向）
    if matched_pairs and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']:
        print(f"\n📝 更新 {len(matched_pairs)} 对已匹配笔记...")
        for pair in tqdm(matched_pairs, desc="更新匹配笔记"):
            joplin_note = pair['joplin']
            obsidian_note = pair['obsidian']
            
            # 比较内容，决定是否需要更新
            joplin_content = joplin_note['body']
            obsidian_content = obsidian_note['body']
            
            # 提取纯内容（去除同步信息）
            joplin_sync_info = extract_sync_info_from_joplin(joplin_content)
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_content)
            
            # 比较同步时间，保留最新的
            joplin_time = joplin_sync_info.get('notebridge_sync_time', '')
            obsidian_time = obsidian_sync_info.get('notebridge_sync_time', '')
            
            if joplin_time > obsidian_time and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
                # Joplin 更新，同步到 Obsidian
                success, result = update_obsidian_note(obsidian_note['path'], joplin_content)
                if success:
                    sync_results['updated'].append(f"Joplin → Obsidian: {joplin_note['title']}")
                else:
                    sync_results['failed'].append(f"Joplin → Obsidian: {joplin_note['title']} - {result}")
            elif obsidian_time > joplin_time and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
                # Obsidian 更新，同步到 Joplin
                success, result = update_joplin_note(joplin_note['id'], obsidian_content)
                if success:
                    sync_results['updated'].append(f"Obsidian → Joplin: {obsidian_note['title']}")
                else:
                    sync_results['failed'].append(f"Obsidian → Joplin: {obsidian_note['title']} - {result}")
    
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
    
    # 按内容哈希分组
    joplin_groups = {}
    obsidian_groups = {}
    
    # 分组 Joplin 笔记
    for note in joplin_notes:
        # 计算内容哈希（去除同步信息）
        clean_body = clean_duplicate_sync_info_keep_oldest(note['body'])
        content_hash = calculate_content_hash(clean_body)
        title = note['title']
        
        key = f"{title}_{content_hash}"
        if key not in joplin_groups:
            joplin_groups[key] = []
        joplin_groups[key].append(note)
    
    # 分组 Obsidian 笔记
    for note in obsidian_notes:
        try:
            with open(note['path'], 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 计算内容哈希（去除同步信息）
            clean_content = clean_duplicate_sync_info_keep_oldest(content)
            content_hash = calculate_content_hash(clean_content)
            title = note['title']
            
            key = f"{title}_{content_hash}"
            if key not in obsidian_groups:
                obsidian_groups[key] = []
            obsidian_groups[key].append(note)
        except Exception as e:
            print(f"❌ 读取 Obsidian 笔记失败: {note['title']} - {e}")
    
    # 3. 删除重复笔记
    print("\n🗑️ 删除重复笔记...")
    deleted_count = 0
    
    # 删除 Joplin 重复笔记
    for key, notes in joplin_groups.items():
        if len(notes) > 1:
            print(f"发现 Joplin 重复笔记: {notes[0]['title']} ({len(notes)} 条)")
            
            # 保留第一条（优先保留有同步ID的）
            keep_note = notes[0]
            for note in notes[1:]:
                success, result = safe_delete_joplin_note(note['id'])
                if success:
                    deleted_count += 1
                    print(f"  ✅ 删除: {note['title']} (ID: {note['id']})")
                else:
                    print(f"  ❌ 删除失败: {note['title']} - {result}")
    
    # 删除 Obsidian 重复笔记
    for key, notes in obsidian_groups.items():
        if len(notes) > 1:
            print(f"发现 Obsidian 重复笔记: {notes[0]['title']} ({len(notes)} 条)")
            
            # 保留第一条
            keep_note = notes[0]
            for note in notes[1:]:
                success = safe_delete_obsidian_file(note['path'])
                if success:
                    deleted_count += 1
                    print(f"  ✅ 删除: {note['title']} (路径: {note['path']})")
                else:
                    print(f"  ❌ 删除失败: {note['title']}")
    
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
    
    # 动态同步状态：在同步过程中实时更新
    dynamic_sync_state = {
        'joplin_notes': {},
        'obsidian_notes': {}
    }
    
    # 1. 更新已匹配的笔记对（根据同步方向）
    if matched_pairs and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian', 'obsidian_to_joplin']:
        print(f"\n📝 更新 {len(matched_pairs)} 对已匹配笔记...")
        for pair in tqdm(matched_pairs, desc="更新匹配笔记"):
            joplin_note = pair['joplin']
            obsidian_note = pair['obsidian']
            
            # 比较内容，决定是否需要更新
            joplin_content = joplin_note['body']
            obsidian_content = obsidian_note['body']
            
            # 提取纯内容（去除同步信息）
            joplin_sync_info = extract_sync_info_from_joplin(joplin_content)
            obsidian_sync_info = extract_sync_info_from_obsidian(obsidian_content)
            
            # 比较同步时间，保留最新的
            joplin_time = joplin_sync_info.get('notebridge_sync_time', '')
            obsidian_time = obsidian_sync_info.get('notebridge_sync_time', '')
            
            if joplin_time > obsidian_time and SYNC_DIRECTION in ['bidirectional', 'joplin_to_obsidian']:
                # Joplin 更新，同步到 Obsidian
                success, result = update_obsidian_note(obsidian_note['path'], joplin_content)
                if success:
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
            elif obsidian_time > joplin_time and SYNC_DIRECTION in ['bidirectional', 'obsidian_to_joplin']:
                # Obsidian 更新，同步到 Joplin
                success, result = update_joplin_note(joplin_note['id'], obsidian_content)
                if success:
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
    """
    if not content:
        return ""
    
    # 去除HTML注释（同步信息）
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    
    # 去除YAML frontmatter
    content = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
    
    # 去除markdown链接 [text](url) -> text
    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
    
    # 去除markdown图片 ![alt](url) -> alt
    content = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', content)
    
    # 去除HTML标签
    content = re.sub(r'<[^>]+>', '', content)
    
    # 去除markdown语法标记
    content = re.sub(r'[*_`~#]+', '', content)  # 去除粗体、斜体、代码等标记
    
    # 标准化空白字符
    content = re.sub(r'\s+', ' ', content)
    
    # 去除首尾空白
    content = content.strip()
    
    return content

def find_duplicates_optimized(joplin_notes, obsidian_notes):
    """
    优化版查重功能（性能更好，更准确）
    """
    duplicates = {
        'exact_duplicates': [],      # 完全重复（内容哈希相同）
        'title_similar': [],         # 标题相似
        'content_similar': [],       # 内容相似
        'content_hash_duplicates': [] # 内容哈希相同
    }
    
    print("正在扫描重复内容（优化版）...")
    
    # 1. 基于内容哈希的完全重复检测（排除空笔记）
    joplin_hashes = {}
    obsidian_hashes = {}
    
    # 预处理并计算哈希
    for note in joplin_notes:
        if not is_empty_note(note['body']):
            processed_content = preprocess_content_for_comparison(note['body'])
            if processed_content:  # 确保预处理后不为空
                content_hash = calculate_content_hash(processed_content)
                joplin_hashes[content_hash] = note
    
    for note in obsidian_notes:
        if not is_empty_note(note['body']):
            processed_content = preprocess_content_for_comparison(note['body'])
            if processed_content:  # 确保预处理后不为空
                content_hash = calculate_content_hash(processed_content)
                obsidian_hashes[content_hash] = note
                if content_hash in joplin_hashes:
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
    
    # 使用优化版查重
    duplicates = find_duplicates_optimized(joplin_notes, obsidian_notes)
    
    # 打印查重报告
    print_duplicate_report(duplicates)
    
    total_duplicates = (len(duplicates['content_hash_duplicates']) + 
                       len(duplicates['exact_duplicates']) + 
                       len(duplicates['title_similar']) + 
                       len(duplicates['content_similar']))
    
    if total_duplicates == 0:
        print("\n✅ 没有发现重复笔记！")
        return
    
    print(f"\n💡 发现 {total_duplicates} 对重复/相似笔记")
    print("请选择清理策略：")
    print("1. 自动清理（保留Joplin版本）")
    print("2. 自动清理（保留Obsidian版本）")
    print("3. 交互式清理（逐个选择）")
    print("4. 跳过清理")
    
    choice = input("\n请输入选择 (1-4): ").strip()
    
    if choice == "1":
        auto_clean_duplicates(duplicates, keep_joplin=True)
    elif choice == "2":
        auto_clean_duplicates(duplicates, keep_joplin=False)
    elif choice == "3":
        interactive_clean_duplicates_step_by_step(duplicates)
    else:
        print("跳过清理。")

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

def interactive_clean_duplicates_step_by_step(duplicates):
    """
    交互式逐步清理重复笔记
    """
    print(f"\n🎯 开始交互式清理...")
    
    all_duplicates = []
    all_duplicates.extend(duplicates['content_hash_duplicates'])
    all_duplicates.extend(duplicates['exact_duplicates'])
    all_duplicates.extend(duplicates['title_similar'])
    all_duplicates.extend(duplicates['content_similar'])
    
    cleaned_count = 0
    
    for i, dup in enumerate(all_duplicates, 1):
        print(f"\n--- 第 {i}/{len(all_duplicates)} 对重复笔记 ---")
        print(f"Joplin: {dup['joplin']['title']} ({dup['joplin']['notebook']})")
        print(f"Obsidian: {dup['obsidian']['title']} ({dup['obsidian']['folder']})")
        
        if 'title_similarity' in dup:
            print(f"相似度: 标题{dup['title_similarity']:.1%}, 内容{dup['content_similarity']:.1%}")
        
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
    
    j_content = dup['joplin']['body'][:200] + "..." if len(dup['joplin']['body']) > 200 else dup['joplin']['body']
    o_content = dup['obsidian']['body'][:200] + "..." if len(dup['obsidian']['body']) > 200 else dup['obsidian']['body']
    
    print("Joplin 内容预览：")
    print(j_content)
    print("\nObsidian 内容预览：")
    print(o_content)
    print("="*50)

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
                # 预览模式
                print("\n🔄 启动智能同步模式...")
                
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
                
                # 打印同步计划
                print_sync_plan(matched_pairs, unmatched_joplin, unmatched_obsidian)
                
                print("\n💡 这是预览模式。要执行实际同步，请运行:")
                print("  python notebridge.py sync --force                    # 双向同步")
                print("  python notebridge.py sync --force --joplin-to-obsidian  # 仅 Joplin → Obsidian")
                print("  python notebridge.py sync --force --obsidian-to-joplin  # 仅 Obsidian → Joplin")
                
            else:
                # 执行实际同步
                print("\n🔄 启动智能同步模式...")
                
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
                
                # 执行同步
                sync_results = perform_sync_with_skip(matched_pairs, unmatched_joplin, unmatched_obsidian)
                
                # 打印结果
                print_sync_results_with_skip(sync_results)
            
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
            duplicates = find_duplicates_optimized(joplin_notes, obsidian_notes)
            
            # 打印查重报告
            print_duplicate_report(duplicates)
            
        elif command == "interactive-clean":
            # 交互式清理重复笔记
            interactive_clean_duplicates()
            
        elif command == "clean-duplicates":
            # 自动清理重复笔记和同步ID
            find_and_remove_duplicates()
            
        elif command == "fix-attachments":
            fix_obsidian_attachments()
            sys.exit(0)
        
        else:
            print(f"❌ 未知命令: {command}")
            print("\n📖 使用方法:")
            print("  python notebridge.py sync         # 智能同步预览")
            print("  python notebridge.py sync --force # 执行实际同步")
            print("  python notebridge.py sync --force --joplin-to-obsidian  # 仅 Joplin → Obsidian")
            print("  python notebridge.py sync --force --obsidian-to-joplin  # 仅 Obsidian → Joplin")
            print("  python notebridge.py check-duplicates # 查重模式（优化版）")
            print("  python notebridge.py interactive-clean # 交互式清理重复笔记")
            print("  python notebridge.py clean-duplicates # 自动清理重复笔记和同步ID")
            print("  python notebridge.py fix-attachments # 补全 Obsidian 附件")
    else:
        # 同步预览模式
        print("\n🔄 启动智能同步模式...")
        
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
        
        # 打印同步计划
        print_sync_plan(matched_pairs, unmatched_joplin, unmatched_obsidian)
        
        print("\n💡 这是预览模式。要执行实际同步，请运行:")
        print("  python notebridge.py sync --force                    # 双向同步")
        print("  python notebridge.py sync --force --joplin-to-obsidian  # 仅 Joplin → Obsidian")
        print("  python notebridge.py sync --force --obsidian-to-joplin  # 仅 Obsidian → Joplin")
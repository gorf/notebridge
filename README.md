# notebridge

古有东家食西家眠，Joplin和Obsidian的特性我都割舍不掉，于是做了这个命令行工具，帮你实现 Joplin 和 Obsidian 笔记的双向同步。目前只是一个粗糙的初始版本。

---

## 工具简介

notebridge 可以让你在 Windows 系统下，轻松同步 Joplin 和 Obsidian 里的所有笔记内容，包括：
- 标题
- 正文
- 标签
- 附件（如图片、PDF 等）
- 文件夹结构
- 支持选择性同步（某些笔记本只单向同步或跳过）
- 支持同步方向控制（双向、单向）

你只需要在命令行输入一条命令，工具就会自动帮你把两边的内容保持一致。

---

## 安装方法

1. 安装 Python（推荐 3.8 及以上版本）。
2. 下载本项目代码。
3. 在命令行中进入项目文件夹，运行：
   ```
   pip install -r requirements.txt
   ```

---

## 配置方法

首次使用前，需要进行简单配置：

1. **Joplin 设置**
   - 打开 Joplin，进入设置 → Web Clipper → 启用 Web Clipper 服务。
   - 记下"端口号"和"令牌"。
2. **Obsidian 设置**
   - 找到你 Obsidian 的笔记库文件夹路径。
3. **创建配置文件**
   - 在项目根目录下新建 `config.json`，内容示例：
     ```json
     {
       "joplin": {
         "api_base": "http://127.0.0.1:41184",
         "token": "你的Joplin令牌"
       },
       "obsidian": {
         "vault_path": "C:/Users/你的用户名/Obsidian 笔记库路径"
       },
       "sync_rules": {
         "joplin_to_obsidian_only": ["工作笔记", "临时笔记"],
         "obsidian_to_joplin_only": ["个人日记"],
         "skip_sync": ["草稿", "测试"],
         "bidirectional": ["学习笔记", "项目文档"]
       }
     }
     ```

---

## 使用方法

### 基本同步命令

```bash
# 预览同步计划（不执行实际同步）
python notebridge.py sync

# 执行双向同步
python notebridge.py sync --force

# 仅从 Joplin 同步到 Obsidian
python notebridge.py sync --force --joplin-to-obsidian

# 仅从 Obsidian 同步到 Joplin
python notebridge.py sync --force --obsidian-to-joplin
```

### 其他功能命令

```bash
# 检查重复笔记（优化版，性能更好）
python notebridge.py check-duplicates

# 交互式清理重复笔记（推荐）
python notebridge.py interactive-clean

# 自动清理重复笔记和同步ID
python notebridge.py clean-duplicates

# 补全 Obsidian 中缺失的附件
python notebridge.py fix-attachments
```

### 同步方向说明

- **双向同步**（默认）：Joplin 和 Obsidian 之间相互同步
- **Joplin → Obsidian**：只从 Joplin 同步到 Obsidian，适合首次导入
- **Obsidian → Joplin**：只从 Obsidian 同步到 Joplin，适合备份

### 选择性同步配置

在 `config.json` 中可以配置不同笔记本的同步规则（支持通配符模式匹配）：

- `joplin_to_obsidian_only`：只从 Joplin 同步到 Obsidian
- `obsidian_to_joplin_only`：只从 Obsidian 同步到 Joplin  
- `skip_sync`：跳过同步
- `bidirectional`：双向同步（默认）

#### 通配符支持

所有同步规则都支持通配符模式匹配：

- `*` 匹配任意数量的字符，如 `"Conflict*"` 匹配所有以 Conflict 开头的笔记本
- `?` 匹配单个字符，如 `"测试?"` 匹配 "测试1", "测试2" 等

示例配置：
```json
{
  "sync_rules": {
    "joplin_to_obsidian_only": ["工作笔记", "项目*"],
    "obsidian_to_joplin_only": ["个人日记", "备份*"],
    "skip_sync": ["Conflict*", "临时*", "草稿*"],
    "bidirectional": ["重要*", "学习*"]
  }
}
```

### 智能查重与清理功能

#### 优化版查重
```bash
python notebridge.py check-duplicates
```
- 使用智能内容预处理（去除markdown语法、HTML标签等）
- 基于标题的快速预筛选，大幅提升性能
- 更准确的相似度计算
- 支持多种重复类型检测

#### 交互式清理（推荐）
```bash
python notebridge.py interactive-clean
```
- 智能检测重复笔记
- 提供多种清理策略选择
- 支持内容对比预览
- 逐个确认删除操作，安全可靠

#### 自动清理
```bash
python notebridge.py clean-duplicates
```
- 自动清理所有笔记中的重复同步ID
- 自动查找并删除重复笔记
- 确保笔记库干净无冲突

---

## 常见问题

- **Q：同步时会不会丢失内容？**
  A：工具会尽量避免丢失内容。如果两边同时修改同一条笔记，会保留最新的版本。
- **Q：支持哪些内容同步？**
  A：支持标题、正文、标签、附件、文件夹结构等。
- **Q：需要一直开着 Joplin 吗？**
  A：需要，且 Web Clipper 服务必须开启。
- **Q：如何处理同步冲突？**
  A：工具会基于时间戳自动选择最新版本，避免手动处理冲突。
- **Q：可以只同步部分笔记吗？**
  A：可以，通过配置 `sync_rules` 可以精确控制哪些笔记本如何同步。
- **Q：程序运行时手动删除文件会出错吗？**
  A：不会，程序已经优化了错误处理机制，会自动跳过不存在的文件并继续运行。
- **Q：遇到权限问题怎么办？**
  A：程序会自动检测权限错误并跳过有问题的文件，不会中断整个同步过程。

---

## 进阶用法与开发计划

- 支持定时自动同步
- 支持同步历史版本
- 支持更多自定义选项

如有建议或问题，欢迎反馈！

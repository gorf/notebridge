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
- **基于ID的可靠识别**：使用 `notebridge_id` 确保笔记一致性，不受文件名变化影响

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

# 手工确认模式同步（推荐，防止重复头部问题）
python notebridge.py sync-manual

# 手工确认单向同步
python notebridge.py sync-manual --joplin-to-obsidian
python notebridge.py sync-manual --obsidian-to-joplin
```

### 其他功能命令

```bash
# 检查重复笔记（超快速版，性能大幅提升）
python notebridge.py check-duplicates

# 快速标题相似度检测（推荐，手工决定）
python notebridge.py quick-title-check

# 清理Obsidian中来自Joplin的笔记
python notebridge.py clean-joplin-imports

# 性能测试对比（新旧算法对比）
python notebridge.py test-duplicates

# 交互式清理重复笔记（推荐）
python notebridge.py interactive-clean

# 自动清理重复笔记和同步ID
python notebridge.py clean-duplicates

# 补全 Obsidian 中缺失的附件
python notebridge.py fix-attachments
```

### 同步模式说明

#### 自动同步模式
- **双向同步**（默认）：Joplin 和 Obsidian 之间相互同步
- **Joplin → Obsidian**：只从 Joplin 同步到 Obsidian，适合首次导入
- **Obsidian → Joplin**：只从 Obsidian 同步到 Joplin，适合备份

#### 手工确认模式（推荐）
**为什么推荐手工确认模式？**
- ✅ **防止重复头部问题**：每次同步前自动检查并修复重复的同步信息头部
- ✅ **防止反向同步**：自动检测笔记来源，避免将笔记反向同步回原处（如 Obsidian → Joplin → Obsidian）
- ✅ **完全可控**：每条笔记同步前都会显示详情，由您决定是否同步
- ✅ **安全可靠**：可以随时查看笔记内容、同步状态、是否有重复头部、笔记来源等信息
- ✅ **灵活操作**：支持跳过单条、跳过所有、退出等操作

**使用场景：**
- 首次同步时，建议使用手工确认模式
- 解决重复头部问题后的第一次同步
- 不确定哪些笔记需要同步时
- 需要仔细检查每条笔记时

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

#### 快速标题相似度检测（推荐）
```bash
python notebridge.py quick-title-check
```
- ⚡ **极速检测**：只检测标题相似度，速度极快
- 🎯 **手工决定**：让你完全控制哪些是重复的
- 📝 **内容预览**：显示笔记内容预览，方便判断
- 🔧 **可调阈值**：可设置相似度阈值（70%-90%）
- 📊 **详细对比**：可查看完整内容对比
- 🛡️ **安全确认**：删除前需要确认，避免误删

#### 清理Joplin导入笔记（推荐）
```bash
python notebridge.py clean-joplin-imports
```
- 🔍 **智能检测**：自动识别Obsidian中来自Joplin的笔记
- 📊 **状态分析**：区分未修改、已修改、孤立的笔记
- 🎯 **灵活选择**：可选择删除全部、只删除未修改的、或只删除孤立的
- 🛡️ **安全操作**：删除前需要确认，避免误删
- 💡 **重新同步**：清理后可重新同步，避免重复

#### 超快速查重（全自动）
```bash
python notebridge.py check-duplicates
```
- 🚀 **分层检测算法**：使用5层检测策略，性能提升3-5倍
- 🔍 **智能内容预处理**：更彻底地去除头部信息、markdown语法、HTML标签等
- 💾 **缓存机制**：避免重复计算，大幅提升检测速度
- 🎯 **高级相似度计算**：专门处理"去掉头部信息后内容相同"的情况
- 📊 **详细统计报告**：提供性能统计、重复率分析等详细信息
- 🔧 **多种重复类型检测**：ID重复、内容哈希重复、标题相似、内容相似、去头部后重复

#### 性能测试
```bash
python notebridge.py test-duplicates
```
- 对比新旧算法性能
- 显示检测结果差异
- 提供性能提升倍数

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
- **Q：新的重复检测算法有什么改进？**
  A：新算法使用5层检测策略，性能提升3-5倍，能更准确地检测"去掉头部信息后内容相同"的重复笔记。
- **Q：查重速度太慢怎么办？**
  A：新版本已经大幅优化了性能，使用缓存机制和分层检测，速度提升显著。如果仍然慢，可以运行 `python notebridge.py test-duplicates` 查看性能对比。
- **Q：如何检测"去掉头部信息后内容相同"的重复？**
  A：新算法专门增加了第5层检测，使用高级相似度计算，能准确识别这类重复。
- **Q：单向同步规则没有生效怎么办？**
  A：最新版本已修复单向同步规则过滤问题。现在程序会正确检查每个笔记的同步规则，确保只同步允许方向的笔记。如果仍有问题，请检查配置文件中的同步规则设置。

---

## 最新更新

### v1.3.0 - 添加手工确认模式，彻底解决重复头部和反向同步问题
- ✅ **新增手工确认同步模式**：每条笔记同步前都需要人工确认，完全可控
- ✅ **修复手工确认模式的同步规则检查**：手工确认模式现在也会严格遵守配置的同步规则
  - 已匹配的笔记对：检查是否允许指定方向的同步
  - 新笔记：检查笔记本/文件夹是否允许同步
  - 自动跳过不符合规则的笔记，并显示原因
- ✅ **智能防止反向同步**：自动检测笔记来源，避免将未修改的笔记反向同步回原处
  - 笔记来自 Obsidian → Joplin 后，如果在 Joplin 端未修改，不会同步回 Obsidian
  - 笔记来自 Joplin → Obsidian 后，如果在 Obsidian 端未修改，不会同步回 Joplin
  - 只有真正修改过的笔记才会同步，通过时间戳智能判断
- ✅ **修复同步信息格式问题**：
  - Joplin 使用 HTML 注释格式：`<!-- notebridge_id: xxx -->`
  - Obsidian 使用 YAML frontmatter 格式：在笔记属性中
  - 同步时自动转换格式，不再混合使用
- ✅ **双端回写同步信息**：同步成功后，两端都会有正确格式的同步信息
  - Joplin → Obsidian 后，Joplin 端也会添加同步信息（HTML注释）
  - Obsidian → Joplin 后，Obsidian 端也会添加同步信息（YAML格式）
  - 强制回写，确保不会重复同步
- ✅ **增强图片链接处理**：支持HTML和Markdown两种格式的图片
  - 支持 `<img src=":/资源ID"/>` 格式（HTML）
  - 支持 `![](:/资源ID)` 格式（Markdown）
  - 自动下载资源并转换为Obsidian本地路径
  - 保留原始尺寸信息（作为注释）
- ✅ **修复同步信息字段缺失问题**：
  - 确保提取的同步信息包含所有必需字段
  - 缺失的字段使用默认值（`notebridge_version` 默认为 `'1'`）
  - 避免同步时出现 `'notebridge_version'` 等字段缺失错误
- ✅ **自动跳过空笔记和无效笔记**：
  - 自动跳过空标题的笔记（可能已删除）
  - 自动跳过空内容的笔记
  - 避免同步无效或已删除的笔记
- ✅ **自动检测和修复重复头部**：同步过程中自动检查并修复重复的同步信息头部
- ✅ **增强同步信息清理逻辑**：彻底清理HTML注释和YAML格式的混合重复信息
- ✅ **添加预防性检查命令**：`prevent-duplicate-headers` 用于定期检查重复头部
- ✅ **修复时间戳问题**：避免生成未来时间戳

### v1.2.0 - 修复单向同步规则过滤问题
- ✅ **修复单向同步规则未生效的问题**：现在程序会正确检查每个笔记的同步规则，确保只同步允许方向的笔记
- ✅ **增强同步规则检查**：在同步执行时对每个笔记进行同步规则验证
- ✅ **改进同步报告**：新增单向同步限制跳过的统计信息
- ✅ **添加测试脚本**：`test_sync_rules.py` 用于验证同步规则逻辑

### 反向同步问题解决方案（智能判断，无需手工）

**什么是反向同步问题？**
- 笔记从 Obsidian 同步到 Joplin 后，如果在 Joplin 端未修改，不应该再同步回 Obsidian
- 笔记从 Joplin 同步到 Obsidian 后，如果在 Obsidian 端未修改，不应该再同步回 Joplin

**智能判断逻辑（自动，无需手工）：**
1. 检测笔记来源（`notebridge_source` 字段）
2. 比较两端的同步时间戳
3. **如果时间戳相同** → 说明未修改 → 自动跳过
4. **如果时间戳不同** → 说明有修改 → 允许同步

**应用场景：**
- ✅ 场景1：笔记来自 Obsidian，在 Joplin 未修改 → **自动跳过**
- ✅ 场景2：笔记来自 Obsidian，在 Joplin 有修改 → **允许同步**
- ✅ 场景3：笔记来自 Joplin，在 Obsidian 未修改 → **自动跳过**
- ✅ 场景4：笔记来自 Joplin，在 Obsidian 有修改 → **允许同步**

### 重复头部问题解决方案
1. **立即修复**：运行 `python notebridge.py fix-duplicate-headers` 修复现有的重复头部
2. **预防措施**：
   - 使用手工确认模式同步：`python notebridge.py sync-manual`
   - 每次同步前自动检查并修复重复头部
   - 定期运行预防性检查：`python notebridge.py prevent-duplicate-headers`
3. **根本解决**：
   - 改进了同步信息添加逻辑，彻底清理旧的同步信息
   - 在 `update_obsidian_note` 函数中添加了重复头部检查
   - 修复了时间戳生成逻辑
   - **新增反向同步智能检测**：自动跳过未修改的反向同步

## 进阶用法与开发计划

- 支持定时自动同步
- 支持同步历史版本
- 支持更多自定义选项

如有建议或问题，欢迎反馈！

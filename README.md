# MAAZS_RACING

基于 MAA Framework + YOLOv8 的《巅峰极速》自动化项目。

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python main.py

项目结构
表格
文件/目录	说明
main.py	主程序入口
gui.py	GUI 界面
train.py	YOLO 模型训练
capture.py	数据采集
tools/	辅助工具
assets/	模型资源
dataset/	训练数据集
config/	配置文件
AI 开发指南
见 HANDOVER.md —— 给 AI 助手的上下文文档。
备份与回滚
本项目使用 Git 管理代码。
每次重大修改前执行：git add -A && git commit -m "描述"
回滚到上次提交：git checkout .
plain

---

### 4. 提交到 Git

在 VSCode 终端（`Ctrl + ``）里执行：

```bash
cd MAAZS_RACING

# 查看状态（应该看到 HANDOVER.md、README.md、.gitignore 是未追踪的）
git status

# 加入暂存区
git add -A

# 提交
git commit -m "docs: 添加 AI 交接文档和项目说明"

红线提醒
表格
❌ 永远不要放进 Git	       ✅ 应该放进 Git
API Key / 密码	            代码逻辑
.venv/ 文件夹	            requirements.txt
dataset/ 里的图片	        dataset.yaml
assets/model/*.onnx	        HANDOVER.md
logs/、debug/              	README.md
个人配置（含路径）	         .gitignore

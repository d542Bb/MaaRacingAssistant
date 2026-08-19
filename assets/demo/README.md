# 演示素材（assets/demo）说明

本目录存放 README「演示」小节引用的素材（README 用相对路径引用）。

## 现有文件

| 文件 | 内容 | 状态 |
|---|---|---|
| `mra_preview.mp4` | 自动鉴宝录屏（演示视频，12.9MB 未压缩版） | ✅ 已就位 |
| `shot_control.png` | GUI 主控（12 阶段 + ▶ 指示器） | ✅ 已就位 |
| `shot_dashboard.png` | 数据页·今日看板 | ✅ 已就位 |
| `shot_peep.png` | 数据页·PEEP 实时预览（同时用作 README 演示缩略图） | ✅ 已就位 |

> **README 视频展示方案（已落地）：**
> - GitHub README 不支持普通仓库文件路径的 `<video>` 标签（会被渲染器剥离）
> - 已通过 GitHub issue #1（演示素材托管）上传压缩版 MP4（6MB），获得 `user-attachments` 稳定 URL，README 用 `<video src="https://github.com/user-attachments/assets/9bf47361-2773-447c-9900-bdf70d4b2af0">` 内联播放（带控件）
> - 仓库内 `mra_preview.mp4` 为本地源文件；上传到 user-attachments 的为压缩版（5.77MB）

## 录制建议（来自 docs/PRESENTATION.md）

- **视频素材**：录"数据页预览窗口 + 自动出价"屏幕 → MP4。
- **截图**：3 张核心图放 README「已实现模块」附近（或"/演示"节）。

> 合规：所有物料必须落在 README 免责声明红线内（仅技术/教学演示）。

# UI 主题与设计系统

## 设计理念

模镜当前主题是“AI 牛马招聘会”：用户像逛招聘会一样浏览模型、智能体、MCP、Skill 和提示词。模型是候选人，价格是期望薪资，能力是技能标签，聊天页是面试间。

这一主题只定义产品入口、UX 文案和视觉隐喻；平台定位与成熟度边界见 [VISION.md](./VISION.md)。

设计目标：

- 有趣，但不牺牲专业感。
- 深色基底，暖色点缀，突出“赛博招聘会”氛围。
- 所有资源页共享导航、卡片和服务台风格。
- 移动端保持可触达，不堆叠过多筛选项。

## 色彩系统

颜色定义在 `client/tailwind.config.js`。

| 色系 | 用途 | 示例 |
| --- | --- | --- |
| `hire` | 招聘会暖色、重点按钮、提示标签 | `hire-300`, `hire-400` |
| `brand` | 科技青色、链接、次重点状态 | `brand-300`, `brand-500` |
| `accent` | 紫色强调、特殊状态 | `accent-400`, `accent-600` |
| `ink` | 深色背景 | `ink-950`, `ink-900` |
| `surface` | 卡片和面板层级 | `surface-900`, `surface-800` |
| `prism` | 霓虹点缀 | `prism-cyan`, `prism-violet` |

## 字体系统

Tailwind `fontFamily.sans`：

```javascript
["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"]
```

原则：

- 标题使用较高字重，但不要过度压缩字距。
- 卡片内标题保持简洁，描述使用 12-14px 的辅助文本。
- 不使用随视口宽度变化的字体大小。

## 组件模式

### 卡片

常用类：

```text
rounded-lg border border-white/10 bg-white/[0.045] shadow-prism
```

交互：

```text
transition duration-200 hover:-translate-y-0.5 hover:border-hire-300/40
```

### 按钮

主按钮：

```text
rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 hover:bg-hire-200
```

次按钮：

```text
rounded-full border border-white/10 bg-white/[0.045] text-slate-200 hover:border-hire-300/35
```

### 标签

```text
rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-[11px]
```

### 聊天气泡

- 用户：主色或暖色强调，右对齐。
- 助手：深色卡片，左对齐，保留 Markdown 可读性。
- 图片：圆角缩略图，点击放大。

## 动效规范

已定义动画：

- `animate-soft-rise`：卡片入场。
- `animate-slow-pan`：背景弥散光移动。
- `animate-pulse-line`：状态提示。

原则：

- 普通 hover：150-200ms。
- 面板展开：200-300ms。
- 优先使用 `transform` 和 `opacity`，避免 layout reflow。
- 不在大列表上使用昂贵动画。

## 响应式断点

- 移动端：资源导航切换到底部或紧凑模式，卡片单列。
- 平板：保持主内容优先，侧边栏可隐藏。
- 桌面：`PageContainer` 显示左侧服务台和主内容区域。

## 无障碍标准

- 普通文本对比度目标 ≥ 4.5:1。
- 可点击元素必须有 hover 和 focus 状态。
- 按钮使用真实 `<button>`，链接使用 `<a>` 或 `Link`。
- 图标不能作为唯一信息来源，应配合文字或 `title`。
- 输入框和范围滑块要保留键盘可操作性。

## Tailwind 使用约定

- 优先使用已有 token，避免随手写大量任意色值。
- 任意值只用于复杂背景或特殊阴影。
- 不新增外部 UI 库。
- 新增主题 token 后必须在本文档补充说明。

最后更新日期：2026-08-09
维护人：模镜团队

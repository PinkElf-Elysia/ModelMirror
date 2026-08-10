# Product

## Register

product

## Users

模镜面向需要发现、比较、试用并组合 AI 能力的中文用户，包括开发者、创作者、产品团队和研究人员。用户可能从模型选择进入，也可能需要 MCP、Skill、知识库、Agent 或 Workflow；共同需求是降低理解异构 AI 生态和验证组合效果的成本。

## Product Purpose

模镜当前是一个可本地部署的 AI 资源发现、比较、调用与组合工作台。它帮助用户定位候选模型和能力资源，理解适用场景与成本，在聊天及专项工作区完成文本、图片、音频或视频试用，并在不同产品入口中使用 MCP、Skill、RAG、Data X 与 Workflow。

成功体验是：用户不需要先掌握每个模型供应商、Agent 框架和工具协议，也能找到、试用并组合合适的 AI 能力。目标产品引擎是 AI Capability Compiler，但完整能力控制平面、自演进和能力内核不属于当前产品完成度声明。

## Positioning Boundary

- “AI 牛马招聘会”是用户入口和体验隐喻。
- AI Capability Compiler 是目标产品类别。
- 中立 AI 能力控制平面是商业与架构方向。
- AI Capability OS / Self-Evolving Meta-System 是长期研究愿景。

详细叙事见 [docs/VISION.md](docs/VISION.md)，当前实现边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## Brand Personality

现代、可靠、普惠。界面应像专业工具一样克制、清晰、高效，同时保留“镜”这一品牌意象带来的精密、透亮和可洞察感。

## Anti-references

不要做成营销落地页、炫技型玻璃拟态、纯黑低对比界面、过度装饰的 AI SaaS 模板，或带有不稳定 affordance 的实验性工具。避免堆叠卡片、过度圆角、过多渐变和影响效率的页面入场动画。

## Design Principles

1. 信息先于装饰：价格、能力、筛选状态和聊天入口必须比视觉效果更容易被读到。
2. 深色但不死黑：用分层中性色、细边框和适度高光构建空间，而不是大面积纯黑。
3. 一套组件语言：按钮、标签、卡片、输入框和侧边栏在各资源页与工作台页面中保持一致。
4. 微交互服务状态：hover、focus、active 和展开收起动效只用于表达可操作性和状态变化。
5. 中文阅读友好：标签、说明、空状态和按钮文案保持直接、短句、可扫描。

## Accessibility & Inclusion

以 WCAG AA 为无障碍目标：普通文本对比度不低于 4.5:1，大文本不低于 3:1。所有可点击元素保留键盘焦点状态，移动端无横向溢出；动画遵守 `prefers-reduced-motion`，对色弱用户不只依赖颜色表达状态。

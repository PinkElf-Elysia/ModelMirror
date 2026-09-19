# RPG 插件 PR 主线整合与帮助复验

日期：2026-09-19。基线 a29da8a5ecd4c3034eb3d7ace9229b379db198b9，分支 codex/rpg-plugin-branch-pr；接受源码208a6b7c，整合提交09c70c06。最新主线较原接受基线多2提交，唯一文件交集App.tsx，两个新增路由均保留。核心代码与接受版本一致，运行hash 05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca。

## 真实界面重放

全新独立18439前端/18438后端，合成林遥会话，Provider关闭、预算0；现有18433/18435/18437和真实数据未动。按已写教程从全新origin重放：

|操作|实际结果|
|---|---|
|RPG → 插件市场 → 安装|已安装1；会话未自动启用|
|地球OL → 历史 → 林遥 → 插件 → 授权|当前会话启用，正文分支可用|
|填写草稿 → 正文分支 → 命名 → 创建并进入|新路线1回合；创建未调用模型；新路线授权关闭|
|对话列表切回原路线|草稿恢复；父子对话同列展示及来源可见|
|插件 → 停用|分支按钮禁用，已有数据保留|
|最终帮助页面|两张PNG实际加载；390px页面clientWidth=scrollWidth=380|

原始浏览器JPEG均在screenshots/；无AI绘制或截图伪造。PNG由原JPEG无损解码重编码，逐像素校验相同。入口790×691/244860字节；分支1000×800/105038字节。旧B4图移入历史证据，不删除。

## 实际验证

- 模块：node --test plugins/*.test.mjs studio/*.test.mjs card-replica/tests/*.test.mjs，63通过。
- client：npx.cmd vitest run src/content/help-center/helpContent.test.ts src/pages/HelpCenterPage.test.tsx src/pages/RpgPluginsPage.test.tsx src/pages/RpgChatState.test.ts src/components/StudioBetaPanels.test.tsx --configLoader runner --maxWorkers=1 --fileParallelism=false，63通过。
- client npm.cmd run build；两卡npm.cmd run build -- --base=/rpg-app/earth/（或rpg05），均通过；现有大chunk警告保留。
- docker build -f studio/Dockerfile -t modelmirror-rpg:plugin-pr . 通过。镜像ID sha256:dcd338b014dfd607d552ef4ca0c4266b9b18e7e0a14e850c59c802a251ddd004。
- docker run --rm --network none --read-only --tmpfs /data:rw,uid=1000,gid=1000,noexec,nosuid --entrypoint node modelmirror-rpg:plugin-pr studio/plugin-container-smoke.mjs /data/check 05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca，通过安装不启用、幂等分支、卸载/重启读取续玩、父快照保持及重装不授权；预算0。
- 全站帮助图片校验失败3处：b266b870/studio-beta-headings.jpg、eeb5bbd2/creator-cache.jpg、eeb5bbd2/studio-layout.jpg，均为原主线无关残留。此次两张RPG图片全规则通过。
- 后端全量测试未运行（没有Python改动）；远端CI在PR创建后查看，不提前宣称通过。

## 保留的失败与纠正

首次前端测试62通过/1失败：帮助基线断言仍为bac37a6e；同步a29da8a5后原套件63通过。首次入口PNG289207字节超限；重拍较小视口后244860字节通过，原JPEG保留。默认git diff --check将既有CRLF新增行的CR报空白；明确cr-at-eol后通过，未改冻结字节。工作树创建未完成时首次cherry-pick被Git拒绝，待完成并验证干净后成功；没有丢弃数据。截图文件直接写入受沙箱限制，改由临时loopback表单传递原buffer、固定路径/hash/大小校验落盘；临时接收服务已停。

## 验收与回退边界

B5真实三份分别获用户通过；原始证据保留在原工作区，当前整合没有追加派发，3/4且第4次保留。整合只改变帮助/登记与主线接合，不重测模型、不泛化长期质量。发布仅授权Commit/Push/PR，未合并和共享部署。回退优先停用插件并保留当前核心读取能力、会话、分支、节点和账本；旧宿主不能枚举新分支目录，降级前必须独立验证。

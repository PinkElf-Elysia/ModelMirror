# R3 输出上限对照准备（2026-10-11）

用户确认上一会话生成、质量及格式正常，要求提高输出上限，全部生成仍由用户手动发送。新增2次授权，本卡总额7、已计费授权次数5、剩余2；物理尝试7，既有明确豁免2。控制面上限100与本卡授权独立。

## 上一轮证据

会话760cc57c-6a47-4bc1-85ba-f8ed4fce0868：slot-6剧情、slot-7记忆均complete。剧情completion_tokens=5817，finish=stop，未达到16384上限；提高上限并不保证宏观篇幅变长。剧情原文hash a01172da8cb2695d518b9bf60132919e9b4e58a00628f9c4cea87c172bdc40ca；记忆原文hash ff5f8528651b1eb812c2dc5c74a1f6c1bba0044863e24ce55f4f13053ea86c24。用户对剧情质量和渲染认可不外推为独立记忆忠实性验收。

## 本批参数和入口

God专用受控profile=god-long-v1：Gemini temperature=0.70、top_p=0.80、max_tokens=32768。剧情及记忆整理共用该配置。默认profile、地球OL和Luna例外保持16384；作者正文、角色设置、世界书及展示未改。目录兼容性按32768检查，任意profile和参数不接受。配置进入选择版本与运行版本绑定，不进入模型消息。

新会话：http://127.0.0.1:18512/rpg-app/god/?session=f63f9b55-eb2e-4e77-848e-fe35240ec7d3 。同一B4角色，新会话，无旧历史迁移。记忆宫殿明确启用且模型Gemini，其他记忆策略关闭；单次主动发送最多2调用（剧情+记忆）。原760会话完整保留，本宿主视旧运行版本为只读。Agent本批派发0次，无自动重试。

## 验证

- 断网临时容器 pytest server/tests/test_rpg_bridge.py：30通过；MockTransport验证32768实际请求、原消息不变、默认16384、参数版本隔离、容量不支持拒绝。
- node --test god-card/tests/real-host.test.mjs studio/controlled-provider.test.mjs：24通过。
- node --test studio/memory-tasks.test.mjs studio/budget-exemption.test.mjs god-card/tests/*.test.mjs：82通过（包含上一步God两项，非独立累计数）。
- UI正式浏览器观察：同角色首轮草稿、剩余2、最多2调用、发送按钮可用。未点击发送。
- 服务端落盘和会话接口：剧情绑定32768，记忆绑定32768，已启用，无生成历史。
- 本批无UI源文件或资源变更，复用原已验收r3-quota/dist；生产重建不适用。
- git diff --check默认对历史CRLF大量告警；仅本批三个已跟踪源文件在core.whitespace=cr-at-eol下通过；未批量改写其他改动。

## 环境与回退

共享modelmirror-server仅替换rpg_bridge.py挂载，保留现有镜像、路由绑定和目录优化、凭据环境（只在内存传递）、100上限、DNS覆盖、网络、卷。旧容器modelmirror-server-rpg-length-backup-20261011停止保留；更早quota回退容器亦保留。第一次部署安全检查发现Docker将Windows源路径规范为/run/desktop/mnt/host，拒绝发生于变更前，修正精确目标后成功。GET /api/health=ok。

新挂载位于.rpg04-work/god-card/r3-length/bridge-runtime.py；以后Compose重建必须显式保留或从已合并代码构建，不能把当前挂载视为仓库正式部署。回退需确认无在途任务，恢复原bridge挂载/容器及旧God源码快照；账本、条目、会话、两份豁免和新增授权均保留，不能回退额度历史。32768会话不在旧宿主上续玩。

预览PID42512，18511/18512；停止前仍需重新核对归属。没有Commit、Push或PR。共享容器发生了上述定点配置部署，无其他服务更新。

# RPG 通用声线

## 本轮契约

- 基线：`8f159258c5ddab11f5b9c33950e21d535b4c861f`，分支 `codex/rpg-voice-library`；开始时工作树干净。
- 目标：所有复用公共聊天界面的卡片与新旧会话，显示第一个默认通用声线“爱莉希雅音色”；个人设置优先，模型和额度保持统一控制面治理。
- 范围：公共 TTS 宿主、音色选择 UI、回归、帮助文章和无敏感信息截图。不改作者提示词、存档、父仓控制面、凭据、配额、共享栈。
- 批次：五个运行/测试文件完成并通过后，再同步帮助及本说明；两组属于同一跨会话默认声线目标。
- 风险：中，涉及共享配置读取及克隆声线授权范围。无数据库迁移，不复制云端密钥、原始语料或云端声线 ID 到源码。
- 用户已授权收尾并提交后续 PR；原 PR #470 已合并。禁止合并、共享栈部署和新增真实计费调用。

- 发布基线：`ae67eb25`（最新 main），独立分支 `codex/rpg-common-voices`。

## 服务端绑定

在受信任的 RPG 数据目录中添加 `speech/common-voices.json`：

```json
{
  "version": 1,
  "voices": [
    {
      "id": "elysia",
      "assetId": "voice_<当前控制面返回的32位资产ID>",
      "model": "<该资产绑定的模型ID>"
    }
  ]
}
```

这是部署级数据，不提交 Git，不是浏览器配置。`assetId` 使用控制面本地音色资产 ID，不能填写云端 voice ID。先用控制面登记并查询就绪的合法音色，再由部署管理员配置映射；本轮使用此前已授权创建的现有音色，无需新建云端资产。只影响连接同一控制面租户的 RPG 实例，不代表跨账号授权。

公共目录只含稳定名称和槽位，不硬编码真实模型、账号、资产编号或额度。宿主每次读取目录重新核对资产 ID、精确模型、ready 状态、当前连接、模型许可与额度。未配置、配置损坏、资产删除或控制面不可达时保留槽位并禁用，不能按名称猜测或换用私人音色。

`speech/models` 增加只读 `commonVoices` 投影；原有调用方可忽略它。只启用 TTS 的会话只能获取和使用配置的公共克隆声线；其他私人克隆声线仍需音色库插件授权。生成前及提交时重复检查公共许可，最终仍调用原 `/api/multimodal/speech`，沿用配额扣减与幂等恢复。打开和刷新目录只读取本地控制面，不创建、查询云端或合成。

所有复用 `createSpeech` 的卡片使用同一配置。历史音频、角色绑定、个人浏览器选择仍按原会话隔离；未修改存档指纹或升级兼容白名单。UI 将通用声线置顶，无个人设置时优先选中可用默认值，允许手动输入试听文字。不会自动生成或发送剧情。

## 验收

- `node --test experiments/ai-rpg-engine/studio/common-voices.test.mjs experiments/ai-rpg-engine/studio/voice-library.test.mjs experiments/ai-rpg-engine/studio/speech.test.mjs experiments/ai-rpg-engine/studio/speech-compatibility.test.mjs`：14 项通过。
- `npm.cmd --prefix experiments/ai-rpg-engine/card-replica run build`：通过，保留既有 mod-library 动静态导入提示。
- 浏览器：两个无密钥会话验证默认、个人设置保存刷新、会话隔离；390px 无横向滚动。真实旧会话额度为 6/6 时入口仍显示且生成禁用。
- `npm.cmd --prefix experiments/ai-rpg-engine/card-replica test`：86 项通过。
- 新增真实调用：0；没有重建或重新查询云端声线。真实声线创建/试听沿用上一轮已完成证据，本轮不宣称重新完成端到端合成。

## 回退

移除或恢复本轮 `speech/common-voices.json` 即禁用公共绑定，不删除资产或历史音频。代码可恢复到基线版本；原有会话固定音色与角色绑定仍采用原格式，不需迁移。

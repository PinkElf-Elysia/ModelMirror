export type PluginBinding={version:string;artifactSha256:string;manifestSha256:string;installationId:string};
export type PluginVersion={id:string;name:string;version:string;installed:boolean;compatibleInstallation?:boolean;updateAvailable?:boolean;installedVersion?:string|null;installedBinding?:PluginBinding|null;versionHistory?:{action:string;from:PluginBinding|null;to:PluginBinding|null;at:string}[];artifactSha256:string;manifestSha256:string;permissions:string[];source?:string;license?:string};
const reasons:Record<string,string>={
 PLUGIN_UPGRADE_REQUIRED:'已安装版本与当前版本不一致，请前往插件市场升级。原有开关和配置保留。',
 PLUGIN_VERSION_MISMATCH:'插件版本已变化，请重新读取版本后操作。',
 PLUGIN_REGISTRY_CONFLICT:'插件状态已被其他页面更新，请重新读取后操作。',
 PLUGIN_SESSION_CONFLICT:'会话状态已变化，请重新读取后操作。',
 PLUGIN_INSTALLATION_CONFLICT:'已安装版本已变化，请重新读取后确认升级。',
 PLUGIN_NOT_INSTALLED:'尚未安装此版本，请前往插件市场安装或升级。',
 PLUGIN_ALREADY_INSTALLED:'插件已经安装，请重新读取状态；有新版时选择升级。',
 PLUGIN_ALREADY_CURRENT:'已经是当前版本，请重新读取状态。',
 PLUGIN_PERMISSION_MISMATCH:'权限要求已变化，请重新读取并核对升级权限。',
 PLUGIN_UPGRADE_BUSY:'仍有会话正在生成或等待核对，请结束或核对记录后再升级。',
 PLUGIN_SESSION_BUSY:'当前会话正在生成或等待核对，请结束或核对记录后再操作。',
 PLUGIN_UPGRADE_SESSION_UNAVAILABLE:'暂时无法读取已有会话，升级未进行；请恢复会话服务后重试。',
 PLUGIN_PERSIST_FAILED:'插件状态保存失败，请重新读取状态后重试。',
 PLUGIN_PACKAGE_UNAVAILABLE:'当前插件包不可用，请检查部署版本。',
 PLUGIN_STORE_INVALID:'插件配置无法读取，请保留数据并检查服务。',
 PLUGIN_NOT_AUTHORIZED:'当前会话尚未授权此插件，请重新读取并启用。',
 ORIGIN_REJECTED:'请求来源校验失败，请从同一站点重新打开插件设置。'
};
export function pluginFailure(error:unknown){const code=error instanceof Error?error.message:String(error);return reasons[code]?reasons[code]+'（'+code+'）':'未收到完整的操作结果，请重新读取状态核对；不会自动重试。';}
export function pluginBinding(p:PluginVersion,action:string){const b=action==='uninstall'&&p.installedBinding?p.installedBinding:p;return {version:b.version,artifactSha256:b.artifactSha256,manifestSha256:b.manifestSha256};}
export function needsPluginUpgrade(p:PluginVersion){return p.installed&&(p.updateAvailable===true||p.compatibleInstallation===false);}

const permissionLabels:Record<string,string>={
 'session.completed.read':'读取已完成的聊天记录','session.message.fork':'编辑输入与重新生成','session.message.edit':'编辑已保存的回复','ui.contribute':'提供聊天界面操作',
 'model.catalog.read':'读取可选模型','session.model.select':'切换会话模型','session.branch.prepare':'创建故事分支','session.history.configure':'设置历史窗口',
 'session.summary.configure':'设置自动总结','session.summary.revise':'编辑总结','session.summary.request':'申请总结任务','session.memory.configure':'设置记忆宫殿',
 'session.memory.revise':'编辑记忆','session.memory.request':'申请记忆任务','context.memory.contribute':'将记忆加入上下文','session.transcript.import':'导入聊天记录',
 'session.illustration.generate':'申请生成场景插画','session.illustration.rewrite':'申请改写插画描述','model.parameters.read':'读取生成参数','session.parameters.configure':'设置生成参数','session.comparison.generate':'申请对比生成'
};
export const pluginPermissionLabel=(permission:string)=>permissionLabels[permission]||permission;

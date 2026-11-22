# 崇祯元年七月 · 全国舆图

在独立古代卡预览里发送崇祯命书，点「展开全国舆图」。地图首次打开为全国总览，选区可看治所或参照府，点「定位辽东」或选择城镇可看京师登记。地图支持拖动、缩放按钮、键盘选择地点，以及 Escape 返回原对话。浏览地图不会发送聊天。已有开局的存档链接可加 `&atlas=1` 直接开图。

区划填色用于分辨相邻行政区。点状或菱形城镇标记显示本档登记守方；虚线概示区界与辽东军事地理。军报送达仅改变京师所见，确认交割仅改变宁远守方。读档沿用同一份事件、送达和存档版本。地图几何按需本地加载。

## 地理来源与再使用

图形取自 Furfur 的 [China Ming-Dynastie zh-Hans.svg](https://commons.wikimedia.org/wiki/File:China_Ming-Dynastie_zh-Hans.svg)，2025-06-02 版本，SHA-256 `3a3eaa5e80e8ca989f7832212d45f087c379cdf7f7f169d2f08abdef06ce2d73`。作品说明注明以 Jason22 的 [Ming Dynasty Administrative division.jpg](https://commons.wikimedia.org/wiki/File:Ming_Dynasty_Administrative_division.jpg) 为来源（原图标注 [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/)）。本作选择矢量版的 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 许可；派生的 `ming-atlas-geometry.json` 以同一许可提供。更改包括提取路径、重组陆地、去除原文和政权设色、重设标签及配色。游戏界面提供作者与许可入口。外部底图的版权与许可独立于程序代码。

[美国国会图书馆《天地图卷》](https://www.loc.gov/item/2002626722/) 编目作 1621–1639 年，涉及十五区、府治与周边区域，用于核对图示年代及层级。采用的矢量底图是明代概图，**没有逐段实测到 1628 年**。以该概图绘出两京十三布政使司的近似省级轮廓；各府县界、边地推进和现代省界不能据此推断。原图中带有其他时期特征的交州、奴儿干等政区仅取陆地轮廓，未标为本档明廷控制地。川黔在 1601 年改置后，遵义府隶四川、平越府隶贵州，区划详情注明分置，未取得能定位两府精确分界的几何。[播州分置记载](https://www.gzxw.gov.cn/wmxf/23765)

| 区划 | ID | 治所或参照府 | 原图路径 |
| --- | --- | --- | --- |
| 北直隶 | bei-zhili | 顺天府 | path87 |
| 南直隶 | nan-zhili | 应天府 | path83 |
| 山东 | shandong | 济南府 | path82 |
| 山西 | shanxi | 太原府 | path91 |
| 河南 | henan | 开封府 | path88 |
| 陕西 | shaanxi | 西安府 | path98 |
| 浙江 | zhejiang | 杭州府 | path76 |
| 江西 | jiangxi | 南昌府 | path84 |
| 湖广 | huguang | 武昌府 | path93 |
| 福建 | fujian | 福州府 | path80 |
| 广东 | guangdong | 广州府 | path89、path90（海南） |
| 广西 | guangxi | 桂林府 | path100 |
| 四川 | sichuan | 成都府 | path97 |
| 贵州 | guizhou | 贵阳府 | path94 |
| 云南 | yunnan | 云南府 | path101 |

十五区均以原图同名标签在形状内的命中关系核对。北直隶与山东、南直隶与浙江等沿各自矢量轮廓分别选取。辽东军事范围取 path78，仅描虚线；其边线不随城镇移交改变。海岸沿同一原图提取，避免不同投影的两张底图错位。四城在原图坐标系定位：北京采用原图顺天府标记，以北京、沈阳和济南为参照，对山海关、宁远和锦州作局部仿射估位。四点连线示意驿路顺序。未细分归属的小岛作自然地理背景。

## 实现与验收

- 基线为 #516 合并提交 `32563f6a`。宿主只读投影为已送达事件和四城附加 `placeId`。旧存档按现有地名解析；无法定位的旧消息仍在会话记录中。测试使用独立工作树和独立数据，既有模型工具合同及存档格式沿用首轮。
- 在 `experiments/ai-rpg-engine` 执行 `node --test ancient-card/frontier/session.test.mjs ancient-card/routes.test.mjs ancient-card/real-host.test.mjs`。另在 `card-replica` 执行 `npm run build`，按[预览运行说明](README.md)启动本地服务。
- 浏览器脚本 `browser-check.mjs` 使用原卡命书走完十回合。默认读档续写后才首次展开地图。设 `ATLAS_CAPTURE_DIR` 可在开局、关报到京时开图，验证缩放拖动、地点选择、草稿、只读版本、交割后读档及手机布局，保存四组截图。两种模式均使用离线叙事。
- `atlas-replay-check.mjs` 使用 `ATLAS_SAVED_URL` 指向已验收存档的本机副本，拒绝非本机请求和页面写操作。第一部分核对既有 Gemini 正文、四地守方和第八刻确认依据；第二部分在内存中替换为明确独立的山海关离线样例，验证该消息仅在山海关出现。它不会重新调用供应商或更新原档。
- 探索线独立核查十五区、川黔分置、京师获报视角，并在实际预览中复看全国与辽东。其反馈已体现在两地详情、跨区定位以及守方旁的交割依据。角色卡和具名人物消息接口另循原路线。

## 周边形势与城镇

周边形势提供全国及东北、西北局部。在已开局的独立预览地址附加 `&atlas=1&extent=neighbors`、`extent=northeast` 或 `extent=northwest` 可分别直达。两京十三省、城镇点位与存档守方均可单独查阅。新增地理参照包含叶赫、乌拉、皮岛、嫩江、西辽河、阿克苏、察力失、吐鲁番、塔尔巴哈台与哈密。陆地之外的海岛以点示位置。

## 1628 年接界与证据精度

本版登记 42 个政权、诸部地域及海上据点。范围随开局固定，城镇守方随存档交割。大色块可点选，侧栏列分类、范围说明和来源。斜纹表示概绘活动地或地方诸势力合示，留白表示本图未能落实归属。并未逐社、逐旗或逐村复原七月边界。

- [Cliopatria v0.2.1 / Seshat Global History Databank](https://github.com/Seshat-Global-History-Databank/cliopatria/tree/v0.2.1) 提供跨年政权轮廓，按 FromYear ≤ 1628 ≤ ToYear 筛选，使用 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。每条要素的原始名称与改绘见生成脚本。岛陆参照为 [Natural Earth 1:50m Land](https://www.naturalearthdata.com/downloads/50m-physical-vectors/50m-land/) 公共领域数据，版本 `ca96624a56bd078437bca8184e78163e5039ad19`。两张用户截图用于读图层级对照。
- **制图**。先合并候选轮廓的全部边线并切成不重叠的面，再给每个面确定归属；整套分区一次性简化和投影，邻区共用折点。河谷、荒漠及部众活动带只能约定概略分线。小政权与异属据点有单列依据时优先保留。
- **复算**。在开发机安装了 Shapely 2.1 的 Python 环境中，将发行压缩包与上述 `ne50-land.geojson` 放入同一目录，调用 `python build_atlas_ranges.py <cliopatria-v021.zip> <ming-atlas-ranges.json>`。生成时检查整体拓扑、明金朝鲜、东海诸部、漠南漠北、西域绿洲和郑阮等相邻面的共边，并核对叶赫、乌拉、宁锦、嫩江、察力失等点归属。浏览器回放另检查最终 SVG 和只读存档版本。

| 地区 | 判读与改绘依据 | 边界精度 |
| --- | --- | --- |
| 辽西与后金 | 1626–1628 原始记录将宁远、锦州画入后金。按[《袁崇焕与宁远》](https://iqh.ruc.edu.cn/old/qszx/qsjt/73ca5e1df33e498ab0767fc08963e947.htm)恢复山海关至锦州沿海走廊。按[故宫努尔哈赤年表](https://www.dpm.org.cn/court/lineage/226250.html)将乌拉、叶赫旧地纳入后金；皮岛单标明军东江镇。 | 锚点、占领顺序可核；城间控制线为地带估绘。 |
| 东北北部与东部 | 科尔沁的盟地同后金分色。嫩江、黑龙江中上游诸部及东海女真分别列为地域，虎尔哈、瓦尔喀只作群体与流域说明。参见[《东北亚历史地图集》](https://doi.org/10.7312/columbia/9780231160704.003.0002)及[1635年瓦尔喀研究](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?artiId=ART002336042)。 | 嫩江以东、西辽河以北有过渡，图中分线为查阅示意，不能读取到部落驻地或逐屯隶属。 |
| 草原 | 漠南区分察哈尔、科尔沁、喀喇沁、鄂尔多斯、内喀尔喀及其余诸部。敖汉、奈曼归附与巴林、扎鲁特关系变化按[故宫研究](https://www.dpm.org.cn/Uploads/pdf/1613/T00030_00.pdf)区分，不把1628全年变化统一提前到七月；漠北喀尔喀、漠西卫拉特与北缘山林地区分别登记。原始“Golden Horde”标签与1628年不合，只用其与邻面的几何外缘，依据地域重分内部。参见[《中亚文明史》第五卷](https://unesdoc.unesco.org/ark:/48223/pf0000130205)。 | 以戈壁、河谷约分，不将林丹汗的名义汗权扩为实际领地；北缘和青海未划到每部。 |
| 天山与塔里木盆地 | [Bregel《中亚历史地图集》图35](https://turkistanilibrary.com/content/historical-atlas-central-asia)将十七世纪初叶尔羌和吐鲁番两廷分述；[《中亚文明史》](https://unesdoc.unesco.org/ark:/48223/pf0000130205)记述察力失、吐鲁番的地方权力。喀什、叶尔羌、和阗、阿克苏在西，察力失、吐鲁番另列。哈萨克、布哈拉、卫拉特、西天山诸部、青海蒙古与哈密以东的过渡区各列。 | 绿洲比沙漠线可靠。哈密及东天山内部待细分，1630年代的统一及之后准噶尔疆域不前移。 |
| 藏地与喜马拉雅 | 拉达克、古格分列。1630年前后的征服晚于开局，参见[牛津藏学研究年表](https://ora.ox.ac.uk/objects/uuid%3Afcc23944-02df-4e39-8273-059e2bbde318/files/ma3e18098982e6f3757904283f9f852f0)。乌斯藏、康区等藏地的合色只表示地理参照。 | 高原内部诸政权未完全细分，不能据合色认定均受日喀则直接管辖。 |
| 南方与海岛 | 高平莫氏在郑、阮以外单列，另显示澜沧、暹罗、若开、占城等。琉球单列；虾夷地阿伊努各地与道南松前据点区分。台湾原住民各社为独立图面，荷、西仅沿海据点。澳门标注[葡商居留地](https://www.icm.gov.mo/rc/viewer/pdfViewerParts/10049/2725)。 | 有点据而无外围依据时以点表示。未考订所有土司、山地小邦及岛内各社疆界。 |

东北、西北局部显示 [Natural Earth 1:10m 河流](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-rivers-lake-centerlines/) 的简化河线，与陆地图使用相同版本及投影。河道只帮助定位，历史河道摆动和改道未专门重建。

**覆盖边界。** 清单由地图范围内的历史记录和查证材料建立。政权、盟部、居留地、活动地域分别登记：敖汉和奈曼保留在内喀尔喀的范围说明中，台湾列岛内诸社，藏地和喜马拉雅南麓的概括色块允许多个地方政权并存。现阶段未对不丹东西部、阿萨姆诸邦、掸邦诸土司、漠北各汗部和黑龙江各聚落逐一划界；这些区域的合色或留白不能作为任何相邻政权的占领依据。河道和地理轮廓不能直接产生游戏事件。


## 地点交割 · 台湾离线存档

启用 `ANCIENT_PLACE_REGISTER=1` 后启动 `ancient-card/frontier/server.mjs`，端口使用 `FRONTIER_PORT`，数据在独立的 `.rpg03-work/ancient-place-register`。从古代卡崇祯命书进入，输入“朕下旨经略东番，命郑芝龙赴大员办理接收”，签发后可推进至本地样例捷报抵京。查询或拟案不改守方。核准交割后，当前大员据点、京师的城防登记和下一轮查询共同使用这条交割事件；开局舆图仍可查阅。鸡笼与岛内各社独立登记。

宁远与大员在此模式共用同一套按地签发、送达、凭据查询、拟案和确认流程。存档记录事发和抵京刻次，以及旨意、接收文书、交割事件之间的引用。输入“开局时台湾各地归谁”和“台湾现在谁管”可对读两种时点。离线叙事只覆盖有限措辞，示例文书由宿主生成；历法、都督府建置、财计和整岛控制未进入本次结算。此前预览与旧存档仍走各自模式。

离线验证：`node --test ancient-card/frontier/place-register.test.mjs`。预览运行后，以相同端口和模式运行 `node ancient-card/frontier/browser-check.mjs`，从命书走至交割、重开会话、自由追问和首次开图。全程使用本机模拟供应商。

## 验收后下一项 · 聊天查舆图

地图视图已通过玩家验收。独立聊天预览可用 `ANCIENT_ATLAS_QUERIES=1` 开启，另设空闲的 `FRONTIER_PORT`，存档目录为 `.rpg03-work/ancient-atlas-chat`。新建命书后可直接问「请比较吐鲁番与叶尔羌」或「科尔沁与后金有什么关系」。工具 `query_atlas` 每次接受一至三个片区名称，从地图同一份数据读取名称、类型、说明和来源；这些资料的时点固定为开局。追问宁远最新守方或关报走原有 `query_map`。查询不改变地图，变更仍须原有凭据和确认。原预览和旧档沿用原工具表。此轮仅验证离线工具选择与续答。

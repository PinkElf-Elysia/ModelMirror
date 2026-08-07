# `@matrix-oasis/game-pack-contracts`

R1 的私有、案例无关 Authoring Game Pack 合同包。JSON Schema 2020-12 文件是唯一结构真源；JavaScript 入口只导出冻结的 Schema 与格式常量。

本包不解析文件、不验证引用或图、不执行条件和效果，也不包含样例、资产、网络、Godot 或父仓适配代码。

```js
import {
  AUTHORING_GAME_PACK_FORMAT,
  AUTHORING_GAME_PACK_SCHEMA,
  AUTHORING_GAME_PACK_VERSION,
} from "@matrix-oasis/game-pack-contracts";
```

格式版本 `0.1.0` 只接受精确匹配；未来版本必须通过显式版本迁移设计，不做隐式兼容。

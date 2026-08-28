# Local Deep Research 镜像许可证处置

## 结论

Local Deep Research v1.10.6 的项目源码使用 MIT 许可证。固定的官方容器镜像
`localdeepresearch/local-deep-research:1.10.6@sha256:b2c634291de8fb8d0662ab81a0b82ec17ab807109d20d57386042c5bdcd472e5`
还包含 Debian、Python、npm 和其他运行时包，因此不能把整个镜像声明为 MIT。

V0.1 采用 `external_pull_only`：Compose 只能从上游公共仓库按上述精确
digest 拉取未修改镜像。模镜不构建、复制、镜像化、离线捆绑、修改或发布该
镜像。内部运行必须随可选模块保留第三方 notice。

这是一项工程分发门禁，不是对全部第三方许可证义务的法律意见。

## SBOM 证据

来源为上游 v1.10.6 发布附件
`sbom-container-amd64.spdx.json`，SHA-256 为
`6f9c0e6f762763d2b34207a7638b65bedd37d818bd86e538483b21cb091c6315`，
大小 5,245,009 字节。

| 项目 | 数量 | 解释 |
| --- | ---: | --- |
| 全部包 | 438 | PyPI 282、Debian 134、npm 5、generic 2、OCI 1、未分类 14 |
| declared license 已知 | 378 | SBOM 提供了许可证声明 |
| declared license 为 `NOASSERTION` | 60 | 不能仅由 declared 字段确认许可证 |
| concluded license 为 `NOASSERTION` | 416 | SBOM 生成方没有作出结论，不等于 416 个许可证未知 |
| declared 已知、concluded 未断言 | 378 | 可继续依据 declared 字段审计 |
| declared 未断言、concluded 已知 | 22 | concluded 字段补足了声明 |
| 两者均未断言 | 38 | 当前有效未知项，阻断镜像再分发候选 |
| declared 表达式含 GPL/LGPL | 100 | Debian 98、PyPI 2；需要保留各包对应义务 |
| declared 表达式含 AGPL | 0 | 仅表示 SBOM 明示字段未匹配，不能消除未知项风险 |

两个 PyPI declared copyleft 项为 `autocommand@2.2.2`
(`LicenseRef-LGPLv3`) 与 `tld@0.13.2`
(`MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later`)。`pyphen@0.18.1`
的 declared 与 concluded 字段均未断言，计入当前有效未知项。

## 分发矩阵

| 行为 | 决策 | 门禁 |
| --- | --- | --- |
| 从上游公共仓库按精确 digest 拉取原始镜像 | 允许 | 镜像名、tag、digest、SBOM 哈希和 Compose 形态必须全部匹配 |
| 内部托管运行已拉取的原始镜像 | 有条件允许 | 随模块保留 notice，不得声称整个镜像为 MIT |
| 推送到模镜或私有镜像仓库 | 阻断 | 需要完成逐包义务与有效未知项处置 |
| 制作离线安装包或预装镜像 | 阻断 | 需要完成逐包义务、许可证文本与源代码提供要求 |
| 修改或派生 LDR 镜像后分发 | 阻断 | 需要重新生成 SBOM 并完成完整许可证复核 |
| 把整个镜像声明为 MIT | 禁止 | 项目许可证不能覆盖聚合镜像内的第三方组件 |

## 自动门禁

`scripts/validate_boundary.py --distribution-mode external-pull` 只验证当前允许
路径，并拒绝 LDR `build:`、非上游镜像名、浮动 tag、错误 digest、私有或模镜
registry、Dockerfile `FROM`/`COPY` LDR 镜像以及离线捆绑痕迹。

`--distribution-mode redistributable-bundle` 是保留的失败门禁。在完成 38 个有效
未知项、copyleft 义务、许可证文本和源代码提供方式的逐包处置前，它必须失败。

升级 LDR 版本、digest 或 SBOM 时必须重新生成本文件中的证据与决策，不能沿用
旧版本结论。

YC 定制复刻打包（长期保存用）

目录目的：把复刻所需的一切信息收拢在一个可长期保存的目录/压缩包里，避免上游同步或仓库删除后丢失关键信息。

包含内容：
- YcCustomBuild.md（完整复刻说明，会在打包时复制一份到本目录）
- FILES.txt（涉及的关键文件清单）
- CONSTANTS.env（固定值/默认值汇总）
- VERSION.txt（版本与回滚信息）
- RG_CHECKS.sh（ripgrep 锚点校验清单）
- pack.sh（一键复制 YcCustomBuild.md 并打包为 tar.gz）

使用方式：
1) 打包归档（会自动复制上一级 docs/YcCustomBuild.md 到当前目录）：
   ./pack.sh

2) 归档产物：
   yc-pack-YYYYMMDD.tar.gz（离线保存即可）

3) 复刻步骤：
   - 解压归档 → 打开本目录内的 YcCustomBuild.md 按顺序执行（或直接把该文件发给工程师复刻）。



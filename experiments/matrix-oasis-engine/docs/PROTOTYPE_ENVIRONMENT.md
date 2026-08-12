# Prototype Environment Bundle 0.1.0

R10私有环境Bundle绑定Scene Blueprint身份、`marble-1.1`环境提示SHA、panorama PNG与collider GLB的相对路径、字节数、SHA-256和离线指标。

Bundle不进入Runtime Pack、Scene Pack或存档格式，不保存原始prompt、operation/world ID、下载URL、密钥或原始响应。panorama仅作为360°天空，没有视差；collider只提供原型级行走碰撞，不宣称与全景像素严格配准。

固定上限为panorama 64 MiB、16384×8192且2:1，collider 32 MiB、provider JSON 1 MiB。SPZ、data URI、redirect、私网/loopback/IP字面量下载、符号链接和模块外路径均拒绝。

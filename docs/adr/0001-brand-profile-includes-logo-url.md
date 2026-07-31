# brand_profile 槽位扩充 logo_url，不新建通用 asset 库

租户品牌定制（brand_profile）原本只含四个槽位（primary_color / radius_preset / background_preset / hide_yimatong_brand），Logo 缺一条完整链路：tenant 表无字段、brand_profile 白名单不允许、H5 接口留了 logo_url 却无人注入与渲染。规范文档（docs/02_tech/design-system/h5-branding.md）写「Logo 走资产库 asset_id」，但代码里不存在通用租户级 asset 库（只有 ProductAsset）。

决定：把 logo_url 作为第五个受控槽位并入 brand_profile，值复用现有 `POST /files/upload`（MinIO 存储）返回的 public_url；同步扩展后端 `validate_brand_profile` 白名单、resolver 注入与 H5 渲染。不新建独立 asset 库 / 不引入 logo_asset_id 外键。

理由：现有 /files/upload + ImageUploadInput 已是成熟上传链路，零新增基础设施即可闭环；新建 asset 库要加模型、迁移、CRUD 与权限，工作量与 kl9k「补齐租户自助品牌配置」的目标不匹配。Logo 是图片资产而非可派生值，不能像主色那样算法派生，所以必须有存储槽位——并入 brand_profile 是最小且与现有四槽位一致的改动。

后果：brand_profile 从「四槽位」变为「五槽位」，CONTEXT.md 与 h5-branding.md 的事实描述需同步。若将来引入统一资产库，logo_url 可平滑迁移为 asset_id 引用，槽位语义不变。

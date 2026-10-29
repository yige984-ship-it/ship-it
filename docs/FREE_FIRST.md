# 免费优先接入

## 当前默认

默认选择硅基流动中国站Kwai-Kolors/Kolors，2026-10-29官方价格页标为免费。
免费API同样需要服务器SILICONFLOW_API_KEY。用户不需要先给Atlas充值。
配置目录按人民币单张估价从低到高展示：

1. Kolors：0元/张，免费优先，1024×1024试图。
2. 阿里云北京wanx2.0-t2i-turbo：0.04元/张，768×1344，单次n=1，关闭提示词改写。
3. 硅基流动Z-Image-Turbo：0.10元/张，576×1024。
4. Atlas：人民币价格未核实，显示在已知价格之后，默认不可用。

不是同模型同分辨率的质量比较，也不是免费额度或可用性保证。
价格是2026-10-29官方页面快照，可在config/image-providers.json维护；实际收费以供应商后台为准。
免费项提交前会再检查官方页面是否仍标为免费，无法确认即拒绝，不会自动调用收费模型。
免费状态不能抵扣服务器、存储和流量费用。

## 实际执行边界

- 适配器代码、页面选择、报价确认、模型列表预检均已实现。
- 已联网核对官方价格页，未提交任何模型生成请求。
- 未配置真实账号密钥；无法完成账户模型列表查询和真实单图验收。
- 硅基流动免费接口模型文档存在变动：能力指南示例包含Kolors，而通用OpenAPI枚举主要列出其他模型；
  因此实现只使用基础model/prompt/image_size，提交前必须查询当前账户/v1/models包含所选模型，否则停止。
- 免费模型不可用或限流时停止，绝不自动切换到收费候选。收费开关仍默认false。
- 免费试生成也需要用户确认向外部服务发送提示词和角色设定。
- 同步硅基图片响应直接记录completed_real_image；阿里/Atlas异步响应保存任务ID再查询。
- POST不重试。超时标记submission_unknown，禁止重复提交；GET预检失败尚未生成，可重新核实。
- 免费模型不是Mock；无密钥时显示不可用，不能显示“已生成”。

## 你需要做的事（仅iPhone即可）

1. 打开https://cloud.siliconflow.cn/，注册/登录中国站账号，完成平台要求的账号验证。
2. 在模型广场查看Kwai-Kolors/Kolors是否仍为免费、是否对你的账户开放。
3. 在API密钥页面创建密钥，自己保管，不发到聊天或仓库。免费测试先不充值、不购买套餐。
4. 后续在运行应用的平台Secrets中填写SILICONFLOW_API_KEY；开启ENABLE_FREE_IMAGES=true，保持ENABLE_PAID_IMAGES=false。
5. 当前尚无已验收公网应用，服务器密钥安全配置和部署需要在平台管理界面完成；不能把密钥填到前端网页。

目前不需要注册阿里云、Atlas或购买GPU。只有免费效果/配额不足且你确认费用时才考虑收费候选。

## 维护与限制

- IMAGE_PROVIDER_FILE为旧Atlas单配置覆盖入口；新部署留空，使用目录默认免费项。
- GET /api/image-providers返回按已知价格排序的选项，automatic_paid_fallback恒为false。
- GET /api/image-provider?provider_id=kolors-free查看特定项状态。
- POST /api/projects/{id}/image-quotes可传provider_id；默认无旧覆盖时为kolors-free。
- 单张确认max_cost_cny允许0。免费也计入MAX_IMAGE_JOBS任务数量额度。
- 所有供应商结果沿用本地图片保存和5秒关键帧预览入口；CDN域名仍需管理员白名单配置。
- 当前为单进程本地MVP。未实现登录/租户隔离，不应直接将付费接口暴露公网。
- 需要人工验证各账号实际可用性、速率、商业使用条件和国风画质；没有声称完成真实端到端验收。

## 官方来源（2026-10-29读取）

- https://www.siliconflow.cn/pricing
- https://docs.siliconflow.com/cn/userguide/capabilities/images.md
- https://docs.siliconflow.com/cn/api-reference/images/images-generations.md
- https://docs.siliconflow.com/cn/api-reference/models/get-model-list
- https://help.aliyun.com/zh/model-studio/model-pricing
- https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference

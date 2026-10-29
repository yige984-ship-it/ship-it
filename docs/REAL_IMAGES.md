# 真实文生图接入

此版本提供Atlas Cloud真实文生图HTTP适配器与逐镜图片任务界面。
实现完成不等于已完成真实生成：2026-10-29验证时服务器无密钥，未执行收费生成。
视频/配音仍为模拟，图片不会自动拼入模拟视频，后续图生视频阶段才接通素材链。

## 官方接口核实

2026-10-29读取 `GET https://api.atlascloud.ai/api/v1/models`，确认配置模型为公开可用项。
读取配置中schema_url核实 model、prompt、size、output_format。
该模型使用size字段（非image_size）；本实现固定竖屏1600*2848、PNG。
模型schema中的path为通用生成路径，提交和查询采用官方集成指南的
`/api/v1/model/generateImage`和`/api/v1/model/prediction/{id}`；实际带凭证联调仍待验证。
供应商可能变更模型或参数，上线前应重新核实，不应任意修改模型ID继续沿用旧参数。

## 启用条件（服务器）

1. 保持默认ENABLE_PAID_IMAGES=false直至部署与认证完成。
2. 服务器环境配置ATLASCLOUD_API_KEY。不要在聊天、GitHub或浏览器输入密钥。
3. 在config/image-provider.json填写price_per_image_cny、price_source、price_verified_at。
   单价为人民币规划预留，需自行核实供应商计价、汇率和适用分辨率；默认null会阻止提交。
4. 配置MAX_IMAGE_JOBS总额度，部署登录和访问控制后开启ENABLE_PAID_IMAGES=true。
   当前单用户本地应用不含认证，不能将启用收费功能的接口直接暴露公网。
5. 创建/保存方案，在每镜点击获取图片报价，审阅后确认单张收费。无需安装手机软件。

## 收费与状态

- 报价仅保存本地记录，不调用供应商。报价锁定模型、参数、提示词、角色和方案版本，10分钟有效。
- 确认时再次检查密钥、开关、配置一致性、方案版本、预算和用户确认金额。
- 预算采用保守方式：方案估价 + 所有历史图片预留 + 本次预留，不超过项目/服务器/150元上限。
- 所有报价都占用预留，包括取消、过期和失败；当前不自动退款/释放，也不声称等于真实账单。
- POST提交绝不自动重试。先持久化submitting，再执行网络调用。
- 超时/异常标记submission_unknown，可能已计费，禁止重复提交。进程在提交时崩溃可能保留submitting；
  需要管理员核对供应商账单和任务，不能直接重置后重新发送。
- 返回prediction_id后保存processing，点击刷新仅GET查询，不重新收费提交。
- 完成状态为completed_real_image，与completed_mock区分；实际费用为null待账单核对，不能标记为0。
- 图片使用供应商HTTPS临时URL直接预览，可能过期，尚未持久化到对象存储。
- 方案变更后旧图继续显示原版本，不能当作新方案成果；未提交报价将因版本改变而被拒绝。

## API

- GET /api/image-provider：可用状态及配置估价，不返回密钥。
- POST /api/projects/{id}/image-quotes：revision、shot_id，获取单张报价。
- POST /api/image-jobs/{id}/submit：confirmed=true、max_cost_cny，确认当前单张估价后提交。
- GET /api/projects/{id}/images：项目所有图片任务（包含历史版本）。
- GET /api/image-jobs/{id}：只读本地状态。
- POST /api/image-jobs/{id}/refresh：查询供应商任务。

## 验证边界

自动化测试通过httpx.MockTransport及替身适配器验证请求格式、报价确认、预算拦截、
版本过期、任务额度、错误脱敏、重复提交防护和查询失败。不是付费端到端测试。
暂未实现多进程锁、图片内容验证/下载、自动轮询队列、对账和预留释放；
单进程单用户运行。正式部署需要认证、全局/每用户额度与并发控制、对象存储和任务恢复。

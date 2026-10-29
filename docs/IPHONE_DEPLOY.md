# iPhone云端免费试用部署

代码已准备Dockerfile、Render Blueprint及单用户登录保护。没有创建你的Render账号、
没有代替你填写密钥或创建远端服务；以下是需要你在手机上完成的安全步骤。
当前还没有可供用户访问的已验收公网应用。

## 手机操作

1. Safari打开 https://render.com/ ，注册/登录，选择使用GitHub连接。
2. 部署入口： https://render.com/deploy?repo=https://github.com/yige984-ship-it/ship-it 。
   如果私有仓库不可见，在Render的GitHub授权设置中允许读取ship-it；不需要把仓库公开。
   若入口无法读取私有仓库，Dashboard → New → Blueprint → 选择ship-it，读取render.yaml。
3. 审阅服务配置，确认实例为Free、没有额外付费资源。若页面要求改付费套餐，先停止。
4. 输入页面要求的两个服务器环境变量：
   - SILICONFLOW_API_KEY：刚创建的硅基流动中国站密钥，只填入Render服务器Secret。
   - STUDIO_ACCESS_PASSWORD：自己新设的工作室登录密码，至少12位，不要与API密钥相同。
5. 保持REQUIRE_AUTH=true、COOKIE_SECURE=true、ENABLE_FREE_IMAGES=true、ENABLE_PAID_IMAGES=false。
   保持EPHEMERAL_STORAGE=true。不要复制.env.example覆盖这些安全值。
6. 点击部署，等待平台构建完成。平台提供实际HTTPS网址（通常为onrender.com子域名），不要猜测网址。
7. Safari打开实际网址，使用工作室密码登录。API密钥不在前端输入。
8. 先输入短故事 → 创建方案 → 选Kolors免费 → 获取单张方案 → 确认发送到外部服务。
   模型可用性或免费价格检查失败会停止，绝不自动转收费。
9. 将实际网址发回对话即可帮助排查；不要发送API密钥、工作室密码或包含它们的截图。

## 免费实例的实际边界

2026-10-29查阅Render官方文档：Free Web Service闲置15分钟会休眠，唤醒约一分钟。
Free实例文件系统为临时磁盘：重启、重新部署、休眠都会丢失SQLite项目和生成素材。
免费试用不是长期保存方案，请及时下载图片/视频。
当前蓝图明确EPHEMERAL_STORAGE=true，应用会拒绝所有收费模型，即使误开收费开关。
恢复付费任务/预算账本需要持久化数据库和磁盘，不能靠免费临时盘保证。
平台免费时长和流量有额度；最终以你创建服务时的页面和平台账单为准，不保证永久免费。

官方文档：
- https://render.com/docs/free
- https://render.com/docs/blueprint-spec
- https://render.com/docs/deploy-to-render

## 登录保护

未登录时项目、图片、生成、素材和接口文档都被锁定；health和session用于健康/登录检查。
至少12位工作室密码仅从服务器环境变量读取；缺失时接口503，不能绕过到生成。
会话Cookie为HttpOnly/Secure/SameSite=Strict，有效8小时，退出或进程重启失效。
每分钟最多5次失败登录；同一应用最多5个会话。目前仅单进程单用户，不是多租户产品。
本机脚本默认可不启用登录；公开Docker镜像与Render蓝图默认启用。

## 图片保存

第一次真实生成返回供应商临时HTTPS图片地址后，管理员需核实主机名再配置
IMAGE_DOWNLOAD_HOSTS，域名以逗号分隔，不含协议和路径。不允许使用通配符。
未配置前仍可查看供应商图片，保存到项目将明确失败，不会再次提交生成或扣费。
免费实例即使保存到项目，也会因休眠丢失；下载到iPhone才能跨休眠保留。

## 本地/CI验收与尚未完成

自动化测试覆盖登录拦截、Cookie、退出、缺少密码、暴力尝试限制，以及原有免费优先生成流程。
真实Render账号部署、真实API生成与实体iPhone交互仍需按上面步骤验收，不能宣称已经上线。

## 本次实际验证结果

- 33项自动化测试通过，前端JavaScript语法检查通过。
- Docker镜像实际构建成功；首次验证发现文件读取权限问题，已修复。
- 在非root容器内验证登录拦截、登录、临时存储提示、120秒模拟H.264/AAC合成与206分段播放均通过。
- 验证使用专用测试密码和模拟素材，没有真实供应商密钥或收费调用。
- 容器验证不是Render线上部署或实体iPhone验收。

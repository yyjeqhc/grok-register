# Cloudflare 无限域名邮箱接入指南

这份文档说明如何把“自己控制的域名邮箱”接到本项目的 `cloudflare` 邮箱模式里。

这里的 Cloudflare 不是指直接使用 `cloudflare.pages.dev` 这个示例地址，而是：

1. 你有一个能配置 DNS 的域名或免费二级域名。
2. 你把这个域名接入 Cloudflare Email Routing。
3. 你部署一个临时邮箱后端，例如 `cloudflare_temp_email`。
4. 本项目通过该后端的 API 创建随机邮箱、轮询邮件、读取验证码。

## 目标架构

```text
grok-register
  |
  | POST /api/new_address
  | GET  /api/mails
  v
你的 Cloudflare 临时邮箱 API
  |
  v
Cloudflare Worker / Pages / D1
  |
  v
Cloudflare Email Routing
  |
  v
随机邮箱地址：xxxx@你的域名
```

本项目期望 Cloudflare 邮箱后端至少支持这些接口：

```text
POST /api/new_address
返回：{"address":"随机名@你的域名","jwt":"邮箱访问凭证"}

GET /api/mails?limit=20&offset=0
请求头：Authorization: Bearer <jwt>
返回：邮件列表

GET /api/mail/{id}
请求头：Authorization: Bearer <jwt>
返回：邮件详情
```

代码位置：

- `grok_register_ttk.py` 的 `cloudflare_create_temp_address()`
- `grok_register_ttk.py` 的 `cloudflare_get_messages()`
- `grok_register_ttk.py` 的 `cloudflare_get_message_detail()`

## 准备内容

你需要先准备：

```text
1. 一个域名或免费二级域名
2. 一个 Cloudflare 账号
3. 能修改该域名 DNS 的权限
4. 一个用于接收 Cloudflare 验证邮件的真实邮箱
5. Node.js / pnpm / wrangler，本机或 GitHub Actions 都可以
```

域名要求：

- 推荐使用你自己控制的域名或二级域名。
- 免费二级域名可以用，但必须能配置 DNS。
- 如果不能添加 MX/TXT 记录，不能用于 Cloudflare Email Routing。
- 如果不能加入 Cloudflare zone，也需要至少能按 Cloudflare 要求配置邮件路由 DNS。

## 第一步：把域名接入 Cloudflare

进入 Cloudflare Dashboard：

```text
Websites -> Add a domain
```

添加你的域名，例如：

```text
example.com
```

如果你用的是免费二级域名，例如：

```text
abc.example.net
```

要确认它能作为 zone 添加到 Cloudflare，或者它的 DNS 服务商允许你添加 Cloudflare Email Routing 需要的记录。

Cloudflare 添加完成后，按提示修改 nameserver，等待状态变成：

```text
Active
```

## 第二步：开启 Cloudflare Email Routing

进入 Cloudflare Dashboard：

```text
Email -> Email Routing
```

如果界面路径有变化，搜索：

```text
Email Routing
```

按 Cloudflare 提示启用 Email Routing。通常会做两件事：

1. 添加 MX 记录。
2. 添加 TXT 验证记录。

Cloudflare 会给出类似这样的 DNS 记录：

```text
MX   example.com   route1.mx.cloudflare.net
MX   example.com   route2.mx.cloudflare.net
MX   example.com   route3.mx.cloudflare.net
TXT  example.com   v=spf1 include:_spf.mx.cloudflare.net ~all
```

如果域名 DNS 已经托管在 Cloudflare，通常可以自动添加。

## 第三步：添加目标邮箱并验证

在 Email Routing 里添加一个真实邮箱作为 Destination Address，例如：

```text
yourname@gmail.com
```

Cloudflare 会发送一封验证邮件。打开真实邮箱，点击验证链接。

验证完成后，Email Routing 才能正常启用。

## 第四步：启用 Catch-all

进入：

```text
Email Routing -> Routing Rules
```

启用 Catch-all。

Catch-all 的作用是让任意地址都能收信，例如：

```text
abc123@example.com
test456@example.com
anything@example.com
```

如果只是转发到真实邮箱，Action 可以先设为：

```text
Send to an email
```

这样可以先验证域名收信是否正常。

后面部署临时邮箱 Worker 后，再改成：

```text
Send to Worker
```

## 第五步：部署临时邮箱后端

本项目最匹配的是 `cloudflare_temp_email` 这类后端，因为当前代码已经适配：

```text
POST /api/new_address -> {address, jwt}
GET  /api/mails
GET  /api/mail/{id}
```

推荐流程：

```bash
cd /root/empty
git clone https://github.com/dreamhunter2333/cloudflare_temp_email.git
cd cloudflare_temp_email
```

安装工具：

```bash
npm install -g wrangler pnpm
wrangler login
```

创建 Cloudflare D1：

```bash
wrangler d1 create temp-email-db
```

执行数据库初始化脚本，具体路径以该项目实际文档为准，通常类似：

```bash
wrangler d1 execute temp-email-db --file=./db/schema.sql --remote
```

创建 KV：

```bash
wrangler kv:namespace create DEV
```

进入 worker 目录：

```bash
cd worker
pnpm install
cp wrangler.toml.template wrangler.toml
```

编辑：

```bash
nano wrangler.toml
```

重点检查这些配置：

```toml
name = "cloudflare-temp-email"
main = "src/worker.ts"

[vars]
JWT_SECRET = "生成一个足够长的随机字符串"
DEFAULT_DOMAINS = ["你的域名"]
DOMAINS = ["你的域名"]
```

D1 配置填 `wrangler d1 create` 输出的 database id：

```toml
[[d1_databases]]
binding = "DB"
database_name = "temp-email-db"
database_id = "你的 database_id"
```

KV 配置填 `wrangler kv:namespace create` 输出的 id：

```toml
[[kv_namespaces]]
binding = "KV"
id = "你的 KV namespace id"
```

部署：

```bash
pnpm run deploy
```

部署成功后，你会得到一个 API 地址，例如：

```text
https://cloudflare-temp-email.xxx.workers.dev
```

或者你可以在 Cloudflare 给 Worker 绑定自定义域名，例如：

```text
https://mail-api.example.com
```

这个地址就是本项目的：

```text
cloudflare_api_base
```

## 第六步：把 Email Routing 发给 Worker

回到 Cloudflare Dashboard：

```text
Email Routing -> Routing Rules
```

把 Catch-all 的 Action 改成：

```text
Send to Worker
```

Worker 选择你刚部署的：

```text
cloudflare-temp-email
```

这样发送到 `任意地址@你的域名` 的邮件才会进入临时邮箱后端。

## 第七步：测试临时邮箱 API

先测试创建邮箱：

```bash
BASE="https://你的临时邮箱API地址"
DOMAIN="你的域名"

curl -sS "$BASE/api/new_address" \
  -H "Content-Type: application/json" \
  --data "{\"domain\":\"$DOMAIN\"}"
```

期望返回：

```json
{
  "address": "随机名@你的域名",
  "jwt": "一长串访问凭证"
}
```

保存返回的 `address` 和 `jwt`。

测试拉邮件：

```bash
JWT="上一步返回的jwt"

curl -sS "$BASE/api/mails?limit=20&offset=0" \
  -H "Authorization: Bearer $JWT"
```

此时你可以从自己的 Gmail/Outlook 给 `address` 发一封测试邮件，然后再执行上面的拉邮件命令。

## 第八步：用本项目自带工具测试

进入本项目：

```bash
cd /root/empty/grok-register
source .venv/bin/activate
```

运行：

```bash
python cf_mail_debug.py \
  --api-base "https://你的临时邮箱API地址" \
  --timeout 180 \
  --interval 3
```

它会输出：

```text
[NEW] address=随机名@你的域名
[NEW] credential(jwt)=...
```

看到地址后，手动给这个地址发一封测试邮件。

如果能看到：

```text
[MAIL] ...
```

说明收信链路正常。

## 第九步：配置 grok-register

编辑：

```bash
cd /root/empty/grok-register
nano config.json
```

建议配置：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的临时邮箱API地址",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的域名",
  "proxy": "",
  "enable_nsfw": false,
  "register_count": 15,
  "grok2api_auto_add_local": false,
  "grok2api_auto_add_remote": false,
  "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
}
```

如果你的 API 开启了鉴权：

Bearer 模式：

```json
{
  "cloudflare_auth_mode": "bearer",
  "cloudflare_api_key": "你的API密钥"
}
```

X-API-Key 模式：

```json
{
  "cloudflare_auth_mode": "x-api-key",
  "cloudflare_api_key": "你的API密钥"
}
```

Query key 模式：

```json
{
  "cloudflare_auth_mode": "query-key",
  "cloudflare_api_key": "你的API密钥"
}
```

## 第十步：启动 CLI

```bash
cd /root/empty/grok-register
source .venv/bin/activate
python grok_register_ttk.py cli
```

输入：

```text
start
```

## 常见问题

### 1. `cloudflare.pages.dev` 不能用

`cloudflare.pages.dev` 是示例占位，不是你的服务地址。

必须换成你自己部署出来的地址，例如：

```text
https://cloudflare-temp-email.xxx.workers.dev
https://mail-api.example.com
```

### 2. 能创建邮箱，但是收不到邮件

优先检查：

```text
1. Cloudflare Email Routing 是否 enabled
2. MX 记录是否正确
3. Catch-all 是否开启
4. Catch-all 是否发给 Worker
5. Worker 的 email handler 是否部署成功
6. D1 表结构是否初始化
```

### 3. `cf_mail_debug.py` 一直 no mails

说明 API 能创建邮箱，但邮件没有进入后端。

先用真实邮箱给生成的地址发邮件，然后检查：

```text
Cloudflare Dashboard -> Email Routing -> Activity Log
```

如果 Activity Log 没有记录，说明 DNS/MX/Catch-all 有问题。

如果 Activity Log 有记录，但 API 没有邮件，说明 Worker/D1 写入链路有问题。

### 4. 目标页面拒绝邮箱

公共临时邮箱域名容易被拒绝。

更稳的方式是：

```text
自己的域名 + Cloudflare Email Routing + Catch-all + Worker API
```

这也是作者说的“免费二级域名邮箱”的核心意思。

### 5. Tailscale 是否影响邮箱 API

会影响。

当前机器的公网访问走 Tailscale exit node，DNS 也可能走 `tailscale0`。

检查方式：

```bash
curl -4 https://api.ipify.org
ip route get 1.1.1.1
resolvectl query 你的API域名
```

如果 `ip route get` 显示：

```text
dev tailscale0 table 52
```

说明请求走 Tailscale 出口。

## 最终你需要给本项目的三个值

部署完成后，把这三个值填进 `config.json`：

```text
cloudflare_api_base = 你的临时邮箱 API 地址
defaultDomains = 你的收信域名
cloudflare_auth_mode / cloudflare_api_key = 你的 API 鉴权方式
```

然后先跑：

```bash
python cf_mail_debug.py --api-base "https://你的临时邮箱API地址"
```

确认收信正常后，再运行：

```bash
python grok_register_ttk.py cli
```

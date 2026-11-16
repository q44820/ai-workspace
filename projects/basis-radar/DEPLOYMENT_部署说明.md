# 部署说明｜价差中位数网页

代码已在本项目，网页静态文件位于`static/`。服务仅使用Python标准库，不需要交易所API Key，不签名或下单。

## 本地运行

Python 3.10+：

```bash
cd projects/basis-radar
python3 -m unittest discover -p 'test_*.py' -v
python3 server.py --host 127.0.0.1 --port 8901
```

浏览器打开`http://127.0.0.1:8901`；健康检查为`/health`，v1.1.0应返回`ok: true`及`read_only: true`。

## Linux服务示例

以下仅为脱敏模板，不是当前服务器配置。替换路径及运行用户后，须另获部署授权才能安装或重启。

```ini
[Unit]
Description=Read-only basis radar
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/ai-workspace/projects/basis-radar
Environment=BASIS_DB=/path/to/private-runtime/quotes.sqlite3
ExecStart=/usr/bin/python3 server.py --host 127.0.0.1 --port 8901
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

可使用systemd用户服务管理；如果未启用linger，退出登录或机器重启后的启动不能保证。配置文件存在或服务当前active都不能证明已具备重启自启。

## 对外访问

默认只监听回环地址。外网访问应通过独立反向代理/HTTPS隧道，不直接暴露数据库或认证材料。临时隧道URL可能变化，不把临时URL当永久入口。不要无授权修改防火墙、SSH配置或服务。

## 版本核对与更新

- 比较`core.py`、`exchanges.py`、`server.py`与`static/`内3份网页文件的SHA256。
- 查看`/health`的版本和只读字段，单独检查网页与JS能否读取；这不等于全部交易所行情可用。
- 更新先在隔离环境测试，经明确部署授权后再备份/替换线上代码；回退也需核对服务与用户侧效果。
- 本次只读核对确认上述6份线上程序与仓库一致，版本v1.1.0；主服务和隧道在检查时active。未部署、重启或修改数据库。

## 不进入公开Git的内容

服务器地址、登录凭据、真实服务配置、隧道认证、数据库、日志、运行时目录和账户数据。这里只保留可移植代码及脱敏运行方法。

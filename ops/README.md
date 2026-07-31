# 每日调度模板

这里的文件仅供手工复制和修改，仓库不会安装 cron、systemd unit 或 launchd job，也不会修改主机设置。模板都计划每天 `18:30` 执行一次 `stock-agent live`。

启用任何模板前：

1. 将所有 `/ABSOLUTE/PATH/TO/agent-plan` 替换为仓库的真实绝对路径；
2. 在仓库中完成 `.venv` 安装，并确认 `.venv/bin/stock-agent` 存在；
3. 运行 `stock-agent validate --config config/watchlist.json --fundamentals data/fundamentals.json`；
4. 手工执行一次同样的 `live` 命令并检查报告；
5. 创建 `var/`，确认运行账户对 `reports/` 和 `var/` 有写权限。

## cron

[`stock-agent.cron.example`](stock-agent.cron.example) 使用 `CRON_TZ=Asia/Shanghai`。Cronie 支持这一设置，但部分 BSD/macOS cron 只按主机时区调度；在后者上，只有当主机时区也是 `Asia/Shanghai` 时才可直接使用该表达式。不要仅设置命令进程的 `TZ` 后就假设 cron 的触发时间也随之改变。

需要启用时，由操作者手工把修改后的内容加入个人 crontab。建议先暂时把时间改为未来几分钟，验证日志、报告和退出码，再恢复 `18:30`。

## systemd

Linux/systemd 可使用 [`stock-agent-daily.service.example`](stock-agent-daily.service.example) 和 [`stock-agent-daily.timer.example`](stock-agent-daily.timer.example)。Timer 的 `OnCalendar` 显式包含 `Asia/Shanghai`，不依赖主机本地时区。

模板没有 `User=`，用户级 unit 应由目标登录账户运行；若改成系统级 unit，必须显式配置一个权限受限的服务账户。请勿以 root 身份运行本项目。

## macOS launchd

[`com.stockagent.daily.plist.example`](com.stockagent.daily.plist.example) 适用于当前这种 macOS 本地运行方式。`StartCalendarInterval` 使用 Mac 的本地时区；只有系统时区为 `Asia/Shanghai` 时，模板才等同于北京时间 18:30。

先替换模板中的全部绝对路径，用 `plutil -lint` 校验，再由用户自行复制到 `~/Library/LaunchAgents/` 并加载。加载会修改本机长期调度状态，因此本项目不会自动执行这一步。

当前机器的已安装版本使用 [`com.stockagent.daily.plist`](com.stockagent.daily.plist)，运行副本部署在 `~/Library/Application Support/StockAgent/`。这是因为 macOS 隐私保护会阻止无界面 LaunchAgent 稳定读取 `Documents`；不要通过放宽全盘隐私权限绕过。源码、配置或财务快照更新后，应重新同步 `src/`、`config/` 和 `data/` 到该运行副本再重载任务。

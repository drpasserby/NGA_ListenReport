# NGA_ListenReport
监听NGA版主举报提醒列表的更新并采用Server酱3发送通知。

## 功能列表
1. **举报监测**: 监听论坛提醒信息列表并记录最新的提醒信息(论坛限制50条);
2. **循环运行**: 可设定循环监测时间;
3. **软件通知**: 监听到存在新举报可通过sc3酱发送通知到手机客户端(目前仅支持该方式),如无更新则不会通知;
4. **日志记录**: 可在`cache.json`中查看监测到的举报记录.

## 使用方法
1. 复制`config.yaml.default`文件并重命名为`config.yaml`,按照说明填写对应的内容。
   - Cookie: 登录NGA后，按F12打开`控制台`-`网络`，复制请求的Cookie字段到`config.yaml`中`Cookie`段。
   - Server酱3: 打开[Server酱3](https://sc3.ft07.com/)并登录，申请sendkey，并填写到`config.yaml`中的`sendkey`字段。
2. 监控端使用方法:
   - 本地Windows设备: 先打开命令行使用pip install安装依赖包: `pip install -r requirements.txt`，然后双击`【Windows直接启动】NGA举报列表监测.bat`运行程序(需要保持电脑开机和程序持续运行，不推荐)。
   - 云服务器使用方法: 使用`PM2`或者`Supervisor`管理工具运行程序，或者使用`crontab`定时运行程序(需要配置一下环境，推荐)。
3. 手机端接收推送: 下载[Server酱3客户端](https://sc3.ft07.com/client)并登录,并填写`sendkey`字段,即可接收推送。

## 免责声明
本脚本仅限交流学习使用,请勿违法使用.

本脚本遵循`GNU General Public License v3.0`协议,使用尽可能标明出处
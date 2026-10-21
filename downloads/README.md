# 可直接分享的插件安装文件

## 最新版 0.4.0：双入口学习与明确确认

**[下载 astrbot_plugin_human_style-0.4.0.zip](https://github.com/Yugongshit/AIE3905-Make-astrbot-speak-like-human/raw/refs/heads/main/downloads/astrbot_plugin_human_style-0.4.0.zip)**

在 AstrBot“插件”页面选择 **从文件安装**，选择 ZIP，不解压、不先卸载旧版。更新后检查卡片显示 **v0.4.0**，刷新浏览器，从左侧 **插件可视化视图 → 表达学习控制台** 打开。

- **教它怎么说话：**学习你认可的表达、语气与回应方式。
- **熟悉工作环境：**提取汽配术语、别名、部件区别、流程和沟通规则。原始交易不再按历史片段写入知识库。
- **先预览再确认：**选文件不会上传；点击预览后，确认学习时会再显示目标群、用途、文件和条数，传错可取消或换文件。环境候选还要审核才生效。

0.3.0 已有的历史交易条目保留供查看、导出和删除，但停止用于回复。表达库和群开关保留。新别名与复杂流程可能无法被规则自动识别，可手动补充，审核后参与检索。

安装包只含程序、说明及虚构素材，不含你上传的真实客户群聊或学习数据库。可以下载后转发文件，也可直接分享此链接。

SHA-256：

```text
fe0ffee2eb8355129ffb6cd212755fc7910724765c4642808867d6fc75dafeba
```

[面板操作说明](../docs/panel.md) · [工作环境学习指南](../docs/auto-parts-knowledge.md) · [确认弹窗预览](../docs/images/learning-confirmation.png)

## 历史版本 0.3.0：汽配业务知识库

**[下载 astrbot_plugin_human_style-0.3.0.zip](https://github.com/Yugongshit/AIE3905-Make-astrbot-speak-like-human/raw/refs/heads/main/downloads/astrbot_plugin_human_style-0.3.0.zip)**

在 AstrBot 插件管理中选择“从文件安装”，直接选择 ZIP，不用解压，也无需先卸载旧版。更新后检查卡片显示 **v0.3.0**，刷新浏览器，再从左侧“插件可视化视图 → 表达学习控制台”打开。

选择“汽配业务知识”，可以直接上传带时间和发言人的原始 TXT 群聊，预览后导入待审知识。新增了分类、来源日期与行号、批量审核、有效期、独立知识开关和知识查询。历史报价与库存不会自动变成当前事实。旧的表达学习仍可使用。

安装包只包含程序、说明和虚构演示素材，不包含任何实际客户的聊天记录、车型标识、模型密钥或学习数据库。下载到手机或电脑后可以作为文件转发。

SHA-256：

```text
7013715a9b0092822c8df2c826407de8c5fc9950e43e7c28ff721adbb8d3da46
```

[汽配知识库使用指南](../docs/auto-parts-knowledge.md)

## 历史版本 0.2.0

**[下载 astrbot_plugin_human_style-0.2.0.zip](https://github.com/Yugongshit/AIE3905-Make-astrbot-speak-like-human/raw/refs/heads/main/downloads/astrbot_plugin_human_style-0.2.0.zip)**

这是已打包的插件文件，下载到手机或电脑后，可以作为文件发送给别人。也可以打开本目录中的 ZIP 文件，在 GitHub 文件页面点击下载按钮获取。

在 AstrBot 插件管理里使用“从文件安装”，选择这个 ZIP，保持压缩包原样。安装或更新后，插件卡片应显示 **v0.2.0**，页面中会出现学习控制台入口。

已经装了 0.1.0 时，也可以点击卡片右下角的 **“⋯ → 更新”**。圆形箭头只会重载现有代码，不会下载新版。无需为了更新先卸载插件。

包内 `metadata.yaml` 版本为 `v0.2.0`，含 `views/learning/index.html`、控制台脚本与样式、学习模块和说明。该包由代码提交 `dfb7420e5b19d56cf729dda8d8157c89d8b1a758` 打包，安装文件不含任何用户的聊天记录或模型密钥。

SHA-256：

```text
dd5bd3426cbbdb61598f7a065b5bb52eb4d39b2a8e09aec9f54bec218de860d2
```

[打开使用指南](../docs/panel.md)

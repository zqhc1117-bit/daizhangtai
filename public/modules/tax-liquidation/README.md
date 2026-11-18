# 企业税务清算及财产清偿报告生成器

进入“首页 → 销售辅助台 → 税务清算报告”。默认页面为“选择纳税人类型 → 上传资产负债表或科目余额表 → 生成清算清偿报告”，支持 Excel、CSV / TSV、PDF 和图片，保留 Word 与 PDF 导出。可展开识别结果修正企业名称、报表日期及金额。

原分步表单和公司历史草稿已移至下方“手工填报与历史公司草稿”。以下模式、草稿与计算说明针对该原填报工具。上传式报告位于 `statement-import.js`、`upload-report-model.js`、`upload-report.js`；读取上传报表后形成待核实清算方案，不自动认定实际变现、已缴税或已偿债。PDF/图片的本机识别由根目录 `statement_extract.py` 及登录后的 `/api/sales/statement-text` 提供，不写入账套。自动报告草稿使用独立的 `ydz:sales-statement:v1:<环境>:<操作员>` 键。

## 模式开关

编辑本目录的 `modes.config.js`，保存后刷新浏览器：

```js
export const ENABLED_MODES = ['quick', 'full']; // 两种模式
// export const ENABLED_MODES = ['quick'];     // 仅极速模式
// export const ENABLED_MODES = ['full'];      // 仅完整模式
```

只保留一种时，模式切换器自动隐藏；保存过的其他模式草稿会切换到启用的模式，示例菜单也仅提供启用模式。两种模式的输入数据各自保留，共享企业、股东和善后信息。

## 目录与接入

- `quick/`、`full/`：各模式表单、财务计算；Excel 导入仅属于完整模式。
- `shared/`：企业与股东表单、善后表单、工商文本提取、控件。
- `schema.js`、`storage.js`：公共草稿结构、白名单恢复、公司独立浏览器保存。
- `money.js`、`calculate.js`、`validation.js`：整数分金额、清偿顺序、分配及校验。
- `report-model.js`、`report.js`、`export.js`：六章公共报告结构、A4预览、Word与打印。
- `vendor/`：可直接使用的本地 Excel/Word 库及许可证。运行时无需 npm 或外网。
- `tests/`、`scripts/`：计算/导入/隔离测试、浏览器验收与依赖重建脚本。

原项目仅改动 `public/company-workspace.js`（公司导航）、`public/finance-ui.js`（页面分发和绑定）、`public/index.html`（局部样式加载）、根目录 `package.json`（检查命令）。修改前这四个文件已备份在 `update-backups/tax-liquidation-20261117-110604/`。

屏幕 CSS 采用专属 `tliq-` 命名空间；小屏下的公司框架调整仅在 `:has(.tliq-module-host)` 时生效，其他模块沿用原样。打印规则仅在存在该模块时生效。命名页 `tax-liquidation` 使用 A4 纵向，覆盖宿主通用报表的横向默认值；隐藏导航、表单、工具栏，只打印报告。财务表格尽量整体排版，超长表格允许跨页并重复表头，每一行禁止在两页间拆开。

## 本地数据

独立键为 `ydz:tax-liquidation:v1:<环境>:<公司ID>:<公司编码>`。按公司保存整个草稿（包含两个模式的各自财务底数），每次输入立即写入 `localStorage`；不调用业务写入接口。清空重置只影响当前公司的本模块草稿，可撤销。

草稿属于当前浏览器和当前访问地址（域名/IP/端口），不会自动同步到其他电脑或浏览器。存储被禁用、损坏或容量已满时会显示明确提示，并保留本页内存数据供导出。

示例数据带有“示例数据”标记，预览和 Word 保留该标记；核实、替换后可点“已核实，转为正式草稿”。报告依据录入及完税勾选生成，不自动读取或推断税务机关办理结果。

## 计算口径

1. 金额输入统一为元，最多两位小数；内部用整数分，税额四舍五入到分。持股比例支持六位小数；最大余数法分配尾差，保证股东分配合计与剩余财产相等。
2. 极速模式核对：货币资金 = 实收资本 + 未分配利润 + 基准日未交税费。实收资本默认随股东实缴合计变动，手动修改后解除联动，可重新带入。
3. 完整模式核对资产 = 负债 + 权益；非货币资产必须填实际变现额（全部损失填0）。Excel 使用明确期末列，支持左右并排表、元/万元/亿元换算，固定资产优先净值；缺失/歧义项目提示复核。
4. 清偿顺序：清算费用 → 基准日欠薪及本期新增工资社保补偿 → 基准日欠税及清算期税费 → 外部债务实际支付。每项记录现金可覆盖额与缺口。任何一项不足，触发红色预警并停止股东分配。
5. 基准日已入账职工欠款与本期新增职工支出分别计算；后者参与清算费用扣除，避免基准日费用再次税前扣除。基准日欠税仅参与现金清偿，不重复冲减本期所得。
6. 豁免差额计入债务清偿所得；股东承接不按债务豁免计入所得。未填写承接人或未确认债权人书面同意时，保留待处理事项并暂停分配。单方承诺不能直接得出公司债务已清偿结论。
7. 所得税提供0%、5%、25%和手动税额。默认25%；5%仅作用户要求的试算选项，正数所得选5%时报告保留待核定状态；正数所得选0%须说明免税依据。税会差异和可弥补亏损通过实际申报/核定的手动税额校准。
8. 自然人分配超出实缴额时提示20%个税测算；涉及累计未分配利润、盈余公积时，应区分股息和投资转让所得。分配表为税前金额，未将股东个税重复计入公司现金池扣减。
9. 资产负债不平、比例不等于100%、金额无效或资产/债务处理未完成时暂停分配；企业信息不全、完税未核实、优惠口径待核定或示例草稿会明确写入报告的待核实事项。

口径核对参考：[现行公司法第236、237条](https://www.samr.gov.cn/djzcj/zcfg/fl/art/2026/art_9b4263a169cf43c2aba950cfc9ac1af2.html)、[财税〔2009〕60号](https://fgk.chinatax.gov.cn/zcfgk/c102416/c5203516/content.html)、[税务总局清算所得税申报填报说明](https://12366.chinatax.gov.cn/bzds/054/054.html)。

## 验证与维护

项目根目录执行：

```powershell
npm.cmd run check
npm.cmd run test:tax-liquidation
python -X utf8 -B public/modules/tax-liquidation/tests/verify_browser.py
```

浏览器脚本使用临时合成公司与独立服务端口；不读取正式账套。开发环境需 Playwright、Chrome、pypdf（也支持将 pypdf 安装到根目录 `tmp/tax-liquidation-dev-deps`）。输出截图、合成Word、PDF和检查结果到 `tmp/tax-liquidation-verification/`。

重建本地库（仅开发时需要）：

```powershell
cd public/modules/tax-liquidation
npm.cmd ci --no-audit --no-fund
npm.cmd run build:vendor
```

版本锁在模块 `package-lock.json`：SheetJS CE 0.20.3、docx 9.15.4、file-saver 2.0.5；esbuild 仅用于开发期打包。模块通过 `entry.js` 按需打开；Excel及Word库也按需加载，日常进入工作台不会先下载本模块大库。

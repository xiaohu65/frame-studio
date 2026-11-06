# 帧工坊本地工作台

React + Vite 前端。生产文件在 `dist/`，由本地 Python 服务统一提供；接口和媒体均从相同的本机地址读取。

```sh
npm install
npm run build
```

开发模式 `npm run dev` 只绑定 `127.0.0.1`，将 `/api` 代理到本机 `8765`。无需另外打开浏览器实例。

界面包含原片上传与分段、人偶参考与关节 JSON 校正、人物与场景素材包、提示词/费用预检/预算授权/生成任务，以及音乐与去重叠拼接。没有付费 API 的自动提交；检查按钮仅预检，生成按钮在用户操作后才发出提交动作。新关节人偶必须单独复核，旧版剪影不能直接标为通过。

`src/api.js` 封装 API；`src/components/` 分别管理通用控件、媒体/时间轴、流程页面和输出参数/API 设置。

## 主题与组件

界面使用 shadcn/ui（Radix primitives）和 Tailwind CSS v4，主题来自 tweakcn Default。基础组件源码在 `src/components/ui/`；页面布局使用语义 HTML 和 Tailwind，已删除旧主题库与过渡布局适配层。左侧导航使用标准 Sidebar，支持桌面收起和手机 Sheet 抽屉。

在 tweakcn 的 Code 面板选择 Tailwind v4，导出主题后，替换 `src/theme.css` 中标注的 `:root` 和 `.dark` 主题变量块。保留该文件的 Tailwind 导入、`@theme inline` 映射及应用语义色扩展。颜色须使用完整 CSS 颜色值（例如 `oklch(...)` 或 `#...`）。主题字体使用 `--font-sans/serif/mono`，圆角使用 `--radius`，新增页面沿用这些语义变量。

成功、警告、错误、媒体舞台、时间轴和屏幕范围标记的颜色统一定义在 `src/theme.css`。人物标识色是项目数据；黑白遮罩的像素含义保持原有逻辑。更换主题后运行 `npm test`、`npm run build`，并在现有浏览器会话中核对五步导航、表单、下拉菜单、弹窗焦点和窄屏布局。

主题迁移已通过 147 项测试和生产构建，其中 `tests/themeMigration.test.mjs` 检查旧依赖与适配层、标准组件入口、固定界面配色、页面覆盖标准控件和侧栏导航契约；遮罩像素测试验证提示色跟随传入主题，并保留原始像素及保护层优先级。既有 Chrome 会话已核对五步导航、侧栏收起展开、390px 手机抽屉切换、折叠控件、标签页、下拉菜单及弹窗焦点恢复。验证未触发付费生成或上传；真实云端生成及全部媒体组合不属于本次主题验证。`scripts/check-contract.jsx` 仍可用于组件契约检查，需先启动后端。

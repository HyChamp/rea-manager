# Android APK 分析接入指南

## 能力边界（先明确能做什么）

REA 把已有的 headless JADX 引擎包装成静态分析工具，返回**与产物绑定、带证据**的结论。全程静态：不执行 APK、不写原始文件。

- 包级：解码后的 manifest XML、声明摘要、权限列表、类/资源数量。**签名验证明确不执行**。
- 类级：类名清单、字段、方法、内部类（反编译器表示，非精确 DEX 描述符）。
- 方法级：Java 或 smali、fallback 标记、完整/部分文本。反编译源码是派生表示，完整文本不等于完整语义。
- 引用级：入站静态关系，**不是运行时观测到的调用**。反射和动态加载会逃出该集合。

不做：执行 APK、split APK/AAB、签名校验、完整资源表语义、native 库分析、Android 运行时捕获。

## 依赖三件套（REA 都不负责安装）

1. **完整 JDK 17+**，必须含 `jdk.compiler` 模块（纯 JRE 不行）。JDK 21 是已验证组合。
   - 校验：`java --list-modules | grep jdk.compiler`（Windows PowerShell 用 Select-String）。
   - 也可用 `scripts/doctor.py` 自动判断。
2. **固定版本 JADX 桥接 JAR**：`jadx-headless-mcp-0.7.1-all.jar`
   - URL：`https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar`
   - SHA-256：`6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75`
   - 注意：上游在 v1.x 改过包名，网上有 issue 说 0.7.1 取不到；但官方披露的 0.7.1 地址与上面一致且摘要可校验通过。以本节常量为准，并在使用前校验摘要。
   - `scripts/install_android.py` 会自动下载并校验。
3. **环境变量** `REA_JADX_MCP_JAR` 指向 JAR 绝对路径（必须在启动 MCP server 的同一个 shell 里设置；在别的终端 export 不会影响已运行进程）。
   - 可选 `JAVA_HOME` 指定某个已存在的 JDK；大 APK 可加 `REA_JADX_HEAP_MIB=8192`。

不需要 Android SDK、模拟器、Gradle 或设备服务。

## 平台支持矩阵（重要）

| 平台 | 支持情况 |
|---|---|
| macOS arm64 + OpenJDK 21 | 元数据桥已验证（配公开 ApiDemos 样本） |
| Linux + JDK | POSIX 适配器支持；JDK 25 尚未实测。需要 PATH 里有 procps 兼容的 `ps` |
| Windows x64 | 需要**较新的 REA 包**（含匹配的原生 controls 与 owned stdin）。旧版会报 package prerequisite 拒绝。用 JDK 21 + 钉死的 0.7.1 JAR 做过真实验证 |
| Windows arm64 | 不在当前原生支持边界内 |

Windows PowerShell 中带空格的路径无需额外转义：

```powershell
$env:REA_JADX_MCP_JAR = 'D:\tools\jadx-headless-mcp-0.7.1-all.jar'
$env:JAVA_HOME = 'D:\tools\jdk-21'
rea inspect-android-package 'D:\targets\Example.apk'
```

REA 会选择 `JAVA_HOME\bin\java.exe`，或解析 PATH 上的 `java.exe`。缺失或不匹配的原生组件会报告前置条件，而不是尝试不受控的 Java 启动。

## 冒烟测试

```bash
rea inspect-android-package /absolute/path/Example.apk --json
```

返回解码后的 manifest，说明 JAR 与 JDK 配置已生效。仓库还提供校验 lane（仅仓库源码，npm 包里没有）：`npm run fixtures:android && npm run verify:android`，会拉取校验过的 JAR 和公开样本 APK 做端到端验证。

## 常用 CLI 命令

```bash
rea inspect-android-package  /abs/Example.apk                       # 包概览、manifest、权限
rea search-android-classes   /abs/Example.apk MainActivity          # 按类名搜索（空查询列出全部）
rea inspect-android-class    /abs/Example.apk example.MainActivity  # 类的成员清单
rea inspect-android-method   /abs/Example.apk example.MainActivity onCreate --json
rea inspect-android-method   /abs/Example.apk example.MainActivity select --overload-index 1
rea trace-android-references /abs/Example.apk example.MainActivity --method-name onCreate
```

要点：

- 类搜索匹配**类名**，不是方法体内的任意字符串。混淆过的包可先从 manifest 的 activity 入手。
- 同名重载方法要用 `inspect_android_class` 先看成员，再用 `--overload-index` 选定；REA 对有歧义的方法请求会拒绝而非静默取第一个。引擎的引用操作无法选重载，遇到多重载会报该边界 unsupported。
- 返回 smali 时视为同一方法的另一种表示，显式检查它。native/abstract 方法无反编译体时报 `body_status: not_available`。
- 超大 APK：先把反编译输出放到仓库目录之外，用 `sha256sum` 记录 APK 哈希，按包名/类/字符串缩小范围，别把整棵类树读进上下文。

## Agent 提问模板

把问题限定到单一功能点，并要求区分"静态声明"与"运行时观测"：

```
Investigate file export in /abs/Example.apk. Trace from the screen that
triggers export to the method that writes the file. Report each class and
method with evidence, and mark anything that cannot be resolved statically.
```

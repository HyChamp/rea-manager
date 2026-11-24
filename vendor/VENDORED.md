# 内置依赖（离线优先）

| 包 | 版本 | 来源 | 说明 |
|---|---|---|---|
| ida_pro_mcp | 2.0.0（已验证含 idb_open/idb_list/idb_close） | G:/WOBANGNI/IDA94ProBable_CN/python311/Lib/site-packages/ida_pro_mcp | 纯 Python，可直接拷进 IDA 自带 Python 的 site-packages |

安装方式：`install_ida.py` 优先把本目录的包拷进 `<IDADIR>\python3xx\Lib\site-packages\`，
不联网、版本确定。只有当内置包缺失时才回退到网络下载。

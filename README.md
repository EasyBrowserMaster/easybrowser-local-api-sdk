# EasyBrowser Local API SDK

EasyBrowser Local API SDK is a small Python client for the EasyBrowser local API.

Website: https://easybrowser.pages.dev/

## Install

```bash
pip install easybrowser-local-api-sdk
```

## Quick Start

```python
from easybrowser_sdk import EasyBrowserAPI

api = EasyBrowserAPI()
api.ensure_local_api_available()

environment = api.ensure_container(
    name="TestEnv",
    defaultUrl="https://example.com",
)
print(environment["id"])
```

## Container lifecycle

```python
# Soft delete (moves to trash, recoverable)
api.delete_container(env_id)

# Restore from trash
api.restore_container(env_id)

# List trashed containers
api.trash_list()

# Permanently delete + clean browser data (cookies/storage). Irreversible.
api.delete_permanently(env_id)
```

## Requirements

- Python 3.10+
- EasyLauncher installed and running

Free VIP activation is available inside EasyLauncher.

## Publish

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

Use `__token__` as the username and a PyPI API token as the password.

---

# EasyBrowser Local API SDK

EasyBrowser Local API SDK 是一个用于 EasyBrowser 本地 API 的简洁 Python 客户端。

官网：https://easybrowser.pages.dev/

## 安装

```bash
pip install easybrowser-local-api-sdk
```

## 快速开始

```python
from easybrowser_sdk import EasyBrowserAPI

api = EasyBrowserAPI()
api.ensure_local_api_available()

environment = api.ensure_container(
    name="TestEnv",
    defaultUrl="https://example.com",
)
print(environment["id"])
```

## 环境生命周期

```python
# 软删除（移入回收站，可恢复）
api.delete_container(env_id)

# 从回收站恢复
api.restore_container(env_id)

# 查看回收站
api.trash_list()

# 彻底删除 + 清理浏览器数据（Cookie/存储），不可恢复
api.delete_permanently(env_id)
```

## 运行要求

- Python 3.10 及以上
- 已安装并运行 EasyLauncher

EasyLauncher 内可免费开通 VIP。

## 发布

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

用户名使用 `__token__`，密码使用 PyPI API token。

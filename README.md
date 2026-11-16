# aitag.win 备份数据集 v1

aitag.win（AI 绘图作品索引站）历史作品的公开备份：

- **作品元数据**：1,367,076 条 detail JSON（2025-01 ~ 2026-03），以及解析后的结构化清单
- **作品图片**：1,487,767 张 webp（每作品第 1 张；`image_count > 20` 的作品加第 2 张），共 157.19 GB
- **映射表**：源数据（作品）↔ 图片 ↔ 归档 的完整对照

图片归档使用 7z AES-256 加密（文件名列表同样加密）。**解压密码不在本仓库中**，请向仓库所有者获取。

## 目录

| 路径 | 内容 |
|---|---|
| `mapping/works.csv.gz` | 作品表：work_id、月份、AI 类型、作者、标题、图片数、书签数、对应元数据路径 |
| `mapping/images.csv.gz` | 图片表：relpath、字节数、月份、work_id、页号、状态、所属加密归档 |
| `mapping/volumes.csv` | 归档清单：名称、类型、条目数、源字节、sha256 / GitHub digest、首尾条目 |
| `scripts/` | 备份、打包、上传脚本（无密钥） |

## 归档（Release `data-v1`）

https://github.com/Brind235808/aitag-backup/releases/tag/data-v1

| 归档 | 类型 | 说明 |
|---|---|---|
| `aitag_state_*.7z` | 明文 | 抓取队列、完成/404 清单、审计汇总 |
| `aitag_index_*.7z` | 明文 | 按月 id 清单、索引 JSONL |
| `aitag_manifest_*.7z` | 明文 | 解析后的结构化清单（含 prompt/negative/采样参数） |
| `aitag_meta_0001~0002.7z` | 明文 | 原始 detail JSON（`meta/月/前6位/id.json.gz`），文件直接在归档根下 |
| `aitag_meta_0003~0011.7z` | 明文 | 同上；7z 内含单个 `aitag_meta_XXXX.7z.tar`（tar 流式打包） |
| `aitag_images_*.7z` | **加密** | webp 图片，条目路径与 `images.csv.gz` 的 `relpath` 一致 |

共 120 个归档，合计约 **174.7 GiB**（100 个图片包 + 11 个原始元数据包 + 7 个 manifest 包 + 1 个 index 包 + 1 个 state 包）。

## 解压

```bash
# 明文元数据（0001/0002）
7z x aitag_meta_0001.7z

# 明文元数据（0003~0011：先出 tar，再解 tar）
7z x aitag_meta_0003.7z
tar xf aitag_meta_0003.7z.tar

# 加密图片（密码向仓库所有者获取）
7z x aitag_images_0001.7z
```

## 映射表字段

`images.csv.gz`

| 字段 | 说明 |
|---|---|
| `relpath` | 归档内路径，格式 `{AI_type}/{userId}/{work_id}_p{page}.webp` |
| `bytes` | 原图字节数 |
| `month` | 作品月份 |
| `work_id` | 作品 id（可从 `works.csv.gz` 关联标题/作者/标签） |
| `page` | 页号（0 基；个别作品 p0 缺失时取实际存在的首个页） |
| `status` | `ok` / `missing_404` / `unknown` |
| `archive` | 所属图片归档；404 为空 |

`works.csv.gz`

| 字段 | 说明 |
|---|---|
| `work_id` | 作品 id |
| `month` | 月份桶 |
| `ai_type` | SD / NAI / ComfyUI / NAI_X |
| `user_id`, `user_name` | 作者 |
| `title`, `create_date` | 标题、发布时间 |
| `image_count` | 站点记录的图片数 |
| `total_bookmarks` | 书签数 |
| `meta_path` | 对应 `aitag_meta_*.7z` 内的原始 JSON 路径 |
| `ok` | 解析状态 |

## 统计

- 作品：1,367,076
- 图片：成功 1,487,767（157.19 GB），404 26,848
- 归档：见 `mapping/volumes.csv`（每包约 1.7 GB 源数据）

## 完整性

`mapping/volumes.csv` 的 `github_digest` 是 GitHub 返回的归档 SHA-256；下载后可用 `certutil -hashfile <file> SHA256` 或 `sha256sum` 校验。

## 说明

- 站点自 2026-10-30 起停止更新，本备份覆盖 2025-01 至 2026-03 的历史作品。
- 图片仅抓取每作品第 1 页（长作品加第 2 页），并非全站全部 1,256 万张。
- 本仓库只存元数据、映射和加密后的图片归档，不存任何访问凭据或解压密码。
